"""Background scan jobs — one worker, FIFO, deduped per market.

A scan takes seconds to ~5 minutes per market (two local models, live
retrieval), so the API never runs one inline: POST /scans returns 202 and
a job id immediately; GET /scans/<id> reports status. One worker thread
on purpose — the engine is CPU-bound and the news sources are
rate-limited (REQUEST_DELAY_SECONDS); parallel scans would fight both.
"""
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    market_id: str
    trigger: str  # "manual" | "schedule"
    status: str = "queued"  # queued | running | done | failed
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "market_id": self.market_id,
            "trigger": self.trigger,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobQueue:
    """scan_fn(market_id, trigger) does the actual work (see app.py);
    injected so tests never load models or touch the network."""

    def __init__(self, scan_fn: Callable[[str, str], None]):
        self._scan_fn = scan_fn
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._active_by_market: dict[str, str] = {}
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="rf-scan-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def enqueue(self, market_id: str, trigger: str) -> Job:
        with self._lock:
            # Dedupe: a market already queued/running returns the existing
            # job instead of piling up duplicates.
            existing_id = self._active_by_market.get(market_id)
            if existing_id:
                existing = self._jobs.get(existing_id)
                if existing and existing.status in ("queued", "running"):
                    return existing
            job = Job(id=uuid.uuid4().hex[:12], market_id=market_id, trigger=trigger)
            self._jobs[job.id] = job
            self._active_by_market[market_id] = job.id
            self._queue.put(job.id)
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    continue
                job.status = "running"
                job.started_at = datetime.now(timezone.utc).isoformat()
            try:
                self._scan_fn(job.market_id, job.trigger)
                with self._lock:
                    job.status = "done"
                    job.finished_at = datetime.now(timezone.utc).isoformat()
            except Exception as exc:  # noqa: BLE001 — one bad market must not kill the worker
                logger.exception("Scan job %s (market %s) failed", job.id, job.market_id)
                with self._lock:
                    job.status = "failed"
                    job.error = str(exc)
                    job.finished_at = datetime.now(timezone.utc).isoformat()
            finally:
                with self._lock:
                    if self._active_by_market.get(job.market_id) == job.id:
                        del self._active_by_market[job.market_id]
