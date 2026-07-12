"""Setup & onboarding API tests against a fresh in-memory database.

Same pattern as ``test_api.py``: a local FastAPI app with the real
router, an in-memory aiosqlite engine behind the ``get_session``
dependency, and fictional data only.  External side effects are
monkeypatched out at the module the routes call through:
``espn_catalog.get_league_teams`` (no network) and
``jobs.kick_daily_refresh`` (spy instead of a background refresh).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import domain
from app.models.orm import (
    Base,
    GameORM,
    LeagueORM,
    NewsORM,
    NotificationSentORM,
    PlayerORM,
    StandingsORM,
    TeamORM,
)
from app.providers import espn_catalog, tsdb_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam, EspnCatalogError
from app.routes import router as api_router
from app.scheduler import jobs
from app.services import repository
from app.timeutil import utcnow

# Fictional stand-ins for the live ESPN catalog fetch, keyed by catalog
# league id (the league ids themselves are real app functionality).
CATALOG_TEAMS: dict[str, list[CatalogTeam]] = {
    "nba": [
        CatalogTeam(
            provider_key="101",
            name="Harborlight Pelicans",
            abbreviation="HLP",
            logo_url="https://img.example.test/hlp.png",
            color="#38bdf8",
        ),
        CatalogTeam(
            provider_key="102",
            name="Stonegate Drifters",
            abbreviation="SGD",
            logo_url=None,
            color="#f59e0b",
        ),
        CatalogTeam(
            provider_key="103",
            name="Larkspur Brigade",
            abbreviation="LKB",
            logo_url=None,
            color=None,
        ),
    ],
    "epl": [
        CatalogTeam(
            provider_key="201",
            name="Wrenfield Athletic",
            abbreviation="WRA",
            logo_url="https://img.example.test/wra.png",
            color="#a78bfa",
        ),
        CatalogTeam(
            provider_key="202",
            name="Saltmarsh Rovers",
            abbreviation="SMR",
            logo_url=None,
            color="#34d399",
        ),
    ],
    # Fictional "nations" for the national-team competition picker. ESPN team
    # ids are global across competitions, so the same key is reused per the
    # sibling-competition attachment logic.
    "euros": [
        CatalogTeam(
            provider_key="478",
            name="Verdania",
            abbreviation="VRD",
            logo_url="https://img.example.test/verdania.png",
            color="#22d3ee",
        ),
        CatalogTeam(
            provider_key="479",
            name="Mistralia",
            abbreviation="MIS",
            logo_url=None,
            color="#f472b6",
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
def fake_catalog(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace the live ESPN teams fetch with the fictional fixtures."""
    calls = {"count": 0}

    async def fake_get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
        calls["count"] += 1
        return list(CATALOG_TEAMS.get(league.id, []))

    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)
    return calls


