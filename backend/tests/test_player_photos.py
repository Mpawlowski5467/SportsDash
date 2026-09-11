"""Unit tests for the TheSportsDB keyless player-headshot lookup."""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services import player_photos
from tests.tsdb_mock import install_tsdb_handler


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return None

    async def _set(*_a, **_k):
        return None

    monkeypatch.setattr(player_photos.cache, "cache_get_json", _get)
    monkeypatch.setattr(player_photos.cache, "cache_set_json", _set)


def _players(*players: dict) -> dict:
    return {"player": list(players)}


async def test_lookup_photo_returns_cutout_with_team_disambiguation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two players share the name; the club picks the right one.
    payload = _players(
        {
            "strPlayer": "Rowan Vale",
            "strSport": "Soccer",
            "strTeam": "Northport City",
            "strCutout": "https://img/wrong.png",
        },
        {
            "strPlayer": "Rowan Vale",
            "strSport": "Soccer",
            "strTeam": "Harbor FC",
            "strCutout": "https://img/vale.png",
            "strThumb": "https://img/vale-thumb.jpg",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "searchplayers.php" in request.url.path
        assert request.url.params.get("p") == "Rowan Vale"
        return httpx.Response(200, json=payload)

    install_tsdb_handler(monkeypatch, handler)
    url = await player_photos.lookup_photo("Rowan Vale", team_name="Harbor FC", sport="soccer")
    assert url == "https://img/vale.png"


async def test_lookup_photo_falls_back_to_thumb_then_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _players(
        {
            "strPlayer": "No Cutout",
            "strSport": "Soccer",
            "strTeam": "Harbor FC",
            "strThumb": "https://img/thumb.jpg",
        }
    )
    install_tsdb_handler(monkeypatch, lambda r: httpx.Response(200, json=payload))
    url = await player_photos.lookup_photo("No Cutout", team_name="Harbor FC", sport="soccer")
    assert url == "https://img/thumb.jpg"


async def test_lookup_photo_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_tsdb_handler(monkeypatch, lambda r: httpx.Response(200, json={"player": None}))
    assert await player_photos.lookup_photo("Nobody", team_name="Harbor FC", sport="soccer") is None


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
    install_tsdb_handler(monkeypatch, lambda r: httpx.Response(429, text="rate limited"))
    result = await player_photos.lookup_photo("Rowan Vale", team_name="Harbor FC", sport="soccer")
    assert result is None
    assert saved == []  # transient failure left uncached


async def test_lookup_photo_genuine_miss_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list = []

    async def _set(key, value, ttl):
        saved.append(value)

    monkeypatch.setattr(player_photos.cache, "cache_set_json", _set)
    install_tsdb_handler(monkeypatch, lambda r: httpx.Response(200, json={"player": []}))
    assert await player_photos.lookup_photo("Nobody", sport="soccer") is None
    assert {"url": None} in saved  # a genuine miss IS cached (to avoid re-querying)


async def test_lookup_photo_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return {"url": "https://img/cached.png"}

    monkeypatch.setattr(player_photos.cache, "cache_get_json", _get)

    def boom(_r: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("network hit on a cache hit")

    install_tsdb_handler(monkeypatch, boom)
    url = await player_photos.lookup_photo("Rowan Vale", team_name="Harbor FC")
    assert url == "https://img/cached.png"


async def test_lookup_photo_empty_name_is_none() -> None:
    assert await player_photos.lookup_photo("   ", team_name="Harbor FC") is None


def test_pick_photo_filters_by_sport() -> None:
    payload = _players(
        {"strPlayer": "Sable Marsh", "strSport": "Basketball", "strCutout": "https://img/b.png"},
        {"strPlayer": "Sable Marsh", "strSport": "Soccer", "strCutout": "https://img/s.png"},
    )
    assert player_photos._pick_photo(payload, None, "soccer") == "https://img/s.png"


@pytest.mark.parametrize(
    "candidate",
    [
        {"strPlayer": "Different Person", "strSport": "Soccer"},
        {"strPlayer": "Rowan Vale", "strSport": "Basketball"},
    ],
)
async def test_lookup_never_borrows_another_players_photo(monkeypatch, candidate):
    payload = _players({**candidate, "strCutout": "https://img/wrong.png"})
    install_tsdb_handler(monkeypatch, lambda r: httpx.Response(200, json=payload))
    assert await player_photos.lookup_photo("Rowan Vale", sport="soccer") is None


def test_ambiguous_namesakes_without_matching_club_are_not_guessed():
    payload = _players(
        {"strPlayer": "Rowan Vale", "strTeam": "Northport", "strCutout": "https://img/a.png"},
        {"strPlayer": "Rowan Vale", "strTeam": "Eastport", "strCutout": "https://img/b.png"},
    )
    assert player_photos._pick_photo(payload, "Harbor FC", "soccer", name="Rowan Vale") is None


def test_exact_name_matches_accents_and_transfer_with_safe_image_fallback():
    payload = _players(
        {
            "strPlayer": "Rówan Valé",
            "strSport": "Soccer",
            "strTeam": "Previous Club",
            "strCutout": "javascript:alert(1)",
            "strThumb": "https://img/vale.jpg",
        }
    )
    assert (
        player_photos._pick_photo(payload, "Harbor FC", "soccer", name="Rowan Vale")
        == "https://img/vale.jpg"
    )


async def test_photo_cache_separates_sports(monkeypatch):
    keys = []

    async def read(key):
        keys.append(key)
        return {"url": None}

    monkeypatch.setattr(player_photos.cache, "cache_get_json", read)
    await player_photos.lookup_photo("Rowan Vale", sport="soccer")
    await player_photos.lookup_photo("Rowan Vale", sport="basketball")
    assert len(set(keys)) == 2
