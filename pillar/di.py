from __future__ import annotations

import inspect
from typing import Any, Dict, Optional, Type, TypeVar

T = TypeVar("T")


class DIContainer:
    """
    Lightweight dependency injection container.

    Resolution strategy:
      1. If a pre-registered instance exists, return it (singleton).
      2. Otherwise inspect __init__, recursively resolve each typed
         parameter, construct the instance, and cache it.

    Usage:
        container.register_instance(Database, db)   # manual singleton
        service = container.resolve(UserService)     # auto-wired
    """

    def __init__(self) -> None:
        self._singletons: Dict[Type, Any] = {}
        self._bindings: Dict[Type, Type] = {}  # interface → concrete class

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register_instance(self, cls: Type[T], instance: T) -> None:
        """Register a pre-created instance as a singleton."""
        self._singletons[cls] = instance

    def bind(self, interface: Type[T], concrete: Type[T]) -> None:
        """Bind an abstract type to a concrete implementation."""
        self._bindings[interface] = concrete

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, cls: Type[T]) -> T:
        """Recursively resolve and return an instance of *cls*."""
        if cls in self._singletons:
            return self._singletons[cls]

        concrete = self._bindings.get(cls, cls)

        # Resolve constructor dependencies
        try:
            sig = inspect.signature(concrete.__init__)
        except (ValueError, TypeError):
            instance = concrete()
            self._singletons[cls] = instance
            return instance

        # Use get_type_hints() so `from __future__ import annotations` files
        # have their string annotations resolved to real types.
        try:
            import typing
            hints = typing.get_type_hints(concrete.__init__)
        except Exception:
            hints = {}

        kwargs: Dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            # Prefer resolved hint; fall back to param.annotation
            annotation = hints.get(name, param.annotation)
            if annotation is inspect.Parameter.empty:
                continue
            # Only auto-inject typed class parameters; skip primitives
            if not isinstance(annotation, type):
                continue
            if annotation in (str, int, float, bool, bytes):
                continue
            kwargs[name] = self.resolve(annotation)

        instance = concrete(**kwargs)
        self._singletons[cls] = instance
        return instance

    def reset(self) -> None:
        """Clear all singletons (useful between tests)."""
        self._singletons.clear()
        self._bindings.clear()

    def __contains__(self, cls: Type) -> bool:
        return cls in self._singletons or cls in self._bindings


# Module-level singleton used by the framework internals.
container: DIContainer = DIContainer()
