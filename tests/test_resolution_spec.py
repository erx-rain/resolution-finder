# tests/test_resolution_spec.py
from datetime import date
from resolution_finder.models import Market
from resolution_finder.resolution_spec import (
    parse_resolution_spec,
    check_availability,
    extract_backstop_deadline,
    extract_default_outcome,
    extract_scheduled_event_date,
    names_primary_source,
    AVAILABLE_SCHEDULED_EVENT_PASSED,
    AVAILABLE_CLOSE_DATE_PASSED,
    AVAILABLE_BACKSTOP_DEADLINE_PASSED,
)

# Every description string below is copied VERBATIM from a real market in
# data/markets.json (per this project's no-ai-authored-test-data rule) --
# not paraphrased or simplified.

CLARITY_DESCRIPTION = (
    'This market resolves to "Yes" if the Digital Asset Market Clarity Act '
    "of 2025 (H.R. 3633) is approved by both the U.S. House of "
    "Representatives and the U.S. Senate, and is signed into law no later "
    "than December 31, 2026, at 11:59 PM ET. If these conditions are not "
    'met by the deadline, the market resolves to "No". The primary '
    "resolution source will be the legislation tracker on Congress.gov, "
    "along with other official information published by the United States "
    "government. If necessary, a consensus of credible reporting may also "
    "be used to determine the outcome."
)

CANADIAN_STANLEY_CUP_DESCRIPTION = (
    'This market will resolve to "Yes" if a NHL team from Canada wins the '
    'NHL Stanley Cup. Otherwise, this market will resolve to "No".\n\n'
    "If the NHL Playoffs have not been completed by July 24, 2025, 11:59 "
    'PM ET, this market will resolve to "No".\n\n'
    "The resolution source for this market will be official information "
    "from the NHL."
)

EGYPT_ELECTION_DESCRIPTION = (
    "Egypt’s 2023 presidential election is currently scheduled to "
    "take place on December 10-12, 2023.\n\n"
    'This market will resolve to "Yes" if Incumbent President Abdel '
    "Fattah el-Sisi wins the 2023 Egyptian presidential election. "
    'Otherwise, this market will resolve to "No".\n\n'
    "This market includes any potential second round. If the result of "
    "this election isn't known by October 31, 2024, 11:59:59 PM ET, the "
    "market will resolve to 50-50.\n\n"
    "The primary resolution source for this market will be official "
    "information from the Egyptian government, however a consensus of "
    "credible reporting will also suffice."
)

BITCOIN_DESCRIPTION = (
    'This market will resolve to "Yes" if the Binance 1 minute candle for '
    'BTC/USDT 12:00 in the ET timezone (noon) on the date specified in '
    'the title has a final "Close" price higher than the price specified '
    'in the title. Otherwise, this market will resolve to "No".\n\n'
    "The resolution source for this market is Binance, specifically the "
    'BTC/USDT "Close" prices currently available at '
    "https://www.binance.com/en/trade/BTC_USDT with \"1m\" and \"Candles\" "
    "selected on the top bar.\n\n"
    "Please note that this market is about the price according to "
    "Binance BTC/USDT, not according to other exchanges or trading "
    "pairs.\n\n"
    "Price precision is determined by the number of decimal places in "
    "the source."
)

IOWA_LSU_DESCRIPTION = (
    "In the upcoming Women's NCAAB game, scheduled for April 1 at 7:15 PM "
    "ET:\n\n"
    'If the Iowa Hawkeyes win, the market will resolve to “Iowa”.\n\n'
    "If the Louisiana State University Tigers win, the market will "
    'resolve to “LSU”.'
)

WORLD_SERIES_2025_DESCRIPTION = (
    "This market will resolve according to the team that wins the 2025 "
    "MLB World Series. \n\n"
    "If at any point it becomes impossible for this team to win the "
    "World Series based on the rules of the MLB (e.g., they are "
    "eliminated in the playoff bracket), this market will resolve "
    "immediately to “No”.\n\n"
    "If the 2025  MLB season is permanently canceled or has not been "
    "completed by February 28, 2026, 11:59 PM this market will resolve "
    "to “Other”."
)


def make_market(description, close_date=None, market_id="m", title="t", options=None):
    return Market(id=market_id, title=title, description=description,
                  options=options or [], close_date=close_date)


def test_extracts_backstop_deadline_and_default_from_clarity_act():
    deadline, raw = extract_backstop_deadline(CLARITY_DESCRIPTION)
    assert deadline == date(2026, 12, 31)
    assert raw == "December 31, 2026"
    assert extract_default_outcome(CLARITY_DESCRIPTION) == "NO"
    assert names_primary_source(CLARITY_DESCRIPTION) is True


def test_backstop_deadline_differs_from_close_date_stanley_cup():
    # Real finding (2026-09-08): close_date is when TRADING stops, the
    # description deadline is the market's own BACKSTOP for when its
    # default applies -- they are different dates, not competing values
    # for the same field. This market's close_date is 2025-06-24 but its
    # description backstop is a month later.
    spec = parse_resolution_spec(make_market(
        CANADIAN_STANLEY_CUP_DESCRIPTION, close_date=date(2025, 6, 24),
    ))
    assert spec.backstop_deadline == date(2025, 7, 24)
    assert spec.default_outcome == "NO"
    assert spec.names_primary_source is True


