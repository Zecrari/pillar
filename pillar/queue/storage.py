from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from _pillar_engine import PillarQueue as _RustQueue
    _RUST_AVAILABLE = True
except ImportError:
    _RustQueue = None
    _RUST_AVAILABLE = False


class TaskStorage:
    """
    Persistent task queue storage.

    Uses the compiled Rust ``PillarQueue`` (SQLite WAL, bundled libsqlite3)
    when the engine is built.  Falls back to a pure-Python implementation
    so the framework boots without the compiled extension (e.g., CI).
    """

    _instance: Optional["TaskStorage"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = "pillar_queue.db") -> None:
        self.db_path = db_path
        if _RUST_AVAILABLE:
            self._backend: Any = _RustQueue(db_path)
            self._rust = True
        else:
            self._backend = _PythonQueueBackend(db_path)
            self._rust = False

    @classmethod
    def get_instance(cls, db_path: str = "pillar_queue.db") -> "TaskStorage":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        func_path: str,
        args: tuple = (),
        kwargs: dict = None,
        retries: int = 0,
        scheduled_at: Optional[str] = None,
    ) -> str:
        return self._backend.enqueue(
            func_path,
            json.dumps(list(args)),
            json.dumps(kwargs or {}),
            retries,
            scheduled_at,
        )

    def dequeue(self, limit: int = 10) -> List[Dict[str, Any]]:
        raw = self._backend.dequeue(limit)
        result = []
        for task in raw:
            result.append({
                "id": task["id"],
                "func_path": task["func_path"],
                "args": json.loads(task["args_json"]),
                "kwargs": json.loads(task["kwargs_json"]),
                "retries_left": task["retries_left"],
            })
        return result

    def mark_complete(self, task_id: str) -> None:
        self._backend.mark_complete(task_id)

    def mark_failed(self, task_id: str, error: str, retry: bool = False) -> None:
        self._backend.mark_failed(task_id, error, retry)

    def pending_count(self) -> int:
        return self._backend.pending_count()

    def failed_count(self) -> int:
        try:
            return self._backend.failed_count()
        except AttributeError:
            return 0

    def done_count(self) -> int:
        try:
            return self._backend.done_count()
        except AttributeError:
            return 0

    def driver_name(self) -> str:
        return "rust-sqlite-wal" if self._rust else "python-sqlite"

    def backend_name(self) -> str:
        return self.driver_name()

    @classmethod
    def instance(cls, db_path: str = "pillar_queue.db") -> "TaskStorage":
        return cls.get_instance(db_path)


# ──────────────────────────────────────────────────────────────────────
# Pure-Python SQLite fallback
# ──────────────────────────────────────────────────────────────────────

_INIT_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS pillar_tasks (
    id            TEXT PRIMARY KEY,
    func_path     TEXT NOT NULL,
    args_json     TEXT NOT NULL DEFAULT '[]',
    kwargs_json   TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    retries_left  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    scheduled_at  TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_pillar_tasks_status
    ON pillar_tasks(status, scheduled_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _PythonQueueBackend:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init(self) -> None:
        self._conn().executescript(_INIT_SQL)

    def enqueue(
        self,
        func_path: str,
        args_json: str,
        kwargs_json: str,
        retries: int,
        scheduled_at: Optional[str],
    ) -> str:
        import uuid as _uuid
        task_id = str(_uuid.uuid4())
        conn = self._conn()
        conn.execute(
            """INSERT INTO pillar_tasks
               (id, func_path, args_json, kwargs_json, retries_left, created_at, scheduled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, func_path, args_json, kwargs_json, retries, _now(), scheduled_at),
        )
        conn.commit()
        return task_id

    def dequeue(self, limit: int) -> List[Dict]:
        conn = self._conn()
        now = _now()
        rows = conn.execute(
            """SELECT id, func_path, args_json, kwargs_json, retries_left
               FROM pillar_tasks
               WHERE status = 'pending'
                 AND (scheduled_at IS NULL OR scheduled_at <= ?)
               ORDER BY created_at ASC LIMIT ?""",
            (now, limit),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(
                f"UPDATE pillar_tasks SET status='running', started_at=? WHERE id IN ({','.join('?'*len(ids))})",
                [now] + ids,
            )
            conn.commit()
        return [dict(r) for r in rows]

    def mark_complete(self, task_id: str) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE pillar_tasks SET status='completed', completed_at=? WHERE id=?",
            (_now(), task_id),
        )
        conn.commit()

    def mark_failed(self, task_id: str, error: str, retry: bool) -> None:
        conn = self._conn()
        if retry:
            conn.execute(
                "UPDATE pillar_tasks SET status='pending', retries_left=retries_left-1, error=? WHERE id=?",
                (error, task_id),
            )
        else:
            conn.execute(
                "UPDATE pillar_tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, _now(), task_id),
            )
        conn.commit()

    def pending_count(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM pillar_tasks WHERE status='pending'"
        ).fetchone()
        return row[0] if row else 0

    def failed_count(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM pillar_tasks WHERE status='failed'"
        ).fetchone()
        return row[0] if row else 0

    def done_count(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM pillar_tasks WHERE status='completed'"
        ).fetchone()
        return row[0] if row else 0
