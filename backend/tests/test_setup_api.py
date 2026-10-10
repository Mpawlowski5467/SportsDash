"""Setup & onboarding API tests against a fresh in-memory database.

Same pattern as ``test_api.py``: a local FastAPI app with the real
router, an in-memory aiosqlite engine behind the ``get_session``
dependency, and fictional data only.  External side effects are
monkeypatched out at the module the routes call through:
``espn_catalog.get_league_teams`` (no network) and
``jobs.kick_daily_refresh`` (spy instead of a background refresh).
"""

from __future__ import annotations

from datetime import timedelta
from typing import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.db import get_session
from tests.db_engine import create_test_schema, make_test_engine
from app.models import domain
from app.models.orm import (
    Base,
    GameORM,
    LeagueORM,
    NewsORM,
    NotificationPrefORM,
    NotificationSentORM,
    PlayerFollowORM,
    PlayerORM,
    StandingsArchiveORM,
    StandingsORM,
    TeamORM,
)
from app.providers import espn_catalog, tsdb_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam, EspnCatalogError
from app.routes import router as api_router
from app.scheduler import jobs
from app.services import repository
from app.services.notify_prefs import follow_all_default_events
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
    engine = make_test_engine()
    await create_test_schema(engine)
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


async def _dump(db: async_sessionmaker[AsyncSession]) -> dict[str, list[dict[str, object]]]:
    """Every row of every table — to assert that a save changed nothing at
    all, not merely the columns a test thought to look at."""
    tables: dict[str, list[dict[str, object]]] = {}
    async with db() as session:
        for table in Base.metadata.sorted_tables:
            rows = (await session.execute(select(table))).mappings().all()
            tables[table.name] = sorted((dict(row) for row in rows), key=repr)
    return tables


async def _follow(client: AsyncClient, *selections: dict[str, object]) -> Response:
    return await client.post("/api/setup/follow", json={"selections": list(selections)})


def _as_follow_request(selection: dict[str, list[dict]]) -> dict[str, object]:
    """``GET /setup/selection`` turned back into a save, as the wizard does it."""
    return {
        "selections": [
            {
                "league_id": entry["league_id"],
                "follow_all": entry["follow_all"],
                "team_provider_keys": [team["provider_key"] for team in entry["teams"]],
            }
            for entry in selection["selections"]
        ]
    }


