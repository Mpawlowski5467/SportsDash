"""Prometheus metrics endpoint (hand-rolled text exposition).

``GET /metrics`` renders exposition format 0.0.4 by hand — a dozen
gauge/counter series don't justify a prometheus_client dependency (and
the desktop PyInstaller bundle stays unchanged).  Every series is read
from the SAME sources ``/api/health`` reads (a live ``SELECT 1`` probe,
the circuit-breaker registry) so the two endpoints can never tell
different stories about the same breaker.  Never raises: a failed
database probe becomes ``sportsdash_database_up 0``, not a 500.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import background
from app.db import get_session
from app.providers import registry
from app.services import cache, circuit_breaker, metrics_state

logger = logging.getLogger(__name__)

router = APIRouter()

# Exposition format 0.0.4 — the version parameter is part of the contract.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# CircuitState -> gauge value; the mapping is repeated in the HELP line.
_CIRCUIT_VALUE = {
    circuit_breaker.CircuitState.CLOSED: 0,
    circuit_breaker.CircuitState.HALF_OPEN: 1,
    circuit_breaker.CircuitState.OPEN: 2,
}


def _escape(value: str) -> str:
    """Escape a label value per the exposition format (backslash, quote, LF)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _family(name: str, kind: str, help_text: str, samples: list[str]) -> list[str]:
    """``# HELP`` / ``# TYPE`` header plus rendered samples (possibly none)."""
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", *samples]


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Prometheus scrape target mirroring ``/api/health``."""
    # Same trivial probe the health endpoint runs.
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Metrics database probe failed")
        database_ok = False

    breakers = circuit_breaker.all_breakers()
    state_samples: list[str] = []
    failure_samples: list[str] = []
    error_at_samples: list[str] = []
    for provider_id in registry.provider_ids():
        label = f'provider="{_escape(provider_id)}"'
        breaker = breakers.get(provider_id)
        if breaker is None:
            # No live breaker (provider never called): /api/health reads
            # this as permanently closed, so the gauges must agree.
            state_samples.append(f"sportsdash_provider_circuit_state{{{label}}} 0")
            failure_samples.append(f"sportsdash_provider_consecutive_failures{{{label}}} 0")
            continue
        state_samples.append(
            f"sportsdash_provider_circuit_state{{{label}}} {_CIRCUIT_VALUE[breaker.state]}"
        )
        failure_samples.append(
            f"sportsdash_provider_consecutive_failures{{{label}}} {breaker.failures}"
        )
        if breaker.last_error_at is not None:
            error_at_samples.append(
                f"sportsdash_provider_last_error_timestamp_seconds{{{label}}}"
                f" {breaker.last_error_at.timestamp()}"
            )

    run_samples: list[str] = []
    last_run_samples: list[str] = []
    for job_id, stats in sorted(metrics_state.all_job_stats().items()):
        label = f'job="{_escape(job_id)}"'
        run_samples.append(
            f'sportsdash_scheduler_job_runs_total{{{label},result="success"}} {stats.success}'
        )
        run_samples.append(
            f'sportsdash_scheduler_job_runs_total{{{label},result="error"}} {stats.error}'
        )
        if stats.last_run_at is not None:
            last_run_samples.append(
                f"sportsdash_scheduler_job_last_run_timestamp_seconds{{{label}}}"
                f" {stats.last_run_at.timestamp()}"
            )

    hits, misses = cache.cache_counters()

    lines = [
        *_family(
            "sportsdash_database_up",
            "gauge",
            "Whether the SELECT 1 database probe succeeded (1) or failed (0); the same probe /api/health runs.",
            [f"sportsdash_database_up {1 if database_ok else 0}"],
        ),
        *_family(
            "sportsdash_provider_circuit_state",
            "gauge",
            "Provider circuit-breaker state: 0 closed, 1 half_open, 2 open.",
            state_samples,
        ),
        *_family(
            "sportsdash_provider_consecutive_failures",
            "gauge",
            "Consecutive failed calls per provider; resets to 0 on any success.",
            failure_samples,
        ),
        *_family(
            "sportsdash_provider_last_error_timestamp_seconds",
            "gauge",
            "Unix time of the provider's most recent error; absent for providers that never errored.",
            error_at_samples,
        ),
        *_family(
            "sportsdash_background_tasks",
            "gauge",
            "Fire-and-forget background tasks currently in flight.",
            # The strong-reference set IS the in-flight set; len is the gauge.
            [f"sportsdash_background_tasks {len(background._tasks)}"],
        ),
        *_family(
            "sportsdash_cache_hits_total",
            "counter",
            "Redis cache reads that returned a decoded value.",
            [f"sportsdash_cache_hits_total {hits}"],
        ),
        *_family(
            "sportsdash_cache_misses_total",
            "counter",
            "Redis cache reads that returned nothing (absent key, no client configured, or any error).",
            [f"sportsdash_cache_misses_total {misses}"],
        ),
        *_family(
            "sportsdash_scheduler_job_runs_total",
            "counter",
            "Completed scheduler job runs by outcome.",
            run_samples,
        ),
        *_family(
            "sportsdash_scheduler_job_last_run_timestamp_seconds",
            "gauge",
            "Unix time each scheduler job last finished; absent before a job's first run.",
            last_run_samples,
        ),
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type=CONTENT_TYPE)