def test_default_outcome_is_not_restricted_to_yes_no():
    # Real motivating case: the Egypt election market's stated default is
    # "50-50", a genuine non-binary outcome.
    assert extract_default_outcome(EGYPT_ELECTION_DESCRIPTION) == "50-50"
    assert extract_default_outcome(WORLD_SERIES_2025_DESCRIPTION) == "Other"


def test_scheduled_event_date_uses_first_day_of_a_range():
    # "December 10-12, 2023" -- the market becomes availability-relevant
    # from when the election STARTS, not when it ends.
    date_, raw = extract_scheduled_event_date(EGYPT_ELECTION_DESCRIPTION)
    assert date_ == date(2023, 12, 10)
    assert raw == "December 10-12, 2023"


def test_scheduled_event_with_no_year_returns_raw_text_but_no_date():
    # Real motivating case: "scheduled for April 1 at 7:15 PM ET" names
    # no year anywhere in the sentence. Never guess the year -- but the
    # raw match is still surfaced, since "found scheduling language, no
    # computable date" is different information than "found nothing".
    date_, raw = extract_scheduled_event_date(IOWA_LSU_DESCRIPTION)
    assert date_ is None
    assert raw == "April 1"


def test_no_deadline_or_scheduled_event_for_a_price_threshold_market():
    # Real motivating case: crypto/price markets state a resolution
    # SOURCE (Binance) but no deadline language and no scheduled-event
    # phrasing at all -- parsing must not invent either.
    spec = parse_resolution_spec(make_market(BITCOIN_DESCRIPTION, close_date=date(2026, 8, 17)))
    assert spec.backstop_deadline is None
    assert spec.scheduled_event_date is None
    assert spec.scheduled_event_raw is None
    assert spec.names_primary_source is True  # "resolution source" still matches


def test_empty_description_yields_an_all_empty_spec():
    spec = parse_resolution_spec(make_market(""))
    assert spec.backstop_deadline is None
    assert spec.default_outcome is None
    assert spec.scheduled_event_date is None
    assert spec.names_primary_source is False


# --- check_availability ---

def test_availability_fires_on_scheduled_event_before_close_date():
    # The most precise signal wins even when a later one would also fire.
    # The real description has no year in its scheduled-event sentence
    # (see test_scheduled_event_with_no_year_returns_raw_text_but_no_date
    # above); this variant adds one so the date is actually computable,
    # to test the tier-ordering logic on its own.
    dated_description = IOWA_LSU_DESCRIPTION.replace("April 1 at", "April 1, 2024 at")
    market = make_market(dated_description, close_date=date(2024, 6, 1))
    result = check_availability(market, today=date(2024, 4, 2))
    assert result.available is True
    assert result.reason == AVAILABLE_SCHEDULED_EVENT_PASSED
    assert result.trigger_date == date(2024, 4, 1)


def test_availability_fires_on_close_date_when_no_scheduled_event():
    market = make_market(CLARITY_DESCRIPTION, close_date=date(2026, 12, 31))
    result = check_availability(market, today=date(2027, 1, 5))
    assert result.available is True
    assert result.reason == AVAILABLE_CLOSE_DATE_PASSED
    assert result.trigger_date == date(2026, 12, 31)


def test_availability_fires_on_backstop_deadline_when_close_date_missing():
    # Real motivating case: some markets genuinely have no close_date
    # (e.g. pulled March Madness markets) but do have a description
    # deadline -- availability must not depend on close_date alone.
    market = make_market(CANADIAN_STANLEY_CUP_DESCRIPTION, close_date=None)
    result = check_availability(market, today=date(2025, 8, 1))
    assert result.available is True
    assert result.reason == AVAILABLE_BACKSTOP_DEADLINE_PASSED
    assert result.trigger_date == date(2025, 7, 24)


def test_availability_is_false_before_any_deadline():
    market = make_market(CLARITY_DESCRIPTION, close_date=date(2026, 12, 31))
    result = check_availability(market, today=date(2026, 1, 1))
    assert result.available is False
    assert result.reason is None
    assert result.trigger_date is None


def test_availability_never_invents_a_deadline_for_a_year_less_scheduled_event():
    # The real Iowa/LSU market: no close_date, no backstop deadline, and
    # a scheduled event with no computable year. Availability must stay
    # False rather than guessing -- this is the honest "we don't know"
    # case, not a bug.
    market = make_market(IOWA_LSU_DESCRIPTION, close_date=None)
    result = check_availability(market, today=date(2026, 9, 8))
    assert result.available is False


def test_availability_result_carries_the_parsed_spec_for_context():
    market = make_market(CLARITY_DESCRIPTION, close_date=date(2026, 12, 31))
    result = check_availability(market, today=date(2027, 1, 5))
    assert result.spec.default_outcome == "NO"
