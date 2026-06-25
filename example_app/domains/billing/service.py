from __future__ import annotations

from pillar.exceptions import NotFoundError, ConflictError
from .repository import BillingRepository


class BillingService:
    def __init__(self, repo: BillingRepository) -> None:
        self.repo = repo

    def get_subscription(self, user_id: int) -> dict:
        sub = self.repo.get_by_user(user_id)
        if not sub:
            raise NotFoundError(f"No subscription found for user {user_id}")
        return sub

    def create_subscription(self, user_id: int, plan: str = "free") -> dict:
        if self.repo.get_by_user(user_id):
            raise ConflictError(f"User {user_id} already has a subscription")
        return self.repo.create(user_id, plan)

    def upgrade_plan(self, user_id: int, plan: str) -> dict:
        valid_plans = ("free", "pro", "enterprise")
        if plan not in valid_plans:
            raise ValueError(f"Invalid plan '{plan}'. Choose from {valid_plans}")
        sub = self.repo.get_by_user(user_id)
        if not sub:
            raise NotFoundError(f"No subscription found for user {user_id}")
        return self.repo.upgrade(user_id, plan)
