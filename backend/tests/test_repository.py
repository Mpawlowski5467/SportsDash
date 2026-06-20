"""Repository tests against a throwaway in-memory SQLite database.

The engine is built directly here (never via ``app.db``) so global
settings and the app engine stay untouched.  All fixture data is
fictional.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import domain
from app.models.orm import Base, NewsORM
from app.services import repository
from app.timeutil import ensure_utc, utcnow

LEAGUE_ID = "pinnacle-basketball"
TEAM_COMETS = "ashport-comets"
TEAM_STAGS = "rivermont-stags"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    """Session pre-loaded with a fictional league and two followed teams."""
    await repository.upsert_league(
        session,
        domain.League(
            id=LEAGUE_ID,
            sport=domain.Sport.BASKETBALL,
            name="Pinnacle Basketball League",
            provider="mock",
            provider_key="mock-basketball",
        ),
    )
    await repository.upsert_team(
        session,
        domain.Team(
            id=TEAM_COMETS,
            league_id=LEAGUE_ID,
            name="Ashport Comets",
            abbreviation="ASH",
            provider_key="ashport-comets",
            color="#f59e0b",
        ),
    )
    await repository.upsert_team(
        session,
        domain.Team(
            id=TEAM_STAGS,
            league_id=LEAGUE_ID,
            name="Rivermont Stags",
            abbreviation="RIV",
            provider_key="rivermont-stags",
            color="#10b981",
        ),
    )
    await session.flush()
    return session


def make_game(
    game_id: str,
    *,
    start_time: datetime,
    home_team_id: str | None = None,
    away_team_id: str | None = None,
    home_name: str = "Ashport Comets",
    away_name: str = "Rivermont Stags",
    venue: str | None = "Ashport Fieldhouse",
    state: domain.GameState | None = None,
) -> domain.Game:
    return domain.Game(
        id=game_id,
        league_id=LEAGUE_ID,
        home_name=home_name,
        away_name=away_name,
        start_time=start_time,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_abbreviation="ASH",
        away_abbreviation="RIV",
        venue=venue,
        state=state,
    )


# ---------------------------------------------------------------------------
# Leagues / teams
# ---------------------------------------------------------------------------


async def test_upsert_league_and_team_insert_then_update(session: AsyncSession) -> None:
    league = domain.League(
        id=LEAGUE_ID,
        sport=domain.Sport.BASKETBALL,
        name="Pinnacle Basketball League",
        provider="mock",
        provider_key="mock-basketball",
    )
    await repository.upsert_league(session, league)
    await session.flush()

    stored = await repository.get_league(session, LEAGUE_ID)
    assert stored is not None
    assert stored.name == "Pinnacle Basketball League"
    assert stored.sport == "basketball"
    assert stored.provider == "mock"

    # Update path: same id, changed fields.
    await repository.upsert_league(
        session,
        domain.League(
            id=LEAGUE_ID,
            sport=domain.Sport.BASKETBALL,
            name="Pinnacle Premier Basketball",
            provider="espn",
            provider_key="basketball/pinnacle",
        ),
    )
    await session.flush()
    stored = await repository.get_league(session, LEAGUE_ID)
    assert stored is not None
    assert stored.name == "Pinnacle Premier Basketball"
    assert stored.provider == "espn"
    assert len(await repository.list_leagues(session)) == 1

    team = domain.Team(
        id=TEAM_COMETS,
        league_id=LEAGUE_ID,
        name="Ashport Comets",
        abbreviation="ASH",
        provider_key="ashport-comets",
        rss_feeds=("https://news.example/ashport.xml",),
    )
    await repository.upsert_team(session, team)
    await session.flush()
    stored_team = await repository.get_team(session, TEAM_COMETS)
    assert stored_team is not None
    assert stored_team.abbreviation == "ASH"
    assert stored_team.rss_feeds == ["https://news.example/ashport.xml"]

    await repository.upsert_team(
        session,
        domain.Team(
            id=TEAM_COMETS,
            league_id=LEAGUE_ID,
            name="Ashport Comets",
            abbreviation="ASC",
            provider_key="ashport-comets",
            color="#f59e0b",
        ),
    )
    await session.flush()
    stored_team = await repository.get_team(session, TEAM_COMETS)
    assert stored_team is not None
    assert stored_team.abbreviation == "ASC"
    assert stored_team.color == "#f59e0b"
    assert len(await repository.list_teams(session)) == 1
    assert await repository.list_teams(session, league_id="other-league") == []


# ---------------------------------------------------------------------------
# Game upsert semantics
# ---------------------------------------------------------------------------


async def test_upsert_games_insert_then_update_schedule_fields(
    seeded: AsyncSession,
) -> None:
    session = seeded
    start = datetime(2026, 6, 20, 23, 0, tzinfo=timezone.utc)
    game = make_game("mock:g1", start_time=start, home_team_id=TEAM_COMETS)
    assert await repository.upsert_games(session, [game]) == 1
    await session.flush()

    moved = datetime(2026, 6, 21, 1, 30, tzinfo=timezone.utc)
    updated = make_game(
        "mock:g1",
        start_time=moved,
        home_team_id=TEAM_COMETS,
        venue="Rivermont Arena",
    )
    assert await repository.upsert_games(session, [updated]) == 1
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:g1")
    assert row is not None
    assert ensure_utc(row.start_time) == moved
    assert row.venue == "Rivermont Arena"
    assert row.phase == "scheduled"


async def test_upsert_games_preserves_live_state_on_schedule_refresh(
    seeded: AsyncSession,
) -> None:
    session = seeded
    start = utcnow() - timedelta(minutes=50)
    await repository.upsert_games(
        session, [make_game("mock:g-live", start_time=start, home_team_id=TEAM_COMETS)]
    )
    await session.flush()

    live = domain.GameState(
        game_id="mock:g-live",
        phase=domain.GamePhase.IN_PROGRESS,
        home_score=54,
        away_score=49,
        period=3,
        period_label="Q3",
        clock="07:42",
        is_intermission=False,
    )
    assert await repository.apply_game_state(session, live) is not None
    await session.flush()

    # A schedule refresh for the same game carries no .state and a
    # tweaked start_time: schedule fields update, live state survives.
    new_start = start + timedelta(minutes=5)
    await repository.upsert_games(
        session,
        [make_game("mock:g-live", start_time=new_start, home_team_id=TEAM_COMETS)],
    )
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:g-live")
    assert row is not None
    assert ensure_utc(row.start_time) == new_start
    assert row.phase == "in_progress"
    assert (row.home_score, row.away_score) == (54, 49)
    assert row.period == 3
    assert row.period_label == "Q3"
    assert row.clock == "07:42"


async def test_upsert_games_applies_incoming_state_while_scheduled(
    seeded: AsyncSession,
) -> None:
    session = seeded
    start = utcnow() - timedelta(minutes=10)
    await repository.upsert_games(
        session, [make_game("mock:g-sched", start_time=start)]
    )
    await session.flush()

    # Stored phase is 'scheduled' and the incoming Game carries a state:
    # the state must be applied (covers scoreboard fetches and
    # scheduled -> postponed/canceled moves).
    incoming = make_game(
        "mock:g-sched",
        start_time=start,
        state=domain.GameState(
            game_id="mock:g-sched",
            phase=domain.GamePhase.POSTPONED,
            home_score=0,
            away_score=0,
        ),
    )
    await repository.upsert_games(session, [incoming])
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:g-sched")
    assert row is not None
    assert row.phase == "postponed"


async def test_upsert_games_merges_team_ids(seeded: AsyncSession) -> None:
    session = seeded
    start = datetime(2026, 6, 25, 0, 0, tzinfo=timezone.utc)

    # The home team's schedule fetch knows only its own side...
    await repository.upsert_games(
        session,
        [make_game("mock:g-merge", start_time=start, home_team_id=TEAM_COMETS)],
    )
    await session.flush()

    # ...and the away team's fetch knows only the other side.  Neither
    # incoming None may erase the previously stored id.
    await repository.upsert_games(
        session,
        [make_game("mock:g-merge", start_time=start, away_team_id=TEAM_STAGS)],
    )
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:g-merge")
    assert row is not None
    assert row.home_team_id == TEAM_COMETS
    assert row.away_team_id == TEAM_STAGS


# ---------------------------------------------------------------------------
# State application / round-trip
# ---------------------------------------------------------------------------


async def test_apply_game_state_and_state_from_row_round_trip(
    seeded: AsyncSession,
) -> None:
    session = seeded
    start = utcnow() - timedelta(minutes=30)
    await repository.upsert_games(
        session, [make_game("mock:g-rt", start_time=start, home_team_id=TEAM_COMETS)]
    )
    await session.flush()

    state = domain.GameState(
        game_id="mock:g-rt",
        phase=domain.GamePhase.IN_PROGRESS,
        home_score=21,
        away_score=18,
        period=2,
        period_label="Q2",
        clock="03:15",
        is_intermission=False,
    )
    row = await repository.apply_game_state(session, state)
    assert row is not None
    await session.flush()
    session.expire_all()  # force a real DB read (SQLite returns naive datetimes)

    reloaded = await repository.get_game(session, "mock:g-rt")
    assert reloaded is not None
    snapshot = repository.state_from_row(reloaded)
    assert snapshot.game_id == "mock:g-rt"
    assert snapshot.phase is domain.GamePhase.IN_PROGRESS
    assert (snapshot.home_score, snapshot.away_score) == (21, 18)
    assert snapshot.period == 2
    assert snapshot.period_label == "Q2"
    assert snapshot.clock == "03:15"
    assert snapshot.is_intermission is False
    assert snapshot.last_update is not None
    assert snapshot.last_update.tzinfo is not None
    assert snapshot.last_update.utcoffset() == timedelta(0)
    assert utcnow() - snapshot.last_update < timedelta(minutes=1)

    assert await repository.apply_game_state(
        session,
        domain.GameState(
            game_id="mock:does-not-exist",
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=0,
            away_score=0,
        ),
    ) is None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def test_games_between_bounds_and_team_filter(seeded: AsyncSession) -> None:
    session = seeded
    base = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    await repository.upsert_games(
        session,
        [
            make_game("mock:gb-0", start_time=base, home_team_id=TEAM_COMETS),
            make_game(
                "mock:gb-1",
                start_time=base + timedelta(days=1),
                away_team_id=TEAM_STAGS,
            ),
            make_game(
                "mock:gb-2",
                start_time=base + timedelta(days=2),
                home_team_id=TEAM_COMETS,
            ),
        ],
    )
    await session.flush()

    # Half-open interval: start inclusive, end exclusive.
    rows = await repository.games_between(session, base, base + timedelta(days=2))
    assert [r.id for r in rows] == ["mock:gb-0", "mock:gb-1"]

    rows = await repository.games_between(
        session, base, base + timedelta(days=3), team_id=TEAM_COMETS
    )
    assert [r.id for r in rows] == ["mock:gb-0", "mock:gb-2"]

    rows = await repository.games_between(
        session, base, base + timedelta(days=3), team_id=TEAM_STAGS
    )
    assert [r.id for r in rows] == ["mock:gb-1"]


async def test_upcoming_games_for_leagues(seeded: AsyncSession) -> None:
    """Map games mode: scheduled-in-window + in-progress (whenever it started)."""
    session = seeded
    now = utcnow()

    def live_state(game_id: str) -> domain.GameState:
        return domain.GameState(
            game_id=game_id,
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=1,
            away_score=0,
            period=1,
            period_label="Q1",
        )

    def final_state(game_id: str) -> domain.GameState:
        return domain.GameState(
            game_id=game_id,
            phase=domain.GamePhase.FINAL,
            home_score=2,
            away_score=1,
            period=4,
            period_label="Q4",
        )

    await repository.upsert_games(
        session,
        [
            # Scheduled within the 3-day window — included.
            make_game("mock:u-soon", start_time=now + timedelta(hours=6)),
            # Scheduled beyond the window — excluded.
            make_game("mock:u-far", start_time=now + timedelta(days=10)),
            # Live now, started two hours ago — included despite start < window.
            make_game(
                "mock:u-live",
                start_time=now - timedelta(hours=2),
                state=live_state("mock:u-live"),
            ),
            # Already final — excluded.
            make_game(
                "mock:u-final",
                start_time=now + timedelta(hours=3),
                state=final_state("mock:u-final"),
            ),
        ],
    )
    await session.flush()

    rows = await repository.upcoming_games_for_leagues(
        session, [LEAGUE_ID], now, now + timedelta(days=3)
    )
    assert {r.id for r in rows} == {"mock:u-soon", "mock:u-live"}

    # Unknown / empty league set yields nothing (no SQL error on empty IN).
    assert await repository.upcoming_games_for_leagues(
        session, [], now, now + timedelta(days=3)
    ) == []
    assert await repository.upcoming_games_for_leagues(
        session, ["no-such-league"], now, now + timedelta(days=3)
    ) == []


async def test_map_relevant_league_ids(seeded: AsyncSession) -> None:
    """A league qualifies via a followed team OR follow_all; deduped + sorted."""
    session = seeded  # LEAGUE_ID has two followed teams
    await repository.upsert_league(
        session,
        domain.League(
            id="whole-cup",
            sport=domain.Sport.SOCCER,
            name="Whole Cup",
            provider="mock",
            provider_key="mock-soccer",
            follow_all=True,
        ),
    )
    await session.flush()
    ids = await repository.map_relevant_league_ids(session)
    assert ids == sorted([LEAGUE_ID, "whole-cup"])


async def test_results_for_team_only_finals_newest_first(
    seeded: AsyncSession,
) -> None:
    session = seeded
    base = utcnow() - timedelta(days=10)

    def final_state(game_id: str, home: int, away: int) -> domain.GameState:
        return domain.GameState(
            game_id=game_id,
            phase=domain.GamePhase.FINAL,
            home_score=home,
            away_score=away,
            period=4,
            period_label="Q4",
        )

    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:r-old",
                start_time=base,
                home_team_id=TEAM_COMETS,
                state=final_state("mock:r-old", 88, 91),
            ),
            make_game(
                "mock:r-mid",
                start_time=base + timedelta(days=3),
                away_team_id=TEAM_COMETS,
                home_name="Calder Bay Pilots",
                away_name="Ashport Comets",
                state=final_state("mock:r-mid", 77, 80),
            ),
            make_game(
                "mock:r-new",
                start_time=base + timedelta(days=6),
                home_team_id=TEAM_COMETS,
                state=final_state("mock:r-new", 102, 95),
            ),
            # Final for a different team: excluded.
            make_game(
                "mock:r-other",
                start_time=base + timedelta(days=4),
                home_team_id=TEAM_STAGS,
                home_name="Rivermont Stags",
                away_name="Norvale Union",
                state=final_state("mock:r-other", 70, 64),
            ),
            # Still in progress: excluded.
            make_game(
                "mock:r-live",
                start_time=base + timedelta(days=7),
                home_team_id=TEAM_COMETS,
                state=domain.GameState(
                    game_id="mock:r-live",
                    phase=domain.GamePhase.IN_PROGRESS,
                    home_score=12,
                    away_score=15,
                    period=1,
                    period_label="Q1",
                ),
            ),
        ],
    )
    await session.flush()

    rows = await repository.results_for_team(session, TEAM_COMETS)
    assert [r.id for r in rows] == ["mock:r-new", "mock:r-mid", "mock:r-old"]

    rows = await repository.results_for_team(session, TEAM_COMETS, limit=2)
    assert [r.id for r in rows] == ["mock:r-new", "mock:r-mid"]


async def test_games_needing_live_poll_gating(seeded: AsyncSession) -> None:
    session = seeded
    now = utcnow()
    lead = timedelta(minutes=20)

    def in_progress(game_id: str) -> domain.GameState:
        return domain.GameState(
            game_id=game_id,
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=10,
            away_score=8,
            period=1,
            period_label="Q1",
        )

    await repository.upsert_games(
        session,
        [
            # Live game started an hour ago: included.
            make_game(
                "mock:lp-live",
                start_time=now - timedelta(hours=1),
                home_team_id=TEAM_COMETS,
                state=in_progress("mock:lp-live"),
            ),
            # Scheduled, tips off within the lead window: included.
            make_game(
                "mock:lp-soon",
                start_time=now + timedelta(minutes=10),
                home_team_id=TEAM_COMETS,
            ),
            # Scheduled, beyond the lead window: excluded.
            make_game(
                "mock:lp-future",
                start_time=now + timedelta(days=3),
                home_team_id=TEAM_COMETS,
            ),
            # Scheduled but older than max_age (never went live): excluded.
            make_game(
                "mock:lp-stale-sched",
                start_time=now - timedelta(hours=9),
                home_team_id=TEAM_COMETS,
            ),
            # Marked in_progress but older than max_age (stale feed): excluded.
            make_game(
                "mock:lp-stale-live",
                start_time=now - timedelta(hours=9),
                home_team_id=TEAM_COMETS,
                state=in_progress("mock:lp-stale-live"),
            ),
            # Finished yesterday: excluded.
            make_game(
                "mock:lp-final",
                start_time=now - timedelta(days=1),
                home_team_id=TEAM_COMETS,
                state=domain.GameState(
                    game_id="mock:lp-final",
                    phase=domain.GamePhase.FINAL,
                    home_score=99,
                    away_score=90,
                    period=4,
                    period_label="Q4",
                ),
            ),
        ],
    )
    await session.flush()

    rows = await repository.games_needing_live_poll(session, now, lead)
    assert [r.id for r in rows] == ["mock:lp-live", "mock:lp-soon"]


async def test_finals_missing_notification_filters_by_dedupe_and_recency(
    seeded: AsyncSession,
) -> None:
    session = seeded
    now = utcnow()

    def final(game_id: str) -> domain.GameState:
        return domain.GameState(
            game_id=game_id, phase=domain.GamePhase.FINAL,
            home_score=3, away_score=1, period=4, period_label="Final",
        )

    await repository.upsert_games(
        session,
        [
            # FINAL within lookback, no dedupe row -> returned.
            make_game("mock:fn-unsent", start_time=now - timedelta(hours=1),
                      home_team_id=TEAM_COMETS, state=final("mock:fn-unsent")),
            # FINAL within lookback but already notified -> excluded.
            make_game("mock:fn-sent", start_time=now - timedelta(hours=1),
                      home_team_id=TEAM_COMETS, state=final("mock:fn-sent")),
            # FINAL but older than the lookback -> excluded.
            make_game("mock:fn-old", start_time=now - timedelta(hours=24),
                      home_team_id=TEAM_COMETS, state=final("mock:fn-old")),
            # Still in progress -> excluded.
            make_game("mock:fn-live", start_time=now - timedelta(hours=1),
                      home_team_id=TEAM_COMETS,
                      state=domain.GameState(
                          game_id="mock:fn-live", phase=domain.GamePhase.IN_PROGRESS,
                          home_score=1, away_score=1, period=2, period_label="Q2",
                      )),
        ],
    )
    await repository.mark_notified(session, "mock:fn-sent:final")
    await session.flush()

    rows = await repository.finals_missing_notification(session, now, timedelta(hours=6))
    assert {r.id for r in rows} == {"mock:fn-unsent"}


async def test_event_finals_missing_notification_uses_state_updated_at(
    seeded: AsyncSession,
) -> None:
    session = seeded
    now = utcnow()

    def golf_final(event_id: str) -> domain.Event:
        return domain.Event(
            id=event_id,
            league_id=LEAGUE_ID,
            name="Harbor Open",
            start_time=now - timedelta(days=3),
            # FUTURE end_time (ESPN's scheduled final-round end) — must NOT
            # exclude a just-finalized event.
            end_time=now + timedelta(days=1),
            phase=domain.GamePhase.FINAL,
            leaderboard=(
                domain.LeaderRow(position=1, position_label="1", name="A", score="-5"),
            ),
        )

    await repository.upsert_events(
        session,
        [golf_final("ev:unsent"), golf_final("ev:sent"), golf_final("ev:old")],
    )
    await repository.mark_notified(session, "ev:sent:final")
    # Age out one event's "recently active" signal.
    old = await repository.get_event(session, "ev:old")
    assert old is not None
    old.state_updated_at = now - timedelta(hours=24)
    await session.flush()

    rows = await repository.event_finals_missing_notification(
        session, now, timedelta(hours=6)
    )
    # ev:unsent qualifies (recent state_updated_at, no dedupe, despite future
    # end_time); ev:sent is deduped; ev:old aged out.
    assert {r.id for r in rows} == {"ev:unsent"}


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def make_news(
    item_id: str,
    *,
    url: str,
    published_at: datetime | None,
    title: str = "Comets clinch a playoff berth",
) -> domain.NewsItem:
    return domain.NewsItem(
        id=item_id,
        team_id=TEAM_COMETS,
        title=title,
        url=url,
        source="Ashport Sports Wire",
        published_at=published_at,
        summary="Recap of the clincher.",
    )


async def test_upsert_news_dedupes_by_id(seeded: AsyncSession) -> None:
    session = seeded
    item = make_news(
        "a1b2c3d4e5f60718",
        url="https://news.example/ashport/clinch",
        published_at=utcnow(),
    )
    assert await repository.upsert_news(session, [item]) == 1
    # Same url -> same id -> not inserted again (even within one batch).
    assert await repository.upsert_news(session, [item, item]) == 0

    rows = await repository.list_news(session)
    assert len(rows) == 1
    assert rows[0].id == "a1b2c3d4e5f60718"


async def test_list_news_orders_published_desc_nulls_last(
    seeded: AsyncSession,
) -> None:
    session = seeded
    now = utcnow()
    await repository.upsert_news(
        session,
        [
            make_news(
                "older0000000000a",
                url="https://news.example/ashport/older",
                published_at=now - timedelta(days=2),
                title="Stags edge Comets in overtime",
            ),
            make_news(
                "nopub0000000000b",
                url="https://news.example/ashport/undated-one",
                published_at=None,
                title="Roster notes",
            ),
            make_news(
                "newer0000000000c",
                url="https://news.example/ashport/newer",
                published_at=now - timedelta(hours=1),
                title="Comets sign Jory Vance",
            ),
            make_news(
                "nopub0000000000d",
                url="https://news.example/ashport/undated-two",
                published_at=None,
                title="Practice report",
            ),
        ],
    )
    # Pin fetched_at so the NULL-published tiebreak is deterministic.
    row_b = await session.get(NewsORM, "nopub0000000000b")
    row_d = await session.get(NewsORM, "nopub0000000000d")
    assert row_b is not None and row_d is not None
    row_b.fetched_at = now - timedelta(minutes=30)
    row_d.fetched_at = now - timedelta(minutes=5)
    await session.flush()
    session.expire_all()

    rows = await repository.list_news(session)
    assert [r.id for r in rows] == [
        "newer0000000000c",   # newest published first
        "older0000000000a",
        "nopub0000000000d",   # NULL published last, newer fetched_at first
        "nopub0000000000b",
    ]

    rows = await repository.list_news(session, team_id=TEAM_STAGS)
    assert rows == []

    rows = await repository.list_news(session, limit=2)
    assert [r.id for r in rows] == ["newer0000000000c", "older0000000000a"]


# ---------------------------------------------------------------------------
# Notification ledger
# ---------------------------------------------------------------------------


async def test_was_notified_and_mark_notified(session: AsyncSession) -> None:
    key = "mock:g1:start"
    assert await repository.was_notified(session, key) is False
    await repository.mark_notified(session, key)
    await session.flush()
    assert await repository.was_notified(session, key) is True
    # Marking again is a harmless no-op.
    await repository.mark_notified(session, key)
    await session.flush()
    assert await repository.was_notified(session, key) is True
    assert await repository.was_notified(session, "mock:g1:final") is False


# ---------------------------------------------------------------------------
# Stuck-game recovery, pruning, and column clipping (review fixes)
# ---------------------------------------------------------------------------


async def test_upsert_games_final_state_heals_stuck_in_progress(
    seeded: AsyncSession,
) -> None:
    """A FINAL from schedule data is authoritative even over 'in_progress'.

    This is how a game that ended while the app was down gets its result
    on the next daily refresh instead of showing live forever.
    """
    start = utcnow() - timedelta(hours=20)
    await repository.upsert_games(
        seeded, [make_game("mock:stuck", start_time=start, home_team_id=TEAM_COMETS)]
    )
    await repository.apply_game_state(
        seeded,
        domain.GameState(
            game_id="mock:stuck",
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=55,
            away_score=51,
            period=3,
            period_label="Q3",
            clock="04:12",
        ),
    )

    final = domain.GameState(
        game_id="mock:stuck",
        phase=domain.GamePhase.FINAL,
        home_score=101,
        away_score=96,
        period=4,
        period_label="Q4",
    )
    await repository.upsert_games(
        seeded,
        [make_game("mock:stuck", start_time=start, home_team_id=TEAM_COMETS, state=final)],
    )

    row = await repository.get_game(seeded, "mock:stuck")
    assert row is not None
    assert row.phase == "final"
    assert (row.home_score, row.away_score) == (101, 96)


async def test_upsert_games_reinstates_postponed_fixture(seeded: AsyncSession) -> None:
    start = utcnow() + timedelta(days=2)
    postponed = domain.GameState(
        game_id="mock:ppd",
        phase=domain.GamePhase.POSTPONED,
        home_score=0,
        away_score=0,
    )
    await repository.upsert_games(
        seeded,
        [make_game("mock:ppd", start_time=start, home_team_id=TEAM_COMETS, state=postponed)],
    )

    rescheduled = domain.GameState(
        game_id="mock:ppd",
        phase=domain.GamePhase.SCHEDULED,
        home_score=0,
        away_score=0,
    )
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:ppd",
                start_time=start + timedelta(days=5),
                home_team_id=TEAM_COMETS,
                state=rescheduled,
            )
        ],
    )

    row = await repository.get_game(seeded, "mock:ppd")
    assert row is not None
    assert row.phase == "scheduled"
    # ...but a FINAL row is never reopened by schedule data.
    await repository.apply_game_state(
        seeded,
        domain.GameState(
            game_id="mock:ppd",
            phase=domain.GamePhase.FINAL,
            home_score=99,
            away_score=90,
            period=4,
            period_label="Q4",
        ),
    )
    await repository.upsert_games(
        seeded,
        [make_game("mock:ppd", start_time=start, home_team_id=TEAM_COMETS, state=rescheduled)],
    )
    row = await repository.get_game(seeded, "mock:ppd")
    assert row is not None
    assert row.phase == "final"


async def test_prune_stale_games(seeded: AsyncSession) -> None:
    now = utcnow()
    old_scheduled = make_game(
        "mock:ghost-sched", start_time=now - timedelta(days=4), home_team_id=TEAM_COMETS
    )
    old_live = make_game(
        "mock:ghost-live", start_time=now - timedelta(days=3), home_team_id=TEAM_COMETS
    )
    recent_scheduled = make_game(
        "mock:fresh-sched", start_time=now - timedelta(days=1), home_team_id=TEAM_COMETS
    )
    old_final = make_game(
        "mock:old-final",
        start_time=now - timedelta(days=10),
        home_team_id=TEAM_COMETS,
        state=domain.GameState(
            game_id="mock:old-final",
            phase=domain.GamePhase.FINAL,
            home_score=88,
            away_score=80,
            period=4,
            period_label="Q4",
        ),
    )
    await repository.upsert_games(
        seeded, [old_scheduled, old_live, recent_scheduled, old_final]
    )
    await repository.apply_game_state(
        seeded,
        domain.GameState(
            game_id="mock:ghost-live",
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=10,
            away_score=12,
            period=1,
            period_label="Q1",
        ),
    )
    await seeded.flush()

    deleted = await repository.prune_stale_games(seeded, now)

    assert deleted == 2
    assert await repository.get_game(seeded, "mock:ghost-sched") is None
    assert await repository.get_game(seeded, "mock:ghost-live") is None
    # History and upcoming/recent games are untouched.
    assert await repository.get_game(seeded, "mock:fresh-sched") is not None
    assert await repository.get_game(seeded, "mock:old-final") is not None


async def test_state_strings_clipped_to_column_widths(seeded: AsyncSession) -> None:
    """Postgres enforces VARCHAR lengths; oversized provider strings must
    truncate instead of aborting a whole refresh transaction."""
    start = utcnow()
    await repository.upsert_games(
        seeded, [make_game("mock:clip", start_time=start, home_team_id=TEAM_COMETS)]
    )
    await repository.apply_game_state(
        seeded,
        domain.GameState(
            game_id="mock:clip",
            phase=domain.GamePhase.IN_PROGRESS,
            home_score=1,
            away_score=2,
            period=1,
            period_label="An Implausibly Verbose Period Label From A Provider",
            clock="123:45:67.8901234567",
        ),
    )
    row = await repository.get_game(seeded, "mock:clip")
    assert row is not None
    assert len(row.period_label) <= 32
    assert row.clock is not None and len(row.clock) <= 16


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


async def test_save_standings_drops_team_ids_no_longer_followed(
    seeded: AsyncSession,
) -> None:
    """Provider in-process id maps can outlive a ``replace_followed``;
    stored rows must never tag teams that are no longer in the DB."""
    standings = domain.Standings(
        league_id=LEAGUE_ID,
        season="2026",
        rows=(
            domain.StandingRow(
                rank=1, team_name="Ashport Comets", wins=9, losses=1,
                team_id=TEAM_COMETS,
            ),
            domain.StandingRow(
                rank=2, team_name="Glimmerfen Owls", wins=7, losses=3,
                team_id="glimmerfen-owls",  # stale: not a followed team
            ),
            domain.StandingRow(
                rank=3, team_name="Bramblewick Foxes", wins=2, losses=8,
            ),
        ),
        fetched_at=utcnow(),
    )
    await repository.save_standings(seeded, standings)
    await seeded.flush()

    stored = await repository.get_standings(seeded, LEAGUE_ID)
    assert stored is not None
    by_name = {row["team_name"]: row for row in stored.rows}
    assert by_name["Ashport Comets"]["team_id"] == TEAM_COMETS
    assert by_name["Glimmerfen Owls"]["team_id"] is None
    assert by_name["Bramblewick Foxes"]["team_id"] is None


# ---------------------------------------------------------------------------
# Team locations (map view)
# ---------------------------------------------------------------------------


async def test_set_team_location_persists_and_lists(seeded: AsyncSession) -> None:
    """set_team_location caches venue+coords; list_teams_with_location finds it."""
    # Before resolution, no team carries coordinates.
    assert await repository.list_teams_with_location(seeded) == []

    await repository.set_team_location(
        seeded, TEAM_COMETS, "Ashport Fieldhouse", 51.5074, -0.1278
    )
    await seeded.flush()

    stored = await repository.get_team(seeded, TEAM_COMETS)
    assert stored is not None
    assert stored.home_venue == "Ashport Fieldhouse"
    assert stored.venue_lat == pytest.approx(51.5074)
    assert stored.venue_lon == pytest.approx(-0.1278)

    # Only the resolved team is listed for the map; the other is omitted.
    located = await repository.list_teams_with_location(seeded)
    assert [row.id for row in located] == [TEAM_COMETS]


async def test_set_team_location_unknown_team_is_noop(seeded: AsyncSession) -> None:
    """An unknown team id logs and does nothing rather than raising."""
    await repository.set_team_location(seeded, "no-such-team", "Nowhere", 1.0, 2.0)
    await seeded.flush()
    assert await repository.list_teams_with_location(seeded) == []


async def test_most_common_home_venue_picks_the_frequent_one(
    seeded: AsyncSession,
) -> None:
    """The team's most-frequent home-game venue wins; away games don't count."""
    now = utcnow()
    # Two home games at the regular ground, one at a neutral venue.
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:home-1",
                start_time=now + timedelta(days=1),
                home_team_id=TEAM_COMETS,
                venue="Ashport Fieldhouse",
            ),
            make_game(
                "mock:home-2",
                start_time=now + timedelta(days=3),
                home_team_id=TEAM_COMETS,
                venue="Ashport Fieldhouse",
            ),
            make_game(
                "mock:home-neutral",
                start_time=now + timedelta(days=5),
                home_team_id=TEAM_COMETS,
                venue="Harbor Neutral Ground",
            ),
            # An away game at a different venue must be ignored.
            make_game(
                "mock:away-1",
                start_time=now + timedelta(days=7),
                away_team_id=TEAM_COMETS,
                home_team_id=TEAM_STAGS,
                venue="Rivermont Coliseum",
            ),
        ],
    )
    await seeded.flush()

    venue = await repository.most_common_home_venue(seeded, TEAM_COMETS)
    assert venue == "Ashport Fieldhouse"


async def test_most_common_home_venue_none_without_home_games(
    seeded: AsyncSession,
) -> None:
    assert await repository.most_common_home_venue(seeded, TEAM_COMETS) is None
