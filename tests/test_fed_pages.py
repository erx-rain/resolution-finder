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
