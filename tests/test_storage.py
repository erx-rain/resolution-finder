import os
import sqlite3
import tempfile
from datetime import date
from resolution_finder.models import Market, Verdict
from resolution_finder.resolution_spec import (
    AVAILABLE_CLOSE_DATE_PASSED, check_availability,
)
from resolution_finder.storage import (
    init_db, save_finding, get_latest_findings, get_history, set_review_status,
    get_setting, set_setting,
)


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    init_db(path)
    return path


def test_save_and_get_latest_finding():
    db_path = make_temp_db()
    verdict = Verdict(outcome="YES", confidence=0.8, evidence_snippet="signed into law",
                       source_url="https://congress.gov/x", source_type="primary")
    finding_id = save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    assert isinstance(finding_id, int)

    latest = get_latest_findings(db_path)
    assert len(latest) == 1
    assert latest[0]["market_id"] == "clarity-act-2026"
    assert latest[0]["outcome"] == "YES"
    assert latest[0]["review_status"] == "Pending"
    os.remove(db_path)


def test_latest_finding_is_most_recent_run():
    db_path = make_temp_db()
    v1 = Verdict(outcome="UNCLEAR", confidence=0.2, evidence_snippet=None,
                 source_url=None, source_type=None)
    v2 = Verdict(outcome="YES", confidence=0.9, evidence_snippet="signed",
                 source_url="https://congress.gov/x", source_type="primary")
    save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", v1)
    save_finding(db_path, "clarity-act-2026", "2026-08-10T06:00:00", v2)

    latest = get_latest_findings(db_path)
    assert len(latest) == 1
    assert latest[0]["outcome"] == "YES"

    history = get_history(db_path, "clarity-act-2026")
    assert len(history) == 2
    os.remove(db_path)


def test_set_review_status():
    db_path = make_temp_db()
    verdict = Verdict(outcome="NO", confidence=0.7, evidence_snippet="not signed",
                       source_url="https://reuters.com/x", source_type="credible_backup")
    finding_id = save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    set_review_status(db_path, finding_id, "Confirmed")

    latest = get_latest_findings(db_path)
    assert latest[0]["review_status"] == "Confirmed"
    os.remove(db_path)


def test_set_and_get_setting():
    db_path = make_temp_db()
    set_setting(db_path, "currents_api_key", "abc123")
    assert get_setting(db_path, "currents_api_key") == "abc123"
    os.remove(db_path)


def test_get_setting_returns_none_when_unset():
    db_path = make_temp_db()
    assert get_setting(db_path, "currents_api_key") is None
    os.remove(db_path)


def test_set_setting_overwrites_existing_value():
    db_path = make_temp_db()
    set_setting(db_path, "currents_api_key", "first")
    set_setting(db_path, "currents_api_key", "second")
    assert get_setting(db_path, "currents_api_key") == "second"
    os.remove(db_path)


def test_get_latest_findings_returns_one_row_per_option():
    db_path = make_temp_db()
    v_winner = Verdict(outcome="YES", confidence=0.8, evidence_snippet="won",
                        source_url="https://reuters.com/x", source_type="credible_backup",
                        option="Pope Leo XIV")
    v_loser = Verdict(outcome="NO", confidence=0.8, evidence_snippet="lost",
                       source_url="https://reuters.com/x", source_type="credible_backup",
                       option="Donald Trump")
    save_finding(db_path, "nobel-peace-2026", "2026-08-17T00:00:00", v_winner)
    save_finding(db_path, "nobel-peace-2026", "2026-08-17T00:00:00", v_loser)

    latest = get_latest_findings(db_path)
    assert len(latest) == 2
    assert {row["option"]: row["outcome"] for row in latest} == {
        "Pope Leo XIV": "YES", "Donald Trump": "NO",
    }
    os.remove(db_path)


def test_save_finding_without_availability_leaves_new_columns_null():
    # Backward-compat: a caller that hasn't computed availability yet (or
    # a test exercising storage in isolation, like every test above this
    # one) must still work -- both new columns are simply NULL.
    db_path = make_temp_db()
    verdict = Verdict(outcome="YES", confidence=0.8, evidence_snippet="signed into law",
                       source_url="https://congress.gov/x", source_type="primary")
    save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)

    latest = get_latest_findings(db_path)
    assert latest[0]["resolution_available"] is None
    assert latest[0]["availability_reason"] is None
    os.remove(db_path)


