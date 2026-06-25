from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple


class Database:
    """
    Framework-managed database handle.

    Sync query methods are intentionally blocking — the Smart Bridge in
    the route handler layer runs them inside ``asyncio.run_in_executor``
    so they never block the event loop.

    Currently supports SQLite (``sqlite:///./path``).
    PostgreSQL / MySQL adapters will be added in Phase 3.
    """

    def __init__(self, url: str = "sqlite:///./app.db") -> None:
        self.url = url
        self._local = threading.local()
        self._db_path = self._parse_sqlite_path(url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sqlite_path(url: str) -> str:
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///"):]
        return url  # treat bare path as-is

    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection so concurrent workers don't collide."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def query(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict]:
        """Return the first matching row as a dict, or None."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        try:
            row = self._conn().execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            record_span("db.query", "db", t0, _ms(), sql=sql[:120])

    def query_all(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict]:
        """Return all matching rows as a list of dicts."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        try:
            rows = self._conn().execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            record_span("db.query_all", "db", t0, _ms(), sql=sql[:120])

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        """Execute a write statement; return the number of affected rows."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        conn = self._conn()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount
        finally:
            record_span("db.execute", "db", t0, _ms(), sql=sql[:120])

    def execute_returning(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict]:
        """Execute a write statement and return the inserted/updated row."""
        conn = self._conn()
        cursor = conn.execute(sql, params)
        conn.commit()
        if cursor.lastrowid:
            # Re-query by rowid to get the full row
            row = conn.execute(
                "SELECT * FROM (SELECT *, rowid FROM ({sql}) LIMIT 1)".format(sql=sql),
                params,
            ).fetchone()
            if row:
                return dict(row)
        return None

    def last_insert_id(self) -> Optional[int]:
        row = self._conn().execute("SELECT last_insert_rowid()").fetchone()
        return row[0] if row else None

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
