"""
Pillar Time-Travel Request Debugger.

Records per-layer timing for every HTTP request in an in-memory ring
buffer (last 1 000 requests).  View live at /trace/{request-id} or via
the CLI::

    pillar trace list              # recent requests
    pillar trace <request-id>      # waterfall for one request
    pillar trace --open <id>       # open HTML flamegraph in browser

Spans are recorded automatically for:
  - HTTP routing (router layer)
  - Handler execution (handler layer)
  - DB queries (db layer — via pillar.db.Database)
  - DI resolution (di layer)

Add custom spans with the context manager::

    from pillar.tracer import span_context
    with span_context("payment.charge", "handler", provider="stripe"):
        charge(...)
"""
from __future__ import annotations

import contextvars
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────

def _ms() -> float:
    return time.perf_counter() * 1000


@dataclass
class Span:
    name: str
    layer: str          # "router" | "di" | "handler" | "db" | "middleware" | "queue"
    start_ms: float
    end_ms: float = 0.0
    error: Optional[str] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(self.end_ms - self.start_ms, 0.0)


@dataclass
class RequestTrace:
    trace_id: str
    method: str
    path: str
    start_ms: float
    end_ms: float = 0.0
    status_code: int = 0
    spans: List[Span] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return max(self.end_ms - self.start_ms, 0.0) if self.end_ms else 0.0


# ──────────────────────────────────────────────────────────────────────
# In-memory ring store
# ──────────────────────────────────────────────────────────────────────

class TraceStore:
    _MAX = 1_000

    def __init__(self) -> None:
        self._by_id: Dict[str, RequestTrace] = {}
        self._order: Deque[str] = deque(maxlen=self._MAX)

    def start(self, trace_id: str, method: str, path: str) -> RequestTrace:
        t = RequestTrace(trace_id=trace_id, method=method, path=path, start_ms=_ms())
        self._by_id[trace_id] = t
        self._order.append(trace_id)
        if len(self._order) == self._MAX:
            self._by_id.pop(self._order[0], None)
        return t

    def get(self, trace_id: str) -> Optional[RequestTrace]:
        return self._by_id.get(trace_id)

    def finish(self, trace_id: str, status_code: int) -> None:
        t = self._by_id.get(trace_id)
        if t:
            t.end_ms = _ms()
            t.status_code = status_code

    def add_span(self, trace_id: str, span: Span) -> None:
        t = self._by_id.get(trace_id)
        if t:
            t.spans.append(span)

    def recent(self, limit: int = 50) -> List[RequestTrace]:
        ids = list(self._order)[-limit:]
        return [self._by_id[i] for i in reversed(ids) if i in self._by_id]


_store = TraceStore()


def get_store() -> TraceStore:
    return _store


# ──────────────────────────────────────────────────────────────────────
# Per-request context
# ──────────────────────────────────────────────────────────────────────

_current_trace: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "pillar_trace_id", default=None
)


def current_trace_id() -> Optional[str]:
    return _current_trace.get()


def record_span(
    name: str,
    layer: str,
    start_ms: float,
    end_ms: float,
    error: str = None,
    **attrs: Any,
) -> None:
    """Record a completed span for the current request. No-op with no active trace."""
    tid = _current_trace.get()
    if tid:
        _store.add_span(tid, Span(
            name=name, layer=layer,
            start_ms=start_ms, end_ms=end_ms,
            error=error, attrs=attrs,
        ))


class span_context:
    """
    Context manager that records a span on __exit__.

    Usage::

        with span_context("payment.charge", "handler", provider="stripe"):
            await charge(...)
    """
    def __init__(self, name: str, layer: str = "handler", **attrs: Any) -> None:
        self._name = name
        self._layer = layer
        self._attrs = attrs
        self._t0 = 0.0

    def __enter__(self) -> "span_context":
        self._t0 = _ms()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, tb: Any) -> bool:
        record_span(
            self._name, self._layer, self._t0, _ms(),
            error=str(exc_val) if exc_val else None,
            **self._attrs,
        )
        return False


