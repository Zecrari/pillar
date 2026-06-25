"""
Pillar Vector Repository — first-class RAG support.

Scales from embedded SQLite-VSS (zero setup, works today) to
Qdrant / Pinecone via a single config change.

Usage::

    from pillar.vector import VectorRepository

    # Auto-selects backend from pillar.toml [vector] section or env vars
    vr = VectorRepository()

    # Store
    await vr.upsert("doc-1", embedding=[0.1, 0.2, ...], metadata={"text": "..."})

    # Search
    results = await vr.search(query_embedding=[0.1, 0.2, ...], top_k=5)
    # → [{"id": "doc-1", "score": 0.97, "metadata": {...}}, ...]

    # Full RAG helper: embed text + search in one call
    results = await vr.search_text("What is Pillar?", embedder=ai.embed, top_k=5)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import threading
from typing import Any, Callable, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────
# Abstract backend interface
# ──────────────────────────────────────────────────────────────────────

class _VectorBackend:
    async def upsert(self, id: str, embedding: List[float], metadata: dict) -> None:
        raise NotImplementedError

    async def search(self, embedding: List[float], top_k: int, filter: dict = None) -> List[dict]:
        raise NotImplementedError

    async def delete(self, id: str) -> None:
        raise NotImplementedError

    async def count(self) -> int:
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────
# SQLite fallback backend (no dependencies — works everywhere)
# Uses a simple cosine similarity scan — fine for dev / small datasets
# ──────────────────────────────────────────────────────────────────────

class _SQLiteVectorBackend(_VectorBackend):
    """
    Pure-Python cosine-similarity scan over SQLite BLOB embeddings.

    Designed for development and datasets up to ~50k vectors.
    Production deployments should switch to Qdrant or Pinecone via
    the pillar.toml [vector] section.
    """

    _INIT = """
    CREATE TABLE IF NOT EXISTS pillar_vectors (
        id          TEXT PRIMARY KEY,
        embedding   BLOB NOT NULL,
        metadata    TEXT NOT NULL DEFAULT '{}'
    );
    """

    def __init__(self, db_path: str = "pillar_vectors.db") -> None:
        self._db_path = db_path
        self._local   = threading.local()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return self._local.conn

    def _init(self) -> None:
        self._conn().executescript(self._INIT)

    @staticmethod
    def _pack(v: List[float]) -> bytes:
        return struct.pack(f"{len(v)}f", *v)

    @staticmethod
    def _unpack(b: bytes) -> List[float]:
        n = len(b) // 4
        return list(struct.unpack(f"{n}f", b))

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def upsert(self, id: str, embedding: List[float], metadata: dict) -> None:
        def _write():
            conn = self._conn()
            conn.execute(
                "INSERT INTO pillar_vectors(id, embedding, metadata) VALUES(?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding, metadata=excluded.metadata",
                (id, self._pack(embedding), json.dumps(metadata)),
            )
            conn.commit()
        await asyncio.get_event_loop().run_in_executor(None, _write)

    async def search(self, embedding: List[float], top_k: int, filter: dict = None) -> List[dict]:
        def _scan():
            rows = self._conn().execute(
                "SELECT id, embedding, metadata FROM pillar_vectors"
            ).fetchall()
            scored = []
            for row_id, blob, meta_json in rows:
                vec  = self._unpack(blob)
                meta = json.loads(meta_json)
                if filter:
                    if not all(meta.get(k) == v for k, v in filter.items()):
                        continue
                score = self._cosine(embedding, vec)
                scored.append({"id": row_id, "score": score, "metadata": meta})
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:top_k]

        return await asyncio.get_event_loop().run_in_executor(None, _scan)

    async def delete(self, id: str) -> None:
        def _del():
            conn = self._conn()
            conn.execute("DELETE FROM pillar_vectors WHERE id=?", (id,))
            conn.commit()
        await asyncio.get_event_loop().run_in_executor(None, _del)

    async def count(self) -> int:
        def _cnt():
            row = self._conn().execute("SELECT COUNT(*) FROM pillar_vectors").fetchone()
            return row[0] if row else 0
        return await asyncio.get_event_loop().run_in_executor(None, _cnt)


# ──────────────────────────────────────────────────────────────────────
# Qdrant backend (production)
# ──────────────────────────────────────────────────────────────────────

class _QdrantBackend(_VectorBackend):
    """
    Qdrant Cloud or self-hosted adapter.

    Install: pip install qdrant-client
    Config:
        PILLAR_VECTOR_QDRANT_URL        = http://localhost:6333
        PILLAR_VECTOR_QDRANT_API_KEY    = ...
        PILLAR_VECTOR_COLLECTION        = my_collection
        PILLAR_VECTOR_DIM               = 1536
    """

    def __init__(self, url: str, api_key: str, collection: str, dim: int) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError:
            raise ImportError("pip install qdrant-client  to use the Qdrant backend")

        self._client     = QdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._dim        = dim

        # Create collection if it doesn't exist
        existing = [c.name for c in self._client.get_collections().collections]
        if collection not in existing:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    async def upsert(self, id: str, embedding: List[float], metadata: dict) -> None:
        from qdrant_client.models import PointStruct
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.upsert(
                collection_name=self._collection,
                points=[PointStruct(id=id, vector=embedding, payload=metadata)],
            ),
        )

    async def search(self, embedding: List[float], top_k: int, filter: dict = None) -> List[dict]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant_filter = None
        if filter:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filter.items()
            ]
            qdrant_filter = Filter(must=conditions)

        loop = asyncio.get_event_loop()
        hits = await loop.run_in_executor(
            None,
            lambda: self._client.search(
                collection_name=self._collection,
                query_vector=embedding,
                limit=top_k,
                query_filter=qdrant_filter,
                with_payload=True,
            ),
        )
        return [{"id": h.id, "score": h.score, "metadata": h.payload} for h in hits]

    async def delete(self, id: str) -> None:
        from qdrant_client.models import PointIdsList
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.delete(
                collection_name=self._collection,
                points_selector=PointIdsList(points=[id]),
            ),
        )

    async def count(self) -> int:
        info = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.get_collection(self._collection),
        )
        return info.vectors_count or 0


# ──────────────────────────────────────────────────────────────────────
# Public VectorRepository
# ──────────────────────────────────────────────────────────────────────

class VectorRepository:
    """
    Pillar's unified vector store abstraction.

    Reads the backend from environment variables::

        PILLAR_VECTOR_BACKEND  = sqlite (default) | qdrant | pinecone
        PILLAR_VECTOR_DB_PATH  = pillar_vectors.db   (sqlite only)
        PILLAR_VECTOR_QDRANT_URL = http://localhost:6333
        PILLAR_VECTOR_QDRANT_API_KEY = ...
        PILLAR_VECTOR_COLLECTION = my_collection
        PILLAR_VECTOR_DIM        = 1536

    Switch from SQLite to Qdrant with a single env var change — zero
    code changes required.
    """

    def __init__(self, backend: str = None, **kwargs) -> None:
        backend = backend or os.getenv("PILLAR_VECTOR_BACKEND", "sqlite")

        if backend == "sqlite":
            db_path = kwargs.get("db_path") or os.getenv(
                "PILLAR_VECTOR_DB_PATH", "pillar_vectors.db"
            )
            self._backend: _VectorBackend = _SQLiteVectorBackend(db_path)

        elif backend == "qdrant":
            self._backend = _QdrantBackend(
                url=kwargs.get("url")        or os.getenv("PILLAR_VECTOR_QDRANT_URL", "http://localhost:6333"),
                api_key=kwargs.get("api_key")or os.getenv("PILLAR_VECTOR_QDRANT_API_KEY", ""),
                collection=kwargs.get("collection") or os.getenv("PILLAR_VECTOR_COLLECTION", "pillar"),
                dim=int(kwargs.get("dim") or os.getenv("PILLAR_VECTOR_DIM", "1536")),
            )
        else:
            raise ValueError(f"Unknown vector backend: {backend!r}. Use 'sqlite' or 'qdrant'.")

        self.backend_name = backend

    # ------------------------------------------------------------------

    async def upsert(self, id: str, embedding: List[float], metadata: dict = None) -> None:
        """Insert or update a vector."""
        await self._backend.upsert(id, embedding, metadata or {})

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filter: Dict[str, Any] = None,
    ) -> List[Dict]:
        """
        Return the *top_k* most similar vectors.

        Each result: ``{"id": "...", "score": 0.97, "metadata": {...}}``
        """
        return await self._backend.search(query_embedding, top_k, filter)

    async def search_text(
        self,
        text: str,
        embedder: Callable,
        top_k: int = 5,
        filter: Dict[str, Any] = None,
    ) -> List[Dict]:
        """
        RAG helper: embed *text* then search in one call.

        ``embedder`` must be an async callable: ``async def embed(text: str) -> List[float]``
        """
        embedding = await embedder(text)
        return await self.search(embedding, top_k, filter)

    async def delete(self, id: str) -> None:
        await self._backend.delete(id)

    async def count(self) -> int:
        return await self._backend.count()

    async def upsert_many(self, records: List[Dict]) -> None:
        """
        Bulk upsert.  Each record: ``{"id": ..., "embedding": [...], "metadata": {...}}``
        """
        await asyncio.gather(*(
            self.upsert(r["id"], r["embedding"], r.get("metadata", {}))
            for r in records
        ))
