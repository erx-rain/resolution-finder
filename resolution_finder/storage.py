import sqlite3
from typing import Optional
from resolution_finder.models import Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_snippet TEXT,
    source_url TEXT,
    source_type TEXT,
    review_status TEXT NOT NULL DEFAULT 'Pending'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def save_finding(db_path: str, market_id: str, run_timestamp: str, verdict: Verdict) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO findings
                (market_id, run_timestamp, outcome, confidence, evidence_snippet,
                 source_url, source_type, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending')
            """,
            (market_id, run_timestamp, verdict.outcome, verdict.confidence,
             verdict.evidence_snippet, verdict.source_url, verdict.source_type),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "market_id": row["market_id"],
        "run_timestamp": row["run_timestamp"],
        "outcome": row["outcome"],
        "confidence": row["confidence"],
        "evidence_snippet": row["evidence_snippet"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "review_status": row["review_status"],
    }


def get_latest_findings(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM findings f
            INNER JOIN (
                SELECT market_id, MAX(run_timestamp) AS max_ts
                FROM findings GROUP BY market_id
            ) latest
            ON f.market_id = latest.market_id AND f.run_timestamp = latest.max_ts
            ORDER BY f.confidence DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_history(db_path: str, market_id: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM findings WHERE market_id = ? ORDER BY run_timestamp DESC",
            (market_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_review_status(db_path: str, finding_id: int, status: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE findings SET review_status = ? WHERE id = ?", (status, finding_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_setting(db_path: str, key: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_setting(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
