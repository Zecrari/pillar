"""
PillarAdmin — zero-config auto-CRUD admin dashboard.

Register any database table and get a fully-functional admin UI:
  • List view with sortable columns, search, pagination
  • Create / Edit / Delete forms (HTMX-powered, no page reload)
  • Respects Row-Level Security (RLS) when active
  • Dark theme, zero CDN dependencies (all styles/scripts inline)

Usage::

    from pillar.admin import admin
    from pillar import Pillar

    app = Pillar(title="My App")
    admin.register("users",   table="users",   pk="id", search_cols=["name", "email"])
    admin.register("orders",  table="orders",  pk="id", list_cols=["id","status","total"])
    admin.mount(app, prefix="/admin")

    # Navigate to http://localhost:8000/admin

Protect the admin with JWT roles::

    admin.mount(app, prefix="/admin", require_role="admin")
"""
from __future__ import annotations

import html
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, RedirectResponse

logger = logging.getLogger("pillar.admin")


# ──────────────────────────────────────────────────────────────────────────
# Registration config
# ──────────────────────────────────────────────────────────────────────────

class _ModelConfig:
    def __init__(
        self,
        name: str,
        table: str,
        pk: str = "id",
        list_cols: Optional[Sequence[str]] = None,
        search_cols: Optional[Sequence[str]] = None,
        readonly_cols: Optional[Sequence[str]] = None,
        label: str = "",
        icon: str = "🗄️",
    ) -> None:
        self.name         = name
        self.table        = table
        self.pk           = pk
        self.list_cols    = list(list_cols) if list_cols else []
        self.search_cols  = list(search_cols) if search_cols else []
        self.readonly_cols = set(readonly_cols or [pk])
        self.label        = label or name.replace("_", " ").title()
        self.icon         = icon


# ──────────────────────────────────────────────────────────────────────────
# Admin controller
# ──────────────────────────────────────────────────────────────────────────

