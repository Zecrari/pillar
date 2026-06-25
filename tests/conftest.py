"""Shared fixtures for the Pillar test suite."""
import pytest
from httpx import AsyncClient, ASGITransport

from pillar import Pillar, Router
from pillar.di import DIContainer


@pytest.fixture
def fresh_container():
    """A clean DI container for each test."""
    c = DIContainer()
    yield c
    c.reset()


@pytest.fixture
def app_factory():
    """
    Returns a factory function that creates a Pillar ASGI app wired with
    the given routers and an in-memory SQLite database.
    """
    def _make(*routers, db_url: str = "sqlite:///:memory:"):
        from pillar.db import Database
        c = DIContainer()
        db = Database(url=db_url)
        c.register_instance(Database, db)

        a = Pillar(title="Test App", config_path="nonexistent.toml", container=c)
        for r in routers:
            a.include_router(r)
        a._build()
        a._built = True
        return a, c

    return _make


@pytest.fixture
async def http_client(app_factory):
    """An httpx AsyncClient ready to call the default test app."""
    router = Router(prefix="/test")

    @router.get("/ping")
    async def ping():
        return {"pong": True}

    app, _ = app_factory(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
