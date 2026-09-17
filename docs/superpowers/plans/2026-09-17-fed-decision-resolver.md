# Fed Decision Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve Fed interest-rate decision markets ("Fed decision in December?") from the Fed's own pages -- FOMC calendar, FOMC statement, rate table -- instead of news search, with every inconsistency ending in UNCLEAR.

**Architecture:** Three new focused modules: `page_fetch.py` (plain HTTP fetch, no cache), `fed_pages.py` (pure parsers for the three federalreserve.gov pages), `fed_decision.py` (recognize a Fed market, find its meeting, cross-check statement vs. rate table, map to an option). `structured_resolvers.py` is the plug-in list the pipeline and `run_eval.py` call before news retrieval; a Fed market never reaches peer-check or news.

**Tech Stack:** Python 3, `requests`, stdlib `re`/`html`/`dataclasses`, pytest. Runs in WSL venv.

**Spec:** `docs/superpowers/specs/2026-09-17-fed-decision-resolver-design.md`

## Global Constraints

- Wrong verdicts are the hard constraint: any check that can't be satisfied cleanly -> `UNCLEAR` (or `NO_EVIDENCE` when the statement isn't out yet). Never guess.
- A market recognized as a Fed-decision market must never fall through to peer-check or news retrieval.
- No caching in production code (`pipeline.py`, `page_fetch.py`, `fed_*.py`). Caching only inside `run_eval.py`'s harness (`eval_cache.py`).
- `data/markets.json`, `data/eval_history.jsonl` stay uncommitted; never edit them by hand.
- Test fixtures are real pages saved verbatim (`tests/fixtures/fed/`, already committed with this plan, fetched 2026-09-17). Negative tests mutate a real fixture inside the test, with a comment saying so.
- Test command (all tasks): `wsl -- bash -c "cd '/mnt/c/Users/liam/Documents/Resolution Finder/.worktrees/resolution-finder-scanner' && PYTHONPATH=. .venv-wsl/bin/python -m pytest <target> -v"`
- Git commands run from the Bash tool directly in the worktree directory.
- Commit messages end with `Co-Authored-By: Claude <Model> <noreply@anthropic.com>` for the model active at commit time (e.g. `Claude Sonnet 5`, `Claude Opus 5`).
- Match surrounding code style: module header comment `# resolution_finder/<name>.py`, docstrings that explain *why* with the real example that motivated a rule.

## Real facts the code depends on (all checked live 2026-09-17)

- Calendar rows: month label `December` or `Apr/May` / `Jan/Feb` / `Oct/Nov`; days `9-10*`, `30-1`; the statement link `/newsevents/pressreleases/monetaryYYYYMMDDa.htm` only exists once released (October 2026 row has none in the fixture). August 2025 has `22 (notation vote)` WITH a statement link -- must be skipped. The page covers 2021-2027.
- Statement decision sentences (verbatim, after tag stripping; U+2011 appears inside `3‑3/4`):
  - Dec 10 2025: `...the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3‑3/4 percent.`
  - Jan 28 2026: `...the Committee decided to maintain the target range for the federal funds rate at 3‑1/2 to 3‑3/4 percent.`
  - Dec 18 2024: `...decided to lower ... by 1/4 percentage point to 4-1/4 to 4-1/2 percent.`
  - Mar 15 2020: `...decided to lower the target range for the federal funds rate to 0 to 1/4 percent.` (no "by"); its dissent says `preferred to reduce the target range for the federal funds rate to 1/2 to 3/4 percent`.
- Rate table: rows only for changes; `December 11 | 0 | 25 | 3.50-3.75` (effective the day after the Dec 9-10 decision); `March 4 *` (2020, footnote asterisk in its own tag); pre-2008 `...` for "no move" and single levels like `4.25`; 2008 has `75-100`. 2026 currently has `September 17 | 25 | 0 | 3.75-4.00`.
- federalreserve.gov serves `Content-Type: text/html` with no charset.
- A throwaway dry run of exactly the code below got 14/14 real Fed markets correct and UNCLEAR on every mutation case.

## File Structure

| File | Responsibility |
|---|---|
| Create `resolution_finder/page_fetch.py` | `Fetcher` type, `FetchError`, `http_fetch` (UTF-8 fallback, no cache) |
| Create `resolution_finder/fed_pages.py` | Pure parsers: calendar, statement decision, rate table |
| Create `resolution_finder/fed_decision.py` | Market recognition, meeting identification, cross-check, option mapping, verdicts |
| Create `resolution_finder/structured_resolvers.py` | Ordered resolver list + `resolve_structured` |
| Modify `resolution_finder/pipeline.py` | `structured_resolver` parameter; short-circuit in `_scan_market` |
| Modify `resolution_finder/eval_cache.py` | `CachedPageFetcher` for the eval harness |
| Modify `run_eval.py` | Call `resolve_structured` before retrieval; small print/record helpers |
| Create `tests/test_page_fetch.py`, `tests/test_fed_pages.py`, `tests/test_fed_decision.py`, `tests/test_structured_resolvers.py` | Unit tests |
| Modify `tests/test_pipeline.py`, `tests/test_eval_cache.py` | Hook + cache tests |
| Already present: `tests/fixtures/fed/*.htm`, `.gitattributes` | Real pages, byte-for-byte |
| Modify `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`, `docs/superpowers/plans/2026-09-16-opus-session-agenda.md` | Status update after eval |

---

### Task 1: `page_fetch.py` -- plain fetch with UTF-8 fallback

**Files:**
- Create: `resolution_finder/page_fetch.py`
- Test: `tests/test_page_fetch.py`

