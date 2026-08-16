import os
import httpx

def _get_config() -> tuple[str, str]:
    base_url = os.environ.get("PSEUDOGRAM_BASE_URL", "https://pseudogram-api.onrender.com")
    api_key = os.environ.get("PSEUDOGRAM_API_KEY", "")
    return base_url, api_key


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


async def send_dm(recipient_user_id: str, message: str, comment_id: str, idempotency_key: str) -> dict:
    base_url, api_key = _get_config()
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        response = await client.post(
            "/v1/dm/send",
            json={"recipient_user_id": recipient_user_id, "message": message, "comment_id": comment_id},
            headers={"X-API-Key": api_key, "Idempotency-Key": idempotency_key},
        )
    if response.status_code == 202:
        return response.json()
    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", "5"))
        raise RateLimited(retry_after)
    if response.status_code == 500:
        raise TransientError(response.text)
    if response.status_code == 400:
        raise PermanentError(response.text)
    raise TransientError(f"unexpected status {response.status_code}: {response.text}")


async def get_dm_status(dm_id: str) -> dict:
    base_url, api_key = _get_config()
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        response = await client.get(f"/v1/dm/{dm_id}", headers={"X-API-Key": api_key})
    response.raise_for_status()
    return response.json()
