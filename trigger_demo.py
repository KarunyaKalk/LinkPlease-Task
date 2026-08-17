import hmac
import hashlib
import json
import time
import sys
import urllib.request
import urllib.error

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://linkplease-task.onrender.com"
SECRET = "pseudogram_secret_key"


def http_post(url: str, json_data: dict, headers: dict = None):
    if headers is None:
        headers = {}
    data_bytes = json.dumps(json_data).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, json.loads(body) if body else {}


def http_get(url: str):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def send_signed_webhook(event_type: str, data: dict):
    payload = {
        "event_id": f"evt_{int(time.time()*1000)}",
        "event_type": event_type,
        "timestamp": int(time.time()),
        "data": data
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(SECRET.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    code, res = http_post(f"{BASE_URL}/webhook", payload, {"X-PseudoGram-Signature": sig})
    print(f"Webhook [{event_type}] -> HTTP {code}: {res}")


def main():
    print("--- 1. Creating Keyword Rule ('link') ---")
    code, rule_res = http_post(f"{BASE_URL}/rules", {
        "keyword": "link",
        "dm_message": "Here is your demo link!"
    })
    print(f"Rule response: {rule_res}")

    print("\n--- 2. Current Stats Before Events ---")
    print(http_get(f"{BASE_URL}/stats"))

    print("\n--- 3. Sending Webhook Comment Events ---")
    # Send comment 1 (User 1)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_1",
        "post_id": "post_100",
        "from": {"user_id": "usr_101", "username": "user1"},
        "text": "Please send me the link!"
    })

    # Send comment 2 (User 2)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_2",
        "post_id": "post_100",
        "from": {"user_id": "usr_102", "username": "user2"},
        "text": "Can I get the link too?"
    })

    # Send duplicate comment for User 1 (Triggers atomic deduplication!)
    send_signed_webhook("comment.created", {
        "comment_id": f"cmt_{time.time()}_3",
        "post_id": "post_100",
        "from": {"user_id": "usr_101", "username": "user1"},
        "text": "Send link again please"
    })

    print("\n--- 4. Updated Stats After Events ---")
    print(http_get(f"{BASE_URL}/stats"))


if __name__ == "__main__":
    main()
