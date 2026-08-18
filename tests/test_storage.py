import os
import tempfile
from resolution_finder.models import Verdict
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
