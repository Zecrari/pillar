from __future__ import annotations

from pillar import background_task
from pillar.exceptions import NotFoundError, ConflictError
from .repository import UserRepository


class UserService:
    """Business logic layer — orchestrates the repository, applies rules."""

    def __init__(self, repo: UserRepository) -> None:
        self.repo = repo  # Auto-injected by Pillar!

    def get_user(self, user_id: int) -> dict:
        user = self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError(f"User {user_id} not found")
        return {**user, "status": "active"}

    def list_users(self) -> list:
        return [{**u, "status": "active"} for u in self.repo.get_all()]

    def create_user(self, name: str, email: str) -> dict:
        if self.repo.get_by_email(email):
            raise ConflictError(f"Email '{email}' is already registered")
        user = self.repo.create(name, email)
        # Queue a welcome email — this does NOT block the response
        self.send_welcome_email(user["email"])
        return {**user, "status": "active"}

    def update_user(self, user_id: int, name: str = None, email: str = None) -> dict:
        if not self.repo.get_by_id(user_id):
            raise NotFoundError(f"User {user_id} not found")
        user = self.repo.update(user_id, name=name, email=email)
        return {**user, "status": "active"}

    def delete_user(self, user_id: int) -> dict:
        if not self.repo.get_by_id(user_id):
            raise NotFoundError(f"User {user_id} not found")
        self.repo.delete(user_id)
        return {"deleted": True, "id": user_id}

    def activate_user(self, user_id: int) -> dict:
        user = self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError(f"User {user_id} not found")
        return {**user, "status": "active"}

    def deactivate_user(self, user_id: int) -> dict:
        user = self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError(f"User {user_id} not found")
        return {**user, "status": "inactive"}

    # ── Background tasks ────────────────────────────────────────────

    @background_task(retries=3)
    def send_welcome_email(self, user_email: str) -> None:
        """
        Runs inside the Pillar Queue (Rust SQLite WAL backend).
        Retried up to 3 times on failure.  No Redis, no Celery.
        """
        # Replace with your real email client (SendGrid, SES, etc.)
        print(f"[PillarQueue] ✉️  Welcome email sent to {user_email}")

    @background_task(retries=1)
    def send_password_reset(self, user_email: str, reset_token: str) -> None:
        """Queue a password-reset email."""
        print(f"[PillarQueue] 🔑 Password reset for {user_email}: token={reset_token}")
