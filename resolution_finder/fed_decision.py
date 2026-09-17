# resolution_finder/fed_decision.py
"""Resolve Fed interest-rate decision markets ("Fed decision in December?")
straight from the Fed's own pages, instead of news search.

The news path scored 0/14 on these markets (unsupported-market-types.md
item 7), and it can read a forecast ("expected to cut") as an outcome.
The markets' own descriptions name the FOMC statement and the Fed's rate
table as the resolution source, and both are public federalreserve.gov
pages. Design: docs/superpowers/specs/2026-09-17-fed-decision-resolver-design.md.

Every check that can't be satisfied cleanly ends in UNCLEAR -- wrong
verdicts are the hard constraint, never a trade-off.
"""
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from resolution_finder.fed_pages import (
    CALENDAR_URL, MONTH_NUMBERS, RATE_TABLE_URL, Decision, FedPageParseError,
    FomcMeeting, RateChange, parse_calendar, parse_decision, parse_rate_table,
)
from resolution_finder.models import Market, Verdict
from resolution_finder.page_fetch import Fetcher, FetchError

FED_TITLE_PATTERN = re.compile(r"^\s*Fed (?:decision|interest rates?)\b", re.IGNORECASE)
_RATE_OPTION = re.compile(r"(\d+)(\+)? bps (decrease|increase)")

# Only the full-spec descriptions state the rounding rule; the one-line
# descriptions state no rules at all, so they get no rounding.
ROUNDING_RULE_TEXT = "rounded up to the nearest 25"
MAX_CLOSE_DATE_GAP_DAYS = 3
# The rate table dates a change by its effective day, the day AFTER the
# decision (Dec 9-10 2025 meeting -> "December 11" row).
TABLE_EFFECTIVE_WINDOW_DAYS = 2

_MONTH_NAMES = ("January|February|March|April|May|June|July|August|"
                "September|October|November|December")
# Real wordings: "meeting scheduled for December 9 - 10, 2025",
# "meeting scheduled for April 28-29, 2026".
_SCHEDULED_MEETING = re.compile(
    rf"meeting scheduled for ({_MONTH_NAMES}) (\d{{1,2}})"
    rf"(?:\s*-\s*(?:({_MONTH_NAMES}) )?(\d{{1,2}}))?,\s*(\d{{4}})"
)
# Real wordings: "prior to the Federal Reserve's December 2025 meeting",
# "interest rates in January 2025", "decision for December 2024".
_MONTH_YEAR = re.compile(rf"\b({_MONTH_NAMES}) (\d{{4}})\b")
_TITLE_MONTH = re.compile(rf"\b({_MONTH_NAMES})\b", re.IGNORECASE)

_ACTION_SIGN = {"lower": -1, "raise": 1, "maintain": 0}


@dataclass(frozen=True)
class RateOption:
    label: str       # the market's option text, verbatim
    kind: str        # "no_change", "change" or "other"
    bps: int = 0
    at_least: bool = False   # "50+ bps decrease"
    direction: int = 0       # -1 decrease, +1 increase


def parse_option(label: str) -> Optional[RateOption]:
    normalized = " ".join(label.split()).lower()
    if normalized == "no change":
        return RateOption(label, "no_change")
    if normalized == "other":
        return RateOption(label, "other")
    match = _RATE_OPTION.fullmatch(normalized)
    if match is None:
        return None
    return RateOption(label, "change", int(match.group(1)), match.group(2) == "+",
                      -1 if match.group(3) == "decrease" else 1)


def is_fed_decision_market(market: Market) -> bool:
    if not FED_TITLE_PATTERN.search(market.title):
        return False
    description = market.description.lower()
    if "federal reserve" not in description and "fomc" not in description:
        return False
    options = [parse_option(label) for label in market.options]
    return (bool(options) and all(o is not None for o in options)
            and any(o.kind != "other" for o in options))


