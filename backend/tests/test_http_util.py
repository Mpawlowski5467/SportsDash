"""Retry/backoff tests for the shared provider HTTP helper."""
from __future__ import annotations

import httpx
import pytest

from app.providers import http_util


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make backoff instant so retry tests don't actually wait."""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(http_util.asyncio, "sleep", fake_sleep)


async def test_retries_429_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await http_util.get_with_retry(
            client, "http://x/y", max_retries=3, backoff_base=0.01
        )
    assert resp.status_code == 200
    assert attempts["n"] == 3


async def test_retries_transport_error_then_raises() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("no route")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(http_util.TransientProviderError) as exc_info:
            await http_util.get_with_retry(
                client, "http://x", max_retries=2, backoff_base=0.01
            )
    # Give-up surfaces as a transient error (so the breaker counts it), with
    # the original transport error preserved as __cause__.
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    # Initial try + 2 retries.
    assert attempts["n"] == 3


async def test_does_not_retry_404() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await http_util.get_with_retry(
            client, "http://x", max_retries=3, backoff_base=0.01
        )
    # 404 isn't retryable: returned immediately for the caller to raise.
    assert resp.status_code == 404
    assert attempts["n"] == 1


async def test_gives_up_after_max_retries_on_persistent_5xx() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(http_util.TransientProviderError):
            await http_util.get_with_retry(
                client, "http://x", max_retries=2, backoff_base=0.01
            )
    # A persistent 5xx is surfaced as transient (not returned for the caller to
    # swallow), so the breaker counts it; 1 + 2 retries.
    assert attempts["n"] == 3


async def test_gives_up_after_max_retries_on_persistent_429() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        # TheSportsDB's free-tier rate limit: 429 with an HTML body.
        attempts["n"] += 1
        return httpx.Response(429, text="<html>rate limited</html>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(http_util.TransientProviderError):
            await http_util.get_with_retry(
                client, "http://x", max_retries=2, backoff_base=0.01
            )
    # Sustained rate-limiting raises transient (the breaker can finally open);
    # never reaches the caller's json() so the HTML body is irrelevant.
    assert attempts["n"] == 3
