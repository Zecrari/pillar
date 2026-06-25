from pydantic import BaseModel
from typing import Optional


class PlanType(str):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionCreate(BaseModel):
    user_id: int
    plan: str = "free"


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan: str
    status: str
