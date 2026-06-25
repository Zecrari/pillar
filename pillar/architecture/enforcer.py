from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, FrozenSet, Set

from ..exceptions import ArchitectureViolationError

# Layer ordering: lower index = deeper (no knowledge of higher layers)
#
#   repository  →  may import schemas only
#   service     →  may import repository + schemas
#   router      →  may import service + schemas
#
# Anything importing "sideways" or "upward" is a violation.

_LAYER_ALLOWED_IMPORTS: Dict[str, FrozenSet[str]] = {
    "repository": frozenset({"schemas"}),
    "service":    frozenset({"repository", "schemas"}),
    "router":     frozenset({"service", "schemas"}),
}

_ALL_LAYERS: FrozenSet[str] = frozenset(_LAYER_ALLOWED_IMPORTS.keys())


class ArchitectureEnforcer:
    """
    Validates DDD layer import rules at startup.

    Walks the ``domains/`` directory and parses every ``router.py``,
    ``service.py``, and ``repository.py`` for import statements.
    Raises ``ArchitectureViolationError`` if a layer imports from a layer
    that is not in its allowed set.
    """

    def __init__(self, domains_dir: str = "domains") -> None:
        self.domains_dir = Path(domains_dir)

    def validate(self) -> None:
        if not self.domains_dir.exists():
            return

        for domain_path in sorted(self.domains_dir.iterdir()):
            if not domain_path.is_dir() or domain_path.name.startswith("_"):
                continue
            self._validate_domain(domain_path)

    # ------------------------------------------------------------------

    def _validate_domain(self, domain_path: Path) -> None:
        domain = domain_path.name
        for layer_name, allowed in _LAYER_ALLOWED_IMPORTS.items():
            target = domain_path / f"{layer_name}.py"
            if not target.exists():
                continue
            self._check_file(target, layer_name, domain, allowed)

    def _check_file(
        self,
        file_path: Path,
        layer: str,
        domain: str,
        allowed: FrozenSet[str],
    ) -> None:
        forbidden = _ALL_LAYERS - allowed - {layer}
        source = file_path.read_text(encoding="utf-8")

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return  # Let Python report the real error on import

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self._check_module(node.module, layer, domain, file_path, forbidden)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_module(alias.name, layer, domain, file_path, forbidden)

    def _check_module(
        self,
        module: str,
        layer: str,
        domain: str,
        file_path: Path,
        forbidden: Set[str],
    ) -> None:
        parts = module.replace("-", "_").split(".")
        for part in parts:
            if part in forbidden:
                raise ArchitectureViolationError(
                    f"\n"
                    f"  File   : domains/{domain}/{file_path.name}\n"
                    f"  Layer  : {layer}.py\n"
                    f"  Imports: '{module}'\n"
                    f"  Problem: '{layer}' must not import from '{part}'.\n"
                    f"  Allowed: {sorted(_LAYER_ALLOWED_IMPORTS[layer])}\n"
                    f"\n"
                    f"  Fix: move the logic that needs '{part}' into a "
                    f"layer that is allowed to use it."
                )