@pytest.fixture
def kick_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No-op spy in place of the background daily-refresh kick."""
    calls: list[str] = []
    monkeypatch.setattr(jobs, "kick_daily_refresh", lambda: calls.append("kicked"))
    return calls


async def _count(session: AsyncSession, table: type) -> int:
    return (await session.execute(select(func.count()).select_from(table))).scalar_one()


async def _seed_existing_followed_set(db: async_sessionmaker[AsyncSession]) -> None:
    """A pre-existing followed set plus its cached sports data — every row of
    which must vanish when the user follows a fresh set via /setup/follow."""
    now = utcnow()
    async with db() as session:
        await repository.replace_followed(
            session,
            [
                domain.League(
                    id="pinnacle-basketball",
                    sport=domain.Sport.BASKETBALL,
                    name="Pinnacle Basketball League",
                    provider="espn",
                    provider_key="basketball/pinnacle",
                )
            ],
            [
                domain.Team(
                    id="ashport-comets",
                    league_id="pinnacle-basketball",
                    name="Ashport Comets",
                    abbreviation="ASH",
                    provider_key="ashport-comets",
                )
            ],
        )
        await repository.set_meta(session, "onboarded", "1")
        session.add(
            GameORM(
                id="mock:pinnacle-d100-g0",
                league_id="pinnacle-basketball",
                home_team_id="ashport-comets",
                away_team_id=None,
                home_name="Ashport Comets",
                away_name="Foxglove Sentinels",
                start_time=now + timedelta(hours=4),
                phase="scheduled",
            )
        )
        session.add(
            StandingsORM(
                league_id="pinnacle-basketball",
                season="2026",
                rows=[
                    {
                        "rank": 1,
                        "team_name": "Ashport Comets",
                        "team_id": "ashport-comets",
                        "wins": 9,
                        "losses": 3,
                    }
                ],
                fetched_at=now,
            )
        )
        session.add(
            PlayerORM(
                team_id="ashport-comets",
                id="mock:plr-1",
                name="Juno Hollyfield",
                position="G",
                jersey_number="11",
                status="active",
            )
        )
        session.add(
            NewsORM(
                id="abc123",
                team_id="ashport-comets",
                title="Comets clinch home stand",
                url="https://example.test/comets",
                source="Ashport Gazette",
                published_at=now,
                fetched_at=now,
            )
        )
        session.add(NotificationSentORM(dedupe_key="mock:pinnacle-d100-g0:start"))
        await session.commit()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_status_before_onboarding(client: AsyncClient) -> None:
    resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"onboarded": False, "followed_team_count": 0}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def test_setup_leagues_static_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/setup/leagues")
    assert resp.status_code == 200
    leagues = resp.json()["leagues"]

    assert [league["id"] for league in leagues] == [
        "nba", "wnba", "mlb", "nhl", "nfl", "epl", "laliga",
        "bundesliga", "seriea", "ligue1", "mls", "ucl",
        # Phase 3a: 17 added soccer codes (national, club, domestic).
        "worldcup", "womens-worldcup", "euros", "nations-league",
        "copa-america", "europa", "conference", "club-world-cup",
        "championship", "scottish-prem", "eredivisie", "liga-portugal",
        "super-lig", "liga-mx", "bundesliga-2", "laliga-2", "ligue-2",
        # Phase 9: more leagues — lower English, South America, more Europe,
        # NA cups (live-verified ESPN soccer codes; ukr.1 404'd, skipped).
        "league-one", "league-two", "brasileirao", "liga-argentina",
        "libertadores", "sudamericana", "belgian-pro", "greek-super",
        "austrian-bundesliga", "swiss-super", "danish-superliga",
        "eliteserien", "allsvenskan", "russian-premier",
        "us-open-cup", "concacaf-champions",
        # Phase 4: individual sports (tennis tours + UFC).
        "atp", "wta", "ufc",
        # Phase 5: golf (leaderboard events).
        "pga",
        # Phase 6: volleyball (second provider — TheSportsDB).
        "cev-euro-men", "evl-men", "evl-women",
    ]
    # Every league is ESPN-served except the TheSportsDB volleyball catalog.
    assert all(
        league["provider"] == "espn"
        for league in leagues
        if league["id"] not in {"cev-euro-men", "evl-men", "evl-women"}
    )
    assert all(
        set(league)
        == {
            "id", "name", "sport", "provider",
            "national", "supports_follow_all", "entity_noun", "logo_url",
        }
        for league in leagues
    )
    by_id = {league["id"]: league for league in leagues}
    assert by_id["nba"]["sport"] == "basketball"
    assert by_id["mlb"]["sport"] == "baseball"
    assert by_id["epl"]["name"] == "Premier League"
    assert by_id["epl"]["sport"] == "soccer"

    # National-team comps are flagged for the wizard's grouping + follow-all.
    assert by_id["worldcup"]["national"] is True
    assert by_id["worldcup"]["supports_follow_all"] is True
    assert by_id["euros"]["national"] is True
    # Club whole-competition follows are not national but still follow-all.
    assert by_id["ucl"]["national"] is False
    assert by_id["ucl"]["supports_follow_all"] is True
    assert by_id["europa"]["supports_follow_all"] is True
    # Domestic leagues are not national, but every league can now be followed
    # in full (whole-league follow), so supports_follow_all is True for them too.
    assert by_id["championship"]["national"] is False
    assert by_id["championship"]["supports_follow_all"] is True
    assert by_id["super-lig"]["sport"] == "soccer"

    # Phase 4: individual sports. A pickable entity is a player/fighter, not a
    # team — the route derives entity_noun from the sport.
    assert by_id["atp"]["sport"] == "tennis"
    assert by_id["atp"]["name"] == "ATP Tour"
    assert by_id["atp"]["entity_noun"] == "player"
    assert by_id["wta"]["sport"] == "tennis"
    assert by_id["wta"]["entity_noun"] == "player"
    assert by_id["ufc"]["sport"] == "mma"
    assert by_id["ufc"]["entity_noun"] == "fighter"
    # Individual sports aren't national, but whole-tour follow is offered too.
    assert by_id["ufc"]["national"] is False
    assert by_id["ufc"]["supports_follow_all"] is True

    # Phase 6: volleyball is the first non-ESPN catalog (TheSportsDB). A
    # pickable entity is a plain team; not national / whole-competition.
    assert by_id["cev-euro-men"]["sport"] == "volleyball"
    assert by_id["cev-euro-men"]["provider"] == "thesportsdb"
    assert by_id["cev-euro-men"]["entity_noun"] == "team"
    assert by_id["cev-euro-men"]["national"] is False
    assert by_id["cev-euro-men"]["supports_follow_all"] is True
    assert by_id["evl-women"]["sport"] == "volleyball"
    assert by_id["evl-women"]["provider"] == "thesportsdb"


async def test_setup_leagues_carry_league_logos(client: AsyncClient) -> None:
    """Phase 11: the picker shows a league logo — the major leagues all
    carry a live-verified ESPN ``logo_url`` (None only where ESPN has no
    real league logo, e.g. the tennis tours' generic sport icon)."""
    resp = await client.get("/api/setup/leagues")
    assert resp.status_code == 200
    by_id = {league["id"]: league for league in resp.json()["leagues"]}

    # Every major team-sport + top-5 European soccer league has a logo, each
    # an https ESPN CDN league-logo URL.
    for league_id in (
        "nba", "wnba", "mlb", "nhl", "nfl",
        "epl", "laliga", "bundesliga", "seriea", "ligue1", "mls", "ucl",
        "worldcup", "euros", "ufc", "pga",
    ):
        logo = by_id[league_id]["logo_url"]
        assert isinstance(logo, str) and logo.startswith("https://a.espncdn.com/"), (
            f"{league_id} should carry an https ESPN league logo, got {logo!r}"
        )

    # The team-sport leagues use the stable teamlogos slug path...
    assert by_id["nba"]["logo_url"].endswith("/leagues/500/nba.png")
    assert by_id["nfl"]["logo_url"].endswith("/leagues/500/nfl.png")
    # ...soccer competitions use the opaque numeric leaguelogos path.
    assert "/leaguelogos/soccer/500/" in by_id["epl"]["logo_url"]
    assert by_id["epl"]["logo_url"].endswith("/23.png")

    # None where ESPN has no real league logo: tennis tours (generic icon)
    # and the two leagues ESPN only serves a default-team-logo placeholder.
    assert by_id["atp"]["logo_url"] is None
    assert by_id["wta"]["logo_url"] is None
    assert by_id["danish-superliga"]["logo_url"] is None

    # Sanity: a clear majority of the catalog resolves to a real logo.
    with_logo = sum(1 for lg in by_id.values() if lg["logo_url"])
    assert with_logo >= len(by_id) - 8


async def test_setup_teams_unknown_league_404(client: AsyncClient) -> None:
    resp = await client.get("/api/setup/teams/no-such-league")
    assert resp.status_code == 404


async def test_setup_teams_lists_catalog(
    client: AsyncClient, fake_catalog: dict[str, int]
) -> None:
    resp = await client.get("/api/setup/teams/nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["league_id"] == "nba"
    assert [team["name"] for team in data["teams"]] == [
        "Harborlight Pelicans", "Stonegate Drifters", "Larkspur Brigade",
    ]
    pelicans = data["teams"][0]
    assert pelicans == {
        "provider_key": "101",
        "name": "Harborlight Pelicans",
        "abbreviation": "HLP",
        "logo_url": "https://img.example.test/hlp.png",
        "color": "#38bdf8",
    }
    assert fake_catalog["count"] == 1


async def test_setup_teams_upstream_failure_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(league: CatalogLeague) -> list[CatalogTeam]:
        raise EspnCatalogError("upstream down")

    monkeypatch.setattr(espn_catalog, "get_league_teams", boom)
    resp = await client.get("/api/setup/teams/nba")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Follow
# ---------------------------------------------------------------------------


async def test_follow_happy_path_replaces_existing_set(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    # Start from an existing followed set plus cached sports data for it.
    await _seed_existing_followed_set(db)

    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "nba", "team_provider_keys": ["101", "102"]},
                {"league_id": "epl", "team_provider_keys": ["201"]},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert [league["id"] for league in data["leagues"]] == ["nba", "epl"]
    assert data["leagues"][0]["sport"] == "basketball"
    assert data["leagues"][1] == {
        "id": "epl",
        "sport": "soccer",
        "name": "Premier League",
        "follow_all": False,
    }
    assert [team["id"] for team in data["teams"]] == [
        "nba-harborlight-pelicans",
        "nba-stonegate-drifters",
        "epl-wrenfield-athletic",
    ]
    pelicans = data["teams"][0]
    assert pelicans["league_id"] == "nba"
    assert pelicans["abbreviation"] == "HLP"
    assert pelicans["logo_url"] == "https://img.example.test/hlp.png"
    assert pelicans["color"] == "#38bdf8"

    # Old demo leagues/teams and every cached row are gone; new set written.
    async with db() as session:
        league_ids = {row.id for row in (await session.execute(select(LeagueORM))).scalars()}
        assert league_ids == {"nba", "epl"}
        team_rows = (await session.execute(select(TeamORM))).scalars().all()
        assert {row.id for row in team_rows} == {
            "nba-harborlight-pelicans",
            "nba-stonegate-drifters",
            "epl-wrenfield-athletic",
        }
        pelicans_row = next(r for r in team_rows if r.id == "nba-harborlight-pelicans")
        assert pelicans_row.provider_key == "101"
        assert pelicans_row.league_id == "nba"
        for table in (GameORM, StandingsORM, PlayerORM, NewsORM, NotificationSentORM):
            assert await _count(session, table) == 0
        assert await repository.get_meta(session, "onboarded") == "1"

    # One fetch per selected league; exactly one refresh kicked.
    assert fake_catalog["count"] == 2
    assert kick_spy == ["kicked"]

    # Status reflects the new followed set.
    resp = await client.get("/api/setup/status")
    assert resp.json() == {"onboarded": True, "followed_team_count": 3}


async def test_follow_all_competition_no_teams(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """A ``follow_all`` selection needs no teams and sets league.follow_all."""
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "worldcup", "team_provider_keys": [], "follow_all": True}
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert [league["id"] for league in data["leagues"]] == ["worldcup"]
    assert data["teams"] == []

    async with db() as session:
        league = await repository.get_league(session, "worldcup")
        assert league is not None
        assert league.follow_all is True
        assert await _count(session, TeamORM) == 0
        # The whole-competition league is surfaced to the scheduler.
        follow_all = await repository.list_follow_all_leagues(session)
        assert {row.id for row in follow_all} == {"worldcup"}
        assert await repository.get_meta(session, "onboarded") == "1"

    # No team-list fetch is needed for a whole-competition follow.
    assert fake_catalog["count"] == 0
    assert kick_spy == ["kicked"]


async def test_follow_national_team_attaches_sibling_competitions(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """Following a nation in a national comp also schedules its siblings."""
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "euros", "team_provider_keys": ["478"]}
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    # A national pick attaches the nation's FULL international slate: the
    # primary comp plus continental (Euro qualifying, Nations League) and the
    # global World Cup slate (WC + the six confederations' qualifying) and
    # friendlies — all upserted as leagues.  ESPN national-team ids are global,
    # so each sibling reuses the same provider_key (478).
    expected_slate = {
        "euros", "euroq", "nations-league",
        "worldcup", "worldq-uefa", "worldq-conmebol", "worldq-concacaf",
        "worldq-afc", "worldq-caf", "worldq-ofc", "friendly",
    }
    assert {league["id"] for league in data["leagues"]} == expected_slate
    assert [team["id"] for team in data["teams"]] == ["euros-verdania"]

    async with db() as session:
        league_ids = {
            row.id for row in (await session.execute(select(LeagueORM))).scalars()
        }
        assert league_ids == expected_slate

        # The followed nation gets a sibling-competition row (reusing its
        # global ESPN provider_key 478) for every sibling EXCEPT its own
        # primary league — that one is already its home league.
        comps = await repository.list_team_competitions(session, "euros-verdania")
        assert {(c.league_id, c.provider_key) for c in comps} == {
            (sibling_id, "478") for sibling_id in expected_slate if sibling_id != "euros"
        }
        assert await repository.get_meta(session, "onboarded") == "1"

    assert kick_spy == ["kicked"]


async def test_follow_neither_teams_nor_follow_all_400(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """A selection with neither teams nor follow_all is rejected (400)."""
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {
                    "league_id": "worldcup",
                    "team_provider_keys": [],
                    "follow_all": False,
                }
            ]
        },
    )
    assert resp.status_code == 400
    assert kick_spy == []
    async with db() as session:
        assert await _count(session, LeagueORM) == 0
        assert await repository.get_meta(session, "onboarded") is None


