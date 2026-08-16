from __future__ import annotations

import hmac
import hashlib


def verify_signature(raw_body: bytes, header_value: str | None, secret: str) -> bool:
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = header_value[len("sha256="):]
    # compare_digest, not ==, so this isn't a timing side-channel on the signature
    return hmac.compare_digest(expected, provided)
