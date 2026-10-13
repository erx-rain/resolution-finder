import sqlite3
from typing import Optional
from resolution_finder.models import Verdict
from resolution_finder.resolution_spec import AvailabilityResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    option TEXT,
    run_timestamp TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_snippet TEXT,
    source_url TEXT,
    source_type TEXT,
    review_status TEXT NOT NULL DEFAULT 'Pending',
    resolution_available INTEGER,
    availability_reason TEXT,
    available_from TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the original CREATE TABLE shipped (resolution_available,
# availability_reason -- 2026-09-08, the availability/outcome split). A real
# db file (data/resolution_finder.db, gitignored) can already exist on disk
# from before these columns existed -- CREATE TABLE IF NOT EXISTS does
# nothing for an existing table, so a migration step is required or every
# already-deployed db silently keeps the old schema forever. Guarded via
# PRAGMA table_info so this is safe to run on every init_db() call,
# including a fresh db that already has the columns from SCHEMA above.
_MIGRATIONS = [
    ("resolution_available", "ALTER TABLE findings ADD COLUMN resolution_available INTEGER"),
    ("availability_reason", "ALTER TABLE findings ADD COLUMN availability_reason TEXT"),
    # 2026-10-13: the date a not-yet-available finding becomes determinable.
    # Needed per FINDING, not per market: sub-pools named by dates each
    # have their own deadline (see rain_pool_client.plan_scan).
    ("available_from", "ALTER TABLE findings ADD COLUMN available_from TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(findings)")}
    for column_name, ddl in _MIGRATIONS:
        if column_name not in existing_columns:
            conn.execute(ddl)


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def save_finding(
    db_path: str, market_id: str, run_timestamp: str, verdict: Verdict,
    availability: Optional[AvailabilityResult] = None,
) -> int:
    """Persist one Verdict, plus this market's independently-computed
    availability signal (see resolution_spec.py's own module docstring for
    why these are two separate concepts, not one field on Verdict).
    `availability` is Optional and defaults to None -- a caller that hasn't
    computed one yet (or a test exercising storage in isolation) still
    works, and both new columns are simply NULL for that row."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO findings
                (market_id, option, run_timestamp, outcome, confidence, evidence_snippet,
                 source_url, source_type, review_status, resolution_available, availability_reason,
                 available_from)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)
            """,
            (market_id, verdict.option, run_timestamp, verdict.outcome, verdict.confidence,
             verdict.evidence_snippet, verdict.source_url, verdict.source_type,
             None if availability is None else int(availability.available),
             None if availability is None else availability.reason,
             None if availability is None or availability.available_from is None
             else availability.available_from.isoformat()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "market_id": row["market_id"],
        "option": row["option"],
        "run_timestamp": row["run_timestamp"],
        "outcome": row["outcome"],
        "confidence": row["confidence"],
        "evidence_snippet": row["evidence_snippet"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "review_status": row["review_status"],
        "resolution_available": (
            None if row["resolution_available"] is None else bool(row["resolution_available"])
        ),
        "availability_reason": row["availability_reason"],
        "available_from": row["available_from"],
    }


def get_latest_findings(db_path: str) -> list[dict]:
    """Every finding of each market's most recent run that produced any.

    Was "latest row per (market, option)" until 2026-10-13, from when a
    run could report only SOME options and the rest had to be carried
    over from earlier runs. Every run now reports every option it scans
    (verdict_engine._fill_missing_options), so carrying rows over can
    only show stale ones: the single whole-market row an unsettled market
    used to get, or a sub-pool Rain has since resolved and the finder no
    longer scans (rain_pool_client.plan_scan) -- found live on the
    U.S.-Iran "by <date>" market, whose three settled sub-pools kept
    their old "no answer" rows next to the one still open."""
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
            ORDER BY f.market_id, f.option, f.confidence DESC
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
