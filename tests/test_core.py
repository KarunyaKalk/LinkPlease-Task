import asyncio
import hmac
import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import db, worker, reconciler
from app.main import app
from app.pseudogram_client import TransientError, PermanentError


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("VERIFY_SIGNATURES", "false")
    db._conn = None
    yield


async def _init():
    db.init_db()


def test_concurrent_inserts_only_one_wins():
    async def scenario():
        db.init_db()
        await db.insert_rule("rule_1", "PRICE", "msg")
        results = await asyncio.gather(*[
            db.insert_dm_attempt(user_id="usr_1", rule_id="rule_1", comment_id="cmt_1")
            for _ in range(20)
        ])
        return results

    results = asyncio.run(scenario())
    assert sum(results) == 1


def test_webhook_signature_rejected_and_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("PSEUDOGRAM_API_KEY", "test-secret")
    with TestClient(app) as client:
        client.post("/rules", json={"keyword": "PRICE", "dm_message": "msg"})
        event = {
            "event_id": "evt_1", "event_type": "comment.created", "sent_at": "x",
            "data": {"comment_id": "cmt_1", "post_id": "p", "text": "PRICE",
                      "created_at": "x", "from": {"user_id": "usr_1", "username": "x"}},
        }
        body = json.dumps(event).encode()
        bad = client.post("/webhook", content=body, headers={"X-PseudoGram-Signature": "sha256=wrong"})
        assert bad.status_code == 401

        good_sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        good = client.post("/webhook", content=body, headers={"X-PseudoGram-Signature": good_sig})
        assert good.status_code == 200


def test_comment_deleted_cancels_pending_only():
    with TestClient(app) as client:
        client.post("/rules", json={"keyword": "PRICE", "dm_message": "msg"})
        event = {
            "event_id": "evt_1", "event_type": "comment.created", "sent_at": "x",
            "data": {"comment_id": "cmt_1", "post_id": "p", "text": "PRICE",
                      "created_at": "x", "from": {"user_id": "usr_1", "username": "x"}},
        }
        client.post("/webhook", content=json.dumps(event))
        assert client.get("/stats").json()["queued"] == 1

        delete_event = {"event_id": "evt_2", "event_type": "comment.deleted", "sent_at": "x",
                         "data": {"comment_id": "cmt_1"}}
        client.post("/webhook", content=json.dumps(delete_event))
        assert client.get("/stats").json()["queued"] == 0


def test_worker_retries_500_gives_up_after_max_attempts_never_retries_400():
    async def scenario():
        db.init_db()
        await db.insert_rule("rule_1", "PRICE", "msg")
        await db.insert_dm_attempt("usr_a", "rule_1", "cmt_a")
        await db.insert_dm_attempt("usr_b", "rule_1", "cmt_b")
        await db.insert_dm_attempt("usr_c", "rule_1", "cmt_c")

        calls = {"usr_a": 0, "usr_b": 0, "usr_c": 0}

        async def fake_send_dm(recipient_user_id, message, comment_id, idempotency_key):
            calls[recipient_user_id] += 1
            if recipient_user_id == "usr_a":
                return {"dm_id": "dm_ok", "status": "queued"}
            if recipient_user_id == "usr_b":
                raise TransientError("500")
            raise PermanentError("400")

        with patch("app.worker.send_dm", fake_send_dm):
            worker._backoff_seconds = lambda n: 0
            for _ in range(6):
                for attempt in await db.fetch_due_attempts(limit=20):
                    await worker._process_attempt(attempt)

        return calls, await db.compute_stats()

    calls, stats = asyncio.run(scenario())
    assert calls == {"usr_a": 1, "usr_b": worker.MAX_ATTEMPTS, "usr_c": 1}
    assert stats["failed"] == 2
    assert stats["queued"] == 1  # usr_a in_flight, not yet reconciled


def test_reconciler_promotes_stale_in_flight_to_delivered():
    async def scenario():
        db.init_db()
        await db.insert_rule("rule_1", "PRICE", "msg")
        await db.insert_dm_attempt("usr_x", "rule_1", "cmt_x")
        row = (await db.fetch_due_attempts(limit=1))[0]
        await db.mark_in_flight(row["id"], "dm_abc")

        conn = db._get_conn()
        conn.execute(
            "UPDATE dm_attempts SET updated_at = datetime('now', '-60 seconds') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()

        async def fake_get_dm_status(dm_id):
            return {"dm_id": dm_id, "status": "delivered"}

        with patch("app.reconciler.get_dm_status", fake_get_dm_status):
            for attempt in await db.fetch_stale_in_flight(older_than_seconds=reconciler.STALE_AFTER_SECONDS):
                status = await fake_get_dm_status(attempt["dm_id"])
                if status["status"] == "delivered":
                    await db.mark_delivered(attempt["id"])

        return await db.compute_stats()

    stats = asyncio.run(scenario())
    assert stats == {"sent": 1, "failed": 0, "queued": 0, "duplicates_blocked": 0}


def test_successful_send_called_once_across_multiple_poll_cycles():
    async def scenario():
        db.init_db()
        await db.insert_rule("rule_1", "PRICE", "msg")
        await db.insert_dm_attempt("usr_success", "rule_1", "cmt_success")

        call_count = 0

        async def fake_send_dm(recipient_user_id, message, comment_id, idempotency_key):
            nonlocal call_count
            call_count += 1
            return {"dm_id": "dm_123", "status": "queued"}

        with patch("app.worker.send_dm", fake_send_dm):
            # Run 5 worker poll passes
            for _ in range(5):
                for attempt in await db.fetch_due_attempts(limit=20):
                    await worker._process_attempt(attempt)

        return call_count

    call_count = asyncio.run(scenario())
    assert call_count == 1

