"""Fire-and-forget background-task plumbing, shared app-wide.

asyncio's event loop holds only weak references to tasks, so an
otherwise-unreferenced fire-and-forget task can be garbage-collected
mid-flight — and a crashed background job must log loudly instead of
dying silently.  This pattern was previously hand-rolled six times
(``main._spawn_logged`` plus five ``kick_*`` variants in the scheduler,
each with its own strong-reference set and coalescing global).

Everything spawns through here now, which also gives shutdown ONE place
to cancel whatever is still in flight — previously the lifespan only
cancelled main.py's own set, and scheduler-kicked tasks could outlive
engine disposal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger(__name__)

# Strong references to every in-flight background task.
_tasks: set[asyncio.Task[None]] = set()
# name -> the single in-flight task for coalesced (single-flight) spawns.
_coalesced: dict[str, asyncio.Task[None]] = {}


def _on_done(task: asyncio.Task[None]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %r failed", task.get_name(), exc_info=exc)


def spawn(coro: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
    """``create_task`` with a strong reference and failure logging."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def spawn_coalesced(name: str, coro: Coroutine[None, None, None]) -> None:
    """Single-flight spawn: a no-op while the named task is still running.

    Used by the poll-triggered map refreshes so repeated ``GET /api/map``
    calls can't pile up overlapping resolves.  When the spawn is skipped,
    ``coro`` is closed so it never triggers a "never awaited" warning.
    """
    current = _coalesced.get(name)
    if current is not None and not current.done():
        coro.close()
        return
    _coalesced[name] = spawn(coro, name)


async def cancel_all() -> None:
    """Cancel and await every in-flight background task (app shutdown)."""
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
