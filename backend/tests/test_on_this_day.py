"""``GET /history/on-this-day``: the month-day scan over season archives.

Fictional names only; ESPN goes through MockTransport on the shared
"espn-history" client so the real schedule parsing/serialization runs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.models.orm import LeagueORM, TeamORM
from app.routes import router as api_router
from app.schemas import GameOut, GameSideOut
from app.services import cache, http_client, on_this_day
from app.timeutil import UTC, local_today, utcnow
from tests.db_engine import create_test_schema, make_test_engine

LEAGUE_ID = "cinder-loop"
TEAM_ID = "ember-otters"


def _route_history(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setitem(
        http_client._clients,
        "espn-history",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _past_match_day(today: date) -> date:
    """The most recent prior year (within the scan window) with today's
    month-day — leap-safe should the suite ever run on Feb 29."""
    for years_back in range(2, on_this_day.SEASONS_BACK + 1):
        try:
            return today.replace(year=today.year - years_back)
        except ValueError:
            continue
    raise AssertionError("no leap year within the scan window")


def _local_game_iso(day: date, hour: int) -> str:
    """UTC ISO timestamp for ``day`` at ``hour`` local (settings tz)."""
    local_dt = datetime(day.year, day.month, day.day, hour, 0, tzinfo=get_settings().tzinfo)
    return local_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _final_event(event_id: str, start_iso: str) -> dict:
    return {
        "id": event_id,
        "date": start_iso,
        "competitions": [
            {
                "id": event_id,
                "date": start_iso,
                "status": {"type": {"state": "post", "completed": True, "name": "STATUS_FINAL"}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": {"value": 104},
                        "team": {
                            "displayName": "Ember Otters",
                            "abbreviation": "EMO",
                            "id": "901",
                        },
                    },
                    {
                        "homeAway": "away",
                        "score": {"value": 97},
                        "team": {
                            "displayName": "Quill Martens",
                            "abbreviation": "QLM",
                            "id": "902",
                        },
                    },
                ],
            }
        ],
    }


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(db: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="basketball",
                name="Cinder Loop Basketball",
                provider="espn",
                provider_key="basketball/cinderloop",
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamORM(
                id=TEAM_ID,
                league_id=LEAGUE_ID,
                name="Ember Otters",
                abbreviation="EMO",
                provider_key="901",
                rss_feeds=[],
            )
        )
        await session.commit()

    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_on_this_day_matches_month_day_across_seasons(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A past game on today's LOCAL month-day surfaces even when its
    calendar year differs from the season key it was served under (the
    cross-year season case) — and same-season games on other days don't."""
    today = local_today(get_settings().tzinfo)
    past_day = _past_match_day(today)
    match_season = utcnow().year - 1
    # 20:00 local lands on the NEXT UTC day for US timezones, so this also
    # pins the filter to the local date rather than the stored UTC one.
    payload = {
        "events": [
            _final_event("otd-1", _local_game_iso(past_day, 20)),
            _final_event("otd-2", _local_game_iso(past_day + timedelta(days=1), 20)),
        ]
    }
    seasons_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seasons_seen.append(request.url.params.get("season", ""))
        if request.url.params.get("season") == str(match_season):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"events": []})

    _route_history(monkeypatch, handler)
    response = await client.get("/api/history/on-this-day")
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == today.isoformat()
    assert len(body["teams"]) == 1
    group = body["teams"][0]
    assert group["team_id"] == TEAM_ID
    assert group["team_name"] == "Ember Otters"
    assert [game["id"] for game in group["games"]] == ["espn:otd-1"]
    assert group["games"][0]["home"]["score"] == 104
    assert group["games"][0]["followed_team_ids"] == [TEAM_ID]
    # The full head-to-head-style window was scanned, one call per season.
    now_year = utcnow().year
    assert set(seasons_seen) == {
        str(season) for season in range(now_year, now_year - on_this_day.SEASONS_BACK, -1)
    }


async def test_on_this_day_excludes_actual_today(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = local_today(get_settings().tzinfo)
    payload = {"events": [_final_event("otd-now", _local_game_iso(today, 12))]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("season") == str(utcnow().year):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"events": []})

    _route_history(monkeypatch, handler)
    response = await client.get("/api/history/on-this-day")
    assert response.status_code == 200
    assert response.json()["teams"] == []


async def test_on_this_day_skips_unsupported_teams(
    client: AsyncClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Individual-sport and non-espn teams never reach ESPN and never
    appear in the payload — the supported team still does."""
    async with db() as session:
        session.add(
            LeagueORM(
                id="lantern-tour",
                sport="tennis",
                name="Lantern Tour",
                provider="espn",
                provider_key="tennis/lantern",
            )
        )
        session.add(
            LeagueORM(
                id="harbor-volley",
                sport="volleyball",
                name="Harbor Volley",
                provider="thesportsdb",
                provider_key="7011",
            )
        )
        await session.flush()
        session.add(
            TeamORM(
                id="petal-vane",
                league_id="lantern-tour",
                name="Petal Vane",
                abbreviation="PTV",
                provider_key="911",
                rss_feeds=[],
            )
        )
        session.add(
            TeamORM(
                id="wave-pipers",
                league_id="harbor-volley",
                name="Wave Pipers",
                abbreviation="WVP",
                provider_key="912",
                rss_feeds=[],
            )
        )
        await session.commit()

    today = local_today(get_settings().tzinfo)
    past_day = _past_match_day(today)
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.params.get("season") == str(utcnow().year - 1):
            return httpx.Response(
                200, json={"events": [_final_event("otd-1", _local_game_iso(past_day, 19))]}
            )
        return httpx.Response(200, json={"events": []})

    _route_history(monkeypatch, handler)
    response = await client.get("/api/history/on-this-day")
    assert response.status_code == 200
    assert [group["team_id"] for group in response.json()["teams"]] == [TEAM_ID]
    # Only the basketball team was ever fetched.
    assert requested_paths
    assert all("/teams/901/" in path for path in requested_paths)


async def test_on_this_day_empty_is_200(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _route_history(monkeypatch, lambda r: httpx.Response(200, json={"events": []}))
    response = await client.get("/api/history/on-this-day")
    assert response.status_code == 200
    body = response.json()
    assert body["teams"] == []
    assert body["date"] == local_today(get_settings().tzinfo).isoformat()


async def test_on_this_day_cache_hit_short_circuits(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = local_today(get_settings().tzinfo)
    month_day = f"{today.month:02d}-{today.day:02d}"
    cached_game = GameOut(
        id="espn:otd-cached",
        league_id=LEAGUE_ID,
        sport="basketball",
        home=GameSideOut(team_id=TEAM_ID, name="Ember Otters", score=88),
        away=GameSideOut(name="Quill Martens", score=80),
        start_time=utcnow() - timedelta(days=730),
        phase="final",
        period=4,
        period_label="Q4",
        is_intermission=False,
        followed_team_ids=[TEAM_ID],
    )

    async def fake_get(key: str):
        if key == f"onthisday:{TEAM_ID}:{month_day}":
            return [cached_game.model_dump(mode="json")]
        return None

    monkeypatch.setattr(cache, "cache_get_json", fake_get)
    fetches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, json={"events": []})

    _route_history(monkeypatch, handler)
    response = await client.get("/api/history/on-this-day")
    assert response.status_code == 200
    body = response.json()
    assert [group["team_id"] for group in body["teams"]] == [TEAM_ID]
    assert [game["id"] for game in body["teams"][0]["games"]] == ["espn:otd-cached"]
    # The assembled-payload cache answered; no season was fetched.
    assert fetches == []