# ──────────────────────────────────────────────────────────────────────
# ASGI Middleware
# ──────────────────────────────────────────────────────────────────────

class TraceMiddleware:
    """
    ASGI middleware that opens a RequestTrace for every HTTP request.

    Must be applied AFTER RequestIDMiddleware so ``scope["request_id"]``
    is already set (used as the trace ID).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = scope.get("request_id") or str(uuid.uuid4())
        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        _store.start(trace_id, method, path)
        _current_trace.set(trace_id)

        status_code = 200

        async def send_wrapper(msg: dict) -> None:
            nonlocal status_code
            if msg["type"] == "http.response.start":
                status_code = msg.get("status", 200)
                headers = list(msg.get("headers", []))
                headers.append((b"x-pillar-trace-id", trace_id.encode()))
                msg = {**msg, "headers": headers}
            await send(msg)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _store.finish(trace_id, status_code)


# ──────────────────────────────────────────────────────────────────────
# HTML flamegraph
# ──────────────────────────────────────────────────────────────────────

_LAYER_COLOR = {
    "router":     "#6366f1",
    "di":         "#8b5cf6",
    "handler":    "#06b6d4",
    "db":         "#10b981",
    "middleware": "#f59e0b",
    "queue":      "#f97316",
}


def trace_html(trace: RequestTrace) -> str:
    total = trace.total_ms or 1.0
    sc = trace.status_code
    badge_bg = "#10b981" if sc < 400 else ("#f59e0b" if sc < 500 else "#ef4444")

    rows = ""
    for sp in trace.spans:
        offset = (sp.start_ms - trace.start_ms) / total * 100
        width  = max(sp.duration_ms / total * 100, 0.5)
        color  = _LAYER_COLOR.get(sp.layer, "#64748b")
        err    = f'<span style="color:#ef4444;margin-left:6px;font-size:11px">&#9888; {sp.error}</span>' if sp.error else ""
        attr_str = " ".join(f'{k}={v}' for k, v in sp.attrs.items())
        title  = f"{sp.name} {attr_str}".strip()
        rows += f"""
        <tr>
          <td style="padding:3px 10px;color:#94a3b8;font-size:11px;white-space:nowrap">{sp.layer}</td>
          <td style="padding:3px 10px;color:#e2e8f0;font-size:12px">{sp.name}{err}</td>
          <td style="padding:3px 10px">
            <div style="position:relative;height:14px;background:#1e293b;border-radius:3px;min-width:200px">
              <div title="{title}" style="position:absolute;left:{offset:.1f}%;width:{width:.1f}%;height:100%;background:{color};border-radius:3px;min-width:2px"></div>
            </div>
          </td>
          <td style="padding:3px 10px;color:#64748b;font-size:11px;text-align:right;white-space:nowrap">{sp.duration_ms:.2f}ms</td>
        </tr>"""

    no_spans = "<tr><td colspan='4' style='padding:24px;text-align:center;color:#475569'>No spans recorded for this path.</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Pillar Trace {trace.trace_id[:8]}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#080b10;color:#e2e8f0;padding:32px 40px}}
h1{{font-size:18px;font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:10px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;background:{badge_bg};color:#fff}}
.meta{{color:#64748b;font-size:13px;margin-bottom:24px}}
.legend{{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
.leg{{display:flex;align-items:center;gap:5px;font-size:11px;color:#94a3b8}}
.dot{{width:10px;height:10px;border-radius:2px}}
table{{width:100%;border-collapse:collapse}}
th{{padding:6px 10px;text-align:left;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;border-bottom:1px solid #1e293b;letter-spacing:.05em}}
tr:hover td{{background:#0f172a}}
.foot{{margin-top:14px;color:#475569;font-size:12px}}
a{{color:#6366f1;text-decoration:none}}a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<h1>
  <span style="color:#6366f1">&#9679;</span>
  Trace <span style="color:#6366f1;font-family:monospace">{trace.trace_id[:16]}…</span>
  <span class="badge">{sc}</span>
</h1>
<div class="meta">{trace.method} <strong>{trace.path}</strong> &nbsp;·&nbsp; {trace.total_ms:.2f}ms total &nbsp;·&nbsp; {len(trace.spans)} spans</div>
<div class="legend">
  {"".join(f'<div class="leg"><div class="dot" style="background:{c}"></div>{l}</div>' for l, c in _LAYER_COLOR.items())}
</div>
<table>
<thead>
  <tr>
    <th style="width:80px">Layer</th>
    <th>Span</th>
    <th>Timeline (0 → {total:.1f}ms)</th>
    <th style="width:90px;text-align:right">Duration</th>
  </tr>
</thead>
<tbody>
{rows if rows else no_spans}
</tbody>
</table>
<div class="foot"><a href="/trace/">&larr; All traces</a></div>
</body>
</html>"""


