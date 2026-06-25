from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Type, get_type_hints

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket


def _path_param_names(path: str) -> Set[str]:
    return set(re.findall(r"\{(\w+)\}", path))


def _is_pydantic(cls: Any) -> bool:
    try:
        from pydantic import BaseModel
        return isinstance(cls, type) and issubclass(cls, BaseModel)
    except ImportError:
        return False


@dataclass
class RouteEntry:
    method: str
    path: str            # relative (without prefix)
    full_path: str       # with prefix
    handler: Callable
    handler_id: str      # unique key used by the Rust router
    # Docs / OpenAPI metadata
    status_code: int = 200
    summary: Optional[str] = None
    description: Optional[str] = None
    response_model: Optional[Type] = None
    tags: List[str] = field(default_factory=list)
    deprecated: bool = False
    include_in_schema: bool = True


@dataclass
class WebSocketEntry:
    path: str
    full_path: str
    handler: Callable
    handler_id: str
    summary: Optional[str] = None
    tags: List[str] = field(default_factory=list)


class Router:
    """
    Pillar route collector.

    Decorate handlers with @router.get / @router.post / etc.
    All collected routes are registered with the Rust PillarRouter
    when ``app.include_router(router)`` is called.

    Auto-injection rules (in precedence order):
      1. Parameter name matches a ``{path_param}`` → coerced to annotated type
      2. Annotation is a Pydantic BaseModel subclass → parsed from JSON body
      3. Annotation is a class (not primitive) → resolved from DI container
      4. Everything else → query parameter (with optional default)
      5. ``Request`` annotation → raw Starlette request object
      6. ``WebSocket`` annotation → raw Starlette websocket object
    """

    def __init__(self, prefix: str = "", tags: List[str] = None) -> None:
        self.prefix = prefix.rstrip("/")
        self.tags: List[str] = tags or []
        self._routes: List[RouteEntry] = []
        self._ws_routes: List[WebSocketEntry] = []

    # ------------------------------------------------------------------
    # HTTP method decorators
    # ------------------------------------------------------------------

    def get(self, path: str, *, status_code: int = 200,
            summary: str = None, description: str = None,
            response_model: Type = None, deprecated: bool = False,
            include_in_schema: bool = True) -> Callable:
        return self._decorator("GET", path, status_code=status_code,
                               summary=summary, description=description,
                               response_model=response_model,
                               deprecated=deprecated,
                               include_in_schema=include_in_schema)

    def post(self, path: str, *, status_code: int = 201,
             summary: str = None, description: str = None,
             response_model: Type = None, deprecated: bool = False,
             include_in_schema: bool = True) -> Callable:
        return self._decorator("POST", path, status_code=status_code,
                               summary=summary, description=description,
                               response_model=response_model,
                               deprecated=deprecated,
                               include_in_schema=include_in_schema)

    def put(self, path: str, *, status_code: int = 200,
            summary: str = None, description: str = None,
            response_model: Type = None, deprecated: bool = False,
            include_in_schema: bool = True) -> Callable:
        return self._decorator("PUT", path, status_code=status_code,
                               summary=summary, description=description,
                               response_model=response_model,
                               deprecated=deprecated,
                               include_in_schema=include_in_schema)

    def patch(self, path: str, *, status_code: int = 200,
              summary: str = None, description: str = None,
              response_model: Type = None, deprecated: bool = False,
              include_in_schema: bool = True) -> Callable:
        return self._decorator("PATCH", path, status_code=status_code,
                               summary=summary, description=description,
                               response_model=response_model,
                               deprecated=deprecated,
                               include_in_schema=include_in_schema)

    def delete(self, path: str, *, status_code: int = 200,
               summary: str = None, description: str = None,
               response_model: Type = None, deprecated: bool = False,
               include_in_schema: bool = True) -> Callable:
        return self._decorator("DELETE", path, status_code=status_code,
                               summary=summary, description=description,
                               response_model=response_model,
                               deprecated=deprecated,
                               include_in_schema=include_in_schema)

    def head(self, path: str, *, include_in_schema: bool = True) -> Callable:
        return self._decorator("HEAD", path, include_in_schema=include_in_schema)

    def options(self, path: str, *, include_in_schema: bool = True) -> Callable:
        return self._decorator("OPTIONS", path, include_in_schema=include_in_schema)

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def websocket(self, path: str, *, summary: str = None,
                  tags: List[str] = None) -> Callable:
        def register(func: Callable) -> Callable:
            full_path = self.prefix + path
            handler_id = f"{func.__module__}.{func.__qualname__}"
            self._ws_routes.append(WebSocketEntry(
                path=path,
                full_path=full_path,
                handler=func,
                handler_id=handler_id,
                summary=summary,
                tags=(tags or []) + self.tags,
            ))
            return func
        return register

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _decorator(self, method: str, path: str, **meta) -> Callable:
        def register(func: Callable) -> Callable:
            full_path = self.prefix + path
            handler_id = f"{func.__module__}.{func.__qualname__}"
            route_tags = meta.pop("tags", []) + self.tags

            # Auto-fill summary from docstring if not provided
            summary = meta.get("summary") or None
            description = meta.get("description") or inspect.getdoc(func)

            self._routes.append(RouteEntry(
                method=method,
                path=path,
                full_path=full_path,
                handler=func,
                handler_id=handler_id,
                tags=route_tags,
                summary=summary,
                description=description,
                **{k: v for k, v in meta.items()
                   if k not in ("summary", "description", "tags")},
            ))
            return func
        return register


