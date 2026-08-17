import sqlite3
import asyncio
import threading
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

def get_db_path() -> str:
    return os.getenv("DB_PATH", "app.db")


_db_lock = threading.Lock()


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id TEXT PRIMARY KEY,
                keyword TEXT NOT NULL,
                dm_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                dm_id TEXT,
                attempt_count INTEGER DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CONSTRAINT unq_user_rule UNIQUE (user_id, rule_id)
            );

            CREATE TABLE IF NOT EXISTS stats_counters (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            );

            INSERT OR IGNORE INTO stats_counters (key, value) VALUES ('duplicates_blocked', 0);
        """)


def _insert_rule_sync(rule_id: str, keyword: str, dm_message: str, created_at: str, db_path: str) -> Dict[str, str]:
    with _db_lock:
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO rules (rule_id, keyword, dm_message, created_at) VALUES (?, ?, ?, ?)",
                (rule_id, keyword, dm_message, created_at)
            )
            conn.commit()
    return {"rule_id": rule_id, "keyword": keyword, "dm_message": dm_message}


async def insert_rule(rule_id: str, keyword: str, dm_message: str, db_path: Optional[str] = None) -> Dict[str, str]:
    if db_path is None:
        db_path = get_db_path()
    created_at = datetime.now(timezone.utc).isoformat()
    return await asyncio.to_thread(_insert_rule_sync, rule_id, keyword, dm_message, created_at, db_path)


def _get_all_rules_sync(db_path: str) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT rule_id, keyword, dm_message FROM rules").fetchall()


async def get_all_rules(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    if db_path is None:
        db_path = get_db_path()
    return await asyncio.to_thread(_get_all_rules_sync, db_path)


def _record_event_and_dedup_sync(
    user_id: str,
    rule_id: str,
    comment_id: str,
    message: str,
    attempt_id: str,
    now_iso: str,
    db_path: str
) -> bool:
    """
    Inserts a pending DM attempt. SQLite handles concurrency safely via the UNIQUE(user_id, rule_id)
    constraint. If the insertion fails due to conflict, atomic increment of duplicates_blocked runs.
    """
    with _db_lock:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO dm_attempts (
                    attempt_id, user_id, rule_id, comment_id, message, status, attempt_count, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                ON CONFLICT(user_id, rule_id) DO NOTHING;
                """,
                (attempt_id, user_id, rule_id, comment_id, message, now_iso, now_iso, now_iso)
            )
            if cursor.rowcount == 0:
                conn.execute(
                    "UPDATE stats_counters SET value = value + 1 WHERE key = 'duplicates_blocked'"
                )
                conn.commit()
                return False
            conn.commit()
            return True


async def record_event_and_dedup(
    user_id: str,
    rule_id: str,
    comment_id: str,
    message: str,
    attempt_id: str,
    db_path: Optional[str] = None
) -> bool:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    return await asyncio.to_thread(
        _record_event_and_dedup_sync, user_id, rule_id, comment_id, message, attempt_id, now_iso, db_path
    )


def _cancel_pending_attempt_sync(comment_id: str, now_iso: str, db_path: str) -> int:
    with _db_lock:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "UPDATE dm_attempts SET status = 'cancelled', updated_at = ? WHERE comment_id = ? AND status = 'pending'",
                (now_iso, comment_id)
            )
            conn.commit()
            return cursor.rowcount


async def cancel_pending_attempt(comment_id: str, db_path: Optional[str] = None) -> int:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    return await asyncio.to_thread(_cancel_pending_attempt_sync, comment_id, now_iso, db_path)


