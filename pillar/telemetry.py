"""
Pillar OpenTelemetry Integration — zero-config traces, metrics, logs.

Install the extras::

    pip install opentelemetry-sdk opentelemetry-exporter-otlp

Then call once at startup::

    from pillar.telemetry import setup_telemetry
    setup_telemetry(service_name="my-api")

Or use environment variables (OTel standard)::

    OTEL_SERVICE_NAME=my-api
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317    # Jaeger / Grafana Tempo
    OTEL_TRACES_EXPORTER=otlp                            # or "console" for debugging

If ``opentelemetry-sdk`` is NOT installed, every call in this module is a
no-op — no import errors, no warnings, no performance overhead.

Instrument your own code::

    from pillar.telemetry import trace_span, current_span

    @trace_span("payment.charge")
    async def charge(amount: float): ...

    # Or manual:
    with current_span().start_as_current_span("redis.get") as span:
        span.set_attribute("key", cache_key)
        return await redis.get(cache_key)
"""
from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Callable, Dict, Optional

# ──────────────────────────────────────────────────────────────────────
# Optional OTel import — everything degrades to no-ops if not installed
# ──────────────────────────────────────────────────────────────────────

_OTEL_AVAILABLE = False

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BSP
    from opentelemetry.sdk.resources import Resource as _Resource
    _OTEL_AVAILABLE = True
except ImportError:
    pass

_tracer: Optional[Any] = None


# ──────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────

def setup_telemetry(
    *,
    service_name: str = None,
    endpoint: str = None,
    exporter: str = None,
    extra_attributes: Dict[str, str] = None,
) -> bool:
    """
    Configure OpenTelemetry.  Returns True if OTel SDK is installed.

    Reads from env if params are omitted:
      OTEL_SERVICE_NAME, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_TRACES_EXPORTER

    Args:
        service_name:      Service name reported to backends.
        endpoint:          OTLP gRPC or HTTP endpoint.
        exporter:          "otlp" | "otlp_http" | "console" | "none".
        extra_attributes:  Additional resource attributes.
    """
    global _tracer

    if not _OTEL_AVAILABLE:
        return False

    svc = service_name or os.getenv("OTEL_SERVICE_NAME", "pillar-app")
    attrs = {"service.name": svc, "service.framework": "pillar", **(extra_attributes or {})}
    resource = _Resource.create(attrs)
    provider = _TracerProvider(resource=resource)

    ep       = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    exp_name = exporter or os.getenv("OTEL_TRACES_EXPORTER", "otlp")

    if exp_name == "console":
        _add_console_exporter(provider)
    elif exp_name == "none":
        pass
    elif ep:
        _add_otlp_exporter(provider, ep, exp_name)
    # else: no exporter → in-memory only (useful for Pillar's built-in tracer)

    _otel_trace.set_tracer_provider(provider)
    _tracer = _otel_trace.get_tracer(
        "pillar",
        schema_url="https://opentelemetry.io/schemas/1.11.0",
    )
    return True


def _add_otlp_exporter(provider: Any, endpoint: str, style: str) -> None:
    if style == "otlp_http":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            ep = endpoint if "/v1/traces" in endpoint else endpoint.rstrip("/") + "/v1/traces"
            provider.add_span_processor(_BSP(OTLPSpanExporter(endpoint=ep)))
            return
        except ImportError:
            pass
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(_BSP(OTLPSpanExporter(endpoint=endpoint)))
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            ep = endpoint if "/v1/traces" in endpoint else endpoint.rstrip("/") + "/v1/traces"
            provider.add_span_processor(_BSP(OTLPSpanExporter(endpoint=ep)))
        except ImportError:
            pass


def _add_console_exporter(provider: Any) -> None:
    try:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        provider.add_span_processor(_BSP(ConsoleSpanExporter()))
    except ImportError:
        pass


# ──────────────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────────────

def current_span() -> Any:
    """Return the active OTel span (or a no-op shim if unavailable)."""
    if _OTEL_AVAILABLE and _tracer is not None:
        return _otel_trace.get_current_span()
    return _NoOpSpan()


def get_tracer() -> Any:
    """Return the configured OTel tracer (or a no-op shim)."""
    if _OTEL_AVAILABLE and _tracer is not None:
        return _tracer
    return _NoOpTracer()


def trace_span(name: str, attributes: Dict[str, Any] = None):
    """
    Decorator that wraps a sync or async function in an OTel span.

    Usage::

        @trace_span("service.get_user")
        async def get_user(self, user_id: int): ...

        @trace_span("repo.find_all", attributes={"db.system": "sqlite"})
        def find_all(self): ...
    """
    def decorator(func: Callable) -> Callable:
        if not _OTEL_AVAILABLE:
            return func

        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def _async(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(name) as span:
                    _apply_attrs(span, attributes)
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        _record_error(span, exc)
                        raise
            return _async
        else:
            @functools.wraps(func)
            def _sync(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(name) as span:
                    _apply_attrs(span, attributes)
                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:
                        _record_error(span, exc)
                        raise
            return _sync

    return decorator


def _apply_attrs(span: Any, attrs: Optional[Dict]) -> None:
    if attrs:
        for k, v in attrs.items():
            try:
                span.set_attribute(k, str(v))
            except Exception:
                pass


def _record_error(span: Any, exc: Exception) -> None:
    try:
        span.record_exception(exc)
        if _OTEL_AVAILABLE:
            from opentelemetry.trace import StatusCode
            span.set_status(StatusCode.ERROR, str(exc))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
# No-op shims
# ──────────────────────────────────────────────────────────────────────

class _NoOpSpan:
    def __enter__(self) -> "_NoOpSpan": return self
    def __exit__(self, *a: Any) -> bool: return False
    def set_attribute(self, k: str, v: Any) -> None: pass
    def record_exception(self, exc: Any) -> None: pass
    def set_status(self, *a: Any) -> None: pass


class _NoOpTracer:
    def start_as_current_span(self, name: str, **kw: Any) -> "_NoOpSpan":
        return _NoOpSpan()


# ──────────────────────────────────────────────────────────────────────
# ASGI Middleware
# ──────────────────────────────────────────────────────────────────────

class TelemetryMiddleware:
    """
    ASGI middleware that creates an OTel span per HTTP request.

    Automatically records:
      http.method, http.route, http.status_code,
      pillar.request_id (from X-Request-ID header / scope).

    Add after ``setup_telemetry()`` is called::

        app = Pillar(...)
        setup_telemetry(service_name="my-api")
        # TelemetryMiddleware is wired automatically via pillar.toml [telemetry]
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not _OTEL_AVAILABLE or _tracer is None:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path   = scope.get("path", "")
        rid    = scope.get("request_id", "")

        with _tracer.start_as_current_span(f"{method} {path}") as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.route", path)
            if rid:
                span.set_attribute("pillar.request_id", rid)

            status_code = 200

            async def _send(msg: dict) -> None:
                nonlocal status_code
                if msg["type"] == "http.response.start":
                    status_code = msg.get("status", 200)
                await send(msg)

            try:
                await self.app(scope, receive, _send)
                span.set_attribute("http.status_code", status_code)
                if status_code >= 500 and _OTEL_AVAILABLE:
                    from opentelemetry.trace import StatusCode
                    span.set_status(StatusCode.ERROR)
            except Exception as exc:
                _record_error(span, exc)
                raise
