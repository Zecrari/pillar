"""
Pillar Async Database — non-blocking database access for async handlers.

Uses ``aiosqlite`` when installed (true async I/O), otherwise falls back to
``asyncio.to_thread`` wrapping the sync ``Database`` — so the event loop is
never blocked in either case.

Install the async extra::

    pip install aiosqlite

Usage::

    from pillar.db.async_db import AsyncDatabase
    from pillar.di import container

    # Register alongside (or instead of) the sync Database:
    async_db = AsyncDatabase("sqlite:///./app.db")
    container.register_instance(AsyncDatabase, async_db)

    # In an async handler:
    async def get_user(user_id: int, db: AsyncDatabase):
        return await db.query("SELECT * FROM users WHERE id = ?", (user_id,))

RLS support::

    from pillar.db.async_rls import AsyncRLSDatabase

    rls_db = AsyncRLSDatabase(async_db, tenant_column="org_id")
    container.register_instance(AsyncDatabase, rls_db)
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

_AIOSQLITE = False
try:
    import aiosqlite
    _AIOSQLITE = True
except ImportError:
    pass


class AsyncDatabase:
    """
    Async SQLite database handle.

    Prefers ``aiosqlite`` (true async, no thread overhead).
    Falls back to ``asyncio.to_thread`` with the sync ``Database`` class
    so the framework works with zero extra dependencies.
    """

    def __init__(self, url: str = "sqlite:///./app.db") -> None:
        self.url = url
        self._db_path = self._parse_path(url)
        self._conn: Optional[Any] = None          # aiosqlite connection
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_path(url: str) -> str:
        return url[len("sqlite:///"):] if url.startswith("sqlite:///") else url

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def _get_conn(self) -> Optional[Any]:
        """Return the aiosqlite connection, creating it on first call."""
        if not _AIOSQLITE:
            return None
        async with self._lock:
            if self._conn is None:
                self._conn = await aiosqlite.connect(self._db_path)
                self._conn.row_factory = aiosqlite.Row
                await self._conn.execute("PRAGMA journal_mode=WAL")
                await self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    async def query(
        self, sql: str, params: Tuple[Any, ...] = ()
    ) -> Optional[Dict]:
        """Return the first matching row as a dict, or None."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        try:
            conn = await self._get_conn()
            if conn:
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            else:
                return await asyncio.to_thread(self._sync_query, sql, params)
        finally:
            record_span("async_db.query", "db", t0, _ms(), sql=sql[:120])

    async def query_all(
        self, sql: str, params: Tuple[Any, ...] = ()
    ) -> List[Dict]:
        """Return all matching rows as a list of dicts."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        try:
            conn = await self._get_conn()
            if conn:
                async with conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            else:
                return await asyncio.to_thread(self._sync_query_all, sql, params)
        finally:
            record_span("async_db.query_all", "db", t0, _ms(), sql=sql[:120])

    async def execute(
        self, sql: str, params: Tuple[Any, ...] = ()
    ) -> int:
        """Execute a write statement; return the number of affected rows."""
        from ..tracer import record_span, _ms
        t0 = _ms()
        try:
            conn = await self._get_conn()
            if conn:
                async with conn.execute(sql, params) as cursor:
                    await conn.commit()
                    return cursor.rowcount
            else:
                return await asyncio.to_thread(self._sync_execute, sql, params)
        finally:
            record_span("async_db.execute", "db", t0, _ms(), sql=sql[:120])

    async def execute_returning(
        self, sql: str, params: Tuple[Any, ...] = ()
    ) -> Optional[Dict]:
        """Execute a write and return the resulting row (for INSERT)."""
        conn = await self._get_conn()
        if conn:
            async with conn.execute(sql, params) as cursor:
                await conn.commit()
                rowid = cursor.lastrowid
                if rowid:
                    table_match = sql.upper().split("INTO")[1].strip().split()[0]
                    async with conn.execute(
                        f"SELECT * FROM {table_match} WHERE rowid = ?", (rowid,)
                    ) as c2:
                        row = await c2.fetchone()
                        return dict(row) if row else None
            return None
        else:
            return await asyncio.to_thread(self._sync_execute_returning, sql, params)

    async def last_insert_id(self) -> Optional[int]:
        conn = await self._get_conn()
        if conn:
            async with conn.execute("SELECT last_insert_rowid()") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
        return await asyncio.to_thread(self._sync_last_id)

    # ------------------------------------------------------------------
    # Thread-pool fallback (no aiosqlite)
    # ------------------------------------------------------------------

    def _sync_query(self, sql: str, params: tuple) -> Optional[Dict]:
        from .database import Database
        return Database(self.url).query(sql, params)

    def _sync_query_all(self, sql: str, params: tuple) -> List[Dict]:
        from .database import Database
        return Database(self.url).query_all(sql, params)

    def _sync_execute(self, sql: str, params: tuple) -> int:
        from .database import Database
        return Database(self.url).execute(sql, params)

    def _sync_execute_returning(self, sql: str, params: tuple) -> Optional[Dict]:
        from .database import Database
        return Database(self.url).execute_returning(sql, params)

    def _sync_last_id(self) -> Optional[int]:
        from .database import Database
        return Database(self.url).last_insert_id()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncDatabase":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