def _get_stats_sync(db_path: str) -> Dict[str, int]:
    with get_connection(db_path) as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM dm_attempts GROUP BY status"
        ).fetchall()
        status_counts = {row["status"]: row["cnt"] for row in status_rows}

        counter_row = conn.execute(
            "SELECT value FROM stats_counters WHERE key = 'duplicates_blocked'"
        ).fetchone()
        duplicates_blocked = counter_row["value"] if counter_row else 0

    sent = status_counts.get("delivered", 0)
    failed = status_counts.get("failed", 0) + status_counts.get("cancelled", 0)
    queued = status_counts.get("pending", 0) + status_counts.get("in_flight", 0)

    return {
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "duplicates_blocked": duplicates_blocked
    }


async def get_stats(db_path: Optional[str] = None) -> Dict[str, int]:
    if db_path is None:
        db_path = get_db_path()
    return await asyncio.to_thread(_get_stats_sync, db_path)


def _fetch_pending_attempts_sync(now_iso: str, limit: int, db_path: str) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            """
            SELECT attempt_id, user_id, rule_id, comment_id, message, attempt_count
            FROM dm_attempts
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC
            LIMIT ?
            """,
            (now_iso, limit)
        ).fetchall()


async def fetch_pending_attempts(now_iso: str, limit: int = 10, db_path: Optional[str] = None) -> List[sqlite3.Row]:
    if db_path is None:
        db_path = get_db_path()
    return await asyncio.to_thread(_fetch_pending_attempts_sync, now_iso, limit, db_path)


def _update_attempt_in_flight_sync(attempt_id: str, dm_id: str, now_iso: str, db_path: str) -> None:
    with _db_lock:
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE dm_attempts SET status = 'in_flight', dm_id = ?, updated_at = ? WHERE attempt_id = ?",
                (dm_id, now_iso, attempt_id)
            )
            conn.commit()


async def update_attempt_in_flight(attempt_id: str, dm_id: str, db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_update_attempt_in_flight_sync, attempt_id, dm_id, now_iso, db_path)


def _update_attempt_retry_sync(attempt_id: str, attempt_count: int, next_attempt_at: str, now_iso: str, db_path: str) -> None:
    with _db_lock:
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE dm_attempts SET status = 'pending', attempt_count = ?, next_attempt_at = ?, updated_at = ? WHERE attempt_id = ?",
                (attempt_count, next_attempt_at, now_iso, attempt_id)
            )
            conn.commit()


async def update_attempt_retry(attempt_id: str, attempt_count: int, next_attempt_at: str, db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_update_attempt_retry_sync, attempt_id, attempt_count, next_attempt_at, now_iso, db_path)


def _update_attempt_failed_sync(attempt_id: str, attempt_count: int, now_iso: str, db_path: str) -> None:
    with _db_lock:
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE dm_attempts SET status = 'failed', attempt_count = ?, updated_at = ? WHERE attempt_id = ?",
                (attempt_count, now_iso, attempt_id)
            )
            conn.commit()


async def update_attempt_failed(attempt_id: str, attempt_count: int, db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_update_attempt_failed_sync, attempt_id, attempt_count, now_iso, db_path)


def _fetch_in_flight_attempts_sync(older_than_iso: str, limit: int, db_path: str) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            """
            SELECT attempt_id, dm_id, attempt_count
            FROM dm_attempts
            WHERE status = 'in_flight' AND updated_at <= ? AND dm_id IS NOT NULL
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (older_than_iso, limit)
        ).fetchall()


async def fetch_in_flight_attempts(older_than_iso: str, limit: int = 20, db_path: Optional[str] = None) -> List[sqlite3.Row]:
    if db_path is None:
        db_path = get_db_path()
    return await asyncio.to_thread(_fetch_in_flight_attempts_sync, older_than_iso, limit, db_path)


def _update_attempt_delivered_sync(attempt_id: str, now_iso: str, db_path: str) -> None:
    with _db_lock:
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE dm_attempts SET status = 'delivered', updated_at = ? WHERE attempt_id = ?",
                (now_iso, attempt_id)
            )
            conn.commit()


async def update_attempt_delivered(attempt_id: str, db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_update_attempt_delivered_sync, attempt_id, now_iso, db_path)
