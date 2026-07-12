"""Test helper: route TheSportsDB requests through an httpx.MockTransport.

All TSDB callers (stadiums, player photos, the setup catalog) now share
one client + pacing gate in ``app.services.tsdb_client``, so tests
install their fake transport there instead of patching each module's
``httpx.AsyncClient``.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from app.services import tsdb_client


def install_tsdb_handler(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Serve every shared-TSDB-client request from ``handler``."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=tsdb_client.TSDB_BASE_URL,
        headers=tsdb_client.HEADERS,
    )
    monkeypatch.setattr(tsdb_client, "_client", client)