def trace_list_html(traces: List[RequestTrace]) -> str:
    rows = ""
    for t in traces:
        sc = t.status_code
        sc_color = "#10b981" if sc < 400 else ("#f59e0b" if sc < 500 else "#ef4444")
        rows += f"""
        <tr onclick="location.href='/trace/{t.trace_id}'" style="cursor:pointer">
          <td style="padding:6px 12px;font-family:monospace;font-size:12px;color:#6366f1">{t.trace_id[:16]}…</td>
          <td style="padding:6px 12px;color:#94a3b8;font-size:12px">{t.method}</td>
          <td style="padding:6px 12px;color:#e2e8f0;font-size:12px">{t.path}</td>
          <td style="padding:6px 12px;text-align:center"><span style="background:{sc_color};color:#fff;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700">{sc}</span></td>
          <td style="padding:6px 12px;color:#64748b;font-size:12px;text-align:right">{t.total_ms:.1f}ms</td>
          <td style="padding:6px 12px;color:#475569;font-size:11px">{len(t.spans)} spans</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Pillar Traces</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#080b10;color:#e2e8f0;padding:32px 40px}}
h1{{font-size:18px;font-weight:700;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse}}
th{{padding:6px 12px;text-align:left;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;border-bottom:1px solid #1e293b;letter-spacing:.05em}}
tr:hover td{{background:#0f172a}}
.empty{{padding:40px;text-align:center;color:#475569}}
</style>
</head>
<body>
<h1>&#9873; Recent Traces</h1>
<table>
<thead>
  <tr>
    <th>Trace ID</th><th>Method</th><th>Path</th><th>Status</th><th style="text-align:right">Total</th><th>Spans</th>
  </tr>
</thead>
<tbody>
{"<tr><td colspan='6' class='empty'>No traces yet. Make a request to see it here.</td></tr>" if not rows else rows}
</tbody>
</table>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────
# ASCII waterfall for CLI
# ──────────────────────────────────────────────────────────────────────

def trace_ascii(trace: RequestTrace, width: int = 60) -> str:
    total = trace.total_ms or 1.0
    lines = [
        f"  Trace  {trace.trace_id}",
        f"  Route  {trace.method} {trace.path}",
        f"  Status {trace.status_code}",
        f"  Total  {trace.total_ms:.2f}ms",
        "",
        f"  {'Layer':<10} {'Span':<28} {'Bar':<{width}} {'ms':>6}",
        "  " + "─" * (width + 48),
    ]
    for sp in trace.spans:
        offset = int((sp.start_ms - trace.start_ms) / total * width)
        bar_w  = max(int(sp.duration_ms / total * width), 1)
        bar    = " " * offset + "█" * bar_w
        err    = " ✗" if sp.error else ""
        lines.append(
            f"  {sp.layer:<10} {(sp.name + err):<28} {bar:<{width}} {sp.duration_ms:>5.1f}"
        )
    if not trace.spans:
        lines.append("  (no spans recorded)")
    return "\n".join(lines)
