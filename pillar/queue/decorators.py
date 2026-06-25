from __future__ import annotations

import functools
from typing import Callable, Optional


def background_task(retries: int = 0, cron: Optional[str] = None):
    """
    Schedule a function or method to execute asynchronously in the Pillar Queue.

    When the decorated function is *called*, it does not run immediately.
    Instead, the call is serialised to the persistent SQLite queue and picked up
    by the background worker, retrying up to ``retries`` times on failure.

    Class method example::

        class UserService:
            @background_task(retries=3)
            def send_welcome_email(self, user_email: str):
                email_client.send(user_email, "Welcome!")

        # Calling self.send_welcome_email(email) queues the task — no blocking.

    Module-level function example::

        @background_task()
        def cleanup_tmp_files():
            shutil.rmtree("/tmp/uploads", ignore_errors=True)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> str:
            from .storage import TaskStorage
            storage = TaskStorage.get_instance()

            if args and _looks_like_self(args[0], func):
                # Bound method call — first positional arg is 'self'
                instance = args[0]
                cls = type(instance)
                func_path = f"{cls.__module__}.{cls.__qualname__}.{func.__name__}"
                actual_args = args[1:]
            else:
                func_path = f"{func.__module__}.{func.__qualname__}"
                actual_args = args

            return storage.enqueue(
                func_path=func_path,
                args=actual_args,
                kwargs=kwargs,
                retries=retries,
            )

        wrapper._is_background_task = True
        wrapper._original_func = func
        wrapper._retries = retries
        wrapper._cron = cron
        return wrapper

    return decorator


def _looks_like_self(arg: object, func: Callable) -> bool:
    """Return True if *arg* is likely the implicit 'self' of *func*."""
    try:
        return func.__name__ in type(arg).__dict__
    except Exception:
        return False
