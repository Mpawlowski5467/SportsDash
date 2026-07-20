"""API route tests against a fresh in-memory database.

Builds its own FastAPI app (no scheduler, no providers), overrides the
``app.db.get_session`` dependency with sessions from a local in-memory
aiosqlite engine, and seeds fictional data directly via the ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.config import get_settings
from tests.db_engine import create_test_schema, make_test_engine
from app.db import get_session
from app.models.domain import (
    GameOdds,
    GameSummary,
    PeriodScore,
    Performer,
    Weather,
)
from app.models.orm import (
    GameORM,
    LeagueORM,
    NewsORM,
    PlayerORM,
    StadiumORM,
    StandingsORM,
    TeamORM,
)
from app.services import game_detail as game_detail_service
from app.routes import odds as odds_route
from app.routes import router as api_router
from app.services.ics import games_to_ics
from app.timeutil import local_day_bounds, local_today, utcnow

LEAGUE_ID = "violet-circuit"
LEAGUE_NAME = "Violet Circuit Basketball"
TEAM_FOXES = "glimmer-foxes"
TEAM_HAWKS = "quarry-hawks"

GAME_LIVE = "mock:vc-live"
GAME_TONIGHT = "mock:vc-tonight"
GAME_FINAL_1 = "mock:vc-final-1"
GAME_FINAL_2 = "mock:vc-final-2"
GAME_FUTURE = "mock:vc-future"
GAME_HAWKS_ONLY = "mock:vc-hawks-only"

VENUE_WITH_COMMA = "Foxhollow Arena, Court 2"


@dataclass(frozen=True)
class Seed:
    """Reference points computed at seed time so assertions stay stable."""

    today: date
    timezone: str


def _day_start(day: date) -> datetime:
    return local_day_bounds(day, get_settings().tzinfo)[0]


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def seed(db: async_sessionmaker[AsyncSession]) -> Seed:
    settings = get_settings()
    today = local_today(settings.tzinfo)
    now = utcnow()

    async with db() as session:
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="basketball",
                name=LEAGUE_NAME,
                provider="mock",
                provider_key="mock-basketball",
            )
        )
        # Real Postgres enforces the FK graph SQLite ignored: parents must
        # be flushed before children reference them.
        await session.flush()
        session.add(
            TeamORM(
                id=TEAM_FOXES,
                league_id=LEAGUE_ID,
                name="Glimmer Foxes",
                abbreviation="GLF",
                provider_key="glimmer-foxes",
                color="#34d399",
                rss_feeds=[],
                roster_updated_at=now,
            )
        )
        session.add(
            TeamORM(
                id=TEAM_HAWKS,
                league_id=LEAGUE_ID,
                name="Quarry Hawks",
                abbreviation="QRH",
                provider_key="quarry-hawks",
                color="#f87171",
                rss_feeds=[],
            )
        )
        await session.flush()

        # In progress today (state columns set).
        session.add(
            GameORM(
                id=GAME_LIVE,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=TEAM_HAWKS,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                home_abbreviation="GLF",
                away_abbreviation="QRH",
                start_time=_day_start(today) + timedelta(hours=1),
                venue=None,
                phase="in_progress",
                home_score=54,
                away_score=61,
                period=3,
                period_label="Q3",
                clock="07:42",
                is_intermission=False,
                state_updated_at=now,
            )
        )
        # Scheduled later today; venue carries a comma (ICS escaping test).
        session.add(
            GameORM(
                id=GAME_TONIGHT,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_HAWKS,
                away_team_id=TEAM_FOXES,
                home_name="Quarry Hawks",
                away_name="Glimmer Foxes",
                home_abbreviation="QRH",
                away_abbreviation="GLF",
                start_time=_day_start(today) + timedelta(hours=5),
                venue=VENUE_WITH_COMMA,
                phase="scheduled",
            )
        )
        # Final yesterday.
        session.add(
            GameORM(
                id=GAME_FINAL_1,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=TEAM_HAWKS,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                home_abbreviation="GLF",
                away_abbreviation="QRH",
                start_time=_day_start(today - timedelta(days=1)) + timedelta(hours=2),
                phase="final",
                home_score=98,
                away_score=101,
                period=4,
                period_label="Q4",
                is_intermission=False,
                state_updated_at=now,
            )
        )
        # Final three days ago (results ordering).
        session.add(
            GameORM(
                id=GAME_FINAL_2,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_HAWKS,
                away_team_id=TEAM_FOXES,
                home_name="Quarry Hawks",
                away_name="Glimmer Foxes",
                home_abbreviation="QRH",
                away_abbreviation="GLF",
                start_time=_day_start(today - timedelta(days=3)) + timedelta(hours=2),
                phase="final",
                home_score=80,
                away_score=87,
                period=4,
                period_label="Q4",
                is_intermission=False,
                state_updated_at=now,
            )
        )
        # Scheduled next week.
        session.add(
            GameORM(
                id=GAME_FUTURE,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=TEAM_HAWKS,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                home_abbreviation="GLF",
                away_abbreviation="QRH",
                start_time=_day_start(today + timedelta(days=7)) + timedelta(hours=3),
                phase="scheduled",
            )
        )
        # Hawks-only game vs an unfollowed opponent (team filter test).
        session.add(
            GameORM(
                id=GAME_HAWKS_ONLY,
                league_id=LEAGUE_ID,
                home_team_id=TEAM_HAWKS,
                away_team_id=None,
                home_name="Quarry Hawks",
                away_name="Rivermark Owls",
                home_abbreviation="QRH",
                away_abbreviation="RVO",
                start_time=_day_start(today + timedelta(days=8)) + timedelta(hours=2),
                phase="scheduled",
            )
        )
        await session.commit()

    return Seed(today=today, timezone=settings.timezone)


@pytest.fixture
async def app(db: async_sessionmaker[AsyncSession], seed: Seed) -> FastAPI:
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


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # Backward-compatible status key plus the Phase 7a deep-check fields.
    assert data["status"] == "ok"
    assert data["database"] is True
    assert isinstance(data["providers"], int) and data["providers"] > 0
    # Per-provider circuit-breaker detail (resilience work).
    health = data["provider_health"]
    assert isinstance(health, dict)
    espn = health["espn"]
    assert espn["registered"] is True
    assert espn["circuit"] in {"closed", "open", "half_open"}


async def test_meta(client: AsyncClient) -> None:
    settings = get_settings()
    resp = await client.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "timezone": settings.timezone,
        "live_poll_seconds": settings.live_poll_seconds,
        "version": "1.0.0",
    }


async def test_teams(client: AsyncClient) -> None:
    resp = await client.get("/api/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert [league["id"] for league in data["leagues"]] == [LEAGUE_ID]
    assert data["leagues"][0]["sport"] == "basketball"
    assert {team["id"] for team in data["teams"]} == {TEAM_FOXES, TEAM_HAWKS}


async def test_today_returns_local_day_games_sorted(client: AsyncClient, seed: Seed) -> None:
    resp = await client.get("/api/today")
    assert resp.status_code == 200
    data = resp.json()

    assert data["date"] == seed.today.isoformat()
    assert data["timezone"] == seed.timezone
    # Exactly today's games, ascending by start time.
    assert [game["id"] for game in data["games"]] == [GAME_LIVE, GAME_TONIGHT]

    live, tonight = data["games"]

    # Live game: full GameOut shape with scores and live state.
    assert live["league_id"] == LEAGUE_ID
    assert live["sport"] == "basketball"
    assert live["phase"] == "in_progress"
    assert live["home"] == {
        "team_id": TEAM_FOXES,
        "name": "Glimmer Foxes",
        "abbreviation": "GLF",
        "logo_url": None,
        "color": None,
        "score": 54,
    }
    assert live["away"] == {
        "team_id": TEAM_HAWKS,
        "name": "Quarry Hawks",
        "abbreviation": "QRH",
        "logo_url": None,
        "color": None,
        "score": 61,
    }
    assert live["period"] == 3
    assert live["period_label"] == "Q3"
    assert live["clock"] == "07:42"
    assert live["is_intermission"] is False
    assert sorted(live["followed_team_ids"]) == [TEAM_FOXES, TEAM_HAWKS]
    assert live["start_time"].endswith("Z") or "+00:00" in live["start_time"]

    # Scheduled game: scores hidden until it starts.
    assert tonight["phase"] == "scheduled"
    assert tonight["home"]["score"] is None
    assert tonight["away"]["score"] is None
    assert tonight["venue"] == VENUE_WITH_COMMA
    assert tonight["period"] == 0
    assert tonight["period_label"] == ""
    assert tonight["clock"] is None


async def test_schedule_for_team_default_range(client: AsyncClient, seed: Seed) -> None:
    resp = await client.get(f"/api/schedule/{TEAM_FOXES}")
    assert resp.status_code == 200
    ids = [game["id"] for game in resp.json()]
    # Default window today-7d .. today+45d covers every foxes game, ascending;
    # the hawks-only game is filtered out.
    assert ids == [GAME_FINAL_2, GAME_FINAL_1, GAME_LIVE, GAME_TONIGHT, GAME_FUTURE]


async def test_schedule_for_team_date_filtering(client: AsyncClient, seed: Seed) -> None:
    today_iso = seed.today.isoformat()

    # Single inclusive day: exactly today's games.
    resp = await client.get(
        f"/api/schedule/{TEAM_FOXES}", params={"start": today_iso, "end": today_iso}
    )
    assert resp.status_code == 200
    assert [game["id"] for game in resp.json()] == [GAME_LIVE, GAME_TONIGHT]

    # Future window: foxes only have the next-week game.
    start = (seed.today + timedelta(days=1)).isoformat()
    end = (seed.today + timedelta(days=10)).isoformat()
    resp = await client.get(f"/api/schedule/{TEAM_FOXES}", params={"start": start, "end": end})
    assert [game["id"] for game in resp.json()] == [GAME_FUTURE]

    # Hawks additionally play the unfollowed Rivermark Owls in that window.
    resp = await client.get(f"/api/schedule/{TEAM_HAWKS}", params={"start": start, "end": end})
    games = resp.json()
    assert [game["id"] for game in games] == [GAME_FUTURE, GAME_HAWKS_ONLY]
    hawks_only = games[1]
    assert hawks_only["followed_team_ids"] == [TEAM_HAWKS]
    assert hawks_only["away"]["team_id"] is None
    assert hawks_only["away"]["name"] == "Rivermark Owls"


async def test_schedule_query_param_variant_matches_path(client: AsyncClient, seed: Seed) -> None:
    start = (seed.today + timedelta(days=1)).isoformat()
    end = (seed.today + timedelta(days=10)).isoformat()
    by_query = await client.get(
        "/api/schedule", params={"team_id": TEAM_HAWKS, "start": start, "end": end}
    )
    by_path = await client.get(f"/api/schedule/{TEAM_HAWKS}", params={"start": start, "end": end})
    assert by_query.status_code == by_path.status_code == 200
    assert by_query.json() == by_path.json()

    # Unfiltered schedule includes every seeded game.
    resp = await client.get("/api/schedule")
    assert len(resp.json()) == 6


async def test_standings_unknown_league_404(client: AsyncClient) -> None:
    resp = await client.get("/api/standings/no-such-league")
    assert resp.status_code == 404


async def test_standings_empty_before_first_fetch(client: AsyncClient) -> None:
    resp = await client.get(f"/api/standings/{LEAGUE_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["league_id"] == LEAGUE_ID
    assert data["league_name"] == LEAGUE_NAME
    assert data["sport"] == "basketball"
    assert data["season"] == ""
    assert data["fetched_at"] is None
    assert data["rows"] == []


async def test_standings_populated(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as session:
        session.add(
            StandingsORM(
                league_id=LEAGUE_ID,
                season="2026",
                rows=[
                    {
                        "rank": 1,
                        "team_name": "Glimmer Foxes",
                        "team_id": TEAM_FOXES,
                        "wins": 10,
                        "losses": 2,
                        "win_pct": 0.833,
                        "games_back": 0.0,
                    },
                    {
                        "rank": 2,
                        "team_name": "Quarry Hawks",
                        "team_id": TEAM_HAWKS,
                        "wins": 8,
                        "losses": 4,
                        "win_pct": 0.667,
                        "games_back": 2.0,
                    },
                ],
                fetched_at=utcnow(),
            )
        )
        await session.commit()

    resp = await client.get(f"/api/standings/{LEAGUE_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["season"] == "2026"
    assert data["fetched_at"] is not None
    assert [row["rank"] for row in data["rows"]] == [1, 2]
    assert data["rows"][0]["team_name"] == "Glimmer Foxes"
    assert data["rows"][0]["win_pct"] == 0.833
    assert data["rows"][1]["games_back"] == 2.0
    # Sport-irrelevant columns default to null.
    assert data["rows"][0]["points"] is None
    assert data["rows"][0]["draws"] is None
    # A just-fetched snapshot is not stale.
    assert data["is_stale"] is False


async def test_standings_is_stale_when_snapshot_is_old(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    settings = get_settings()
    old = utcnow() - timedelta(minutes=settings.data_stale_after_minutes + 60)
    async with db() as session:
        session.add(
            StandingsORM(
                league_id=LEAGUE_ID,
                season="2026",
                rows=[{"rank": 1, "team_name": "Glimmer Foxes", "wins": 10, "losses": 2}],
                fetched_at=old,
            )
        )
        await session.commit()

    resp = await client.get(f"/api/standings/{LEAGUE_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"]  # data is still served...
    assert data["is_stale"] is True  # ...but flagged stale


async def test_roster_unknown_team_404(client: AsyncClient) -> None:
    resp = await client.get("/api/roster/no-such-team")
    assert resp.status_code == 404


async def test_roster_players_ordered_by_name(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as session:
        # Inserted deliberately out of alphabetical order.
        session.add(
            PlayerORM(
                team_id=TEAM_FOXES,
                id="fox-3",
                name="Wren Okafor",
                position="C",
                jersey_number="42",
                status="injured",
                status_detail="Out - ankle",
            )
        )
        session.add(
            PlayerORM(
                team_id=TEAM_FOXES,
                id="fox-1",
                name="Avery Lindqvist",
                position="G",
                jersey_number="7",
                status="active",
            )
        )
        session.add(
            PlayerORM(
                team_id=TEAM_FOXES,
                id="fox-2",
                name="Milo Carrasco",
                position="F",
                jersey_number="23",
                status="day_to_day",
                status_detail="Questionable - knee",
            )
        )
        await session.commit()

    resp = await client.get(f"/api/roster/{TEAM_FOXES}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team_id"] == TEAM_FOXES
    assert data["team_name"] == "Glimmer Foxes"
    assert data["fetched_at"] is not None
    assert [player["name"] for player in data["players"]] == [
        "Avery Lindqvist",
        "Milo Carrasco",
        "Wren Okafor",
    ]
    injured = data["players"][2]
    assert injured["status"] == "injured"
    assert injured["status_detail"] == "Out - ankle"
    assert injured["jersey_number"] == "42"


async def test_results_unknown_team_404(client: AsyncClient) -> None:
    resp = await client.get("/api/results/no-such-team")
    assert resp.status_code == 404


async def test_results_finals_newest_first(client: AsyncClient) -> None:
    resp = await client.get(f"/api/results/{TEAM_FOXES}")
    assert resp.status_code == 200
    games = resp.json()
    assert [game["id"] for game in games] == [GAME_FINAL_1, GAME_FINAL_2]
    assert all(game["phase"] == "final" for game in games)
    assert games[0]["home"]["score"] == 98
    assert games[0]["away"]["score"] == 101

    # Limit is honored.
    resp = await client.get(f"/api/results/{TEAM_FOXES}", params={"limit": 1})
    assert [game["id"] for game in resp.json()] == [GAME_FINAL_1]


async def test_news_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/news")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.get("/api/news", params={"team_id": TEAM_FOXES})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_news_serializes_image_url(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    """image_url must survive the ORM -> NewsItemOut hop (end-to-end contract)."""
    now = utcnow()
    async with db() as session:
        session.add(
            NewsORM(
                id="news-with-image",
                team_id=TEAM_FOXES,
                title="Foxes clinch the pennant",
                url="https://news.example/foxes/clinch",
                source="Harborline Sports Desk",
                published_at=now,
                summary="A fictional triumph.",
                image_url="https://img.example/foxes/clinch.jpg",
                fetched_at=now,
            )
        )
        session.add(
            NewsORM(
                id="news-without-image",
                team_id=TEAM_FOXES,
                title="Foxes practice notes",
                url="https://news.example/foxes/practice",
                source="Fieldside Notebook",
                published_at=now - timedelta(hours=1),
                summary=None,
                image_url=None,
                fetched_at=now,
            )
        )
        await session.commit()

    resp = await client.get("/api/news", params={"team_id": TEAM_FOXES})
    assert resp.status_code == 200
    by_id = {item["id"]: item for item in resp.json()}
    assert by_id["news-with-image"]["image_url"] == ("https://img.example/foxes/clinch.jpg")
    assert by_id["news-without-image"]["image_url"] is None


async def test_calendar_ics(client: AsyncClient) -> None:
    resp = await client.get("/api/calendar.ics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert resp.headers["content-disposition"] == "attachment; filename=sportsdash.ics"

    body = resp.text
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "PRODID:-//SportsDash//EN" in body
    assert "VERSION:2.0" in body
    assert "CALSCALE:GREGORIAN" in body
    assert "\r\nBEGIN:VEVENT\r\n" in body
    assert body.count("BEGIN:VEVENT") == 6
    assert f"UID:{GAME_LIVE}@sportsdash" in body

    # RFC 5545 TEXT escaping: comma in the venue is backslash-escaped.
    assert "LOCATION:Foxhollow Arena\\, Court 2" in body
    # Finished game carries the final score in DESCRIPTION (comma escaped).
    assert "DESCRIPTION:Final: Quarry Hawks 101\\, Glimmer Foxes 98" in body
    # UTC basic-format timestamps.
    assert "DTSTART:" in body
    for line in body.split("\r\n"):
        if line.startswith(("DTSTART:", "DTEND:", "DTSTAMP:")):
            value = line.split(":", 1)[1]
            assert len(value) == 16 and value.endswith("Z")


async def test_calendar_ics_team_filter(client: AsyncClient) -> None:
    # The Foxes play in every game except the Hawks-only fixture, so the
    # per-team feed drops exactly that one event and renames the download.
    resp = await client.get("/api/calendar.ics", params={"team_id": TEAM_FOXES})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert resp.headers["content-disposition"] == (
        f"attachment; filename=sportsdash-{TEAM_FOXES}.ics"
    )

    body = resp.text
    assert body.count("BEGIN:VEVENT") == 5
    assert f"UID:{GAME_LIVE}@sportsdash" in body
    assert f"UID:{GAME_HAWKS_ONLY}@sportsdash" not in body


async def test_calendar_ics_unknown_team_404(client: AsyncClient) -> None:
    resp = await client.get("/api/calendar.ics", params={"team_id": "no-such-team"})
    assert resp.status_code == 404


async def test_ics_folds_long_lines_and_escapes() -> None:
    league = LeagueORM(
        id=LEAGUE_ID,
        sport="basketball",
        name=LEAGUE_NAME,
        provider="mock",
        provider_key="mock-basketball",
    )
    game = GameORM(
        id="mock:vc-folded",
        league_id=LEAGUE_ID,
        home_team_id=TEAM_FOXES,
        away_team_id=TEAM_HAWKS,
        home_name="Glimmer Foxes",
        away_name="Quarry Hawks",
        home_abbreviation=None,
        away_abbreviation=None,
        start_time=datetime(2026, 6, 11, 23, 30, tzinfo=None),  # naive -> ensure_utc
        venue=(
            "The Extraordinarily Long Pavilion of Greater Foxhollow; "
            "Hall B, Upper Mezzanine, Gate 12\\West"
        ),
        phase="scheduled",
        home_score=0,
        away_score=0,
        period=0,
        period_label="",
        clock=None,
        is_intermission=False,
    )

    text = games_to_ics([game], {LEAGUE_ID: league})

    lines = text.split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    # Folded continuation lines start with a space.
    assert any(line.startswith(" ") for line in lines)
    # Unfold and verify escaping of ;  ,  and backslash.
    unfolded = text.replace("\r\n ", "")
    assert "Greater Foxhollow\\;" in unfolded
    assert "Hall B\\, Upper Mezzanine\\, Gate 12\\\\West" in unfolded
    assert "DTSTART:20260611T233000Z" in unfolded
    # Basketball duration: DTEND = start + 3h.
    assert "DTEND:20260612T023000Z" in unfolded


# ---------------------------------------------------------------------------
# Game detail / box-score drill-down (Phase 7b)
# ---------------------------------------------------------------------------


class _FakeProvider:
    """A stand-in provider whose ``get_game_summary`` is fully scripted.

    ``summary`` is returned verbatim for any game; ``raises`` makes the
    call blow up so the route's never-500 contract can be exercised.
    """

    provider_id = "mock"

    def __init__(
        self,
        summary: GameSummary | None = None,
        *,
        raises: bool = False,
        odds: GameOdds | None = None,
        odds_raises: bool = False,
    ) -> None:
        self._summary = summary
        self._raises = raises
        self._odds = odds
        self._odds_raises = odds_raises
        self.calls: list[tuple[str, str]] = []
        self.odds_calls: list[tuple[str, str]] = []

    async def get_game_summary(self, league, provider_game_key):  # type: ignore[no-untyped-def]
        self.calls.append((league.id, provider_game_key))
        if self._raises:
            raise RuntimeError("boom: upstream summary fetch failed")
        return self._summary

    async def get_game_odds(self, league, provider_game_key):  # type: ignore[no-untyped-def]
        self.odds_calls.append((league.id, provider_game_key))
        if self._odds_raises:
            raise RuntimeError("boom: upstream odds fetch failed")
        return self._odds


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider) -> None:
    """Point the games route's registry lookup at ``provider``."""
    monkeypatch.setattr(game_detail_service.registry, "get_provider", lambda provider_id: provider)


