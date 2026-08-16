from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app import db
from app.pseudogram_client import send_dm, RateLimited, TransientError, PermanentError

MAX_ATTEMPTS = 5
RATE_LIMIT_PER_WINDOW = 10
RATE_LIMIT_WINDOW_SECONDS = 60
POLL_INTERVAL_SECONDS = 1

# Intentionally in-memory, not persisted. Losing this window on restart just risks
# one avoidable 429 on the next send, which the retry path below already handles
# safely — persisting it would add write traffic for no correctness benefit, since
# nothing here can cause a lost or duplicated DM.
_send_timestamps: list[float] = []


def _rate_limit_wait() -> float:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while _send_timestamps and _send_timestamps[0] < cutoff:
        _send_timestamps.pop(0)
    if len(_send_timestamps) < RATE_LIMIT_PER_WINDOW:
        return 0.0
    return _send_timestamps[0] + RATE_LIMIT_WINDOW_SECONDS - now


def _backoff_seconds(attempt_count: int) -> float:
    return min(2 ** attempt_count, 60)


def _iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


async def _rule_message(rule_id: str) -> str | None:
    for rule in await db.fetch_all_rules():
        if rule["id"] == rule_id:
            return rule["dm_message"]
    return None


async def _process_attempt(attempt) -> None:
    wait = _rate_limit_wait()
    if wait > 0:
        await db.reschedule(attempt["id"], _iso_in(wait), error="")
        return

    message = await _rule_message(attempt["rule_id"])
    if message is None:
        # Rule was deleted after the attempt was queued. No retry will fix this.
        await db.mark_failed(attempt["id"], "rule no longer exists")
        return

    try:
        _send_timestamps.append(time.monotonic())
        result = await send_dm(
            recipient_user_id=attempt["user_id"],
            message=message,
            comment_id=attempt["comment_id"],
            idempotency_key=attempt["idempotency_key"],
        )
        await db.mark_in_flight(attempt["id"], result["dm_id"])
    except RateLimited as exc:
        await db.reschedule(attempt["id"], _iso_in(exc.retry_after), error="rate_limited")
    except PermanentError as exc:
        await db.mark_failed(attempt["id"], str(exc))
    except TransientError as exc:
        if attempt["attempt_count"] + 1 >= MAX_ATTEMPTS:
            await db.mark_failed(attempt["id"], f"gave up after {MAX_ATTEMPTS} attempts: {exc}")
        else:
            backoff = _backoff_seconds(attempt["attempt_count"])
            await db.reschedule(attempt["id"], _iso_in(backoff), error=str(exc))


async def run_worker_loop() -> None:
    while True:
        due = await db.fetch_due_attempts(limit=20)
        for attempt in due:
            await _process_attempt(attempt)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
