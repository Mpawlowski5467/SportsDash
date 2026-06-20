"""Notification-preference API tests against a fresh in-memory database.

Same harness as ``test_setup_api.py``: a local FastAPI app wired to the
real router, an in-memory aiosqlite engine behind ``get_session``, and
fictional data only.  The follow route's ESPN catalog fetch and its
background refresh kick are monkeypatched out so the follow-all default
seeding can be exercised end to end without network or a scheduler.
"""
from __future__ import annotations

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
from app.models import domain
from app.models.orm import Base
from app.providers import espn_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam
from app.routes import router as api_router
from app.scheduler import jobs
from app.services import notify_prefs, repository

# The five notifiable event types, in canonical order — mirrors the spec.
EVENT_TYPES = [
    "starting_soon",
    "game_start",
    "period_start",
    "intermission",
    "final",
]

# Fictional league + teams seeded directly into the DB for the GET/PUT
# tests (no setup wizard needed — these scopes are configurable as soon
# as the followed set exists).
LEAGUE = domain.League(
    id="meridian-football",
    sport=domain.Sport.SOCCER,
    name="Meridian Football League",
    provider="mock",
    provider_key="mock-soccer",
)
TEAMS = [
    domain.Team(
        id="violet-hollow-fc",
        league_id="meridian-football",
        name="Violet Hollow FC",
        abbreviation="VHF",
        provider_key="violet-hollow-fc",
        color="#a78bfa",
    ),
    domain.Team(
        id="eastmoor-rovers",
        league_id="meridian-football",
        name="Eastmoor Rovers",
        abbreviation="EMR",
        provider_key="eastmoor-rovers",
        color="#34d399",
    ),
]

