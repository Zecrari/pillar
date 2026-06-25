from __future__ import annotations

import os
import sys
from pathlib import Path

import click

# ──────────────────────────────────────────────────────────────────────
# Templates used by `pillar new`
# ──────────────────────────────────────────────────────────────────────

_MAIN_PY = '''\
from pillar import Pillar
from domains.users.router import router as users_router

app = Pillar(title="{name}")
app.include_router(users_router)
'''

_PILLAR_TOML = '''\
[app]
title = "{name}"
debug = true
host  = "0.0.0.0"
port  = 8000

[database]
url = "sqlite:///./app.db"

[queue]
driver       = "sqlite"
db_path      = "pillar_queue.db"
workers      = 4
poll_interval = 0.5
'''

_USERS_SCHEMAS = '''\
from pydantic import BaseModel

class UserCreate(BaseModel):
    name: str
    email: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    status: str
'''

_USERS_REPOSITORY = '''\
from pillar.db import Database

class UserRepository:
    def __init__(self, db: Database):
        self.db = db
        self._setup()

    def _setup(self):
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS users (
               id    INTEGER PRIMARY KEY AUTOINCREMENT,
               name  TEXT NOT NULL,
               email TEXT NOT NULL UNIQUE
            )"""
        )

    def get_by_id(self, user_id: int) -> dict | None:
        return self.db.query("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_all(self) -> list:
        return self.db.query_all("SELECT * FROM users")

    def create(self, name: str, email: str) -> dict:
        self.db.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        row_id = self.db.last_insert_id()
        return self.get_by_id(row_id)
'''

_USERS_SERVICE = '''\
from pillar import background_task
from pillar.exceptions import NotFoundError
from .repository import UserRepository

class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def get_user(self, user_id: int) -> dict:
        user = self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError(f"User {user_id} not found")
        return {**user, "status": "active"}

    def list_users(self) -> list:
        return [{**u, "status": "active"} for u in self.repo.get_all()]

    def create_user(self, name: str, email: str) -> dict:
        user = self.repo.create(name, email)
        self.send_welcome_email(user["email"])
        return {**user, "status": "active"}

    @background_task(retries=3)
    def send_welcome_email(self, user_email: str):
        # Runs in the Pillar Queue — no blocking, survives restarts
        print(f"[queue] Sending welcome email to {user_email}")
'''

_USERS_ROUTER = '''\
from pillar import Router
from .schemas import UserCreate, UserResponse
from .service import UserService

router = Router(prefix="/users", tags=["Users"])

@router.get("/")
async def list_users(service: UserService):
    return service.list_users()

@router.get("/{user_id}")
async def get_user(user_id: int, service: UserService):
    return service.get_user(user_id)

@router.post("/")
async def create_user(data: UserCreate, service: UserService):
    return service.create_user(data.name, data.email)
'''

_DOCKERFILE = '''\
# ── Stage 1: builder ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8000/health || exit 1

CMD ["pillar", "run", "main:app", "--host", "0.0.0.0", "--port", "8000"]
'''

# ──────────────────────────────────────────────────────────────────────
# CLI definition
# ──────────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="pillar")
def cli():
    """🏛️  Pillar — Production-Grade Python Backend Framework"""


# ── pillar run ────────────────────────────────────────────────────────

@cli.command()
@click.argument("app_path", default="main:app")
@click.option("--host", default=None, help="Bind host (default: from pillar.toml)")
@click.option("--port", default=None, type=int, help="Bind port (default: from pillar.toml)")
@click.option("--reload", is_flag=True, help="Enable hot-reload (development only)")
@click.option("--workers", default=1, type=int, help="Number of worker processes")
def run(app_path: str, host: str, port: int, reload: bool, workers: int):
    """Start the Pillar ASGI server."""
    try:
        import uvicorn
    except ImportError:
        click.echo("❌ uvicorn is required: pip install uvicorn[standard]", err=True)
        sys.exit(1)

    # Inject CWD into sys.path and PYTHONPATH so uvicorn can find the app
    # module from whichever directory the user runs `pillar run` in.
    # PYTHONPATH covers reload-mode subprocesses; sys.path covers direct mode.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    existing = os.environ.get("PYTHONPATH", "")
    if cwd not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = cwd + (os.pathsep + existing if existing else "")

    # Read pillar.toml for defaults
    from ..config import PillarConfig
    cfg = PillarConfig.load("pillar.toml")

    _host = host or cfg.app.host
    _port = port or cfg.app.port

    click.echo(f"Pillar starting on http://{_host}:{_port}")
    click.echo(f"   app     : {app_path}")
    click.echo(f"   reload  : {reload}")
    click.echo(f"   workers : {workers}")

    uvicorn.run(
        app_path,
        host=_host,
        port=_port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="debug" if cfg.app.debug else "info",
    )


