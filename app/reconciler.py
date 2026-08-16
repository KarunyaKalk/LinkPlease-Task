import asyncio

from app import db
from app.pseudogram_client import get_dm_status
from app.worker import MAX_ATTEMPTS, _backoff_seconds, _iso_in

STALE_AFTER_SECONDS = 30
RECONCILE_INTERVAL_SECONDS = 10


async def run_reconciler_loop() -> None:
    while True:
        stale = await db.fetch_stale_in_flight(older_than_seconds=STALE_AFTER_SECONDS)
        for attempt in stale:
            status = await get_dm_status(attempt["dm_id"])
            if status["status"] == "delivered":
                await db.mark_delivered(attempt["id"])
            elif status["status"] == "failed":
                if attempt["attempt_count"] >= MAX_ATTEMPTS:
                    await db.mark_failed(attempt["id"], "delivery failed after reconciliation, attempts exhausted")
                else:
                    backoff = _backoff_seconds(attempt["attempt_count"])
                    await db.reschedule(attempt["id"], _iso_in(backoff), error="delivery failed, retrying")
            # status == "queued" still: leave it, check again next pass
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
