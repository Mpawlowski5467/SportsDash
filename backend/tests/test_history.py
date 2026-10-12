"""Historical archives: the espn_history fetchers, the archive repository
helpers, and the two HTTP endpoints (standings ?season= and
/history/results). Fictional names only; all HTTP through MockTransport.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.models import domain
from app.models.orm import LeagueORM, StandingsArchiveORM, TeamORM
from app.providers import espn_history
from app.routes import router as api_router
from app.services import cache, http_client, repository, season_results
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
# A season ESPN serves in two calls (hockey, football, baseball): regular + playoffs
# — or three (basketball): the play-in between them
# ---------------------------------------------------------------------------

HOCKEY_LEAGUE_ROW = LeagueORM(
    id="frost-league",
    sport="hockey",
    name="Frost League",
    provider="espn",
    provider_key="hockey/frost",
)
HOCKEY_TEAM_ROW = TeamORM(
    id=TEAM_ID,
    league_id="frost-league",
    name="Glimmer Foxes",
    abbreviation="GLF",
    provider_key="401",
    rss_feeds=[],
)
HOCKEY_LEAGUE = domain.League(
    id="frost-league",
    sport=domain.Sport.HOCKEY,
    name="Frost League",
    provider="espn",
    provider_key="hockey/frost",
)
SEASON_KEY = f"history:results:{TEAM_ID}:2024"


def _final_event(event_id: str, start_iso: str, opponent: str) -> dict[str, Any]:
    def side(home_away: str, name: str, abbreviation: str, team_id: str, score: int) -> dict:
        return {
            "homeAway": home_away,
            "score": {"value": score},
            "team": {"displayName": name, "abbreviation": abbreviation, "id": team_id},
        }

    return {
        "id": event_id,
        "date": start_iso,
        "competitions": [
            {
                "id": event_id,
                "date": start_iso,
                "status": {"type": {"state": "post", "completed": True, "name": "STATUS_FINAL"}},
                "competitors": [
                    side("home", "Glimmer Foxes", "GLF", "401", 3),
                    side("away", opponent, "OPP", "402", 1),
                ],
            }
        ],
    }


_REGULAR_SEASON = {
    "events": [
        _final_event(f"reg-{index}", f"2024-01-{index + 1:02d}T00:00Z", "Quarry Hawks")
        for index in range(20)
    ]
}
_PLAYOFFS = {
    "events": [
        _final_event(f"po-{index}", f"2024-05-{index + 1:02d}T00:00Z", "Rivermark Owls")
        for index in range(4)
    ]
}


def _route_split_season(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve the two halves by ``seasontype``; the regular season can be down.

    Returns the knobs: ``state["regular_down"]`` and the ``state["calls"]``
    log of the seasontype each request asked for.
    """
    monkeypatch.setattr(get_settings(), "provider_max_retries", 0)  # no backoff sleeps
    state: dict[str, Any] = {"regular_down": True, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seasontype = request.url.params.get("seasontype")
        state["calls"].append(seasontype)
        if seasontype == "2":
            if state["regular_down"]:
                return httpx.Response(503, text="schedule down")
            return httpx.Response(200, json=_REGULAR_SEASON)
        return httpx.Response(200, json=_PLAYOFFS)

    _route_history(monkeypatch, handler)
    return state


async def test_fetch_season_results_raises_with_what_it_got_when_a_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half a season is not the season, and the caller has to be told.

    The playoff call answered, so its finals are handed over on the error —
    still worth showing — but they must not come back as the plain list a
    whole season does.  A call that ANSWERS with nothing is no failure.
    """
    state = _route_split_season(monkeypatch)

    with pytest.raises(espn_history.EspnHistoryError) as caught:
        await espn_history.fetch_season_results(HOCKEY_LEAGUE, FOXES, 2024)
    assert [game.id for game in caught.value.games] == [
        f"espn:po-{index}" for index in (3, 2, 1, 0)
    ]

    state["regular_down"] = False
    whole = await espn_history.fetch_season_results(HOCKEY_LEAGUE, FOXES, 2024)
    assert len(whole) == 24

    # An off-season answer — both calls fine, no events — is just empty.
    _route_history(monkeypatch, lambda request: httpx.Response(200, json={"events": []}))
    assert await espn_history.fetch_season_results(HOCKEY_LEAGUE, FOXES, 2024) == []


async def test_half_fetched_season_is_returned_but_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache holds a finished season for 7 days, so it may only be given
    a whole one.  With the regular-season call failing, the playoff games
    were cached as the season: the Results archive, the head-to-head record
    and on-this-day then missed twenty games for a week after ESPN recovered."""
    state = _route_split_season(monkeypatch)

    partial = await season_results.season_results_out(HOCKEY_LEAGUE_ROW, HOCKEY_TEAM_ROW, 2024)
    assert len(partial) == 4  # the half that answered is still shown
    assert SEASON_KEY not in cache._local

    state["regular_down"] = False
    state["calls"].clear()
    whole = await season_results.season_results_out(HOCKEY_LEAGUE_ROW, HOCKEY_TEAM_ROW, 2024)
    assert state["calls"] == ["2", "3"]  # asked again: nothing had been kept
    assert len(whole) == 24
    assert SEASON_KEY in cache._local

    state["calls"].clear()
    assert await season_results.season_results_out(HOCKEY_LEAGUE_ROW, HOCKEY_TEAM_ROW, 2024)
    assert state["calls"] == []  # the whole season is served from the cache


async def test_last_years_season_key_keeps_the_short_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESPN keys some seasons by the year they START in, so in January the
    season being played is LAST year's key.  Given the finished-season TTL
    it showed a results list up to a week behind, as if complete."""
    state = _route_split_season(monkeypatch)
    state["regular_down"] = False
    monkeypatch.setattr(
        season_results, "utcnow", lambda: datetime(2027, 1, 15, tzinfo=timezone.utc)
    )
    ttls: dict[str, int] = {}

    async def record(key: str, value: object, ttl_seconds: int) -> None:
        ttls[key.rsplit(":", 1)[1]] = ttl_seconds

    monkeypatch.setattr(season_results.cache, "cache_set_json", record)

    for season in (2027, 2026, 2025):
        await season_results.season_results_out(HOCKEY_LEAGUE_ROW, HOCKEY_TEAM_ROW, season)

    assert ttls == {"2027": 6 * 3600, "2026": 6 * 3600, "2025": 7 * 24 * 3600}


BASEBALL_LEAGUE = domain.League(
    id="meridian-baseball",
    sport=domain.Sport.BASEBALL,
    name="Meridian Baseball Association",
    provider="espn",
    provider_key="baseball/meridian",
)


@pytest.mark.parametrize(("made_postseason", "finals"), [(True, 24), (False, 20)])
async def test_baseball_season_is_the_regular_season_and_the_postseason(
    monkeypatch: pytest.MonkeyPatch, made_postseason: bool, finals: int
) -> None:
    """ESPN's bare baseball schedule is one phase of a season (verified live
    2026-10-11): with a postseason under way it is those games alone, or no
    events at all for a team that missed out, and for a past year it is the
    regular season without its postseason.  Asked for by seasontype, the
    season comes back whole."""
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seasontype = request.url.params.get("seasontype")
        calls.append(seasontype)
        if seasontype == "2":
            return httpx.Response(200, json=_REGULAR_SEASON)
        # seasontype=3 — and what a bare call got mid-postseason.
        return httpx.Response(200, json=_PLAYOFFS if made_postseason else {"events": []})

    _route_history(monkeypatch, handler)
    games = await espn_history.fetch_season_results(BASEBALL_LEAGUE, FOXES, 2024)

    assert calls == ["2", "3"]
    assert len(games) == finals
    assert games[-1].id == "espn:reg-0"  # the regular season, back to its first game
    assert games[0].id == ("espn:po-3" if made_postseason else "espn:reg-19")


_PLAY_IN = {
    "events": [
        _final_event(f"pi-{index}", f"2024-04-{index + 16:02d}T00:00Z", "Cinder Vale Lynx")
        for index in range(2)
    ]
}


@pytest.mark.parametrize(
    ("play_in", "playoffs", "newest", "finals"),
    [
        (True, True, "po-3", 26),
        (True, False, "pi-1", 22),  # out in the play-in
        (False, True, "po-3", 24),  # seeded past it, or a league with none
        (False, False, "reg-19", 20),
    ],
)
async def test_basketball_season_is_the_regular_season_the_play_in_and_the_postseason(
    monkeypatch: pytest.MonkeyPatch, play_in: bool, playoffs: bool, newest: str, finals: int
) -> None:
    """ESPN's bare basketball schedule is one phase of a season (verified
    live 2026-10-11): for a past year it is the regular season without its
    postseason.  And play-in games are a season type of their own (5),
    under neither the regular season (2) nor the postseason (3).  A league
    with no play-in answers that call with no events, like any empty phase."""
    seen: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seasontype = request.url.params.get("seasontype")
        seen.append((seasontype, request.url.params.get("season")))
        if seasontype == "2":
            return httpx.Response(200, json=_REGULAR_SEASON)
        if seasontype == "5" and play_in:
            return httpx.Response(200, json=_PLAY_IN)
        if seasontype == "3" and playoffs:
            return httpx.Response(200, json=_PLAYOFFS)
        return httpx.Response(200, json={"events": []})

    _route_history(monkeypatch, handler)
    games = await espn_history.fetch_season_results(BASKETBALL_LEAGUE, FOXES, 2024)

    assert seen == [("2", "2024"), ("5", "2024"), ("3", "2024")]
    assert len(games) == finals
    assert games[0].id == f"espn:{newest}"
    assert games[-1].id == "espn:reg-0"  # the regular season, back to its first game
    assert ("espn:pi-0" in {game.id for game in games}) is play_in


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
