from __future__ import annotations

from typing import Optional
from pillar.db import Database


class UserRepository:
    """Data access layer — ONLY raw SQL queries, nothing else."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._setup()

    def _setup(self) -> None:
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS users (
               id    INTEGER PRIMARY KEY AUTOINCREMENT,
               name  TEXT    NOT NULL,
               email TEXT    NOT NULL UNIQUE
            )"""
        )

    # Notice: No 'async'. The Smart Bridge in the router layer runs
    # sync methods in a thread pool automatically.
    def get_by_id(self, user_id: int) -> Optional[dict]:
        return self.db.query("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_by_email(self, email: str) -> Optional[dict]:
        return self.db.query("SELECT * FROM users WHERE email = ?", (email,))

    def get_all(self) -> list:
        return self.db.query_all("SELECT * FROM users ORDER BY id")

    def create(self, name: str, email: str) -> dict:
        self.db.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        row_id = self.db.last_insert_id()
        return self.get_by_id(row_id)

    def update(self, user_id: int, name: str = None, email: str = None) -> Optional[dict]:
        if name is not None:
            self.db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        if email is not None:
            self.db.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        return self.get_by_id(user_id)

    def delete(self, user_id: int) -> bool:
        rows = self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return rows > 0
