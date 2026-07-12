"""The actual ntfy POST wire format — URL topic, body, and headers.

test_notify_prefs.py only ever spies on send_event, so a malformed
notification body would previously ship unnoticed.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import http_client, notify


def _settings(**overrides: object) -> SimpleNamespace:
    base = dict(
        notifications_enabled=True,
        ntfy_url="http://ntfy.test:8090",
        ntfy_topic="fictional-topic",
        ntfy_token=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    monkeypatch.setitem(
        http_client._clients,
        "ntfy",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


async def test_send_posts_topic_body_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "get_settings", _settings)
    seen = _capture(monkeypatch)

    ok = await notify.send(
        "Kickoff", "Glimmer Foxes vs Quarry Hawks", tags="soccer", priority="high"
    )

    assert ok is True
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "http://ntfy.test:8090/fictional-topic"
    assert request.content == "Glimmer Foxes vs Quarry Hawks".encode()
    assert request.headers["Title"] == "Kickoff"
    assert request.headers["Tags"] == "soccer"
    assert request.headers["Priority"] == "high"
    assert "Authorization" not in request.headers


async def test_send_carries_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notify, "get_settings", lambda: _settings(ntfy_token="fictional-token")
    )
    seen = _capture(monkeypatch)

    assert await notify.send("Hello", "body") is True
    assert seen[0].headers["Authorization"] == "Bearer fictional-token"


async def test_send_failure_returns_false_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "get_settings", _settings)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setitem(
        http_client._clients,
        "ntfy",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await notify.send("Hello", "body") is False


async def test_send_disabled_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notify, "get_settings", lambda: _settings(notifications_enabled=False)
    )
    seen = _capture(monkeypatch)
    assert await notify.send("Hello", "body") is True
    assert seen == []
