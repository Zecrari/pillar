"""
Pillar Auto Row-Level Security.

Wraps ``Database`` to transparently inject tenant isolation into every
SELECT, UPDATE, and DELETE query, and to stamp tenant_id on every INSERT.

The tenant ID is stored in a ``contextvars.ContextVar`` so it is naturally
scoped per asyncio task — concurrent requests never bleed across.

``JWTMiddleware`` sets the context automatically when ``rls_tenant_claim``
is configured.  You can also set it manually::

    from pillar.db.rls import set_tenant, clear_tenant
    set_tenant("acme-corp")
    ...
    clear_tenant()

Usage::

    # main.py
    from pillar.db.rls import RLSDatabase
    from pillar.db import Database
    from pillar.di import container

    db  = Database("sqlite:///./app.db")
    rls = RLSDatabase(db)                    # default column: tenant_id
    container.register_instance(Database, rls)

    # Your repositories use Database as normal — RLS is invisible:
    rows = db.query_all("SELECT * FROM orders")
    # → SELECT * FROM orders WHERE tenant_id = ?   (injected)
"""
from __future__ import annotations

import contextvars
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# ──────────────────────────────────────────────────────────────────────
# Per-request tenant context (asyncio-safe)
# ──────────────────────────────────────────────────────────────────────

_tenant_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "pillar_rls_tenant", default=None
)


def set_tenant(tenant_id: Optional[str]) -> None:
    """Set current request's tenant. Called by JWTMiddleware per request."""
    _tenant_ctx.set(tenant_id)


def get_tenant() -> Optional[str]:
    """Return the active tenant_id for this request, or None."""
    return _tenant_ctx.get()


def clear_tenant() -> None:
    """Clear the tenant context (called at request teardown)."""
    _tenant_ctx.set(None)


# ──────────────────────────────────────────────────────────────────────
# Depth-aware SQL filter injection
# ──────────────────────────────────────────────────────────────────────

def _inject_filter(
    sql: str,
    params: tuple,
    col: str,
    tenant: str,
) -> Tuple[str, tuple]:
    """
    Inject ``WHERE {col} = ?`` (or ``AND {col} = ?``) into a SQL statement
    at the outermost query level (depth 0), subquery-safe.

    Supported:  SELECT, UPDATE, DELETE (and CTEs via WITH).
    Ignored:    INSERT (handled separately), DDL, PRAGMA.
    """
    s = sql.strip()
    su = s.upper()

    is_dml = (
        su.startswith("SELECT") or su.startswith("WITH") or
        su.startswith("UPDATE") or su.startswith("DELETE")
    )
    if not is_dml:
        return sql, params

    depth       = 0
    where_pos   = -1
    cutoff      = len(s)
    i           = 0
    is_select   = su.startswith("SELECT") or su.startswith("WITH")

    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            tail = s[i : i + 12].upper()

            # Detect top-level WHERE (not part of an identifier)
            if where_pos == -1 and tail[:5] == "WHERE":
                nxt = s[i + 5] if i + 5 < len(s) else " "
                if not (nxt.isalnum() or nxt == "_"):
                    where_pos = i

            # For SELECT/CTE, stop before aggregate/sort clauses
            if is_select:
                for kw in ("ORDER BY ", "GROUP BY ", "HAVING ", "LIMIT "):
                    if tail[: len(kw)] == kw:
                        cutoff = i
                        i = len(s)  # exit; the i += 1 below makes len+1 but loop ends
                        break
        i += 1

    new_params = params + (tenant,)
    suffix = (" " + s[cutoff:].lstrip()) if cutoff < len(s) else ""

    if where_pos != -1:
        new_sql = s[:cutoff].rstrip() + f" AND {col} = ?" + suffix
    else:
        new_sql = s[:cutoff].rstrip() + f" WHERE {col} = ?" + suffix

    return new_sql, new_params