@dataclass(frozen=True)
class MeetingTarget:
    year: int
    month: int
    start: Optional[date] = None           # set only when the description
    decision_date: Optional[date] = None   # states explicit meeting dates


def meeting_target(market: Market) -> Optional[MeetingTarget]:
    month_year = _MONTH_YEAR.search(market.description)
    scheduled = _SCHEDULED_MEETING.search(market.description)
    if scheduled:
        year = int(scheduled.group(5))
        first_month = MONTH_NUMBERS[scheduled.group(1).lower()]
        last_month = MONTH_NUMBERS[(scheduled.group(3) or scheduled.group(1)).lower()]
        first_day = int(scheduled.group(2))
        try:
            start = date(year, first_month, first_day)
            decision_date = date(year, last_month, int(scheduled.group(4) or first_day))
        except ValueError:
            return None
        # The same description also names the meeting by month and year;
        # if the two disagree, don't pick one.
        if month_year and (int(month_year.group(2)) != year
                           or MONTH_NUMBERS[month_year.group(1).lower()] not in (first_month, last_month)):
            return None
        return MeetingTarget(year, last_month, start, decision_date)
    if month_year:
        return MeetingTarget(int(month_year.group(2)), MONTH_NUMBERS[month_year.group(1).lower()])
    title_month = _TITLE_MONTH.search(market.title)
    if title_month and market.close_date:
        return MeetingTarget(market.close_date.year, MONTH_NUMBERS[title_month.group(1).lower()])
    return None


