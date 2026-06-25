from __future__ import annotations

import asyncio
import functools
import importlib
import logging
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger("pillar.queue")


class TaskWorker:
    """
    Async background task worker.

    Polls the ``TaskStorage`` (backed by the Rust SQLite queue) and executes
    tasks concurrently.  Sync tasks run in the default thread-pool executor
    so the event loop is never blocked.

    Graceful shutdown (SIGTERM / lifespan.shutdown):
      - ``stop()`` sets a flag so no new tasks are dequeued.
      - In-flight tasks are awaited to completion (up to *drain_timeout* s).
      - Only after all in-flight tasks finish does the worker exit.
    """

    def __init__(self, config: Any, drain_timeout: float = 30.0) -> None:
        from .storage import TaskStorage
        self._storage       = TaskStorage.get_instance(config.queue.db_path)
        self._poll_interval = config.queue.poll_interval
        self._drain_timeout = drain_timeout
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="pillar-queue-worker")
        logger.info(
            "Pillar Queue worker started (%s backend)",
            self._storage.backend_name(),
        )

    async def stop(self) -> None:
        """
        Graceful drain: signal the polling loop to stop accepting new tasks,
        then wait up to ``drain_timeout`` seconds for in-flight tasks to finish.
        """
        self._running = False
        if not (self._task and not self._task.done()):
            logger.info("Pillar Queue worker stopped (was idle)")
            return

        logger.info(
            "Pillar Queue worker draining in-flight tasks (up to %.0fs)…",
            self._drain_timeout,
        )
        try:
            await asyncio.wait_for(self._task, timeout=self._drain_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Queue worker did not drain within %.0fs — cancelling remaining tasks",
                self._drain_timeout,
            )
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

        logger.info("Pillar Queue worker stopped")

    # ------------------------------------------------------------------
    # Poll loop — tracks in-flight tasks explicitly so stop() can drain
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        in_flight: Set[asyncio.Task] = set()

        while True:
            # Dequeue only while we are not shutting down
            if self._running:
                try:
                    tasks = self._storage.dequeue(limit=5)
                    for t in tasks:
                        fut = asyncio.ensure_future(self._execute(t))
                        in_flight.add(fut)
                        fut.add_done_callback(in_flight.discard)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Queue worker poll error: %s", exc)

            # Exit cleanly once shutdown requested and all tasks drained
            if not self._running and not in_flight:
                break

            # Wait for a task to finish or for the next poll tick
            if in_flight:
                try:
                    await asyncio.wait(
                        in_flight,
                        timeout=self._poll_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    break
            else:
                try:
                    await asyncio.sleep(self._poll_interval)
                except asyncio.CancelledError:
                    break

        # Best-effort cancel anything still running after a hard break
        for fut in in_flight:
            fut.cancel()

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute(self, task: Dict[str, Any]) -> None:
        task_id: str   = task["id"]
        func_path: str = task["func_path"]
        args: list     = task["args"]
        kwargs: dict   = task["kwargs"]
        retries_left   = task["retries_left"]

        try:
            func = _resolve_callable(func_path)
            if asyncio.iscoroutinefunction(func):
                await func(*args, **kwargs)
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
            self._storage.mark_complete(task_id)
            logger.debug("Task %s (%s) completed", task_id[:8], func_path)
        except Exception as exc:
            error_msg    = str(exc)
            should_retry = retries_left > 0
            self._storage.mark_failed(task_id, error_msg, retry=should_retry)
            if should_retry:
                logger.warning(
                    "Task %s failed (%d retries left): %s",
                    task_id[:8], retries_left, error_msg,
                )
            else:
                logger.error("Task %s permanently failed: %s", task_id[:8], error_msg)


# ──────────────────────────────────────────────────────────────────────
# Callable resolution
# ──────────────────────────────────────────────────────────────────────

def _resolve_callable(func_path: str) -> Callable:
    """
    Turn a dotted string path into a callable.

    Handles two formats:
    - ``"my_app.utils.send_email"``                      → module-level function
    - ``"domains.users.service.UserService.send_email"`` → class method (DI-resolved)
    """
    parts = func_path.rsplit(".", 1)
    if len(parts) < 2:
        raise ImportError(f"Cannot resolve '{func_path}': need at least one dot")

    parent, name = parts

    # Try as module.function first
    try:
        mod = importlib.import_module(parent)
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass

    # Try as module.ClassName.method
    parts2 = parent.rsplit(".", 1)
    if len(parts2) == 2:
        mod_path, class_name = parts2
        try:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, class_name)
        except (ImportError, AttributeError) as exc:
            raise ImportError(f"Cannot resolve '{func_path}': {exc}") from exc

        from ..di import container
        instance = container.resolve(cls)

        method_descriptor = cls.__dict__.get(name)
        if method_descriptor and hasattr(method_descriptor, "_original_func"):
            original = method_descriptor._original_func
            return functools.partial(original, instance)

        return getattr(instance, name)

    raise ImportError(f"Cannot resolve '{func_path}'")
