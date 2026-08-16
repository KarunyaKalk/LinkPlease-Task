import os
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    dm_message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dm_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    dm_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, rule_id)
);

CREATE TABLE IF NOT EXISTS dedup_blocked_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    blocked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_status ON dm_attempts(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_attempts_comment ON dm_attempts(comment_id);
"""

# sqlite3 is blocking, but at this scale (500 events/10s, sub-ms per query) a single
# connection guarded by one lock is simpler and fast enough — no real reason to reach
# for aiosqlite or a connection pool for a service this size.
_conn: Optional[sqlite3.Connection] = None
_lock = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    global _conn
    db_path = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "linkplease.db"))
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _conn.commit()


def _get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("init_db() must be called before use")
    return _conn


async def insert_rule(rule_id: str, keyword: str, dm_message: str) -> None:
    async with _lock:
        _get_conn().execute(
            "INSERT INTO rules (id, keyword, dm_message, created_at) VALUES (?, ?, ?, ?)",
            (rule_id, keyword, dm_message, now_iso()),
        )
        _get_conn().commit()


async def fetch_all_rules() -> list[sqlite3.Row]:
    async with _lock:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, keyword, dm_message FROM rules").fetchall()
        conn.row_factory = None
        return rows


async def insert_dm_attempt(user_id: str, rule_id: str, comment_id: str) -> bool:
    """Atomically claim (user_id, rule_id). Returns True if this call won the race
    and a row was created, False if a matching attempt already existed — the caller
    should NOT send a DM in that case, and should log it as a blocked duplicate.

    This is the whole dedup mechanism: the UNIQUE(user_id, rule_id) constraint plus
    ON CONFLICT DO NOTHING means two concurrently-redelivered webhook events racing
    to insert the same pair can never both succeed. SQLite serializes writers, so
    exactly one INSERT commits — there's no read-then-write gap for a second event
    to land in, unlike a SELECT-then-INSERT check.
    """
    idempotency_key = f"{user_id}:{rule_id}"
    timestamp = now_iso()
    async with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            """
            INSERT INTO dm_attempts
                (user_id, rule_id, comment_id, idempotency_key, status,
                 attempt_count, next_attempt_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            ON CONFLICT (user_id, rule_id) DO NOTHING
            """,
            (user_id, rule_id, comment_id, idempotency_key, timestamp, timestamp, timestamp),
        )
        won = cursor.rowcount == 1
        if not won:
            conn.execute(
                "INSERT INTO dedup_blocked_log (user_id, rule_id, blocked_at) VALUES (?, ?, ?)",
                (user_id, rule_id, timestamp),
            )
        conn.commit()
        return won


async def cancel_pending_by_comment(comment_id: str) -> int:
    async with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            "UPDATE dm_attempts SET status = 'cancelled', updated_at = ? "
            "WHERE comment_id = ? AND status = 'pending'",
            (now_iso(), comment_id),
        )
        conn.commit()
        return cursor.rowcount


async def fetch_due_attempts(limit: int) -> list[sqlite3.Row]:
    async with _lock:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, user_id, rule_id, comment_id, idempotency_key, attempt_count, dm_id
            FROM dm_attempts
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY created_at
            LIMIT ?
            """,
            (now_iso(), limit),
        ).fetchall()
        conn.row_factory = None
        return rows


async def mark_in_flight(attempt_id: int, dm_id: str) -> None:
    async with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE dm_attempts SET status = 'in_flight', dm_id = ?, attempt_count = attempt_count + 1, "
            "updated_at = ? WHERE id = ?",
            (dm_id, now_iso(), attempt_id),
        )
        conn.commit()


async def mark_delivered(attempt_id: int) -> None:
    async with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE dm_attempts SET status = 'delivered', updated_at = ? WHERE id = ?",
            (now_iso(), attempt_id),
        )
        conn.commit()


async def mark_failed(attempt_id: int, error: str) -> None:
    async with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE dm_attempts SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
            (error, now_iso(), attempt_id),
        )
        conn.commit()


async def reschedule(attempt_id: int, next_attempt_at: str, error: str = "") -> None:
    async with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE dm_attempts SET attempt_count = attempt_count + 1, next_attempt_at = ?, "
            "last_error = ?, updated_at = ? WHERE id = ?",
            (next_attempt_at, error, now_iso(), attempt_id),
        )
        conn.commit()


async def fetch_stale_in_flight(older_than_seconds: int) -> list[sqlite3.Row]:
    async with _lock:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, dm_id, attempt_count FROM dm_attempts
            WHERE status = 'in_flight'
              AND updated_at <= datetime('now', ?)
            """,
            (f"-{older_than_seconds} seconds",),
        ).fetchall()
        conn.row_factory = None
        return rows


async def compute_stats() -> dict:
    async with _lock:
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status IN ('pending', 'in_flight') THEN 1 ELSE 0 END) AS queued
            FROM dm_attempts
            """
        ).fetchone()
        blocked = conn.execute("SELECT COUNT(*) FROM dedup_blocked_log").fetchone()[0]
        return {
            "sent": row[0] or 0,
            "failed": row[1] or 0,
            "queued": row[2] or 0,
            "duplicates_blocked": blocked or 0,
        }
