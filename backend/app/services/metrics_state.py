"""In-process scheduler-job metrics registry.

The ``AsyncIOScheduler`` instance is a local variable inside
``main.lifespan``, so ``/api/metrics`` cannot ask it anything after
startup.  Instead the jobs tick this module-level registry (the same
pattern as ``circuit_breaker._breakers``): ``setup_scheduler`` wraps
every job coroutine with :func:`instrumented`, which records each run's
outcome and finish time here for the metrics route to read.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app import timeutil


@dataclass
class JobStats:
    """Cumulative run counts and the last finish time for one job."""

    success: int = 0
    error: int = 0
    last_run_at: datetime | None = None


_jobs: dict[str, JobStats] = {}


def get_job_stats(job_id: str) -> JobStats:
    """The (lazily created) stats slot for a scheduler job id."""
    stats = _jobs.get(job_id)
    if stats is None:
        stats = JobStats()
        _jobs[job_id] = stats
    return stats


def all_job_stats() -> dict[str, JobStats]:
    """A snapshot copy of every known job's stats, keyed by job id."""
    return dict(_jobs)


def reset_all() -> None:
    """Drop all job stats (used by tests for isolation)."""
    _jobs.clear()


def instrumented(job_id: str, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """Wrap a job coroutine function so every run ticks the registry.

    Registers the job id eagerly so every scheduled job appears in
    ``/api/metrics`` (with zero counts) from startup, not only after its
    first run.  Jobs are defensive end-to-end and should never raise; if
    one escapes anyway the run counts as an error and re-raises so
    APScheduler's own logging still fires.
    """
    get_job_stats(job_id)

    @functools.wraps(func)
    async def run() -> None:
        stats = get_job_stats(job_id)
        try:
            await func()
        except Exception:
            stats.error += 1
            stats.last_run_at = timeutil.utcnow()
            raise
        stats.success += 1
        stats.last_run_at = timeutil.utcnow()

    return run
