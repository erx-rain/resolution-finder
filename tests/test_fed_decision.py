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
