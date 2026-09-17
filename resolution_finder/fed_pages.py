# resolution_finder/fed_pages.py
"""Parsers for the three public federalreserve.gov pages that Fed-decision
markets name as their resolution source:

* the FOMC meeting calendar -- which meetings exist, and each one's
  statement link once it is released;
* an FOMC statement -- the Committee's rate decision sentence;
* the "open market operations" rate table -- every change to the target
  range, with its effective date and new level.

Pure functions over page HTML: no fetching, no market logic. Every parser
is tested against real pages saved verbatim in tests/fixtures/fed/.
Anything that doesn't match the real layout raises FedPageParseError
instead of guessing -- the resolver turns that into UNCLEAR, never into a
verdict.
"""
import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

FED_BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = FED_BASE_URL + "/monetarypolicy/fomccalendars.htm"
RATE_TABLE_URL = FED_BASE_URL + "/monetarypolicy/openmarket.htm"

MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    # The real calendar labels two-month meetings with abbreviations:
    # "Apr/May", "Jan/Feb", "Oct/Nov".
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Real statements write rates like "3‑3/4" with U+2011 (non-breaking hyphen).
_DASH_TRANSLATION = dict.fromkeys(map(ord, "‐‑‒–−"), "-")


class FedPageParseError(Exception):
    """A Fed page didn't have the structure these parsers expect."""


def clean_text(fragment: str) -> str:
    """Tags stripped, entities decoded, dash variants -> "-", whitespace collapsed."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text.translate(_DASH_TRANSLATION)).strip()


# --- rates ---------------------------------------------------------------

_FRACTION_BPS = {
    "1/8": 12.5, "1/4": 25.0, "3/8": 37.5, "1/2": 50.0,
    "5/8": 62.5, "3/4": 75.0, "7/8": 87.5,
}


def statement_rate_to_bps(text: str) -> float:
    """A statement rate in percent, as basis points: '3-3/4' -> 375.0,
    '1/4' -> 25.0, '0' -> 0.0."""
    match = re.fullmatch(r"(?:(\d+)-)?(\d/\d)|(\d+)", text.strip())
    if match is None:
        raise FedPageParseError(f"unrecognized statement rate {text!r}")
    if match.group(3) is not None:
        return int(match.group(3)) * 100.0
    fraction = match.group(2)
    if fraction not in _FRACTION_BPS:
        raise FedPageParseError(f"unrecognized fraction in statement rate {text!r}")
    return int(match.group(1) or 0) * 100.0 + _FRACTION_BPS[fraction]


def table_level_upper_bps(text: str) -> Optional[float]:
    """Upper bound of a rate-table level, as basis points: '3.50-3.75' ->
    375.0, '0-0.25' -> 25.0, '4.25' (pre-2008 single target) -> 425.0.
    None if the cell isn't a level."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?", text.strip())
    if match is None:
        return None
    return round(float(match.group(2) or match.group(1)) * 100, 2)


def _table_change_bps(text: str) -> Optional[int]:
    """'25' -> 25; '...' (the table's "no move this direction") -> 0;
    anything else (e.g. 2008's '75-100') -> None."""
    text = text.strip()
    if text in ("...", "…"):
        return 0
    return int(text) if text.isdigit() else None


# --- calendar ------------------------------------------------------------

@dataclass(frozen=True)
class FomcMeeting:
    months: tuple[int, ...]       # (12,) or (4, 5) for "Apr/May"
    start: date                   # first meeting day
    decision_date: date           # last meeting day, when the statement is issued
    statement_url: Optional[str]  # None until the statement is released


