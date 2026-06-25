from __future__ import annotations

from pillar import Controller, action
from .schemas import UserCreate, UserUpdate, UserResponse
from .service import UserService


class UserController(Controller):
    prefix = "/users"
    tags   = ["Users"]

    async def list(self, service: UserService) -> list:
        """Return all users."""
        return service.list_users()

    async def get(self, user_id: int, service: UserService) -> UserResponse:
        """Fetch a single user by ID."""
        return service.get_user(user_id)

    async def create(self, data: UserCreate, service: UserService) -> UserResponse:
        """Create a new user (queues a welcome e-mail via background task)."""
        return service.create_user(data.name, data.email)

    async def update(self, user_id: int, data: UserUpdate, service: UserService) -> UserResponse:
        """Replace a user's details."""
        return service.update_user(user_id, name=data.name, email=data.email)

    async def delete(self, user_id: int, service: UserService):
        """Permanently delete a user."""
        return service.delete_user(user_id)

    # ── Custom actions beyond CRUD ────────────────────────────────────

    @action.post("/{user_id}/activate")
    async def activate(self, user_id: int, service: UserService) -> UserResponse:
        """Set user status to active."""
        return service.activate_user(user_id)

    @action.post("/{user_id}/deactivate")
    async def deactivate(self, user_id: int, service: UserService) -> UserResponse:
        """Set user status to inactive."""
        return service.deactivate_user(user_id)
