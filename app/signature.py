import hmac
import hashlib
import os

API_KEY = os.getenv("PSEUDOGRAM_API_KEY", "pseudogram_secret_key")


def verify_signature(raw_body: bytes, signature_header: str, secret: str = API_KEY) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header.split("sha256=", 1)[1]
    computed_hex = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hex, expected_hex)
