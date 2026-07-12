"""Unit tests for the keyless Wikipedia club-summary enrichment."""

from __future__ import annotations

import httpx
import pytest

from app.services import wiki


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return None

    async def _set(*_a, **_k):
        return None

    monkeypatch.setattr(wiki.cache, "cache_get_json", _get)
    monkeypatch.setattr(wiki.cache, "cache_set_json", _set)


def _route(handler) -> object:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    return lambda *a, **k: real_client(transport=transport, **k)


def _dispatch(search_title: str, summary: dict):
    """Build a handler that answers the search then the REST summary call."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/w/api.php" in request.url.path:
            return httpx.Response(
                200,
                json={"query": {"search": [{"title": search_title}]}},
            )
        return httpx.Response(200, json=summary)

    return handler


async def test_team_summary_resolves_title_then_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = {
        "type": "standard",
        "title": "Chelsea F.C.",
        "extract": "Chelsea Football Club is an English club.",
        "thumbnail": {"source": "https://img/crest.png"},
    }
    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(_dispatch("Chelsea F.C.", summary)))

    result = await wiki.team_summary("Chelsea", sport="soccer")
    assert result is not None
    assert result.title == "Chelsea F.C."
    assert result.extract == "Chelsea Football Club is an English club."
    assert result.image_url == "https://img/crest.png"


async def test_team_summary_skips_rivalry_and_list_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The search returns a junk page first, then the real club article — the
    # bad-title filter must skip the rivalry and resolve the club.
    def handler(request: httpx.Request) -> httpx.Response:
        if "/w/api.php" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "search": [
                            {"title": "Arsenal F.C.–Chelsea F.C. rivalry"},
                            {"title": "List of Chelsea F.C. seasons"},
                            {"title": "Chelsea F.C."},
                        ]
                    }
                },
            )
        assert "Chelsea_F.C." in request.url.path
        return httpx.Response(
            200, json={"type": "standard", "title": "Chelsea F.C.", "extract": "x"}
        )

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    result = await wiki.team_summary("Chelsea", sport="soccer")
    assert result is not None and result.title == "Chelsea F.C."


async def test_team_summary_none_for_disambiguation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = {"type": "disambiguation", "title": "Chelsea", "extract": "many things"}
    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(_dispatch("Chelsea", summary)))
    assert await wiki.team_summary("Chelsea") is None


async def test_team_summary_never_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    assert await wiki.team_summary("Chelsea", sport="soccer") is None


async def test_team_summary_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "wiki_enabled", False)
    assert await wiki.team_summary("Chelsea") is None


async def test_team_summary_empty_name_is_none() -> None:
    assert await wiki.team_summary("   ") is None


# ---------------------------------------------------------------------------
# player_photo — per-player headshot fallback (soccer)
# ---------------------------------------------------------------------------


async def test_player_photo_resolves_with_club_qualified_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/w/api.php" in request.url.path:
            seen["srsearch"] = request.url.params.get("srsearch", "")
            return httpx.Response(200, json={"query": {"search": [{"title": "Cole Palmer"}]}})
        assert "Cole_Palmer" in request.url.path
        return httpx.Response(
            200,
            json={
                "type": "standard",
                "title": "Cole Palmer",
                # originalimage is preferred over thumbnail.
                "originalimage": {"source": "https://img/palmer.jpg"},
                "thumbnail": {"source": "https://img/palmer-thumb.jpg"},
            },
        )

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    url = await wiki.player_photo("Cole Palmer", team_name="Chelsea", sport="soccer")
    assert url == "https://img/palmer.jpg"
    # Club + sport-qualified so a common name resolves to the right athlete.
    assert "Cole Palmer" in seen["srsearch"]
    assert "Chelsea" in seen["srsearch"]
    assert "footballer" in seen["srsearch"]


async def test_player_photo_skips_wrong_person_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The player has no article of their own, so the search falls through to
    # the club / season pages — the name-match guard must reject every result
    # (the wrong face is worse than the initials chip) and never fetch a
    # summary.
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/w/api.php" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "search": [
                            {"title": "Chelsea F.C."},
                            {"title": "2024–25 Chelsea F.C. season"},
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"type": "standard", "title": "x"})

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    result = await wiki.player_photo("Reserve Youngster", team_name="Chelsea", sport="soccer")
    assert result is None
    assert not any("/page/summary/" in path for path in calls)


async def test_player_photo_matches_name_with_diacritics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rosters and Wikipedia spell diacritics inconsistently — the name-match
    # guard is accent-insensitive, so "Estêvão" still matches its article.
    def handler(request: httpx.Request) -> httpx.Response:
        if "/w/api.php" in request.url.path:
            return httpx.Response(
                200,
                json={"query": {"search": [{"title": "Estêvão (footballer, born 2007)"}]}},
            )
        return httpx.Response(
            200,
            json={
                "type": "standard",
                "title": "Estêvão",
                "thumbnail": {"source": "https://img/estevao.jpg"},
            },
        )

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    url = await wiki.player_photo("Estêvão", team_name="Chelsea", sport="soccer")
    assert url == "https://img/estevao.jpg"


async def test_player_photo_none_when_summary_has_no_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = {"type": "standard", "title": "Cole Palmer", "extract": "A footballer."}
    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(_dispatch("Cole Palmer", summary)))
    assert await wiki.player_photo("Cole Palmer", team_name="Chelsea", sport="soccer") is None


async def test_player_photo_never_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    assert await wiki.player_photo("Cole Palmer", team_name="Chelsea", sport="soccer") is None


async def test_player_photo_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "wiki_enabled", False)
    assert await wiki.player_photo("Cole Palmer", team_name="Chelsea", sport="soccer") is None


async def test_player_photo_empty_name_is_none() -> None:
    assert await wiki.player_photo("   ", team_name="Chelsea", sport="soccer") is None


async def test_player_photo_returns_cached_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _get(_key: str):
        return {"image_url": "https://img/cached.jpg"}

    monkeypatch.setattr(wiki.cache, "cache_get_json", _get)

    def boom(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("network hit on a cache hit")

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(boom))
    url = await wiki.player_photo("Cole Palmer", team_name="Chelsea", sport="soccer")
    assert url == "https://img/cached.jpg"


async def test_player_photo_caches_miss_to_avoid_reflooding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}

    async def _set(key: str, value, _ttl: int):
        saved[key] = value

    monkeypatch.setattr(wiki.cache, "cache_set_json", _set)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/w/api.php" in request.url.path:
            return httpx.Response(200, json={"query": {"search": [{"title": "Chelsea F.C."}]}})
        return httpx.Response(200, json={"type": "standard", "title": "x"})

    monkeypatch.setattr(wiki.httpx, "AsyncClient", _route(handler))
    result = await wiki.player_photo("Nobody Here", team_name="Chelsea", sport="soccer")
    assert result is None
    # A negative result is cached so the same photoless player isn't re-queried
    # on every daily roster refresh.
    assert any(value == {"image_url": None} for value in saved.values())
