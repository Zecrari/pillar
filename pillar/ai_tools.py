"""
Pillar AI-Native Tool-Calling Router.

Decorate any handler or function with @ai_tool to:
  1. Serve it as a normal HTTP route (if registered with a Router/Controller).
  2. Auto-generate the exact JSON schema required by OpenAI and Anthropic
     function-calling APIs.
  3. Expose the full manifest at GET /ai/manifest.json.

Usage::

    from pillar.ai_tools import ai_tool

    @ai_tool(description="Search users by name or email")
    @router.get("/users/search")
    async def search_users(q: str, limit: int = 10) -> list[UserResponse]:
        return service.search(q, limit)

    # The manifest is automatically available:
    # GET /ai/manifest.json → OpenAI "tools" format
    # GET /ai/manifest.json?format=anthropic → Anthropic "tools" format

Standalone tools (not mounted as routes) can also be registered::

    @ai_tool(description="Send a notification email")
    async def notify_user(user_id: int, message: str) -> bool: ...

LLM agents fetch /ai/manifest.json and auto-wire the entire API.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Callable, Dict, List, Optional, get_type_hints

# ──────────────────────────────────────────────────────────────────────
# Global tool registry
# ──────────────────────────────────────────────────────────────────────

_registry: List[Dict[str, Any]] = []


def _all_tools() -> List[Dict[str, Any]]:
    return list(_registry)


# ──────────────────────────────────────────────────────────────────────
# Python → JSON Schema type mapping
# ──────────────────────────────────────────────────────────────────────

def _py_to_json_schema(annotation: Any) -> dict:
    """Convert a Python type annotation to a JSON Schema fragment."""
    import typing

    origin = getattr(annotation, "__origin__", None)
    args   = getattr(annotation, "__args__", ()) or ()

    # Optional[X]
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            base = _py_to_json_schema(non_none[0])
            return {**base, "nullable": True}

    # list[X]
    if origin is list or annotation is list:
        item_schema = _py_to_json_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}

    # dict / mapping
    if origin is dict or annotation is dict:
        return {"type": "object"}

    # Pydantic model
    try:
        from pydantic import BaseModel
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            schema = annotation.model_json_schema()
            schema.pop("title", None)
            return schema
    except ImportError:
        pass

    _MAP = {
        int:   {"type": "integer"},
        float: {"type": "number"},
        bool:  {"type": "boolean"},
        str:   {"type": "string"},
        bytes: {"type": "string", "format": "binary"},
    }
    return _MAP.get(annotation, {"type": "string"})


def _build_parameters_schema(func: Callable) -> dict:
    """Build an OpenAI-compatible parameters schema from a function's signature."""
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    sig        = inspect.signature(func)
    properties: Dict[str, dict] = {}
    required:   List[str]       = []

    _SKIP = {"self", "request", "websocket", "return"}

    for name, param in sig.parameters.items():
        if name in _SKIP:
            continue
        annotation = hints.get(name)
        if annotation is None:
            continue

        # Skip DI-injected services (non-primitive class types)
        try:
            from pydantic import BaseModel as _BM
            is_pydantic = isinstance(annotation, type) and issubclass(annotation, _BM)
        except ImportError:
            is_pydantic = False

        is_primitive = annotation in (str, int, float, bool, bytes)
        is_simple_type = is_primitive or is_pydantic
        origin = getattr(annotation, "__origin__", None)
        if not is_simple_type and origin is None and isinstance(annotation, type):
            continue  # DI service — skip

        schema = _py_to_json_schema(annotation)
        doc = (inspect.getdoc(func) or "").strip()
        properties[name] = schema

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# ──────────────────────────────────────────────────────────────────────
# @ai_tool decorator
# ──────────────────────────────────────────────────────────────────────

def ai_tool(
    description: str = "",
    name: str = None,
    include_in_manifest: bool = True,
):
    """
    Mark a function as an AI tool.

    The function is left unchanged; a tool schema is added to the global
    registry and exposed at ``GET /ai/manifest.json``.

    Args:
        description:         Human-readable description for the LLM.
        name:                Override the tool name (default: function name).
        include_in_manifest: Set False to build the schema but hide it from
                             the manifest (useful for testing).
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        params    = _build_parameters_schema(func)

        schema = {
            "name":        tool_name,
            "description": description or (inspect.getdoc(func) or "").strip(),
            "parameters":  params,
        }

        # Attach to the function so routers can introspect it
        func._pillar_ai_tool = schema

        if include_in_manifest:
            _registry.append(schema)

        return func

    return decorator


# ──────────────────────────────────────────────────────────────────────
# Manifest builders
# ──────────────────────────────────────────────────────────────────────

def openai_manifest() -> dict:
    """
    Return the manifest in OpenAI "tools" format::

        {
          "tools": [
            {
              "type": "function",
              "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... }
              }
            }
          ]
        }
    """
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t["description"],
                    "parameters":  t["parameters"],
                },
            }
            for t in _registry
        ]
    }


def anthropic_manifest() -> dict:
    """
    Return the manifest in Anthropic "tools" format::

        {
          "tools": [
            {
              "name": "...",
              "description": "...",
              "input_schema": { ... }
            }
          ]
        }
    """
    return {
        "tools": [
            {
                "name":         t["name"],
                "description":  t["description"],
                "input_schema": t["parameters"],
            }
            for t in _registry
        ]
    }


def manifest(format: str = "openai") -> dict:
    """Return the manifest in the requested format (``"openai"`` or ``"anthropic"``)."""
    if format == "anthropic":
        return anthropic_manifest()
    return openai_manifest()
