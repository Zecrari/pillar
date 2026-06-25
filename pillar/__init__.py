"""
Pillar — Production-Grade Python Backend Framework

Public API surface:

    from pillar import Pillar, Router, background_task
    from pillar import ok, created, paginate, problem, no_content
    from pillar.db import Database
    from pillar.exceptions import NotFoundError, UnauthorizedError, ...
    from pillar.di import container
    from pillar.metrics import metrics
"""

from .app import Pillar
from .router import Router
from .controller import Controller, action
from .queue.decorators import background_task
from .exceptions import (
    PillarError,
    NotFoundError,
    UnauthorizedError,
    ForbiddenError,
    ValidationError,
    ConflictError,
    PillarContractError,
    ArchitectureViolationError,
)
from .di import container, DIContainer
from .config import PillarConfig
from .responses import ok, created, no_content, paginate, problem, PaginatedResponse
from .metrics import metrics
from .ai import PillarAI
from .security import JWTMiddleware, encode_jwt, decode_jwt, RequireAuth
from .telemetry import setup_telemetry, trace_span, TelemetryMiddleware
from .tracer import span_context, current_trace_id, record_span
from .db.rls import RLSDatabase, set_tenant, get_tenant

__version__ = "0.1.0"

__all__ = [
    # Core
    "Pillar",
    "Router",
    "background_task",
    # Exceptions
    "PillarError",
    "NotFoundError",
    "UnauthorizedError",
    "ForbiddenError",
    "ValidationError",
    "ConflictError",
    "PillarContractError",
    "ArchitectureViolationError",
    # DI
    "container",
    "DIContainer",
    # Config
    "PillarConfig",
    # Response helpers
    "ok",
    "created",
    "no_content",
    "paginate",
    "problem",
    "PaginatedResponse",
    # Metrics
    "metrics",
    # Controller-based routing
    "Controller",
    "action",
    # AI-native extraction
    "PillarAI",
    # Security
    "JWTMiddleware",
    "encode_jwt",
    "decode_jwt",
    "RequireAuth",
    # Telemetry (OTel)
    "setup_telemetry",
    "trace_span",
    "TelemetryMiddleware",
    # Time-travel tracer (built-in)
    "span_context",
    "current_trace_id",
    "record_span",
    # Auto RLS
    "RLSDatabase",
    "set_tenant",
    "get_tenant",
    # Version
    "__version__",
]
