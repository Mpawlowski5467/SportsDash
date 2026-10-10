"""The actual ntfy POST wire format — server-root URL, JSON body, and headers.

test_notify_prefs.py only ever spies on send_event, so a malformed
notification body would previously ship unnoticed.  Titles and messages
are provider text ("Getafe @ Atlético Madrid"), so the non-ASCII cases
below are the everyday ones rather than an edge.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.models.domain import EventType, GameEvent
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


async def test_send_posts_json_body_to_server_root(
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
    # ntfy reads a JSON publish only at the server root; the topic rides in
    # the body instead of the path.
    assert str(request.url) == "http://ntfy.test:8090/"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "topic": "fictional-topic",
        "title": "Kickoff",
        "message": "Glimmer Foxes vs Quarry Hawks",
        "tags": ["soccer"],
        "priority": 4,
    }
    for header in ("Title", "Tags", "Priority", "Authorization"):
        assert header not in request.headers


async def test_send_delivers_non_ascii_title_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accented team name used to cost the whole notification.

    httpx encodes ``str`` header values as ASCII, so as a ``Title`` header
    this title raised UnicodeEncodeError inside ``client.post``: ``send``
    reported a failure and no request ever left the process.
    """
    monkeypatch.setattr(notify, "get_settings", _settings)
    seen = _capture(monkeypatch)
    title = "Getafe @ Atlético Madrid"
    message = "Bayer Leverkusen 1, 1. FC Köln 2 — End of 1st half"

    assert await notify.send(title, message) is True

    assert len(seen) == 1
    request = seen[0]
    assert json.loads(request.content) == {
        "topic": "fictional-topic",
        "title": title,
        "message": message,
    }
    assert "Title" not in request.headers


async def test_send_event_delivers_accented_team_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler's real entry point, with the tag and priority it adds.

    A FINAL that fails to send is retried every tick for the resend
    window, so one that can never be encoded fails every one of them.
    """
    monkeypatch.setattr(notify, "get_settings", _settings)
    seen = _capture(monkeypatch)
    event = GameEvent(
        type=EventType.FINAL,
        game_id="fictional-game",
        title="Bodø/Glimt @ Atlético Madrid",
        message="Final: Bodø/Glimt 1, Atlético Madrid 3.",
        dedupe_key="fictional-game:final",
    )

    assert await notify.send_event(event) is True

    assert len(seen) == 1
    assert json.loads(seen[0].content) == {
        "topic": "fictional-topic",
        "title": "Bodø/Glimt @ Atlético Madrid",
        "message": "Final: Bodø/Glimt 1, Atlético Madrid 3.",
        "tags": ["checkered_flag"],
        "priority": 4,
    }


async def test_send_omits_tags_and_priority_it_cannot_express(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON form takes a list of tags and an integer priority.

    Blank tags, and a priority name ntfy has no level for, are left out
    rather than sent in a shape the server would have to reject: neither
    is worth losing the notification over.
    """
    monkeypatch.setattr(notify, "get_settings", _settings)
    seen = _capture(monkeypatch)

    assert await notify.send("Hello", "body", tags=" trophy, ,soccer,", priority="loud") is True
    assert await notify.send("Hello", "body", tags=" , ") is True

    assert json.loads(seen[0].content) == {
        "topic": "fictional-topic",
        "title": "Hello",
        "message": "body",
        "tags": ["trophy", "soccer"],
    }
    assert json.loads(seen[1].content) == {
        "topic": "fictional-topic",
        "title": "Hello",
        "message": "body",
    }


async def test_send_carries_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(ntfy_token="fictional-token"))
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
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(notifications_enabled=False))
    seen = _capture(monkeypatch)
    assert await notify.send("Hello", "body") is True
    assert seen == []
