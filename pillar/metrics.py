"""
In-memory request metrics.

Tracks per-route request counts, error counts, and response times.
Exposed at ``GET /metrics`` as JSON and in Prometheus text format at
``GET /metrics?format=prometheus``.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List


class _RouteStats:
    __slots__ = ("requests", "errors", "times")

    def __init__(self) -> None:
        self.requests: int = 0
        self.errors: int = 0
        self.times: List[float] = []   # rolling window of last 1 000 response times (ms)


class Metrics:
    """Thread-safe, in-memory metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: Dict[str, _RouteStats] = defaultdict(_RouteStats)
        self._start = time.time()

    def record(self, route: str, status_code: int, duration_ms: float) -> None:
        with self._lock:
            s = self._stats[route]
            s.requests += 1
            if status_code >= 400:
                s.errors += 1
            s.times.append(round(duration_ms, 2))
            if len(s.times) > 1_000:
                s.times = s.times[-1_000:]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            routes: Dict[str, dict] = {}
            total_req = 0
            total_err = 0

            for route, s in self._stats.items():
                total_req += s.requests
                total_err += s.errors
                times = s.times
                if times:
                    avg = sum(times) / len(times)
                    p99 = sorted(times)[int(len(times) * 0.99)]
                else:
                    avg = p99 = 0.0

                routes[route] = {
                    "requests":       s.requests,
                    "errors":         s.errors,
                    "error_rate":     round(s.errors / s.requests, 4) if s.requests else 0,
                    "avg_ms":         round(avg, 2),
                    "p99_ms":         round(p99, 2),
                }

            return {
                "uptime_seconds":  round(time.time() - self._start, 1),
                "total_requests":  total_req,
                "total_errors":    total_err,
                "routes":          routes,
            }

    def prometheus_text(self) -> str:
        """Return metrics in Prometheus exposition format."""
        snap = self.snapshot()
        lines: List[str] = []

        lines += [
            "# HELP pillar_uptime_seconds Seconds since the server started",
            "# TYPE pillar_uptime_seconds gauge",
            f"pillar_uptime_seconds {snap['uptime_seconds']}",
            "",
            "# HELP pillar_requests_total Total HTTP requests handled",
            "# TYPE pillar_requests_total counter",
        ]
        for route, data in snap["routes"].items():
            label = route.replace('"', '\\"')
            lines.append(f'pillar_requests_total{{route="{label}"}} {data["requests"]}')

        lines += [
            "",
            "# HELP pillar_errors_total Total HTTP errors (4xx/5xx)",
            "# TYPE pillar_errors_total counter",
        ]
        for route, data in snap["routes"].items():
            label = route.replace('"', '\\"')
            lines.append(f'pillar_errors_total{{route="{label}"}} {data["errors"]}')

        lines += [
            "",
            "# HELP pillar_response_time_avg_ms Average response time in ms",
            "# TYPE pillar_response_time_avg_ms gauge",
        ]
        for route, data in snap["routes"].items():
            label = route.replace('"', '\\"')
            lines.append(f'pillar_response_time_avg_ms{{route="{label}"}} {data["avg_ms"]}')

        lines.append("")
        return "\n".join(lines)


# Module-level singleton
metrics: Metrics = Metrics()
