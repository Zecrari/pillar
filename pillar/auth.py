"""
Pillar RBAC — Role-Based Access Control.

Decorate handlers to require specific JWT roles or fine-grained permissions.
Roles and permissions are read from the decoded JWT payload (set by
``JWTMiddleware`` into ``scope["user"]``).

Usage::

    from pillar.auth import require_role, require_permission

    # Require the caller to have the "admin" role:
    @router.get("/admin/users")
    @require_role("admin")
    async def list_all_users(service: UserService):
        return service.list_all()

    # Require any one of several roles:
    @router.delete("/invoices/{invoice_id}")
    @require_role("admin", "billing_manager")
    async def delete_invoice(invoice_id: int, service: BillingService):
        service.delete(invoice_id)

    # Fine-grained permission string (format: "resource:action"):
    @router.post("/billing/charge")
    @require_permission("billing:write")
    async def charge_card(data: ChargeRequest, service: BillingService):
        return service.charge(data)

JWT payload conventions::

    {
      "sub": "user-123",
      "roles": ["admin", "billing_manager"],       # list or single string
      "permissions": ["users:read", "billing:write"]
    }

Both decorators raise ``ForbiddenError`` (HTTP 403) on mismatch and
``UnauthorizedError`` (HTTP 401) when no user context is present.

The decorators are checked inside ``invoke_handler`` — they add lightweight
markers to the function object; no wrapper overhead on the happy path.
"""
from __future__ import annotations

from typing import Callable, Set


def require_role(*roles: str) -> Callable:
    """
    Require the authenticated user to have at least one of *roles*.

    Reads ``user["roles"]`` from the JWT payload.  Accepts both
    ``"roles": "admin"`` (string) and ``"roles": ["admin", "editor"]`` (list).
    """
    role_set: Set[str] = set(roles)

    def decorator(func: Callable) -> Callable:
        existing = getattr(func, "_pillar_require_roles", set())
        func._pillar_require_roles = existing | role_set
        return func

    return decorator


def require_permission(*permissions: str) -> Callable:
    """
    Require the authenticated user to have at least one of *permissions*.

    Reads ``user["permissions"]`` from the JWT payload.  Accepts both
    string and list formats.
    """
    perm_set: Set[str] = set(permissions)

    def decorator(func: Callable) -> Callable:
        existing = getattr(func, "_pillar_require_permissions", set())
        func._pillar_require_permissions = existing | perm_set
        return func

    return decorator


def require_all_roles(*roles: str) -> Callable:
    """Like ``require_role`` but the user must have *all* specified roles."""
    role_set: Set[str] = set(roles)

    def decorator(func: Callable) -> Callable:
        func._pillar_require_all_roles = getattr(func, "_pillar_require_all_roles", set()) | role_set
        return func

    return decorator


# ──────────────────────────────────────────────────────────────────────
# Internal check — called by invoke_handler
# ──────────────────────────────────────────────────────────────────────

def check_auth(handler: Callable, scope: dict) -> None:
    """
    Enforce RBAC markers on *handler* against ``scope["user"]``.

    Raises:
        ``UnauthorizedError`` — no user in scope (JWT middleware not active).
        ``ForbiddenError``    — user lacks the required role/permission.
    """
    from .exceptions import ForbiddenError, UnauthorizedError

    needs_roles   = getattr(handler, "_pillar_require_roles",       None)
    needs_all     = getattr(handler, "_pillar_require_all_roles",   None)
    needs_perms   = getattr(handler, "_pillar_require_permissions", None)

    if not (needs_roles or needs_all or needs_perms):
        return  # fast path — no RBAC on this handler

    user = scope.get("user")
    if not user:
        raise UnauthorizedError("Authentication required for this endpoint")

    raw_roles = user.get("roles", [])
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]
    user_roles = set(raw_roles)

    raw_perms = user.get("permissions", [])
    if isinstance(raw_perms, str):
        raw_perms = [raw_perms]
    user_perms = set(raw_perms)

    if needs_roles and not needs_roles.intersection(user_roles):
        raise ForbiddenError(
            f"Required role(s): {', '.join(sorted(needs_roles))}"
        )

    if needs_all and not needs_all.issubset(user_roles):
        missing = needs_all - user_roles
        raise ForbiddenError(
            f"Missing required role(s): {', '.join(sorted(missing))}"
        )

    if needs_perms and not needs_perms.intersection(user_perms):
        raise ForbiddenError(
            f"Required permission(s): {', '.join(sorted(needs_perms))}"
        )
