from __future__ import annotations

from pillar import Router
from .schemas import SubscriptionCreate
from .service import BillingService

router = Router(prefix="/billing", tags=["Billing"])


@router.get("/subscriptions/{user_id}")
async def get_subscription(user_id: int, service: BillingService):
    """Get subscription for a user."""
    return service.get_subscription(user_id)


@router.post("/subscriptions")
async def create_subscription(data: SubscriptionCreate, service: BillingService):
    """Create a new subscription."""
    return service.create_subscription(data.user_id, data.plan)


@router.patch("/subscriptions/{user_id}/upgrade")
async def upgrade_plan(user_id: int, service: BillingService):
    """Upgrade to pro plan."""
    return service.upgrade_plan(user_id, "pro")
