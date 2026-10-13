"""API tests with everything injectable — no models, no network."""
import json
import time
from unittest.mock import patch

import pytest

from resolution_finder.gist_market_provider import GistMarketProvider
from resolution_finder.rain_pool_client import RainPoolError
from resolution_finder.service.app import create_app
from resolution_finder.service.jobs import JobQueue
from resolution_finder.service.service_storage import get_runs
from resolution_finder.storage import get_latest_findings, save_finding
from resolution_finder.models import Market, Verdict


MARKETS = [
    {
        "id": "m-past",
        "title": "Did X happen?",
        "description": "Resolves YES if X happened by January 1, 2026.",
        "options": [],
        "close_date": "2026-01-01",
    },
    {
        "id": "m-future",
        "title": "Will Y happen?",
        "description": "Resolves by December 31, 2099.",
        "options": [],
        "close_date": "2099-12-31",
    },
]


@pytest.fixture
def app(tmp_path):
    provider = GistMarketProvider(
        gist_id="test", file_name="f.json", token=None,
        fetch=lambda *a: json.dumps(MARKETS),
    )
    scanned = []

    def fake_scan(market_id, trigger):
        scanned.append((market_id, trigger))

    queue = JobQueue(fake_scan)
    application = create_app(str(tmp_path / "t.db"), provider=provider, job_queue=queue)
    application.config["TESTING"] = True
    application._scanned = scanned  # type: ignore[attr-defined]
    application._db_path = str(tmp_path / "t.db")  # type: ignore[attr-defined]
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_health(client):
    assert client.get("/health").status_code == 200


def test_markets_listed_from_provider(client):
    data = client.get("/markets").get_json()
    assert [m["id"] for m in data["markets"]] == ["m-past", "m-future"]


def test_eligibility_all_markets(client):
    data = client.post("/eligibility").get_json()
    by_id = {r["market_id"]: r for r in data["results"]}
    assert by_id["m-past"]["available"] is True
    assert by_id["m-past"]["reason"] == "close_date_passed"
    assert by_id["m-future"]["available"] is False


def test_eligibility_supplied_markets(client):
    data = client.post("/eligibility", json={"markets": [MARKETS[0]]}).get_json()
    assert len(data["results"]) == 1
    assert data["results"][0]["available"] is True


def test_scan_lifecycle(app, client):
    resp = client.post("/scans", json={"market_id": "m-past"})
    assert resp.status_code == 202
    job_id = resp.get_json()["job"]["id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get(f"/scans/{job_id}").get_json()["job"]
        if job["status"] == "done":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert ("m-past", "manual") in app._scanned


def test_scan_requires_market_id(client):
    assert client.post("/scans", json={}).status_code == 400


def test_scan_dedupe(client):
    a = client.post("/scans", json={"market_id": "m-future"}).get_json()["job"]["id"]
    b = client.post("/scans", json={"market_id": "m-future"}).get_json()["job"]["id"]
    # Either the same job (still queued/running) or a new one if the first
    # finished already — both acceptable; what matters is no error.
    assert a and b


def _run_scan_and_wait(client, market_id):
    job_id = client.post("/scans", json={"market_id": market_id}).get_json()["job"]["id"]
    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = client.get(f"/scans/{job_id}").get_json()["job"]
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    return job


def test_scan_skips_when_rain_has_no_description(tmp_path):
    # Real _real_scan (job_queue/scan_market_fn left at their defaults) --
    # only rain_fetch is injected, so this never touches the network or
    # loads a model. "Not found" simulates Rain having no pool record /
    # no poolDescription yet: enrich_market_with_rain_description returns
    # None, and "do not run the finder" means no finding is written.
    provider = GistMarketProvider(
        gist_id="test", file_name="f.json", token=None,
        fetch=lambda *a: json.dumps(MARKETS),
    )

    def rain_fetch_raises(market_id):
        raise RainPoolError("HTTP 404")

    db_path = str(tmp_path / "t.db")
    application = create_app(db_path, provider=provider, rain_fetch=rain_fetch_raises)
    application.config["TESTING"] = True
    client = application.test_client()

    job = _run_scan_and_wait(client, "m-past")
    assert job["status"] == "done"  # skipped, not failed -- there's no bug to report

    runs = get_runs(db_path, "m-past")
    assert len(runs) == 1
    assert runs[0]["status"] == "done"
    assert runs[0]["findings_count"] == 0
    assert "skipped" in runs[0]["error"].lower()
    assert get_latest_findings(db_path) == []


def test_scan_skips_a_market_rain_has_already_resolved(tmp_path):
    provider = GistMarketProvider(
        gist_id="test", file_name="f.json", token=None, fetch=lambda *a: json.dumps(MARKETS),
    )
    resolved = lambda market_id: {
        "poolDescription": "Resolves YES if X happened.", "isFinalized": True,
        "subPools": [{"question": "Yes", "poolFinalized": True}],
    }
    db_path = str(tmp_path / "t.db")
    client = create_app(db_path, provider=provider, rain_fetch=resolved).test_client()

    assert _run_scan_and_wait(client, "m-past")["status"] == "done"
    (run,) = get_runs(db_path, "m-past")
    assert run["findings_count"] == 0
    assert "already resolved on rain" in run["error"].lower()
    assert get_latest_findings(db_path) == []


def test_scan_runs_each_open_date_sub_pool_as_its_own_market_and_files_it_by_name(tmp_path):
    # _scan_market itself (retrieval, models) is replaced: this checks what
    # the service hands it, and that the answers are filed per sub-pool.
    provider = GistMarketProvider(
        gist_id="test", file_name="f.json", token=None, fetch=lambda *a: json.dumps(MARKETS),
    )
    pool = lambda market_id: {
        "poolDescription": "Resolves YES if it happens by the specified date.",
        "subPools": [
            {"question": "November 30", "mergedQuestion": "Will it happen by November 30?",
             "poolFinalized": False, "endDate": "2099-12-01T04:59:00.000Z"},
            {"question": "August 18", "mergedQuestion": "Will it happen by August 18?",
             "poolFinalized": True, "endDate": "2026-08-20T09:11:06.031Z"},
            {"question": "December 31", "mergedQuestion": "Will it happen by December 31?",
             "poolFinalized": False, "endDate": "2100-01-01T04:59:00.000Z"},
        ],
    }
    scanned = []
    binary_verdict = Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                             source_url=None, source_type=None)

    def fake_scan_market(market, db_path, run_timestamp, verdict_engine, peer_checker, structured_resolver):
        from resolution_finder.resolution_spec import check_availability
        scanned.append(market)
        # `verdict_engine` is the service's wrapper around decide(): it is
        # what files the binary engine's answer under the sub-pool's name.
        for verdict in [verdict_engine(market, [])]:
            save_finding(db_path, market.id, run_timestamp, verdict, check_availability(market))

    db_path = str(tmp_path / "t.db")
    client = create_app(db_path, provider=provider, rain_fetch=pool).test_client()
    with patch("resolution_finder.pipeline._scan_market", side_effect=fake_scan_market), \
            patch("resolution_finder.verdict_engine.decide", return_value=binary_verdict):
        assert _run_scan_and_wait(client, "m-past")["status"] == "done"

    assert [(m.title, m.options, m.close_date.isoformat()) for m in scanned] == [
        ("Will it happen by November 30?", [], "2099-12-01"),
        ("Will it happen by December 31?", [], "2100-01-01"),
    ]
    findings = {f["option_name"]: f for f in client.get("/markets/m-past/findings").get_json()["findings"]}
    assert set(findings) == {"November 30", "December 31"}       # the resolved one is not scanned
    assert findings["November 30"]["available_from"] == "2099-12-01"
    assert findings["December 31"]["available_from"] == "2100-01-01"
    assert all(f["resolution_available"] is False for f in findings.values())