**Interfaces:**
- Consumes: `resolution_finder.evidence_retriever.FEED_USER_AGENT` (existing string constant)
- Produces: `Fetcher = Callable[[str], str]`; `class FetchError(Exception)`; `def http_fetch(url: str) -> str` (raises `FetchError`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_page_fetch.py
from unittest.mock import patch

import pytest
import requests

from resolution_finder.page_fetch import FetchError, http_fetch


def _response(body: bytes, content_type: str, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = content_type
    # What requests' HTTP adapter does for a real response.
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


def test_http_fetch_decodes_utf8_when_the_server_names_no_charset():
    # federalreserve.gov answers "Content-Type: text/html" with no charset
    # (checked live 2026-09-17). requests would fall back to ISO-8859-1 and
    # mangle the U+2011 hyphen real FOMC statements use in "3-3/4".
    body = "at 3-1/2 to 3‑3/4 percent".encode("utf-8")
    with patch("resolution_finder.page_fetch.requests.get", return_value=_response(body, "text/html")):
        assert http_fetch("https://www.federalreserve.gov/x.htm") == "at 3-1/2 to 3‑3/4 percent"


def test_http_fetch_respects_a_declared_charset():
    body = "café".encode("latin-1")
    with patch("resolution_finder.page_fetch.requests.get",
               return_value=_response(body, "text/html; charset=ISO-8859-1")):
        assert http_fetch("https://example.org/") == "café"


def test_http_fetch_raises_fetch_error_on_http_error_status():
    with patch("resolution_finder.page_fetch.requests.get", return_value=_response(b"", "text/html", 503)):
        with pytest.raises(FetchError):
            http_fetch("https://www.federalreserve.gov/x.htm")


def test_http_fetch_raises_fetch_error_on_network_failure():
    with patch("resolution_finder.page_fetch.requests.get", side_effect=requests.ConnectionError("down")):
        with pytest.raises(FetchError):
            http_fetch("https://www.federalreserve.gov/x.htm")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: test command with `tests/test_page_fetch.py`
Expected: FAIL / collection error `ModuleNotFoundError: No module named 'resolution_finder.page_fetch'`

- [ ] **Step 3: Implement**

```python
# resolution_finder/page_fetch.py
"""Plain page fetching for structured resolvers (the Fed pages today).

Deliberately no caching: production always reads the live page. Caching
is allowed only inside run_eval.py's harness (standing project rule) --
see eval_cache.CachedPageFetcher.
"""
from typing import Callable

import requests

from resolution_finder.evidence_retriever import FEED_USER_AGENT

FETCH_TIMEOUT_SECONDS = 20

Fetcher = Callable[[str], str]


class FetchError(Exception):
    """A page could not be fetched. Resolvers turn this into UNCLEAR."""


def http_fetch(url: str) -> str:
    try:
        response = requests.get(
            url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": FEED_USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc
    # federalreserve.gov serves "Content-Type: text/html" with no charset
    # (checked live 2026-09-17). requests then falls back to ISO-8859-1,
    # which mangles the U+2011 non-breaking hyphens real FOMC statements
    # use inside rates like "3-3/4" -- and the decision regex stops matching.
    if "charset" not in response.headers.get("Content-Type", "").lower():
        response.encoding = "utf-8"
    return response.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: test command with `tests/test_page_fetch.py`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/page_fetch.py tests/test_page_fetch.py
git commit -m "Add page_fetch: plain fetch with UTF-8 fallback for charset-less pages

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

---

### Task 2: `fed_pages.py` -- parsers for the three Fed pages

**Files:**
- Create: `resolution_finder/fed_pages.py`
- Test: `tests/test_fed_pages.py`
- Uses fixtures (already committed): `tests/fixtures/fed/fomccalendars.htm`, `openmarket.htm`, `monetary20251210a.htm`, `monetary20260128a.htm`, `monetary20241218a.htm`, `monetary20200315a.htm`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `FED_BASE_URL: str`, `CALENDAR_URL: str`, `RATE_TABLE_URL: str`, `MONTH_NUMBERS: dict[str, int]`
  - `class FedPageParseError(Exception)`
  - `def clean_text(fragment: str) -> str`
  - `def statement_rate_to_bps(text: str) -> float`; `def table_level_upper_bps(text: str) -> Optional[float]`
  - `@dataclass(frozen=True) FomcMeeting(months: tuple[int, ...], start: date, decision_date: date, statement_url: Optional[str])`
  - `def parse_calendar(page_html: str) -> dict[int, list[FomcMeeting]]`
  - `@dataclass(frozen=True) Decision(action: str, stated_change_bps: Optional[float], new_upper_bps: float, sentence: str)`
  - `def parse_decision(page_html: str) -> Decision`
  - `@dataclass(frozen=True) RateChange(effective: date, increase_bps: Optional[int], decrease_bps: Optional[int], upper_bps: Optional[float])`
  - `def parse_rate_table(page_html: str) -> list[RateChange]` (oldest first)

- [ ] **Step 1: Confirm the fixtures are present**

Run: `ls -la tests/fixtures/fed`
Expected: the 6 `.htm` files listed above. If any is missing, stop -- do not re-fetch (the October 2026 "no statement yet" test depends on the 2026-09-17 snapshot).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_fed_pages.py
from datetime import date
from pathlib import Path

import pytest

from resolution_finder.fed_pages import (
    FED_BASE_URL, FedPageParseError, RateChange, clean_text, parse_calendar,
    parse_decision, parse_rate_table, statement_rate_to_bps, table_level_upper_bps,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fed"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_statement_rate_to_bps_handles_every_real_shape():
    assert statement_rate_to_bps("3-3/4") == 375.0
    assert statement_rate_to_bps("1/4") == 25.0
    assert statement_rate_to_bps("0") == 0.0
    assert statement_rate_to_bps("4") == 400.0


def test_statement_rate_to_bps_rejects_unknown_text():
    with pytest.raises(FedPageParseError):
        statement_rate_to_bps("three")


def test_table_level_upper_bps():
    assert table_level_upper_bps("3.50-3.75") == 375.0
    assert table_level_upper_bps("0-0.25") == 25.0
    assert table_level_upper_bps("4.25") == 425.0
    assert table_level_upper_bps("n/a") is None


def test_clean_text_normalizes_the_non_breaking_hyphen_and_entities():
    assert clean_text("<p>3‑3/4&nbsp; percent</p>") == "3-3/4 percent"


def test_parse_calendar_reads_a_released_meeting():
    calendar = parse_calendar(fixture("fomccalendars.htm"))
    (meeting,) = [m for m in calendar[2025] if m.months == (12,)]
    assert meeting.start == date(2025, 12, 9)
    assert meeting.decision_date == date(2025, 12, 10)
    assert meeting.statement_url == FED_BASE_URL + "/newsevents/pressreleases/monetary20251210a.htm"


def test_parse_calendar_has_no_statement_url_before_release():
    calendar = parse_calendar(fixture("fomccalendars.htm"))
    assert [m.statement_url for m in calendar[2026] if m.months == (10,)] == [None]


def test_parse_calendar_handles_a_real_two_month_meeting():
    calendar = parse_calendar(fixture("fomccalendars.htm"))
    (meeting,) = [m for m in calendar[2024] if 5 in m.months]
    assert meeting.months == (4, 5)
    assert meeting.start == date(2024, 4, 30)
    assert meeting.decision_date == date(2024, 5, 1)


def test_parse_calendar_skips_the_real_notation_vote_entry():
    # August 2025's "22 (notation vote)" has its own statement link, but it
    # is not a rate-setting meeting any market refers to.
    calendar = parse_calendar(fixture("fomccalendars.htm"))
    assert [m for m in calendar[2025] if 8 in m.months] == []
    assert len(calendar[2025]) == 8


def test_parse_calendar_rejects_a_page_without_year_panels():
    with pytest.raises(FedPageParseError):
        parse_calendar("<html><body>Scheduled maintenance</body></html>")


def test_parse_decision_reads_a_cut_with_a_stated_amount():
    decision = parse_decision(fixture("monetary20251210a.htm"))
    assert decision.action == "lower"
    assert decision.stated_change_bps == 25.0
    assert decision.new_upper_bps == 375.0
    assert decision.sentence == (
        "In support of its goals and in light of the shift in the balance of risks, the Committee "
        "decided to lower the target range for the federal funds rate by 1/4 percentage point to "
        "3-1/2 to 3-3/4 percent."
    )


def test_parse_decision_reads_a_hold():
    decision = parse_decision(fixture("monetary20260128a.htm"))
    assert decision.action == "maintain"
    assert decision.stated_change_bps is None
    assert decision.new_upper_bps == 375.0


def test_parse_decision_reads_the_december_2024_cut():
    decision = parse_decision(fixture("monetary20241218a.htm"))
    assert (decision.action, decision.stated_change_bps, decision.new_upper_bps) == ("lower", 25.0, 450.0)


def test_parse_decision_reads_a_cut_with_no_stated_amount_and_ignores_the_dissent():
    # March 15 2020: "decided to lower the target range ... to 0 to 1/4
    # percent" (no "by X"), and a dissent that "preferred to reduce the
    # target range ... to 1/2 to 3/4 percent" -- which must not be read.
    decision = parse_decision(fixture("monetary20200315a.htm"))
    assert decision.action == "lower"
    assert decision.stated_change_bps is None
    assert decision.new_upper_bps == 25.0


def test_parse_decision_rejects_a_page_with_no_decision_sentence():
    with pytest.raises(FedPageParseError):
        parse_decision(fixture("fomccalendars.htm"))


def test_parse_rate_table_reads_real_rows_oldest_first():
    table = parse_rate_table(fixture("openmarket.htm"))
    assert table == sorted(table, key=lambda row: row.effective)
    assert [row for row in table if row.effective == date(2025, 12, 11)] == [
        RateChange(date(2025, 12, 11), 0, 25, 375.0)
    ]


def test_parse_rate_table_reads_footnoted_and_pre_2008_rows():
    table = {row.effective: row for row in parse_rate_table(fixture("openmarket.htm"))}
    assert table[date(2020, 3, 4)] == RateChange(date(2020, 3, 4), 0, 50, 125.0)     # "March 4 *"
    assert table[date(2007, 12, 11)] == RateChange(date(2007, 12, 11), 0, 25, 425.0)  # "..." + single level
    assert table[date(2008, 12, 16)].decrease_bps is None                             # "75-100"


def test_parse_rate_table_rejects_a_page_without_rows():
    with pytest.raises(FedPageParseError):
        parse_rate_table("<html><body>Scheduled maintenance</body></html>")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: test command with `tests/test_fed_pages.py`
Expected: collection error `ModuleNotFoundError: No module named 'resolution_finder.fed_pages'`

- [ ] **Step 4: Implement**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: test command with `tests/test_fed_pages.py`
Expected: 17 passed

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/fed_pages.py tests/test_fed_pages.py
git commit -m "Add fed_pages: parsers for the FOMC calendar, statement and rate table

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

---

### Task 3: `fed_decision.py` -- recognize, identify meeting, cross-check, resolve

**Files:**
- Create: `resolution_finder/fed_decision.py`
- Test: `tests/test_fed_decision.py`

**Interfaces:**
- Consumes (Task 1): `Fetcher`, `FetchError` from `resolution_finder.page_fetch`
- Consumes (Task 2): `CALENDAR_URL`, `RATE_TABLE_URL`, `FED_BASE_URL`, `MONTH_NUMBERS`, `Decision`, `FedPageParseError`, `FomcMeeting`, `RateChange`, `parse_calendar`, `parse_decision`, `parse_rate_table` from `resolution_finder.fed_pages`
- Consumes (existing): `Market`, `Verdict` from `resolution_finder.models`
- Produces:
  - `@dataclass(frozen=True) RateOption(label: str, kind: str, bps: int = 0, at_least: bool = False, direction: int = 0)`
  - `def parse_option(label: str) -> Optional[RateOption]`
  - `def is_fed_decision_market(market: Market) -> bool`
  - `@dataclass(frozen=True) MeetingTarget(year: int, month: int, start: Optional[date] = None, decision_date: Optional[date] = None)`
  - `def meeting_target(market: Market) -> Optional[MeetingTarget]`
  - `def resolve_fed_decision(market: Market, fetch: Fetcher) -> Optional[list[Verdict]]`
  - (module-private, tested directly) `def _matching_options(options: list[RateOption], change_bps: float, rounding: bool) -> list[RateOption]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fed_decision.py
import dataclasses
from datetime import date
from pathlib import Path

from resolution_finder.fed_decision import (
    MeetingTarget, RateOption, _matching_options, is_fed_decision_market,
    meeting_target, parse_option, resolve_fed_decision,
)
from resolution_finder.fed_pages import CALENDAR_URL, FED_BASE_URL, RATE_TABLE_URL
from resolution_finder.models import Market
from resolution_finder.page_fetch import FetchError

FIXTURES = Path(__file__).parent / "fixtures" / "fed"
DECEMBER_2025_STATEMENT_URL = FED_BASE_URL + "/newsevents/pressreleases/monetary20251210a.htm"
JANUARY_2026_STATEMENT_URL = FED_BASE_URL + "/newsevents/pressreleases/monetary20260128a.htm"
DECEMBER_2024_STATEMENT_URL = FED_BASE_URL + "/newsevents/pressreleases/monetary20241218a.htm"
PAGES = {
    CALENDAR_URL: "fomccalendars.htm",
    RATE_TABLE_URL: "openmarket.htm",
    DECEMBER_2025_STATEMENT_URL: "monetary20251210a.htm",
    JANUARY_2026_STATEMENT_URL: "monetary20260128a.htm",
    DECEMBER_2024_STATEMENT_URL: "monetary20241218a.htm",
}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_fetch(overrides=None):
    """Serves the real saved pages. A URL not in PAGES raises KeyError, so a
    test fails loudly if the resolver asks for a page it shouldn't."""
    fetched = []

    def fetch(url):
        fetched.append(url)
        if overrides and url in overrides:
            return overrides[url]
        return fixture(PAGES[url])

    fetch.fetched = fetched
    return fetch


# Verbatim from data/markets.json (pulled from Polymarket), truth "25 bps decrease".
FED_DECISION_IN_DECEMBER_2025 = Market(
    id="fed-decision-in-december",
    title="Fed decision in December?",
    description=(
        "The FED interest rates are defined in this market by the upper bound of the target federal funds range. The decisions on the target federal fund range are made by the Federal Open Market Committee (FOMC) meetings.\n\n"
        "This market will resolve to the amount of basis points the upper bound of the target federal funds rate is changed by versus the level it was prior to the Federal Reserve's December 2025 meeting.\n\n"
        "If the target federal funds rate is changed to a level not expressed in the displayed options, the change will be rounded up to the nearest 25 and will resolve to the relevant bracket. (e.g. if there's a cut/increase of 12.5 bps it will be considered to be 25 bps)\n\n"
        "The resolution source for this market is the FOMC’s statement after its meeting scheduled for December 9 - 10, 2025 according to the official calendar: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm.\n\n"
        "The level and change of the target federal funds rate is also published at the official website of the Federal Reserve at https://www.federalreserve.gov/monetarypolicy/openmarket.htm.\n\n"
        "This market may resolve as soon as the FOMC’s statement for their December meeting with relevant data is issued. If no statement is released by the end date of the next scheduled meeting, this market will resolve to the \"No change\" bracket.\n"
    ),
    options=["50+ bps decrease", "25 bps decrease", "No change", "25+ bps increase"],
    close_date=date(2025, 12, 10),
)

# Verbatim from data/markets.json, truth "No change".
FED_DECISION_IN_JANUARY_2026 = Market(
    id="fed-decision-in-january",
    title="Fed decision in January?",
    description=(
        "The FED interest rates are defined in this market by the upper bound of the target federal funds range. The decisions on the target federal fund range are made by the Federal Open Market Committee (FOMC) meetings.\n\n"
        "This market will resolve to the amount of basis points the upper bound of the target federal funds rate is changed by versus the level it was prior to the Federal Reserve's January 2026 meeting.\n\n"
        "If the target federal funds rate is changed to a level not expressed in the displayed options, the change will be rounded up to the nearest 25 and will resolve to the relevant bracket. (e.g. if there's a cut/increase of 12.5 bps it will be considered to be 25 bps)\n\n"
        "The resolution source for this market is the FOMC’s statement after its meeting scheduled for January 27 - 28, 2026 according to the official calendar: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm.\n\n"
        "The level and change of the target federal funds rate is also published at the official website of the Federal Reserve at https://www.federalreserve.gov/monetarypolicy/openmarket.htm.\n\n"
        "This market may resolve as soon as the FOMC’s statement for their January meeting with relevant data is issued. If no statement is released by the end date of the next scheduled meeting, this market will resolve to the \"No change\" bracket.\n"
    ),
    options=["50+ bps decrease", "25 bps decrease", "No change", "25+ bps increase"],
    close_date=date(2026, 1, 28),
)

# Verbatim from data/markets.json, truth "25 bps decrease". One-line
# description: no rules stated, so no rounding.
FED_INTEREST_RATES_DECEMBER_2024 = Market(
    id="fed-interest-rates-december-2024",
    title="Fed decision in December?",
    description="This is a market on the anticipated Federal Reserve interest rate decision for December 2024.",
    options=["75+ bps decrease", "50 bps decrease", "25 bps decrease", "No Change", "25+ bps increase", "Other"],
    close_date=date(2024, 12, 18),
)

# Verbatim from data/markets.json -- a Fed market, but NOT a rate-decision one.
HOW_MANY_DISSENT = Market(
    id="how-many-dissent-at-the-next-fed-meeting-113",
    title="How many dissent at the next Fed meeting?",
    description=(
        "The next Federal Open Market Committee (FOMC) meeting is scheduled for April 28-29, 2026. "
        "The policy decision will be announced at 2:00 PM Eastern Time on April 29, followed by the Fed "
        "Chair’s press conference at around 2:30 PM ET."
    ),
    options=["0", "1", "2", "3", "4+"],
    close_date=date(2026, 4, 29),
)


def one_liner(month_year: str, close_date: date) -> Market:
    """Constructed test input mirroring the real one-line description shape,
    for meetings no real market in the dataset covers."""
    return Market(
        id="constructed-fed-market",
        title=f"Fed decision in {month_year.split()[0]}?",
        description=f"This is a market on predictions for the Federal Reserve's interest rates in {month_year}.",
        options=["25 bps decrease", "No change", "25+ bps increase"],
        close_date=close_date,
    )


# --- recognition --------------------------------------------------------

def test_parse_option_handles_every_real_label_shape():
    assert parse_option("50+ bps decrease") == RateOption("50+ bps decrease", "change", 50, True, -1)
    assert parse_option("25 bps increase") == RateOption("25 bps increase", "change", 25, False, 1)
    assert parse_option("No Change") == RateOption("No Change", "no_change")
    assert parse_option("Other") == RateOption("Other", "other")
    assert parse_option("4+") is None


def test_real_fed_decision_markets_are_recognized():
    assert is_fed_decision_market(FED_DECISION_IN_DECEMBER_2025)
    assert is_fed_decision_market(FED_INTEREST_RATES_DECEMBER_2024)


def test_other_markets_are_not_recognized():
    assert not is_fed_decision_market(HOW_MANY_DISSENT)
    assert not is_fed_decision_market(dataclasses.replace(FED_DECISION_IN_DECEMBER_2025, options=["Hike", "Hold"]))
    assert not is_fed_decision_market(dataclasses.replace(FED_DECISION_IN_DECEMBER_2025, options=["Other"]))
    assert not is_fed_decision_market(dataclasses.replace(FED_DECISION_IN_DECEMBER_2025, options=[]))


# --- meeting identification ----------------------------------------------

def test_meeting_target_uses_explicit_meeting_dates():
    assert meeting_target(FED_DECISION_IN_DECEMBER_2025) == MeetingTarget(
        2025, 12, date(2025, 12, 9), date(2025, 12, 10))


def test_meeting_target_uses_month_and_year_from_a_one_line_description():
    assert meeting_target(FED_INTEREST_RATES_DECEMBER_2024) == MeetingTarget(2024, 12)


def test_meeting_target_falls_back_to_title_month_and_close_date_year():
    market = dataclasses.replace(FED_INTEREST_RATES_DECEMBER_2024, description="A Federal Reserve rate market.")
    assert meeting_target(market) == MeetingTarget(2024, 12)


def test_meeting_target_refuses_when_the_description_contradicts_itself():
    market = dataclasses.replace(
        FED_DECISION_IN_DECEMBER_2025,
        description=FED_DECISION_IN_DECEMBER_2025.description.replace(
            "December 2025 meeting", "November 2025 meeting"),
    )
    assert meeting_target(market) is None


# --- option mapping -------------------------------------------------------

FULL_SPEC_OPTIONS = [parse_option(o) for o in FED_DECISION_IN_DECEMBER_2025.options]
ONE_LINER_OPTIONS = [parse_option(o) for o in FED_INTEREST_RATES_DECEMBER_2024.options]


def test_matching_options_exact_plus_and_no_change():
    assert [o.label for o in _matching_options(FULL_SPEC_OPTIONS, -25.0, rounding=True)] == ["25 bps decrease"]
    assert [o.label for o in _matching_options(FULL_SPEC_OPTIONS, -75.0, rounding=True)] == ["50+ bps decrease"]
    assert [o.label for o in _matching_options(FULL_SPEC_OPTIONS, 0.0, rounding=True)] == ["No change"]
    assert [o.label for o in _matching_options(ONE_LINER_OPTIONS, -50.0, rounding=False)] == ["50 bps decrease"]


def test_matching_options_rounds_up_only_when_the_description_says_so():
    # Full-spec description: "if there's a cut/increase of 12.5 bps it will be considered to be 25 bps".
    assert [o.label for o in _matching_options(FULL_SPEC_OPTIONS, -12.5, rounding=True)] == ["25 bps decrease"]
    assert _matching_options(ONE_LINER_OPTIONS, -12.5, rounding=False) == []


# --- end to end against the real pages ----------------------------------

def test_resolves_the_real_december_2025_cut():
    verdicts = resolve_fed_decision(FED_DECISION_IN_DECEMBER_2025, fixture_fetch())
    assert {v.option: v.outcome for v in verdicts} == {
        "50+ bps decrease": "NO", "25 bps decrease": "YES", "No change": "NO", "25+ bps increase": "NO",
    }
    winner = next(v for v in verdicts if v.outcome == "YES")
    assert winner.source_url == DECEMBER_2025_STATEMENT_URL
    assert winner.source_type == "primary"
    assert winner.confidence == 1.0
    assert "decided to lower the target range" in winner.evidence_snippet


def test_resolves_the_real_january_2026_hold():
    verdicts = resolve_fed_decision(FED_DECISION_IN_JANUARY_2026, fixture_fetch())
    assert [v.option for v in verdicts if v.outcome == "YES"] == ["No change"]


def test_resolves_the_real_december_2024_one_liner_and_never_picks_other():
    verdicts = resolve_fed_decision(FED_INTEREST_RATES_DECEMBER_2024, fixture_fetch())
    assert [v.option for v in verdicts if v.outcome == "YES"] == ["25 bps decrease"]
    assert len(verdicts) == 6
    assert next(v for v in verdicts if v.option == "Other").outcome == "NO"


def test_non_fed_market_returns_none_without_fetching():
    fetch = fixture_fetch()
    assert resolve_fed_decision(HOW_MANY_DISSENT, fetch) is None
    assert fetch.fetched == []


def test_statement_not_published_yet_is_no_evidence():
    # The saved calendar (2026-09-17) has the October 27-28 2026 meeting with no statement link.
    fetch = fixture_fetch()
    (verdict,) = resolve_fed_decision(one_liner("October 2026", date(2026, 10, 28)), fetch)
    assert verdict.outcome == "NO_EVIDENCE"
    assert verdict.option is None
    assert fetch.fetched == [CALENDAR_URL]


def test_notation_vote_is_never_treated_as_a_meeting():
    # August 2025's only calendar entry is "22 (notation vote)", which has a statement link.
    (verdict,) = resolve_fed_decision(one_liner("August 2025", date(2025, 8, 22)), fixture_fetch())
    assert verdict.outcome == "UNCLEAR"


def test_close_date_far_from_the_meeting_is_unclear_and_reads_no_statement():
    fetch = fixture_fetch()
    market = dataclasses.replace(FED_DECISION_IN_DECEMBER_2025, close_date=date(2025, 12, 20))
    (verdict,) = resolve_fed_decision(market, fetch)
    assert verdict.outcome == "UNCLEAR"
    assert DECEMBER_2025_STATEMENT_URL not in fetch.fetched


def test_rate_table_disagreeing_with_the_statement_is_unclear():
    real = fixture("openmarket.htm")
    # Deliberate mutation of the real page: December 11 2025's level changed.
    mutated = real.replace('<td class="stub" nowrap="nowrap">3.50-3.75</td>',
                           '<td class="stub" nowrap="nowrap">3.25-3.50</td>', 1)
    assert mutated != real
    (verdict,) = resolve_fed_decision(FED_DECISION_IN_DECEMBER_2025, fixture_fetch({RATE_TABLE_URL: mutated}))
    assert verdict.outcome == "UNCLEAR"


def test_rate_table_not_yet_updated_after_a_change_is_unclear():
    real = fixture("openmarket.htm")
    # Deliberate mutation of the real page: the December 11 2025 row removed
    # (the first "December 11" row on the page is 2025's; 2007's comes later).
    start = real.index("December 11</td>")
    row_start = real.rindex("<tr>", 0, start)
    row_end = real.index("</tr>", start) + len("</tr>")
    mutated = real[:row_start] + real[row_end:]
    (verdict,) = resolve_fed_decision(FED_DECISION_IN_DECEMBER_2025, fixture_fetch({RATE_TABLE_URL: mutated}))
    assert verdict.outcome == "UNCLEAR"


def test_statement_saying_hold_while_the_table_lists_a_change_is_unclear():
    real = fixture("monetary20251210a.htm")
    # Deliberate mutation of the real statement into a hold at the prior level.
    mutated = real.replace(
        "decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3‑3/4 percent",
        "decided to maintain the target range for the federal funds rate at 3-3/4 to 4 percent",
    )
    assert mutated != real
    (verdict,) = resolve_fed_decision(FED_DECISION_IN_DECEMBER_2025,
                                      fixture_fetch({DECEMBER_2025_STATEMENT_URL: mutated}))
    assert verdict.outcome == "UNCLEAR"


def test_fetch_failure_is_unclear():
    def failing_fetch(url):
        raise FetchError(f"could not fetch {url}")

    (verdict,) = resolve_fed_decision(FED_DECISION_IN_DECEMBER_2025, failing_fetch)
    assert verdict.outcome == "UNCLEAR"
    assert verdict.source_url == CALENDAR_URL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: test command with `tests/test_fed_decision.py`
Expected: collection error `ModuleNotFoundError: No module named 'resolution_finder.fed_decision'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: test command with `tests/test_fed_decision.py`
Expected: 20 passed

If `test_statement_saying_hold_while_the_table_lists_a_change_is_unclear` fails on `assert mutated != real`, print `repr(real[real.index("decided to lower"):][:140])` and copy the exact characters (the fixture has an ASCII hyphen in `3-1/2` and U+2011 in `3‑3/4`) -- do not change the resolver.

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/fed_decision.py tests/test_fed_decision.py
git commit -m "Add fed_decision: resolve Fed-decision markets from FOMC statement + rate table

Statement and rate table must agree; any mismatch, parse failure, fetch
failure or ambiguous option ends in UNCLEAR. Statement not yet released
is NO_EVIDENCE.

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

---

### Task 4: `structured_resolvers.py` + pipeline hook

**Files:**
- Create: `resolution_finder/structured_resolvers.py`
- Modify: `resolution_finder/pipeline.py` (imports; `run_pipeline` signature and loop, lines 69-91; `_scan_market` signature and body, lines 94-120)
- Test: `tests/test_structured_resolvers.py`, `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `resolve_fed_decision(market, fetch) -> Optional[list[Verdict]]` (Task 3); `Fetcher`, `http_fetch` (Task 1)
- Produces:
  - `STRUCTURED_RESOLVERS: list[Callable[[Market, Fetcher], Optional[list[Verdict]]]]`
  - `def resolve_structured(market: Market, fetch: Fetcher = http_fetch) -> Optional[list[Verdict]]`
  - `pipeline.StructuredResolver = Callable[[Market], Optional[list[Verdict]]]`
  - `run_pipeline(market_provider, db_path, verdict_engine=decide, peer_checker=_default_peer_checker, structured_resolver=resolve_structured)` -- new parameter LAST (an existing test reads `run_pipeline.__defaults__[1]`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_structured_resolvers.py
from datetime import date

from resolution_finder import structured_resolvers
from resolution_finder.models import Market, Verdict
from resolution_finder.structured_resolvers import resolve_structured

CLARITY = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description="If these conditions are not met by the deadline, the market resolves to \"No\".",
    options=[],
    close_date=date(2026, 12, 31),
)


def test_non_structured_market_returns_none_without_fetching():
    fetched = []
    assert resolve_structured(CLARITY, fetch=fetched.append) is None
    assert fetched == []


def test_first_resolver_that_claims_the_market_wins(monkeypatch):
    claimed = [Verdict(outcome="UNCLEAR", confidence=0.0, evidence_snippet="x",
                       source_url=None, source_type="primary")]
    calls = []
    monkeypatch.setattr(structured_resolvers, "STRUCTURED_RESOLVERS", [
        lambda market, fetch: calls.append("first") or None,
        lambda market, fetch: calls.append("second") or claimed,
        lambda market, fetch: calls.append("third") or None,
    ])
    assert resolve_structured(CLARITY, fetch=lambda url: "") is claimed
    assert calls == ["first", "second"]
```

Append to `tests/test_pipeline.py`:

```python
def test_run_pipeline_saves_structured_verdicts_and_skips_peer_check_and_news():
    # run_pipeline logs and skips a market whose scan raises, so "must not
    # run" is checked by recording calls, not by raising inside the stubs.
    statement_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20251210a.htm"
    peer_calls, engine_calls = [], []

    def fake_structured_resolver(market):
        return [
            Verdict(outcome="YES", option="25 bps decrease", confidence=1.0,
                    evidence_snippet="the Committee decided to lower the target range",
                    source_url=statement_url, source_type="primary"),
            Verdict(outcome="NO", option="No change", confidence=1.0,
                    evidence_snippet="the Committee decided to lower the target range",
                    source_url=statement_url, source_type="primary"),
        ]

    db_path = temp_db_path()
    with patch("resolution_finder.pipeline.retrieve_evidence") as mock_retrieve:
        run_pipeline(
            FakeMarketProvider(), db_path,
            verdict_engine=lambda market, ranked: engine_calls.append(market.id),
            peer_checker=lambda market: peer_calls.append(market.id),
            structured_resolver=fake_structured_resolver,
        )

    findings = {f["option"]: f for f in get_latest_findings(db_path)}
    assert findings["25 bps decrease"]["outcome"] == "YES"
    assert findings["25 bps decrease"]["source_type"] == "primary"
    assert findings["No change"]["outcome"] == "NO"
    assert peer_calls == []
    assert engine_calls == []
    mock_retrieve.assert_not_called()
    os.remove(db_path)


def test_run_pipeline_defaults_structured_resolver_to_resolve_structured():
    import resolution_finder.pipeline as pipeline_module
    assert pipeline_module.run_pipeline.__defaults__[2] is pipeline_module.resolve_structured
```

- [ ] **Step 2: Run tests to verify they fail**

Run: test command with `tests/test_structured_resolvers.py tests/test_pipeline.py`
Expected: `ModuleNotFoundError: No module named 'resolution_finder.structured_resolvers'` and, in test_pipeline, `TypeError: run_pipeline() got an unexpected keyword argument 'structured_resolver'`

- [ ] **Step 3: Implement `structured_resolvers.py`**

```python
# resolution_finder/structured_resolvers.py
"""Resolvers that answer a market straight from its own named, structured
resolution source (an official page or data feed) instead of news search.

Each resolver takes (market, fetch) and returns None for "not my market
type", or a list of verdicts -- possibly a single UNCLEAR/NO_EVIDENCE --
for "mine". A claimed market never falls through to peer-check or news:
for these market types the news path is the known source of wrong answers
(Fed markets scored 0/14 there; see unsupported-market-types.md item 7).

Fed-decision markets are the only entry today. The price-market handoff
(docs/superpowers/plans/2026-09-17-price-markets-handoff.md) plugs in here.
"""
from typing import Callable, Optional

from resolution_finder.fed_decision import resolve_fed_decision
from resolution_finder.models import Market, Verdict
from resolution_finder.page_fetch import Fetcher, http_fetch

STRUCTURED_RESOLVERS: list[Callable[[Market, Fetcher], Optional[list[Verdict]]]] = [
    resolve_fed_decision,
]


def resolve_structured(market: Market, fetch: Fetcher = http_fetch) -> Optional[list[Verdict]]:
    for resolver in STRUCTURED_RESOLVERS:
        verdicts = resolver(market, fetch)
        if verdicts is not None:
            return verdicts
    return None
```

- [ ] **Step 4: Wire into `pipeline.py`**

Add the import next to the other `resolution_finder` imports:

```python
from resolution_finder.structured_resolvers import resolve_structured
```

Below `PeerChecker = Callable[[Market], Optional[Verdict]]` add:

```python
StructuredResolver = Callable[[Market], Optional[list[Verdict]]]
```

Replace `run_pipeline`'s signature and loop (keep the docstring, adding the new paragraph shown):

```python
def run_pipeline(
    market_provider: MarketProvider,
    db_path: str,
    verdict_engine: VerdictEngine = decide,
    peer_checker: PeerChecker = _default_peer_checker,
    structured_resolver: StructuredResolver = resolve_structured,
) -> None:
    """Scan every unresolved market and store a proposed verdict for review.

    `verdict_engine` is injected the same way `market_provider` is, so the
    rule-based engine can be swapped for an AI-based one with no changes here
    or further upstream. `peer_checker` is injected the same way, so the
    Polymarket cross-check can be swapped out (or stubbed in tests) without
    editing the pipeline. The default already honours `PEER_MARKET_ENABLED`
    (see `_default_peer_checker`).

    `structured_resolver` runs first and is injected the same way: a market
    it claims (e.g. a Fed rate decision, answered from federalreserve.gov)
    is saved from its verdicts and never reaches peer-check or news.
    """
    init_db(db_path)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    for market in market_provider.get_unresolved_markets():
        try:
            _scan_market(market, db_path, run_timestamp, verdict_engine, peer_checker, structured_resolver)
        except Exception as exc:  # noqa: BLE001 - one bad market must not abort the run
            logger.exception("Skipping market %s after error: %s", market.id, exc)
```

Change `_scan_market`'s signature to:

```python
def _scan_market(
    market: Market,
    db_path: str,
    run_timestamp: str,
    verdict_engine: VerdictEngine,
    peer_checker: PeerChecker,
    structured_resolver: StructuredResolver,
) -> None:
```

and insert between `availability = check_availability(market)` and `peer_verdict = peer_checker(market)`:

```python
    # A market answered from its own official structured source (Fed rate
    # decisions today) never falls through to peer-check or news -- for
    # these types the news path is where wrong answers come from. See
    # structured_resolvers.py.
    structured_verdicts = structured_resolver(market)
    if structured_verdicts is not None:
        for verdict in structured_verdicts:
            save_finding(db_path, market.id, run_timestamp, verdict, availability)
        return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: test command with `tests/test_structured_resolvers.py tests/test_pipeline.py`
Expected: all passed (existing pipeline tests use a CLARITY market, which `resolve_structured` returns None for without any network call)

- [ ] **Step 6: Run the full suite**

Run: test command with `tests/`
Expected: all passed (310 before this plan + the new tests)

- [ ] **Step 7: Commit**

```bash
git add resolution_finder/structured_resolvers.py resolution_finder/pipeline.py tests/test_structured_resolvers.py tests/test_pipeline.py
git commit -m "Run structured resolvers before peer-check and news in the pipeline

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

---

### Task 5: Eval harness -- cached page fetcher + structured path in `run_eval.py`

**Files:**
- Modify: `resolution_finder/eval_cache.py` (append)
- Modify: `run_eval.py` (imports lines 49-56; `_run_one_market` lines 165-275; two new helpers above it)
- Test: `tests/test_eval_cache.py` (append)

**Interfaces:**
- Consumes: `Fetcher`, `FetchError`, `http_fetch` (Task 1); `resolve_structured` (Task 4)
- Produces:
  - `eval_cache.STRUCTURED_PAGES_KEY = "__structured_pages__"`
  - `class eval_cache.CachedPageFetcher(cache: dict, live: bool, fetch: Fetcher = http_fetch)`; callable `(url) -> str`; attributes `urls: list[str]`, `fetched_live: bool`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_eval_cache.py`)

```python
import pytest

from resolution_finder.page_fetch import FetchError


def test_cached_page_fetcher_replays_a_cached_page_without_fetching():
    cache = {eval_cache.STRUCTURED_PAGES_KEY: {"https://x/a.htm": "cached"}}
    calls = []
    fetcher = eval_cache.CachedPageFetcher(cache, live=False, fetch=lambda url: calls.append(url) or "live")
    assert fetcher("https://x/a.htm") == "cached"
    assert calls == []
    assert fetcher.fetched_live is False
    assert fetcher.urls == ["https://x/a.htm"]


def test_cached_page_fetcher_fetches_and_stores_a_miss():
    cache = {}
    fetcher = eval_cache.CachedPageFetcher(cache, live=False, fetch=lambda url: "live")
    assert fetcher("https://x/a.htm") == "live"
    assert cache[eval_cache.STRUCTURED_PAGES_KEY] == {"https://x/a.htm": "live"}
    assert fetcher.fetched_live is True


def test_cached_page_fetcher_live_mode_refetches_and_overwrites():
    cache = {eval_cache.STRUCTURED_PAGES_KEY: {"https://x/a.htm": "old"}}
    fetcher = eval_cache.CachedPageFetcher(cache, live=True, fetch=lambda url: "new")
    assert fetcher("https://x/a.htm") == "new"
    assert cache[eval_cache.STRUCTURED_PAGES_KEY]["https://x/a.htm"] == "new"


def test_cached_page_fetcher_caches_nothing_on_fetch_failure():
    def failing(url):
        raise FetchError("down")

    cache = {}
    fetcher = eval_cache.CachedPageFetcher(cache, live=False, fetch=failing)
    with pytest.raises(FetchError):
        fetcher("https://x/a.htm")
    assert cache[eval_cache.STRUCTURED_PAGES_KEY] == {}


def test_structured_pages_key_never_collides_with_a_market_snapshot_lookup():
    cache = {eval_cache.STRUCTURED_PAGES_KEY: {"https://x/a.htm": "page"}}
    assert eval_cache.load_market_snapshot(cache, "fed-decision-in-december") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: test command with `tests/test_eval_cache.py`
Expected: FAIL with `AttributeError: module 'resolution_finder.eval_cache' has no attribute 'STRUCTURED_PAGES_KEY'`

- [ ] **Step 3: Implement** (append to `resolution_finder/eval_cache.py`; add `from resolution_finder.page_fetch import Fetcher, http_fetch` to its imports)

```python
# Structured resolvers (resolution_finder/structured_resolvers.py) read
# official pages, not news. Their pages are cached under this reserved
# top-level key -- never a market id -- so replays are deterministic and
# offline like retrieval snapshots are. Eval harness only: production
# fetching (page_fetch.http_fetch) never caches.
STRUCTURED_PAGES_KEY = "__structured_pages__"


class CachedPageFetcher:
    """A `Fetcher` for run_eval.py: serves a page from the cache when it has
    it (unless `live`), otherwise fetches and stores it. A FetchError
    propagates and stores nothing, so a failed fetch is retried next run."""

    def __init__(self, cache: dict, live: bool, fetch: Fetcher = http_fetch):
        self._pages = cache.setdefault(STRUCTURED_PAGES_KEY, {})
        self._live = live
        self._fetch = fetch
        self.urls: list[str] = []
        self.fetched_live = False

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        if not self._live and url in self._pages:
            return self._pages[url]
        page = self._fetch(url)
        self._pages[url] = page
        self.fetched_live = True
        return page
```

- [ ] **Step 4: Run tests to verify they pass**

Run: test command with `tests/test_eval_cache.py`
Expected: all passed

- [ ] **Step 5: Wire into `run_eval.py`**

Imports -- replace the `eval_cache` import line and add one:

```python
from resolution_finder.eval_cache import (
    CachedPageFetcher, load_cache, save_cache, load_market_snapshot, store_market_snapshot,
)
from resolution_finder.structured_resolvers import resolve_structured
```

Add these two helpers directly above `def _run_one_market`:

```python
def _print_verdicts(verdicts: list) -> None:
    for v in verdicts:
        print(f"  VERDICT: outcome={v.outcome} option={v.option} confidence={v.confidence:.3f}")
        if v.evidence_snippet:
            print(f"    evidence: {v.evidence_snippet[:200]!r}")
        if v.source_url:
            print(f"    source: {v.source_url}")


def _result_record(market: Market, ground_truth: str | None, verdicts: list,
                   from_cache: bool, trace: dict) -> dict:
    return {
        "market_id": market.id,
        "ground_truth": ground_truth,
        "from_cache": from_cache,
        "verdicts": [
            {
                "outcome": v.outcome, "option": v.option, "confidence": round(v.confidence, 3),
                "evidence_snippet": v.evidence_snippet, "source_url": v.source_url,
                "source_type": v.source_type,
            }
            for v in verdicts
        ],
        "verdict_class": _score(verdicts, ground_truth, market.options),
        "trace": trace,
    }
```

In `_run_one_market`, insert right after the four opening `print(...)` lines (before `snapshot = None if live else ...`):

```python
    # Same order as pipeline._scan_market: a market a structured resolver
    # claims (Fed rate decisions today) is answered from its official pages
    # and never reaches news retrieval. Its pages are cached in the eval
    # cache -- eval harness only, never in production.
    page_fetcher = CachedPageFetcher(cache, live)
    structured_verdicts = resolve_structured(market, fetch=page_fetcher)
    if structured_verdicts is not None:
        if page_fetcher.fetched_live:
            save_cache(cache)
        print(f"  [structured resolver; pages: {page_fetcher.urls}]")
        _print_verdicts(structured_verdicts)
        return _result_record(
            market, ground_truth, structured_verdicts,
            from_cache=not page_fetcher.fetched_live,
            trace={"structured_resolver": True, "fetched_urls": page_fetcher.urls},
        )
```

Replace the tail of `_run_one_market`, from `verdicts = decide(market, ranked)` through the end of the returned dict, with (the trace dict and its comment are unchanged):

```python
    verdicts = decide(market, ranked)
    if not isinstance(verdicts, list):
        verdicts = [verdicts]
    _print_verdicts(verdicts)

    return _result_record(
        market, ground_truth, verdicts, from_cache=snapshot is not None,
        # Pipeline-stage detail, not just the final outcome -- so a later
        # debugging session can see WHERE a market got stuck (no
        # candidates? candidates but extraction failed? extracted but
        # ranked below threshold? ranked but no verdict-engine match?)
        # straight from this saved record, without re-running any
        # network calls.
        trace={
            "queries": queries,
            "candidates_by_type": candidates_by_type,
            "articles_with_text_count": len(articles_with_text),
            "fetch_failure_urls": fetch_failure_urls,
            "ranked_above_threshold": ranked_detail,
        },
    )
```

- [ ] **Step 6: Run the full suite**

Run: test command with `tests/`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add resolution_finder/eval_cache.py run_eval.py tests/test_eval_cache.py
git commit -m "Run structured resolvers in run_eval, caching their pages in the eval cache only

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

---

### Task 6: Measure on real markets, then update the tracking docs

**Files:**
- Create (untracked, gitignored): `scratchpad/run_fed_eval.py`, `scratchpad/run_full_replay.py`, `scratchpad/compare_wrong.py`
- Modify: `docs/superpowers/plans/2026-08-25-unsupported-market-types.md` (item 7), `docs/superpowers/plans/2026-09-16-opus-session-agenda.md`

**Interfaces:**
- Consumes: `run_eval.run_eval(market_ids: set[str], live: bool)` (existing)

- [ ] **Step 1: Run the 14 Fed markets live**

Create `scratchpad/run_fed_eval.py` (a script avoids the known WSL argument-quoting failure with space-separated ids):

```python
from run_eval import run_eval

FED_MARKET_IDS = {
    "fed-decision-in-january", "fed-decision-in-december", "fed-decision-in-april",
    "fed-decision-in-march-885", "fed-decision-in-october", "fed-decision-in-september",
    "fed-interest-rates-january-2025", "fed-decision-in-june-825", "fed-decision-in-july-181",
    "fed-decision-in-july", "fed-decision-in-june", "fed-decision-in-may-2025",
    "fed-decision-in-march", "fed-interest-rates-december-2024",
}
run_eval(FED_MARKET_IDS, live=True)
```

Run: `wsl -- bash -c "cd '/mnt/c/Users/liam/Documents/Resolution Finder/.worktrees/resolution-finder-scanner' && PYTHONPATH=. .venv-wsl/bin/python scratchpad/run_fed_eval.py"`
Expected scorecard line: `correct=14 wrong=0 unresolved=0 no_ground_truth=0`

If any market is not correct: stop and read its printed UNCLEAR reason. A wrong result is a bug to fix before continuing. An UNCLEAR result means a check refused -- find out which real page detail caused it; loosen a check only if the real page shows it was a false refusal, and add a fixture-based test for that detail.

- [ ] **Step 2: Replay the full batch and confirm no market became newly wrong**

Create `scratchpad/run_full_replay.py`:

```python
from run_eval import run_eval

run_eval(set(), live=False)
```

Create `scratchpad/compare_wrong.py`:

```python
import json

with open("data/eval_history.jsonl", encoding="utf-8") as f:
    runs = [json.loads(line) for line in f]
latest, earlier = runs[-1], runs[:-1]
previous_class = {}
for run in earlier:
    for result in run["results"]:
        previous_class[result["market_id"]] = result["verdict_class"]

print("latest summary:", latest["summary"])
newly_wrong = [r["market_id"] for r in latest["results"]
               if r["verdict_class"] == "wrong" and previous_class.get(r["market_id"]) != "wrong"]
print("newly wrong:", newly_wrong)
```

Run: `wsl -- bash -c "cd '/mnt/c/Users/liam/Documents/Resolution Finder/.worktrees/resolution-finder-scanner' && PYTHONPATH=. .venv-wsl/bin/python scratchpad/run_full_replay.py > scratchpad/full_replay.log 2>&1; PYTHONPATH=. .venv-wsl/bin/python scratchpad/compare_wrong.py"`
Expected: `newly wrong: []`. (Markets without a cached retrieval snapshot fetch live and take longer; the cache saves after every market, so an interrupted run resumes.)

If `newly wrong` is not empty, stop and investigate before Step 3 -- the Fed resolver only claims Fed-decision markets, so a newly wrong non-Fed market means either live re-fetch variance (check `from_cache` for it in the latest record) or an unintended change.

- [ ] **Step 3: Update item 7 in `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`**

Append at the end of the "## 7. Fed interest-rate decision markets" section (before "## User input captured 2026-09-16"), filling in the real numbers from Steps 1-2:

```markdown
**Status 2026-09-17: resolved for Fed-decision markets.** New
`resolution_finder/fed_decision.py` answers them from the FOMC calendar,
the FOMC statement and the Fed's rate table (federalreserve.gov), with
statement and table required to agree; claimed markets never reach
news retrieval. Eval on the 14 real markets: correct=<N> wrong=<N>
unresolved=<N>. Full-batch replay: newly wrong = <list or none>. Spec:
docs/superpowers/specs/2026-09-17-fed-decision-resolver-design.md.

Still open from this item: the `_winner_hypotheses` wording bug itself
("{option} has won {title}") for OTHER multi-outcome markets whose
options are outcome descriptions -- the Fed resolver sidesteps it, it
doesn't fix it. Follow-up noted: "How many dissent" markets can be
answered from the same statement ("Voting against this action were ...").
```

- [ ] **Step 4: Update `docs/superpowers/plans/2026-09-16-opus-session-agenda.md`**

Append at the end of the file:

```markdown
## Done 2026-09-17 (Opus planning session)

- Fed-decision markets: built (see unsupported-market-types.md item 7
  status for the eval numbers).
- Crypto/currency/gold/stock price markets: deliberately NOT built -- no
  free source licensed for commercial settlement use was found. Full
  handoff: docs/superpowers/plans/2026-09-17-price-markets-handoff.md
  (includes the submarket-reopening requirement).
```

- [ ] **Step 5: Commit the docs**

```bash
git add docs/superpowers/plans/2026-08-25-unsupported-market-types.md docs/superpowers/plans/2026-09-16-opus-session-agenda.md
git commit -m "Record Fed-decision resolver results; point agenda at price-markets handoff

Co-Authored-By: Claude <Model> <noreply@anthropic.com>"
```

Do NOT commit `data/eval_history.jsonl`, `data/markets.json` or anything in `scratchpad/`.