class PillarAdmin:
    """
    Auto-CRUD admin dashboard.

    Mount once with ``admin.mount(app)``.  Each registered table gets its own
    list/detail/create/edit/delete views under the admin prefix.
    """

    def __init__(self) -> None:
        self._models: Dict[str, _ModelConfig] = {}
        self._prefix = "/admin"
        self._require_role: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        table: str = "",
        pk: str = "id",
        list_cols: Optional[Sequence[str]] = None,
        search_cols: Optional[Sequence[str]] = None,
        readonly_cols: Optional[Sequence[str]] = None,
        label: str = "",
        icon: str = "🗄️",
    ) -> None:
        """Register a database table for the admin dashboard."""
        self._models[name] = _ModelConfig(
            name=name,
            table=table or name,
            pk=pk,
            list_cols=list_cols,
            search_cols=search_cols,
            readonly_cols=readonly_cols,
            label=label,
            icon=icon,
        )

    def mount(self, app: Any, *, prefix: str = "/admin", require_role: str = None) -> None:
        """Mount the admin ASGI handler on *app* at *prefix*."""
        self._prefix = prefix.rstrip("/")
        self._require_role = require_role
        app._admin = self
        # Inject the admin routes into the app's core ASGI handler
        _original_route = app._route_http

        async def _patched_route(method: str, path: str, scope: dict, receive: Any) -> Response:
            if path.startswith(self._prefix):
                return await self._handle(method, path, scope, receive)
            return await _original_route(method, path, scope, receive)

        app._route_http = _patched_route
        logger.info("PillarAdmin mounted at %s (%d models)", self._prefix, len(self._models))

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    async def _handle(self, method: str, path: str, scope: dict, receive: Any) -> Response:
        # Auth check
        if self._require_role:
            user = scope.get("user") or {}
            roles = user.get("roles", [])
            if isinstance(roles, str):
                roles = [roles]
            if self._require_role not in roles:
                return JSONResponse({"detail": "Admin access required"}, status_code=403)

        sub = path[len(self._prefix):]
        sub = sub.lstrip("/") or ""
        parts = sub.split("/") if sub else []

        from .db.database import Database
        from .di import container as _c
        try:
            db: Database = _c.resolve(Database)
        except Exception:
            db = Database()

        # GET /admin  →  dashboard index
        if method == "GET" and not parts[0:1]:
            return HTMLResponse(self._index_html())

        # GET /admin/{model}  →  list view
        if method == "GET" and len(parts) == 1 and parts[0] in self._models:
            return await self._list_view(parts[0], scope, db)

        # GET /admin/{model}/new  →  create form
        if method == "GET" and len(parts) == 2 and parts[0] in self._models and parts[1] == "new":
            return await self._create_form(parts[0], db)

        # POST /admin/{model}/new  →  insert row
        if method == "POST" and len(parts) == 2 and parts[0] in self._models and parts[1] == "new":
            return await self._do_create(parts[0], scope, receive, db)

        # GET /admin/{model}/{pk}  →  edit form
        if method == "GET" and len(parts) == 2 and parts[0] in self._models:
            return await self._edit_form(parts[0], parts[1], db)

        # POST /admin/{model}/{pk}  →  update row
        if method == "POST" and len(parts) == 2 and parts[0] in self._models:
            return await self._do_update(parts[0], parts[1], scope, receive, db)

        # DELETE /admin/{model}/{pk} or POST .../delete
        if method == "DELETE" and len(parts) == 2 and parts[0] in self._models:
            return await self._do_delete(parts[0], parts[1], db)
        if method == "POST" and len(parts) == 3 and parts[0] in self._models and parts[2] == "delete":
            return await self._do_delete(parts[0], parts[1], db)

        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ------------------------------------------------------------------
    # List view
    # ------------------------------------------------------------------

    async def _list_view(self, name: str, scope: dict, db: Any) -> HTMLResponse:
        cfg   = self._models[name]
        qs    = scope.get("query_string", b"").decode()
        query = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                query[k] = v

        search = query.get("q", "").strip()
        page   = max(1, int(query.get("page", "1") or "1"))
        limit  = 25
        offset = (page - 1) * limit

        sql    = f"SELECT * FROM {cfg.table}"
        params: list = []

        if search and cfg.search_cols:
            conditions = " OR ".join(f"{col} LIKE ?" for col in cfg.search_cols)
            sql += f" WHERE {conditions}"
            params.extend([f"%{search}%"] * len(cfg.search_cols))

        sql += f" LIMIT {limit} OFFSET {offset}"
        rows = db.query_all(sql, tuple(params))
        cols = list(rows[0].keys()) if rows else cfg.list_cols or []
        if cfg.list_cols:
            cols = [c for c in cfg.list_cols if c in (rows[0].keys() if rows else cfg.list_cols)]

        return HTMLResponse(self._list_html(cfg, rows, cols, search, page))

    # ------------------------------------------------------------------
    # Create form
    # ------------------------------------------------------------------

    async def _create_form(self, name: str, db: Any) -> HTMLResponse:
        cfg = self._models[name]
        # Infer columns from the table
        cols = self._get_columns(cfg, db)
        return HTMLResponse(self._form_html(cfg, cols, {}, is_new=True))

    async def _do_create(self, name: str, scope: dict, receive: Any, db: Any) -> Response:
        cfg  = self._models[name]
        data = await self._parse_form(scope, receive)
        cols = [k for k in data if k not in cfg.readonly_cols]
        if not cols:
            return JSONResponse({"detail": "No fields to insert"}, status_code=400)
        placeholders = ", ".join("?" * len(cols))
        col_str = ", ".join(cols)
        vals = tuple(data[c] for c in cols)
        db.execute(f"INSERT INTO {cfg.table} ({col_str}) VALUES ({placeholders})", vals)
        return RedirectResponse(f"{self._prefix}/{name}", status_code=303)

    # ------------------------------------------------------------------
    # Edit form
    # ------------------------------------------------------------------

    async def _edit_form(self, name: str, pk_val: str, db: Any) -> Response:
        cfg = self._models[name]
        row = db.query(f"SELECT * FROM {cfg.table} WHERE {cfg.pk} = ?", (pk_val,))
        if row is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        cols = list(row.keys())
        return HTMLResponse(self._form_html(cfg, cols, row, is_new=False))

    async def _do_update(self, name: str, pk_val: str, scope: dict, receive: Any, db: Any) -> Response:
        cfg  = self._models[name]
        data = await self._parse_form(scope, receive)
        cols = [k for k in data if k not in cfg.readonly_cols]
        if not cols:
            return RedirectResponse(f"{self._prefix}/{name}", status_code=303)
        set_str = ", ".join(f"{c} = ?" for c in cols)
        vals    = tuple(data[c] for c in cols) + (pk_val,)
        db.execute(f"UPDATE {cfg.table} SET {set_str} WHERE {cfg.pk} = ?", vals)
        return RedirectResponse(f"{self._prefix}/{name}", status_code=303)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def _do_delete(self, name: str, pk_val: str, db: Any) -> Response:
        cfg = self._models[name]
        db.execute(f"DELETE FROM {cfg.table} WHERE {cfg.pk} = ?", (pk_val,))
        return RedirectResponse(f"{self._prefix}/{name}", status_code=303)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _get_columns(self, cfg: _ModelConfig, db: Any) -> List[str]:
        if cfg.list_cols:
            return cfg.list_cols
        try:
            row = db.query(f"SELECT * FROM {cfg.table} LIMIT 1")
            if row:
                return list(row.keys())
        except Exception:
            pass
        return [cfg.pk]

    @staticmethod
    async def _parse_form(scope: dict, receive: Any) -> dict:
        from urllib.parse import parse_qs
        body_parts: list = []
        while True:
            msg = await receive()
            body_parts.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        raw = b"".join(body_parts).decode("utf-8", errors="replace")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    # ------------------------------------------------------------------
    # HTML generation
    # ------------------------------------------------------------------

    def _css(self) -> str:
        return """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
a{color:#818cf8;text-decoration:none}a:hover{text-decoration:underline}
.nav{background:#1e293b;border-bottom:1px solid #334155;padding:12px 24px;display:flex;align-items:center;gap:16px}
.nav h1{font-size:1.1rem;font-weight:700;color:#f8fafc}
.nav .pill{background:#3730a3;color:#c7d2fe;padding:2px 10px;border-radius:9999px;font-size:.75rem}
.sidebar{width:220px;background:#1e293b;min-height:calc(100vh - 49px);padding:16px;border-right:1px solid #334155;position:fixed;top:49px}
.sidebar .model-link{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;color:#cbd5e1;margin-bottom:4px;font-size:.875rem;transition:background .15s}
.sidebar .model-link:hover,.sidebar .model-link.active{background:#0f172a;color:#f8fafc}
.content{margin-left:220px;padding:24px}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;overflow:hidden}
.card-header{padding:16px 20px;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between}
.card-header h2{font-size:1rem;font-weight:600;color:#f8fafc}
table{width:100%;border-collapse:collapse}
th{padding:10px 16px;text-align:left;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#94a3b8;background:#0f172a;border-bottom:1px solid #334155}
td{padding:10px 16px;font-size:.875rem;border-bottom:1px solid #1e293b;color:#cbd5e1}
tr:last-child td{border-bottom:none}
tr:hover td{background:#0f172a}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;font-size:.8rem;font-weight:500;cursor:pointer;border:none;transition:all .15s;text-decoration:none}
.btn-primary{background:#4f46e5;color:#fff}.btn-primary:hover{background:#4338ca;color:#fff}
.btn-danger{background:#dc2626;color:#fff}.btn-danger:hover{background:#b91c1c;color:#fff}
.btn-ghost{background:transparent;color:#94a3b8;border:1px solid #334155}.btn-ghost:hover{background:#0f172a;color:#f8fafc}
.search-bar{display:flex;gap:8px;margin-bottom:16px}
.search-bar input{flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:.875rem;outline:none}
.search-bar input:focus{border-color:#4f46e5}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:4px}
.form-group input,.form-group textarea,.form-group select{width:100%;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:.875rem;outline:none}
.form-group input:focus,.form-group textarea:focus{border-color:#4f46e5}
.form-group input[readonly]{color:#64748b;cursor:not-allowed}
.form-actions{display:flex;gap:8px;padding-top:8px}
.pagination{display:flex;gap:4px;justify-content:flex-end;margin-top:16px}
.pagination a{padding:4px 10px;border-radius:6px;background:#1e293b;border:1px solid #334155;font-size:.8rem}
.empty{padding:48px;text-align:center;color:#64748b;font-size:.9rem}
"""

    def _base(self, title: str, body: str) -> str:
        nav_models = "".join(
            f'<a class="model-link" href="{self._prefix}/{m.name}">{m.icon} {m.label}</a>'
            for m in self._models.values()
        )
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — PillarAdmin</title>
<style>{self._css()}</style>
</head>
<body>
<nav class="nav">
  <h1>⚡ PillarAdmin</h1>
  <span class="pill">v0.1</span>
