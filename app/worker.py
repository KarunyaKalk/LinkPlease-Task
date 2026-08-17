import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from app.db import (
    fetch_pending_attempts,
    update_attempt_in_flight,
    update_attempt_retry,
    update_attempt_failed,
)
from app.pseudogram_client import PseudogramClient


class SlidingWindowRateLimiter:
    """
    Design Decision #5: In-memory sliding window rate limiter (10 req / 60s).
    Losing state on restart only risks a 429 from downstream, which is safely handled by retry backoff.
    """
    def __init__(self, limit: int = 10, window: float = 60.0):
        self.limit = limit
        self.window = window
        self.timestamps = []

    async def acquire(self):
        while True:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < self.window]
            if len(self.timestamps) < self.limit:
                self.timestamps.append(now)
                break
            sleep_time = self.timestamps[0] + self.window - now + 0.05
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)


async def process_pending_attempt(attempt: dict, client: PseudogramClient, rate_limiter: SlidingWindowRateLimiter):
    await rate_limiter.acquire()

    attempt_id = attempt["attempt_id"]
    user_id = attempt["user_id"]
    rule_id = attempt["rule_id"]
    comment_id = attempt["comment_id"]
    message = attempt["message"]
    attempt_count = attempt["attempt_count"]

    try:
        status_code, data, retry_after = await client.send_dm(
            recipient_user_id=user_id,
            message=message,
            comment_id=comment_id,
            user_id=user_id,
            rule_id=rule_id
        )

        if status_code == 202:
            dm_id = data.get("dm_id", "")
            await update_attempt_in_flight(attempt_id, dm_id)
        elif status_code == 429:
            delay = retry_after if (retry_after is not None and retry_after > 0) else 10
            next_attempt_dt = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await update_attempt_retry(attempt_id, attempt_count, next_attempt_dt.isoformat())
        elif status_code == 400:
            await update_attempt_failed(attempt_id, attempt_count + 1)
        else:
            new_count = attempt_count + 1
            if new_count >= 5:
                await update_attempt_failed(attempt_id, new_count)
            else:
                backoff_sec = 2 ** new_count
                next_attempt_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
                await update_attempt_retry(attempt_id, new_count, next_attempt_dt.isoformat())
    except Exception:
        new_count = attempt_count + 1
        if new_count >= 5:
            await update_attempt_failed(attempt_id, new_count)
        else:
            backoff_sec = 2 ** new_count
            next_attempt_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
            await update_attempt_retry(attempt_id, new_count, next_attempt_dt.isoformat())


async def worker_loop(client: Optional[PseudogramClient] = None, poll_interval: float = 0.5):
    if client is None:
        client = PseudogramClient()
    rate_limiter = SlidingWindowRateLimiter(limit=10, window=60.0)

    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            pending = await fetch_pending_attempts(now_iso, limit=10)
            if not pending:
                await asyncio.sleep(poll_interval)
                continue

            for attempt in pending:
                await process_pending_attempt(attempt, client, rate_limiter)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(poll_interval)
