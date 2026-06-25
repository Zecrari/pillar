from __future__ import annotations

from typing import Optional
from pillar.db import Database


class BillingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._setup()

    def _setup(self) -> None:
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions (
               id      INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL,
               plan    TEXT    NOT NULL DEFAULT 'free',
               status  TEXT    NOT NULL DEFAULT 'active'
            )"""
        )

    def get_by_user(self, user_id: int) -> Optional[dict]:
        return self.db.query(
            "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
        )

    def create(self, user_id: int, plan: str) -> dict:
        self.db.execute(
            "INSERT INTO subscriptions (user_id, plan) VALUES (?, ?)", (user_id, plan)
        )
        row_id = self.db.last_insert_id()
        return self.db.query("SELECT * FROM subscriptions WHERE id = ?", (row_id,))

    def upgrade(self, user_id: int, plan: str) -> Optional[dict]:
        self.db.execute(
            "UPDATE subscriptions SET plan = ? WHERE user_id = ?", (plan, user_id)
        )
        return self.get_by_user(user_id)