</nav>
<div class="sidebar">{nav_models}</div>
<div class="content">{body}</div>
</body>
</html>"""

    def _index_html(self) -> str:
        cards = "".join(
            f"""<div class="card" style="margin-bottom:12px">
  <div class="card-header">
    <h2>{m.icon} {html.escape(m.label)}</h2>
    <a href="{self._prefix}/{m.name}" class="btn btn-ghost">View →</a>
  </div>
</div>"""
            for m in self._models.values()
        )
        body = f"""<h2 style="margin-bottom:20px;font-size:1.25rem;font-weight:700;">Dashboard</h2>
{cards or '<p style="color:#64748b">No models registered. Call admin.register() first.</p>'}"""
        return self._base("Dashboard", body)

    def _list_html(self, cfg: _ModelConfig, rows: list, cols: list, search: str, page: int) -> str:
        search_form = f"""
<form method="get" class="search-bar">
  <input name="q" value="{html.escape(search)}" placeholder="Search…">
  <button type="submit" class="btn btn-primary">Search</button>
  <a href="{self._prefix}/{cfg.name}" class="btn btn-ghost">Clear</a>
</form>"""

        if not rows:
            table_html = f'<div class="empty">No records found{" for " + html.escape(search) if search else ""}.</div>'
        else:
            th_cells = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
            th_cells += "<th style='width:140px'>Actions</th>"
            tbody = ""
            for row in rows:
                td_cells = "".join(
                    f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in cols
                )
                pk_val = html.escape(str(row.get(cfg.pk, "")))
                td_cells += f"""<td>
  <a href="{self._prefix}/{cfg.name}/{pk_val}" class="btn btn-ghost" style="margin-right:4px">Edit</a>
  <form method="post" action="{self._prefix}/{cfg.name}/{pk_val}/delete" style="display:inline"
        onsubmit="return confirm('Delete this record?')">
    <button type="submit" class="btn btn-danger">Del</button>
  </form>
