"""Unit tests for the TheSportsDB ``lookup_team_info`` enrichment."""

from __future__ import annotations

import httpx
import pytest

from app.services import stadiums
from tests.tsdb_mock import install_tsdb_handler


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    stadiums._cache.clear()
    stadiums._info_cache.clear()


async def test_lookup_team_info_reads_description_and_founded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "searchteams.php" in request.url.path
        return httpx.Response(
            200,
            json={
                "teams": [
                    {
                        "strTeam": "Chelsea",
                        "strSport": "Soccer",
                        "strStadium": "Stamford Bridge",
                        "strDescriptionEN": "Chelsea Football Club are a club...",
                        "intFormedYear": "1905",
                    }
                ]
            },
        )

    install_tsdb_handler(monkeypatch, handler)

    info = await stadiums.lookup_team_info("Chelsea", sport="soccer")
    assert info is not None
    assert info.description == "Chelsea Football Club are a club..."
    assert info.founded == 1905


async def test_lookup_team_info_none_when_no_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A hit with neither description nor founded year -> None.
        return httpx.Response(
            200,
            json={"teams": [{"strTeam": "Nowhere FC", "strSport": "Soccer"}]},
        )

    install_tsdb_handler(monkeypatch, handler)
    assert await stadiums.lookup_team_info("Nowhere FC", sport="soccer") is None


async def test_lookup_team_info_never_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="<html>rate limited</html>")

    install_tsdb_handler(monkeypatch, handler)
    assert await stadiums.lookup_team_info("Chelsea", sport="soccer") is None


async def test_lookup_team_info_empty_name_is_none() -> None:
    assert await stadiums.lookup_team_info("  ") is None


# ---------------------------------------------------------------------------
# Transient failures must NOT poison the cache (the off-season map bug)
# ---------------------------------------------------------------------------


async def test_lookup_stadium_does_not_cache_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate-limited (429) lookup is not cached, so a retry still resolves.

    The off-season map bug: during a busy refresh sweep TheSportsDB's shared
    free key returns a 429 for a club's lookup; if that ``None`` were cached
    the club (Arsenal, Liverpool, ...) would never re-resolve for the life of
    the process, and an off-season club with no fixtures to borrow a venue
    from would stay off the map.  The transient miss must be left uncached.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "searchteams.php" in request.url.path
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="<html>rate limited</html>")
        return httpx.Response(
            200,
            json={
                "teams": [
                    {
                        "strTeam": "Arsenal",
                        "strSport": "Soccer",
                        "strStadium": "Emirates Stadium",
                        "strLocation": "Holloway, London",
                    }
                ]
            },
        )

    install_tsdb_handler(monkeypatch, handler)

    # First call hits the transient 429 -> None, and MUST NOT be cached.
    assert await stadiums.lookup_stadium("Arsenal", sport="soccer") is None
    assert "arsenal|soccer" not in stadiums._cache

    # Same name, same process: the retry actually re-fetches and resolves.
    second = await stadiums.lookup_stadium("Arsenal", sport="soccer")
    assert second is not None
    assert second.venue == "Emirates Stadium, Holloway, London"
    assert calls["n"] == 2


async def test_lookup_stadium_caches_definitive_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A definitive miss (search succeeded, nothing matched) IS cached.

    The counterpart to the transient case: when the call succeeds but the
    team genuinely has no record, the ``None`` is cached so a known-missing
    team is not re-fetched on every refresh pass.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # HTTP 200 with no matching team — TheSportsDB's "no such team".
        return httpx.Response(200, json={"teams": None})

    install_tsdb_handler(monkeypatch, handler)

    assert await stadiums.lookup_stadium("Nowhere FC", sport="soccer") is None
    assert await stadiums.lookup_stadium("Nowhere FC", sport="soccer") is None
    # The miss was cached, so only ONE HTTP request was made.
    assert calls["n"] == 1
    assert stadiums._cache["nowhere fc|soccer"] is None


async def test_lookup_team_info_does_not_cache_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same no-poison guarantee holds for the club "About" lookup."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="<html>rate limited</html>")
        return httpx.Response(
            200,
            json={
                "teams": [
                    {
                        "strTeam": "Chelsea",
                        "strSport": "Soccer",
                        "strDescriptionEN": "Chelsea Football Club are a club...",
                        "intFormedYear": "1905",
                    }
                ]
            },
        )

    install_tsdb_handler(monkeypatch, handler)

    assert await stadiums.lookup_team_info("Chelsea", sport="soccer") is None
    assert "chelsea|soccer" not in stadiums._info_cache

    info = await stadiums.lookup_team_info("Chelsea", sport="soccer")
    assert info is not None and info.founded == 1905
