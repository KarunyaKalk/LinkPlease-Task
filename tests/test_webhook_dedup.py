import os
import json
import hmac
import hashlib
import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.signature import API_KEY


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test.db")
    os.environ["DB_PATH"] = db_file
    init_db(db_file)

    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


def sign_payload(payload_bytes: bytes, secret: str = API_KEY) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_signature_validation(client):
    payload = {
        "event_id": "evt_1",
        "event_type": "comment.created",
        "data": {
            "comment_id": "cmt_1",
            "text": "hello",
            "from": {"user_id": "usr_test"}
        }
    }
    body = json.dumps(payload).encode("utf-8")

    # Invalid signature -> 401
    resp = client.post("/webhook", content=body, headers={"X-PseudoGram-Signature": "sha256=bad"})
    assert resp.status_code == 401

    # Valid signature -> 200
    headers = {"X-PseudoGram-Signature": sign_payload(body)}
    resp = client.post("/webhook", content=body, headers=headers)
    assert resp.status_code == 200


def test_webhook_dedup_and_stats(client):
    # Create a rule
    rule_resp = client.post("/rules", json={"keyword": "price", "dm_message": "Check our pricing at link.com"})
    assert rule_resp.status_code == 201
    rule_data = rule_resp.json()
    assert rule_data["keyword"] == "price"

    # Send first comment event
    webhook_data = {
        "event_id": "evt_100",
        "event_type": "comment.created",
        "sent_at": "2026-08-16T12:00:00Z",
        "data": {
            "comment_id": "cmt_100",
            "post_id": "post_1",
            "text": "How much is the PRICE?",
            "created_at": "2026-08-16T12:00:00Z",
            "from": {"user_id": "usr_1", "username": "alice"}
        }
    }
    body1 = json.dumps(webhook_data).encode("utf-8")
    headers1 = {"X-PseudoGram-Signature": sign_payload(body1)}
    r1 = client.post("/webhook", content=body1, headers=headers1)
    assert r1.status_code == 200

    # Stats after 1st event
    s1 = client.get("/stats").json()
    assert s1 == {"sent": 0, "failed": 0, "queued": 1, "duplicates_blocked": 0}

    # Send duplicate comment event from same user for same rule
    webhook_data_dup = {
        "event_id": "evt_101",
        "event_type": "comment.created",
        "sent_at": "2026-08-16T12:01:00Z",
        "data": {
            "comment_id": "cmt_101",
            "post_id": "post_1",
            "text": "What is the price again?",
            "created_at": "2026-08-16T12:01:00Z",
            "from": {"user_id": "usr_1", "username": "alice"}
        }
    }
    body2 = json.dumps(webhook_data_dup).encode("utf-8")
    headers2 = {"X-PseudoGram-Signature": sign_payload(body2)}
    r2 = client.post("/webhook", content=body2, headers=headers2)
    assert r2.status_code == 200

    # Stats after duplicate event -> queued still 1, duplicates_blocked is 1!
    s2 = client.get("/stats").json()
    assert s2 == {"sent": 0, "failed": 0, "queued": 1, "duplicates_blocked": 1}


def test_comment_deleted_cancels_pending(client):
    client.post("/rules", json={"keyword": "discount", "dm_message": "Use code SAVE10"})

    webhook_data = {
        "event_id": "evt_200",
        "event_type": "comment.created",
        "data": {
            "comment_id": "cmt_200",
            "text": "Can I get a discount?",
            "from": {"user_id": "usr_2", "username": "bob"}
        }
    }
    body1 = json.dumps(webhook_data).encode("utf-8")
    client.post("/webhook", content=body1, headers={"X-PseudoGram-Signature": sign_payload(body1)})

    # Initial stats -> 1 queued
    assert client.get("/stats").json()["queued"] == 1

    # Send comment.deleted
    deleted_data = {
        "event_id": "evt_201",
        "event_type": "comment.deleted",
        "data": {
            "comment_id": "cmt_200"
        }
    }
    body2 = json.dumps(deleted_data).encode("utf-8")
    client.post("/webhook", content=body2, headers={"X-PseudoGram-Signature": sign_payload(body2)})

    # Stats -> 0 queued, 1 failed (cancelled counts as failed in stats)
    stats = client.get("/stats").json()
    assert stats["queued"] == 0
    assert stats["failed"] == 1


def test_concurrent_webhook_dedup(client):
    import concurrent.futures

    client.post("/rules", json={"keyword": "sale", "dm_message": "50% off!"})

    payload = {
        "event_id": "evt_concurrent",
        "event_type": "comment.created",
        "data": {
            "comment_id": "cmt_concurrent",
            "text": "Tell me about the SALE!",
            "from": {"user_id": "usr_concurrent", "username": "charlie"}
        }
    }
    body = json.dumps(payload).encode("utf-8")
    sig = sign_payload(body)

    def send_request():
        return client.post("/webhook", content=body, headers={"X-PseudoGram-Signature": sig})

    num_threads = 20
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(send_request) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    for resp in results:
        assert resp.status_code == 200

    stats = client.get("/stats").json()
    # 1 attempt created, 19 duplicate attempts blocked by SQLite UNIQUE(user_id, rule_id) constraint
    assert stats["queued"] == 1
    assert stats["duplicates_blocked"] == 19
