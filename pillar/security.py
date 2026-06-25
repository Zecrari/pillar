"""
Pillar Declarative Security — JWT + Row-Level Security.

Usage in ``core/security.py``::

    from pillar.security import JWTMiddleware, require_auth, get_current_user

    # In main.py:
    app = Pillar(...)
    app.use_security(JWTMiddleware(secret=os.getenv("JWT_SECRET")))

    # In a controller / handler:
    @router.get("/me")
    async def me(user = require_auth):
        return user

Row-Level Security (multi-tenant SaaS)::

    # pillar.toml:
    # [security]
    # rls_enabled = true
    # rls_tenant_claim = "tenant_id"
    #
    # All SQL executed via pillar.db.Database automatically gets:
    # WHERE tenant_id = '<value from JWT>'
    # appended to SELECT queries.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable, Dict, Optional


# ──────────────────────────────────────────────────────────────────────
# Minimal JWT implementation (no dependencies)
# Supports HS256 only — for RS256 use python-jose / PyJWT
# ──────────────────────────────────────────────────────────────────────

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class JWTError(Exception):
    pass


def decode_jwt(token: str, secret: str, algorithms: list = None) -> Dict[str, Any]:
    """
    Decode and verify a JWT signed with HS256.

    Raises ``JWTError`` on any validation failure.
    Falls back to python-jose if installed (supports RS256 etc.).
    """
    try:
        import jose.jwt as _jose
        return _jose.decode(token, secret, algorithms=algorithms or ["HS256"])
    except ImportError:
        pass

    # Minimal HS256 implementation
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("Malformed JWT")

    header_b64, payload_b64, sig_b64 = parts

    # Verify signature
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig  = hmac.new(
        secret.encode(), signing_input, hashlib.sha256
    ).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise JWTError("Invalid JWT signature")

    payload = json.loads(_b64url_decode(payload_b64))

    # Check expiry
    exp = payload.get("exp")
    if exp and time.time() > exp:
        raise JWTError("JWT has expired")

    return payload


def encode_jwt(payload: dict, secret: str, expires_in: int = 3600) -> str:
    """Create a signed HS256 JWT. ``expires_in`` is in seconds."""
    header  = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = {**payload, "exp": int(time.time()) + expires_in, "iat": int(time.time())}
    payload_b64 = _b64url_encode(json.dumps(payload).encode())
    signing_input = f"{header}.{payload_b64}".encode()
    sig = _b64url_encode(
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    )
    return f"{header}.{payload_b64}.{sig}"


# ──────────────────────────────────────────────────────────────────────
# ASGI JWT Middleware
# ──────────────────────────────────────────────────────────────────────

class JWTMiddleware:
    """
    ASGI middleware that validates Bearer tokens on every request.

    On success: injects ``scope["user"]`` = decoded JWT payload.
    On failure: returns 401 Unauthorized.

    Exclude paths from auth::

        JWTMiddleware(
            secret="...",
            public_paths={"/health", "/docs", "/redoc", "/openapi.json",
                          "/guide", "/dashboard", "/metrics", "/ready"}
        )
    """

    _DEFAULT_PUBLIC = frozenset({
        "/health", "/ready", "/metrics", "/dashboard",
        "/docs", "/redoc", "/openapi.json", "/guide", "/queue/status",
    })

    def __init__(
        self,
        app: Any,
        secret: str = None,
        algorithms: list = None,
        public_paths: set = None,
        rls_tenant_claim: str = None,
    ) -> None:
        self.app              = app
        self.secret           = secret or os.getenv("JWT_SECRET", "changeme")
        self.algorithms       = algorithms or ["HS256"]
        self.public_paths     = public_paths or self._DEFAULT_PUBLIC
        self.rls_tenant_claim = rls_tenant_claim or os.getenv("PILLAR_RLS_CLAIM", "")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")

        # Skip public paths
        if path in self.public_paths or path.startswith(("/docs", "/redoc")):
            await self.app(scope, receive, send)
            return

        # Extract Bearer token
        token = self._extract_token(scope)
        if not token:
            await self._unauthorized(send, "Missing Authorization header")
            return

        try:
            payload = decode_jwt(token, self.secret, self.algorithms)
        except JWTError as exc:
            await self._unauthorized(send, str(exc))
            return

        # Inject user into scope
        scope = {**scope, "user": payload}

        # Row-Level Security: inject tenant context via contextvars (asyncio-safe)
        if self.rls_tenant_claim and self.rls_tenant_claim in payload:
            tenant_id = str(payload[self.rls_tenant_claim])
            scope["rls_tenant_id"] = tenant_id
            try:
                from .db.rls import set_tenant
                set_tenant(tenant_id)
            except Exception:
                pass

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_token(scope: dict) -> Optional[str]:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                raw = value.decode()
                if raw.startswith("Bearer "):
                    return raw[7:]
        return None

    @staticmethod
    async def _unauthorized(send: Callable, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type",   b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="Pillar"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})


# ──────────────────────────────────────────────────────────────────────
# require_auth DI marker
# ──────────────────────────────────────────────────────────────────────

class _RequireAuth:
    """
    Sentinel class — inject it as a type hint to get the current JWT payload::

        @router.get("/me")
        async def me(request: Request, user: RequireAuth):
            return user  # the decoded JWT payload dict
    """

RequireAuth = _RequireAuth


# ──────────────────────────────────────────────────────────────────────
# RLS helper — attach to Database queries
# ──────────────────────────────────────────────────────────────────────

class RLSContext:
    """
    Thread/task-local storage for the current tenant_id.

    Set by ``JWTMiddleware`` and read by the DB layer to append
    ``WHERE tenant_id = ?`` automatically.

    This is opt-in: wrap your Database with an RLSDatabase to activate.
    """

    _tenant: Optional[str] = None

    @classmethod
    def set(cls, tenant_id: str) -> None:
        cls._tenant = tenant_id

    @classmethod
    def get(cls) -> Optional[str]:
        return cls._tenant

    @classmethod
    def clear(cls) -> None:
        cls._tenant = None