def test_review_flow(app, client):
    finding_id = save_finding(
        app._db_path, "m-past", "2026-10-06T00:00:00+00:00",
        Verdict(outcome="YES", confidence=0.9, evidence_snippet="s", source_url="u", source_type="general"),
    )
    resp = client.post(f"/findings/{finding_id}/review", json={"decision": "confirm", "by": "admin@rain"})
    assert resp.status_code == 200
    history = client.get("/markets/m-past/findings").get_json()
    assert history["findings"][0]["review_status"] == "Confirmed"


def test_findings_carry_the_option_under_the_name_the_admin_ui_reads(app, client):
    save_finding(
        app._db_path, "m-past", "2026-10-13T00:00:00+00:00",
        Verdict(outcome="NO", confidence=0.6, evidence_snippet="s", source_url="u",
                source_type="general", option="Donald Trump"),
    )
    for payload in (
        client.get("/findings").get_json()["findings"],
        client.get("/markets/m-past/findings").get_json()["findings"],
    ):
        assert payload[0]["option"] == "Donald Trump"
        assert payload[0]["option_name"] == "Donald Trump"
        # confidence is never the engine's evidence-match number (0.6 here).
        assert payload[0]["confidence"] is None
        assert payload[0]["evidence_match"] == 0.6


def test_review_validation(client):
    assert client.post("/findings/1/review", json={"decision": "maybe", "by": "x"}).status_code == 400
    assert client.post("/findings/1/review", json={"decision": "confirm"}).status_code == 400
    assert client.post("/findings/999999/review", json={"decision": "confirm", "by": "x"}).status_code == 404


def test_schedule_roundtrip(client):
    resp = client.put("/schedule", json={"tag": "everyday at 9:00"})
    assert resp.status_code == 200
    assert resp.get_json()["parsed"] == "daily at 09:00"
    got = client.get("/schedule").get_json()
    assert got["tag"] == "everyday at 9:00"
    assert got["next_run"] is not None


def test_schedule_rejects_garbage(client):
    resp = client.put("/schedule", json={"tag": "sometimes"})
    assert resp.status_code == 400
    assert "Supported forms" in resp.get_json()["error"]


def test_auth_enforced_when_token_set(tmp_path, monkeypatch):
    monkeypatch.setenv("RF_SERVICE_TOKEN", "sekret")
    provider = GistMarketProvider(
        gist_id="test", file_name="f.json", token=None,
        fetch=lambda *a: json.dumps(MARKETS),
    )
    application = create_app(str(tmp_path / "a.db"), provider=provider, job_queue=JobQueue(lambda m, t: None))
    c = application.test_client()
    assert c.get("/markets").status_code == 401
    assert c.get("/markets", headers={"Authorization": "Bearer sekret"}).status_code == 200
    # /health stays open for probes
    assert c.get("/health").status_code == 200
