"""
Pillar Controller — resource-oriented class-based routing.

Instead of scattering @router.get / @router.post decorators, define
a Controller class whose methods map directly to HTTP verbs:

    class UserController(Controller):
        prefix = "/users"
        tags   = ["Users"]

        async def list(self, service: UserService):
            return service.list_users()                    # GET  /users/

        async def get(self, user_id: int, service: UserService):
            return service.get_user(user_id)              # GET  /users/{user_id}

        async def create(self, data: UserCreate, service: UserService):
            return service.create_user(data)              # POST /users/

        async def update(self, user_id: int, data: UserUpdate, service: UserService):
            return service.update_user(user_id, data)     # PUT  /users/{user_id}

        async def delete(self, user_id: int, service: UserService):
            return service.delete_user(user_id)           # DELETE /users/{user_id}

        @action.get("/{user_id}/activate")
        async def activate(self, user_id: int, service: UserService):
            return service.activate(user_id)              # GET /users/{user_id}/activate

    app.include_controller(UserController)
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Dict, List, Optional, Type, get_type_hints

from .router import RouteEntry, WebSocketEntry


# ──────────────────────────────────────────────────────────────────────
# Convention table: method name → (HTTP verb, path suffix)
# None suffix = auto-detect from first primitive parameter
# ──────────────────────────────────────────────────────────────────────

_CRUD: Dict[str, tuple] = {
    "list":   ("GET",    "/"),
    "get":    ("GET",    None),
    "create": ("POST",   "/"),
    "update": ("PUT",    None),
    "patch":  ("PATCH",  None),
    "delete": ("DELETE", None),
}

_DEFAULT_STATUS: Dict[str, int] = {
    "create": 201,
    "delete": 204,
}

_PRIMITIVES = (str, int, float, bool, bytes)


# ──────────────────────────────────────────────────────────────────────
# @action decorator
# ──────────────────────────────────────────────────────────────────────

class _ActionDecorator:
    """
    Mark a controller method as a custom route.

    Usage::

        @action("GET", "/{user_id}/avatar")
        async def avatar(self, user_id: int, service: UserService): ...

        # Shorthand helpers:
        @action.get("/{user_id}/avatar")
        @action.post("/{user_id}/follow")
        @action.delete("/{user_id}/follow")
    """

    def __call__(self, method: str, path: str, **kwargs) -> Callable:
        def decorator(func: Callable) -> Callable:
            func._pillar_action = {"method": method.upper(), "path": path, **kwargs}
            return func
        return decorator

    def get(self, path: str, **kw) -> Callable:    return self("GET",    path, **kw)
    def post(self, path: str, **kw) -> Callable:   return self("POST",   path, **kw)
    def put(self, path: str, **kw) -> Callable:    return self("PUT",    path, **kw)
    def patch(self, path: str, **kw) -> Callable:  return self("PATCH",  path, **kw)
    def delete(self, path: str, **kw) -> Callable: return self("DELETE", path, **kw)


action = _ActionDecorator()


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _detect_id_param(func: Callable) -> str:
    """Return the first non-self primitive parameter name (used as the URL id)."""
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}
    for name, param in inspect.signature(func).parameters.items():
        if name == "self":
            continue
        ann = hints.get(name, param.annotation)
        if ann in _PRIMITIVES or ann is inspect.Parameter.empty:
            return name
    return "id"


def _build_handler(cls: Type, method: Callable) -> Callable:
    """
    Wrap an unbound controller method into a free async function that
    ``invoke_handler`` can introspect normally.

    The wrapper:
      - Has the same signature as *method* but without ``self``
      - Has pre-resolved type annotations (handles ``from __future__ import annotations``)
      - Instantiates the controller and forwards all kwargs on each call
    """
    is_coro = asyncio.iscoroutinefunction(method)

    async def _handler(**kwargs: Any) -> Any:
        instance = cls()
        if is_coro:
            return await method(instance, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: method(instance, **kwargs))

    _handler.__name__     = method.__name__
    _handler.__qualname__ = f"{cls.__qualname__}.{method.__name__}"
    _handler.__module__   = method.__module__

    # Resolve annotations in the source module's context so that
    # `from __future__ import annotations` (PEP 563) strings become real types.
    try:
        hints = get_type_hints(method)
        hints.pop("self",   None)
        hints.pop("return", None)
        _handler.__annotations__ = hints
    except Exception:
        _handler.__annotations__ = {}

    # Build a Signature without 'self', using the resolved annotations.
    try:
        orig_sig = inspect.signature(method)
        resolved = _handler.__annotations__
        new_params = []
        for name, p in orig_sig.parameters.items():
            if name == "self":
                continue
            if name in resolved:
                p = p.replace(annotation=resolved[name])
            new_params.append(p)
        _handler.__signature__ = orig_sig.replace(parameters=new_params)
    except Exception:
        pass

    return _handler


# ──────────────────────────────────────────────────────────────────────
# Route collection
# ──────────────────────────────────────────────────────────────────────

def _collect_routes(cls: Type) -> None:
    prefix = cls._pillar_prefix
    tags   = cls._pillar_tags

    for attr_name in list(vars(cls)):          # vars() → only this class, not bases
        if attr_name.startswith("_"):
            continue
        func = vars(cls).get(attr_name)
        if func is None or not callable(func):
            continue
        if not inspect.isfunction(func):
            continue

        handler_id = f"{cls.__module__}.{cls.__qualname__}.{attr_name}"
        handler    = _build_handler(cls, func)

        # ── Custom @action ──────────────────────────────────────────
        if hasattr(func, "_pillar_action"):
            meta = func._pillar_action
            full_path = prefix + meta["path"]
            extra_keys = ("status_code", "summary", "description",
                          "response_model", "deprecated", "include_in_schema")
            extra = {k: meta[k] for k in extra_keys if k in meta}
            cls._routes.append(RouteEntry(
                method=meta["method"],
                path=meta["path"],
                full_path=full_path,
                handler=handler,
                handler_id=handler_id,
                tags=list(tags),
                **extra,
            ))
            continue

        # ── CRUD convention ─────────────────────────────────────────
        if attr_name not in _CRUD:
            continue

        verb, path_suffix = _CRUD[attr_name]
        if path_suffix is None:
            id_param    = _detect_id_param(func)
            path_suffix = f"/{{{id_param}}}"

        cls._routes.append(RouteEntry(
            method=verb,
            path=path_suffix,
            full_path=prefix + path_suffix,
            handler=handler,
            handler_id=handler_id,
            tags=list(tags),
            status_code=_DEFAULT_STATUS.get(attr_name, 200),
        ))


# ──────────────────────────────────────────────────────────────────────
# Metaclass
# ──────────────────────────────────────────────────────────────────────

class _ControllerMeta(type):
    def __new__(mcs, name, bases, namespace, prefix: str = "", tags: list = None, **kw):
        cls = super().__new__(mcs, name, bases, namespace, **kw)
        # Class-level attributes override keyword args
        cls._pillar_prefix = (prefix or getattr(cls, "prefix", "") or "").rstrip("/")
        cls._pillar_tags   = tags or getattr(cls, "tags", []) or []
        cls._routes:    List[RouteEntry]     = []
        cls._ws_routes: List[WebSocketEntry] = []

        if name != "Controller":
            _collect_routes(cls)

        return cls


# ──────────────────────────────────────────────────────────────────────
# Public base class
# ──────────────────────────────────────────────────────────────────────

class Controller(metaclass=_ControllerMeta):
    """
    Resource-oriented controller.

    Convention::

        Method name   HTTP verb   Path
        -----------   ---------   -----------------------
        list          GET         /prefix/
        get           GET         /prefix/{id_param}
        create        POST        /prefix/
        update        PUT         /prefix/{id_param}
        patch         PATCH       /prefix/{id_param}
        delete        DELETE      /prefix/{id_param}

    The id parameter is auto-detected from the first primitive-typed
    argument in the method signature (e.g. ``user_id: int`` → ``/{user_id}``).

    Custom endpoints use ``@action``::

        @action.get("/{user_id}/activate")
        async def activate(self, user_id: int, svc: UserService): ...

    Register::

        app.include_controller(UserController)
    """

    prefix: str  = ""
    tags:   list = []
