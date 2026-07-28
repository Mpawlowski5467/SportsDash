"""A coarse administrative area must never become a map pin.

TheSportsDB's ``strLocation`` is free text, and for some clubs it is all
the record has: a county or a city ("Elmshire, Wessex"), with no stadium
name at all.  The enrichment used to hand that string back as the venue,
the location job geocoded it, and a REGION geocodes to its centroid — so
the team was cached and plotted at a point tens of km from its ground,
indistinguishable from a correct pin and just as confidently flown to.

These tests pin down the whole chain: the enrichment refuses to promote an
area to a venue, the location job then reaches the stored-fixture venue it
used to shadow, a team that resolves to nothing at all simply stays off the
map, and rows already cached at a centroid are cleared / re-resolved rather
than trusted forever.

The engine is built directly here (never via ``app.db``); ``session_scope``
is monkeypatched into the job modules so reads hit this throwaway database.
All fixture data is fictional.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.models import domain
from app.models.orm import GameORM, LeagueORM, StadiumORM, TeamORM
from app.providers import espn_catalog
from app.scheduler import refresh as scheduler_refresh
from app.scheduler import stadium_cache as scheduler_stadium_cache
from app.services import geocode, repository, stadiums
from app.timeutil import utcnow
from tests.db_engine import create_test_schema, make_test_engine
from tests.tsdb_mock import install_tsdb_handler

LEAGUE_ID = "cinder-league"
TEAM_ID = "cinder-foxes"
TEAM_NAME = "Cinder Foxes"
# All TheSportsDB knows about this club: a county, not a ground.
COARSE_AREA = "Elmshire, Wessex"
# ...while its own fixtures name the ground it actually plays at.
REAL_VENUE = "Cinder Arena"
# Where the coarse area geocodes to — the middle of the county, far from
# the ground.  A pin here is the defect.
AREA_CENTROID = (50.7968, -2.3447)
VENUE_COORDS = (50.7352, -1.8384)

LEAGUE = domain.League(
    id=LEAGUE_ID,
    sport=domain.Sport.SOCCER,
    name="Cinder League",
    provider="espn",
    provider_key="cinder",
)
TEAM = domain.Team(
    id=TEAM_ID,
    league_id=LEAGUE_ID,
    name=TEAM_NAME,
    abbreviation="CIN",
    provider_key="cinder-foxes",
)


@pytest.fixture(autouse=True)
def _clear_stadium_cache() -> None:
    """The enrichment memoizes per process; keep tests independent."""
    stadiums._cache.clear()
    stadiums._info_cache.clear()


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
    """Point both job modules' ``session_scope`` at the throwaway database."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    # The jobs bind session_scope at import time, so patch it into each module.
    monkeypatch.setattr(scheduler_refresh, "session_scope", scope)
    monkeypatch.setattr(scheduler_stadium_cache, "session_scope", scope)


class _VenuelessProvider:
    """A provider with no home venue for the team — ESPN, for a soccer club."""

    async def get_team_location(self, league: domain.League, team: domain.Team) -> None:
        return None


def _area_only_handler(request: httpx.Request) -> httpx.Response:
    """TheSportsDB knows the club, but names no stadium — only its county."""
    assert "searchteams.php" in request.url.path
    return httpx.Response(
        200,
        json={
            "teams": [
                {
                    "strTeam": TEAM_NAME,
                    "strSport": "Soccer",
                    # No strStadium and no idVenue: the county is all there is.
                    "strLocation": COARSE_AREA,
                    "intStadiumCapacity": "11000",
                }
            ]
        },
    )


@pytest.fixture
def geocoded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every geocode query; resolve the real venue, else the centroid."""
    queries: list[str] = []

    async def fake_geocode_venue(venue: str) -> tuple[float, float] | None:
        queries.append(venue)
        return VENUE_COORDS if venue == REAL_VENUE else AREA_CENTROID

    monkeypatch.setattr(geocode, "geocode_venue", fake_geocode_venue)
    return queries


async def _seed_team(
    db: async_sessionmaker[AsyncSession], *, home_venue: str | None = None
) -> None:
    """A followed club, optionally with a stored home fixture naming its ground."""
    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="soccer",
                name="Cinder League",
                provider="espn",
                provider_key="cinder",
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamORM(
                id=TEAM_ID,
                league_id=LEAGUE_ID,
                name=TEAM_NAME,
                abbreviation="CIN",
                provider_key="cinder-foxes",
                rss_feeds=[],
            )
        )
        if home_venue is not None:
            await session.flush()  # team before game
            session.add(
                GameORM(
                    id="espn:cinder-home",
                    league_id=LEAGUE_ID,
                    home_team_id=TEAM_ID,
                    away_team_id=None,
                    home_name=TEAM_NAME,
                    away_name="Nowhere Wanderers",
                    start_time=utcnow() + timedelta(days=3),
                    venue=home_venue,
                    phase="scheduled",
                )
            )
        await session.commit()


async def test_coarse_area_leaves_a_team_unplotted_rather_than_wrong(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    geocoded: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stadium anywhere → no pin, and the county is never geocoded.

    The provider has no venue, TheSportsDB names only a county, and the
    club has no stored fixture to borrow a ground from.  The honest answer
    is "not located yet" — the county's centroid would be a confident
    marker tens of km from the real stadium.
    """
    install_tsdb_handler(monkeypatch, _area_only_handler)
    await _seed_team(db)

    location = await scheduler_refresh._resolve_team_location(_VenuelessProvider(), LEAGUE, TEAM)

    assert location is None
    assert geocoded == []


