"""
Minimal JWT/token auth helpers.

In a real app, replace the secret key with an env-var and add
token-expiry validation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Optional


_SECRET = "pillar-example-secret-change-in-production"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_token(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body   = _b64url(json.dumps(payload).encode())
    sig    = _b64url(
        hmac.new(_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{body}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    try:
        header, body, sig = token.split(".")
        expected = _b64url(
            hmac.new(_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        padding = "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(body + padding))
    except Exception:
        return None
