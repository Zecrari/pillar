"""
Standard response helpers.

Provides:
  - ``ok()``            — 200 JSON envelope
  - ``created()``       — 201 with Location header
  - ``paginate()``      — paginated list envelope
  - ``problem()``       — RFC 7807 Problem Details (machine-readable errors)
  - ``no_content()``    — 204 empty response
  - ``PaginatedResponse`` — Pydantic model for paginated results
"""
from __future__ import annotations

from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

T = TypeVar("T")


# ──────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated result envelope."""
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int
    has_next: bool
    has_prev: bool


class SuccessEnvelope(BaseModel):
    """Standard success envelope."""
    success: bool = True
    data: Any = None
    message: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
# Factory functions
# ──────────────────────────────────────────────────────────────────────

def ok(data: Any = None, message: str = None) -> JSONResponse:
    """Return a 200 response with a standard ``{success, data, message}`` body."""
    body: dict = {"success": True}
    if data is not None:
        body["data"] = data
    if message:
        body["message"] = message
    return JSONResponse(body, status_code=200)


def created(data: Any = None, location: str = None) -> JSONResponse:
    """Return a 201 response, optionally with a ``Location`` header."""
    body: dict = {"success": True}
    if data is not None:
        body["data"] = data
    headers = {}
    if location:
        headers["Location"] = location
    return JSONResponse(body, status_code=201, headers=headers or None)


def no_content() -> Response:
    """Return a 204 No Content response."""
    return Response(status_code=204)


def paginate(
    items: List[Any],
    total: int,
    page: int = 1,
    page_size: int = 20,
) -> JSONResponse:
    """
    Return a standard paginated response envelope.

    Example::

        return paginate(users, total=150, page=2, page_size=20)
        # → {"items": [...], "total": 150, "page": 2, "page_size": 20,
        #    "pages": 8, "has_next": true, "has_prev": true}
    """
    pages = max(1, (total + page_size - 1) // page_size)
    body = {
        "items":     items,
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     pages,
        "has_next":  page < pages,
        "has_prev":  page > 1,
    }
    return JSONResponse(body)


def problem(
    title: str,
    detail: str,
    status: int = 400,
    type_uri: str = "about:blank",
    instance: str = None,
    **extra: Any,
) -> JSONResponse:
    """
    Return an RFC 7807 Problem Details response.

    Content-Type: application/problem+json

    Example::

        return problem(
            title="Validation Failed",
            detail="The 'email' field is required.",
            status=422,
            invalid_field="email",
        )
    """
    body: dict = {
        "type":   type_uri,
        "title":  title,
        "status": status,
        "detail": detail,
    }
    if instance:
        body["instance"] = instance
    body.update(extra)

    return JSONResponse(
        body,
        status_code=status,
        headers={"Content-Type": "application/problem+json"},
    )
