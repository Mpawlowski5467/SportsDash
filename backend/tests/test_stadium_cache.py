"""Stadium-cache scheduler job tests against a throwaway in-memory database.

Two jobs are covered.  ``refresh_game_venue_coords`` must be a genuine no-op
on installs without Redis — the venue-coords cache it warms
(:mod:`app.services.venue_coords`) is Redis-backed, so without a configured
``redis_url`` every geocode result would be discarded while still spending
Nominatim's ≤1 req/s budget.

``refresh_competition_stadiums`` must give every team a row of its own.  A
provider id is only an identity inside the space it was issued in — the NFL
and the NHL both have a team 1 — and the cache used to key on the bare id,
so the second league to be swept found the first one's row and was plotted
at its ground.  Rows cached that way are still in real databases: the sweep
has to keep the ones it can prove right without paying for the lookups
again, and without taking them away from an older build that may yet run
against the same database.

And it must not mistake a refusal for an answer.  TheSportsDB rate limits a
long sweep, and "could not be asked" used to be recorded exactly like "has
no stadium": a miss, which keeps a team off the map until a later sweep.
The section after that drives the real resolve against a scripted
TheSportsDB.

Last, the sweep must step aside when told to.  It is the longest stage of a
daily refresh — a pacing sleep per team, five times longer once throttled —
and the pass a save in the setup wizard asks for cannot start until the
stage returns.

The engine is built directly here (never via ``app.db``); ``session_scope``
is monkeypatched into the job module so reads hit this throwaway database.
All fixture data is fictional; the league ids are real because the sweep
only walks leagues the catalog knows.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.config import get_settings
from app.models import domain
from app.models.orm import GameORM, LeagueORM, StadiumORM, TeamORM
from app.providers import espn_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam
from app.scheduler import common as scheduler_common
from app.scheduler import refresh as scheduler_refresh
from app.scheduler import stadium_cache as scheduler_stadium_cache
from app.services import geocode, repository, stadiums, venue_coords
from app.timeutil import ensure_utc, utcnow
from tests.db_engine import create_test_schema, make_test_engine
from tests.tsdb_mock import install_tsdb_handler

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


# ---------------------------------------------------------------------------
# refresh_competition_stadiums: one row per team, per id space
# ---------------------------------------------------------------------------

# ESPN's team 1 in two leagues followed in whole.
FALCONS = CatalogTeam(provider_key="1", name="Ashgrove Falcons", abbreviation="ASH")
BRUINS = CatalogTeam(provider_key="1", name="Tidewater Bruins", abbreviation="TID")
# The key an older build cached both of them under.
LEGACY_ONE = "espn:1"

GROUNDS = {
    FALCONS.name: domain.TeamLocation(
        venue="Falcon Field", lat=33.7554, lon=-84.4008, capacity=71000, opened=2017
    ),
    BRUINS.name: domain.TeamLocation(
        venue="Tidewater Garden", lat=42.3662, lon=-71.0621, capacity=17850, opened=1995
    ),
}


@dataclass
class Pipeline:
    """The outside world as one sweep sees it."""

    fields: dict[str, list[CatalogTeam]] = field(default_factory=dict)  # by league id
    unplaceable: set[str] = field(default_factory=set)  # team names nobody can locate
    lookups: list[str] = field(default_factory=list)  # team names resolved, in order


@pytest.fixture
def pipeline(patched_scope: None, monkeypatch: pytest.MonkeyPatch) -> Pipeline:
    """Stand in for the catalog and the TheSportsDB + geocode lookup.

    Each lookup is the expensive, rate-limited part of the sweep, so which
    teams reach it is most of what these tests assert.  TheSportsDB always
    answers here — ``unplaceable`` is a team it has nothing on — so the
    sweep never has a rate limit to wait out; the ``tsdb`` fixture further
    down is where it refuses.
    """
    state = Pipeline()

    async def fake_get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
        return list(state.fields.get(league.id, []))

    async def fake_answered(league: domain.League, team: CatalogTeam) -> bool:
        return True

    async def fake_resolve(league: domain.League, team: CatalogTeam) -> domain.TeamLocation | None:
        state.lookups.append(team.name)
        if team.name in state.unplaceable:
            return None
        return GROUNDS.get(team.name) or domain.TeamLocation(
            venue=f"{team.name} Ground", lat=51.5, lon=-0.12
        )

    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)
    monkeypatch.setattr(scheduler_stadium_cache, "_tsdb_answered", fake_answered)
    monkeypatch.setattr(scheduler_stadium_cache, "_resolve_competition_stadium", fake_resolve)
    monkeypatch.setattr(scheduler_stadium_cache, "_COMPETITION_RESOLVE_DELAY_SECONDS", 0.0)
    return state


async def _follow_whole(db: async_sessionmaker[AsyncSession], *league_ids: str) -> None:
    """Follow each catalog league in whole, as the setup wizard stores it."""
    async with db() as session:
        for league_id in league_ids:
            catalog = espn_catalog.get_catalog_league(league_id)
            assert catalog is not None
            session.add(
                LeagueORM(
                    id=catalog.id,
                    sport=catalog.sport.value,
                    name=catalog.name,
                    provider=catalog.provider,
                    provider_key=catalog.provider_key,
                    follow_all=True,
                )
            )
        await session.commit()


def _legacy_row(
    key: str, team_name: str, *, located: bool = True, fetched_at: datetime | None = None
) -> StadiumORM:
    """A row as an older build cached it: located, or a definitive miss."""
    return StadiumORM(
        key=key,
        team_name=team_name,
        venue="Falcon Field" if located else None,
        lat=33.7554 if located else None,
        lon=-84.4008 if located else None,
        capacity=71000 if located else None,
        opened=2017 if located else None,
        image_url="https://images.example.com/falcon-field.jpg" if located else None,
        location="Ashgrove, Georgia" if located else None,
        surface="Turf" if located else None,
        resolved=True,
        fetched_at=fetched_at or datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )


async def _seed_stadium(db: async_sessionmaker[AsyncSession], row: StadiumORM) -> None:
    async with db() as session:
        session.add(row)
        await session.commit()


async def _stadiums(db: async_sessionmaker[AsyncSession]) -> dict[str, dict[str, Any]]:
    """Every cached stadium by key: all it stores, read back from the database."""
    async with db() as session:
        rows = (await session.execute(select(StadiumORM))).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        facts = {
            column.key: getattr(row, column.key)
            for column in StadiumORM.__table__.columns
            if not column.primary_key
        }
        facts["fetched_at"] = ensure_utc(facts["fetched_at"])  # sqlite hands it back naive
        out[row.key] = facts
    return out


async def test_sweep_resolves_same_id_in_two_leagues(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """Two leagues' team 1 are two teams: two lookups, two rows.

    Under the bare-id key the NHL side found the NFL side's row already
    located, was never looked up, and was plotted in the NFL stadium.
    """
    pipeline.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    await _follow_whole(db, "nfl", "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == [FALCONS.name, BRUINS.name]
    rows = await _stadiums(db)
    assert set(rows) == {"espn:nfl:1", "espn:nhl:1"}
    assert (rows["espn:nfl:1"]["team_name"], rows["espn:nfl:1"]["venue"]) == (
        FALCONS.name,
        "Falcon Field",
    )
    assert (rows["espn:nhl:1"]["team_name"], rows["espn:nhl:1"]["venue"]) == (
        BRUINS.name,
        "Tidewater Garden",
    )


async def test_sweep_adopts_legacy_row_and_resolves_only_unproven_team(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """An upgrade keeps what it can prove and looks up only the rest.

    The legacy row names the NFL side, so it is that side's stadium and is
    copied to its key with no lookup.  It is NOT the NHL side's, which
    therefore gets the lookup the shared row used to rob it of.  The legacy
    row itself stays: an older build run against this database reads it.
    """
    pipeline.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    await _follow_whole(db, "nfl", "nhl")
    await _seed_stadium(db, _legacy_row(LEGACY_ONE, FALCONS.name))
    legacy = (await _stadiums(db))[LEGACY_ONE]

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == [BRUINS.name]
    rows = await _stadiums(db)
    assert set(rows) == {LEGACY_ONE, "espn:nfl:1", "espn:nhl:1"}
    # Every fact came across — the age of the lookup included — and the
    # original is exactly as it was.
    assert rows["espn:nfl:1"] == legacy
    assert rows[LEGACY_ONE] == legacy
    assert (rows["espn:nhl:1"]["team_name"], rows["espn:nhl:1"]["venue"]) == (
        BRUINS.name,
        "Tidewater Garden",
    )


async def test_sweep_adoption_is_idempotent(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """The sweep runs at every start, at 05:00 and on map kicks: once is enough.

    A second pass finds each team's own row first, so the legacy row is not
    copied again and nobody is looked up again.
    """
    pipeline.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    await _follow_whole(db, "nfl", "nhl")
    await _seed_stadium(db, _legacy_row(LEGACY_ONE, FALCONS.name))

    await scheduler_stadium_cache.refresh_competition_stadiums()

    first = await _stadiums(db)
    assert set(first) == {LEGACY_ONE, "espn:nfl:1", "espn:nhl:1"}
    assert pipeline.lookups == [BRUINS.name]

    pipeline.lookups.clear()
    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == []
    assert await _stadiums(db) == first


async def test_sweep_leaves_unprovable_legacy_row(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """A legacy row under another name is not evidence, so it is not used.

    A renamed club and another league's team look the same from here; the
    cost of doubting the row is one lookup, the cost of believing it is a
    pin in the wrong city.  The row is left alone either way.
    """
    renamed = CatalogTeam(provider_key="9", name="New Name FC", abbreviation="NEW")
    pipeline.fields = {"epl": [renamed]}
    await _follow_whole(db, "epl")
    await _seed_stadium(db, _legacy_row("espn:9", "Old Name FC"))
    legacy = (await _stadiums(db))["espn:9"]

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == ["New Name FC"]
    rows = await _stadiums(db)
    assert set(rows) == {"espn:9", "espn:soccer:9"}
    assert rows["espn:9"] == legacy
    assert (rows["espn:soccer:9"]["team_name"], rows["espn:soccer:9"]["venue"]) == (
        "New Name FC",
        "New Name FC Ground",
    )


async def test_sweep_shares_one_row_across_soccer_leagues(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """A club keeps its ESPN id in every competition: one lookup, one row.

    The opposite failure to the NFL/NHL one — scoping soccer by league would
    resolve a Champions League club once per competition it is followed in.
    """
    club = CatalogTeam(provider_key="364", name="Harbour Reds", abbreviation="HAR")
    pipeline.fields = {"epl": [club], "ucl": [club]}
    await _follow_whole(db, "epl", "ucl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == ["Harbour Reds"]
    assert set(await _stadiums(db)) == {"espn:soccer:364"}


async def test_sweep_miss_does_not_block_other_league(
    db: async_sessionmaker[AsyncSession], pipeline: Pipeline
) -> None:
    """One league's miss is not the next league's answer.

    A fresh miss is held back from retry for a cool-down.  Sharing the row
    made the NHL side inherit the NFL side's miss inside that window, so it
    was skipped without ever being asked about.
    """
    pipeline.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    pipeline.unplaceable = {FALCONS.name}
    await _follow_whole(db, "nfl", "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == [FALCONS.name, BRUINS.name]
    rows = await _stadiums(db)
    assert set(rows) == {"espn:nfl:1", "espn:nhl:1"}
    assert rows["espn:nfl:1"]["lat"] is None and rows["espn:nfl:1"]["resolved"] is True
    assert rows["espn:nhl:1"]["venue"] == "Tidewater Garden"


@pytest.mark.parametrize(
    ("missed_ago", "retried"),
    [
        (timedelta(hours=1), True),  # long past the cool-down: due again
        (timedelta(minutes=1), False),  # still cooling down
    ],
)
async def test_sweep_keeps_an_adopted_miss_on_its_own_clock(
    db: async_sessionmaker[AsyncSession],
    pipeline: Pipeline,
    missed_ago: timedelta,
    retried: bool,
) -> None:
    """Adopting a miss neither restarts its cool-down nor skips it.

    The copy carries the legacy row's ``fetched_at``, so an old miss is
    retried on the pass that adopts it and a fresh one still waits — the
    same thing that would have happened had the key never changed.
    """
    pipeline.fields = {"nfl": [FALCONS]}
    await _follow_whole(db, "nfl")
    await _seed_stadium(
        db,
        _legacy_row(LEGACY_ONE, FALCONS.name, located=False, fetched_at=utcnow() - missed_ago),
    )
    legacy = (await _stadiums(db))[LEGACY_ONE]

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert pipeline.lookups == ([FALCONS.name] if retried else [])
    rows = await _stadiums(db)
    assert set(rows) == {LEGACY_ONE, "espn:nfl:1"}
    assert rows[LEGACY_ONE] == legacy
    if retried:
        assert rows["espn:nfl:1"]["venue"] == "Falcon Field"
    else:
        assert rows["espn:nfl:1"] == legacy


async def test_sweep_logs_what_it_adopted_and_what_it_refused(
    db: async_sessionmaker[AsyncSession],
    pipeline: Pipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log is the only place an upgrade's repair can be read back.

    Per league: how many rows were adopted and how many teams went through
    a fresh lookup.  Per refused legacy row: its key, the name it holds and
    the name it was asked to match — which is what a cross-league collision
    or a renamed team looks like.
    """
    pipeline.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    await _follow_whole(db, "nfl", "nhl")
    await _seed_stadium(db, _legacy_row(LEGACY_ONE, FALCONS.name))
    caplog.set_level(logging.INFO, logger=scheduler_stadium_cache.logger.name)
    caplog.set_level(logging.INFO, logger=repository.logger.name)

    async def sweep_log() -> tuple[list[str], list[str]]:
        """One sweep's per-league summaries and its refused-row lines."""
        caplog.clear()
        await scheduler_stadium_cache.refresh_competition_stadiums()
        messages = [record.getMessage() for record in caplog.records]
        return (
            [message for message in messages if "team stadium(s) located" in message],
            [message for message in messages if "ignored" in message],
        )

    summaries, refused = await sweep_log()

    assert len(summaries) == 2
    assert "nfl" in summaries[0]
    assert "1 adopted from legacy rows, 0 resolved fresh" in summaries[0]
    assert "nhl" in summaries[1]
    assert "0 adopted from legacy rows, 1 resolved fresh" in summaries[1]
    assert len(refused) == 1
    assert LEGACY_ONE in refused[0]
    assert repr(FALCONS.name) in refused[0]
    assert repr(BRUINS.name) in refused[0]

    # The upgrade is reported once.  On the next pass each team has a row of
    # its own, so finding it is not an adoption and the refused row — still
    # in the table — is never consulted again.
    summaries, refused = await sweep_log()

    assert len(summaries) == 2
    assert all("0 adopted from legacy rows, 0 resolved fresh" in line for line in summaries)
    assert refused == []


