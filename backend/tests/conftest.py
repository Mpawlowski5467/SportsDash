"""Shared fixtures for the backend test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.providers import registry
from app.services import cache, http_client, tsdb_client


@pytest.fixture(autouse=True)
async def _close_shared_clients(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Each test owns a loop; pooled clients must close before that loop does.

    API fixtures do not run the production lifespan. Mirror its outbound
    client cleanup so a keep-alive socket cannot leak into a later test.
    Depending on monkeypatch keeps installed test clients in place until
    after this cleanup.
    """
    yield
    await registry.close_all()
    await cache.close_cache()
    await tsdb_client.close_client()
    await http_client.close_all()


@pytest.fixture(autouse=True)
def _fast_tsdb_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out the process-wide TSDB pacing gate.

    The real gate spaces requests ~0.34s apart to be polite to the shared
    free key; in tests that would add real sleeps to every TSDB-touching
    test, so the spacing is removed and the gate reset.
    """
    monkeypatch.setattr(tsdb_client, "_MIN_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(tsdb_client, "_next_allowed", 0.0)
