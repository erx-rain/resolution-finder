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
    available_from TEXT,
    score_version INTEGER,
    can_resolve_early INTEGER
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
    # 2026-10-13: marks a row written since `confidence` stopped being
    # served from the evidence-match number -- see FINDING_SCORE_VERSION.
    ("score_version", "ALTER TABLE findings ADD COLUMN score_version INTEGER"),
    # 2026-10-14: the market's rules let it resolve before its close date
    # (resolution_spec.states_early_resolution). Per finding, like
    # available_from: it changes what "not yet available" means to a reader.
    ("can_resolve_early", "ALTER TABLE findings ADD COLUMN can_resolve_early INTEGER"),
]

# What the `confidence` COLUMN holds has never changed: the engine's
# evidence-match number (models.Verdict.confidence -- embedding similarity
# of the quoted article to the market text). What is SERVED did, on
# 2026-10-13, because that number was being read as "how sure the outcome
# is" (found live: five Nobel options each "60% confidence" on a result
# confirmed by the committee itself).
#
# A finding now has two separate things (see _row_to_dict):
#   confidence      how sure the service is that the proposed outcome is
#                   correct, 0..1 -- or null. It is null on every new
#                   finding today: nothing computes such a figure, and no
#                   default, similarity or constant is ever sent in its
#                   place.
#   evidence_match  the number above, under its own name -- null when the
#                   engine quoted no matched article (see _evidence_match).
#
# Rows written before this (score_version NULL) are served exactly as
# they always were, the old number still under `confidence`: stored
# findings are history and are not reinterpreted.
FINDING_SCORE_VERSION = 2


def _evidence_match(raw: float) -> Optional[float]:
    """The engine's number when it is a real similarity. Exactly 0.0 and
    exactly 1.0 are never similarities -- they are the engine's own
    hard-coded stand-ins for "nothing quoted" and "read from an official
    page" -- and are not passed on as if they were measurements."""
    return raw if 0.0 < raw < 1.0 else None


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
                 available_from, score_version, can_resolve_early)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?)
            """,
            (market_id, verdict.option, run_timestamp, verdict.outcome, verdict.confidence,
             verdict.evidence_snippet, verdict.source_url, verdict.source_type,
             None if availability is None else int(availability.available),
             None if availability is None else availability.reason,
             None if availability is None or availability.available_from is None
             else availability.available_from.isoformat(),
             FINDING_SCORE_VERSION,
             None if availability is None else int(availability.spec.can_resolve_early)),
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
        # See FINDING_SCORE_VERSION: a legacy row is served as it always
        # was; a current one never passes the evidence match off as
        # confidence.
        "confidence": row["confidence"] if row["score_version"] is None else None,
        "evidence_match": None if row["score_version"] is None else _evidence_match(row["confidence"]),
        "evidence_snippet": row["evidence_snippet"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "review_status": row["review_status"],
        "resolution_available": (
            None if row["resolution_available"] is None else bool(row["resolution_available"])
        ),
        "availability_reason": row["availability_reason"],
        "available_from": row["available_from"],
        "can_resolve_early": None if row["can_resolve_early"] is None else bool(row["can_resolve_early"]),
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