# ---------------------------------------------------------------------------
# refresh_competition_stadiums: a rate-limited TheSportsDB is not a miss
# ---------------------------------------------------------------------------

# The sweep's own three delays, by the names it reads them under.
PACE = scheduler_stadium_cache._COMPETITION_RESOLVE_DELAY_SECONDS
SLOW_PACE = scheduler_stadium_cache._COMPETITION_THROTTLED_DELAY_SECONDS
PAUSE = scheduler_stadium_cache._TSDB_RATE_LIMIT_PAUSE_SECONDS

HERONS = CatalogTeam(provider_key="2", name="Marlow Herons", abbreviation="MAR")
OTTERS = CatalogTeam(provider_key="3", name="Saltmarsh Otters", abbreviation="SAL")
WRENS = CatalogTeam(provider_key="4", name="Pinecrest Wrens", abbreviation="PIN")
LYNXES = CatalogTeam(provider_key="5", name="Coldwater Lynxes", abbreviation="COL")

# What the scripted TheSportsDB holds on every team it knows: a named ground
# with a venue record, so a lookup it answers needs no geocoding.
TSDB_COORDS = (42.36611, -71.06222)
GEOCODED = (10.0, 20.0)


@dataclass
class Tsdb:
    """TheSportsDB, the geocoder and the clock as one sweep sees them."""

    fields: dict[str, list[CatalogTeam]] = field(default_factory=dict)  # by league id
    refusals: dict[str, int] = field(default_factory=dict)  # team -> searches still to answer 429
    unknown: set[str] = field(default_factory=set)  # team names it has no record of
    searches: list[str] = field(default_factory=list)  # team names searched, in order
    geocoded: list[str] = field(default_factory=list)  # venue names sent to the geocoder
    sleeps: list[float] = field(default_factory=list)  # every delay the sweep asked for
    # The stadium keys in the table when each rate-limit pause began.
    rows_at_pause: list[set[str]] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)  # stadium keys upserted, in order


