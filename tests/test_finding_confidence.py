"""A finding's `confidence` means one thing -- how sure the service is that
the proposed outcome is correct -- and is null whenever no such figure was
computed. The engine's own number (how closely the quoted article matches
the market) is served as `evidence_match`. See storage.FINDING_SCORE_VERSION.
"""
import os
import sqlite3
import tempfile
from datetime import date

from resolution_finder.models import Verdict
from resolution_finder.storage import get_history, get_latest_findings, init_db, save_finding
from resolution_finder.verdict_engine import decide
from tests.test_verdict_engine import (
    IRAN_EVIDENCE, NOBEL_MARKET, PILLAY_X_AWARD, PILLAY_YAHOO,
    _decide_dated_on, _from_domain, _iran_sub_pool, make_ranked,
)

RUN = "2026-10-13T12:00:00+00:00"


def _db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    init_db(path)
    return path


def _stored(verdicts, market_id="nobel-peace-2026"):
    db_path = _db()
    for verdict in verdicts if isinstance(verdicts, list) else [verdicts]:
        save_finding(db_path, market_id, RUN, verdict)
    findings = {f["option"]: f for f in get_latest_findings(db_path)}
    os.remove(db_path)
    return findings


def test_confirmed_listed_winner_carries_no_made_up_confidence():
    verdicts = decide(NOBEL_MARKET, [
        make_ranked("The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
                    url="https://nobelprize.org/announcement", similarity=0.71),
        make_ranked("Pope Leo XIV has been named winner of this year's Nobel Peace Prize, the committee said Friday.",
                    url="https://apnews.com/x", source_type="credible_backup", similarity=0.64),
    ])
    findings = _stored(verdicts)
    assert {o: f["outcome"] for o, f in findings.items()} == {
        "Pope Leo XIV": "YES", "Yulia Navalnaya": "NO", "Volodymyr Zelenskyy": "NO",
        "UNRWA": "NO", "Donald Trump": "NO",
    }
    # No certainty figure exists for these outcomes, so none is sent...
    assert all(f["confidence"] is None for f in findings.values())
    # ...and the similarity of the quoted article is there under its own name.
    assert all(f["evidence_match"] == 0.71 for f in findings.values())


def test_confirmed_unlisted_winner_carries_no_made_up_confidence():
    verdicts = decide(NOBEL_MARKET, [
        _from_domain(PILLAY_X_AWARD, "x.com", "official_social", similarity=0.5997815132141113),
        _from_domain(PILLAY_YAHOO, "www.yahoo.com", "credible_backup_secondary", similarity=0.517),
    ])
    findings = _stored(verdicts)
    assert {f["outcome"] for f in findings.values()} == {"NO"} and len(findings) == 5
    # The live finding this was written for showed 0.5997815132141113 as
    # "60% confidence" on all five options.
    assert all(f["confidence"] is None for f in findings.values())
    assert all(f["evidence_match"] == 0.5997815132141113 for f in findings.values())


def test_no_figure_is_null_never_a_default():
    # Nothing found: no outcome, no matched article -- two nulls, not 0.0.
    nothing = _stored(decide(NOBEL_MARKET, []))
    assert {f["outcome"] for f in nothing.values()} == {"NO_EVIDENCE"}
    assert all(f["confidence"] is None and f["evidence_match"] is None for f in nothing.values())

    # A "No" asserted from absence quotes no matched article either.
    absent = _decide_dated_on(date(2026, 10, 13), _iran_sub_pool(), IRAN_EVIDENCE)
    assert absent.outcome == "NO"
    (finding,) = _stored(absent, "iran").values()
    assert finding["confidence"] is None and finding["evidence_match"] is None

    # The official-page resolver's hard-coded 1.0 is not a similarity.
    (official,) = _stored(Verdict(outcome="YES", confidence=1.0, evidence_snippet="s",
                                   source_url="https://www.federalreserve.gov/x", source_type="primary"),
                          "fed").values()
    assert official["confidence"] is None and official["evidence_match"] is None


def test_unclear_finding_proposes_nothing_so_has_no_confidence():
    verdicts = decide(NOBEL_MARKET, [_from_domain(PILLAY_X_AWARD, "x.com", "official_social")])
    findings = _stored(verdicts)
    assert not any(f["outcome"] in ("YES", "NO") for f in findings.values())
    assert all(f["confidence"] is None for f in findings.values())


def test_findings_stored_before_the_change_are_served_as_they_were():
    db_path = _db()
    save_finding(db_path, "old-market", "2026-10-13T09:58:18", Verdict(
        outcome="NO", confidence=0.5997815132141113, evidence_snippet="s",
        source_url="https://x.com/a", source_type="official_social", option="Donald Trump"))
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE findings SET score_version = NULL")   # as written by the old code
    conn.commit()
    conn.close()
    (old,) = get_history(db_path, "old-market")
    assert old["confidence"] == 0.5997815132141113
    assert old["evidence_match"] is None
    os.remove(db_path)