# ──────────────────────────────────────────────────────────────────────
# Handler invocation — The Smart Bridge lives here
# ──────────────────────────────────────────────────────────────────────

async def invoke_handler(
    handler: Callable,
    request: Request,
    path_params: Dict[str, str],
    container: Any,
    expected_status: int = 200,
) -> Response:
    """
    Build kwargs for *handler* and call it.

    Smart Bridge: sync handlers run in the thread-pool executor so the
    event loop is never blocked.
    """
    try:
        hints = get_type_hints(handler)
    except Exception:
        hints = {}

    sig = inspect.signature(handler)
    kwargs: Dict[str, Any] = {}

    body: Optional[dict] = None
    body_read = False

    for name, param in sig.parameters.items():
        annotation = hints.get(name, inspect.Parameter.empty)

        if annotation is inspect.Parameter.empty:
            if name in path_params:
                kwargs[name] = path_params[name]
            elif name in request.query_params:
                kwargs[name] = request.query_params[name]
            continue

        # Raw objects
        if annotation is Request:
            kwargs[name] = request
            continue
        if annotation is WebSocket:
            continue  # WebSocket injected separately

        # Path parameter
        if name in path_params:
            raw = path_params[name]
            try:
                kwargs[name] = annotation(raw) if annotation not in (str, Any) else raw
            except (ValueError, TypeError):
                kwargs[name] = raw
            continue

        # Pydantic request body
        if _is_pydantic(annotation):
            if not body_read:
                body = await request.json()
                body_read = True
            kwargs[name] = annotation(**(body or {}))
            continue

        # DI-resolved service / repository
        if isinstance(annotation, type) and annotation not in (str, int, float, bool, bytes):
            try:
                kwargs[name] = container.resolve(annotation)
                continue
            except Exception as exc:
                if param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default
                    continue
                raise TypeError(
                    f"DI could not resolve '{name}: {annotation.__name__}' "
                    f"for handler '{handler.__name__}': {exc}"
                ) from exc

        # Query parameter (primitive)
        raw = request.query_params.get(name)
        if raw is not None:
            try:
                kwargs[name] = annotation(raw)
            except (ValueError, TypeError):
                kwargs[name] = raw
        elif param.default is not inspect.Parameter.empty:
            kwargs[name] = param.default

    # Smart Bridge
    if inspect.iscoroutinefunction(handler):
        result = await handler(**kwargs)
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: handler(**kwargs))

    return _serialize(result, expected_status)


async def invoke_websocket_handler(
    handler: Callable,
    websocket: WebSocket,
    path_params: Dict[str, str],
    container: Any,
) -> None:
    try:
        hints = get_type_hints(handler)
    except Exception:
        hints = {}

    sig = inspect.signature(handler)
    kwargs: Dict[str, Any] = {}

    for name, param in sig.parameters.items():
        annotation = hints.get(name, inspect.Parameter.empty)
        if annotation is WebSocket or name == "websocket":
            kwargs[name] = websocket
        elif name in path_params:
            raw = path_params[name]
            try:
                kwargs[name] = annotation(raw) if annotation not in (inspect.Parameter.empty, str, Any) else raw
            except Exception:
                kwargs[name] = raw
        elif isinstance(annotation, type) and annotation not in (str, int, float, bool, bytes, inspect.Parameter.empty):
            try:
                kwargs[name] = container.resolve(annotation)
            except Exception:
                pass

    if inspect.iscoroutinefunction(handler):
        await handler(**kwargs)
    else:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: handler(**kwargs))


def _serialize(result: Any, status_code: int = 200) -> Response:
    if isinstance(result, Response):
        return result
    if result is None:
        return Response(status_code=204)
    try:
        from pydantic import BaseModel
        if isinstance(result, BaseModel):
            return JSONResponse(result.model_dump(), status_code=status_code)
    except ImportError:
        pass
    if isinstance(result, (dict, list, int, float, str, bool)):
        return JSONResponse(result, status_code=status_code)
    try:
        return JSONResponse(dict(result), status_code=status_code)
    except Exception:
        return JSONResponse(str(result), status_code=status_code)
