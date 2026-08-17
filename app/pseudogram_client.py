import os
import httpx
from typing import Dict, Any, Tuple, Optional

BASE_URL = os.getenv("PSEUDOGRAM_API_URL", "https://pseudogram-api.onrender.com")


class PseudogramClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def send_dm(
        self,
        recipient_user_id: str,
        message: str,
        comment_id: str,
        user_id: str,
        rule_id: str
    ) -> Tuple[int, Dict[str, Any], Optional[int]]:
        """
        Sends outbound DM request to /v1/dm/send with deterministic Idempotency-Key: f"{user_id}:{rule_id}".
        Returns (status_code, response_json, retry_after_seconds).
        """
        url = f"{self.base_url}/v1/dm/send"
        idempotency_key = f"{user_id}:{rule_id}"
        headers = {"Idempotency-Key": idempotency_key}
        payload = {
            "recipient_user_id": recipient_user_id,
            "message": message,
            "comment_id": comment_id
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            status_code = resp.status_code

            retry_after = None
            if status_code == 429:
                raw_retry = resp.headers.get("Retry-After")
                if raw_retry and raw_retry.isdigit():
                    retry_after = int(raw_retry)

            data = {}
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    pass

            return status_code, data, retry_after

    async def get_dm_status(self, dm_id: str) -> Tuple[int, Dict[str, Any]]:
        """
        Polls GET /v1/dm/{dm_id} for async delivery resolution.
        """
        url = f"{self.base_url}/v1/dm/{dm_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            data = {}
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    pass
            return resp.status_code, data
