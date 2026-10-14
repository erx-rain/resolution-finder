# resolution_finder/resolution_spec.py
"""Deterministic parsing of a market's DESCRIPTION into a resolution spec.

Opus plan (2026-09-08, "description-first strategy"): real rain.trade
market descriptions ship a machine-actionable resolution spec -- a
deadline, a stated default outcome, often a scheduled event time, and a
named primary source -- and name news consensus as the BACKUP, not the
primary. This module extracts the DETERMINISTIC half of that spec (no
model calls): the parts of the description that are highly templated
across every real example examined, as opposed to the genuinely variable
parts (numeric conditions, tiebreak cascades, enumerated membership
sets) that need model assistance and are out of scope here.

This module answers ONE question: is a resolution AVAILABLE for this
market, i.e. is there any point going and looking? It deliberately does
NOT try to determine the outcome -- that stays the verdict engine's job,
gated behind its own, much stricter, evidence bar. See
docs/superpowers/plans/2026-09-08-resolution-availability-design.md for
the full argument: a false "available" costs one wasted human glance; a
wrong outcome resolves a market incorrectly. Different costs justify
different bars, which is why this module can be this permissive without
weakening anything in verdict_engine.py.

Measured on the real 97-market batch before writing any of this (see
that same plan doc): a usable deadline (close_date or a description
deadline) covers 96% of markets, and 87 of 97 are already past their
close_date today -- so the three signals below, on their own, answer
the main goal for the large majority of the batch.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from resolution_finder.models import Market

_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December"
)

# "Month D, YYYY" or "Month D-D, YYYY" (a day range -- e.g. "December
# 10-12, 2023" for a multi-day election). The day-range form is handled
# by _parse_calendar_date below, which uses the range's FIRST day: a
# market becomes availability-relevant from when the event STARTS, not
# when it ends.
_FULL_DATE = rf"(?:{_MONTH_NAMES})\s+\d{{1,2}}(?:-\d{{1,2}})?,\s*\d{{4}}"

# "Month D" with no year -- real bug shape this exists to handle
# (verified live, 2026-09-08): "In the upcoming Women's NCAAB game,
# scheduled for April 1 at 7:15 PM ET" names no year anywhere in the
# sentence. _parse_calendar_date deliberately does NOT guess a year for
# this form -- see its own comment for why.
_MONTH_DAY_NO_YEAR = rf"(?:{_MONTH_NAMES})\s+\d{{1,2}}"


def _parse_calendar_date(raw: str) -> Optional[date]:
    """Parse a 'Month D[-D], YYYY' string into a date. Returns None for a
    year-less 'Month D' string -- deliberately: this project's standing
    rule is to never invent data (see the no-ai-authored-test-data
    convention), and guessing a year for a scheduled event is exactly
    that. The RAW matched text is still surfaced by callers even when no
    date can be computed from it, so "we found phrasing but can't
    compute a date" stays visibly different from "we found nothing"."""
    # Collapse a day range to its first day before parsing: "December
    # 10-12, 2023" -> "December 10, 2023".
    cleaned = re.sub(r"(\d{1,2})-\d{1,2},", r"\1,", raw.strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


# Same small-phrase-list style as verdict_engine.py's
# CUMULATIVE_DATE_TRIGGER_PHRASES and DEFAULT_OUTCOME_PATTERN -- reusing
# the identical trigger set already calibrated there for "this date is a
# DEADLINE" phrasing, rather than inventing a second one that could
# silently drift out of sync.
_BACKSTOP_DEADLINE_RE = re.compile(
    rf"(?:by|no later than|on or before|prior to)\s+({_FULL_DATE})",
    re.IGNORECASE,
)

# Distinct from a deadline: this describes when the underlying EVENT
# (a game, a debate, an election, a hearing) is expected to occur, which
# is typically much MORE PRECISE than either close_date or the
# description's backstop deadline -- measured at 17% of the real batch,
# concentrated in exactly the single-event markets (one game, one
# debate) where the other two signals are least precise. See the design
# doc's "three tiers" table.
_SCHEDULED_EVENT_RE = re.compile(
    r"(?:is |are |was |were )?scheduled (?:to (?:take place|begin|start|"
    rf"occur))?\s*(?:for|on)?\s*({_FULL_DATE}|{_MONTH_DAY_NO_YEAR})",
    re.IGNORECASE,
)

# Real bug found live (2026-09-08) building this: an earlier, broader
# draft of this pattern also matched "takes place" and "kickoff" as
# scheduling triggers, but neither appeared in ANY of the real markets
# examined and "takes place" in particular reads naturally in sentences
# that are NOT stating a schedule ("the vote takes place in the Senate"),
# risking false positives for no measured benefit. Narrowed to just the
# "scheduled ..." phrasing actually observed across 17 real hits before
# broadening further.

# Moved here from verdict_engine.py 2026-09-08 (description-first
# design): verdict_engine is the CONSUMER of a parsed spec, not the
# other way around, so the pattern belongs in this module and
# verdict_engine now imports it instead of defining its own copy.
#
# Generalized: captures a plain Yes/No default (CLARITY Act, Nobel
# Prize) OR a specific named option default (Vinicius Junior -> "Real
# Madrid") OR a named outcome outside the option list (Osun -> "Other").
# The trigger phrases anchor on deadline-miss language so this doesn't
# match an unrelated "resolves to X" sentence describing the normal win
# condition. "not known" was added after a real market ("are not known
# definitively by [date] ... resolve to 'Other'") didn't match any of
# the original trigger phrases. "otherwise" was added after finding 9 of
# 18 real markets in the dataset (2026-08-25) state their default purely
# as "Otherwise, this market will resolve to 'No'." -- no negation word
# at all, so none of the original triggers matched it, silently
# disabling the deadline-passed default for roughly half the dataset.
# The quote character class also accepts curly quotes (""), not just
# straight ones -- real markets.json descriptions use curly quotes
# around the resolved value, which a plain \"? literal never matched.
# Re-verified 2026-09-08 against a real, genuinely non-binary default:
# the Egypt election market's "the market will resolve to 50-50" -- which
# surfaced two real bugs while writing that test (both are new, this
# session; not present when this pattern lived in verdict_engine.py
# purely because no test had exercised a digit-starting default before):
#   1. the value-capture group required the FIRST character to be a
#      letter ([A-Za-z]...), so a value like "50-50" could never match
#      at all -- widened to [A-Za-z0-9]...
#   2. the real text reads "isn't known by October 31, 2024" -- the
#      trigger list had "not known" but not the contracted "isn't
#      known"/"aren't known", so it silently missed this real sentence
#      entirely and fell through to the market's OTHER, unrelated
#      "Otherwise...resolve to 'No'" statement (that one describes the
#      election-LOSS outcome, not the deadline-miss outcome -- a
#      different question this market's description happens to also
#      answer with "resolve to X" phrasing). Fixed by adding the
#      contraction explicitly rather than trying to generalize the
#      trigger phrase, matching this list's existing style of adding
#      the SPECIFIC phrasing gaps found live rather than a broader regex
#      that risks new false positives.
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been|not known|"
    r"isn'?t known|aren'?t known|otherwise)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+[\"“]?([A-Za-z0-9][A-Za-z0-9 .&'-]*?)[\"”]?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

# A market's description can state its default TWICE for two different
# questions -- real motivating case, the Egypt election market: "Otherwise,
# this market will resolve to 'No'" answers "what if el-Sisi doesn't win",
# while "If the result isn't known by October 31, 2024... resolve to
# 50-50" answers "what if we still don't know by the deadline". Both are
# genuine "resolve to X" statements DEFAULT_OUTCOME_PATTERN matches, but
# only the second is the deadline-miss default this module actually
# needs (it's what applies once AVAILABLE_BACKSTOP_DEADLINE_PASSED
# fires). "otherwise" is the weak, generic trigger among the set --
# it can anchor to ANY preceding condition, not specifically a deadline
# -- so a deadline-specific trigger is preferred over it whenever both
# are present. Bare "otherwise" stays the correct choice when it's the
# ONLY trigger a description uses (the original reason it was added --
# see this pattern's own history above).
_DEADLINE_ANCHORED_DEFAULT_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been|not known|"
    r"isn'?t known|aren'?t known)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+[\"“]?([A-Za-z0-9][A-Za-z0-9 .&'-]*?)[\"”]?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

# A multi-outcome market's own statement that every listed option which is
# NOT the winner resolves "No" -- real wording, both from live markets:
#   "If none of the listed individuals or entities receive the prize, all
#    listed outcomes will resolve to "No"."        (Nobel Peace Prize 2026)
#   "The winning team's market will resolve to "Yes". All other team
#    markets will resolve to "No"."                (The International 2026)
# verdict_engine's unlisted-winner path only ever fires for a market whose
# own rules say this (description beats title): without it, "someone not
# on the list won" is not known to settle the listed options at all (a
# market whose options are scores, seeds or divisions -- "Dodgers win
# 4-1" -- is decided by a named team winning, not negated by it).
_NON_WINNERS_RESOLVE_NO_RE = re.compile(
    r"none of the listed[^.]{0,120}?resolve[^.]{0,20}?[\"“]?no\b|"
    r"all other (?:listed )?[a-z]*\s?(?:outcomes|options|markets)[^.]{0,60}?resolve[^.]{0,20}?[\"“]?no\b",
    re.IGNORECASE | re.DOTALL,
)


# The market's own rules let it resolve as soon as the event happens -- the
# close date is the LAST moment, not the moment of resolution. Real wording,
# all live rain.trade markets (2026-10-13/14):
#   "...if, at any time from the market's creation through December 31,
#    2026, 11:59 PM ET, Anthropic's public valuation exceeds ..."
#   "...if, between the moment this market is created and 11:59 PM ET on the
#    specified date, the United States government ... makes a public ..."
#   "...if, by the specified date at 11:59 PM ET, the United States and Iran
#    have signed ..."
#   "...available for purchase ... on or before December 31, 2026, 11:59 PM ET."
#   "This market will immediately resolve to "Yes" if any Binance 1-minute
#    candle for Bitcoin (BTC/USDT) between July 21, 2026, and December 31..."
# Owner decision 2026-10-14: such a market is never described as "can't be
# determined until <close date>" -- the end time is a time frame, not a
# date the answer waits for.
_EARLY_RESOLUTION_RE = re.compile(
    r"\bat any time\b"
    r"|\bbetween the moment (?:this|the) market (?:is|was) created and\b"
    r"|\bimmediately resolve"
    r"|\bbetween (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?(?:\s+\d{4})?,?\s+and\b"
    r"|\b(?:by|on or before|no later than|prior to)\s+(?:the\s+)?"
    r"(?:specified date|end of\b|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|\d{1,2}:\d{2})",
    re.IGNORECASE,
)


def states_early_resolution(description: str) -> bool:
    return bool(_EARLY_RESOLUTION_RE.search(description or ""))


def states_non_winners_resolve_no(description: str) -> bool:
    return bool(_NON_WINNERS_RESOLVE_NO_RE.search(description or ""))


# Highly regular across every real description examined -- "The primary
# resolution source...", "official information from...", "a consensus
# of credible reporting". This deliberately only detects WHETHER a
# source is named, not WHICH one -- extracting and querying the actual
# named source (Congress.gov, the NHL, a specific exchange) is the
# model-assisted parsing step in the design doc, not this deterministic
# pass.
_NAMES_PRIMARY_SOURCE_RE = re.compile(
    r"official (?:information|statements?|results?|announcement)|"
    r"consensus of credible reporting|credible reporting consensus|"
    r"primary resolution source|resolution source",
    re.IGNORECASE,
)


@dataclass
class ResolutionSpec:
    """The deterministic half of a market's resolution spec, parsed from
    its description. Every field is Optional/False-by-default -- a
    market whose description doesn't match any pattern gets an all-empty
    spec, which is a normal, expected outcome (see
    docs/superpowers/plans/2026-09-08-resolution-availability-design.md,
    "How should a market with no parseable deadline behave?" -- never
    invent one)."""

    backstop_deadline: Optional[date]
    backstop_deadline_raw: Optional[str]
    default_outcome: Optional[str]
    scheduled_event_date: Optional[date]
    scheduled_event_raw: Optional[str]
    names_primary_source: bool
    # Resolvable as soon as the event happens, before the close date --
    # states_early_resolution. Never true for a market with a scheduled
    # event date: its answer does wait for that date.
    can_resolve_early: bool = False


def extract_backstop_deadline(description: str) -> tuple[Optional[date], Optional[str]]:
    """The date after which the description's own stated default applies
    -- e.g. "...signed into law no later than December 31, 2026...".
    Returns (parsed_date, raw_matched_text); either may be present
    without the other only in principle (the pattern only ever captures
    a full "Month D, YYYY" string, which _parse_calendar_date always
    parses), kept as a pair for symmetry with extract_scheduled_event."""
    match = _BACKSTOP_DEADLINE_RE.search(description)
    if not match:
        return None, None
    raw = match.group(1)
    return _parse_calendar_date(raw), raw


def extract_default_outcome(description: str) -> Optional[str]:
    """The market's own stated fallback outcome if its deadline passes
    with the condition unmet -- e.g. "Otherwise, this market will
    resolve to 'No'". Normalizes Yes/No to upper case; passes through
    any other stated value verbatim (real example: "the market will
    resolve to 50-50", a genuine non-binary default).

    Prefers a deadline-anchored trigger over a bare "otherwise" one when
    a description states both -- see _DEADLINE_ANCHORED_DEFAULT_PATTERN's
    own comment for why."""
    match = _DEADLINE_ANCHORED_DEFAULT_PATTERN.search(description) or DEFAULT_OUTCOME_PATTERN.search(description)
    if not match:
        return None
    candidate = match.group(1).strip()
    if candidate.lower() == "yes":
        return "YES"
    if candidate.lower() == "no":
        return "NO"
    return candidate


def extract_scheduled_event_date(description: str) -> tuple[Optional[date], Optional[str]]:
    """When the description states the underlying event is scheduled to
    occur -- e.g. "scheduled for April 1 at 7:15 PM ET". Returns
    (parsed_date, raw_matched_text). parsed_date is None when the
    matched text has no year (see _parse_calendar_date's own comment for
    why that is never guessed) -- raw_matched_text is still returned in
    that case, since "we found scheduling language but can't compute a
    date from it" is real, useful information a caller should be able to
    surface, distinct from finding nothing at all."""
    match = _SCHEDULED_EVENT_RE.search(description)
    if not match:
        return None, None
    raw = match.group(1)
    return _parse_calendar_date(raw), raw


def names_primary_source(description: str) -> bool:
    return bool(_NAMES_PRIMARY_SOURCE_RE.search(description))


def parse_resolution_spec(market: Market) -> ResolutionSpec:
    description = market.description or ""
    backstop_date, backstop_raw = extract_backstop_deadline(description)
    scheduled_date, scheduled_raw = extract_scheduled_event_date(description)
    return ResolutionSpec(
        backstop_deadline=backstop_date,
        backstop_deadline_raw=backstop_raw,
        default_outcome=extract_default_outcome(description),
        scheduled_event_date=scheduled_date,
        scheduled_event_raw=scheduled_raw,
        names_primary_source=names_primary_source(description),
        can_resolve_early=scheduled_date is None and states_early_resolution(description),
    )


# Reasons an availability check can fire, in order of PRECISION (not
# coverage) -- see the design doc's "three tiers" table. Checked in this
# same order by check_availability: the most precise signal that fired
# is reported, even if a less precise one also would have.
AVAILABLE_SCHEDULED_EVENT_PASSED = "scheduled_event_passed"
AVAILABLE_CLOSE_DATE_PASSED = "close_date_passed"
AVAILABLE_BACKSTOP_DEADLINE_PASSED = "backstop_deadline_passed"


@dataclass
class AvailabilityResult:
    """Whether a resolution is AVAILABLE for this market -- i.e. whether
    it's worth going and looking -- deliberately kept separate from any
    claim about the OUTCOME. `available=True` is cheap to get wrong (one
    wasted human glance); asserting an outcome is not, so this class
    never does that. `spec.default_outcome` is exposed on the spec for a
    caller to use as CONTEXT once it has separately checked whether
    Path B (an early triggering event) actually found anything -- that
    composition needs evidence-checking this module doesn't do, so it
    deliberately isn't baked into `suggested_outcome` here."""

    available: bool
    reason: Optional[str]
    trigger_date: Optional[date]
    spec: ResolutionSpec
    # When NOT yet available: the earliest date one of the three signals
    # below will fire -- what "can't be determined until ..." means for
    # this market. None when available, or when no date is known at all.
    available_from: Optional[date] = None


def check_availability(
    market: Market,
    spec: Optional[ResolutionSpec] = None,
    today: Optional[date] = None,
) -> AvailabilityResult:
    """Three tiers, checked in order of PRECISION:

    1. A stated scheduled-event date has passed -- the most precise
       signal (measured at 17% of the real batch, concentrated in
       single-event markets where this fires the same day the event
       happened, versus weeks-to-months later for the other two tiers).
    2. `market.close_date` has passed -- the most common signal (96% of
       the real batch), but only "probably" resolvable: close_date is
       when trading stops, not a resolution-source guarantee.
    3. The description's own backstop deadline has passed -- less common
       (measured ~40%) but, when present, is the market's OWN stated
       rule for when its default outcome applies.

    A market can trip more than one tier; the most precise one that
    fired is what gets reported, since it's the most actionable (e.g. a
    scheduled event date, when present, is a better answer to "when do I
    check this" than a months-later backstop deadline)."""
    if spec is None:
        spec = parse_resolution_spec(market)
    today = today if today is not None else date.today()

    if spec.scheduled_event_date and today > spec.scheduled_event_date:
        return AvailabilityResult(True, AVAILABLE_SCHEDULED_EVENT_PASSED, spec.scheduled_event_date, spec)
    if market.close_date and today > market.close_date:
        return AvailabilityResult(True, AVAILABLE_CLOSE_DATE_PASSED, market.close_date, spec)
    if spec.backstop_deadline and today > spec.backstop_deadline:
        return AvailabilityResult(True, AVAILABLE_BACKSTOP_DEADLINE_PASSED, spec.backstop_deadline, spec)
    upcoming = [d for d in (spec.scheduled_event_date, market.close_date, spec.backstop_deadline) if d]
    return AvailabilityResult(False, None, None, spec, min(upcoming) if upcoming else None)
