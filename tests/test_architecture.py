"""Tests for the DDD architecture enforcer."""
import textwrap
from pathlib import Path

import pytest

from pillar.architecture.enforcer import ArchitectureEnforcer
from pillar.exceptions import ArchitectureViolationError


def _write(tmp_path: Path, domain: str, layer: str, code: str) -> None:
    d = tmp_path / "domains" / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{layer}.py").write_text(textwrap.dedent(code))


# ── Valid code — should not raise ───────────────────────────────────

def test_valid_router_imports_service(tmp_path):
    _write(tmp_path, "users", "repository", "from pillar.db import Database")
    _write(tmp_path, "users", "service", "from .repository import UserRepository")
    _write(tmp_path, "users", "router", "from .service import UserService")

    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    enforcer.validate()  # must not raise


def test_valid_service_imports_repository(tmp_path):
    _write(tmp_path, "billing", "repository", "pass")
    _write(tmp_path, "billing", "service", "from .repository import BillingRepository")

    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    enforcer.validate()


def test_no_domains_dir_does_not_raise(tmp_path):
    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "nonexistent"))
    enforcer.validate()  # should be a no-op


# ── Violations — must raise ──────────────────────────────────────────

def test_router_imports_repository_raises(tmp_path):
    _write(tmp_path, "users", "repository", "pass")
    _write(
        tmp_path, "users", "router",
        "from .repository import UserRepository  # VIOLATION"
    )
    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    with pytest.raises(ArchitectureViolationError, match="repository"):
        enforcer.validate()


def test_repository_imports_service_raises(tmp_path):
    _write(tmp_path, "users", "service", "pass")
    _write(
        tmp_path, "users", "repository",
        "from .service import UserService  # VIOLATION"
    )
    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    with pytest.raises(ArchitectureViolationError, match="service"):
        enforcer.validate()


def test_repository_imports_router_raises(tmp_path):
    _write(tmp_path, "orders", "router", "pass")
    _write(
        tmp_path, "orders", "repository",
        "from .router import OrderRouter  # VIOLATION"
    )
    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    with pytest.raises(ArchitectureViolationError, match="router"):
        enforcer.validate()


def test_error_message_contains_domain_and_file(tmp_path):
    _write(
        tmp_path, "payments", "router",
        "from .repository import PaymentRepository"
    )
    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    with pytest.raises(ArchitectureViolationError) as exc_info:
        enforcer.validate()
    msg = str(exc_info.value)
    assert "payments" in msg
    assert "router.py" in msg


def test_schemas_import_is_allowed_in_router(tmp_path):
    _write(tmp_path, "items", "schemas", "from pydantic import BaseModel")
    _write(tmp_path, "items", "service", "from .schemas import ItemSchema")
    _write(tmp_path, "items", "router", "from .schemas import ItemSchema; from .service import ItemService")

    enforcer = ArchitectureEnforcer(domains_dir=str(tmp_path / "domains"))
    enforcer.validate()  # should not raise
