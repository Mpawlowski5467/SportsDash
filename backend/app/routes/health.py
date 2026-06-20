"""Health endpoint with a shallow deep-check.

``GET /health`` stays backward compatible — it always carries a
``status`` key (``"ok"`` or ``"degraded"``) and always returns 200, even
when the database probe fails (a 500 would be useless to an uptime
checker that needs to read the body).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.providers import registry
from app.services import circuit_breaker

logger = logging.getLogger(__name__)

router = APIRouter()


def _provider_health() -> tuple[dict[str, dict[str, object]], bool]:
    """Per-provider circuit state, plus whether any breaker is open.

    Every registered provider is reported; remote providers carry a live
    circuit-breaker (the mock has none, so it reads as permanently closed).
    """
    breakers = circuit_breaker.all_breakers()
    detail: dict[str, dict[str, object]] = {}
    any_open = False
    for provider_id in sorted(registry._providers):
        breaker = breakers.get(provider_id)
        if breaker is None:
            detail[provider_id] = {
                "registered": True,
                "circuit": circuit_breaker.CircuitState.CLOSED.value,
                "last_error_at": None,
            }
            continue
        snapshot = breaker.snapshot()
        detail[provider_id] = {
            "registered": True,
            "circuit": snapshot["state"],
            "last_error_at": snapshot["last_error_at"],
        }
        if breaker.state is circuit_breaker.CircuitState.OPEN:
            any_open = True
    return detail, any_open


@router.get("/health")
async def health(
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Liveness + shallow readiness probe.

    Runs a trivial ``SELECT 1`` to confirm the database is reachable and
    reports each provider's circuit-breaker state.  ``status`` degrades when
    the DB probe fails *or* any provider's breaker is open.  Never raises: a
    failed probe degrades the status rather than 500-ing.
    """
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check database probe failed")
        database_ok = False

    provider_health, any_provider_open = _provider_health()

    return {
        # Backward-compatible fields: `providers` stays the integer count.
        "status": "ok" if (database_ok and not any_provider_open) else "degraded",
        "database": database_ok,
        "providers": len(registry._providers),
        # Per-provider circuit-breaker detail (added with the resilience work).
        "provider_health": provider_health,
    }