@pytest.fixture
def tsdb(
    db: async_sessionmaker[AsyncSession], patched_scope: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Tsdb]:
    """Run the sweep's REAL resolve against a scripted TheSportsDB.

    Only the edges are replaced: the catalog's team lists, TheSportsDB's
    HTTP transport, the geocoder, and ``asyncio.sleep`` — which records the
    delay it was asked for and returns at once, so the sweep's real pacing
    constants can be asserted without waiting them out.
    """
    state = Tsdb()

    async def fake_get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
        return list(state.fields.get(league.id, []))

    def handler(request: httpx.Request) -> httpx.Response:
        if "lookupvenue.php" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "venues": [
                        {
                            "strThumb": "https://images.example.com/ground.jpg",
                            "intFormedYear": "1995",
                            "strMap": "42°21′58″N 71°03′44″W",
                        }
                    ]
                },
            )
        assert "searchteams.php" in request.url.path
        name = request.url.params["t"]
        state.searches.append(name)
        if state.refusals.get(name, 0) > 0:
            state.refusals[name] -= 1
            return httpx.Response(429, text="<html>rate limited</html>")
        if name in state.unknown:
            return httpx.Response(200, json={"teams": None})
        return httpx.Response(
            200,
            json={
                "teams": [
                    {
                        "strTeam": name,
                        "strStadium": f"{name} Arena",
                        "strLocation": "Port Ember",
                        "intStadiumCapacity": "17850",
                        "idVenue": "5001",
                    }
                ]
            },
        )

    async def fake_geocode(venue: str) -> tuple[float, float]:
        state.geocoded.append(venue)
        return GEOCODED

    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        state.sleeps.append(delay)
        if delay == PAUSE:
            state.rows_at_pause.append(set(await _stadiums(db)))
        await real_sleep(0)

    real_upsert = repository.upsert_stadium

    async def counting_upsert(session: AsyncSession, key: str, *args: Any, **kwargs: Any) -> Any:
        state.writes.append(key)
        return await real_upsert(session, key, *args, **kwargs)

    # The lookup memoizes its answers for the life of the process.
    stadiums._cache.clear()
    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)
    install_tsdb_handler(monkeypatch, handler)
    monkeypatch.setattr(geocode, "geocode_venue", fake_geocode)
    monkeypatch.setattr(scheduler_stadium_cache.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(repository, "upsert_stadium", counting_upsert)
    yield state
    stadiums._cache.clear()


async def test_sweep_waits_out_a_rate_limit_and_resolves_the_team(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused lookup is asked once more, after a pause, and nothing is
    written in between.

    Measured on a real database: 29 teams were each answered HTTP 429 and
    recorded as misses.  A miss row cools down for 15 minutes and the map
    only kicks a sweep for a team with NO row, so they stayed off the map
    until a later startup or the 05:00 run.
    """
    tsdb.fields = {"nhl": [BRUINS]}
    tsdb.refusals = {BRUINS.name: 1}
    await _follow_whole(db, "nhl")
    caplog.set_level(logging.INFO, logger=scheduler_stadium_cache.logger.name)

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, BRUINS.name]
    assert tsdb.sleeps == [PAUSE, SLOW_PACE]
    assert tsdb.rows_at_pause == [set()]  # no miss was recorded before the retry
    assert tsdb.writes == ["espn:nhl:1"]
    # Located from the provider's own record, with its facts — not from a
    # geocoded fixture venue.
    assert tsdb.geocoded == []
    row = (await _stadiums(db))["espn:nhl:1"]
    assert row["venue"] == "Tidewater Bruins Arena, Port Ember"
    assert (row["lat"], row["lon"]) == pytest.approx(TSDB_COORDS)
    assert (row["capacity"], row["opened"]) == (17850, 1995)
    assert row["image_url"] == "https://images.example.com/ground.jpg"
    said = [r.getMessage() for r in caplog.records if "rate limiting" in r.getMessage()]
    assert len(said) == 1 and BRUINS.name in said[0]


async def test_sweep_records_what_the_second_attempt_gets_when_it_is_refused_too(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """Refused again after the pause, the team is resolved the way it always
    was: from the venue its own stored home fixtures name, geocoded.  One
    write, and it is the row — never a miss first and a location after."""
    tsdb.fields = {"nhl": [BRUINS]}
    tsdb.refusals = {BRUINS.name: 2}
    await _follow_whole(db, "nhl")
    async with db() as session:
        session.add(
            GameORM(
                id="espn:tidewater-home",
                league_id="nhl",
                home_name=BRUINS.name,
                away_name=HERONS.name,
                start_time=utcnow() + timedelta(days=3),
                venue="Old Tidewater Rink",
                phase="scheduled",
            )
        )
        await session.commit()

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, BRUINS.name]
    assert tsdb.sleeps == [PAUSE, SLOW_PACE]
    assert tsdb.geocoded == ["Old Tidewater Rink"]
    assert tsdb.writes == ["espn:nhl:1"]
    row = (await _stadiums(db))["espn:nhl:1"]
    assert row["venue"] == "Old Tidewater Rink"
    assert (row["lat"], row["lon"]) == GEOCODED
    assert row["capacity"] is None  # the fallback knows a place, not the ground's facts


async def test_sweep_never_waits_on_a_team_twice_and_lets_its_miss_cool_down(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """One pause per team, however long TheSportsDB keeps refusing.

    With no fixture venue to fall back on the second refusal is recorded as
    a miss, and that row is the point: it is what stops the map kicking a
    sweep on every poll, and its cool-down keeps the next sweep from asking
    again at once.
    """
    tsdb.fields = {"nhl": [BRUINS]}
    tsdb.refusals = {BRUINS.name: 99}
    await _follow_whole(db, "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, BRUINS.name]
    assert tsdb.sleeps == [PAUSE, SLOW_PACE]
    assert tsdb.writes == ["espn:nhl:1"]
    row = (await _stadiums(db))["espn:nhl:1"]
    assert row["lat"] is None and row["resolved"] is True

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, BRUINS.name]  # cooling down: not asked again
    assert tsdb.sleeps == [PAUSE, SLOW_PACE]
    assert tsdb.writes == ["espn:nhl:1"]


async def test_sweep_does_not_wait_on_a_definitive_miss(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """A team TheSportsDB has no record of got its answer.  It is recorded at
    once, at the normal pace: only a lookup that could not be made is worth
    waiting for."""
    tsdb.fields = {"nhl": [BRUINS, HERONS]}
    tsdb.unknown = {BRUINS.name}
    await _follow_whole(db, "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, HERONS.name]
    assert tsdb.sleeps == [PACE, PACE]
    assert tsdb.rows_at_pause == []
    rows = await _stadiums(db)
    assert rows["espn:nhl:1"]["lat"] is None and rows["espn:nhl:1"]["resolved"] is True
    assert rows["espn:nhl:2"]["venue"] == "Marlow Herons Arena, Port Ember"


async def test_sweep_slows_down_for_the_rest_of_the_pass_after_a_refusal(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """The refusal is the sweep's own doing — too many lookups too fast — so
    from the first one on it resolves at the slow pace, in every league
    still to come.  Teams before it went at the normal pace."""
    tsdb.fields = {"nfl": [FALCONS, HERONS], "nhl": [BRUINS, OTTERS]}
    tsdb.refusals = {HERONS.name: 1}
    await _follow_whole(db, "nfl", "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [
        FALCONS.name,
        HERONS.name,
        HERONS.name,
        BRUINS.name,
        OTTERS.name,
    ]
    assert tsdb.sleeps == [PACE, PAUSE, SLOW_PACE, SLOW_PACE, SLOW_PACE]
    rows = await _stadiums(db)
    assert set(rows) == {"espn:nfl:1", "espn:nfl:2", "espn:nhl:1", "espn:nhl:3"}
    assert all(row["lat"] is not None for row in rows.values())


async def test_sweep_stops_waiting_once_pauses_keep_changing_nothing(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """TheSportsDB has refused every lookup for a whole session before.  A
    pause per team would then hold the sweep — and the rest of the daily
    refresh pass queued behind it — for half a minute times the field.
    After a few pauses that bought nothing the pass records what it gets,
    as it used to, still at the slow pace."""
    limit = scheduler_stadium_cache._TSDB_MAX_FRUITLESS_PAUSES
    teams = [BRUINS, HERONS, OTTERS, WRENS, LYNXES]
    assert len(teams) > limit
    tsdb.fields = {"nhl": teams}
    tsdb.refusals = {team.name: 99 for team in teams}
    await _follow_whole(db, "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    waited_on, not_waited_on = teams[:limit], teams[limit:]
    assert tsdb.searches == [
        *(name for team in waited_on for name in (team.name, team.name)),
        *(team.name for team in not_waited_on),
    ]
    assert tsdb.sleeps == [PAUSE, SLOW_PACE] * limit + [SLOW_PACE] * len(not_waited_on)
    rows = await _stadiums(db)
    assert len(rows) == len(teams)
    assert all(row["lat"] is None and row["resolved"] is True for row in rows.values())


async def test_sweep_keeps_waiting_while_each_pause_gets_an_answer(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """Only a pause that changed nothing counts against the limit above: one
    that got the team its answer was worth taking, however many there are."""
    teams = [BRUINS, HERONS, OTTERS, WRENS, LYNXES]
    assert len(teams) > scheduler_stadium_cache._TSDB_MAX_FRUITLESS_PAUSES
    tsdb.fields = {"nhl": teams}
    tsdb.refusals = {team.name: 1 for team in teams}
    await _follow_whole(db, "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [name for team in teams for name in (team.name, team.name)]
    assert tsdb.sleeps == [PAUSE, SLOW_PACE] * len(teams)
    rows = await _stadiums(db)
    assert len(rows) == len(teams)
    assert all(row["lat"] is not None for row in rows.values())


# ---------------------------------------------------------------------------
# refresh_competition_stadiums: stepping aside for a newer refresh pass
# ---------------------------------------------------------------------------

LEFT_EARLY = "leaving the rest of the sweep to the next pass"


async def test_sweep_leaves_between_teams_once_told_to_stop(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb, caplog: pytest.LogCaptureFixture
) -> None:
    """Told to stop, the sweep finishes the team in hand and asks about no
    other.  What it wrote stays, the single-flight lock is let go, and the
    next sweep takes up from the first team without a row."""
    tsdb.fields = {"nhl": [BRUINS, HERONS, OTTERS]}
    await _follow_whole(db, "nhl")
    caplog.set_level(logging.INFO, logger=scheduler_stadium_cache.logger.name)

    await scheduler_stadium_cache.refresh_competition_stadiums(
        should_stop=lambda: len(tsdb.writes) >= 1
    )

    assert tsdb.searches == [BRUINS.name]
    assert tsdb.writes == ["espn:nhl:1"]
    assert tsdb.sleeps == [PACE]
    assert not scheduler_common._competition_stadiums_lock.locked()
    assert [LEFT_EARLY in r.getMessage() for r in caplog.records].count(True) == 1

    await scheduler_stadium_cache.refresh_competition_stadiums()

    assert tsdb.searches == [BRUINS.name, HERONS.name, OTTERS.name]
    assert tsdb.writes == ["espn:nhl:1", "espn:nhl:2", "espn:nhl:3"]
    assert all(row["lat"] is not None for row in (await _stadiums(db)).values())


async def test_sweep_told_to_stop_does_not_list_the_next_league(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop is asked before a league's teams are listed, not only before
    its first team: listing them is a catalog request the sweep would make
    for nothing."""
    tsdb.fields = {"nfl": [FALCONS], "nhl": [BRUINS]}
    await _follow_whole(db, "nfl", "nhl")
    listed: list[str] = []
    list_teams = espn_catalog.get_league_teams

    async def recording_list_teams(league: CatalogLeague) -> list[CatalogTeam]:
        listed.append(league.id)
        return await list_teams(league)

    monkeypatch.setattr(espn_catalog, "get_league_teams", recording_list_teams)

    await scheduler_stadium_cache.refresh_competition_stadiums(
        should_stop=lambda: len(tsdb.writes) >= 1
    )

    assert listed == ["nfl"]
    assert tsdb.searches == [FALCONS.name]
    assert set(await _stadiums(db)) == {"espn:nfl:1"}


async def test_sweep_told_to_stop_during_a_pause_writes_nothing_for_that_team(
    db: async_sessionmaker[AsyncSession], tsdb: Tsdb
) -> None:
    """The rate-limit pause is the longest wait in the sweep, so it is where
    a save most often lands.  The sweep leaves as soon as the pause is over:
    the team it was waiting on is not asked about again and gets no row — a
    miss written here would keep it off the map for its cool-down — so the
    next sweep asks about it afresh."""
    tsdb.fields = {"nhl": [BRUINS, HERONS]}
    tsdb.refusals = {BRUINS.name: 1}
    await _follow_whole(db, "nhl")

    await scheduler_stadium_cache.refresh_competition_stadiums(
        should_stop=lambda: PAUSE in tsdb.sleeps
    )

    assert tsdb.searches == [BRUINS.name]
    assert tsdb.sleeps == [PAUSE]
    assert tsdb.writes == []
    assert await _stadiums(db) == {}
    assert not scheduler_common._competition_stadiums_lock.locked()

    await scheduler_stadium_cache.refresh_competition_stadiums()

    rows = await _stadiums(db)
    assert set(rows) == {"espn:nhl:1", "espn:nhl:2"}
    assert all(row["lat"] is not None for row in rows.values())


@pytest.mark.parametrize("refused", ["the third team, once", "every team, once"])
async def test_follow_change_during_the_stadium_stage_restarts_the_pass_within_one_team(
    db: async_sessionmaker[AsyncSession],
    tsdb: Tsdb,
    monkeypatch: pytest.MonkeyPatch,
    refused: str,
) -> None:
    """A save that lands while a pass is sweeping stadiums gets its own pass
    at once, not when the sweep is done.

    The real ``daily_refresh`` runs the real sweep over a field of 100, and
    the clock is the sum of the delays the sweep asked for.  One refusal
    early on puts the rest of the field at the slow pace; a refusal per team
    adds a pause to each.  With no checkpoint inside the stage, a save made
    a minute in waited 460 s for its pass in the first case and 3435 s in
    the second.
    """
    teams = [
        CatalogTeam(provider_key=str(1000 + i), name=f"Quarry Vale {i:03d}", abbreviation="QVL")
        for i in range(100)
    ]
    tsdb.fields = {"nhl": teams}
    tsdb.refusals = (
        {teams[2].name: 1} if refused == "the third team, once" else {t.name: 1 for t in teams}
    )
    await _follow_whole(db, "nhl")

    pass_started_at: list[float] = []
    saved_at: list[float] = []
    asked_about_at_save: list[int] = []
    asked_about_at_restart: list[int] = []

    async def fake_refresh_schedules() -> None:
        pass_started_at.append(sum(tsdb.sleeps))
        if len(pass_started_at) == 2:
            asked_about_at_restart.append(len(set(tsdb.searches)))

    async def other_stage() -> None:
        return None

    monkeypatch.setattr(scheduler_refresh, "refresh_schedules", fake_refresh_schedules)
    for name in (
        "refresh_standings",
        "refresh_rosters",
        "refresh_news",
        "refresh_team_info",
        "refresh_locations",
        "refresh_game_venue_coords",
    ):
        monkeypatch.setattr(scheduler_refresh, name, other_stage)
    # The two prunes around a pass's stages read through the refresh
    # module's own scope: point it at the throwaway database too.
    monkeypatch.setattr(scheduler_refresh, "session_scope", scheduler_stadium_cache.session_scope)

    recording_sleep = scheduler_stadium_cache.asyncio.sleep

    async def sleep_then_save_once(delay: float) -> None:
        await recording_sleep(delay)
        if not saved_at and sum(tsdb.sleeps) >= 60:
            saved_at.append(sum(tsdb.sleeps))
            asked_about_at_save.append(len(set(tsdb.searches)))
            scheduler_refresh.note_follow_change()  # what a setup save's kick does first

    monkeypatch.setattr(scheduler_stadium_cache.asyncio, "sleep", sleep_then_save_once)

    await scheduler_refresh.daily_refresh()

    assert len(pass_started_at) == 2
    assert pass_started_at[1] - saved_at[0] <= PAUSE + SLOW_PACE
    assert asked_about_at_restart[0] - asked_about_at_save[0] <= 1
    # Nothing was lost by leaving: the second pass's sweep finished the field.
    rows = await _stadiums(db)
    assert len(rows) == len(teams)
    assert all(row["lat"] is not None for row in rows.values())
