"""Tests for the Pillar Queue (background task system)."""
import json
import os
import tempfile
import pytest

from pillar.queue.storage import TaskStorage
from pillar.queue.decorators import background_task


# ── TaskStorage ──────────────────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    db = str(tmp_path / "test_queue.db")
    s = TaskStorage(db_path=db)
    yield s
    TaskStorage.reset_instance()


def test_enqueue_returns_task_id(storage):
    task_id = storage.enqueue("my.module.func", args=(1, 2), kwargs={"x": 3})
    assert isinstance(task_id, str)
    assert len(task_id) > 0


def test_pending_count(storage):
    assert storage.pending_count() == 0
    storage.enqueue("a.b.c", args=())
    storage.enqueue("a.b.d", args=())
    assert storage.pending_count() == 2


def test_dequeue_claims_tasks(storage):
    storage.enqueue("a.b.func", args=("hello",), kwargs={})
    tasks = storage.dequeue(limit=10)
    assert len(tasks) == 1
    assert tasks[0]["func_path"] == "a.b.func"
    assert tasks[0]["args"] == ["hello"]
    # Should no longer be pending
    assert storage.pending_count() == 0


def test_dequeue_respects_limit(storage):
    for i in range(5):
        storage.enqueue(f"mod.func{i}")
    tasks = storage.dequeue(limit=3)
    assert len(tasks) == 3
    assert storage.pending_count() == 2


def test_mark_complete(storage):
    task_id = storage.enqueue("mod.func")
    storage.dequeue(limit=1)
    storage.mark_complete(task_id)
    # Pending stays 0; no error
    assert storage.pending_count() == 0


def test_mark_failed_no_retry(storage):
    task_id = storage.enqueue("mod.func", retries=0)
    storage.dequeue(limit=1)
    storage.mark_failed(task_id, "boom", retry=False)
    assert storage.pending_count() == 0


def test_mark_failed_with_retry(storage):
    task_id = storage.enqueue("mod.func", retries=2)
    storage.dequeue(limit=1)
    storage.mark_failed(task_id, "boom", retry=True)
    # Should be re-queued as pending
    assert storage.pending_count() == 1


def test_retry_count_decrements(storage):
    task_id = storage.enqueue("mod.func", retries=2)
    tasks = storage.dequeue(limit=1)
    assert tasks[0]["retries_left"] == 2
    storage.mark_failed(task_id, "err", retry=True)
    tasks2 = storage.dequeue(limit=1)
    assert tasks2[0]["retries_left"] == 1


# ── @background_task decorator ───────────────────────────────────────

def test_background_task_decorator_queues(tmp_path):
    TaskStorage.reset_instance()
    db = str(tmp_path / "deco_queue.db")

    # Force fresh instance with test db
    storage = TaskStorage(db_path=db)
    TaskStorage._instance = storage

    @background_task(retries=1)
    def my_task(x: int, y: int) -> int:
        return x + y  # never called directly

    result = my_task(3, 4)
    assert isinstance(result, str)  # returns task_id
    assert storage.pending_count() == 1

    tasks = storage.dequeue(limit=1)
    assert tasks[0]["args"] == [3, 4]
    assert tasks[0]["retries_left"] == 1

    TaskStorage.reset_instance()


def test_background_task_on_class_method(tmp_path):
    TaskStorage.reset_instance()
    db = str(tmp_path / "class_queue.db")
    storage = TaskStorage(db_path=db)
    TaskStorage._instance = storage

    class MyService:
        @background_task(retries=2)
        def process(self, item: str) -> None:
            pass

    svc = MyService()
    svc.process("hello")

    assert storage.pending_count() == 1
    tasks = storage.dequeue(limit=1)
    assert "MyService" in tasks[0]["func_path"]
    assert tasks[0]["args"] == ["hello"]

    TaskStorage.reset_instance()


# ── Rust PillarQueue ─────────────────────────────────────────────────

def test_rust_queue(tmp_path):
    try:
        from _pillar_engine import PillarQueue
    except ImportError:
        pytest.skip("Rust engine not compiled")

    db = str(tmp_path / "rust_queue.db")
    q = PillarQueue(db)

    task_id = q.enqueue("my.func", "[]", "{}", 3)
    assert isinstance(task_id, str)
    assert q.pending_count() == 1

    tasks = q.dequeue(10)
    assert len(tasks) == 1
    assert tasks[0]["func_path"] == "my.func"
    assert q.pending_count() == 0

    q.mark_complete(task_id)
    q.mark_failed("fake-id", "error", False)  # no-op for unknown id
