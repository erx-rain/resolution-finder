"""HTTP API for the Resolution Finder service (standalone, for a VM).

Design (approved 2026-10-05/06): the engine proposes, a human disposes.
This API exposes eligibility (cheap, no models), background scan jobs,
findings + run history, Confirm/Reject with reviewer identity, and the
UI-settable schedule tag. Nothing here settles a market.

Auth: every route (except /health) requires Authorization: Bearer
$RF_SERVICE_TOKEN when that env var is set. Rain-admin reaches this
service through its own server-side proxy route that holds the token —
the browser never sees it (rain-admin CLAUDE.md credential rule).
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import Flask, jsonify, request

from resolution_finder.models import Market
from resolution_finder.gist_market_provider import GistMarketProvider, GistProviderError, _parse_close_date, _parse_options
from resolution_finder.resolution_spec import check_availability
from resolution_finder.storage import get_history, get_latest_findings, init_db
from resolution_finder.service import service_storage
from resolution_finder.service.jobs import JobQueue
from resolution_finder.service.schedule import (
    Schedule,
    ScheduleParseError,
    is_due,
    next_run,
    parse_schedule_tag,
)

logger = logging.getLogger(__name__)


def _availability_payload(market: Market) -> dict:
    result = check_availability(market)
    return {
        "market_id": market.id,
        "available": result.available,
        "reason": result.reason,
        "trigger_date": result.trigger_date.isoformat() if result.trigger_date else None,
        "default_outcome_context": result.spec.default_outcome,
        "names_primary_source": result.spec.names_primary_source,
    }


def _market_from_payload(item: dict) -> Optional[Market]:
    title = item.get("title") or item.get("question") or ""
    if not isinstance(title, str) or not title.strip():
        return None
    description = item.get("description")
    if not isinstance(description, str):
        description = ""
    return Market(
        id=str(item.get("id", "")),
        title=title,
        description=description,
        options=_parse_options(item.get("options")),
        close_date=_parse_close_date(
            item.get("close_date") or item.get("closeDate") or item.get("endDate")
        ),
    )


def create_app(
    db_path: str,
    provider=None,
    job_queue: Optional[JobQueue] = None,
    scan_market_fn=None,
    start_scheduler: bool = False,
) -> Flask:
    """App factory. `provider`/`job_queue`/`scan_market_fn` are injectable
    for tests (no models, no network); run_service.py wires the real ones."""
    app = Flask(__name__)
    init_db(db_path)
    service_storage.init_service_schema(db_path)

    token = os.environ.get("RF_SERVICE_TOKEN", "")
    if not token:
        logger.warning("RF_SERVICE_TOKEN is not set — the API is UNAUTHENTICATED. "
                       "Fine on localhost, never on an exposed port.")

    def require_auth(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if token:
                supplied = request.headers.get("Authorization", "")
                if supplied != f"Bearer {token}":
                    return jsonify({"error": "unauthorized"}), 401
            return fn(*args, **kwargs)
        return wrapper

    def get_provider():
        nonlocal provider
        if provider is None:
            provider = GistMarketProvider()
        return provider

    # ── The real scan function (overridable in tests) ──────────────────
    def _real_scan(market_id: str, trigger: str) -> None:
        from resolution_finder.pipeline import _scan_market, _default_peer_checker
        from resolution_finder.verdict_engine import decide
        from resolution_finder.structured_resolvers import resolve_structured

        market = get_provider().get_market(market_id)
        if market is None:
            raise ValueError(f"Market {market_id!r} not found in the gist market list")
        run_id = service_storage.start_run(db_path, market_id, trigger)
        run_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            _scan_market(market, db_path, run_timestamp, decide, _default_peer_checker, resolve_structured)
            count = service_storage.count_findings_for_run(db_path, market_id, run_timestamp)
            service_storage.finish_run(db_path, run_id, "done", None, count)
        except Exception as exc:
            service_storage.finish_run(db_path, run_id, "failed", str(exc), None)
            raise

    scan_fn = scan_market_fn or _real_scan
    queue = job_queue or JobQueue(scan_fn)
    queue.start()
    app.extensions["rf_job_queue"] = queue

    # ── Routes ──────────────────────────────────────────────────────────
    @app.get("/health")
    def health():
        return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat()})

    @app.get("/markets")
    @require_auth
    def markets():
        try:
            market_list = get_provider().get_unresolved_markets()
        except GistProviderError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify({"markets": [
            {
                "id": m.id,
                "title": m.title,
                "description": m.description,
                "options": m.options,
                "close_date": m.close_date.isoformat() if m.close_date else None,
            }
            for m in market_list
        ]})

    @app.post("/eligibility")
    @require_auth
    def eligibility():
        """Cheap date-based availability. Body {"markets": [...]} checks the
        supplied markets; empty/absent body checks every gist market."""
        body = request.get_json(silent=True) or {}
        supplied = body.get("markets")
        if supplied:
            checked = []
            for item in supplied:
                market = _market_from_payload(item) if isinstance(item, dict) else None
                if market:
                    checked.append(_availability_payload(market))
            return jsonify({"results": checked})
        try:
            market_list = get_provider().get_unresolved_markets()
        except GistProviderError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify({"results": [_availability_payload(m) for m in market_list]})

    @app.post("/scans")
    @require_auth
    def start_scan():
        body = request.get_json(silent=True) or {}
        market_id = body.get("market_id")
        if not market_id:
            return jsonify({"error": "market_id is required"}), 400
        job = queue.enqueue(str(market_id), "manual")
        return jsonify({"job": job.to_dict()}), 202

    @app.get("/scans")
    @require_auth
    def list_scans():
        return jsonify({"jobs": [j.to_dict() for j in queue.list_jobs()]})

    @app.get("/scans/<job_id>")
    @require_auth
    def scan_status(job_id: str):
        job = queue.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        return jsonify({"job": job.to_dict()})

    @app.get("/findings")
    @require_auth
    def latest_findings():
        return jsonify({"findings": get_latest_findings(db_path)})

    @app.get("/markets/<market_id>/findings")
    @require_auth
    def market_findings(market_id: str):
        return jsonify({
            "findings": get_history(db_path, market_id),
            "runs": service_storage.get_runs(db_path, market_id),
        })

    @app.post("/findings/<int:finding_id>/review")
    @require_auth
    def review(finding_id: int):
        body = request.get_json(silent=True) or {}
        decision = body.get("decision")
        by = body.get("by")
        if decision not in ("confirm", "reject"):
            return jsonify({"error": 'decision must be "confirm" or "reject"'}), 400
        if not by or not isinstance(by, str):
            return jsonify({"error": "by (reviewer identity) is required"}), 400
        status = "Confirmed" if decision == "confirm" else "Rejected"
        if not service_storage.record_review(db_path, finding_id, status, by):
            return jsonify({"error": "finding not found"}), 404
        return jsonify({"ok": True, "finding_id": finding_id, "review_status": status})

    @app.get("/schedule")
    @require_auth
    def get_schedule():
        from resolution_finder.storage import get_setting
        tag = get_setting(db_path, service_storage.SCHEDULE_TAG_KEY) or ""
        last_raw = get_setting(db_path, service_storage.SCHEDULE_LAST_RUN_KEY)
        schedule = parse_schedule_tag(tag)
        last_run = datetime.fromisoformat(last_raw) if last_raw else None
        upcoming = next_run(schedule, datetime.now(), last_run)
        return jsonify({
            "tag": tag,
            "parsed": schedule.describe(),
            "last_run": last_raw,
            "next_run": upcoming.isoformat() if upcoming else None,
        })

    @app.put("/schedule")
    @require_auth
    def put_schedule():
        from resolution_finder.storage import set_setting
        body = request.get_json(silent=True) or {}
        tag = body.get("tag", "")
        try:
            schedule = parse_schedule_tag(tag)
        except ScheduleParseError as exc:
            return jsonify({"error": str(exc)}), 400
        set_setting(db_path, service_storage.SCHEDULE_TAG_KEY, tag)
        upcoming = next_run(schedule, datetime.now(), None)
        return jsonify({
            "ok": True,
            "tag": tag,
            "parsed": schedule.describe(),
            "next_run": upcoming.isoformat() if upcoming else None,
        })

    # ── Scheduler loop (started by run_service.py, not under tests) ────
    def _scheduler_loop():
        from resolution_finder.storage import get_setting, set_setting
        while True:
            try:
                tag = get_setting(db_path, service_storage.SCHEDULE_TAG_KEY) or ""
                schedule = parse_schedule_tag(tag) if tag else Schedule(kind="off", raw="")
                last_raw = get_setting(db_path, service_storage.SCHEDULE_LAST_RUN_KEY)
                last = datetime.fromisoformat(last_raw) if last_raw else None
                now = datetime.now()
                if is_due(schedule, now, last):
                    set_setting(db_path, service_storage.SCHEDULE_LAST_RUN_KEY, now.isoformat())
                    logger.info("Schedule %r due — enqueueing scans for available markets", tag)
                    try:
                        for market in get_provider().get_unresolved_markets():
                            # Scheduled runs scan only markets whose
                            # resolution is AVAILABLE (date signals) —
                            # scanning a market months before its close
                            # date wastes the news budget. A manual POST
                            # /scans has no such gate: a human asking is
                            # the override.
                            if check_availability(market).available:
                                queue.enqueue(market.id, "schedule")
                    except GistProviderError as exc:
                        logger.error("Scheduled run skipped: %s", exc)
            except ScheduleParseError as exc:
                logger.error("Stored schedule tag unparseable: %s", exc)
            except Exception:  # noqa: BLE001 — scheduler must survive anything
                logger.exception("Scheduler loop error")
            time.sleep(30)

    if start_scheduler:
        threading.Thread(target=_scheduler_loop, name="rf-scheduler", daemon=True).start()

    return app