_CALENDAR_YEAR_HEADING = re.compile(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>')
_CALENDAR_ROW_START = re.compile(r'<div class="(?:fomc-meeting--shaded )?row fomc-meeting"')
_CALENDAR_MONTH = re.compile(r'fomc-meeting__month[^"]*"><strong>([^<]+)</strong>')
_CALENDAR_DAYS = re.compile(r'fomc-meeting__date[^"]*">([^<]+)</div>')
_STATEMENT_LINK = re.compile(r'href="(/newsevents/pressreleases/monetary\d{8}a\.htm)"')
_MEETING_DAYS = re.compile(r"(\d{1,2})(?:-(\d{1,2}))?")


def parse_calendar(page_html: str) -> dict[int, list[FomcMeeting]]:
    """Year -> that year's regular meetings, in page order."""
    headings = list(_CALENDAR_YEAR_HEADING.finditer(page_html))
    if not headings:
        raise FedPageParseError("no 'YYYY FOMC Meetings' panels on the calendar page")
    calendar: dict[int, list[FomcMeeting]] = {}
    for index, heading in enumerate(headings):
        year = int(heading.group(1))
        panel_end = headings[index + 1].start() if index + 1 < len(headings) else len(page_html)
        rows = _CALENDAR_ROW_START.split(page_html[heading.end():panel_end])[1:]
        calendar[year] = [m for m in (_parse_calendar_row(year, row) for row in rows) if m is not None]
    return calendar


def _parse_calendar_row(year: int, row: str) -> Optional[FomcMeeting]:
    """None for anything that isn't a regular meeting. Real example: the
    "22 (notation vote)" entry in August 2025 has its own statement link,
    but it isn't a rate-setting meeting any market refers to."""
    month_match = _CALENDAR_MONTH.search(row)
    days_match = _CALENDAR_DAYS.search(row)
    if month_match is None or days_match is None:
        return None
    days = _MEETING_DAYS.fullmatch(days_match.group(1).strip().rstrip("*"))
    if days is None:
        return None
    try:
        months = tuple(MONTH_NUMBERS[part.strip().lower()] for part in month_match.group(1).split("/"))
        first_day = int(days.group(1))
        start = date(year, months[0], first_day)
        decision_date = date(year, months[-1], int(days.group(2) or first_day))
    except (KeyError, ValueError):
        return None
    if decision_date < start:
        return None
    link = _STATEMENT_LINK.search(row)
    return FomcMeeting(months, start, decision_date, FED_BASE_URL + link.group(1) if link else None)


# --- statement -----------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    action: str                          # "lower", "raise" or "maintain"
    stated_change_bps: Optional[float]   # the "by 1/4 percentage point", if stated
    new_upper_bps: float
    sentence: str


_RATE = r"(\d+-\d/\d|\d/\d|\d+)"
# Anchored on "the Committee decided to": that is what keeps dissent text
# out. Real example, March 15 2020: a dissenter "preferred to reduce the
# target range for the federal funds rate to 1/2 to 3/4 percent".
_CHANGE_DECISION = re.compile(
    r"[Tt]he Committee decided to (lower|raise) the target range for the federal funds rate "
    r"(?:by " + _RATE + r" percentage points? )?to " + _RATE + r" to " + _RATE + r" percent"
)
_HOLD_DECISION = re.compile(
    r"[Tt]he Committee decided to maintain the target range for the federal funds rate at "
    + _RATE + r" to " + _RATE + r" percent"
)


def parse_decision(page_html: str) -> Decision:
    text = clean_text(page_html)
    changes = list(_CHANGE_DECISION.finditer(text))
    holds = list(_HOLD_DECISION.finditer(text))
    if len(changes) + len(holds) != 1:
        raise FedPageParseError(
            f"expected exactly one rate decision sentence, found {len(changes) + len(holds)}"
        )
    if changes:
        match = changes[0]
        stated = statement_rate_to_bps(match.group(2)) if match.group(2) else None
        return Decision(match.group(1), stated, statement_rate_to_bps(match.group(4)),
                        _sentence_around(text, match))
    match = holds[0]
    return Decision("maintain", None, statement_rate_to_bps(match.group(2)),
                    _sentence_around(text, match))


def _sentence_around(text: str, match: re.Match) -> str:
    previous_end = text.rfind(". ", 0, match.start())
    start = previous_end + 2 if previous_end != -1 else 0
    end = text.find(".", match.end())
    return text[start:end + 1] if end != -1 else text[start:]


# --- rate table ----------------------------------------------------------

@dataclass(frozen=True)
class RateChange:
    effective: date
    increase_bps: Optional[int]
    decrease_bps: Optional[int]
    upper_bps: Optional[float]


_RATE_TABLE_YEAR = re.compile(r"<h4>(\d{4})</h4>(.*?)</table>", re.S)
_TABLE_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TABLE_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# Real footnoted row: "March 4 *" (2020) -- the asterisk is its own tag.
_TABLE_DATE = re.compile(r"([A-Za-z]+) (\d{1,2})(?: ?\*)?")


def parse_rate_table(page_html: str) -> list[RateChange]:
    """Every listed change, oldest first. The table lists changes only --
    a meeting that held rates has no row."""
    changes: list[RateChange] = []
    for year_match in _RATE_TABLE_YEAR.finditer(page_html):
        year, block = int(year_match.group(1)), year_match.group(2)
        if "<h4>" in block:
            # A year heading with no table of its own would silently hand
            # the NEXT year's rows this year's date.
            raise FedPageParseError(f"rate table heading {year} is not followed by its own table")
        for row in _TABLE_ROW.findall(block):
            cells = [clean_text(cell) for cell in _TABLE_CELL.findall(row)]
            if cells[:1] == ["Date"]:
                continue
            if len(cells) != 4:
                raise FedPageParseError(f"rate table row in {year} has {len(cells)} cells: {cells}")
            date_match = _TABLE_DATE.fullmatch(cells[0])
            month = MONTH_NUMBERS.get(date_match.group(1).lower()) if date_match else None
            try:
                effective = date(year, month, int(date_match.group(2)))
            except (TypeError, ValueError, AttributeError):
                raise FedPageParseError(f"unrecognized rate table date {cells[0]!r} in {year}")
            changes.append(RateChange(
                effective, _table_change_bps(cells[1]), _table_change_bps(cells[2]),
                table_level_upper_bps(cells[3]),
            ))
    if not changes:
        raise FedPageParseError("no rows found on the rate table page")
    return sorted(changes, key=lambda change: change.effective)
