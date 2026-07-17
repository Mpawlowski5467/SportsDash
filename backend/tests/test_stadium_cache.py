"""Stadium-cache scheduler job tests against a throwaway in-memory database.

Focus: ``refresh_game_venue_coords`` must be a genuine no-op on installs
without Redis — the venue-coords cache it warms (:mod:`app.services.venue_coords`)
is Redis-backed, so without a configured ``redis_url`` every geocode result
would be discarded while still spending Nominatim's ≤1 req/s budget.

The engine is built directly here (never via ``app.db``); ``session_scope``
is monkeypatched into the job module so reads hit this throwaway database.
All fixture data is fictional.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.config import get_settings
from app.models.orm import GameORM, LeagueORM, TeamORM
from app.scheduler import stadium_cache as scheduler_stadium_cache
from app.services import geocode, venue_coords
from app.timeutil import utcnow
from tests.db_engine import create_test_schema, make_test_engine

LEAGUE_ID = "cinder-league"
TEAM_ID = "cinder-foxes"
# A venue no in-memory source resolves: not in the World Cup host table and
# with no located team/stadium row to index it under.
UNRESOLVED_VENUE = "Nowhere Fields, Undiscovered City"


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def patched_scope(db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the job module's ``session_scope`` at the throwaway database."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    # The job binds session_scope at import time, so patch it into its module.
    monkeypatch.setattr(scheduler_stadium_cache, "session_scope", scope)


async def _seed_pending_game(db: async_sessionmaker[AsyncSession]) -> None:
    """A followed league with an upcoming game at an unresolvable venue."""
    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="soccer",
                name="Cinder League",
                provider="mock",
                provider_key="mock-cinder",
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamORM(
                id=TEAM_ID,
                league_id=LEAGUE_ID,
                name="Cinder Foxes",
                abbreviation="CIN",
                provider_key="cinder-foxes",
                rss_feeds=[],
            )
        )
        await session.flush()  # team before game: postgres enforces FKs
        session.add(
            GameORM(
                id="mock:cinder-away",
                league_id=LEAGUE_ID,
                home_team_id=None,
                away_team_id=TEAM_ID,
                home_name="Nowhere Wanderers",
                away_name="Cinder Foxes",
                start_time=utcnow() + timedelta(days=2),
                venue=UNRESOLVED_VENUE,
                phase="scheduled",
            )
        )
        await session.commit()


async def test_game_venue_coords_does_no_geocoding_without_redis(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``redis_url`` unset the job must not geocode the pending venue.

    Its cache writes would no-op, so even a perfectly resolvable venue would
    be re-geocoded on every pass — the only correct behavior is no work.
    """
    monkeypatch.setattr(get_settings(), "redis_url", None)
    geocoded: list[str] = []

    async def fake_geocode(venue: str) -> tuple[float, float]:
        geocoded.append(venue)
        return (1.0, 2.0)

    monkeypatch.setattr(geocode, "geocode_venue", fake_geocode)
    await _seed_pending_game(db)

    await scheduler_stadium_cache.refresh_game_venue_coords()

    assert geocoded == []


async def test_game_venue_coords_geocodes_pending_venue_when_redis_configured(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control for the gate above: the same seed DOES geocode once
    Redis is configured, so the no-Redis test proves the gate, not a seed
    that never had pending work.  Cache reads/writes are stubbed so no real
    Redis client is built.
    """
    monkeypatch.setattr(get_settings(), "redis_url", "redis://localhost:6379/0")
    geocoded: list[str] = []
    stored: list[tuple[str, tuple[float, float] | None]] = []

    async def fake_geocode(venue: str) -> tuple[float, float]:
        geocoded.append(venue)
        return (12.0, 34.0)

    async def fake_has_entry(venue: str | None) -> bool:
        return False

    async def fake_set_coords(venue: str | None, coords: tuple[float, float] | None) -> None:
        stored.append((str(venue), coords))

    monkeypatch.setattr(geocode, "geocode_venue", fake_geocode)
    monkeypatch.setattr(venue_coords, "has_entry", fake_has_entry)
    monkeypatch.setattr(venue_coords, "set_coords", fake_set_coords)
    await _seed_pending_game(db)

    await scheduler_stadium_cache.refresh_game_venue_coords()

    assert geocoded == [UNRESOLVED_VENUE]
    assert stored == [(UNRESOLVED_VENUE, (12.0, 34.0))]
