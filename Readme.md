# 🏛️ Pillar: The Production-Grade, Rust-Powered Python Backend Framework

**Pillar** is an opinionated, high-performance backend framework for Python, powered by a Rust engine. It is designed to solve the "Day 100" architecture problem, eliminate the need for external task queues like Celery, and provide a native, secure runtime for AI agents.

## 📖 Table of Contents
1. [Core Philosophy](#1-core-philosophy)
2. [Architecture Overview](#2-architecture-overview)
3. [The "Game Changer" Features](#3-the-game-changer-features)
4. [Project Structure (The Opinionated Standard)](#4-project-structure)
5. [Quickstart & Code Examples](#5-quickstart--code-examples)
6. [Production & Deployment](#6-production--deployment)
7. [Development Setup](#7-development-setup)

---

## 1. Core Philosophy

FastAPI is incredible for Day 1. But on Day 100, when your app has 150 endpoints, FastAPI gives you zero guidance on how to organize your code. Developers end up with massive `main.py` files, circular imports, and spaghetti code.

**Pillar's Philosophy:**
*   **Opinionated by Design:** We enforce Clean Architecture (Routers → Services → Repositories). If you break the rules, Pillar stops you before you deploy.
*   **Rust for the Heavy Lifting:** Python is for business logic. Rust handles routing, dependency injection, JSON serialization, and background tasks at C-speeds.
*   **Batteries-Included for Modern Apps:** No more duct-taping Celery for background tasks or LangChain for AI. Pillar has them built into the core.

---

## 2. Architecture Overview

Pillar uses a hybrid **Python/Rust architecture** via `PyO3` and `Maturin`.

```text
[ Client Request ]
       │
       ▼
┌─────────────────────────────────────────────┐
│  🦀 The Rust Engine (The Core)              │
│  • HTTP Parsing & Routing (Radix Tree)      │
│  • Dependency Injection Resolution          │
│  • Async/Sync "Smart Bridge"                │
│  • Pillar Queue (Background Tasks)          │
└──────────────────────┬──────────────────────┘
                       │ (Zero-cost FFI boundary)
                       ▼
┌─────────────────────────────────────────────┐
│  🐍 The Python Layer (Business Logic)       │
│  • Domain Routers (HTTP definitions)        │
│  • Domain Services (Business rules)         │
│  • Domain Repositories (Database queries)   │
└─────────────────────────────────────────────┘
```

---

## 3. The "Game Changer" Features

### 🚀 Pillar Queue (The Celery Killer)
Stop setting up Redis, RabbitMQ, and separate worker processes just to send an email.
*   **Zero-Dependency:** Uses a highly optimized, embedded Rust database (SQLite in WAL mode) out-of-the-box. Tasks survive server restarts.
*   **Scale on Demand:** Need massive scale? Flip a config switch in `pillar.toml` to use Redis or Postgres.
*   **Built-in Cron:** Schedule recurring tasks natively without external tools.

### 🤖 Pillar Agent Runtime (AI-Native)
Build AI agents without memory leaks or crashing your main server.
*   **Native Streaming:** First-class support for LLM token streaming over WebSockets and HTTP.
*   **Tool Sandboxing:** If an AI agent calls a Python function to execute code or query a DB, Pillar runs it in an isolated, memory-safe Rust sandbox.

### 🛡️ Compile-Time Contract Validation
Catch database errors before you even start the server.
*   The Rust engine reads your Python type hints and Pydantic models at startup.
*   If your DB returns a `String` but your API expects an `Integer`, Pillar throws a `PillarContractError` and refuses to boot.

### 🌉 The "Smart Bridge" (Async/Sync Harmony)
Never worry about blocking the event loop.
*   Write synchronous database queries? The Rust engine automatically routes them to a dedicated, highly optimized thread pool.
*   Write async LLM calls? They go straight to the event loop. You just write Python; Pillar handles the execution context.

---

## 4. Project Structure

Pillar enforces a strict Domain-Driven Design (DDD) folder structure. If a `router.py` tries to import directly from a `repository.py`, Pillar will throw an `ArchitectureViolationError` on startup.

```text
my_pillar_app/
├── domains/                  # Your business logic, separated by domain
│   ├── users/
│   │   ├── router.py         # ONLY HTTP logic and Pydantic schemas
│   │   ├── service.py        # ONLY business rules and orchestration
│   │   ├── repository.py     # ONLY database queries
│   │   └── schemas.py        # Pydantic models for this domain
│   └── billing/
│       ├── router.py
│       ├── service.py
│       └── repository.py
├── core/                     # Framework and infrastructure setup
│   ├── config.py             # Environment variables & settings
│   ├── database.py           # DB connection pooling
│   └── security.py           # Auth & JWT logic
├── main.py                   # The entry point (Usually just 3 lines of code)
└── pillar.toml               # Framework configuration (Queue, AI, DB settings)
```

---

## 5. Quickstart & Code Examples

### Step 1: Define the Repository (Database)
```python
# domains/users/repository.py
from pillar.db import Database

class UserRepository:
    def __init__(self, db: Database):
        self.db = db

    # Notice: No 'async' needed. The Smart Bridge handles it!
    def get_by_id(self, user_id: int) -> dict | None:
        return self.db.query("SELECT * FROM users WHERE id = ?", (user_id,))
```

### Step 2: Define the Service (Business Logic)
```python
# domains/users/service.py
from .repository import UserRepository
from pillar.exceptions import NotFoundError

class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo # Auto-injected by Pillar!

    def get_user_profile(self, user_id: int):
        user = self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("User not found")
        
        # Business logic here (e.g., calculate subscription status)
        return {**user, "status": "active"}
```

### Step 3: Define the Router (HTTP)
```python
# domains/users/router.py
from pillar import Router
from .service import UserService

router = Router(prefix="/users", tags=["Users"])

# Notice: No ugly Depends()! Pillar auto-injects the UserService.
@router.get("/{user_id}")
async def get_user(user_id: int, service: UserService):
    return service.get_user_profile(user_id)
```

### Step 4: Add a Background Task (No Celery!)
```python
# domains/users/service.py
from pillar import background_task

class UserService:
    # ... previous code ...

    @background_task(retries=3)
    def send_welcome_email(self, user_email: str):
        # This runs in the embedded Rust task queue!
        # No Redis required.
        email_client.send(user_email, "Welcome to Pillar!")
```

### Step 5: Start the App
```python
# main.py
from pillar import Pillar
from domains.users.router import router as users_router

app = Pillar(title="My Awesome App")
app.include_router(users_router)

# Run with: pillar run main:app
```

---

## 6. Production & Deployment

Pillar is built for the enterprise. 

### Observability (Zero-Config)
Pillar automatically instruments your app with **OpenTelemetry**.
*   Every HTTP request, DB query, and background task is traced.
*   Export directly to Datadog, Jaeger, or Grafana by adding your endpoint to `pillar.toml`.

### Graceful Shutdown
When you deploy a new version, Pillar intercepts the `SIGTERM` signal. It stops accepting new requests, waits for all active background tasks (Pillar Queue) to finish, and shuts down cleanly. **Zero dropped connections.**

### Docker Deployment
Pillar includes a highly optimized, multi-stage `Dockerfile` generator.
```bash
pillar generate dockerfile
docker build -t my-pillar-app .
docker run -p 8000:8000 my-pillar-app
```

---

## 7. Development Setup

*Note: End-users installing via `pip` do NOT need Rust. This is only for developing the Pillar framework itself.*

### Prerequisites
1.  **Python 3.10+**
2.  **Rust** (Install via [rustup.rs](https://rustup.rs/))
3.  **Maturin** (`pip install maturin`)

### Initialize the Project
```bash
# Create the project using Maturin
maturin new pillar --bindings pyo3
cd pillar

# Install in development mode
pip install -e .
```

### Run the Test Suite
```bash
# Run Python tests
pytest tests/

# Run Rust unit tests
cargo test
```

---

## 🗺️ The Roadmap

*   **Phase 1: The Rust Foundation** - HTTP Router, PyO3 bindings, basic Python decorators.
*   **Phase 2: Architecture & DI** - Dependency Injection container, Smart Bridge (Async/Sync), Folder Enforcer.
*   **Phase 3: The Game Changers** - Pillar Queue (Embedded task queue), Compile-time validation.
*   **Phase 4: AI & Production** - Pillar Agent Runtime, OpenTelemetry integration, CLI tooling.
*   **Phase 5: Public Beta** - Launch on Reddit, HackerNews, and PyPI.