# ── pillar new ───────────────────────────────────────────────────────

@cli.command("new")
@click.argument("project_name")
def new_project(project_name: str):
    """Scaffold a new Pillar project."""
    root = Path(project_name)

    dirs = [
        root,
        root / "domains" / "users",
        root / "core",
        root / "tests",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").touch()

    (root / "main.py").write_text(_MAIN_PY.format(name=project_name))
    (root / "pillar.toml").write_text(_PILLAR_TOML.format(name=project_name))
    (root / "requirements.txt").write_text("pillar\n")
    (root / "domains" / "__init__.py").touch()
    (root / "domains" / "users" / "schemas.py").write_text(_USERS_SCHEMAS)
    (root / "domains" / "users" / "repository.py").write_text(_USERS_REPOSITORY)
    (root / "domains" / "users" / "service.py").write_text(_USERS_SERVICE)
    (root / "domains" / "users" / "router.py").write_text(_USERS_ROUTER)

    click.echo(f"✅ Created Pillar project: {project_name}/")
    click.echo(f"\n   Next steps:")
    click.echo(f"     cd {project_name}")
    click.echo(f"     pip install -r requirements.txt")
    click.echo(f"     pillar run main:app --reload")


# ── pillar generate ──────────────────────────────────────────────────

@cli.group()
def generate():
    """Generate project files."""


@generate.command("dockerfile")
def gen_dockerfile():
    """Generate a production-optimised multi-stage Dockerfile."""
    Path("Dockerfile").write_text(_DOCKERFILE)
    click.echo("✅ Generated Dockerfile")


@generate.command("client")
@click.option("--lang", default="typescript", type=click.Choice(["typescript"]),
              show_default=True, help="Target language for the generated client")
@click.option("--out", default=None, help="Output file (default: pillar-client.ts)")
@click.option("--url", default="http://localhost:8000",
              help="Base URL the client will use")
@click.argument("openapi_source", default="openapi.json")
def gen_client(lang: str, out: str, url: str, openapi_source: str):
    """
    Generate a fully-typed API client from the OpenAPI spec.

    \b
    Examples:
      pillar generate client                     # reads openapi.json
      pillar generate client --url https://api.example.com
      pillar generate client openapi.json --out src/api/client.ts
    """
    import json as _json

    # Load spec
    source_path = Path(openapi_source)
    if source_path.exists():
        spec = _json.loads(source_path.read_text())
    else:
        click.echo(f"Cannot find {openapi_source}. Start the server and run:\n"
                   f"  curl http://localhost:8000/openapi.json > openapi.json", err=True)
        sys.exit(1)

    ts = _generate_typescript_client(spec, url)
    out_path = Path(out or "pillar-client.ts")
    out_path.write_text(ts, encoding="utf-8")
    click.echo(f"Generated TypeScript client: {out_path}")
    click.echo(f"  {len(spec.get('paths', {}))} endpoint(s)  |  "
               f"{len(spec.get('components', {}).get('schemas', {}))} schema(s)")


def _generate_typescript_client(spec: dict, base_url: str) -> str:
    """Generate a typed TypeScript fetch client from an OpenAPI 3.1 spec."""
    paths      = spec.get("paths",       {})
    schemas    = spec.get("components",  {}).get("schemas", {})
    api_title  = spec.get("info",        {}).get("title", "PillarAPI")
    safe_title = "".join(c if c.isalnum() else "" for c in api_title)

    lines = [
        "// Auto-generated by Pillar — do not edit manually",
        f"// Source: {api_title}",
        "",
        f'const BASE_URL = "{base_url.rstrip("/")}"',
        "",
        "async function _fetch<T>(method: string, path: string, body?: unknown): Promise<T> {",
        "  const opts: RequestInit = { method, headers: { 'Content-Type': 'application/json' } }",
        "  if (body !== undefined) opts.body = JSON.stringify(body)",
        "  const res = await fetch(BASE_URL + path, opts)",
        "  if (!res.ok) {",
        "    const err = await res.json().catch(() => ({ detail: res.statusText }))",
        "    throw Object.assign(new Error(err.detail || res.statusText), { status: res.status, data: err })",
        "  }",
        "  return res.status === 204 ? (undefined as unknown as T) : res.json()",
        "}",
        "",
    ]

    # ── TypeScript interfaces from Pydantic schemas ───────────────────
    lines.append("// ── Schemas ─────────────────────────────────────────────────────")
    lines.append("")
    for name, schema in schemas.items():
        if name == "HTTPValidationError":
            continue
        lines.append(f"export interface {name} {{")
        props = schema.get("properties", {})
        req   = set(schema.get("required", []))
        for field, info in props.items():
            ts_type = _json_schema_to_ts(info, schemas)
            opt     = "" if field in req else "?"
            lines.append(f"  {field}{opt}: {ts_type}")
        lines.append("}")
        lines.append("")

    # ── API functions ─────────────────────────────────────────────────
    lines.append("// ── API ─────────────────────────────────────────────────────────")
    lines.append("")
    lines.append(f"export const {safe_title} = {{")

    for path, methods in paths.items():
        for http_method, op in methods.items():
            if http_method in ("head", "options"):
                continue

            op_id    = op.get("operationId", f"{http_method}_{path.replace('/', '_')}")
            fn_name  = _to_camel(op_id)
            summary  = op.get("summary", "")
            params   = op.get("parameters", [])
            has_body = "requestBody" in op
            status   = next(iter(op.get("responses", {200: {}})), 200)

            # Return type
            response = op.get("responses", {}).get(str(status), {})
            ret_type = _response_ts_type(response, schemas)

            # Build args list
            path_params  = [p for p in params if p.get("in") == "path"]
            query_params = [p for p in params if p.get("in") == "query"]

            args: list = []
            for p in path_params:
                t = _json_schema_to_ts(p.get("schema", {}), schemas)
                args.append(f"{p['name']}: {t}")
            if query_params:
                q_fields = ", ".join(
                    f"{p['name']}{'?' if not p.get('required') else ''}: "
                    f"{_json_schema_to_ts(p.get('schema', {}), schemas)}"
                    for p in query_params
                )
                args.append(f"query?: {{ {q_fields} }}")
            if has_body:
                body_ref  = (op.get("requestBody", {})
                               .get("content", {})
                               .get("application/json", {})
                               .get("schema", {})
                               .get("$ref", ""))
                body_type = body_ref.split("/")[-1] if body_ref else "unknown"
                args.append(f"body: {body_type}")

            # Build URL + fetch call
            ts_path = path.replace("{", "${")
            qs_line = ""
            if query_params:
                qs_line = (
                    "\n    const qs = query ? "
                    "'?' + new URLSearchParams(Object.entries(query).filter(([,v]) => v != null)"
                    ".map(([k,v]) => [k, String(v)])).toString() : ''"
                )
                ts_path = f"{ts_path}${{qs}}"

            args_str   = ", ".join(args)
            body_arg   = ", body" if has_body else ""
            doc        = f"/** {summary} */" if summary else ""

            lines += [
                f"  {doc}",
                f"  {fn_name}: ({args_str}): Promise<{ret_type}> => {{",
            ]
            if qs_line:
                lines.append(f"  {qs_line}")
            lines += [
                f"    return _fetch<{ret_type}>('{http_method.upper()}', `{ts_path}`{body_arg})",
                "  },",
                "",
            ]

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _to_camel(s: str) -> str:
    parts = s.replace("-", "_").split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _json_schema_to_ts(schema: dict, all_schemas: dict) -> str:
    ref = schema.get("$ref", "")
    if ref:
        return ref.split("/")[-1]
    t = schema.get("type")
    if t == "integer" or t == "number": return "number"
    if t == "boolean":                  return "boolean"
    if t == "array":
        items = schema.get("items", {})
        return f"{_json_schema_to_ts(items, all_schemas)}[]"
    if t == "object":                   return "Record<string, unknown>"
    if schema.get("nullable"):          return f"{_json_schema_to_ts({**schema, 'nullable': False}, all_schemas)} | null"
    return "string"


def _response_ts_type(response: dict, schemas: dict) -> str:
    content = response.get("content", {}).get("application/json", {}).get("schema", {})
    if not content:
        return "void"
    ref = content.get("$ref", "")
    if ref:
        return ref.split("/")[-1]
    return _json_schema_to_ts(content, schemas)


@generate.command("domain")
@click.argument("domain_name")
def gen_domain(domain_name: str):
    """Scaffold a new domain (router + service + repository + schemas)."""
    base = Path("domains") / domain_name
    base.mkdir(parents=True, exist_ok=True)
    (base / "__init__.py").touch()

    cap = domain_name.capitalize()

    (base / "schemas.py").write_text(
        f"from pydantic import BaseModel\n\n"
        f"class {cap}Create(BaseModel):\n    name: str\n\n"
        f"class {cap}Response(BaseModel):\n    id: int\n    name: str\n"
    )
    (base / "repository.py").write_text(
        f"from pillar.db import Database\n\n"
        f"class {cap}Repository:\n"
        f"    def __init__(self, db: Database):\n"
        f"        self.db = db\n"
    )
    (base / "service.py").write_text(
        f"from .repository import {cap}Repository\n\n"
        f"class {cap}Service:\n"
        f"    def __init__(self, repo: {cap}Repository):\n"
        f"        self.repo = repo\n"
    )
    (base / "router.py").write_text(
        f"from pillar import Router\n"
        f"from .service import {cap}Service\n\n"
        f"router = Router(prefix='/{domain_name}', tags=['{cap}'])\n"
    )

    click.echo(f"✅ Generated domain: domains/{domain_name}/")


# ── pillar routes ────────────────────────────────────────────────────

@cli.command("routes")
@click.argument("app_path", default="main:app")
def routes(app_path: str):
    """
    List all registered routes for the given app.

    \b
    Example:
      pillar routes main:app
    """
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    module_path, attr = app_path.rsplit(":", 1)
    try:
        import importlib
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        click.echo(f"Cannot import '{module_path}': {exc}", err=True)
        sys.exit(1)

    app = getattr(module, attr, None)
    if app is None:
        click.echo(f"No attribute '{attr}' in '{module_path}'", err=True)
        sys.exit(1)

    # Collect routes from all included routers before _build()
    from ..router import RouteEntry, WebSocketEntry

    all_routes = []
    all_ws = []
    for router in getattr(app, "_routers", []):
        all_routes.extend(router._routes)
        all_ws.extend(router._ws_routes)

    if not all_routes and not all_ws:
        click.echo("No routes found (have you called app.include_router()?)")
        return

    method_width = 8
    path_width   = max((len(r.full_path) for r in all_routes), default=20) + 2
    path_width   = max(path_width, 22)

    header = f"{'METHOD':<{method_width}}  {'PATH':<{path_width}}  {'TAGS'}"
    click.echo(header)
    click.echo("-" * (method_width + path_width + 30))

    for r in all_routes:
        tags = ",".join(r.tags) if r.tags else "-"
        click.echo(f"{r.method:<{method_width}}  {r.full_path:<{path_width}}  {tags}")

    for ws in all_ws:
        tags = ",".join(ws.tags) if ws.tags else "-"
        click.echo(f"{'WS':<{method_width}}  {ws.full_path:<{path_width}}  {tags}")

    click.echo(f"\n  {len(all_routes)} HTTP route(s) + {len(all_ws)} WebSocket route(s)")


# ── pillar info ──────────────────────────────────────────────────────

@cli.command()
def info():
    """Print framework and engine information."""
    try:
        from _pillar_engine import engine_version, PillarRouter
        eng = engine_version()
        r = PillarRouter()
        r.add_route("GET", "/ping", "test")
        match = r.match_route("GET", "/ping")
        rust_ok = match is not None
    except ImportError:
        eng = "not compiled (run `maturin develop`)"
        rust_ok = False

    click.echo("Pillar Framework")
    click.echo(f"   Python  : {sys.version.split()[0]}")
    click.echo(f"   Engine  : {eng}")
    click.echo(f"   Router  : {'Rust radix-tree' if rust_ok else 'Python fallback'}")
    click.echo(f"   Queue   : {'Rust SQLite WAL' if rust_ok else 'Python SQLite'}")


# ── pillar trace ─────────────────────────────────────────────────────

@cli.command("trace")
@click.argument("trace_id", default="")
@click.option("--host", default="http://localhost:8000", help="Base URL of the running Pillar app")
@click.option("--open", "open_browser", is_flag=True, help="Open the HTML flamegraph in a browser")
@click.option("--list", "show_list", is_flag=True, help="Show the 50 most recent traces")
def trace_cmd(trace_id: str, host: str, open_browser: bool, show_list: bool):
    """
    Inspect per-layer request timing from the running Pillar app.

    \b
    Examples:
      pillar trace                          # list recent traces
      pillar trace <request-id>             # ASCII waterfall
      pillar trace <request-id> --open      # HTML flamegraph in browser
      pillar trace --list --host http://...
    """
    import json as _json

    base = host.rstrip("/")

    if show_list or (not trace_id):
        _trace_list(base)
        return

    if open_browser:
        import webbrowser
        url = f"{base}/trace/{trace_id}"
        webbrowser.open(url)
        click.echo(f"Opened {url}")
        return

    # Fetch the trace JSON via a lightweight approach:
    # /trace/<id> returns HTML; we expose a JSON endpoint at the same path
    # via Accept: application/json
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{base}/trace/{trace_id}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        click.echo(f"Could not fetch trace from {base}: {exc}", err=True)
        click.echo(f"Tip: make sure the app is running and tracing is active.", err=True)
        sys.exit(1)

    _render_trace_ascii(data)


def _trace_list(base: str) -> None:
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}/trace/?format=json", timeout=5) as resp:
            traces = _json.loads(resp.read())
    except Exception as exc:
        click.echo(f"Could not fetch traces from {base}: {exc}", err=True)
        click.echo("Open /trace/ in a browser to view the visual list.", err=True)
        sys.exit(1)

    if not traces:
        click.echo("No traces yet. Make a request to see traces here.")
        return

    click.echo(f"\n  {'TRACE ID':<20} {'METHOD':<8} {'PATH':<30} {'STATUS':<8} {'TOTAL':>8}")
    click.echo("  " + "─" * 78)
    for t in traces:
        click.echo(
            f"  {t['trace_id'][:18]:<20} {t['method']:<8} {t['path']:<30} "
            f"{t['status_code']:<8} {t['total_ms']:>6.1f}ms"
        )
    click.echo(f"\n  {len(traces)} trace(s)  ·  /trace/ for HTML view")


def _render_trace_ascii(data: dict) -> None:
    spans = data.get("spans", [])
    total = data.get("total_ms", 1.0) or 1.0
    width = 50

    click.echo(f"\n  Trace   {data.get('trace_id', '')}")
    click.echo(f"  Route   {data.get('method', '')} {data.get('path', '')}")
    click.echo(f"  Status  {data.get('status_code', '')}")
    click.echo(f"  Total   {total:.2f}ms")
    click.echo("")
    click.echo(f"  {'Layer':<10} {'Span':<28} {'Bar':<{width}} {'ms':>6}")
    click.echo("  " + "─" * (width + 48))

    for sp in spans:
        offset  = int((sp.get("start_ms", 0) - data.get("start_ms", 0)) / total * width)
        bar_w   = max(int(sp.get("duration_ms", 0) / total * width), 1)
        bar     = " " * offset + "█" * bar_w
        err     = " ✗" if sp.get("error") else ""
        label   = sp.get("name", "") + err
        click.echo(
            f"  {sp.get('layer',''):<10} {label:<28} {bar:<{width}} {sp.get('duration_ms', 0):>5.1f}"
        )

    if not spans:
        click.echo("  (no spans recorded — see /trace/ for help adding @trace_span decorators)")
