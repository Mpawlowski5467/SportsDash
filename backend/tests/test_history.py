"""Historical archives: the espn_history fetchers, the archive repository
helpers, and the two HTTP endpoints (standings ?season= and
/history/results). Fictional names only; all HTTP through MockTransport.
"""

from __future__ import annotations

from datetime import timedelta
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session
from app.models import domain
from app.models.orm import LeagueORM, StandingsArchiveORM, TeamORM
from app.providers import espn_history
from app.routes import router as api_router
from app.services import http_client, repository
from app.timeutil import utcnow
from tests.db_engine import create_test_schema, make_test_engine

LEAGUE_ID = "violet-circuit"
TEAM_ID = "glimmer-foxes"

BASKETBALL_LEAGUE = domain.League(
    id=LEAGUE_ID,
    sport=domain.Sport.BASKETBALL,
    name="Violet Circuit Basketball",
    provider="espn",
    provider_key="basketball/crestline",
)
FOXES = domain.Team(
    id=TEAM_ID,
    league_id=LEAGUE_ID,
    name="Glimmer Foxes",
    abbreviation="GLF",
    provider_key="401",
)


def _route_history(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setitem(
        http_client._clients,
        "espn-history",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ---------------------------------------------------------------------------
# season_key_from_label
# ---------------------------------------------------------------------------


def test_season_key_from_label() -> None:
    assert repository.season_key_from_label("2026") == "2026"
    assert repository.season_key_from_label("2025-26") == "2026"
    assert repository.season_key_from_label(" 2025-26 ") == "2026"
    assert repository.season_key_from_label("Regular Season") is None
    assert repository.season_key_from_label("") is None


# ---------------------------------------------------------------------------
# espn_history fetchers
# ---------------------------------------------------------------------------


def _standings_payload() -> dict:
    return {
        "children": [
            {
                "name": "Harbor Conference",
                "standings": {
                    "entries": [
                        {
                            "team": {"displayName": "Glimmer Foxes", "abbreviation": "GLF"},
                            "stats": [
                                {"name": "wins", "value": 52},
                                {"name": "losses", "value": 30},
                                {"name": "playoffSeed", "value": 1},
                            ],
                        }
                    ]
                },
            }
        ],
        "season": {"displayName": "2019-20"},
    }


async def test_fetch_season_standings_passes_season_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_standings_payload())

    _route_history(monkeypatch, handler)
    standings = await espn_history.fetch_season_standings(BASKETBALL_LEAGUE, 2020)
    assert seen["season"] == "2020"
    assert seen["level"] == "3"
    assert standings is not None
    assert [row.team_name for row in standings.rows] == ["Glimmer Foxes"]


async def test_fetch_season_standings_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route_history(monkeypatch, lambda r: httpx.Response(404, text="no such season"))
    assert await espn_history.fetch_season_standings(BASKETBALL_LEAGUE, 1994) is None


async def test_fetch_season_standings_skips_individual_sports() -> None:
    tennis = domain.League(
        id="tour", sport=domain.Sport.TENNIS, name="Tour", provider="espn", provider_key="tennis/x"
    )
    assert await espn_history.fetch_season_standings(tennis, 2020) is None


def _schedule_payload() -> dict:
    return {
        "events": [
            {
                "id": "hist-1",
                "date": "2020-02-01T00:00Z",
                "competitions": [
                    {
                        "id": "hist-1",
                        "date": "2020-02-01T00:00Z",
                        "status": {
                            "type": {"state": "post", "completed": True, "name": "STATUS_FINAL"}
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": {"value": 101},
                                "team": {
                                    "displayName": "Glimmer Foxes",
                                    "abbreviation": "GLF",
                                    "id": "401",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": {"value": 99},
                                "team": {
                                    "displayName": "Quarry Hawks",
                                    "abbreviation": "QRH",
                                    "id": "402",
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "id": "hist-2",
                "date": "2020-02-08T00:00Z",
                "competitions": [
                    {
                        "id": "hist-2",
                        "date": "2020-02-08T00:00Z",
                        "status": {
                            "type": {"state": "pre", "completed": False, "name": "STATUS_SCHEDULED"}
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {
                                    "displayName": "Glimmer Foxes",
                                    "abbreviation": "GLF",
                                    "id": "401",
                                },
                            },
                            {
                                "homeAway": "away",
                                "team": {
                                    "displayName": "Rivermark Owls",
                                    "abbreviation": "RVO",
                                    "id": "403",
                                },
                            },
                        ],
                    }
                ],
            },
        ]
    }


async def test_fetch_season_results_returns_finals_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seasons_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seasons_seen.append(request.url.params.get("season", ""))
        return httpx.Response(200, json=_schedule_payload())

    _route_history(monkeypatch, handler)
    games = await espn_history.fetch_season_results(BASKETBALL_LEAGUE, FOXES, 2020)
    assert set(seasons_seen) == {"2020"}
    assert [game.id for game in games] == ["espn:hist-1"]
    assert games[0].state is not None
    assert games[0].state.phase is domain.GamePhase.FINAL


# ---------------------------------------------------------------------------
# Archive repository + HTTP endpoints
# ---------------------------------------------------------------------------


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
                name="Violet Circuit Basketball",
                provider="espn",
                provider_key="basketball/crestline",
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamORM(
                id=TEAM_ID,
                league_id=LEAGUE_ID,
                name="Glimmer Foxes",
                abbreviation="GLF",
                provider_key="401",
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


async def test_archive_upsert_and_get(db: async_sessionmaker[AsyncSession]) -> None:
    standings = domain.Standings(
        league_id=LEAGUE_ID,
        season="2019-20",
        rows=(
            domain.StandingRow(
                rank=1, team_name="Glimmer Foxes", wins=52, losses=30, team_id=TEAM_ID
            ),
        ),
        fetched_at=utcnow(),
    )
    async with db() as session:
        # league row must exist for the FK
        session.add(
            LeagueORM(
                id=LEAGUE_ID, sport="basketball", name="VC", provider="espn", provider_key="k"
            )
        )
        await session.flush()
        row = await repository.save_standings_archive(session, standings)
        assert row is not None
        await session.commit()

    async with db() as session:
        row = await repository.get_standings_archive(session, LEAGUE_ID, "2020")
        assert row is not None
        assert row.season_label == "2019-20"
        # Followed-team tags are stripped — they reference the CURRENT follow set.
        assert row.rows[0]["team_id"] is None

        # Upsert: same season key replaces, never duplicates.
        updated = domain.Standings(
            league_id=LEAGUE_ID,
            season="2019-20",
            rows=(domain.StandingRow(rank=1, team_name="Quarry Hawks", wins=60, losses=22),),
            fetched_at=utcnow() + timedelta(hours=1),
        )
        await repository.save_standings_archive(session, updated)
        await session.commit()

    async with db() as session:
        row = await repository.get_standings_archive(session, LEAGUE_ID, "2020")
        assert row is not None
        assert row.rows[0]["team_name"] == "Quarry Hawks"


async def test_standings_season_serves_archive_without_provider(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as session:
        session.add(
            StandingsArchiveORM(
                league_id=LEAGUE_ID,
                season="2019",
                season_label="2018-19",
                rows=[{"rank": 1, "team_name": "Glimmer Foxes", "wins": 50, "losses": 32}],
            )
        )
        await session.commit()

    response = await client.get(f"/api/standings/{LEAGUE_ID}", params={"season": 2019})
    assert response.status_code == 200
    payload = response.json()
    assert payload["season"] == "2018-19"
    assert payload["is_stale"] is False
    assert [row["team_name"] for row in payload["rows"]] == ["Glimmer Foxes"]


async def test_standings_season_backfills_from_espn_and_archives(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def fake_fetch(league: domain.League, season: int) -> domain.Standings:
        calls.append(season)
        return domain.Standings(
            league_id=league.id,
            season="2016-17",
            rows=(domain.StandingRow(rank=1, team_name="Quarry Hawks", wins=61, losses=21),),
            fetched_at=utcnow(),
        )

    monkeypatch.setattr("app.routes.standings.espn_history.fetch_season_standings", fake_fetch)

    first = await client.get(f"/api/standings/{LEAGUE_ID}", params={"season": 2017})
    assert first.status_code == 200
    assert first.json()["season"] == "2016-17"

    # Second request is served from the archive — no second upstream call.
    second = await client.get(f"/api/standings/{LEAGUE_ID}", params={"season": 2017})
    assert second.status_code == 200
    assert calls == [2017]

    async with db() as session:
        assert await repository.get_standings_archive(session, LEAGUE_ID, "2017") is not None


async def test_standings_season_unavailable_404s(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(league: domain.League, season: int) -> None:
        return None

    monkeypatch.setattr("app.routes.standings.espn_history.fetch_season_standings", fake_fetch)
    response = await client.get(f"/api/standings/{LEAGUE_ID}", params={"season": 1994})
    assert response.status_code == 404


async def test_history_results_serves_finals(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(
        league: domain.League, team: domain.Team, season: int
    ) -> list[domain.Game]:
        return [
            domain.Game(
                id="espn:hist-1",
                league_id=league.id,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                home_team_id=team.id,
                home_abbreviation="GLF",
                away_abbreviation="QRH",
                start_time=utcnow() - timedelta(days=800),
                state=domain.GameState(
                    game_id="espn:hist-1",
                    phase=domain.GamePhase.FINAL,
                    home_score=101,
                    away_score=99,
                    period=4,
                    period_label="Q4",
                ),
            )
        ]

    monkeypatch.setattr("app.routes.history.espn_history.fetch_season_results", fake_fetch)
    response = await client.get(f"/api/history/results/{TEAM_ID}", params={"season": 2020})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["home"]["score"] == 101
    assert payload[0]["phase"] == "final"
    assert payload[0]["followed_team_ids"] == [TEAM_ID]


async def test_history_results_unknown_team_404(client: AsyncClient) -> None:
    response = await client.get("/api/history/results/nope", params={"season": 2020})
    assert response.status_code == 404


async def test_history_results_requires_season(client: AsyncClient) -> None:
    response = await client.get(f"/api/history/results/{TEAM_ID}")
    assert response.status_code == 422
