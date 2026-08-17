import hmac
import hashlib
import json
import time
import httpx

import sys

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://linkplease-task.onrender.com"
SECRET = "pseudogram_secret_key"


def send_signed_webhook(event_type: str, data: dict):
    payload = {
        "event_id": f"evt_{int(time.time()*1000)}",
        "event_type": event_type,
        "timestamp": int(time.time()),
        "data": data
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(SECRET.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    
    resp = httpx.post(
        f"{BASE_URL}/webhook",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "X-PseudoGram-Signature": sig}
    )
    print(f"Webhook [{event_type}] -> HTTP {resp.status_code}: {resp.json()}")

def main():
    print("--- 1. Creating Keyword Rule ('link') ---")
    rule_resp = httpx.post(f"{BASE_URL}/rules", json={
        "keyword": "link",
        "dm_message": "Here is your demo link!"
    })
    print(f"Rule response: {rule_resp.json()}")

    print("\n--- 2. Current Stats Before Events ---")
    print(httpx.get(f"{BASE_URL}/stats").json())

    print("\n--- 3. Sending Webhook Comment Events ---")
    # Send comment 1 (User 1)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_1",
        "post_id": "post_100",
        "from": {"user_id": f"usr_101", "username": "user1"},
        "text": "Please send me the link!"
    })

    # Send comment 2 (User 2)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_2",
        "post_id": "post_100",
        "from": {"user_id": f"usr_102", "username": "user2"},
        "text": "Can I get the link too?"
    })

    # Send duplicate comment for User 1 (Triggers atomic deduplication!)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_3",
        "post_id": "post_100",
        "from": {"user_id": f"usr_101", "username": "user1"},
        "text": "Send link again please"
    })


    print("\n--- 4. Updated Stats After Events ---")
    print(httpx.get(f"{BASE_URL}/stats").json())

if __name__ == "__main__":
    main()