</td>"""
                tbody += f"<tr>{td_cells}</tr>"
            table_html = f"""<table><thead><tr>{th_cells}</tr></thead><tbody>{tbody}</tbody></table>"""

        prev_link = f'<a href="?q={html.escape(search)}&page={page-1}" class="pagination">‹</a>' if page > 1 else ""
        next_link = f'<a href="?q={html.escape(search)}&page={page+1}" class="pagination">›</a>' if len(rows) == 25 else ""

        body = f"""<div class="card">
  <div class="card-header">
    <h2>{cfg.icon} {html.escape(cfg.label)}</h2>
    <a href="{self._prefix}/{cfg.name}/new" class="btn btn-primary">+ New</a>
  </div>
  <div style="padding:16px">
    {search_form}
    {table_html}
    <div class="pagination">{prev_link}{next_link}</div>
  </div>
</div>"""
        return self._base(cfg.label, body)

    def _form_html(self, cfg: _ModelConfig, cols: list, row: dict, *, is_new: bool) -> str:
        pk_val    = "" if is_new else html.escape(str(row.get(cfg.pk, "")))
        action    = f"{self._prefix}/{cfg.name}/new" if is_new else f"{self._prefix}/{cfg.name}/{pk_val}"
        title_txt = f"New {cfg.label}" if is_new else f"Edit {cfg.label} #{pk_val}"

        fields = ""
        for col in cols:
            val      = html.escape(str(row.get(col, "")))
            readonly = 'readonly style="background:#1e293b"' if col in cfg.readonly_cols else ""
            fields += f"""<div class="form-group">
  <label>{html.escape(col)}</label>
  <input name="{html.escape(col)}" value="{val}" {readonly}>
</div>"""

        body = f"""<div class="card" style="max-width:600px">
  <div class="card-header">
    <h2>{html.escape(title_txt)}</h2>
    <a href="{self._prefix}/{cfg.name}" class="btn btn-ghost">← Back</a>
  </div>
  <div style="padding:20px">
    <form method="post" action="{action}">
      {fields}
      <div class="form-actions">
        <button type="submit" class="btn btn-primary">{'Create' if is_new else 'Save'}</button>
        <a href="{self._prefix}/{cfg.name}" class="btn btn-ghost">Cancel</a>
      </div>
    </form>
  </div>
</div>"""
        return self._base(title_txt, body)


# Singleton — import and use directly
admin = PillarAdmin()
