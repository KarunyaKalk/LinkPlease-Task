import os
from fastapi import APIRouter, Request, HTTPException

from app import db
from app.signature import verify_signature

router = APIRouter()


@router.post("/webhook", status_code=200)
async def receive_webhook(request: Request):
    raw_body = await request.body()

    # Read from the environment per-request rather than at import time, so
    # tests can toggle VERIFY_SIGNATURES without reloading the module, and a
    # deployed process can rotate PSEUDOGRAM_API_KEY without a restart.
    verify_signatures = os.environ.get("VERIFY_SIGNATURES", "true").lower() == "true"
    if verify_signatures:
        api_key = os.environ.get("PSEUDOGRAM_API_KEY", "")
        signature = request.headers.get("X-PseudoGram-Signature")
        if not verify_signature(raw_body, signature, api_key):
            raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    event_type = payload.get("event_type")
    data = payload.get("data", {})

    if event_type == "comment.deleted":
        await db.cancel_pending_by_comment(data["comment_id"])
        return {"status": "ok"}

    if event_type == "comment.created":
        text = data["text"].lower()
        user_id = data["from"]["user_id"]
        comment_id = data["comment_id"]
        for rule in await db.fetch_all_rules():
            if rule["keyword"].lower() in text:
                await db.insert_dm_attempt(user_id=user_id, rule_id=rule["id"], comment_id=comment_id)
        return {"status": "ok"}

    # Unrecognized event types are ignored rather than rejected — the platform may
    # add new event types over time and we only care about the two documented here.
    return {"status": "ignored"}