async def test_follow_mixes_teams_and_whole_competition(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """A single request may mix team-follows and whole-competition follows."""
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "nba", "team_provider_keys": ["101"]},
                {"league_id": "ucl", "team_provider_keys": [], "follow_all": True},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert {league["id"] for league in data["leagues"]} == {"nba", "ucl"}
    assert [team["id"] for team in data["teams"]] == ["nba-harborlight-pelicans"]

    async with db() as session:
        ucl = await repository.get_league(session, "ucl")
        assert ucl is not None and ucl.follow_all is True
        nba = await repository.get_league(session, "nba")
        assert nba is not None and nba.follow_all is False
        follow_all = await repository.list_follow_all_leagues(session)
        assert {row.id for row in follow_all} == {"ucl"}

    # Only the team-follow league needed a roster fetch; follow_all skips it.
    assert fake_catalog["count"] == 1
    assert kick_spy == ["kicked"]


async def test_follow_empty_selections_400(
    client: AsyncClient, fake_catalog: dict[str, int], kick_spy: list[str]
) -> None:
    resp = await client.post("/api/setup/follow", json={"selections": []})
    assert resp.status_code == 400

    # A selection with no team keys is rejected just the same.
    resp = await client.post(
        "/api/setup/follow",
        json={"selections": [{"league_id": "nba", "team_provider_keys": []}]},
    )
    assert resp.status_code == 400
    assert kick_spy == []


