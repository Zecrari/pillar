"""
Pillar Middleware Stack

Applied in this order (outermost → innermost):
  GZip → CORS → SecurityHeaders → RequestID → Timing → core handler

All middleware classes follow the raw ASGI protocol so they work with
any ASGI server (uvicorn, hypercorn, daphne, etc.).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, List, Optional


# ──────────────────────────────────────────────────────────────────────
# Security Headers
# ──────────────────────────────────────────────────────────────────────

class SecurityHeadersMiddleware:
    """
    Injects hardened HTTP security headers on every response.

    Headers added:
      X-Content-Type-Options: nosniff
      X-Frame-Options: DENY
      X-XSS-Protection: 1; mode=block
      Referrer-Policy: strict-origin-when-cross-origin
      Permissions-Policy: camera=(), microphone=(), geolocation=()
    """

    _HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options",        b"DENY"),
        (b"x-xss-protection",       b"1; mode=block"),
        (b"referrer-policy",        b"strict-origin-when-cross-origin"),
        (b"permissions-policy",     b"camera=(), microphone=(), geolocation=()"),
    ]

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ──────────────────────────────────────────────────────────────────────
# Request ID
# ──────────────────────────────────────────────────────────────────────

class RequestIDMiddleware:
    """
    Attaches a unique ``X-Request-ID`` header to every response.

    If the incoming request already has an ``X-Request-ID`` header it is
    echoed back; otherwise a new UUID4 is generated.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Look for existing request ID in incoming headers
        request_id = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                request_id = value.decode()
                break
        if not request_id:
            request_id = str(uuid.uuid4())

        # Make the ID available to handlers via scope
        scope = {**scope, "request_id": request_id}
        rid_header = (b"x-request-id", request_id.encode())

        async def send_with_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(rid_header)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_id)


# ──────────────────────────────────────────────────────────────────────
# Response Timing
# ──────────────────────────────────────────────────────────────────────

class TimingMiddleware:
    """
    Adds ``X-Response-Time`` (milliseconds) and ``X-Process-Time`` headers.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()

        async def send_with_timing(message: dict) -> None:
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-response-time", f"{elapsed_ms:.2f}ms".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_timing)


# ──────────────────────────────────────────────────────────────────────
# Middleware builder
# ──────────────────────────────────────────────────────────────────────

def apply_middleware(app: Any, config: Any) -> Any:
    """
    Wrap *app* with the configured middleware stack and return the
    outermost ASGI callable.

    Execution order (first to last):
      GZip → CORS → SecurityHeaders → RequestID → Tracer → Timing → core
    """
    # Timing (innermost after core)
    if getattr(config.security, "add_timing", True):
        app = TimingMiddleware(app)

    # Tracer — must run after RequestID so scope["request_id"] is set
    try:
        from .tracer import TraceMiddleware
        app = TraceMiddleware(app)
    except Exception:
        pass

    # Request ID
    if getattr(config.security, "add_request_id", True):
        app = RequestIDMiddleware(app)

    # Security headers
    if getattr(config.security, "add_security_headers", True):
        app = SecurityHeadersMiddleware(app)

    # CORS
    if getattr(config.cors, "enabled", True):
        try:
            from starlette.middleware.cors import CORSMiddleware
            app = CORSMiddleware(
                app,
                allow_origins=getattr(config.cors, "allow_origins", ["*"]),
                allow_methods=getattr(config.cors, "allow_methods", ["*"]),
                allow_headers=getattr(config.cors, "allow_headers", ["*"]),
                allow_credentials=getattr(config.cors, "allow_credentials", False),
                expose_headers=getattr(config.cors, "expose_headers", [
                    "X-Request-ID", "X-Response-Time",
                ]),
            )
        except ImportError:
            pass

    # GZip (outermost — compresses everything including error responses)
    try:
        from starlette.middleware.gzip import GZipMiddleware
        app = GZipMiddleware(app, minimum_size=512)
    except ImportError:
        pass

    return app
