"""HTTP-level tests for the previously untested routes: /events,
/scorers, /nation, /bracket, and /leaders.

Same harness style as test_api.py: an in-memory SQLite app with the
``get_session`` dependency overridden, requests through ASGITransport,
fictional names only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.db import get_session
from tests.db_engine import create_test_schema, make_test_engine
from app.models.domain import GameSummary, Goal
from app.models.orm import EventORM, GameORM, LeagueORM, StandingsORM, TeamORM
from app.providers import registry
from app.routes import router as api_router
from app.routes import scorers as scorers_route
from app.timeutil import utcnow

LEAGUE_ID = "violet-circuit"
GOLF_LEAGUE_ID = "gilded-links"
# /scorers only walks soccer leagues (goals are parsed for no other sport).
SOCCER_LEAGUE_ID = "azure-coast-cup"
EVENT_ID = "mock:gl-open"


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def seeded(db: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="basketball",
                name="Violet Circuit Basketball",
                provider="mock",
                provider_key="mock-basketball",
            )
        )
        session.add(
            LeagueORM(
                id=GOLF_LEAGUE_ID,
                sport="golf",
                name="Gilded Links Tour",
                provider="mock",
                provider_key="mock-golf",
            )
        )
        session.add(
            LeagueORM(
                id=SOCCER_LEAGUE_ID,
                sport="soccer",
                name="Azure Coast Cup",
                provider="mock",
                provider_key="mock-soccer",
            )
        )
        # Parents flushed before children — Postgres enforces the FKs.
        await session.flush()
        session.add(
            TeamORM(
                id="glimmer-foxes",
                league_id=LEAGUE_ID,
                name="Glimmer Foxes",
                abbreviation="GLF",
                provider_key="glimmer-foxes",
                rss_feeds=[],
            )
        )
        session.add(
            EventORM(
                id=EVENT_ID,
                league_id=GOLF_LEAGUE_ID,
                name="The Gilded Open",
                start_time=now + timedelta(days=2),
                phase="scheduled",
                round_label="",
                leaderboard=[],
            )
        )
        session.add(
            StandingsORM(
                league_id=LEAGUE_ID,
                season="2026",
                rows=[
                    {
                        "rank": 1,
                        "team_name": "Glimmer Foxes",
                        "abbreviation": "GLF",
                        "wins": 10,
                        "losses": 2,
                        "group": "Harbor Division",
                    }
                ],
            )
        )
        await session.commit()


@pytest.fixture
async def client(db: async_sessionmaker[AsyncSession], seeded: None) -> AsyncIterator[AsyncClient]:
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- /events ---------------------------------------------------------------


async def test_events_lists_events_in_default_window(client: AsyncClient) -> None:
    response = await client.get("/api/events")
    assert response.status_code == 200
    payload = response.json()
    assert [event["id"] for event in payload] == [EVENT_ID]
    assert payload[0]["name"] == "The Gilded Open"
    assert payload[0]["league_id"] == GOLF_LEAGUE_ID


async def test_events_respects_explicit_window(client: AsyncClient) -> None:
    response = await client.get("/api/events", params={"start": "2000-01-01", "end": "2000-01-02"})
    assert response.status_code == 200
    assert response.json() == []


async def test_event_detail_and_404(client: AsyncClient) -> None:
    ok = await client.get(f"/api/events/{EVENT_ID}")
    assert ok.status_code == 200
    assert ok.json()["id"] == EVENT_ID

    missing = await client.get("/api/events/mock:nope")
    assert missing.status_code == 404


# --- /scorers ----------------------------------------------------------------


async def test_scorers_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/scorers/unknown-league")
    assert response.status_code == 404


async def test_scorers_unregistered_provider_degrades_to_empty(
    client: AsyncClient,
) -> None:
    # The seeded soccer league's provider ("mock") is not in the registry:
    # the route must degrade to an empty board, not 500.
    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == SOCCER_LEAGUE_ID
    assert payload["rows"] == []


class _RecordingSummaryProvider:
    """Provider that records every summary the route asks it for."""

    provider_id = "mock"

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def get_game_summary(self, league: object, provider_game_key: str) -> GameSummary:
        self.requested.append(provider_game_key)
        return GameSummary(
            game_id=provider_game_key,
            goals=(Goal(player="Wren Halloway", team="Glimmer Foxes"),),
        )


def _seed_finals(
    session: AsyncSession, league_id: str, prefix: str, count: int, now: datetime
) -> None:
    """Add ``count`` FINAL games to ``league_id``, oldest first."""
    for index in range(count):
        session.add(
            GameORM(
                id=f"mock:{prefix}-{index:04d}",
                league_id=league_id,
                home_name="Glimmer Foxes",
                away_name="Bramblewick Falcons",
                start_time=now - timedelta(days=count - index),
                phase="final",
            )
        )


async def test_scorers_caps_the_summary_fan_out_to_the_recent_games(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk costs one upstream summary per FINAL game, so it must be bounded.

    Finished games are never pruned, so an uncapped fan-out grows every
    matchweek (and across seasons) until a burst rate-limits the provider.
    The cap keeps the most recent games and reports what it actually walked.
    """
    total = scorers_route._MAX_GAMES + 5
    now = utcnow()
    async with db() as session:
        _seed_finals(session, SOCCER_LEAGUE_ID, "ac", total, now)
        await session.commit()

    provider = _RecordingSummaryProvider()
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")

    assert response.status_code == 200
    payload = response.json()
    # Bounded — and the games kept are the most recent ones, not the openers.
    assert len(provider.requested) == scorers_route._MAX_GAMES
    assert set(provider.requested) == {f"ac-{index:04d}" for index in range(5, total)}
    # The count the client sees matches what was actually tallied.
    assert payload["games_counted"] == scorers_route._MAX_GAMES
    assert payload["rows"][0]["player"] == "Wren Halloway"
    assert payload["rows"][0]["goals"] == scorers_route._MAX_GAMES


async def test_scorers_skips_the_fan_out_entirely_for_non_soccer_leagues(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only soccer summaries carry goals, so no other sport may fan out.

    A basketball league's finished games would each cost one upstream
    summary call to tally a guaranteed-empty board; the route must answer
    from the league row alone.
    """
    now = utcnow()
    async with db() as session:
        _seed_finals(session, LEAGUE_ID, "vc", 6, now)
        await session.commit()

    provider = _RecordingSummaryProvider()
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{LEAGUE_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert provider.requested == []
    assert payload["games_counted"] == 0
    assert payload["rows"] == []


# --- /nation -----------------------------------------------------------------


async def test_nation_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/nation/unknown-league/Glimmer%20Foxes")
    assert response.status_code == 404


async def test_nation_reads_standing_from_stored_rows(client: AsyncClient) -> None:
    response = await client.get(f"/api/nation/{LEAGUE_ID}/glimmer%20foxes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Glimmer Foxes"  # display name from the row
    assert payload["standing"]["rank"] == 1
    assert payload["standing"]["wins"] == 10


# --- /bracket ------------------------------------------------------------------


async def test_bracket_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/bracket/unknown-league")
    assert response.status_code == 404


async def test_bracket_non_espn_provider_is_empty(client: AsyncClient) -> None:
    response = await client.get(f"/api/bracket/{LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == LEAGUE_ID
    assert payload["rounds"] == []


# --- /leaders -----------------------------------------------------------------


async def test_leaders_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/leaders/unknown-league")
    assert response.status_code == 404


async def test_leaders_empty_rosters_yield_empty_board(client: AsyncClient) -> None:
    response = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == LEAGUE_ID
    assert payload["rows"] == []
