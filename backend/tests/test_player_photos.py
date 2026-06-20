"""Unit tests for the TheSportsDB keyless player-headshot lookup."""
from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services import player_photos


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return None

    async def _set(*_a, **_k):
        return None

    monkeypatch.setattr(player_photos.cache, "cache_get_json", _get)
    monkeypatch.setattr(player_photos.cache, "cache_set_json", _set)
    # No real inter-request delay in tests.
    monkeypatch.setattr(player_photos, "_REQUEST_SPACING", 0.0)


def _route(handler) -> object:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    # Forward base_url/timeout/headers so the relative "searchplayers.php" path
    # resolves against TheSportsDB's base URL.
    return lambda *a, **k: real_client(transport=transport, **k)


def _players(*players: dict) -> dict:
    return {"player": list(players)}


async def test_lookup_photo_returns_cutout_with_team_disambiguation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two players share the name; the club picks the right one.
    payload = _players(
        {
            "strPlayer": "Cole Palmer",
            "strSport": "Soccer",
            "strTeam": "Manchester City",
            "strCutout": "https://img/wrong.png",
        },
        {
            "strPlayer": "Cole Palmer",
            "strSport": "Soccer",
            "strTeam": "Chelsea",
            "strCutout": "https://img/palmer.png",
            "strThumb": "https://img/palmer-thumb.jpg",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "searchplayers.php" in request.url.path
        assert request.url.params.get("p") == "Cole Palmer"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(player_photos.httpx, "AsyncClient", _route(handler))
    url = await player_photos.lookup_photo(
        "Cole Palmer", team_name="Chelsea", sport="soccer"
    )
    assert url == "https://img/palmer.png"


async def test_lookup_photo_falls_back_to_thumb_then_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _players(
        {
            "strPlayer": "No Cutout",
            "strSport": "Soccer",
            "strTeam": "Chelsea",
            "strThumb": "https://img/thumb.jpg",
        }
    )
    monkeypatch.setattr(
        player_photos.httpx,
        "AsyncClient",
        _route(lambda r: httpx.Response(200, json=payload)),
    )
    url = await player_photos.lookup_photo("No Cutout", team_name="Chelsea", sport="soccer")
    assert url == "https://img/thumb.jpg"


async def test_lookup_photo_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        player_photos.httpx,
        "AsyncClient",
        _route(lambda r: httpx.Response(200, json={"player": None})),
    )
    assert (
        await player_photos.lookup_photo("Nobody", team_name="Chelsea", sport="soccer")
        is None
    )


async def test_lookup_photo_transient_429_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no retries, a 429 surfaces as a transient failure immediately; the
    # miss must NOT be cached, so the player is retried next refresh.
    monkeypatch.setattr(get_settings(), "provider_max_retries", 0)
    saved: list = []

    async def _set(key, value, ttl):
        saved.append((key, value))

    monkeypatch.setattr(player_photos.cache, "cache_set_json", _set)
    monkeypatch.setattr(
        player_photos.httpx,
        "AsyncClient",
        _route(lambda r: httpx.Response(429, text="rate limited")),
    )
    result = await player_photos.lookup_photo(
        "Cole Palmer", team_name="Chelsea", sport="soccer"
    )
    assert result is None
    assert saved == []  # transient failure left uncached


async def test_lookup_photo_genuine_miss_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list = []

    async def _set(key, value, ttl):
        saved.append(value)

    monkeypatch.setattr(player_photos.cache, "cache_set_json", _set)
    monkeypatch.setattr(
        player_photos.httpx,
        "AsyncClient",
        _route(lambda r: httpx.Response(200, json={"player": []})),
    )
    assert await player_photos.lookup_photo("Nobody", sport="soccer") is None
    assert {"url": None} in saved  # a genuine miss IS cached (to avoid re-querying)


async def test_lookup_photo_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return {"url": "https://img/cached.png"}

    monkeypatch.setattr(player_photos.cache, "cache_get_json", _get)

    def boom(_r: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("network hit on a cache hit")

    monkeypatch.setattr(player_photos.httpx, "AsyncClient", _route(boom))
    url = await player_photos.lookup_photo("Cole Palmer", team_name="Chelsea")
    assert url == "https://img/cached.png"


async def test_lookup_photo_empty_name_is_none() -> None:
    assert await player_photos.lookup_photo("   ", team_name="Chelsea") is None


def test_pick_photo_filters_by_sport() -> None:
    payload = _players(
        {"strPlayer": "Mike Smith", "strSport": "Basketball", "strCutout": "b.png"},
        {"strPlayer": "Mike Smith", "strSport": "Soccer", "strCutout": "s.png"},
    )
    assert player_photos._pick_photo(payload, None, "soccer") == "s.png"
