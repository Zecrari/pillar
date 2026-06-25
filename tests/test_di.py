"""Tests for the Dependency Injection container."""
import pytest
from pillar.di import DIContainer


class FakeDB:
    pass


class FakeRepo:
    def __init__(self, db: FakeDB) -> None:
        self.db = db


class FakeService:
    def __init__(self, repo: FakeRepo) -> None:
        self.repo = repo


class NoHints:
    def __init__(self, x, y):  # no type annotations
        self.x = x
        self.y = y


# ── Resolution ──────────────────────────────────────────────────────

def test_resolve_simple(fresh_container):
    db = fresh_container.resolve(FakeDB)
    assert isinstance(db, FakeDB)


def test_resolve_one_level_deep(fresh_container):
    repo = fresh_container.resolve(FakeRepo)
    assert isinstance(repo, FakeRepo)
    assert isinstance(repo.db, FakeDB)


def test_resolve_two_levels_deep(fresh_container):
    svc = fresh_container.resolve(FakeService)
    assert isinstance(svc, FakeService)
    assert isinstance(svc.repo, FakeRepo)
    assert isinstance(svc.repo.db, FakeDB)


# ── Singleton behaviour ─────────────────────────────────────────────

def test_singleton_returns_same_instance(fresh_container):
    a = fresh_container.resolve(FakeService)
    b = fresh_container.resolve(FakeService)
    assert a is b


def test_register_instance(fresh_container):
    db = FakeDB()
    fresh_container.register_instance(FakeDB, db)
    resolved = fresh_container.resolve(FakeDB)
    assert resolved is db


def test_register_instance_propagates_to_deps(fresh_container):
    db = FakeDB()
    fresh_container.register_instance(FakeDB, db)
    repo = fresh_container.resolve(FakeRepo)
    assert repo.db is db


# ── Binding ─────────────────────────────────────────────────────────

def test_bind_interface_to_concrete(fresh_container):
    class AbstractRepo:
        pass

    fresh_container.bind(AbstractRepo, FakeRepo)
    # FakeRepo requires FakeDB — that should be auto-wired
    r = fresh_container.resolve(AbstractRepo)
    assert isinstance(r, FakeRepo)


# ── Reset ───────────────────────────────────────────────────────────

def test_reset_clears_singletons(fresh_container):
    svc1 = fresh_container.resolve(FakeService)
    fresh_container.reset()
    svc2 = fresh_container.resolve(FakeService)
    assert svc1 is not svc2


# ── Edge cases ──────────────────────────────────────────────────────

def test_class_without_type_hints_resolves(fresh_container):
    # Parameters without annotations are skipped; the class is constructed
    # with no kwargs.  Python will raise if required positional args remain,
    # but classes that default everything should work.
    class NoDeps:
        def __init__(self) -> None:
            self.ok = True

    obj = fresh_container.resolve(NoDeps)
    assert obj.ok is True


def test_contains(fresh_container):
    assert FakeDB not in fresh_container
    fresh_container.resolve(FakeDB)
    assert FakeDB in fresh_container
