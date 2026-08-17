import os
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from app.db import (
    init_db,
    record_event_and_dedup,
    insert_rule,
    get_stats,
    update_attempt_in_flight,
    get_connection,
)
from app.pseudogram_client import PseudogramClient
from app.worker import process_pending_attempt, SlidingWindowRateLimiter
from app.reconciler import reconcile_in_flight_attempts


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_file = str(tmp_path / "worker_test.db")
    os.environ["DB_PATH"] = db_file
    init_db(db_file)
    yield db_file


@pytest.mark.asyncio
async def test_worker_202_transitions_to_in_flight():
    await insert_rule("rule_1", "info", "Here is info")
    await record_event_and_dedup("usr_1", "rule_1", "cmt_1", "Here is info", "att_1")

    client = PseudogramClient()
    client.send_dm = AsyncMock(return_value=(202, {"dm_id": "dm_123", "status": "queued"}, None))
    rate_limiter = SlidingWindowRateLimiter(limit=10, window=60)

    attempt = {
        "attempt_id": "att_1",
        "user_id": "usr_1",
        "rule_id": "rule_1",
        "comment_id": "cmt_1",
        "message": "Here is info",
        "attempt_count": 0
    }
    await process_pending_attempt(attempt, client, rate_limiter)

    # Assert send_dm called with deterministic Idempotency-Key f"{user_id}:{rule_id}"
    client.send_dm.assert_called_once_with(
        recipient_user_id="usr_1",
        message="Here is info",
        comment_id="cmt_1",
        user_id="usr_1",
        rule_id="rule_1"
    )

    stats = await get_stats()
    assert stats["queued"] == 1  # in_flight counts as queued in stats

    with get_connection() as conn:
        row = conn.execute("SELECT status, dm_id FROM dm_attempts WHERE attempt_id = 'att_1'").fetchone()
        assert row["status"] == "in_flight"
        assert row["dm_id"] == "dm_123"


@pytest.mark.asyncio
async def test_worker_400_fails_immediately():
    await insert_rule("rule_2", "price", "Price is $10")
    await record_event_and_dedup("usr_2", "rule_2", "cmt_2", "Price is $10", "att_2")

    client = PseudogramClient()
    client.send_dm = AsyncMock(return_value=(400, {"error": "bad request"}, None))
    rate_limiter = SlidingWindowRateLimiter(limit=10, window=60)

    attempt = {
        "attempt_id": "att_2",
        "user_id": "usr_2",
        "rule_id": "rule_2",
        "comment_id": "cmt_2",
        "message": "Price is $10",
        "attempt_count": 0
    }
    await process_pending_attempt(attempt, client, rate_limiter)

    stats = await get_stats()
    assert stats["failed"] == 1
    assert stats["queued"] == 0


@pytest.mark.asyncio
async def test_reconciler_delivered_status():
    await insert_rule("rule_3", "buy", "Buy link")
    await record_event_and_dedup("usr_3", "rule_3", "cmt_3", "Buy link", "att_3")
    await update_attempt_in_flight("att_3", "dm_999")

    # Set updated_at to 40 seconds ago to simulate stale in_flight row
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE dm_attempts SET updated_at = ? WHERE attempt_id = 'att_3'", (old_time,))
        conn.commit()

    client = PseudogramClient()
    client.get_dm_status = AsyncMock(return_value=(200, {"dm_id": "dm_999", "status": "delivered"}))

    await reconcile_in_flight_attempts(client, stale_seconds=30)

    stats = await get_stats()
    assert stats["sent"] == 1
    assert stats["queued"] == 0