async def test_game_detail_returns_game_and_summary(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = GameSummary(
        game_id=GAME_FINAL_1,
        periods=(
            PeriodScore(label="Q1", home=24, away=28),
            PeriodScore(label="Q2", home=26, away=21),
            PeriodScore(label="Q3", home=22, away=25),
            PeriodScore(label="Q4", home=26, away=27),
        ),
        performers=(
            Performer(name="Avery Ashgrove", side="home", detail="31 PTS"),
            Performer(name="Bram Coldstream", side="away", detail="29 PTS"),
        ),
        home_total=98,
        away_total=101,
    )
    fake = _FakeProvider(summary)
    _use_provider(monkeypatch, fake)

    resp = await client.get(f"/api/games/{GAME_FINAL_1}")
    assert resp.status_code == 200
    data = resp.json()

    # The game half is the regular GameOut shape.
    assert data["game"]["id"] == GAME_FINAL_1
    assert data["game"]["phase"] == "final"
    assert data["game"]["home"]["score"] == 98
    assert data["game"]["away"]["score"] == 101

    # The summary carries per-period line scores plus performers.
    summary_out = data["summary"]
    assert summary_out["game_id"] == GAME_FINAL_1
    assert [p["label"] for p in summary_out["periods"]] == ["Q1", "Q2", "Q3", "Q4"]
    assert sum(p["home"] for p in summary_out["periods"]) == 98
    assert sum(p["away"] for p in summary_out["periods"]) == 101
    assert summary_out["home_total"] == 98
    assert summary_out["away_total"] == 101
    assert {perf["side"] for perf in summary_out["performers"]} == {"home", "away"}

    # The provider was asked using the bare key (no "mock:" prefix).
    assert fake.calls == [(LEAGUE_ID, "vc-final-1")]


async def test_game_detail_unknown_game_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even if a provider were available, an unknown id never reaches it.
    fake = _FakeProvider(GameSummary(game_id="mock:nope"))
    _use_provider(monkeypatch, fake)

    resp = await client.get("/api/games/mock:no-such-game")
    assert resp.status_code == 404
    assert fake.calls == []


async def test_game_detail_summary_none_does_not_break(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A provider that has no summary for the game yields summary: null.
    _use_provider(monkeypatch, _FakeProvider(None))

    resp = await client.get(f"/api/games/{GAME_TONIGHT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["game"]["id"] == GAME_TONIGHT
    assert data["summary"] is None


async def test_game_detail_summary_failure_does_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A provider that raises must not surface as a 500 — summary degrades
    # to null while the game itself is still returned.
    _use_provider(monkeypatch, _FakeProvider(raises=True))

    resp = await client.get(f"/api/games/{GAME_LIVE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["game"]["id"] == GAME_LIVE
    assert data["game"]["phase"] == "in_progress"
    assert data["summary"] is None


SOCCER_LEAGUE = "emerald-pitch"
SOCCER_TEAM = "moss-rovers"
SOCCER_GAME = "mock:ep-scheduled"


async def _seed_scheduled_soccer_game(
    db: async_sessionmaker[AsyncSession],
) -> None:
    async with db() as session:
        session.add(
            LeagueORM(
                id=SOCCER_LEAGUE,
                sport="soccer",
                name="Emerald Pitch",
                provider="mock",
                provider_key="mock-soccer",
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamORM(
                id=SOCCER_TEAM,
                league_id=SOCCER_LEAGUE,
                name="Moss Rovers",
                abbreviation="MOS",
                provider_key="moss-rovers",
                rss_feeds=[],
                home_venue="Fernway Park",
                venue_lat=51.5,
                venue_lon=-0.12,
            )
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            GameORM(
                id=SOCCER_GAME,
                league_id=SOCCER_LEAGUE,
                home_team_id=SOCCER_TEAM,
                away_team_id=None,
                home_name="Moss Rovers",
                away_name="Tidewater FC",
                home_abbreviation="MOS",
                away_abbreviation="TID",
                start_time=utcnow() + timedelta(days=2),
                phase="scheduled",
            )
        )
        await session.commit()


async def test_game_detail_attaches_weather_for_scheduled_outdoor_game(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_scheduled_soccer_game(db)
    _use_provider(monkeypatch, _FakeProvider(None))

    seen: list[tuple[float, float]] = []

    async def fake_fetch(lat, lon, **_kwargs):  # type: ignore[no-untyped-def]
        seen.append((round(lat, 2), round(lon, 2)))
        return Weather(
            temperature=18.0,
            condition="Partly cloudy",
            code=2,
            wind_speed=12.0,
            units="metric",
            high=20.0,
            low=14.0,
            precip_chance=10,
        )

    monkeypatch.setattr(game_detail_service.weather, "fetch", fake_fetch)

    resp = await client.get(f"/api/games/{SOCCER_GAME}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] is None
    assert data["weather"]["condition"] == "Partly cloudy"
    assert data["weather"]["temperature"] == 18.0
    assert data["weather"]["units"] == "metric"
    # Resolved the home team's stored stadium coordinates.
    assert seen == [(51.5, -0.12)]


async def test_game_detail_weather_requests_game_date_not_today(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The seeded game starts in 2 days; the forecast must be requested for the
    # game's own UTC day, not today's (the bug threaded no date at all).
    await _seed_scheduled_soccer_game(db)
    _use_provider(monkeypatch, _FakeProvider(None))

    captured: dict[str, date | None] = {"date": None}

    async def fake_fetch(lat, lon, *, target_date=None, **_kwargs):  # type: ignore[no-untyped-def]
        captured["date"] = target_date
        return Weather(
            temperature=18.0,
            condition="Partly cloudy",
            code=2,
            wind_speed=12.0,
            units="metric",
        )

    monkeypatch.setattr(game_detail_service.weather, "fetch", fake_fetch)

    resp = await client.get(f"/api/games/{SOCCER_GAME}")
    assert resp.status_code == 200
    expected = datetime.fromisoformat(
        resp.json()["game"]["start_time"].replace("Z", "+00:00")
    ).date()
    assert captured["date"] == expected  # game day, not date.today()


async def test_game_detail_no_weather_for_indoor_sport(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, _FakeProvider(None))

    async def boom(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("weather must not be fetched for indoor sports")

    monkeypatch.setattr(game_detail_service.weather, "fetch", boom)

    # GAME_FUTURE is a scheduled basketball game (indoor) — no weather.
    resp = await client.get(f"/api/games/{GAME_FUTURE}")
    assert resp.status_code == 200
    assert resp.json()["weather"] is None


# ---------------------------------------------------------------------------
# Odds + win-probability (game detail + /api/odds batch)
# ---------------------------------------------------------------------------

_SAMPLE_ODDS = GameOdds(
    provider="Summit Sportsbook",
    details="VC -150",
    home_moneyline=-150,
    away_moneyline=130,
    spread=-2.5,
    over_under=210.5,
    home_win_pct=61.0,
    away_win_pct=39.0,
)


async def test_game_detail_attaches_odds(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, _FakeProvider(None, odds=_SAMPLE_ODDS))

    resp = await client.get(f"/api/games/{GAME_TONIGHT}")
    assert resp.status_code == 200
    odds = resp.json()["odds"]
    assert odds["provider"] == "Summit Sportsbook"
    assert odds["home_moneyline"] == -150
    assert odds["away_moneyline"] == 130
    assert odds["spread"] == -2.5
    assert odds["over_under"] == 210.5
    assert odds["home_win_pct"] == 61.0
    assert odds["away_win_pct"] == 39.0


async def test_game_detail_odds_failure_does_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, _FakeProvider(None, odds_raises=True))

    resp = await client.get(f"/api/games/{GAME_TONIGHT}")
    assert resp.status_code == 200
    assert resp.json()["odds"] is None


async def test_odds_batch_prices_scheduled_and_live_skips_final(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeProvider(None, odds=_SAMPLE_ODDS)
    monkeypatch.setattr(odds_route.registry, "get_provider", lambda provider_id: fake)

    resp = await client.get(f"/api/odds?ids={GAME_TONIGHT},{GAME_LIVE},{GAME_FINAL_1}")
    assert resp.status_code == 200
    data = resp.json()
    # Scheduled + live are priced; the final game is gated out entirely.
    assert set(data.keys()) == {GAME_TONIGHT, GAME_LIVE}
    assert data[GAME_TONIGHT]["home_win_pct"] == 61.0
    # The final game never reached the provider.
    assert (LEAGUE_ID, "vc-final-1") not in fake.odds_calls


async def test_odds_batch_empty_ids_returns_empty(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeProvider(None, odds=_SAMPLE_ODDS)
    monkeypatch.setattr(odds_route.registry, "get_provider", lambda provider_id: fake)
    resp = await client.get("/api/odds?ids=")
    assert resp.status_code == 200
    assert resp.json() == {}
    assert fake.odds_calls == []


async def test_odds_batch_unpriced_game_absent(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A provider with no line for the game → that game is simply omitted.
    fake = _FakeProvider(None, odds=None)
    monkeypatch.setattr(odds_route.registry, "get_provider", lambda provider_id: fake)
    resp = await client.get(f"/api/odds?ids={GAME_TONIGHT}")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# Matchup preview (/api/matchup)
# ---------------------------------------------------------------------------


def test_matchup_outcome_wld() -> None:
    from types import SimpleNamespace

    from app.routes.matchup import _outcome

    win = SimpleNamespace(home_name="A", away_name="B", home_score=2, away_score=1)
    assert _outcome(win, "A") == "W"
    assert _outcome(win, "B") == "L"
    draw = SimpleNamespace(home_name="A", away_name="B", home_score=1, away_score=1)
    assert _outcome(draw, "A") == "D"
    assert _outcome(win, "C") is None


async def test_matchup_assembles_preview(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, _FakeProvider(None, odds=_SAMPLE_ODDS))

    resp = await client.get(f"/api/matchup/{GAME_TONIGHT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["game"]["id"] == GAME_TONIGHT
    assert data["odds"]["provider"] == "Summit Sportsbook"
    # Assembly fields are always present (possibly empty), never missing.
    for key in ("home_form", "away_form", "head_to_head", "home_injuries", "away_injuries"):
        assert isinstance(data[key], list)


async def test_matchup_includes_cross_season_record(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cross-season record flows through from the builder, preferring
    the followed HOME side's perspective."""
    from app.routes import matchup as matchup_route
    from app.schemas import HeadToHeadRecordOut

    _use_provider(monkeypatch, _FakeProvider(None))

    async def fake_record(league_row, team_row, opponent_name):
        return HeadToHeadRecordOut(
            team_id=team_row.id,
            team_name=team_row.name,
            opponent_name=opponent_name,
            seasons=5,
            wins=4,
            losses=2,
            draws=1,
            meetings=[],
        )

    monkeypatch.setattr(matchup_route.head_to_head, "build_record", fake_record)

    resp = await client.get(f"/api/matchup/{GAME_TONIGHT}")
    assert resp.status_code == 200
    record = resp.json()["head_to_head_record"]
    # GAME_TONIGHT's home side is the followed Quarry Hawks.
    assert record["team_name"] == "Quarry Hawks"
    assert record["opponent_name"] == "Glimmer Foxes"
    assert (record["wins"], record["draws"], record["losses"]) == (4, 1, 2)


async def test_matchup_unknown_game_404(client: AsyncClient) -> None:
    resp = await client.get("/api/matchup/mock:no-such-game")
    assert resp.status_code == 404


async def test_map_returns_only_teams_with_coordinates_and_facts(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One followed team has resolved coordinates + stadium facts + a logo,
    # the other has no coordinates yet.
    foxes_venue = "Glimmer Arena"
    foxes_logo = "https://logos.example.com/glimmer-foxes.png"
    foxes_image = "https://images.example.com/glimmer-arena.jpg"
    foxes_description = "Glimmer Foxes are a fictional basketball club."
    foxes_venue_description = "Glimmer Arena has hosted the Foxes since 1999."
    async with db() as session:
        foxes = await session.get(TeamORM, TEAM_FOXES)
        foxes.logo_url = foxes_logo
        foxes.home_venue = foxes_venue
        foxes.venue_lat = 40.7505
        foxes.venue_lon = -73.9935
        foxes.venue_capacity = 18500
        foxes.venue_opened = 1999
        foxes.venue_image_url = foxes_image
        foxes.venue_location = "Glimmerwood, Foxhollow"
        foxes.venue_surface = "Hardwood"
        foxes.description = foxes_description
        foxes.description_source = "thesportsdb"
        foxes.venue_description = foxes_venue_description
        # Quarry Hawks deliberately left without coordinates.
        await session.commit()

    # The map route kicks an on-demand location refresh when a followed team
    # still lacks coordinates; capture the call instead of spawning the real
    # background job (which would touch the production DB / network).
    kicked: list[bool] = []
    monkeypatch.setattr(
        "app.routes.map_view.jobs.kick_locations_if_pending",
        lambda: kicked.append(True),
    )

    resp = await client.get("/api/map")
    assert resp.status_code == 200
    data = resp.json()

    # Only the coord-bearing team appears; the un-resolved one is omitted.
    assert [team["team_id"] for team in data["teams"]] == [TEAM_FOXES]
    # Hawks lacks coordinates, so an on-demand resolve was kicked.
    assert kicked == [True]

    located = data["teams"][0]
    assert located["name"] == "Glimmer Foxes"
    assert located["abbreviation"] == "GLF"
    assert located["league_id"] == LEAGUE_ID
    assert located["sport"] == "basketball"
    assert located["color"] == "#34d399"
    assert located["logo_url"] == foxes_logo
    assert located["venue"] == foxes_venue
    assert located["lat"] == pytest.approx(40.7505)
    assert located["lon"] == pytest.approx(-73.9935)
    # Stadium facts (Phase 11) flow from the team row into MapTeamOut.
    assert located["capacity"] == 18500
    assert located["opened"] == 1999
    assert located["image_url"] == foxes_image
    assert located["location"] == "Glimmerwood, Foxhollow"
    assert located["surface"] == "Hardwood"
    # The club "About" facts (history, attribution, stadium prose) flow too.
    assert located["description"] == foxes_description
    assert located["description_source"] == "thesportsdb"
    assert located["venue_description"] == foxes_venue_description


async def test_map_empty_when_no_team_has_coordinates(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The seed leaves every team without coordinates: the map is empty,
    # not an error (the frontend renders a graceful empty state), and the
    # route kicks an on-demand resolve for the still-pending teams.
    kicked: list[bool] = []
    monkeypatch.setattr(
        "app.routes.map_view.jobs.kick_locations_if_pending",
        lambda: kicked.append(True),
    )
    # The seed's live/scheduled games can't resolve a venue without coords or a
    # warm cache, so the route also kicks a background game-venue geocode —
    # stub it so the hermetic test spawns no real-DB task.
    monkeypatch.setattr("app.routes.map_view.jobs.kick_game_venue_coords", lambda: None)
    resp = await client.get("/api/map")
    assert resp.status_code == 200
    body = resp.json()
    assert body["teams"] == []
    assert body["games"] == []
    assert body["days"] == 3
    assert kicked == [True]


async def test_set_team_location_persists_stadium_facts(
    seed: Seed, db: async_sessionmaker[AsyncSession]
) -> None:
    """set_team_location stores the Phase 11 stadium facts on the team row.

    The extended keyword arguments (capacity/opened/image_url/location/
    surface) must round-trip; the over-long location/surface strings are
    clipped to their column widths.
    """
    from app.services import repository

    long_location = "L" * 300  # exceeds venue_location's 256-char column
    long_surface = "S" * 80  # exceeds venue_surface's 64-char column
    async with db() as session:
        await repository.set_team_location(
            session,
            TEAM_FOXES,
            "Glimmer Arena",
            40.7505,
            -73.9935,
            capacity=18500,
            opened=1999,
            image_url="https://images.example.com/glimmer-arena.jpg",
            location=long_location,
            surface=long_surface,
        )
        await session.commit()

    async with db() as session:
        row = await session.get(TeamORM, TEAM_FOXES)
        assert row is not None
        assert row.venue_capacity == 18500
        assert row.venue_opened == 1999
        assert row.venue_image_url == "https://images.example.com/glimmer-arena.jpg"
        assert row.venue_location == long_location[:256]
        assert row.venue_surface == long_surface[:64]


async def test_map_games_mode(client, seed, db, monkeypatch) -> None:
    """`/api/map?days=` returns upcoming games placed at venue coordinates.

    Covers the three in-memory resolution paths (a followed team's own
    coordinates, the curated World Cup host table) plus the omit-and-flag
    behavior for an unresolved venue, the window cutoff, and the `days` echo.
    """
    from app.scheduler import jobs

    # Keep the route hermetic: don't spawn background resolves at a real DB.
    monkeypatch.setattr(jobs, "kick_game_venue_coords", lambda: None)
    monkeypatch.setattr(jobs, "kick_locations_if_pending", lambda: None)
    monkeypatch.setattr(jobs, "kick_competition_stadiums", lambda: None)

    now = utcnow()
    async with db() as session:
        # A located followed team: a stadium pin AND home-game coordinates.
        foxes = await session.get(TeamORM, TEAM_FOXES)
        assert foxes is not None
        foxes.home_venue = "Glimmer Arena"
        foxes.venue_lat = 40.7505
        foxes.venue_lon = -73.9935

        session.add(  # followed home game, in window
            GameORM(
                id="mock:vc-map-soon",
                league_id=LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=TEAM_HAWKS,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                start_time=now + timedelta(hours=6),
                venue="Glimmer Arena",
                phase="scheduled",
            )
        )
        session.add(  # whole-competition game at a World Cup host venue
            GameORM(
                id="mock:vc-map-host",
                league_id=LEAGUE_ID,
                home_team_id=None,
                away_team_id=None,
                home_name="Brazil",
                away_name="Croatia",
                start_time=now + timedelta(days=1),
                venue="MetLife Stadium",
                phase="scheduled",
            )
        )
        session.add(  # beyond the 3-day window
            GameORM(
                id="mock:vc-map-far",
                league_id=LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=TEAM_HAWKS,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                start_time=now + timedelta(days=10),
                venue="Glimmer Arena",
                phase="scheduled",
            )
        )
        session.add(  # in window but venue resolves nowhere -> omitted
            GameORM(
                id="mock:vc-map-unknown",
                league_id=LEAGUE_ID,
                home_team_id=None,
                away_team_id=None,
                home_name="Mystery A",
                away_name="Mystery B",
                start_time=now + timedelta(hours=8),
                venue="Nowhere Fields, Undiscovered City",
                phase="scheduled",
            )
        )
        await session.commit()

    resp = await client.get("/api/map", params={"days": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 3

    games = {game["game_id"]: game for game in data["games"]}

    # Followed home game placed at the team's own coordinates.
    assert "mock:vc-map-soon" in games
    soon = games["mock:vc-map-soon"]
    assert (soon["lat"], soon["lon"]) == (40.7505, -73.9935)
    assert soon["followed"] is True
    assert soon["source"] == "followed"
    assert soon["home"]["name"] == "Glimmer Foxes"

    # World Cup host venue resolved from the curated table (no team coords).
    assert "mock:vc-map-host" in games
    host = games["mock:vc-map-host"]
    assert host["venue"] == "MetLife Stadium"
    assert abs(host["lat"] - 40.8135) < 0.01
    assert abs(host["lon"] - (-74.0745)) < 0.01
    assert host["followed"] is False
    assert host["source"] == "competition"

    # Out-of-window and unresolvable-venue games are absent.
    assert "mock:vc-map-far" not in games
    assert "mock:vc-map-unknown" not in games

    # The located followed team also appears as a stadium pin.
    assert any(team["team_id"] == TEAM_FOXES for team in data["teams"])


async def test_map_without_redis_omits_unresolved_venues_without_kick(
    client: AsyncClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Redis → unresolved game venues are omitted and nothing is kicked.

    The venue-coords cache is Redis-backed, so on a no-Redis install the map
    must not flag pending / kick the background geocode job on every poll
    (its writes would no-op); the game simply stays off the map until its
    venue resolves from an in-memory source.
    """
    from app.scheduler import jobs

    monkeypatch.setattr(get_settings(), "redis_url", None)
    kicked: list[bool] = []
    monkeypatch.setattr(jobs, "kick_game_venue_coords", lambda: kicked.append(True))
    # The seed's followed teams lack coordinates, which kicks unrelated
    # on-demand resolves — stub those so the test stays hermetic; only the
    # game-venue kick is asserted.
    monkeypatch.setattr(jobs, "kick_locations_if_pending", lambda: None)
    monkeypatch.setattr(jobs, "kick_competition_stadiums", lambda: None)

    async with db() as session:
        session.add(  # in window, but its venue resolves nowhere
            GameORM(
                id="mock:vc-map-noredis",
                league_id=LEAGUE_ID,
                home_team_id=None,
                away_team_id=None,
                home_name="Mystery A",
                away_name="Mystery B",
                start_time=utcnow() + timedelta(hours=8),
                venue="Nowhere Fields, Undiscovered City",
                phase="scheduled",
            )
        )
        await session.commit()

    resp = await client.get("/api/map")
    assert resp.status_code == 200
    data = resp.json()
    assert all(game["game_id"] != "mock:vc-map-noredis" for game in data["games"])
    assert kicked == []


async def test_map_plots_offseason_follow_all_league(client, db, monkeypatch) -> None:
    """An off-season ``follow_all`` league still plots its whole field.

    A whole-competition follow with NO near-window games (its tournament
    isn't running) must still return every catalog team whose home stadium
    is cached, as ``source="competition"`` — the activity gate that used to
    hide off-season leagues is gone.  The teams resolve from the stadium
    cache (``StadiumORM`` by ``"{provider}:{provider_key}"``), no host-venue
    fixtures needed.
    """
    from app.providers import espn_catalog
    from app.providers.espn_catalog import CatalogLeague, CatalogTeam
    from app.scheduler import jobs

    # Keep the route hermetic: don't spawn background resolves at a real DB.
    monkeypatch.setattr(jobs, "kick_game_venue_coords", lambda: None)
    monkeypatch.setattr(jobs, "kick_locations_if_pending", lambda: None)
    monkeypatch.setattr(jobs, "kick_competition_stadiums", lambda: None)

    # A real catalog league (so get_catalog_league resolves) followed in
    # whole, with NO games at all → it is off-season / inactive.
    catalog = espn_catalog.get_catalog_league("ucl")
    assert catalog is not None and catalog.provider == "espn"

    fields = [
        CatalogTeam(provider_key="111", name="Ivory Stags", abbreviation="IVS"),
        CatalogTeam(provider_key="222", name="Cobalt Wolves", abbreviation="COB"),
        # No stadium cached for this one → omitted (and no host fixtures
        # either, so it can't fall back anywhere).
        CatalogTeam(provider_key="333", name="Amber Larks", abbreviation="AMB"),
    ]

    async def fake_get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
        return list(fields) if league.id == "ucl" else []

    monkeypatch.setattr(espn_catalog, "get_league_teams", fake_get_league_teams)

    async with db() as session:
        session.add(
            LeagueORM(
                id="ucl",
                sport="soccer",
                name="Champions League",
                provider="espn",
                provider_key="soccer/uefa.champions",
                follow_all=True,
            )
        )
        # Two of the three teams have cached home stadiums with coords.
        session.add(
            StadiumORM(
                key="espn:111",
                team_name="Ivory Stags",
                venue="Ivory Park",
                lat=51.5079,
                lon=-0.1281,
                capacity=42000,
                resolved=True,
            )
        )
        session.add(
            StadiumORM(
                key="espn:222",
                team_name="Cobalt Wolves",
                venue="Cobalt Grounds",
                lat=48.8566,
                lon=2.3522,
                capacity=38000,
                resolved=True,
            )
        )
        await session.commit()

    resp = await client.get("/api/map")
    assert resp.status_code == 200
    data = resp.json()

    competition = {
        team["team_id"]: team for team in data["teams"] if team["source"] == "competition"
    }
    # Both cached teams appear, keyed by "{league_id}:{provider_key}"; the
    # un-cached one is omitted even though the league is off-season.
    assert set(competition) == {"ucl:111", "ucl:222"}

    stags = competition["ucl:111"]
    assert stags["name"] == "Ivory Stags"
    assert stags["league_id"] == "ucl"
    assert stags["sport"] == "soccer"
    assert stags["venue"] == "Ivory Park"
    assert stags["lat"] == pytest.approx(51.5079)
    assert stags["lon"] == pytest.approx(-0.1281)
    assert stags["capacity"] == 42000


async def test_map_days_param_validation(client) -> None:
    assert (await client.get("/api/map", params={"days": 0})).status_code == 422
    assert (await client.get("/api/map", params={"days": 31})).status_code == 422
    # Default window when omitted.
    resp = await client.get("/api/map")
    assert resp.status_code == 200
    assert resp.json()["days"] == 3
