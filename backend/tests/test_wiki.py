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
