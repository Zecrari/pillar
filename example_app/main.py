"""
Pillar Example Application — entry point.

Run with:
    cd example_app
    pillar run main:app --reload

Or from the project root:
    pillar run example_app.main:app
"""
import os
from pillar import Pillar

from domains.users.router   import UserController
from domains.billing.router import router as billing_router

app = Pillar(
    title="Pillar Example API",
    version="1.0.0",
    description="A demo API built with the Pillar framework — Rust-powered, DDD-enforced.",
    config_path=os.path.join(os.path.dirname(__file__), "pillar.toml"),
)

app.include_controller(UserController)   # Controller-based — no @router decorators
app.include_router(billing_router)       # Classic Router also supported