async def test_follow_unknown_league_400(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "starlight-cup", "team_provider_keys": ["101"]}
            ]
        },
    )
    assert resp.status_code == 400
    assert kick_spy == []
    # Nothing was written or wiped.
    async with db() as session:
        assert await _count(session, LeagueORM) == 0
        assert await repository.get_meta(session, "onboarded") is None


async def test_follow_unknown_provider_key_400(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    resp = await client.post(
        "/api/setup/follow",
        json={
            "selections": [
                {"league_id": "nba", "team_provider_keys": ["101", "999"]}
            ]
        },
    )
    assert resp.status_code == 400
    assert "999" in resp.json()["detail"]
    assert kick_spy == []
    async with db() as session:
        assert await _count(session, TeamORM) == 0
        assert await repository.get_meta(session, "onboarded") is None




# ---------------------------------------------------------------------------
# Individual-sport catalog fetch (tennis/UFC rankings → athletes-as-"teams")
# ---------------------------------------------------------------------------
#
# These exercise espn_catalog.get_league_teams' rankings branch directly with
# a stubbed HTTP layer (espn_catalog._fetch_json) and fictional athletes.


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> Iterator[None]:
    """The 1h teams cache is module-global; isolate each test from it."""
    espn_catalog._cache.clear()
    tsdb_catalog._cache.clear()
    yield
    espn_catalog._cache.clear()
    tsdb_catalog._cache.clear()


def _tennis_rankings_payload() -> dict[str, object]:
    """One ranking block (as tennis tours emit), fictional players."""
    return {
        "rankings": [
            {
                "name": "Coastline ATP",
                "ranks": [
                    {
                        "current": 1,
                        "points": 9000.0,
                        "athlete": {
                            "id": "9001",
                            "displayName": "Rowan Ashgrove",
                            "shortname": "R. Ashgrove",
                            "headshot": "https://img.example.test/ashgrove.png",
                            "flag": "https://img.example.test/flags/vrd.png",
                            "color": "2d6cdf",
                        },
                    },
                    {
                        "current": 2,
                        "points": 7200.0,
                        "athlete": {
                            "id": "9002",
                            "displayName": "Mira Stonecroft",
                            # No shortname / headshot: exercise the fallbacks.
                            "headshot": None,
                        },
                    },
                    # Malformed row (missing id) must be skipped, not crash.
                    {"current": 3, "athlete": {"displayName": "Nameless"}},
                ],
            }
        ]
    }


def _ufc_rankings_payload() -> dict[str, object]:
    """Many ranking blocks with a repeated fighter (UFC's P4P + divisions)."""
    block_p4p = {
        "name": "Pound for Pound",
        "ranks": [
            {
                "current": 1,
                "athlete": {
                    "id": "5001",
                    "displayName": "Cassius Dunmore",
                    "shortname": "C. Dunmore",
                    "headshot": "https://img.example.test/dunmore.png",
                },
            },
            {
                "current": 2,
                "athlete": {
                    "id": "5002",
                    "displayName": "Iris Vandalay",
                    "shortname": "I. Vandalay",
                    "headshot": "https://img.example.test/vandalay.png",
                },
            },
        ],
    }
    block_division = {
        "name": "Heavyweight",
        "ranks": [
            # Dunmore again — must dedupe to a single CatalogTeam.
            {
                "current": 1,
                "athlete": {
                    "id": "5001",
                    "displayName": "Cassius Dunmore",
                    "shortname": "C. Dunmore",
                    "headshot": "https://img.example.test/dunmore.png",
                },
            },
            {
                "current": 2,
                "athlete": {
                    "id": "5003",
                    "displayName": "Bram Holloway",
                    "shortname": "B. Holloway",
                    "headshot": "https://img.example.test/holloway.png",
                },
            },
        ],
    }
    return {"rankings": [block_p4p, block_division]}


async def test_get_league_teams_tennis_parses_rankings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        seen.append(url)
        assert params == {}  # rankings takes no limit param
        return _tennis_rankings_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("atp")
    assert league is not None
    teams = await espn_catalog.get_league_teams(league)

    # Hits the /rankings endpoint, not /teams.
    assert seen == ["https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings"]
    # Malformed row dropped; athletes mapped onto CatalogTeam.
    assert [t.name for t in teams] == ["Rowan Ashgrove", "Mira Stonecroft"]
    rowan = teams[0]
    assert rowan.provider_key == "9001"
    assert rowan.abbreviation == "R. Ashgr"  # shortname clipped to 8
    assert rowan.logo_url == "https://img.example.test/ashgrove.png"
    assert rowan.color == "#2d6cdf"
    # Fallbacks: no shortname → first 3 chars upper; no headshot → None.
    mira = teams[1]
    assert mira.abbreviation == "MIR"
    assert mira.logo_url is None
    assert mira.color is None


async def test_get_league_teams_ufc_dedupes_across_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        assert url.endswith("/mma/ufc/rankings")
        return _ufc_rankings_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("ufc")
    assert league is not None
    teams = await espn_catalog.get_league_teams(league)

    # A fighter appearing in several blocks (P4P + division) is listed once,
    # first occurrence wins, original order preserved.
    assert [t.provider_key for t in teams] == ["5001", "5002", "5003"]
    assert [t.name for t in teams] == [
        "Cassius Dunmore",
        "Iris Vandalay",
        "Bram Holloway",
    ]


async def test_get_league_teams_individual_caps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep rankings are capped so the picker stays usable."""
    ranks = [
        {"current": i, "athlete": {"id": str(10000 + i), "displayName": f"Player {i}"}}
        for i in range(300)
    ]

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        return {"rankings": [{"name": "WTA", "ranks": ranks}]}

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("wta")
    assert league is not None
    teams = await espn_catalog.get_league_teams(league)
    assert len(teams) == espn_catalog._INDIVIDUAL_ROSTER_CAP
    assert teams[0].provider_key == "10000"  # ranking order preserved


async def test_get_league_teams_individual_upstream_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(url: str, params: dict[str, str], league_id: str) -> object:
        raise EspnCatalogError("rankings down")

    monkeypatch.setattr(espn_catalog, "_fetch_json", boom)
    league = espn_catalog.get_catalog_league("atp")
    assert league is not None
    with pytest.raises(EspnCatalogError):
        await espn_catalog.get_league_teams(league)


async def test_get_league_teams_caches_individual_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        calls["count"] += 1
        return _tennis_rankings_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("atp")
    assert league is not None
    first = await espn_catalog.get_league_teams(league)
    second = await espn_catalog.get_league_teams(league)
    assert calls["count"] == 1  # second call served from the 1h cache
    assert [t.provider_key for t in first] == [t.provider_key for t in second]


# ---------------------------------------------------------------------------
# Multi-provider catalog dispatch (Phase 6: volleyball via TheSportsDB)
# ---------------------------------------------------------------------------
#
# get_league_teams must route a thesportsdb-provider league to tsdb_catalog
# WITHOUT touching ESPN's fetch path, and tsdb_catalog must parse the
# TheSportsDB lookup_all_teams shape defensively. Fictional teams only.


async def test_get_league_teams_routes_thesportsdb_to_tsdb_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A volleyball (thesportsdb) league delegates to tsdb_catalog, not ESPN."""
    fictional = [
        CatalogTeam(
            provider_key="7001",
            name="Saltmarsh Spikers",
            abbreviation="SMS",
            logo_url="https://img.example.test/spikers.png",
            color="#22d3ee",
        ),
        CatalogTeam(
            provider_key="7002",
            name="Verdania Volley",
            abbreviation="VDV",
        ),
    ]
    seen: list[CatalogLeague] = []

    async def fake_tsdb(league: CatalogLeague) -> list[CatalogTeam]:
        seen.append(league)
        return list(fictional)

    # If ESPN's fetch is reached, the dispatch is wrong — fail loudly.
    async def forbidden_fetch(*args: object, **kwargs: object) -> object:
        raise AssertionError("ESPN _fetch_json must not be hit for thesportsdb")

    monkeypatch.setattr(tsdb_catalog, "get_tsdb_league_teams", fake_tsdb)
    monkeypatch.setattr(espn_catalog, "_fetch_json", forbidden_fetch)

    league = espn_catalog.get_catalog_league("cev-euro-men")
    assert league is not None
    assert league.provider == "thesportsdb"

    teams = await espn_catalog.get_league_teams(league)

    # Routed to the tsdb path with the same league object; ESPN never touched.
    assert seen == [league]
    assert [t.name for t in teams] == ["Saltmarsh Spikers", "Verdania Volley"]


def _tsdb_teams_payload() -> dict[str, object]:
    """A lookup_all_teams.php shape with fictional volleyball clubs."""
    return {
        "teams": [
            {
                "idTeam": "7001",
                "strTeam": "Saltmarsh Spikers",
                "strTeamShort": "SMS",
                "strBadge": "https://img.example.test/spikers.png",
                "strColour1": "#22d3ee",
            },
            {
                # No short code / badge / colour: exercise the fallbacks.
                "idTeam": "7002",
                "strTeam": "Verdania Volley",
            },
            # Malformed (missing idTeam) — skipped, not crash.
            {"strTeam": "Nameless"},
        ]
    }


async def test_tsdb_catalog_parses_lookup_all_teams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tsdb_catalog maps idTeam/strTeam/strTeamShort/strBadge/strColour1."""
    seen: list[str] = []

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        seen.append(url)
        assert params == {"id": "5613"}  # the league's provider_key
        return _tsdb_teams_payload()

    monkeypatch.setattr(tsdb_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("cev-euro-men")
    assert league is not None

    teams = await tsdb_catalog.get_tsdb_league_teams(league)

    assert seen == ["lookup_all_teams.php"]
    # Malformed entry dropped; fields mapped onto CatalogTeam.
    assert [t.name for t in teams] == ["Saltmarsh Spikers", "Verdania Volley"]
    spikers = teams[0]
    assert spikers.provider_key == "7001"
    assert spikers.abbreviation == "SMS"
    assert spikers.logo_url == "https://img.example.test/spikers.png"
    assert spikers.color == "#22d3ee"
    # Fallbacks: no strTeamShort → first 3 chars upper; no badge/colour → None.
    verdania = teams[1]
    assert verdania.abbreviation == "VER"
    assert verdania.logo_url is None
    assert verdania.color is None


async def test_tsdb_catalog_upstream_failure_raises_catalog_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP/decode failure surfaces as EspnCatalogError (route → 502)."""
    async def boom(url: str, params: dict[str, str], league_id: str) -> object:
        raise EspnCatalogError("thesportsdb down")

    monkeypatch.setattr(tsdb_catalog, "_fetch_json", boom)
    league = espn_catalog.get_catalog_league("evl-women")
    assert league is not None
    with pytest.raises(EspnCatalogError):
        await tsdb_catalog.get_tsdb_league_teams(league)


async def test_tsdb_catalog_empty_payload_is_empty_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free tier may return {"teams": null}; yield an empty list, no crash."""
    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> object:
        return {"teams": None}

    monkeypatch.setattr(tsdb_catalog, "_fetch_json", fake_fetch)
    league = espn_catalog.get_catalog_league("evl-men")
    assert league is not None
    teams = await tsdb_catalog.get_tsdb_league_teams(league)
    assert teams == []
