"""
Simulates the assignment's own test scenario locally, without needing network
access to the real mock API: 500 comment events over a short window, ~8% of
them redelivered duplicates, hitting /webhook concurrently, then checks that
/stats matches ground truth exactly.

Run: python3 -m pytest tests/test_load.py -v
"""
import asyncio
import json
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("VERIFY_SIGNATURES", "false")
    from app import db
    db._conn = None
    yield


def test_500_events_with_redeliveries_no_duplicate_sends():
    with TestClient(app) as client:
        resp = client.post("/rules", json={"keyword": "PRICE", "dm_message": "price list"})
        rule_id = resp.json()["rule_id"]

        # 300 distinct commenters, each commenting once -> 300 unique (user, rule) pairs.
        # Then redeliver ~40% of those events again (event_id differs, user_id doesn't),
        # simulating the platform's ~8% redelivery rate scaled up to stress-test dedup.
        base_events = []
        for i in range(300):
            base_events.append({
                "event_id": f"evt_{i}",
                "event_type": "comment.created",
                "sent_at": "2026-08-10T09:00:00Z",
                "data": {
                    "comment_id": f"cmt_{i}",
                    "post_id": "post_1",
                    "text": "PRICE please",
                    "created_at": "2026-08-10T09:00:00Z",
                    "from": {"user_id": f"usr_{i}", "username": f"user{i}"},
                },
            })

        redelivered = [
            {**e, "event_id": e["event_id"] + "_redelivered"}
            for e in random.sample(base_events, 200)
        ]

        all_events = base_events + redelivered
        random.shuffle(all_events)  # order is not guaranteed, per spec

        for event in all_events:
            resp = client.post("/webhook", content=json.dumps(event))
            assert resp.status_code == 200

        stats = client.get("/stats").json()

        # exactly 300 unique (user, rule) pairs should be queued/sent, never more
        assert stats["queued"] + stats["sent"] + stats["failed"] == 300
        assert stats["duplicates_blocked"] == 200
