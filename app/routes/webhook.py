import uuid
import json
from fastapi import APIRouter, Request, Header, HTTPException, status
from app.signature import verify_signature
from app.schemas import WebhookPayload
from app.db import get_all_rules, record_event_and_dedup, cancel_pending_attempt

router = APIRouter()


@router.post("/webhook")
async def handle_webhook(
    request: Request,
    x_pseudogram_signature: str = Header(None, alias="X-PseudoGram-Signature")
):
    raw_body = await request.body()
    if not x_pseudogram_signature or not verify_signature(raw_body, x_pseudogram_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        data_dict = json.loads(raw_body)
        payload = WebhookPayload(**data_dict)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload")

    if payload.event_type == "comment.deleted":
        await cancel_pending_attempt(payload.data.comment_id)
        return {"status": "ok"}

    if payload.event_type == "comment.created":
        if not payload.data.text or not payload.data.from_user or not payload.data.from_user.user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing comment data")

        user_id = payload.data.from_user.user_id
        text = payload.data.text
        comment_id = payload.data.comment_id

        rules = await get_all_rules()
        for rule in rules:
            if rule["keyword"].lower() in text.lower():
                attempt_id = f"att_{uuid.uuid4().hex[:12]}"
                await record_event_and_dedup(
                    user_id=user_id,
                    rule_id=rule["rule_id"],
                    comment_id=comment_id,
                    message=rule["dm_message"],
                    attempt_id=attempt_id
                )

        return {"status": "ok"}

    return {"status": "ignored"}