def test_save_finding_persists_availability():
    db_path = make_temp_db()
    market = Market(id="clarity-act-2026", title="t",
                     description="If these conditions are not met by the deadline, "
                                  "the market resolves to \"No\".",
                     options=[], close_date=date(2020, 1, 1))
    availability = check_availability(market, today=date(2026, 1, 1))
    assert availability.available is True
    assert availability.reason == AVAILABLE_CLOSE_DATE_PASSED

    verdict = Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                       source_url=None, source_type=None)
    save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict, availability)

    latest = get_latest_findings(db_path)
    assert latest[0]["resolution_available"] is True
    assert latest[0]["availability_reason"] == AVAILABLE_CLOSE_DATE_PASSED
    os.remove(db_path)


def test_save_finding_persists_availability_false():
    db_path = make_temp_db()
    market = Market(id="clarity-act-2026", title="t", description="", options=[],
                     close_date=date(2030, 1, 1))
    availability = check_availability(market, today=date(2026, 1, 1))
    assert availability.available is False

    verdict = Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                       source_url=None, source_type=None)
    save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict, availability)

    latest = get_latest_findings(db_path)
    # False must round-trip as False, not be confused with the "never
    # computed" None case above -- both are falsy in Python but mean
    # different things to a reviewer ("checked, not available yet" vs.
    # "we don't know").
    assert latest[0]["resolution_available"] is False
    assert latest[0]["availability_reason"] is None
    os.remove(db_path)


def test_migrate_is_idempotent_and_preserves_existing_data():
    # Real scenario this guards against: a db file that already existed
    # on disk before the resolution_available/availability_reason columns
    # were added (data/resolution_finder.db, gitignored, from earlier
    # work this session) -- CREATE TABLE IF NOT EXISTS does nothing for
    # an existing table, so without a migration step it would silently
    # keep the old schema forever.
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    # Simulate the OLD schema, pre-migration, with one real row in it.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            option TEXT,
            run_timestamp TEXT NOT NULL,
            outcome TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_snippet TEXT,
            source_url TEXT,
            source_type TEXT,
            review_status TEXT NOT NULL DEFAULT 'Pending'
        );
    """)
    conn.execute(
        "INSERT INTO findings (market_id, run_timestamp, outcome, confidence, review_status) "
        "VALUES ('old-market', '2026-01-01T00:00:00', 'YES', 0.9, 'Pending')"
    )
    conn.commit()
    conn.close()

    # init_db() must migrate the existing table AND not touch its data.
    init_db(db_path)
    latest = get_latest_findings(db_path)
    assert len(latest) == 1
    assert latest[0]["market_id"] == "old-market"
    assert latest[0]["resolution_available"] is None

    # Running it again (e.g. every real init_db() call on every scan) must
    # not error on "duplicate column".
    init_db(db_path)
    os.remove(db_path)


def test_get_latest_findings_tracks_each_option_independently_across_runs():
    db_path = make_temp_db()
    v1 = Verdict(outcome="NO", confidence=0.6, evidence_snippet="eliminated",
                 source_url="https://dexerto.com/x", source_type="credible_backup",
                 option="Aurora Gaming")
    save_finding(db_path, "international-2026-champion", "2026-08-17T00:00:00", v1)

    v2 = Verdict(outcome="UNCLEAR", confidence=0.3, evidence_snippet="still undecided",
                 source_url=None, source_type=None)
    save_finding(db_path, "international-2026-champion", "2026-08-18T00:00:00", v2)

    latest = get_latest_findings(db_path)
    market_rows = [r for r in latest if r["market_id"] == "international-2026-champion"]
    assert len(market_rows) == 2
    assert {r["option"]: r["outcome"] for r in market_rows} == {
        "Aurora Gaming": "NO", None: "UNCLEAR",
    }
    os.remove(db_path)
