"""Shared long-lived httpx clients for the lighter outbound callers.

The two registered providers own their clients (closed via
``registry.close_all``) and TheSportsDB helpers share
``services.tsdb_client``; the remaining small services (ntfy, Nominatim,
Open-Meteo) previously built a throwaway ``httpx.AsyncClient`` per call,
losing connection pooling — the map view would build one client per
weather pin.  They now share named clients, created lazily with the
caller's timeout/headers and closed once at shutdown.

Tests can install a ``httpx.MockTransport``-backed client with
``monkeypatch.setitem(http_client._clients, "<name>", mock_client)``.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_clients: dict[str, httpx.AsyncClient] = {}


def get_client(
    name: str,
    *,
    timeout: httpx.Timeout | float | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """The shared client for ``name``, built on first use.

    ``timeout``/``headers`` apply only when the client is (re)built, so a
    given name should always be requested with the same configuration.
    """
    client = _clients.get(name)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout, headers=headers)
        _clients[name] = client
    return client


async def close_all() -> None:
    """Close every shared client (app shutdown)."""
    while _clients:
        name, client = _clients.popitem()
        try:
            await client.aclose()
        except Exception:
            logger.debug("http_client: closing %r failed", name, exc_info=True)
