import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from app.db import (
    fetch_in_flight_attempts,
    update_attempt_delivered,
    update_attempt_retry,
    update_attempt_failed,
)
from app.pseudogram_client import PseudogramClient


async def reconcile_in_flight_attempts(
    client: PseudogramClient,
    stale_seconds: float = 30.0
):
    cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    cutoff_iso = cutoff_dt.isoformat()

    in_flight_rows = await fetch_in_flight_attempts(cutoff_iso, limit=20)
    for row in in_flight_rows:
        attempt_id = row["attempt_id"]
        dm_id = row["dm_id"]
        attempt_count = row["attempt_count"]

        try:
            status_code, data = await client.get_dm_status(dm_id)
            if status_code == 200:
                dm_status = data.get("status")
                if dm_status == "delivered":
                    await update_attempt_delivered(attempt_id)
                elif dm_status == "failed":
                    new_count = attempt_count + 1
                    if new_count >= 5:
                        await update_attempt_failed(attempt_id, new_count)
                    else:
                        backoff_sec = 2 ** new_count
                        next_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
                        await update_attempt_retry(attempt_id, new_count, next_dt.isoformat())
        except Exception:
            pass


async def reconciler_loop(
    client: Optional[PseudogramClient] = None,
    stale_seconds: float = 30.0,
    poll_interval: float = 5.0
):
    if client is None:
        client = PseudogramClient()

    while True:
        try:
            await reconcile_in_flight_attempts(client, stale_seconds=stale_seconds)
            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(poll_interval)
