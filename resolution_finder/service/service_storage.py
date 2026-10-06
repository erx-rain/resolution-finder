"""Service-side storage additions on top of storage.py's SQLite file.

Adds (same PRAGMA-guarded migration style as storage._migrate, same db
file — one system of record):
- findings.reviewed_by / findings.reviewed_at — WHO confirmed/rejected
  and WHEN (the review API requires both; the engine's own
  set_review_status predates reviewer identity).
- runs table — append-only log of every scan run (market, trigger,
  started/finished, outcome count) so the UI can show per-market history
  of runs, not just findings.
Schedule state lives in the existing settings table (keys below).
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

SCHEDULE_TAG_KEY = "service_schedule_tag"
SCHEDULE_LAST_RUN_KEY = "service_schedule_last_run"

_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    findings_count INTEGER
);
"""

_FINDINGS_MIGRATIONS = [
    ("reviewed_by", "ALTER TABLE findings ADD COLUMN reviewed_by TEXT"),
    ("reviewed_at", "ALTER TABLE findings ADD COLUMN reviewed_at TEXT"),
]


def init_service_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_RUNS_SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(findings)")}
        for column, ddl in _FINDINGS_MIGRATIONS:
            if column not in existing:
                conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def start_run(db_path: str, market_id: str, trigger: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO runs (market_id, trigger, started_at) VALUES (?, ?, ?)",
            (market_id, trigger, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_run(db_path: str, run_id: int, status: str, error: Optional[str], findings_count: Optional[int]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, error = ?, findings_count = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, error, findings_count, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_runs(db_path: str, market_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if market_id:
            rows = conn.execute(
                "SELECT * FROM runs WHERE market_id = ? ORDER BY id DESC LIMIT ?",
                (market_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_review(db_path: str, finding_id: int, status: str, by: str) -> bool:
    """Confirm/Reject with reviewer identity + timestamp. Returns False when
    the finding id doesn't exist."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE findings SET review_status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (status, by, datetime.now(timezone.utc).isoformat(), finding_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_findings_for_run(db_path: str, market_id: str, run_timestamp: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE market_id = ? AND run_timestamp = ?",
            (market_id, run_timestamp),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()