async def _seed_existing_followed_set(db: async_sessionmaker[AsyncSession]) -> None:
    """A pre-existing followed set plus its cached sports data — every row of
    which must vanish when the user follows a disjoint set via /setup/follow,
    bar the dedupe-ledger row, which no save ever deletes."""
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
        # Written by every standings refresh, so it is present within minutes
        # of the first setup — and it is a leagues-FK child like the rest.
        session.add(
            StandingsArchiveORM(
                league_id="pinnacle-basketball",
                season="2026",
                season_label="2025-26",
                rows=[
                    {
                        "rank": 1,
                        "team_name": "Ashport Comets",
                        "team_id": "ashport-comets",
                        "wins": 21,
                        "losses": 9,
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
    assert resp.json() == {"onboarded": False, "followed_team_count": 0, "refreshing": False}


async def test_status_reports_running_refresh(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``refreshing`` is the scheduler's flag, read at the module the route
    calls through — the app polls it to know when a save's data has landed."""
    monkeypatch.setattr(jobs, "daily_refresh_running", lambda: True)
    resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"onboarded": False, "followed_team_count": 0, "refreshing": True}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def test_setup_leagues_static_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/setup/leagues")
    assert resp.status_code == 200
    leagues = resp.json()["leagues"]

    assert [league["id"] for league in leagues] == [
        "nba",
        "wnba",
        "mlb",
        "nhl",
        "nfl",
        "epl",
        "laliga",
        "bundesliga",
        "seriea",
        "ligue1",
        "mls",
        "ucl",
        # Phase 3a: 17 added soccer codes (national, club, domestic).
        "worldcup",
        "womens-worldcup",
        "euros",
        "nations-league",
        "copa-america",
        "europa",
        "conference",
        "club-world-cup",
        "championship",
        "scottish-prem",
        "eredivisie",
        "liga-portugal",
        "super-lig",
        "liga-mx",
        "bundesliga-2",
        "laliga-2",
        "ligue-2",
        # Phase 9: more leagues — lower English, South America, more Europe,
        # NA cups (live-verified ESPN soccer codes; ukr.1 404'd, skipped).
        "league-one",
        "league-two",
        "brasileirao",
        "liga-argentina",
        "libertadores",
        "sudamericana",
        "belgian-pro",
        "greek-super",
        "austrian-bundesliga",
        "swiss-super",
        "danish-superliga",
        "eliteserien",
        "allsvenskan",
        "russian-premier",
        "us-open-cup",
        "concacaf-champions",
        # Phase 4: individual sports (tennis tours + UFC).
        "atp",
        "wta",
        "ufc",
        # Phase 5: golf (leaderboard events).
        "pga",
        # Motorsport (leaderboard events, driver-as-team).
        "f1",
        "nascar-cup",
        "indycar",
        # Phase 6: volleyball (second provider — TheSportsDB).
        "cev-euro-men",
        "evl-men",
        "evl-women",
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
            "id",
            "name",
            "sport",
            "provider",
            "national",
            "supports_follow_all",
            "entity_noun",
            "logo_url",
            "country_code",
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
        "nba",
        "wnba",
        "mlb",
        "nhl",
        "nfl",
        "epl",
        "laliga",
        "bundesliga",
        "seriea",
        "ligue1",
        "mls",
        "ucl",
        "worldcup",
        "euros",
        "ufc",
        "pga",
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


async def test_setup_teams_lists_catalog(client: AsyncClient, fake_catalog: dict[str, int]) -> None:
    resp = await client.get("/api/setup/teams/nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["league_id"] == "nba"
    assert [team["name"] for team in data["teams"]] == [
        "Harborlight Pelicans",
        "Stonegate Drifters",
        "Larkspur Brigade",
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
        "provider": "espn",
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

    # Nothing of the old set is still followed, so its leagues/teams and
    # every cached row are gone; new set written.
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
        for table in (
            GameORM,
            StandingsORM,
            StandingsArchiveORM,
            PlayerORM,
            NewsORM,
        ):
            assert await _count(session, table) == 0
        # The ledger outlives every save: it is what keeps a game that
        # returns with the next refresh from being announced twice.
        assert await _count(session, NotificationSentORM) == 1
        assert await repository.get_meta(session, "onboarded") == "1"

    # One fetch per selected league; exactly one refresh kicked.
    assert fake_catalog["count"] == 2
    assert kick_spy == ["kicked"]

    # Status reflects the new followed set.
    resp = await client.get("/api/setup/status")
    assert resp.json() == {"onboarded": True, "followed_team_count": 3, "refreshing": False}


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
            "selections": [{"league_id": "worldcup", "team_provider_keys": [], "follow_all": True}]
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
        json={"selections": [{"league_id": "euros", "team_provider_keys": ["478"]}]},
    )
    assert resp.status_code == 200
    data = resp.json()

    # A national pick attaches the nation's FULL international slate: the
    # primary comp plus continental (Euro qualifying, Nations League) and the
    # global World Cup slate (WC + the six confederations' qualifying) and
    # friendlies — all upserted as leagues.  ESPN national-team ids are global,
    # so each sibling reuses the same provider_key (478).
    expected_slate = {
        "euros",
        "euroq",
        "nations-league",
        "worldcup",
        "worldq-uefa",
        "worldq-conmebol",
        "worldq-concacaf",
        "worldq-afc",
        "worldq-caf",
        "worldq-ofc",
        "friendly",
    }
    assert {league["id"] for league in data["leagues"]} == expected_slate
    assert [team["id"] for team in data["teams"]] == ["euros-verdania"]

    async with db() as session:
        league_ids = {row.id for row in (await session.execute(select(LeagueORM))).scalars()}
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
        json={"selections": [{"league_id": "starlight-cup", "team_provider_keys": ["101"]}]},
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
        json={"selections": [{"league_id": "nba", "team_provider_keys": ["101", "999"]}]},
    )
    assert resp.status_code == 400
    assert "999" in resp.json()["detail"]
    assert kick_spy == []
    async with db() as session:
        assert await _count(session, TeamORM) == 0
        assert await repository.get_meta(session, "onboarded") is None


# ---------------------------------------------------------------------------
# Editing the followed set — a later save reconciles, it does not start over
# ---------------------------------------------------------------------------
#
# The wizard is also the editor, so every change to the followed set comes
# through /setup/follow as the WHOLE new set.  What that set still follows
# must come out the other side untouched.


async def test_resave_same_selection_changes_nothing(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """Saving the set that is already followed leaves every row as it was.

    The save used to wipe each table and leave the refresh to rebuild it,
    which a refresh cannot fully do: it reaches back a week, so older
    results were gone for good, along with the venue enrichment, the
    rosters, the player follows and the user's alert toggles.
    """
    selections: list[dict[str, object]] = [
        {"league_id": "nba", "team_provider_keys": ["101", "102"]},
        {"league_id": "ucl", "team_provider_keys": [], "follow_all": True},
    ]
    assert (await _follow(client, *selections)).status_code == 200
    assert fake_catalog["count"] == 1

    now = utcnow()
    async with db() as session:
        # Enrichment and feeds exist nowhere but on the team's own row.
        await repository.set_team_location(
            session, "nba-harborlight-pelicans", "Harborlight Arena", 12.5, -45.25, capacity=18000
        )
        pelicans = await repository.get_team(session, "nba-harborlight-pelicans")
        assert pelicans is not None
        pelicans.rss_feeds = ["https://example.test/pelicans.rss"]
        session.add_all(
            [
                # Older than any refresh would fetch again.
                GameORM(
                    id="espn:9001",
                    league_id="nba",
                    home_team_id="nba-harborlight-pelicans",
                    away_team_id="nba-stonegate-drifters",
                    home_name="Harborlight Pelicans",
                    away_name="Stonegate Drifters",
                    start_time=now - timedelta(days=30),
                    phase="final",
                    home_score=101,
                    away_score=96,
                ),
                # Untagged: stored only because the whole competition is followed.
                GameORM(
                    id="espn:9002",
                    league_id="ucl",
                    home_name="Wrenfield Athletic",
                    away_name="Saltmarsh Rovers",
                    start_time=now + timedelta(days=2),
                    phase="scheduled",
                ),
                StandingsORM(
                    league_id="nba",
                    season="2026",
                    rows=[
                        {
                            "rank": 1,
                            "team_name": "Harborlight Pelicans",
                            "team_id": "nba-harborlight-pelicans",
                            "wins": 9,
                            "losses": 3,
                        }
                    ],
                    fetched_at=now,
                ),
                StandingsArchiveORM(
                    league_id="nba",
                    season="2025",
                    season_label="2024-25",
                    rows=[{"rank": 1, "team_name": "Larkspur Brigade", "wins": 50, "losses": 32}],
                    fetched_at=now,
                ),
                PlayerORM(
                    team_id="nba-harborlight-pelicans",
                    id="espn:77",
                    name="Juno Hollyfield",
                    position="G",
                    jersey_number="11",
                    status="active",
                ),
                PlayerFollowORM(
                    athlete_id="77",
                    name="Juno Hollyfield",
                    team_id="nba-harborlight-pelicans",
                    league_id="nba",
                    followed_at=now,
                ),
                NewsORM(
                    id="team-article",
                    team_id="nba-harborlight-pelicans",
                    title="Pelicans clinch home stand",
                    url="https://example.test/pelicans",
                    source="Harborlight Gazette",
                    published_at=now,
                    fetched_at=now,
                ),
                NewsORM(
                    id="league-article",
                    league_id="ucl",
                    title="Draw made for the last sixteen",
                    url="https://example.test/draw",
                    source="Continental Wire",
                    published_at=now,
                    fetched_at=now,
                ),
                NotificationSentORM(dedupe_key="espn:9001:final", sent_at=now),
            ]
        )
        await session.commit()
    # The user has since switched period alerts back on for the competition
    # (the follow-all seed has them off) and muted one of the teams.
    for update in (
        {"scope": "league:ucl", "events": {"period_start": True}},
        {"scope": "team:nba-stonegate-drifters", "muted": True},
    ):
        assert (await client.put("/api/notifications/prefs", json=update)).status_code == 200

    before = await _dump(db)
    assert {name: len(rows) for name, rows in before.items() if rows} == {
        "leagues": 2,
        "teams": 2,
        "games": 2,
        "standings": 1,
        "standings_archive": 1,
        "players": 1,
        "player_follows": 1,
        "news_items": 2,
        "notifications_sent": 1,
        "notification_prefs": 2,
        "app_meta": 1,
    }

    assert (await _follow(client, *selections)).status_code == 200

    after = await _dump(db)
    for name, rows in before.items():
        assert after[name] == rows, name
    # Nothing new was picked, so the provider was not asked for anything —
    # but the save still kicks its refresh.
    assert fake_catalog["count"] == 1
    assert kick_spy == ["kicked", "kicked"]


async def test_recent_final_not_renotified_after_resave(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """A final that was announced stays announced across an edit.

    The live tick re-sends any recent final with no ledger row.  A save that
    dropped the ledger therefore pushed every final of the last six hours a
    second time, as soon as the refresh it kicked stored the game again.
    """
    selection = {"league_id": "nba", "team_provider_keys": ["101"]}
    assert (await _follow(client, selection)).status_code == 200

    final = domain.Game(
        id="espn:9001",
        league_id="nba",
        home_name="Harborlight Pelicans",
        away_name="Stonegate Drifters",
        start_time=utcnow() - timedelta(hours=1),
        home_team_id="nba-harborlight-pelicans",
        state=domain.GameState(
            game_id="espn:9001",
            phase=domain.GamePhase.FINAL,
            home_score=101,
            away_score=96,
        ),
    )
    lookback = timedelta(hours=6)
    async with db() as session:
        await repository.upsert_games(session, [final])
        await session.commit()
        # Unannounced, it is exactly what the resend query is for...
        missing = await repository.finals_missing_notification(session, utcnow(), lookback)
        assert [game.id for game in missing] == ["espn:9001"]
        # ...and announcing it takes it out.
        await repository.mark_notified(session, "espn:9001:final")
        await session.commit()
        assert await repository.finals_missing_notification(session, utcnow(), lookback) == []

    assert (await _follow(client, selection)).status_code == 200

    # The refresh the save kicked fetches the same game again.
    async with db() as session:
        await repository.upsert_games(session, [final])
        await session.commit()
        stored = await repository.get_game(session, "espn:9001")
        assert stored is not None and stored.phase == "final"
        assert await repository.finals_missing_notification(session, utcnow(), lookback) == []


@pytest.mark.parametrize(
    ("kept_league", "tagged_afterwards"),
    [
        # The pick that holds the tags goes: the kept one takes them over.
        ("euros", "euros-verdania"),
        # The other pick goes: nothing about the games changes.
        ("worldcup", "worldcup-verdania"),
    ],
)
async def test_unfollowing_one_of_two_picks_of_a_nation_keeps_its_games(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
    kept_league: str,
    tagged_afterwards: str,
) -> None:
    """Whichever duplicate pick is removed, the kept one keeps every result.

    The picker lets one nation be followed under two national competitions.
    Each pick has the other's league in its slate, so both fetch the same
    fixtures, and every game carries the id of whichever is fetched last —
    ``worldcup-…`` sorts after ``euros-…``.  Removing that pick used to
    untag the games and then delete them as nobody's, though the nation
    was still followed; and a refresh only reaches back a week.
    """
    monkeypatch.setitem(CATALOG_TEAMS, "worldcup", [CATALOG_TEAMS["euros"][0]])
    resp = await _follow(
        client,
        {"league_id": "euros", "team_provider_keys": ["478"]},
        {"league_id": "worldcup", "team_provider_keys": ["478"]},
    )
    assert resp.status_code == 200
    assert [team["id"] for team in resp.json()["teams"]] == ["euros-verdania", "worldcup-verdania"]

    game_ids = {f"espn:{league_id}-final" for league_id in ("euros", "friendly", "worldcup")}
    async with db() as session:
        await repository.upsert_games(
            session,
            [
                domain.Game(
                    id=f"espn:{league_id}-final",
                    league_id=league_id,
                    home_name="Verdania",
                    away_name="Mistralia",
                    start_time=utcnow() - timedelta(days=40),
                    # As the refresh leaves it: the last pick fetched.
                    home_team_id="worldcup-verdania",
                    state=domain.GameState(
                        game_id=f"espn:{league_id}-final",
                        phase=domain.GamePhase.FINAL,
                        home_score=2,
                        away_score=1,
                    ),
                )
                for league_id in ("euros", "friendly", "worldcup")
            ],
        )
        await session.commit()

    resp = await _follow(client, {"league_id": kept_league, "team_provider_keys": ["478"]})
    assert resp.status_code == 200
    assert [team["id"] for team in resp.json()["teams"]] == [tagged_afterwards]

    async with db() as session:
        tags = dict(
            (await session.execute(select(GameORM.id, GameORM.home_team_id))).tuples().all()
        )
        assert tags == {game_id: tagged_afterwards for game_id in game_ids}
        results = await repository.results_for_team(session, tagged_afterwards)
        assert {game.id for game in results} == game_ids


async def test_follow_same_provider_key_in_two_leagues_is_two_teams(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored team is its league AND its provider key, never the key alone.

    ESPN's team ids are per sport, so two leagues routinely share one (the
    NBA's team 2 and the NFL's).  Matched on the key alone, the second pick
    would resolve to the first one's stored row and re-point it to the
    other league — taking its id, feeds and every row it owns along.
    """
    monkeypatch.setitem(
        CATALOG_TEAMS,
        "epl",
        [CatalogTeam(provider_key="101", name="Wrenfield Athletic", abbreviation="WRA")],
    )
    assert (
        await _follow(client, {"league_id": "nba", "team_provider_keys": ["101"]})
    ).status_code == 200
    async with db() as session:
        pelicans = await repository.get_team(session, "nba-harborlight-pelicans")
        assert pelicans is not None
        pelicans.rss_feeds = ["https://example.test/pelicans.rss"]
        pelicans.description = "Harborlight Pelicans are a fictional basketball club."
        await session.commit()

    resp = await _follow(
        client,
        {"league_id": "nba", "team_provider_keys": ["101"]},
        {"league_id": "epl", "team_provider_keys": ["101"]},
    )
    assert resp.status_code == 200
    assert [(team["id"], team["league_id"]) for team in resp.json()["teams"]] == [
        ("nba-harborlight-pelicans", "nba"),
        ("epl-wrenfield-athletic", "epl"),
    ]

    async with db() as session:
        rows = {row.id: row for row in await repository.list_teams(session)}
    assert set(rows) == {"nba-harborlight-pelicans", "epl-wrenfield-athletic"}
    kept = rows["nba-harborlight-pelicans"]
    assert (kept.league_id, kept.name) == ("nba", "Harborlight Pelicans")
    assert kept.rss_feeds == ["https://example.test/pelicans.rss"]
    assert kept.description == "Harborlight Pelicans are a fictional basketball club."
    assert rows["epl-wrenfield-athletic"].rss_feeds == []


async def test_follow_keeps_team_id_when_catalog_name_changes(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A followed team is the provider's key, not the slug of its name.

    The id is minted from the display name when a team is first followed.
    Minted again on a later save it would move with a provider rename, and
    the team would be unfollowed and re-added as a stranger — losing every
    row that hangs off the old id.
    """
    assert (
        await _follow(client, {"league_id": "nba", "team_provider_keys": ["101"]})
    ).status_code == 200
    async with db() as session:
        await repository.set_team_location(
            session, "nba-harborlight-pelicans", "Harborlight Arena", 12.5, -45.25
        )
        pelicans = await repository.get_team(session, "nba-harborlight-pelicans")
        assert pelicans is not None
        pelicans.rss_feeds = ["https://example.test/pelicans.rss"]
        await session.commit()

    # The provider rebrands the club; its key stays 101.
    monkeypatch.setitem(
        CATALOG_TEAMS,
        "nba",
        [
            CatalogTeam(
                provider_key="101",
                name="Harborlight Pelicans BC",
                abbreviation="HPB",
                logo_url="https://img.example.test/hpb.png",
                color="#0ea5e9",
            ),
            *CATALOG_TEAMS["nba"][1:],
        ],
    )
    # Adding 102 is what sends this save to the catalog at all.
    resp = await _follow(client, {"league_id": "nba", "team_provider_keys": ["101", "102"]})
    assert resp.status_code == 200
    assert fake_catalog["count"] == 2

    teams = resp.json()["teams"]
    assert [team["id"] for team in teams] == ["nba-harborlight-pelicans", "nba-stonegate-drifters"]
    # Same team, shown as the provider now shows it.
    assert teams[0]["name"] == "Harborlight Pelicans BC"
    assert teams[0]["abbreviation"] == "HPB"
    assert teams[0]["logo_url"] == "https://img.example.test/hpb.png"
    assert teams[0]["color"] == "#0ea5e9"

    async with db() as session:
        rows = {row.id: row for row in await repository.list_teams(session)}
        assert set(rows) == {"nba-harborlight-pelicans", "nba-stonegate-drifters"}
        kept = rows["nba-harborlight-pelicans"]
        assert kept.provider_key == "101"
        assert kept.name == "Harborlight Pelicans BC"
        # What only the stored row held is still on it.
        assert kept.rss_feeds == ["https://example.test/pelicans.rss"]
        assert (kept.home_venue, kept.venue_lat, kept.venue_lon) == (
            "Harborlight Arena",
            12.5,
            -45.25,
        )


async def test_follow_accepts_stored_key_missing_from_catalog(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A followed pick the catalog no longer lists does not block the save.

    Routine for individual sports: the golf catalog is the current
    tournament's field and the tennis/MMA ones are capped rankings, so a
    followed athlete drops out of it while the user is adding another.
    """
    assert (
        await _follow(client, {"league_id": "nba", "team_provider_keys": ["101", "102"]})
    ).status_code == 200

    monkeypatch.setitem(CATALOG_TEAMS, "nba", CATALOG_TEAMS["nba"][1:])  # 101 is gone
    # 103 is new, so the catalog IS fetched — and has no entry for 101.
    resp = await _follow(client, {"league_id": "nba", "team_provider_keys": ["101", "103"]})
    assert resp.status_code == 200
    assert fake_catalog["count"] == 2

    teams = resp.json()["teams"]
    assert [team["id"] for team in teams] == ["nba-harborlight-pelicans", "nba-larkspur-brigade"]
    # With no catalog entry to go by, the kept team is shown as it was saved.
    assert teams[0] == {
        "id": "nba-harborlight-pelicans",
        "league_id": "nba",
        "name": "Harborlight Pelicans",
        "abbreviation": "HLP",
        "logo_url": "https://img.example.test/hlp.png",
        "color": "#38bdf8",
    }
    async with db() as session:
        rows = {row.id: row.provider_key for row in await repository.list_teams(session)}
        assert rows == {"nba-harborlight-pelicans": "101", "nba-larkspur-brigade": "103"}

    # A key that is neither followed nor listed is still refused — and it is
    # the only one named, though the catalog lacks 101 just the same.
    resp = await _follow(client, {"league_id": "nba", "team_provider_keys": ["101", "999"]})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown team key(s) for league 'nba': 999"
    assert kick_spy == ["kicked", "kicked"]
    async with db() as session:
        assert {row.id for row in await repository.list_teams(session)} == {
            "nba-harborlight-pelicans",
            "nba-larkspur-brigade",
        }


async def test_follow_all_stored_keys_skips_catalog_fetch(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A save that picks nothing new never touches the provider.

    So unfollowing a team works while ESPN is down, where it used to fail
    on a catalog it had no use for.
    """
    assert (
        await _follow(
            client,
            {"league_id": "nba", "team_provider_keys": ["101", "102"]},
            {"league_id": "epl", "team_provider_keys": ["201"]},
        )
    ).status_code == 200
    assert fake_catalog["count"] == 2

    resp = await _follow(
        client,
        {"league_id": "nba", "team_provider_keys": ["102"]},
        {"league_id": "epl", "team_provider_keys": ["201"]},
    )
    assert resp.status_code == 200
    assert [team["id"] for team in resp.json()["teams"]] == [
        "nba-stonegate-drifters",
        "epl-wrenfield-athletic",
    ]
    assert fake_catalog["count"] == 2

    async def boom(league: CatalogLeague) -> list[CatalogTeam]:
        raise EspnCatalogError("upstream down")

    monkeypatch.setattr(espn_catalog, "get_league_teams", boom)
    # Still only stored picks: saved with the provider down.
    resp = await _follow(client, {"league_id": "epl", "team_provider_keys": ["201"]})
    assert resp.status_code == 200
    # A new pick does need the catalog, and fails as it always has — before
    # anything is written.
    resp = await _follow(client, {"league_id": "epl", "team_provider_keys": ["201", "202"]})
    assert resp.status_code == 502
    async with db() as session:
        assert {row.id for row in await repository.list_teams(session)} == {
            "epl-wrenfield-athletic"
        }
    assert kick_spy == ["kicked", "kicked", "kicked"]


async def test_follow_all_off_removes_only_untouched_seed_pref(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving whole-competition follow takes the headline-only seed with it,
    but never a row the user has changed.

    Left on a league that is now followed by team, the seed would keep
    silencing all but the start and final alerts of those teams.
    """
    for league_id, key, name in (
        ("mlb", "301", "Quarry Bay Anglers"),
        ("nhl", "401", "Frostmere Lanterns"),
    ):
        monkeypatch.setitem(
            CATALOG_TEAMS,
            league_id,
            [CatalogTeam(provider_key=key, name=name, abbreviation=name[:3].upper())],
        )
    whole = ("nba", "epl", "mlb", "nhl", "ucl")
    assert (
        await _follow(
            client,
            *(
                {"league_id": league_id, "team_provider_keys": [], "follow_all": True}
                for league_id in whole
            ),
        )
    ).status_code == 200

    seed = follow_all_default_events()
    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
        assert {scope: pref.events for scope, pref in prefs.items()} == {
            f"league:{league_id}": seed for league_id in whole
        }
        # epl was seeded by a build that knew five event types, not six.
        prefs["league:epl"].events = {
            event_type: enabled
            for event_type, enabled in seed.items()
            if event_type != "player_status"
        }
        await session.commit()
    # The user muted one competition and changed a toggle on another.
    for update in (
        {"scope": "league:mlb", "muted": True},
        {"scope": "league:nhl", "events": {"period_start": True}},
    ):
        assert (await client.put("/api/notifications/prefs", json=update)).status_code == 200

    # Four of the five become team follows; ucl stays a whole competition.
    resp = await _follow(
        client,
        {"league_id": "nba", "team_provider_keys": ["101"]},
        {"league_id": "epl", "team_provider_keys": ["201"]},
        {"league_id": "mlb", "team_provider_keys": ["301"]},
        {"league_id": "nhl", "team_provider_keys": ["401"]},
        {"league_id": "ucl", "team_provider_keys": [], "follow_all": True},
    )
    assert resp.status_code == 200

    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    # nba (today's seed) and epl (the five-key one) were never touched: gone.
    assert set(prefs) == {"league:mlb", "league:nhl", "league:ucl"}
    assert prefs["league:mlb"].muted is True
    assert prefs["league:mlb"].events == seed
    assert prefs["league:nhl"].muted is False
    assert prefs["league:nhl"].events == {**seed, "period_start": True}
    # Still followed in whole, so its seed is still what it should have.
    assert prefs["league:ucl"].muted is False
    assert prefs["league:ucl"].events == seed


async def test_follow_all_on_seeds_only_a_league_without_a_pref_row(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """The headline-only seed is a default, so it yields to a row that exists.

    The pref upsert merges: seeding over the user's row would turn their
    explicit choices back into the seed's.
    """
    assert (
        await _follow(
            client,
            {"league_id": "nba", "team_provider_keys": ["101"]},
            {"league_id": "epl", "team_provider_keys": ["201"]},
        )
    ).status_code == 200
    chosen = {"period_start": True, "final": False}  # the reverse of the seed
    resp = await client.put(
        "/api/notifications/prefs", json={"scope": "league:nba", "events": chosen}
    )
    assert resp.status_code == 200

    assert (
        await _follow(
            client,
            {"league_id": "nba", "team_provider_keys": [], "follow_all": True},
            {"league_id": "epl", "team_provider_keys": [], "follow_all": True},
        )
    ).status_code == 200

    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    assert {scope: pref.events for scope, pref in prefs.items()} == {
        "league:nba": chosen,
        "league:epl": follow_all_default_events(),
    }


async def test_seed_like_pref_on_a_pick_teams_league_survives_saves(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """Only a league that just LEFT whole-competition follow can lose a seed.

    A row on a league followed by team all along is the user's own, even
    when its toggles happen to agree with the seed's — ``period_start``
    off is the first thing anyone switches.  Judged by its content alone,
    every save would delete it and switch those alerts back on.
    """
    by_team = {"league_id": "nba", "team_provider_keys": ["101"]}
    selections = (by_team, {"league_id": "epl", "team_provider_keys": [], "follow_all": True})
    assert (await _follow(client, *selections)).status_code == 200
    own = {"period_start": False}
    assert follow_all_default_events()["period_start"] is False
    resp = await client.put("/api/notifications/prefs", json={"scope": "league:nba", "events": own})
    assert resp.status_code == 200

    # A plain re-save...
    assert (await _follow(client, *selections)).status_code == 200
    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    assert prefs["league:nba"].events == own

    # ...and one in which a DIFFERENT league leaves whole-competition follow.
    assert (
        await _follow(client, by_team, {"league_id": "epl", "team_provider_keys": ["201"]})
    ).status_code == 200
    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    assert {scope: pref.events for scope, pref in prefs.items()} == {"league:nba": own}


async def test_resave_does_not_seed_a_league_that_was_already_followed_in_whole(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """The seed marks a league BECOMING a whole-competition follow, once.

    A league that already was one and has no row is not waiting for its
    seed — it was followed by a build that wrote none, or the row is gone
    on purpose — and a re-save must not quietly narrow its alerts to the
    headline two.
    """
    whole = {"league_id": "nba", "team_provider_keys": [], "follow_all": True}
    assert (await _follow(client, whole)).status_code == 200
    async with db() as session:
        await session.execute(delete(NotificationPrefORM))
        await session.commit()

    assert (await _follow(client, whole)).status_code == 200
    async with db() as session:
        assert await repository.prefs_by_scope(session) == {}


async def test_follow_all_off_keeps_an_empty_pref_row(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """A row with no toggles at all is nobody's seed.

    Muting a league and unmuting it again leaves one behind.  Every key it
    holds equals the seed's, vacuously — so "untouched seed" has to ask for
    at least one key, or leaving whole-competition follow would delete a
    row the user created.
    """
    by_team = {"league_id": "nba", "team_provider_keys": ["101"]}
    assert (await _follow(client, by_team)).status_code == 200
    for muted in (True, False):
        resp = await client.put(
            "/api/notifications/prefs", json={"scope": "league:nba", "muted": muted}
        )
        assert resp.status_code == 200

    # The row exists, so becoming a whole competition seeds nothing over it...
    assert (
        await _follow(client, {"league_id": "nba", "team_provider_keys": [], "follow_all": True})
    ).status_code == 200
    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    assert (prefs["league:nba"].muted, prefs["league:nba"].events) == (False, {})

    # ...and it is still there when the league goes back to picked teams.
    assert (await _follow(client, by_team)).status_code == 200
    async with db() as session:
        prefs = await repository.prefs_by_scope(session)
    assert set(prefs) == {"league:nba"}
    assert (prefs["league:nba"].muted, prefs["league:nba"].events) == (False, {})


# ---------------------------------------------------------------------------
# Current selection — what the wizard reopens on
# ---------------------------------------------------------------------------


async def test_setup_selection_omits_siblings_and_round_trips(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """The followed set reads back as the selections that produced it, and
    saving what was read back changes nothing."""
    resp = await _follow(
        client,
        {"league_id": "nba", "team_provider_keys": ["102", "101"]},
        {"league_id": "ucl", "team_provider_keys": [], "follow_all": True},
        {"league_id": "euros", "team_provider_keys": ["478"]},
    )
    assert resp.status_code == 200
    # The national pick stored its whole slate of sibling competitions as
    # leagues — two of which the picker offers in their own right.
    assert {"worldcup", "nations-league", "euroq", "friendly"} <= {
        league["id"] for league in resp.json()["leagues"]
    }
    assert fake_catalog["count"] == 2

    resp = await client.get("/api/setup/selection")
    assert resp.status_code == 200
    selection = resp.json()
    # Only what the user chose, in stored (id) order whatever order it was
    # picked in: no sibling, and no teams listed under a whole competition.
    assert selection == {
        "selections": [
            {
                "league_id": "euros",
                "follow_all": False,
                "teams": [
                    {
                        "provider_key": "478",
                        "name": "Verdania",
                        "abbreviation": "VRD",
                        "logo_url": "https://img.example.test/verdania.png",
                        "color": "#22d3ee",
                    }
                ],
            },
            {
                "league_id": "nba",
                "follow_all": False,
                "teams": [
                    {
                        "provider_key": "101",
                        "name": "Harborlight Pelicans",
                        "abbreviation": "HLP",
                        "logo_url": "https://img.example.test/hlp.png",
                        "color": "#38bdf8",
                    },
                    {
                        "provider_key": "102",
                        "name": "Stonegate Drifters",
                        "abbreviation": "SGD",
                        "logo_url": None,
                        "color": "#f59e0b",
                    },
                ],
            },
            {"league_id": "ucl", "follow_all": True, "teams": []},
        ]
    }
    # Read from the database alone.
    assert fake_catalog["count"] == 2

    before = await _dump(db)
    resp = await client.post("/api/setup/follow", json=_as_follow_request(selection))
    assert resp.status_code == 200
    after = await _dump(db)
    for name, rows in before.items():
        assert after[name] == rows, name
    # Sent back as read, every pick is a stored one: no catalog fetch either.
    assert fake_catalog["count"] == 2
    assert (await client.get("/api/setup/selection")).json() == selection


async def test_setup_selection_whole_competition_lists_no_teams(
    client: AsyncClient, fake_catalog: dict[str, int], kick_spy: list[str]
) -> None:
    """A league followed in whole reads back as that and nothing more.

    The follow route merges two selections of one league, so a league can
    be stored both ways at once — but the wizard's modes are exclusive, and
    it could not show team picks under a whole-competition follow.
    """
    resp = await _follow(
        client,
        {"league_id": "nba", "team_provider_keys": ["101"]},
        {"league_id": "nba", "team_provider_keys": [], "follow_all": True},
    )
    assert resp.status_code == 200
    assert [league["follow_all"] for league in resp.json()["leagues"]] == [True]
    assert [team["id"] for team in resp.json()["teams"]] == ["nba-harborlight-pelicans"]

    resp = await client.get("/api/setup/selection")
    assert resp.status_code == 200
    assert resp.json() == {"selections": [{"league_id": "nba", "follow_all": True, "teams": []}]}


async def test_setup_selection_lists_only_picker_leagues(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    fake_catalog: dict[str, int],
    kick_spy: list[str],
) -> None:
    """Only a league the picker offers can come back as a selection.

    "Offered" is narrower than "known to the catalog": the internal sibling
    competitions resolve by id but are never shown as picks, so the wizard
    would have nowhere to put one.

    The cost is a documented limit, pinned at the end: a league the editor
    cannot show is not in what it posts back, so the first save from it
    unfollows that league (reachable only through a hand-authored
    ``teams.yaml``; the README says so).
    """
    resp = await client.get("/api/setup/selection")
    assert resp.status_code == 200
    assert resp.json() == {"selections": []}

    async with db() as session:
        await repository.replace_followed(
            session,
            [
                # A league from outside the catalog altogether, with a team.
                domain.League(
                    id="pinnacle-basketball",
                    sport=domain.Sport.BASKETBALL,
                    name="Pinnacle Basketball League",
                    provider="espn",
                    provider_key="basketball/pinnacle",
                ),
                # An internal sibling competition, followed in whole.
                domain.League(
                    id="friendly",
                    sport=domain.Sport.SOCCER,
                    name="International Friendly",
                    provider="espn",
                    provider_key="soccer/fifa.friendly",
                    follow_all=True,
                ),
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
        await session.commit()
    assert espn_catalog.get_catalog_league("friendly") is not None

    resp = await client.get("/api/setup/selection")
    assert resp.status_code == 200
    assert resp.json() == {"selections": []}

    # The editor opened empty; adding one team from it saves only that team.
    resp = await _follow(client, {"league_id": "nba", "team_provider_keys": ["101"]})
    assert resp.status_code == 200
    async with db() as session:
        assert [league.id for league in await repository.list_leagues(session)] == ["nba"]
        assert [team.id for team in await repository.list_teams(session)] == [
            "nba-harborlight-pelicans"
        ]


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


async def test_setup_leagues_carries_countries(client: AsyncClient) -> None:
    """The wizard's country drill-down, and what sits behind each entry.

    A country that opens an empty list is worse than no country, so only
    those with at least one league are offered — and every league that claims
    a country must name one that was actually offered.
    """
    response = await client.get("/api/setup/leagues")
    assert response.status_code == 200
    payload = response.json()

    countries = payload["countries"]
    assert countries, "expected at least one country with leagues"
    assert all(set(country) == {"code", "name", "league_count"} for country in countries)
    codes = {country["code"] for country in countries}
    assert len(codes) == len(countries), "country codes must be unique"

    # Every offered country has something behind it, and the count is the
    # real number of leagues rather than a placeholder.
    leagues = payload["leagues"]
    for country in countries:
        actual = sum(1 for lg in leagues if lg["country_code"] == country["code"])
        assert country["league_count"] == actual > 0, country["code"]

    # No league may reference a country that was not offered.
    referenced = {lg["country_code"] for lg in leagues if lg["country_code"]}
    assert referenced <= codes

    by_id = {lg["id"]: lg for lg in leagues}
    # ESPN's own prefix vocabulary, which is what makes this derivable at all.
    assert by_id["epl"]["country_code"] == "eng"
    assert by_id["laliga"]["country_code"] == "esp"
    # A confederation competition belongs to no country and must say so,
    # rather than being filed under whoever happens to host it.
    assert by_id["ucl"]["country_code"] is None
    assert by_id["worldcup"]["country_code"] is None
    # A sport organised globally never drills through a country either.
    assert by_id["nba"]["country_code"] is None