class _Unresolvable(Exception):
    def __init__(self, reason: str, source_url: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.source_url = source_url


def resolve_fed_decision(market: Market, fetch: Fetcher) -> Optional[list[Verdict]]:
    """None if this isn't a Fed-decision market. Otherwise always a list:
    per-option YES/NO when everything checks out, else one whole-market
    NO_EVIDENCE (statement not out yet) or UNCLEAR (with the reason)."""
    if not is_fed_decision_market(market):
        return None
    try:
        return _resolve(market, fetch)
    except _Unresolvable as exc:
        return [Verdict(outcome="UNCLEAR", confidence=0.0, evidence_snippet=exc.reason,
                        source_url=exc.source_url, source_type="primary")]


def _resolve(market: Market, fetch: Fetcher) -> list[Verdict]:
    options = [parse_option(label) for label in market.options]
    target = meeting_target(market)
    if target is None:
        raise _Unresolvable("Could not tell which FOMC meeting this market is about.")

    calendar = _parse_page(parse_calendar, fetch, CALENDAR_URL)
    meeting = _find_meeting(calendar, target)
    if meeting is None:
        raise _Unresolvable(
            "Could not match this market to exactly one FOMC meeting on the Fed calendar.", CALENDAR_URL)
    # Before the published check, so a wrongly matched meeting can never
    # hide behind NO_EVIDENCE.
    if market.close_date and abs((meeting.decision_date - market.close_date).days) > MAX_CLOSE_DATE_GAP_DAYS:
        raise _Unresolvable(
            f"FOMC meeting decision date {meeting.decision_date} is more than "
            f"{MAX_CLOSE_DATE_GAP_DAYS} days from the market close date {market.close_date}.", CALENDAR_URL)
    if meeting.statement_url is None:
        return [Verdict(outcome="NO_EVIDENCE", confidence=0.0,
                        evidence_snippet="FOMC statement for this meeting is not published yet.",
                        source_url=CALENDAR_URL, source_type="primary")]

    decision = _parse_page(parse_decision, fetch, meeting.statement_url)
    table = _parse_page(parse_rate_table, fetch, RATE_TABLE_URL)
    change_bps = _corroborated_change_bps(decision, meeting, table, meeting.statement_url)

    matches = _matching_options(options, change_bps, rounding=ROUNDING_RULE_TEXT in market.description)
    if len(matches) != 1:
        raise _Unresolvable(
            f"A {change_bps:+g} bps change matches {len(matches)} of this market's options, not exactly one.",
            meeting.statement_url)
    winner = matches[0]
    return [
        Verdict(outcome="YES" if option is winner else "NO", option=option.label, confidence=1.0,
                evidence_snippet=decision.sentence[:280], source_url=meeting.statement_url,
                source_type="primary")
        for option in options
    ]


def _parse_page(parser, fetch: Fetcher, url: str):
    try:
        page_html = fetch(url)
    except FetchError as exc:
        raise _Unresolvable(f"Could not fetch {url}.", url) from exc
    try:
        return parser(page_html)
    except FedPageParseError as exc:
        raise _Unresolvable(f"Fed page not in the expected format ({exc}).", url) from exc


def _find_meeting(calendar: dict[int, list[FomcMeeting]], target: MeetingTarget) -> Optional[FomcMeeting]:
    meetings = calendar.get(target.year, [])
    if target.start is not None:
        candidates = [m for m in meetings
                      if m.start == target.start and m.decision_date == target.decision_date]
    else:
        candidates = [m for m in meetings if target.month in m.months]
    return candidates[0] if len(candidates) == 1 else None


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _corroborated_change_bps(decision: Decision, meeting: FomcMeeting,
                             table: list[RateChange], statement_url: str) -> float:
    """The change in the upper bound vs. the level before the meeting (the
    description's own definition), computed from the statement's new level
    and the rate table's prior level, then required to agree with
    everything else both pages say about this meeting."""
    prior = [row for row in table if row.effective < meeting.start]
    if not prior or prior[-1].upper_bps is None:
        raise _Unresolvable("The rate table has no usable level from before this meeting.", RATE_TABLE_URL)
    change = decision.new_upper_bps - prior[-1].upper_bps

    if _sign(change) != _ACTION_SIGN[decision.action]:
        raise _Unresolvable(
            f"The FOMC statement says '{decision.action}', but its new upper bound "
            f"({decision.new_upper_bps:g} bps) vs. the rate table's prior level "
            f"({prior[-1].upper_bps:g} bps) is a {change:+g} bps change.", statement_url)
    if decision.stated_change_bps is not None and decision.stated_change_bps != abs(change):
        raise _Unresolvable(
            f"The FOMC statement states a {decision.stated_change_bps:g} bps move, but the levels "
            f"imply {abs(change):g} bps.", statement_url)

    window_end = meeting.decision_date + timedelta(days=TABLE_EFFECTIVE_WINDOW_DAYS)
    listed = [row for row in table if meeting.decision_date <= row.effective <= window_end]
    if change == 0:
        if listed:
            raise _Unresolvable(
                "The FOMC statement says no change, but the rate table lists a change for this meeting.",
                RATE_TABLE_URL)
        return 0.0
    if len(listed) != 1:
        raise _Unresolvable(
            "The rate table does not list exactly one change for this meeting (it may not be updated yet).",
            RATE_TABLE_URL)
    row = listed[0]
    moved, unmoved = (row.increase_bps, row.decrease_bps) if change > 0 else (row.decrease_bps, row.increase_bps)
    if row.upper_bps != decision.new_upper_bps or moved != abs(change) or unmoved != 0:
        raise _Unresolvable("The FOMC statement and the rate table disagree on this meeting's change.",
                            RATE_TABLE_URL)
    return change


def _matching_options(options: list[RateOption], change_bps: float, rounding: bool) -> list[RateOption]:
    magnitude = abs(change_bps)
    if rounding:
        magnitude = math.ceil(magnitude / 25) * 25
    elif magnitude % 25:
        return []
    direction = _sign(change_bps)
    return [
        option for option in options
        if (option.kind == "no_change" and magnitude == 0)
        or (option.kind == "change" and magnitude > 0 and option.direction == direction
            and (magnitude >= option.bps if option.at_least else magnitude == option.bps))
    ]
