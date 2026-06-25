"""Tests for routing, injection, and the Smart Bridge."""
import pytest
from httpx import AsyncClient, ASGITransport
from pydantic import BaseModel

from pillar import Pillar, Router
from pillar.db import Database
from pillar.di import DIContainer
from pillar.exceptions import NotFoundError


# ── Helpers ─────────────────────────────────────────────────────────

def make_app(*routers, db_url="sqlite:///:memory:"):
    c = DIContainer()
    db = Database(url=db_url)
    c.register_instance(Database, db)
    a = Pillar(title="Test", config_path="nonexistent.toml", container=c)
    for r in routers:
        a.include_router(r)
    a._build()
    return a


# ── Basic GET ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_returns_json():
    r = Router()

    @r.get("/hello")
    async def hello():
        return {"hello": "world"}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/hello")
    assert resp.status_code == 200
    assert resp.json() == {"hello": "world"}


# ── Path parameters ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_path_param_int_coercion():
    r = Router(prefix="/items")

    @r.get("/{item_id}")
    async def get_item(item_id: int):
        return {"id": item_id, "type": type(item_id).__name__}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/items/99")
    assert resp.json() == {"id": 99, "type": "int"}


# ── Query parameters ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_params():
    r = Router()

    @r.get("/search")
    async def search(q: str = ""):
        return {"query": q}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/search?q=pillar")
    assert resp.json()["query"] == "pillar"


# ── Request body (Pydantic) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_pydantic_body():
    class Item(BaseModel):
        name: str
        price: float

    r = Router()

    @r.post("/items")
    async def create(data: Item):
        return {"name": data.name, "price": data.price}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/items", json={"name": "Gadget", "price": 9.99})
    assert resp.status_code == 201
    assert resp.json() == {"name": "Gadget", "price": 9.99}


# ── DI injection ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_di_auto_injection():
    class GreetService:
        def greet(self, name: str) -> str:
            return f"Hello, {name}!"

    r = Router()

    @r.get("/greet/{name}")
    async def greet(name: str, svc: GreetService):
        return {"message": svc.greet(name)}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/greet/Pillar")
    assert resp.json()["message"] == "Hello, Pillar!"


# ── Exception mapping ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_not_found_error_maps_to_404():
    r = Router()

    @r.get("/missing")
    async def missing():
        raise NotFoundError("Gone")

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Gone"


# ── Health endpoint ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint():
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ── 404 for unknown route ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_route_404():
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/does-not-exist")
    assert resp.status_code == 404


# ── Smart Bridge: sync handler ───────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_handler_smart_bridge():
    r = Router()

    @r.get("/sync")
    def sync_handler():  # NOT async — Smart Bridge routes to thread pool
        return {"sync": True}

    app = make_app(r)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/sync")
    assert resp.json() == {"sync": True}


# ── Rust router ─────────────────────────────────────────────────────

def test_rust_router_available():
    try:
        from _pillar_engine import PillarRouter
        r = PillarRouter()
        r.add_route("GET", "/users/{id}", "users.get")
        m = r.match_route("GET", "/users/42")
        assert m["handler_id"] == "users.get"
        assert m["params"]["id"] == "42"
        assert r.route_count() == 1
    except ImportError:
        pytest.skip("Rust engine not compiled")
