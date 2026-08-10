import os
import tempfile
from resolution_finder.models import Verdict
from resolution_finder.storage import (
    init_db, save_finding, get_latest_findings, get_history, set_review_status,
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
