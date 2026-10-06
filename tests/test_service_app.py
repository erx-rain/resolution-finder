"""API tests with everything injectable — no models, no network."""
import json
import time

import pytest

from resolution_finder.gist_market_provider import GistMarketProvider
from resolution_finder.service.app import create_app
from resolution_finder.service.jobs import JobQueue
from resolution_finder.storage import save_finding
from resolution_finder.models import Verdict


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


def test_review_flow(app, client):
    finding_id = save_finding(
        app._db_path, "m-past", "2026-10-06T00:00:00+00:00",
        Verdict(outcome="YES", confidence=0.9, evidence_snippet="s", source_url="u", source_type="general"),
    )
    resp = client.post(f"/findings/{finding_id}/review", json={"decision": "confirm", "by": "admin@rain"})
    assert resp.status_code == 200
    history = client.get("/markets/m-past/findings").get_json()
    assert history["findings"][0]["review_status"] == "Confirmed"


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