def _inject_insert_tenant(
    sql: str,
    params: tuple,
    col: str,
    tenant: str,
) -> Tuple[str, tuple]:
    """
    Add ``{col}`` to the column list and ``?`` to the VALUES clause of an
    INSERT statement.  No-op if the column is already present.

    Supports: INSERT INTO t (a, b) VALUES (?, ?)
    Does not support: INSERT INTO t SELECT ...
    """
    if col.lower() in sql.lower():
        return sql, params

    m = re.match(
        r"(INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s*)\(([^)]+)\)\s*(VALUES\s*)\(([^)]+)\)(.*)",
        sql.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return sql, params

    prefix, cols, values_kw, vals, suffix = m.groups()
    new_sql = f"{prefix}({cols.strip()}, {col}) {values_kw.strip()}({vals.strip()}, ?){suffix}"
    return new_sql, params + (tenant,)


# ──────────────────────────────────────────────────────────────────────
# RLSDatabase wrapper
# ──────────────────────────────────────────────────────────────────────

class RLSDatabase:
    """
    Proxy around ``pillar.db.Database`` that auto-injects tenant isolation
    on every query when a tenant is active in the current request context.

    Tables that lack the tenant column are queried as-is — safe to use
    with lookup/reference tables shared across all tenants.

    The column cache is built lazily from ``sqlite_master`` the first time
    a tenant context is active.  Call ``invalidate_cache()`` after DDL.
    """

    def __init__(self, db: Any, tenant_column: str = "tenant_id") -> None:
        self._db = db
        self._col = tenant_column
        self._rls_tables: Optional[Set[str]] = None

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _tables_with_tenant_col(self) -> Set[str]:
        if self._rls_tables is None:
            self._rls_tables = self._scan_rls_tables()
        return self._rls_tables

    def _scan_rls_tables(self) -> Set[str]:
        out: Set[str] = set()
        try:
            rows = self._db.query_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            for row in rows:
                tname = row.get("name") if isinstance(row, dict) else row[0]
                try:
                    cols = self._db.query_all(f"PRAGMA table_info({tname})")
                    names = {
                        (c.get("name") if isinstance(c, dict) else c[1])
                        for c in cols
                    }
                    if self._col in names:
                        out.add(tname.lower())
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def invalidate_cache(self) -> None:
        """Force re-scan of the schema on next query (call after migrations)."""
        self._rls_tables = None

    def _should_inject(self, sql: str) -> bool:
        tables = self._tables_with_tenant_col()
        if not tables:
            return False
        su = sql.upper()
        return any(t in su for t in (t.upper() for t in tables))

    # ------------------------------------------------------------------
    # Public query API (mirrors pillar.db.Database)
    # ------------------------------------------------------------------

    def query(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        tenant = _tenant_ctx.get()
        if tenant and self._should_inject(sql):
            sql, params = _inject_filter(sql, params, self._col, tenant)
        return self._db.query(sql, params)

    def query_all(self, sql: str, params: tuple = ()) -> List[Dict]:
        tenant = _tenant_ctx.get()
        if tenant and self._should_inject(sql):
            sql, params = _inject_filter(sql, params, self._col, tenant)
        return self._db.query_all(sql, params)

    def execute(self, sql: str, params: tuple = ()) -> Any:
        tenant = _tenant_ctx.get()
        if tenant and self._should_inject(sql):
            su = sql.strip().upper()
            if su.startswith("INSERT"):
                sql, params = _inject_insert_tenant(sql, params, self._col, tenant)
            else:
                sql, params = _inject_filter(sql, params, self._col, tenant)
        return self._db.execute(sql, params)

    def execute_returning(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        tenant = _tenant_ctx.get()
        if tenant and self._should_inject(sql):
            su = sql.strip().upper()
            if su.startswith("INSERT"):
                sql, params = _inject_insert_tenant(sql, params, self._col, tenant)
            else:
                sql, params = _inject_filter(sql, params, self._col, tenant)
        return self._db.execute_returning(sql, params)

    def last_insert_id(self) -> Optional[int]:
        return self._db.last_insert_id()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "RLSDatabase":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)
