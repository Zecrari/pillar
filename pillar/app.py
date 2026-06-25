from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import PillarConfig
from .controller import Controller
from .di import DIContainer, container as _global_container
from .exceptions import PillarError
from .metrics import metrics as _metrics
from .router import Router, RouteEntry, WebSocketEntry, invoke_handler, invoke_websocket_handler

logger = logging.getLogger("pillar")

try:
    from _pillar_engine import PillarRouter as _RustRouter
    _RUST_AVAILABLE = True
except ImportError:
    _RustRouter = None
    _RUST_AVAILABLE = False


class Pillar:
    """
    Main Pillar application — the entry point for every project.

    Usage::

        app = Pillar(title="My API", version="1.0.0")
        app.include_router(users_router)
        app.include_router(billing_router)

        # pillar run main:app --reload

    Built-in endpoints (always available):
        GET /health        — liveness probe
        GET /ready         — readiness probe
        GET /docs          — Swagger UI
        GET /redoc         — ReDoc
        GET /openapi.json  — raw OpenAPI 3.1 schema
        GET /guide         — interactive HTML guide
        GET /metrics       — request metrics (JSON or ?format=prometheus)
    """

    def __init__(
        self,
        title: str = "Pillar App",
        version: str = "0.1.0",
        description: str = "",
        debug: bool = False,
        config_path: str = "pillar.toml",
        container: DIContainer = None,
    ) -> None:
        self.title = title
        self.version = version
        self.description = description
        self.debug = debug

        self._config = PillarConfig.load(config_path)
        self._config.app.title = title
        self._config.app.version = version
        self._config.app.description = description or self._config.app.description

        self._container: DIContainer = container or _global_container

        self._routers: List[Router] = []
        self._handlers: Dict[str, RouteEntry] = {}
        self._ws_handlers: Dict[str, WebSocketEntry] = {}

        self._rust_router: Optional[Any] = None
        self._worker: Optional[Any] = None

        # Cached OpenAPI spec (built lazily once all routers are included)
        self._openapi_spec: Optional[dict] = None

        # The middleware-wrapped ASGI callable (built in _build())
        self._asgi: Optional[Callable] = None

        self._ready = False

        level = logging.DEBUG if (debug or self._config.app.debug) else logging.INFO
        logging.basicConfig(level=level, format="%(levelname)s:     %(name)s - %(message)s")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def include_router(self, router: Router) -> None:
        """Register a domain Router. Call before the server starts."""
        self._routers.append(router)
        self._openapi_spec = None

    def include_controller(self, ctrl: type) -> None:
        """
        Register a Controller class.

        Pillar inspects the controller's CRUD methods (list / get / create /
        update / patch / delete) and @action-decorated methods and builds
        RouteEntry objects identical to those from a Router.
        """
        if not (isinstance(ctrl, type) and issubclass(ctrl, Controller)):
            raise TypeError(f"{ctrl!r} must be a subclass of pillar.Controller")
        synthetic = Router.__new__(Router)
        synthetic.prefix   = ctrl._pillar_prefix
        synthetic.tags     = ctrl._pillar_tags
        synthetic._routes  = list(ctrl._routes)
        synthetic._ws_routes = list(ctrl._ws_routes)
        self._routers.append(synthetic)
        self._openapi_spec = None

    # ------------------------------------------------------------------
    # ASGI interface
    # ------------------------------------------------------------------

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if self._asgi is None:
            self._build()
        await self._asgi(scope, receive, send)

    # ------------------------------------------------------------------
    # Build phase — wires everything together
    # ------------------------------------------------------------------

    def _build(self) -> None:
        from .architecture.enforcer import ArchitectureEnforcer
        from .db.database import Database
        from .middleware import apply_middleware

        # Register DB singleton
        if Database not in self._container:
            db = Database(url=self._config.database.url)
            self._container.register_instance(Database, db)

        # Architecture enforcement
        ArchitectureEnforcer().validate()

        # Wire Rust router
        if _RUST_AVAILABLE:
            self._rust_router = _RustRouter()
            engine_label = "rust"
        else:
            self._rust_router = _PythonFallbackRouter()
            engine_label = "python"
            logger.warning(
                "Rust engine not available — using pure-Python fallback router. "
                "Run `maturin develop` to compile the Rust engine."
            )

        for router in self._routers:
            for entry in router._routes:
                self._rust_router.add_route(entry.method, entry.full_path, entry.handler_id)
                self._handlers[entry.handler_id] = entry
            for ws in router._ws_routes:
                self._ws_handlers[ws.handler_id] = ws

        total_routes = self._rust_router.route_count()
        logger.info(
            "Pillar %s ready — %d route(s) [%s engine]",
            self.version, total_routes, engine_label,
        )

        # Build the core ASGI handler, then wrap with middleware
        core = self._core_asgi
        self._asgi = apply_middleware(core, self._config)

    # ------------------------------------------------------------------
    # Core ASGI handler (before middleware)
    # ------------------------------------------------------------------

    async def _core_asgi(self, scope: dict, receive: Callable, send: Callable) -> None:
        t = scope["type"]
        if t == "lifespan":
            await self._handle_lifespan(scope, receive, send)
        elif t == "http":
            await self._handle_http(scope, receive, send)
        elif t == "websocket":
            await self._handle_websocket(scope, receive, send)

    # ------------------------------------------------------------------
    # Lifespan
    # ------------------------------------------------------------------

    async def _handle_lifespan(self, scope: dict, receive: Callable, send: Callable) -> None:
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                try:
                    await self._on_startup()
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:
                    logger.critical("Startup failed: %s", exc, exc_info=True)
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
            elif msg["type"] == "lifespan.shutdown":
                await self._on_shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _on_startup(self) -> None:
        from .queue.worker import TaskWorker
        self._worker = TaskWorker(self._config)
        await self._worker.start()
        self._ready = True
        logger.info("Pillar is ready to serve requests")

    async def _on_shutdown(self) -> None:
        self._ready = False
        logger.info("Pillar shutting down — draining queue …")
        if self._worker:
            await self._worker.stop()
        logger.info("Pillar shutdown complete")

    # ------------------------------------------------------------------
    # HTTP request dispatch
    # ------------------------------------------------------------------

    async def _handle_http(self, scope: dict, receive: Callable, send: Callable) -> None:
        method: str = scope["method"]
        path: str   = scope["path"]
        t0 = time.perf_counter()

        response = await self._route_http(method, path, scope, receive)

        duration_ms = (time.perf_counter() - t0) * 1000
        route_key = f"{method} {path}"
        _metrics.record(route_key, response.status_code, duration_ms)

        await response(scope, receive, send)

    async def _route_http(
        self, method: str, path: str, scope: dict, receive: Callable
    ) -> Response:
        cfg = self._config

        # ── Built-in endpoints ──────────────────────────────────────
        if method == "GET":
            if path == "/health":
                return self._health_response()
            if path == "/ready":
                return self._ready_response()
            if path == "/metrics":
                return await self._metrics_response(scope)
            if path == "/dashboard":
                return HTMLResponse(self._dashboard_html())
            if path == "/queue/status":
                return self._queue_status_response()
            if path in ("/trace", "/trace/"):
                return self._trace_list_response(scope)
            if path.startswith("/trace/") and len(path) > 7:
                return self._trace_detail_response(path[7:], scope)
            if cfg.docs.enabled:
                if path == cfg.docs.openapi_url:
                    return self._openapi_json_response()
                if path == cfg.docs.swagger_url:
                    return HTMLResponse(
                        __import__("pillar.openapi", fromlist=["swagger_ui_html"])
                        .swagger_ui_html(self.title, cfg.docs.openapi_url)
                    )
                if path == cfg.docs.redoc_url:
                    return HTMLResponse(
                        __import__("pillar.openapi", fromlist=["redoc_html"])
                        .redoc_html(self.title, cfg.docs.openapi_url)
                    )
                if path == cfg.docs.guide_url:
                    return HTMLResponse(self._guide_html())

        # ── User-defined routes ─────────────────────────────────────
        match = self._rust_router.match_route(method, path)
        if match is None:
            return JSONResponse(
                {"detail": f"Route '{method} {path}' not found"},
                status_code=404,
            )

        handler_id: str = match["handler_id"]
        path_params: Dict[str, str] = dict(match["params"])

        entry = self._handlers.get(handler_id)
        if entry is None:
            return JSONResponse({"detail": "Handler not registered"}, status_code=500)

        scope = {**scope, "path_params": path_params}
        request = Request(scope, receive)

        try:
            return await invoke_handler(
                entry.handler, request, path_params,
                self._container, entry.status_code,
            )
        except PillarError as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception as exc:
            if self.debug or cfg.app.debug:
                import traceback
                return JSONResponse(
                    {"detail": str(exc), "traceback": traceback.format_exc()},
                    status_code=500,
                )
            logger.exception("Unhandled exception in handler %s", handler_id)
            return JSONResponse({"detail": "Internal server error"}, status_code=500)

    # ------------------------------------------------------------------
    # WebSocket dispatch
    # ------------------------------------------------------------------

    async def _handle_websocket(self, scope: dict, receive: Callable, send: Callable) -> None:
        path: str = scope["path"]

        # Find a matching WS handler
        ws_entry: Optional[WebSocketEntry] = None
        path_params: Dict[str, str] = {}

        for entry in self._ws_handlers.values():
            import re
            pattern = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", entry.full_path) + "$"
            m = re.match(pattern, path)
            if m:
                ws_entry = entry
                path_params = m.groupdict()
                break

        websocket = WebSocket(scope, receive=receive, send=send)

        if ws_entry is None:
            await websocket.close(code=1008)
            return

        try:
            await invoke_websocket_handler(
                ws_entry.handler, websocket, path_params, self._container
            )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception("WebSocket error on %s", path)
            try:
                await websocket.close(code=1011)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Built-in response builders
    # ------------------------------------------------------------------

    def _health_response(self) -> JSONResponse:
        try:
            from _pillar_engine import engine_version
            eng = engine_version()
        except ImportError:
            eng = "python-fallback"

        return JSONResponse({
            "status":  "healthy",
            "title":   self.title,
            "version": self.version,
            "engine":  eng,
        })

    def _ready_response(self) -> JSONResponse:
        if self._ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "starting"}, status_code=503)

    async def _metrics_response(self, scope: dict) -> Response:
        query = {}
        qs = scope.get("query_string", b"").decode()
        if qs:
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    query[k] = v
                else:
                    query[part] = ""

        if query.get("format") == "prometheus":
            return PlainTextResponse(
                _metrics.prometheus_text(),
                media_type="text/plain; version=0.0.4",
            )
        return JSONResponse(_metrics.snapshot())

    def _openapi_json_response(self) -> JSONResponse:
        if self._openapi_spec is None:
            self._openapi_spec = self._build_openapi()
        return JSONResponse(self._openapi_spec)

    def _build_openapi(self) -> dict:
        from .openapi import build_openapi
        all_routes = [e for e in self._handlers.values()]
        all_ws = list(self._ws_handlers.values())
        cfg = self._config
        return build_openapi(
            title=cfg.docs.title or self.title,
            version=self.version,
            description=cfg.docs.description or cfg.app.description,
            routes=all_routes,
            ws_routes=all_ws,
        )

    def _dashboard_html(self) -> str:
        from .dashboard import dashboard_html
        try:
            from _pillar_engine import engine_version
            eng = engine_version()
        except ImportError:
            eng = "python-fallback"
        cfg = self._config
        return dashboard_html(
            title=self.title,
            version=self.version,
            routes=list(self._handlers.values()),
            ws_routes=list(self._ws_handlers.values()),
            engine=eng,
            docs_url=cfg.docs.swagger_url,
            redoc_url=cfg.docs.redoc_url,
            openapi_url=cfg.docs.openapi_url,
            guide_url=cfg.docs.guide_url,
        )

    def _queue_status_response(self) -> JSONResponse:
        from .dashboard import queue_status
        return JSONResponse(queue_status())

    def _trace_list_response(self, scope: dict = None) -> Response:
        from .tracer import get_store, trace_list_html
        traces = get_store().recent(limit=50)
        if scope and self._wants_json(scope):
            import dataclasses
            return JSONResponse([
                {**dataclasses.asdict(t), "total_ms": t.total_ms}
                for t in traces
            ])
        return HTMLResponse(trace_list_html(traces))

    def _trace_detail_response(self, trace_id: str, scope: dict = None) -> Response:
        from .tracer import get_store, trace_html
        trace = get_store().get(trace_id)
        if trace is None:
            return JSONResponse({"detail": f"Trace '{trace_id}' not found"}, status_code=404)
        if scope and self._wants_json(scope):
            import dataclasses
            return JSONResponse({**dataclasses.asdict(trace), "total_ms": trace.total_ms})
        return HTMLResponse(trace_html(trace))

    @staticmethod
    def _wants_json(scope: dict) -> bool:
        qs = scope.get("query_string", b"").decode()
        if "format=json" in qs:
            return True
        for name, value in scope.get("headers", []):
            if name.lower() == b"accept" and b"application/json" in value:
                return True
        return False

    def _guide_html(self) -> str:
        from .openapi import guide_html
        try:
            from _pillar_engine import engine_version
            eng = engine_version()
        except ImportError:
            eng = "python-fallback"

        cfg = self._config
        return guide_html(
            title=self.title,
            version=self.version,
            routes=list(self._handlers.values()),
            ws_routes=list(self._ws_handlers.values()),
            engine=eng,
            docs_url=cfg.docs.swagger_url,
            redoc_url=cfg.docs.redoc_url,
            openapi_url=cfg.docs.openapi_url,
        )


# ──────────────────────────────────────────────────────────────────────
# Pure-Python fallback router (when Rust engine not compiled)
# ──────────────────────────────────────────────────────────────────────

class _PythonFallbackRouter:
    def __init__(self) -> None:
        self._routes: Dict[str, list] = {}
        self._count = 0

    def add_route(self, method: str, path: str, handler_id: str) -> None:
        self._routes.setdefault(method.upper(), []).append((path, handler_id))
        self._count += 1

    def match_route(self, method: str, path: str) -> Optional[dict]:
        import re
        for pattern, handler_id in self._routes.get(method.upper(), []):
            regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
            m = re.match(regex, path)
            if m:
                return {"handler_id": handler_id, "params": m.groupdict()}
        return None

    def route_count(self) -> int:
        return self._count
