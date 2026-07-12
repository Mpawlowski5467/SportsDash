"""HTTP-level tests for the previously untested routes: /events,
/scorers, /nation, /bracket, and /leaders.

Same harness style as test_api.py: an in-memory SQLite app with the
``get_session`` dependency overridden, requests through ASGITransport,
fictional names only.
"""

from __future__ import annotations

from datetime import timedelta
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models.orm import Base, EventORM, LeagueORM, StandingsORM, TeamORM
from app.routes import router as api_router
from app.timeutil import utcnow

LEAGUE_ID = "violet-circuit"
GOLF_LEAGUE_ID = "gilded-links"
EVENT_ID = "mock:gl-open"


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    # The seeded league's provider ("mock") is not in the registry: the
    # route must degrade to an empty board, not 500.
    response = await client.get(f"/api/scorers/{LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == LEAGUE_ID
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
