# tests/test_dashboard.py
import os
import tempfile
from resolution_finder.storage import init_db, save_finding, get_setting, set_setting
from resolution_finder.models import Verdict
from resolution_finder.dashboard import create_app


def make_temp_db_with_finding():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    init_db(path)
    verdict = Verdict(outcome="YES", confidence=0.8, evidence_snippet="signed into law",
                       source_url="https://congress.gov/x", source_type="primary")
    finding_id = save_finding(path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    return path, finding_id


def test_index_lists_findings():
    db_path, _ = make_temp_db_with_finding()
    app = create_app(db_path)
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert b"clarity-act-2026" in response.data
    assert b"YES" in response.data
    os.remove(db_path)


def test_review_updates_status_and_redirects():
    db_path, finding_id = make_temp_db_with_finding()
    app = create_app(db_path)
    client = app.test_client()

    response = client.post(f"/review/{finding_id}", data={"status": "Confirmed"})
    assert response.status_code == 302

    response = client.get("/")
    assert b"Confirmed" in response.data
    os.remove(db_path)


def test_index_flags_official_social_source_for_manual_verification():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="Pope Leo XIV", confidence=0.5,
                       evidence_snippet="NobelPrize: The 2026 laureate is Pope Leo XIV.",
                       source_url="https://x.com/NobelPrize/status/123",
                       source_type="official_social")
    save_finding(db_path, "nobel-peace-2026", "2026-08-10T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"verify this is the real official account" in response.data
    assert b"https://x.com/NobelPrize/status/123" in response.data
    os.remove(db_path)


def test_index_flags_secondary_tier_source_as_lower_reliability():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="Arsenal", confidence=0.6,
                       evidence_snippet="Goal.com reports Vinicius to Arsenal",
                       source_url="https://www.goal.com/en/news/x",
                       source_type="credible_backup_secondary")
    save_finding(db_path, "vinicius-transfer-2026", "2026-08-10T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"lower-reliability" in response.data
    assert b'href="https://www.goal.com/en/news/x"' in response.data
    os.remove(db_path)


def test_index_flags_peer_market_source_for_manual_verification():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="YES", confidence=0.85,
                       evidence_snippet="Resolved \"Yes\" on Polymarket for a similar question",
                       source_url="https://polymarket.com/event/clarity-act",
                       source_type="peer_market")
    save_finding(db_path, "clarity-act-2026", "2026-08-16T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"verify this is genuinely the same event" in response.data
    os.remove(db_path)


def test_index_does_not_render_non_http_source_url_as_link():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="YES", confidence=0.6, evidence_snippet="test",
                       source_url="javascript:alert(1)", source_type="primary")
    save_finding(db_path, "malicious-market", "2026-08-10T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b'href="javascript:alert(1)"' not in response.data
    assert b"javascript:alert(1)" not in response.data
    assert b"primary" in response.data
    os.remove(db_path)


def test_settings_page_shows_masked_key_when_set():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"abcd1234567890" not in response.data  # never show the full key
    assert b"7890" in response.data  # last 4 chars, so the user can tell which key is saved
    os.remove(db_path)


def test_settings_page_shows_no_key_message_when_unset():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"No API key saved" in response.data
    os.remove(db_path)


def test_settings_page_saves_new_key():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)

    app = create_app(db_path)
    client = app.test_client()
    response = client.post("/settings", data={"currents_api_key": "newkey1234567890"})

    assert response.status_code == 302
    assert get_setting(db_path, "currents_api_key") == "newkey1234567890"
    os.remove(db_path)


def test_settings_page_shows_usage_when_available():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")
    set_setting(db_path, "currents_rate_limit_remaining", "543")
    set_setting(db_path, "currents_rate_limit_limit", "600")
    set_setting(db_path, "currents_rate_limit_updated_at", "2026-08-16T12:00:00+00:00")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"543" in response.data
    assert b"600" in response.data


def test_settings_page_shows_no_usage_data_message_when_unavailable():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"No usage data yet" in response.data
