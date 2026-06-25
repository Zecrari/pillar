# Pillar Framework — Full Technical Specification

**Version:** 0.1.0  
**Repository:** https://github.com/Zecrari/pillar  
**Install:** `pip install git+https://github.com/Zecrari/pillar.git`

---

## Table of Contents

1. [Philosophy & Positioning](#1-philosophy--positioning)
2. [Architecture Overview](#2-architecture-overview)
3. [Installation](#3-installation)
4. [Project Structure](#4-project-structure)
5. [Application Entry Point](#5-application-entry-point)
6. [Routing — Router](#6-routing--router)
7. [Routing — Controller](#7-routing--controller)
8. [Dependency Injection](#8-dependency-injection)
9. [Configuration](#9-configuration)
10. [Background Queue](#10-background-queue)
11. [Database Layer](#11-database-layer)
12. [Row-Level Security](#12-row-level-security)
13. [Security & JWT Middleware](#13-security--jwt-middleware)
14. [Middleware Stack](#14-middleware-stack)
15. [Metrics](#15-metrics)
16. [Time-Travel Request Debugger](#16-time-travel-request-debugger)
17. [OpenTelemetry Integration](#17-opentelemetry-integration)
18. [AI-Native Data Layer](#18-ai-native-data-layer)
19. [Vector Search](#19-vector-search)
20. [Zero-Migration Schema Sync](#20-zero-migration-schema-sync)
21. [Architecture Enforcer](#21-architecture-enforcer)
22. [OpenAPI & Documentation](#22-openapi--documentation)
23. [Response Helpers](#23-response-helpers)
24. [Exceptions](#24-exceptions)
25. [CLI Reference](#25-cli-reference)
26. [Rust Engine](#26-rust-engine)
27. [Testing](#27-testing)
28. [Deployment](#28-deployment)
29. [Environment Variables](#29-environment-variables)
30. [Public API Surface](#30-public-api-surface)

---

## 1. Philosophy & Positioning

Pillar is a **production-grade, opinionated Python backend framework** built around a Rust engine. It targets the "Day 100" problem: FastAPI is excellent for Day 1, but provides no guidance on architecture when a project reaches 50+ endpoints, 10+ developers, and real scaling requirements.

### Core Tenets

| Tenet | Description |
|-------|-------------|
| **Opinionated by design** | Enforces Clean Architecture (Router → Service → Repository) at startup. Violations cause the server to refuse to start. |
| **Domain-first, not file-first** | Code lives in `domains/<name>/` not in flat `routers/`, `models/`, `schemas/` directories. |
| **Rust for infrastructure, Python for business logic** | Routing, queue persistence, and radix-tree matching run in compiled Rust. Handlers and services run in Python. |
| **Batteries-included** | Background tasks, JWT auth, RLS, metrics, tracing, AI extraction, and vector search ship in the core — no Celery, no LangChain needed. |
| **Zero-config escape hatches** | Every system degrades gracefully. No Rust toolchain? Pure-Python fallback. No OTel SDK? Spans are no-ops. No `.env`? Sensible defaults. |

### Differentiation from FastAPI

| Feature | FastAPI | Pillar |
|---------|---------|--------|
| Routing style | `@router.get(...)` decorators | `Controller` class + conventions |
| Architecture enforcement | None | AST-checked at startup |
| Background tasks | Celery (external) | Built-in SQLite/Rust queue |
| Documentation UI | Swagger UI (CDN) | Custom zero-CDN explorer |
| Live dashboard | None | `/dashboard` with auto-refresh |
| Metrics | None | `/metrics` (JSON + Prometheus) |
| Request tracing | None | `/trace/{id}` flamegraph |
| AI extraction | None | `PillarAI.extract()` built-in |
| Row-level security | None | Auto-inject via `RLSDatabase` |
| Schema migrations | Alembic (external) | `SchemaSync.ensure_table()` |

---

## 2. Architecture Overview

```
                    ┌─────────────────────────────────┐
                    │         ASGI Server              │
                    │   (uvicorn / hypercorn)          │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │        Middleware Stack          │
                    │  GZip → CORS → SecurityHeaders  │
                    │  → RequestID → Tracer → Timing  │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │         Pillar App               │
                    │  ┌──────────────────────────┐   │
                    │  │  Rust PillarRouter        │   │
                    │  │  (matchit radix-tree)     │   │
                    │  └──────────────────────────┘   │
                    │  ┌──────────────────────────┐   │
                    │  │  Smart Bridge            │   │
                    │  │  (sync → thread pool)    │   │
                    │  └──────────────────────────┘   │
                    └──────────────┬──────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
   ┌──────────▼──────┐  ┌─────────▼────────┐  ┌───────▼────────┐
   │   Controller /  │  │  DI Container    │  │  Background    │
   │   Router        │  │  (auto-wired)    │  │  Queue Worker  │
   └──────────┬──────┘  └─────────┬────────┘  └───────┬────────┘
              │                    │                    │
   ┌──────────▼──────┐  ┌─────────▼────────┐  ┌───────▼────────┐
   │   Service       │  │  Database /      │  │  Rust          │
   │   Layer         │  │  RLSDatabase     │  │  PillarQueue   │
   └─────────────────┘  └──────────────────┘  └────────────────┘
```

### File Tree

```
pillar/                     # Framework package
├── __init__.py             # Public API exports
├── app.py                  # Pillar ASGI application
├── router.py               # Router, RouteEntry, invoke_handler
├── controller.py           # Controller base class, @action decorator
├── di.py                   # DIContainer
├── config.py               # PillarConfig (TOML + env)
├── middleware.py           # CORS, Security, RequestID, Timing, Trace
├── metrics.py              # In-memory metrics + Prometheus export
├── tracer.py               # Time-travel request debugger
├── telemetry.py            # OpenTelemetry integration (optional)
├── security.py             # JWTMiddleware, encode_jwt, decode_jwt, RLSContext
├── responses.py            # ok(), created(), paginate(), problem()
├── exceptions.py           # NotFoundError, UnauthorizedError, etc.
├── openapi.py              # OpenAPI 3.1 builder + custom docs UI
├── dashboard.py            # Live /dashboard HTML
├── ai.py                   # PillarAI — typed LLM extraction
├── vector.py               # VectorRepository — RAG abstraction
├── architecture/
│   └── enforcer.py         # AST-based DDD layer enforcer
├── db/
│   ├── database.py         # SQLite Database with WAL mode
│   ├── rls.py              # RLSDatabase — auto WHERE tenant_id
│   └── sync.py             # SchemaSync — zero-migration schema sync
├── queue/
│   ├── decorators.py       # @background_task decorator
│   ├── storage.py          # TaskStorage (Rust/Python backends)
│   └── worker.py           # TaskWorker — asyncio poll loop
└── cli/
    └── main.py             # pillar run / new / routes / generate / trace

src/
└── lib.rs                  # Rust engine (PillarRouter + PillarQueue)

example_app/
├── main.py
├── pillar.toml
├── domains/
│   ├── users/
│   │   ├── router.py       # UserController (Controller-based)
│   │   ├── service.py
│   │   ├── repository.py
│   │   └── schemas.py
│   └── billing/
│       └── router.py       # billing_router (classic Router)
└── core/
    ├── database.py
    └── security.py

tests/
├── conftest.py
├── test_router.py
├── test_di.py
├── test_queue.py
└── test_architecture.py
```

---

## 3. Installation

### Pure Python (no Rust required)

```bash
pip install git+https://github.com/Zecrari/pillar.git
```

The framework detects whether the compiled Rust extension (`_pillar_engine.pyd`) is present at startup. If not, it falls back to a pure-Python router and queue implementation automatically.

### With optional extras

```bash
# OpenTelemetry exporters (gRPC)
pip install "git+https://github.com/Zecrari/pillar.git#egg=pillar-framework[otel]"

# OpenTelemetry via HTTP
pip install "git+https://github.com/Zecrari/pillar.git#egg=pillar-framework[otel-http]"

# python-jose for RS256 JWT support
pip install "git+https://github.com/Zecrari/pillar.git#egg=pillar-framework[jwt]"

# Qdrant production vector backend
pip install "git+https://github.com/Zecrari/pillar.git#egg=pillar-framework[qdrant]"

# Everything
pip install "git+https://github.com/Zecrari/pillar.git#egg=pillar-framework[all]"
```

### With Rust engine (performance mode)

Requires Rust toolchain (`rustup`) and `maturin`:

```bash
git clone https://github.com/Zecrari/pillar.git
cd pillar
pip install maturin
maturin develop --release       # dev — installs editable + compiled .pyd
# or
maturin build --release         # builds .whl in target/wheels/
pip install target/wheels/pillar_framework-*.whl --force-reinstall
```

---

## 4. Project Structure

Pillar enforces the following directory layout for application code:

```
my_project/
├── main.py                 # app = Pillar(...)
├── pillar.toml             # configuration
├── requirements.txt
├── domains/
│   └── <domain_name>/
│       ├── __init__.py
│       ├── schemas.py      # Pydantic models only
│       ├── repository.py   # DB queries only — imports schemas
│       ├── service.py      # Business logic — imports repository + schemas
│       └── router.py       # HTTP handlers — imports service + schemas
└── core/
    ├── database.py         # Database instance
    └── security.py         # JWT config
```

The `domains/` hierarchy is the **only enforced constraint**. Non-domain code can live anywhere. The `core/` directory is conventional, not required.

---

## 5. Application Entry Point

```python
from pillar import Pillar
from domains.users.router import UserController
from domains.billing.router import router as billing_router

app = Pillar(
    title="My API",
    version="1.0.0",
    description="Optional description.",
    debug=False,
    config_path="pillar.toml",   # optional; uses defaults if absent
)

app.include_controller(UserController)   # Controller-based routing
app.include_router(billing_router)       # Classic Router-based routing
```

### `Pillar.__init__` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | `"Pillar App"` | Displayed in docs and `/health` |
| `version` | `str` | `"0.1.0"` | API version string |
| `description` | `str` | `""` | Shown in OpenAPI spec |
| `debug` | `bool` | `False` | Enables detailed error tracebacks in responses |
| `config_path` | `str` | `"pillar.toml"` | Path to TOML config; silently ignored if absent |
| `container` | `DIContainer` | global | Override the DI container (useful for tests) |

### Built-in endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Liveness probe — returns `{"status": "healthy", "engine": "..."}` |
| `/ready` | GET | Readiness probe — 200 after startup, 503 during startup |
| `/metrics` | GET | JSON metrics snapshot (add `?format=prometheus` for Prometheus) |
| `/dashboard` | GET | Live HTML dashboard (auto-refreshes every 5 s) |
| `/queue/status` | GET | Queue pending/failed/done counts |
| `/docs` | GET | Custom Pillar API Explorer (zero CDN) |
| `/redoc` | GET | ReDoc documentation UI |
| `/openapi.json` | GET | Raw OpenAPI 3.1 spec |
| `/guide` | GET | Interactive getting-started guide |
| `/trace` | GET | Time-travel trace list (HTML or `?format=json`) |
| `/trace/{id}` | GET | Per-request flamegraph (HTML or `Accept: application/json`) |

### ASGI lifespan

On startup, Pillar:
1. Registers the `Database` singleton into the DI container.
2. Runs the `ArchitectureEnforcer` to validate domain imports.
3. Builds the Rust (or Python-fallback) router from all registered routes.
4. Wraps the core handler with the middleware stack.
5. Starts the `TaskWorker` asyncio polling loop.

On shutdown (SIGTERM → uvicorn lifespan shutdown):
1. Sets `_ready = False` (readiness probe returns 503).
2. Signals the queue worker to stop accepting new tasks.
3. Waits up to 30 s for in-flight tasks to finish (graceful drain).
4. Cancels any remaining tasks after timeout.

---

## 6. Routing — Router

The classic decorator-based router, compatible with the existing FastAPI mental model.

```python
from pillar import Router

router = Router(prefix="/billing", tags=["Billing"])

@router.get("/invoices")
async def list_invoices():
    return [...]

@router.post("/invoices", status_code=201)
async def create_invoice(data: InvoiceCreate, service: BillingService):
    return service.create(data)

@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, service: BillingService):
    return service.get(invoice_id)
```

### Router constructor

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prefix` | `str` | `""` | URL prefix applied to all routes |
| `tags` | `list[str]` | `[]` | OpenAPI tags applied to all routes |

### Decorator parameters (all methods)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status_code` | `int` | 200 (POST: 201) | Default HTTP status code for success |
| `summary` | `str` | `None` | OpenAPI summary (falls back to function name) |
| `description` | `str` | `None` | OpenAPI description (falls back to docstring) |
| `response_model` | `Type` | `None` | Pydantic model for OpenAPI response schema |
| `deprecated` | `bool` | `False` | Mark as deprecated in OpenAPI |
| `include_in_schema` | `bool` | `True` | Hide from OpenAPI spec if False |

### HTTP methods

`@router.get`, `@router.post`, `@router.put`, `@router.patch`, `@router.delete`, `@router.head`, `@router.options`

### WebSocket

```python
@router.websocket("/ws/chat")
async def chat(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")
```

### Smart Bridge — parameter injection rules

The `invoke_handler` function resolves handler parameters in this precedence order:

1. **Path parameter** — parameter name matches `{name}` in the route pattern → coerced to annotated type.
2. **`Request`** — annotation is `starlette.requests.Request` → raw ASGI request object.
3. **`WebSocket`** — annotation is `starlette.websockets.WebSocket` → raw WebSocket object.
4. **Pydantic body** — annotation is a `BaseModel` subclass → parsed from JSON request body.
5. **DI service** — annotation is a class (not a primitive) → resolved from the DI container.
6. **Query parameter** — everything else → taken from `?key=value` query string with optional default.

Sync handlers are automatically dispatched to `loop.run_in_executor(None, ...)` to avoid blocking the event loop.

---

## 7. Routing — Controller

Resource-oriented class-based routing. One controller class represents one REST resource.

```python
from pillar import Controller, action

class UserController(Controller):
    prefix = "/users"
    tags   = ["Users"]

    async def list(self, service: UserService) -> list:
        """GET /users/"""
        return service.list_users()

    async def get(self, user_id: int, service: UserService) -> UserResponse:
        """GET /users/{user_id}"""
        return service.get_user(user_id)

    async def create(self, data: UserCreate, service: UserService) -> UserResponse:
        """POST /users/ — returns 201"""
        return service.create_user(data)

    async def update(self, user_id: int, data: UserUpdate, service: UserService) -> UserResponse:
        """PUT /users/{user_id}"""
        return service.update_user(user_id, data)

    async def patch(self, user_id: int, data: UserUpdate, service: UserService) -> UserResponse:
        """PATCH /users/{user_id}"""
        return service.patch_user(user_id, data)

    async def delete(self, user_id: int, service: UserService):
        """DELETE /users/{user_id} — returns 204"""
        service.delete_user(user_id)

    @action.post("/{user_id}/activate")
    async def activate(self, user_id: int, service: UserService) -> UserResponse:
        """POST /users/{user_id}/activate"""
        return service.activate_user(user_id)

app.include_controller(UserController)
```

### Convention table

| Method name | HTTP verb | Path | Default status |
|-------------|-----------|------|----------------|
| `list` | GET | `/prefix/` | 200 |
| `get` | GET | `/prefix/{id_param}` | 200 |
| `create` | POST | `/prefix/` | 201 |
| `update` | PUT | `/prefix/{id_param}` | 200 |
| `patch` | PATCH | `/prefix/{id_param}` | 200 |
| `delete` | DELETE | `/prefix/{id_param}` | 204 |

The `{id_param}` is auto-detected: it is the first parameter with a primitive type annotation (`int`, `str`, `float`, `bool`, `bytes`).

### `@action` decorator

```python
action("GET",    "/path")   # explicit
action.get(     "/path")    # shorthand
action.post(    "/path")
action.put(     "/path")
action.patch(   "/path")
action.delete(  "/path")
```

Supports the same `status_code`, `summary`, `description`, `response_model`, `deprecated`, `include_in_schema` parameters as the Router decorators.

### Controller class declaration

```python
# Option 1: class attributes
class UserController(Controller):
    prefix = "/users"
    tags   = ["Users"]

# Option 2: keyword args on the class line
class UserController(Controller, prefix="/users", tags=["Users"]):
    ...
```

### Registration

```python
app.include_controller(UserController)
```

Internally, `include_controller` creates a synthetic `Router` from the controller's collected `RouteEntry` objects and appends it to `_routers`. Both `Controller` and `Router` routes coexist in the same application.

---

## 8. Dependency Injection

Pillar uses a lightweight, reflection-based DI container that auto-wires constructors by type annotation.

```python
from pillar.di import container, DIContainer

# Register a pre-built instance (manual singleton)
db = Database("sqlite:///./app.db")
container.register_instance(Database, db)

# Register an interface → concrete class binding
container.bind(IUserRepository, SQLiteUserRepository)

# Resolve manually (framework does this automatically for handlers)
service = container.resolve(UserService)
```

### Resolution algorithm

```
resolve(cls):
  1. Return cls from _singletons if registered.
  2. Look up _bindings[cls] → concrete (defaults to cls itself).
  3. Inspect concrete.__init__ with get_type_hints() (handles PEP 563 string annotations).
  4. For each typed, non-primitive parameter: recursively resolve(annotation).
  5. Construct concrete(**resolved_kwargs).
  6. Cache in _singletons[cls].
  7. Return instance.
```

All resolved instances are **singletons** — the container constructs each type at most once per application lifetime.

### Primitives are not injected

Parameters annotated with `str`, `int`, `float`, `bool`, or `bytes` are never auto-injected. They must have defaults or be supplied as path/query parameters.

### Test isolation

```python
container.reset()   # clears all singletons and bindings
```

### PEP 563 compatibility

Pillar's DI uses `typing.get_type_hints(cls.__init__)` to resolve annotations, which correctly handles `from __future__ import annotations` (where all annotations become lazy strings at parse time).

---

## 9. Configuration

Configuration is loaded from `pillar.toml` (if present), then overridden by environment variables.

### `pillar.toml` reference

```toml
[app]
title       = "My API"
version     = "1.0.0"
debug       = false
host        = "0.0.0.0"
port        = 8000
description = "Powered by Pillar"

[database]
url       = "sqlite:///./app.db"   # only SQLite in v0.1.0
pool_size = 10
echo      = false                  # log SQL to stdout

[queue]
driver        = "sqlite"           # "sqlite" | "redis" (future)
db_path       = "pillar_queue.db"
workers       = 4
poll_interval = 0.5                # seconds between polls

[docs]
enabled      = true
swagger_url  = "/docs"
redoc_url    = "/redoc"
openapi_url  = "/openapi.json"
guide_url    = "/guide"
title        = ""                  # defaults to [app].title
description  = ""

[cors]
enabled           = true
allow_origins     = ["*"]
allow_methods     = ["*"]
allow_headers     = ["*"]
allow_credentials = false
expose_headers    = ["X-Request-ID", "X-Response-Time"]

[security]
add_request_id       = true
add_timing           = true
add_security_headers = true

[telemetry]
enabled      = false
exporter     = "jaeger"            # "jaeger" | "datadog" | "grafana"
endpoint     = ""
service_name = "pillar-app"
```

### Environment variable overrides

| Variable | Config key | Description |
|----------|-----------|-------------|
| `DATABASE_URL` | `database.url` | Database connection string |
| `DEBUG` | `app.debug` | `1` / `true` / `yes` |
| `HOST` | `app.host` | Bind address |
| `PORT` | `app.port` | Bind port |
| `QUEUE_DRIVER` | `queue.driver` | Queue backend |
| `REDIS_URL` | `queue.redis_url` | Redis connection string |
| `PILLAR_DOCS_ENABLED` | `docs.enabled` | `0` / `false` to disable |

---

## 10. Background Queue

Pillar ships a persistent, durable background task queue backed by the Rust `PillarQueue` (SQLite WAL) or a pure-Python fallback. No Celery, Redis, or external broker required.

### `@background_task` decorator

```python
from pillar import background_task

class UserService:
    @background_task(retries=3)
    def send_welcome_email(self, user_email: str):
        email_client.send(user_email, "Welcome to Pillar!")

# In a handler:
async def create(self, data: UserCreate, service: UserService):
    user = service.create_user(data)
    service.send_welcome_email(user.email)  # queued, not executed
    return user
```

When the decorated method is **called**, it does not execute. Instead, the call is serialised (function path + args + kwargs) and written atomically to the SQLite queue. The `TaskWorker` polls the queue and executes it asynchronously.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `retries` | `int` | `0` | Number of retry attempts on failure before permanent failure |
| `cron` | `str` | `None` | Cron expression for scheduled tasks (future feature) |

### Task lifecycle

```
PENDING → RUNNING → DONE
                 ↘ FAILED → PENDING (if retries_left > 0)
                          → DEAD (if retries_left == 0)
```

### `TaskStorage` API

```python
from pillar.queue.storage import TaskStorage

storage = TaskStorage.get_instance()

task_id = storage.enqueue("my_app.tasks.send_email", args=("user@example.com",), retries=3)
tasks   = storage.dequeue(limit=10)       # returns list of task dicts
storage.mark_complete(task_id)
storage.mark_failed(task_id, "error msg", retry=True)
count   = storage.pending_count()
failed  = storage.failed_count()
done    = storage.done_count()
driver  = storage.backend_name()          # "rust-sqlite-wal" or "python-sqlite"
```

### `TaskWorker`

Started automatically during ASGI lifespan startup. Polls `TaskStorage.dequeue()` every `queue.poll_interval` seconds. Runs up to 5 tasks concurrently (via `asyncio.ensure_future`). Sync task functions run in `loop.run_in_executor(None, ...)`.

**Graceful shutdown:** When the ASGI server receives SIGTERM, `worker.stop()` is called. The worker stops dequeuing new tasks, waits up to 30 s for in-flight tasks to finish, then cancels any that remain.

### Callable resolution

Tasks are stored by **dotted path string** (e.g., `"domains.users.service.UserService.send_welcome_email"`). On execution, the worker imports the module, resolves the class or function, and re-instantiates the class through the DI container so all dependencies are injected.

---

## 11. Database Layer

### `Database`

A thin SQLite wrapper with WAL mode, thread-local connections, and automatic `row_factory = sqlite3.Row`.

```python
from pillar.db import Database

db = Database("sqlite:///./app.db")

# Read
row  = db.query("SELECT * FROM users WHERE id = ?", (user_id,))   # → dict | None
rows = db.query_all("SELECT * FROM users WHERE active = ?", (1,)) # → list[dict]

# Write
rows_affected = db.execute("UPDATE users SET active = ? WHERE id = ?", (1, user_id))
last_id       = db.last_insert_id()

# Write + return inserted row
row = db.execute_returning(
    "INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "a@b.com")
)
```

### Connection management

One connection per thread, created on first access and reused. WAL mode (`PRAGMA journal_mode=WAL`) and foreign keys (`PRAGMA foreign_keys=ON`) are enabled at connection creation time.

### Automatic span recording

Every `query`, `query_all`, and `execute` call records a `db` span to the Pillar time-travel tracer (zero-cost when no trace is active).

### DI registration

```python
db = Database(url=config.database.url)
container.register_instance(Database, db)
```

This is done automatically during `Pillar._build()`. Repositories receive the `Database` instance via constructor injection.

---

## 12. Row-Level Security

`RLSDatabase` is a transparent proxy around `Database` that automatically appends `WHERE tenant_id = ?` to every `SELECT`, `UPDATE`, and `DELETE`, and stamps `tenant_id` on every `INSERT` — based on a per-request `contextvars.ContextVar`.

### Setup

```python
from pillar.db.rls import RLSDatabase
from pillar.di import container

db  = Database("sqlite:///./app.db")
rls = RLSDatabase(db, tenant_column="tenant_id")   # default column name
container.register_instance(Database, rls)         # repositories see RLS transparently
```

### JWT → RLS wiring

```python
from pillar.security import JWTMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# Pillar will call rls.set_tenant(payload["org_id"]) on every authenticated request:
jwt = JWTMiddleware(app, secret=SECRET, rls_tenant_claim="org_id")
```

`JWTMiddleware` extracts the claim from the decoded JWT payload and calls `pillar.db.rls.set_tenant(value)`, which sets the `contextvars.ContextVar`. Each asyncio task (i.e., each request) has its own copy — concurrent requests never bleed tenant context into each other.

### SQL injection rules

| Statement | Action |
|-----------|--------|
| `SELECT` | Injects `WHERE {col} = ?` or `AND {col} = ?` at depth-0 (subquery-safe) before `ORDER BY`/`GROUP BY`/`LIMIT` |
| `UPDATE` | Injects `WHERE {col} = ?` or `AND {col} = ?` at the end |
| `DELETE` | Same as UPDATE |
| `INSERT INTO t (cols) VALUES (?)` | Appends `, {col}` to column list and `?` to VALUES |
| DDL / PRAGMA / other | Passed through unchanged |

### Table detection

`RLSDatabase` scans `sqlite_master` once per startup to detect which tables have the tenant column. Call `rls.invalidate_cache()` after schema migrations to force a re-scan.

### Manual tenant management

```python
from pillar.db.rls import set_tenant, get_tenant, clear_tenant

set_tenant("acme-corp")
# ... run queries ...
clear_tenant()
```

---

## 13. Security & JWT Middleware

### `JWTMiddleware`

ASGI middleware that validates `Authorization: Bearer <token>` on every request.

```python
from pillar.security import JWTMiddleware

# Wrap the Pillar app before registering with uvicorn:
from starlette.middleware import Middleware
secured_app = JWTMiddleware(
    app,
    secret="your-jwt-secret",
    algorithms=["HS256"],                        # default
    public_paths={"/health", "/docs", "/ready"}, # default set is larger
    rls_tenant_claim="tenant_id",                # optional: enables RLS
)
```

On success, `scope["user"]` is set to the decoded JWT payload dict.  
On failure, returns `401 Unauthorized` with `WWW-Authenticate: Bearer realm="Pillar"`.

### Default public paths (no auth required)

`/health`, `/ready`, `/metrics`, `/dashboard`, `/docs`, `/redoc`, `/openapi.json`, `/guide`, `/queue/status`

### `encode_jwt` / `decode_jwt`

Minimal HS256 implementation with no external dependencies. Falls back to `python-jose` if installed (enables RS256, ES256, etc.).

```python
from pillar.security import encode_jwt, decode_jwt

token   = encode_jwt({"user_id": 42, "role": "admin"}, secret="...", expires_in=3600)
payload = decode_jwt(token, secret="...")  # raises JWTError on invalid/expired
```

### `RequireAuth` — DI marker

```python
from pillar import RequireAuth

@router.get("/me")
async def me(user: RequireAuth):
    return user  # the decoded JWT payload dict
```

*Note: `RequireAuth` injection is a sentinel pattern — full handler-level extraction requires combining with `JWTMiddleware`.*

---

## 14. Middleware Stack

Applied in this order (outermost → innermost, i.e., first applied = last to execute):

```
Incoming request:
  GZip → CORS → SecurityHeaders → RequestID → TraceMiddleware → Timing → core
```

All middleware follow the raw ASGI protocol and are compatible with any ASGI server.

### `GZipMiddleware` (Starlette)

Compresses responses ≥ 512 bytes. Outermost so it compresses everything including error responses.

### `CORSMiddleware` (Starlette)

Configurable via `[cors]` in `pillar.toml`. Default: `allow_origins=["*"]`.

### `SecurityHeadersMiddleware`

Injects hardened headers on every response:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |

### `RequestIDMiddleware`

- Reads `X-Request-ID` from incoming request; generates UUID4 if absent.
- Sets `scope["request_id"]`.
- Echoes the ID in `X-Request-ID` response header.

### `TraceMiddleware`

- Creates a `RequestTrace` in the in-memory `TraceStore` keyed by `scope["request_id"]`.
- Sets `contextvars.ContextVar` so nested DB calls record spans.
- Appends `X-Pillar-Trace-ID` to the response headers.

### `TimingMiddleware`

Appends `X-Response-Time: {ms}ms` to every response.

### Disabling middleware

```toml
[security]
add_request_id       = false
add_timing           = false
add_security_headers = false

[cors]
enabled = false
```

---

## 15. Metrics

In-memory, thread-safe metrics store. No external dependency.

### `GET /metrics` — JSON

```json
{
  "uptime_seconds": 3600.1,
  "total_requests": 14200,
  "total_errors": 42,
  "routes": {
    "GET /users/": {
      "requests": 5000,
      "errors": 1,
      "error_rate": 0.0002,
      "avg_ms": 4.2,
      "p99_ms": 18.7
    }
  }
}
```

### `GET /metrics?format=prometheus`

Prometheus exposition format. Labels: `route="{METHOD /path}"`.

Metrics exported:
- `pillar_uptime_seconds` (gauge)
- `pillar_requests_total` (counter, per route)
- `pillar_errors_total` (counter, per route)
- `pillar_response_time_avg_ms` (gauge, per route)

### `Metrics` API

```python
from pillar.metrics import metrics

metrics.record("GET /users/", status_code=200, duration_ms=4.2)
snap = metrics.snapshot()   # returns the dict above
text = metrics.prometheus_text()
```

The `Pillar` app calls `metrics.record()` automatically after every request.

---

## 16. Time-Travel Request Debugger

Pillar records a per-layer timing waterfall for every HTTP request, stored in a ring buffer of the last 1 000 requests.

### How it works

1. `TraceMiddleware` (in the middleware stack) opens a `RequestTrace` for each request.
2. `Database.query/query_all/execute` call `record_span("db.query", "db", ...)` automatically.
3. Custom spans can be added with the `span_context` context manager.
4. The trace is closed on response send with the final status code.

### Viewing traces

**Browser:** `/trace/` shows the 50 most recent requests as a clickable table. `/trace/{request-id}` shows a flamegraph-style waterfall with per-span timings.

**CLI:**
```bash
pillar trace                              # list recent traces
pillar trace <request-id>                 # ASCII waterfall in terminal
pillar trace <request-id> --open          # open HTML flamegraph in browser
pillar trace --host http://api.prod.com   # target a remote server
```

**JSON API:**
```bash
curl http://localhost:8000/trace/?format=json
curl http://localhost:8000/trace/{id} -H "Accept: application/json"
```

### Custom spans

```python
from pillar.tracer import span_context

async def charge_card(amount: float):
    with span_context("payment.stripe.charge", "handler", provider="stripe"):
        result = await stripe.charge(amount)
    return result
```

### Span layers and colors

| Layer | Color | Auto-recorded |
|-------|-------|---------------|
| `router` | Indigo | — |
| `di` | Purple | — |
| `handler` | Cyan | — |
| `db` | Green | Yes (Database methods) |
| `middleware` | Amber | — |
| `queue` | Orange | — |

### `TraceStore` API

```python
from pillar.tracer import get_store

store  = get_store()
trace  = store.get(trace_id)      # RequestTrace | None
recent = store.recent(limit=50)   # List[RequestTrace], newest first
```

---

## 17. OpenTelemetry Integration

Zero-config integration with the OpenTelemetry SDK. Completely no-op — no import errors, no warnings — when `opentelemetry-sdk` is not installed.

### Setup

```python
from pillar.telemetry import setup_telemetry

setup_telemetry(
    service_name="my-api",                      # or OTEL_SERVICE_NAME env var
    endpoint="http://localhost:4317",           # or OTEL_EXPORTER_OTLP_ENDPOINT
    exporter="otlp",                            # "otlp" | "otlp_http" | "console" | "none"
)
```

### `@trace_span` decorator

```python
from pillar.telemetry import trace_span

class UserService:
    @trace_span("service.get_user")
    async def get_user(self, user_id: int): ...

    @trace_span("repo.find_all", attributes={"db.system": "sqlite"})
    def find_all(self): ...
```

Works on both sync and async functions. Records exceptions and sets `StatusCode.ERROR` on failure.

### `TelemetryMiddleware`

ASGI middleware that creates an OTel span per HTTP request. Automatically records `http.method`, `http.route`, `http.status_code`, and `pillar.request_id`.

```python
# Applied via apply_middleware when telemetry is configured:
app = TelemetryMiddleware(app)
```

### Supported backends

| Backend | Config |
|---------|--------|
| Jaeger | `endpoint=http://jaeger:4317` (OTLP gRPC) |
| Grafana Tempo | `endpoint=http://tempo:4317` |
| Datadog Agent | `endpoint=http://datadog-agent:4317` |
| Grafana Cloud | `endpoint=https://otlp-gateway-...grafana.net/otlp` (HTTP) |
| Console (debug) | `exporter="console"` |

---

## 18. AI-Native Data Layer

`PillarAI` provides typed LLM extraction with no framework lock-in. Pass a Pydantic model, get a filled instance back.

```python
from pillar.ai import PillarAI
from pydantic import BaseModel

class ProductDetails(BaseModel):
    name: str
    price: float
    category: str
    in_stock: bool

ai = PillarAI()

# Returns a validated ProductDetails instance
product = await ai.extract(
    prompt="Extract product details from: 'Blue Nike Air Max 90, $129.99, sneakers, available'",
    model=ProductDetails,
    system="You are a product data extractor. Return only valid JSON.",
    retries=3,
    cache=True,   # cache by (prompt hash, model name) in memory
)
```

### Configuration

| Environment variable | Description |
|---------------------|-------------|
| `PILLAR_LLM_PROVIDER` | `"openai"` (default) or `"anthropic"` |
| `PILLAR_LLM_API_KEY` | API key (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) |
| `PILLAR_LLM_MODEL` | Model name (default: `gpt-4o-mini` / `claude-haiku-4-5`) |
| `PILLAR_LLM_BASE_URL` | Override base URL for OpenAI-compatible endpoints |
| `PILLAR_LLM_FALLBACK` | Fallback model name when circuit breaker opens |

### Circuit breaker

`PillarAI` includes a built-in circuit breaker with three states:

| State | Description |
|-------|-------------|
| `CLOSED` | Normal operation |
| `OPEN` | After `failure_threshold` (default: 3) consecutive failures; requests fail fast for `cooldown_seconds` (default: 30) |
| `HALF_OPEN` | After cooldown; next request is a test — success resets to CLOSED, failure reopens |

When the circuit opens, `PillarAI` automatically retries with `PILLAR_LLM_FALLBACK` model.

### Supported providers

- **OpenAI / OpenAI-compatible** — any endpoint that implements the `/chat/completions` API (OpenRouter, Together AI, Groq, local Ollama, etc.)
- **Anthropic** — native Messages API

Uses `httpx` for all requests (no openai SDK dependency).

---

## 19. Vector Search

`VectorRepository` provides a unified RAG (retrieval-augmented generation) interface with swappable backends.

```python
from pillar.vector import VectorRepository

repo = VectorRepository(collection="documents")   # defaults to SQLite backend

# Upsert
await repo.upsert("doc-1", embedding=[0.1, 0.2, ...], metadata={"title": "Spec"})
await repo.upsert_many([
    ("doc-2", [0.3, 0.4, ...], {"title": "Guide"}),
])

# Semantic search
results = await repo.search(query_vector=[0.1, 0.2, ...], top_k=5)
# [{"id": "doc-1", "score": 0.97, "metadata": {...}}, ...]

# Text search (requires embedder function)
async def embed(text: str) -> list[float]: ...
results = await repo.search_text("installation guide", embedder=embed, top_k=3)

# Count / delete
count = await repo.count()
await repo.delete("doc-1")
```

### Backends

| Backend | Config | Use case |
|---------|--------|----------|
| SQLite (default) | `PILLAR_VECTOR_BACKEND=sqlite` | Development, zero deps, cosine similarity scan |
| Qdrant | `PILLAR_VECTOR_BACKEND=qdrant` + `PILLAR_QDRANT_URL` | Production, ANN search, billions of vectors |

Switch backends by changing a single environment variable — no code changes.

**SQLite backend:** Stores embeddings as BLOBs (`struct.pack("f" * n, *embedding)`), performs brute-force cosine similarity scan. Suitable for collections up to ~100 K vectors.

---

## 20. Zero-Migration Schema Sync

`SchemaSync` compares Pydantic models to the live SQLite schema and applies safe, additive migrations automatically. Only `ADD COLUMN` is ever run — no destructive changes.

```python
from pillar.db.sync import SchemaSync
from pillar.db import Database
from pydantic import BaseModel

class User(BaseModel):
    id:     int
    name:   str
    email:  str
    bio:    str = ""    # NEW field

db   = Database("sqlite:///./app.db")
sync = SchemaSync(db)

# Development: create or update table
sync.ensure_table("users", User)
# → If 'bio' column is missing: ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''

# Production startup: validate contract (no DDL)
sync.validate_contract("users", User)
# → Raises PillarContractError if any required field is missing from the DB

# Show diff without applying
diff = sync.diff("users", User)
# → {"missing_in_db": ["bio"], "extra_in_db": ["legacy_field"]}
```

### Type mapping

| Python type | SQLite type |
|-------------|-------------|
| `int` | `INTEGER` |
| `float` | `REAL` |
| `bool` | `INTEGER` |
| `bytes` | `BLOB` |
| `str` | `TEXT` |
| `Optional[X]` | Same as X (nullable) |
| `list`, `dict` | `TEXT` (JSON) |
| Everything else | `TEXT` |

### Auto primary key

If the first `int` field in the model is named `id`, it becomes `INTEGER PRIMARY KEY AUTOINCREMENT`.

---

## 21. Architecture Enforcer

Pillar validates DDD layer import rules using AST analysis at every server startup. If any layer imports from a forbidden layer, the server refuses to start with a clear error message.

### Rules

| File | May import from |
|------|----------------|
| `repository.py` | `schemas` only |
| `service.py` | `repository` + `schemas` |
| `router.py` | `service` + `schemas` |

### Example violation

```
ArchitectureViolationError:
  File   : domains/users/repository.py
  Layer  : repository.py
  Imports: 'domains.users.service'
  Problem: 'repository' must not import from 'service'.
  Allowed: ['schemas']

  Fix: move the logic that needs 'service' into a layer that is allowed to use it.
```

### Configuration

```python
ArchitectureEnforcer(domains_dir="domains").validate()
```

Runs automatically in `Pillar._build()` before the server accepts connections. Pass `domains_dir=""` or ensure `domains/` does not exist to skip validation.

---

## 22. OpenAPI & Documentation

### OpenAPI 3.1 spec

Built lazily on first request to `/openapi.json` from all registered `RouteEntry` objects. Pydantic models are converted via `model_json_schema()`. The spec includes:
- Title, version, description
- All HTTP routes (grouped by tags)
- WebSocket routes (in the `x-websockets` extension)
- Request body schemas from Pydantic models
- Response schemas from `response_model`
- Deprecated / hidden route flags

### `/docs` — Custom Pillar API Explorer

A zero-CDN, zero-dependency split-pane explorer:
- **Left pane:** Route list grouped by tags
- **Right pane:** Route detail (method, path, params, body schema, example response)
- **Try it:** Live request runner — fills in params, sends the request, displays response with syntax highlighting

### `/redoc`

ReDoc documentation UI.

### `/guide`

Interactive getting-started guide with code examples for installing, running, and making first requests.

### Disabling docs

```toml
[docs]
enabled = false
```

Or at runtime via `PILLAR_DOCS_ENABLED=0`.

---

## 23. Response Helpers

```python
from pillar import ok, created, no_content, paginate, problem

# 200 success envelope
return ok(data={"id": 1, "name": "Alice"}, message="User retrieved")
# → {"success": true, "data": {...}, "message": "..."}

# 201 created, optional Location header
return created(data=user.dict(), location=f"/users/{user.id}")

# 204 no content
return no_content()

# Paginated list
return paginate(items=users, total=150, page=2, page_size=20)
# → {"items": [...], "total": 150, "page": 2, "page_size": 20,
#    "pages": 8, "has_next": true, "has_prev": true}

# RFC 7807 Problem Details
return problem(
    title="Validation Failed",
    detail="The 'email' field is required.",
    status=422,
    invalid_field="email",   # extra fields allowed
)
# Content-Type: application/problem+json
```

---

## 24. Exceptions

All Pillar exceptions extend `PillarError` and are automatically caught by the request dispatcher and converted to JSON responses.

| Exception | Status | Default detail |
|-----------|--------|---------------|
| `PillarError` | 500 | `"Internal server error"` |
| `NotFoundError` | 404 | `"Resource not found"` |
| `UnauthorizedError` | 401 | `"Authentication required"` |
| `ForbiddenError` | 403 | `"Permission denied"` |
| `ValidationError` | 422 | `"Validation failed"` |
| `ConflictError` | 409 | `"Resource already exists"` |
| `PillarContractError` | 500 | `"Contract violation"` |
| `ArchitectureViolationError` | 500 | `"Architecture violation"` |

```python
from pillar import NotFoundError, ConflictError

def get_user(user_id: int):
    user = db.query("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    return user

def create_user(data: UserCreate):
    if db.query("SELECT 1 FROM users WHERE email = ?", (data.email,)):
        raise ConflictError("Email already registered")
    ...
```

Custom exceptions:

```python
class PaymentFailedError(PillarError):
    status_code = 402
    detail = "Payment processing failed"
```

---

## 25. CLI Reference

### `pillar run`

```bash
pillar run main:app
pillar run main:app --reload              # hot-reload (development)
pillar run main:app --host 0.0.0.0 --port 8080
pillar run main:app --workers 4           # production multi-process
```

### `pillar new`

```bash
pillar new my_project
```

Scaffolds:
```
my_project/
├── main.py
├── pillar.toml
├── requirements.txt
└── domains/users/
    ├── schemas.py
    ├── repository.py
    ├── service.py
    └── router.py
```

### `pillar routes`

```bash
pillar routes main:app
```

Prints all registered HTTP and WebSocket routes with method, path, and tags.

### `pillar info`

```bash
pillar info
```

Prints framework version, Python version, Rust engine status, and router type.

### `pillar generate`

```bash
pillar generate dockerfile               # multi-stage production Dockerfile
pillar generate domain orders            # scaffold domains/orders/ with all 4 files
pillar generate client                   # TypeScript client from openapi.json
pillar generate client --out src/api/client.ts --url https://api.example.com
```

#### TypeScript client generation

Reads `openapi.json` (or a local path/URL) and generates a fully-typed TypeScript fetch client with:
- `interface` types for every Pydantic schema
- One typed function per endpoint with path/query/body parameters
- A shared `_fetch<T>()` helper with error handling
- Zero dependencies (native `fetch`)

### `pillar trace`

```bash
pillar trace                              # list recent traces (HTML link)
pillar trace <request-id>                 # ASCII waterfall in terminal
pillar trace <request-id> --open          # open HTML flamegraph in browser
pillar trace --list --host http://api:8000
```

---

## 26. Rust Engine

The optional Rust engine provides two classes compiled via PyO3/Maturin:

### `PillarRouter`

HTTP router backed by the [`matchit`](https://crates.io/crates/matchit) crate — a radix-tree with O(log n) route matching.

```python
from _pillar_engine import PillarRouter

router = PillarRouter()
router.add_route("GET", "/users/{user_id}", "handler_id_string")
match = router.match_route("GET", "/users/42")
# → {"handler_id": "handler_id_string", "params": {"user_id": "42"}}
count = router.route_count()
```

All methods are protected by a `Mutex<RouterInner>` so the object is safe to share across Python threads.

### `PillarQueue`

Persistent task queue backed by SQLite in WAL mode with bundled `libsqlite3` (via `rusqlite-bundled`).

```python
from _pillar_engine import PillarQueue

queue = PillarQueue("pillar_queue.db")
task_id = queue.enqueue("func.path", '["arg1"]', '{"key": "val"}', 3, None)
tasks   = queue.dequeue(10)             # list of dicts
queue.mark_complete(task_id)
queue.mark_failed(task_id, "err msg", True)  # True = retry
```

Tasks are stored with UUIDs, timestamps (via `chrono`), and status fields. Concurrent access is safe via the bundled SQLite WAL mode.

### `engine_version()`

```python
from _pillar_engine import engine_version
print(engine_version())   # "Pillar Rust Engine v0.1.0"
```

### Python fallback

When the compiled extension is not present, Pillar uses:
- `_PythonFallbackRouter` — regex-based route matching, otherwise identical API
- `_PythonQueueBackend` — pure-Python SQLite queue backend

---

## 27. Testing

### Test setup

```python
# tests/conftest.py
import pytest
from starlette.testclient import TestClient
from pillar.di import container

@pytest.fixture(autouse=True)
def reset_di():
    container.reset()
    yield
    container.reset()

@pytest.fixture
def client():
    from main import app
    return TestClient(app)
```

### Testing handlers

```python
def test_create_user(client):
    resp = client.post("/users/", json={"name": "Alice", "email": "a@b.com"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Alice"

def test_not_found(client):
    resp = client.get("/users/9999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "User 9999 not found"
```

### Testing with DI overrides

```python
from pillar.di import container
from unittest.mock import MagicMock

def test_with_mock_service(client):
    mock_service = MagicMock()
    mock_service.list_users.return_value = [{"id": 1, "name": "Alice"}]
    container.register_instance(UserService, mock_service)

    resp = client.get("/users/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

### Testing background tasks

```python
from pillar.queue.storage import TaskStorage

def test_task_queued(client):
    TaskStorage.reset_instance()
    storage = TaskStorage.get_instance(":memory:")

    resp = client.post("/users/", json={"name": "Bob", "email": "b@b.com"})
    assert resp.status_code == 201
    assert storage.pending_count() == 1   # welcome email was queued
```

### Run tests

```bash
pip install pillar-framework[dev]
pytest tests/ -q
```

The test suite runs fully in pure-Python mode — no Rust compilation required for CI.

---

## 28. Deployment

### Development

```bash
pillar run main:app --reload
```

### Production (single process)

```bash
pillar run main:app --host 0.0.0.0 --port 8000
```

### Production (multi-process, uvicorn)

```bash
pillar run main:app --workers 4
# Note: Background task worker runs in each worker process. Use a shared
# queue backend (Redis / PostgreSQL) when using multiple workers to avoid
# duplicate task execution.
```

### Docker

```bash
pillar generate dockerfile
docker build -t my-api .
docker run -p 8000:8000 -e DATABASE_URL=sqlite:///./data/app.db my-api
```

Generated `Dockerfile` uses a multi-stage build:
1. **Build stage:** `python:3.12-slim` + installs dependencies
2. **Runtime stage:** Minimal image, non-root user, no build tools

### Environment-based config

For production, prefer environment variables over `pillar.toml` to avoid secrets in source control:

```bash
DATABASE_URL=postgresql://user:pass@db:5432/myapp
DEBUG=false
PORT=8000
JWT_SECRET=<random-256-bit-secret>
PILLAR_DOCS_ENABLED=0          # disable docs in production
PILLAR_RLS_CLAIM=tenant_id     # enable RLS
OTEL_SERVICE_NAME=my-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

### Health checks

```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/ready"]
  interval: 10s
  timeout: 3s
  retries: 3
  start_period: 5s
```

---

## 29. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./app.db` | Database connection string |
| `DEBUG` | `false` | Enable debug mode |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server bind port |
| `QUEUE_DRIVER` | `sqlite` | `"sqlite"` \| `"redis"` |
| `REDIS_URL` | `` | Redis connection string for queue |
| `PILLAR_DOCS_ENABLED` | `true` | Set `"0"` or `"false"` to disable |
| `JWT_SECRET` | `changeme` | Secret for HS256 JWT signing |
| `PILLAR_RLS_CLAIM` | `` | JWT claim name to use as tenant ID |
| `PILLAR_LLM_PROVIDER` | `openai` | `"openai"` \| `"anthropic"` |
| `PILLAR_LLM_API_KEY` | `` | LLM API key |
| `PILLAR_LLM_MODEL` | `gpt-4o-mini` | Default LLM model |
| `PILLAR_LLM_BASE_URL` | `` | OpenAI-compatible endpoint URL |
| `PILLAR_LLM_FALLBACK` | `` | Fallback model when circuit opens |
| `PILLAR_VECTOR_BACKEND` | `sqlite` | `"sqlite"` \| `"qdrant"` |
| `PILLAR_QDRANT_URL` | `` | Qdrant server URL |
| `OTEL_SERVICE_NAME` | `pillar-app` | OTel service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `` | OTel collector endpoint |
| `OTEL_TRACES_EXPORTER` | `` | `"otlp"` \| `"console"` \| `"none"` |

---

## 30. Public API Surface

Everything exported from `pillar/__init__.py`:

### Core

```python
from pillar import Pillar, Router, Controller, action, background_task
```

### DI

```python
from pillar import container, DIContainer
```

### Config

```python
from pillar import PillarConfig
```

### Response helpers

```python
from pillar import ok, created, no_content, paginate, problem, PaginatedResponse
```

### Exceptions

```python
from pillar import (
    PillarError,
    NotFoundError,
    UnauthorizedError,
    ForbiddenError,
    ValidationError,
    ConflictError,
    PillarContractError,
    ArchitectureViolationError,
)
```

### Metrics

```python
from pillar import metrics
```

### Security

```python
from pillar import JWTMiddleware, encode_jwt, decode_jwt, RequireAuth
```

### AI

```python
from pillar import PillarAI
```

### Telemetry (OTel)

```python
from pillar import setup_telemetry, trace_span, TelemetryMiddleware
```

### Time-travel tracer (built-in)

```python
from pillar import span_context, current_trace_id, record_span
```

### Auto RLS

```python
from pillar import RLSDatabase, set_tenant, get_tenant
```

### Database

```python
from pillar.db import Database
from pillar.db.rls import RLSDatabase
from pillar.db.sync import SchemaSync
```

### Vector search

```python
from pillar.vector import VectorRepository
```

### Queue

```python
from pillar.queue.storage import TaskStorage
from pillar.queue.worker import TaskWorker
```

---

## Appendix — Dependency matrix

| Feature | Required dependencies | Optional |
|---------|----------------------|---------|
| Core framework | `starlette`, `uvicorn[standard]`, `pydantic`, `click`, `anyio` | — |
| Rust engine | `maturin` (build-time), Rust toolchain | Built as `.pyd`/`.so` wheel |
| OpenTelemetry | — | `opentelemetry-sdk`, `opentelemetry-exporter-otlp` |
| RS256 JWT | — | `python-jose[cryptography]` |
| Qdrant vector | — | `qdrant-client` |
| AI extraction | — | `httpx` (usually already installed via starlette) |
| PostgreSQL | — | `asyncpg` |
| Redis queue | — | `redis` |
| TOML config | `tomllib` (Python 3.11+) | `tomli` (Python 3.10) |

---

*Specification generated 2026-06-25. Repository: https://github.com/Zecrari/pillar*