async def test_stored_fixture_venue_wins_over_a_coarse_area(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    geocoded: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The club lands on the ground its own fixtures name, not on its county.

    Composing the county into the venue name used to SHADOW this fallback:
    the venue string was always truthy, so the stored-game venue — a real
    stadium name already in our database — was never consulted.
    """
    install_tsdb_handler(monkeypatch, _area_only_handler)
    await _seed_team(db, home_venue=REAL_VENUE)

    location = await scheduler_refresh._resolve_team_location(_VenuelessProvider(), LEAGUE, TEAM)

    assert geocoded == [REAL_VENUE]
    assert location is not None
    assert location.venue == REAL_VENUE
    assert (location.lat, location.lon) == pytest.approx(VENUE_COORDS)
    # The county survives as a fact — it is true, it is just not a venue.
    assert location.location == COARSE_AREA
    assert location.capacity == 11000


async def test_refresh_locations_clears_a_team_cached_at_an_area_centroid(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
) -> None:
    """A row whose "venue" is only its location text is cleared for re-resolution.

    The location job never revisits a team that already has coordinates, so
    a pin cached before the fix would stay wrong forever.  Only the two
    false facts go (the venue name and the coordinates); the true ones stay.
    """
    await _seed_team(db)
    async with db() as session:
        await repository.set_team_location(
            session,
            TEAM_ID,
            COARSE_AREA,  # the pre-fix composition: the bare location text
            AREA_CENTROID[0],
            AREA_CENTROID[1],
            capacity=11000,
            location=COARSE_AREA,
        )
        await session.commit()

    await scheduler_refresh._clear_area_only_team_locations()

    async with db() as session:
        row = await repository.get_team(session, TEAM_ID)
    assert row is not None
    assert row.home_venue is None
    assert row.venue_lat is None and row.venue_lon is None
    assert row.venue_location == COARSE_AREA
    assert row.venue_capacity == 11000


async def test_refresh_locations_keeps_a_genuine_venue(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
) -> None:
    """Control for the repair: a real venue name is left completely alone."""
    await _seed_team(db)
    async with db() as session:
        await repository.set_team_location(
            session,
            TEAM_ID,
            f"{REAL_VENUE}, {COARSE_AREA}",
            VENUE_COORDS[0],
            VENUE_COORDS[1],
            location=COARSE_AREA,
        )
        await session.commit()

    await scheduler_refresh._clear_area_only_team_locations()

    async with db() as session:
        row = await repository.get_team(session, TEAM_ID)
    assert row is not None
    assert row.home_venue == f"{REAL_VENUE}, {COARSE_AREA}"
    assert (row.venue_lat, row.venue_lon) == pytest.approx(VENUE_COORDS)


async def test_competition_sweep_re_resolves_a_stadium_cached_at_a_centroid(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    geocoded: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole-competition path drops a centroid pin instead of keeping it.

    A ``follow_all`` field reaches the map through the stadium cache, where
    a resolved coordinate is otherwise treated as permanent — so the same
    fingerprint (venue == location) has to force a re-resolve there too.
    """
    install_tsdb_handler(monkeypatch, _area_only_handler)
    monkeypatch.setattr(scheduler_stadium_cache, "_COMPETITION_RESOLVE_DELAY_SECONDS", 0.0)

    catalog_team = espn_catalog.CatalogTeam(
        provider_key="cinder-foxes",
        name=TEAM_NAME,
        abbreviation="CIN",
    )
    monkeypatch.setattr(espn_catalog, "get_catalog_league", lambda league_id: object())

    async def fake_get_league_teams(catalog_league: object) -> list[espn_catalog.CatalogTeam]:
        return [catalog_team]

    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)

    key = f"espn:{catalog_team.provider_key}"
    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="soccer",
                name="Cinder League",
                provider="espn",
                provider_key="cinder",
                follow_all=True,
            )
        )
        session.add(
            StadiumORM(
                key=key,
                team_name=TEAM_NAME,
                venue=COARSE_AREA,  # the pre-fix composition, again
                lat=AREA_CENTROID[0],
                lon=AREA_CENTROID[1],
                location=COARSE_AREA,
                resolved=True,
                fetched_at=utcnow(),
            )
        )
        await session.commit()

    await scheduler_stadium_cache.refresh_competition_stadiums()

    async with db() as session:
        row = await repository.get_stadium(session, key)
    assert row is not None
    # Re-resolved: TheSportsDB still names no stadium, so the pin goes away
    # rather than staying at the centre of the county.
    assert row.venue is None
    assert row.lat is None and row.lon is None
    assert geocoded == []