# Fictional stand-in for the live ESPN catalog fetch used by the follow
# route (national-team comps reuse a global provider_key).
CATALOG_TEAMS: dict[str, list[CatalogTeam]] = {
    "euros": [
        CatalogTeam(
            provider_key="478",
            name="Verdania",
            abbreviation="VRD",
            logo_url=None,
            color="#22d3ee",
        ),
    ],
}


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def app(db: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the live ESPN teams fetch with the fictional fixtures."""

    async def fake_get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
        return list(CATALOG_TEAMS.get(league.id, []))

    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)


@pytest.fixture
def kick_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No-op spy in place of the background daily-refresh kick."""
    calls: list[str] = []
    monkeypatch.setattr(jobs, "kick_daily_refresh", lambda: calls.append("kicked"))
    return calls


async def _seed_followed_set(db: async_sessionmaker[AsyncSession]) -> None:
    """Persist the fictional league + teams so their scopes are listable."""
    async with db() as session:
        await repository.upsert_league(session, LEAGUE)
        for team in TEAMS:
            await repository.upsert_team(session, team)
        await session.commit()


# ---------------------------------------------------------------------------
# GET — scope construction + all-on defaults
# ---------------------------------------------------------------------------


async def test_get_prefs_lists_global_team_and_league_scopes(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_followed_set(db)

    resp = await client.get("/api/notifications/prefs")
    assert resp.status_code == 200
    data = resp.json()

    # Canonical, ordered event-type set.
    assert data["event_types"] == EVENT_TYPES

    prefs = data["prefs"]
    by_scope = {pref["scope"]: pref for pref in prefs}

    # Global + one per followed team + one per followed league.
    assert set(by_scope) == {
        "global",
        "team:violet-hollow-fc",
        "team:eastmoor-rovers",
        "league:meridian-football",
    }

    # Global comes first; labels match the team / league names.
    assert prefs[0]["scope"] == "global"
    assert by_scope["global"]["label"] == "All notifications"
    assert by_scope["team:violet-hollow-fc"]["label"] == "Violet Hollow FC"
    assert by_scope["team:eastmoor-rovers"]["label"] == "Eastmoor Rovers"
    assert by_scope["league:meridian-football"]["label"] == "Meridian Football League"

    # No stored rows yet → every scope is un-muted with all five types on.
    for pref in prefs:
        assert pref["muted"] is False
        assert pref["events"] == {event_type: True for event_type in EVENT_TYPES}


async def test_get_prefs_empty_followed_set_has_only_global(
    client: AsyncClient,
) -> None:
    """With nothing followed, only the catch-all global scope is offered."""
    resp = await client.get("/api/notifications/prefs")
    assert resp.status_code == 200
    data = resp.json()

    assert data["event_types"] == EVENT_TYPES
    assert [pref["scope"] for pref in data["prefs"]] == ["global"]
    assert data["prefs"][0]["label"] == "All notifications"


# ---------------------------------------------------------------------------
# PUT — mute / per-event toggles persist and re-GET reflects them
# ---------------------------------------------------------------------------


async def test_put_mute_team_persists_and_is_reflected(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_followed_set(db)

    resp = await client.put(
        "/api/notifications/prefs",
        json={"scope": "team:violet-hollow-fc", "muted": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    by_scope = {pref["scope"]: pref for pref in data["prefs"]}

    # The PUT response is the full refreshed set, with the muted team applied.
    assert by_scope["team:violet-hollow-fc"]["muted"] is True
    # Muting does not zero the per-event display — those stay all-on; the
    # mute flag is what silences the scope at send time.
    assert by_scope["team:violet-hollow-fc"]["events"] == {
        event_type: True for event_type in EVENT_TYPES
    }
    # Other scopes are untouched.
    assert by_scope["team:eastmoor-rovers"]["muted"] is False
    assert by_scope["global"]["muted"] is False

    # The change survives to a fresh GET, and the stored row exists.
    resp = await client.get("/api/notifications/prefs")
    by_scope = {pref["scope"]: pref for pref in resp.json()["prefs"]}
    assert by_scope["team:violet-hollow-fc"]["muted"] is True

    async with db() as session:
        stored = await repository.get_notification_prefs(session)
        assert {row.scope for row in stored} == {"team:violet-hollow-fc"}
        assert stored[0].muted is True


async def test_put_disable_one_event_for_global(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_followed_set(db)

    resp = await client.put(
        "/api/notifications/prefs",
        json={"scope": "global", "events": {"period_start": False}},
    )
    assert resp.status_code == 200
    by_scope = {pref["scope"]: pref for pref in resp.json()["prefs"]}

    # Only period_start is off for global; the other four stay on.
    assert by_scope["global"]["events"] == {
        "starting_soon": True,
        "game_start": True,
        "period_start": False,
        "intermission": True,
        "final": True,
    }
    assert by_scope["global"]["muted"] is False
    # Team scopes are unaffected and still all-on.
    assert by_scope["team:violet-hollow-fc"]["events"] == {
        event_type: True for event_type in EVENT_TYPES
    }

    # Persisted across a fresh GET.
    resp = await client.get("/api/notifications/prefs")
    by_scope = {pref["scope"]: pref for pref in resp.json()["prefs"]}
    assert by_scope["global"]["events"]["period_start"] is False


async def test_put_merges_rather_than_replaces(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    """A second PUT toggling a different field keeps the first field."""
    await _seed_followed_set(db)

    await client.put(
        "/api/notifications/prefs",
        json={"scope": "global", "events": {"intermission": False}},
    )
    resp = await client.put(
        "/api/notifications/prefs",
        json={"scope": "global", "events": {"period_start": False}},
    )
    assert resp.status_code == 200
    global_pref = next(
        pref for pref in resp.json()["prefs"] if pref["scope"] == "global"
    )
    # Both explicit disables are remembered.
    assert global_pref["events"]["intermission"] is False
    assert global_pref["events"]["period_start"] is False
    assert global_pref["events"]["final"] is True


# ---------------------------------------------------------------------------
# Follow-all seeding — a whole-competition follow defaults to game_start+final
# ---------------------------------------------------------------------------


async def test_follow_all_seeds_headline_only_league_pref(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: None,
    kick_spy: list[str],
) -> None:
    """A ``follow_all`` league's pref defaults to only game_start + final."""
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "ucl", "team_provider_keys": [], "follow_all": True}
            ]
        },
    )
    assert resp.status_code == 200
    assert kick_spy == ["kicked"]

    # The league scope's stored events are exactly game_start + final on.
    async with db() as session:
        stored = await repository.get_notification_prefs(session)
        assert {row.scope for row in stored} == {"league:ucl"}
        assert stored[0].events == {
            "starting_soon": False,
            "game_start": True,
            "period_start": False,
            "intermission": False,
            "final": True,
        }

    # The GET surfaces that resolved state for the league scope; the global
    # scope (no row) stays all-on.
    resp = await client.get("/api/notifications/prefs")
    by_scope = {pref["scope"]: pref for pref in resp.json()["prefs"]}
    assert by_scope["league:ucl"]["events"] == {
        "starting_soon": False,
        "game_start": True,
        "period_start": False,
        "intermission": False,
        "final": True,
    }
    assert by_scope["league:ucl"]["muted"] is False
    assert by_scope["global"]["events"] == {
        event_type: True for event_type in EVENT_TYPES
    }


async def test_follow_team_seeds_no_pref_row(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: None,
    kick_spy: list[str],
) -> None:
    """A plain team follow writes no pref row — the default is all-on."""
    resp = await client.post(
        "/api/setup/follow",
        json={"selections": [{"league_id": "euros", "team_provider_keys": ["478"]}]},
    )
    assert resp.status_code == 200

    async with db() as session:
        assert await repository.get_notification_prefs(session) == []

    # The team scope is still offered for editing, defaulting all-on.
    resp = await client.get("/api/notifications/prefs")
    by_scope = {pref["scope"]: pref for pref in resp.json()["prefs"]}
    assert by_scope["team:euros-verdania"]["events"] == {
        event_type: True for event_type in EVENT_TYPES
    }
    assert by_scope["team:euros-verdania"]["muted"] is False


# ---------------------------------------------------------------------------
# Cross-check: the canonical event-type set matches notify_prefs
# ---------------------------------------------------------------------------


def test_event_type_order_matches_shared_constant() -> None:
    assert list(notify_prefs.EVENT_TYPES) == EVENT_TYPES
    assert notify_prefs.follow_all_default_events() == {
        "starting_soon": False,
        "game_start": True,
        "period_start": False,
        "intermission": False,
        "final": True,
    }
