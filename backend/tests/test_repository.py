"""Repository tests against a throwaway in-memory SQLite database.

The engine is built directly here (never via ``app.db``) so global
settings and the app engine stay untouched.  All fixture data is
fictional.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy import Delete, Select, Update, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from app.models import domain
from tests.db_engine import create_test_schema, make_test_engine
from app.models.orm import (
    Base,
    EventORM,
    GameORM,
    LeagueORM,
    NewsORM,
    NotificationPrefORM,
    PlayerFollowORM,
    PlayerORM,
    StadiumORM,
    StandingsORM,
    TeamCompetitionORM,
    TeamORM,
)
from app.providers import espn_catalog
from app.services import repository
from app.timeutil import ensure_utc, utcnow

LEAGUE_ID = "pinnacle-basketball"
TEAM_COMETS = "ashport-comets"
TEAM_STAGS = "rivermont-stags"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = make_test_engine()
    await create_test_schema(engine)
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
    fight_result: domain.FightResult | None = None,
    broadcasts: tuple[str, ...] = (),
    league_id: str = LEAGUE_ID,
) -> domain.Game:
    return domain.Game(
        id=game_id,
        league_id=league_id,
        home_name=home_name,
        away_name=away_name,
        start_time=start_time,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_abbreviation="ASH",
        away_abbreviation="RIV",
        venue=venue,
        fight_result=fight_result,
        broadcasts=broadcasts,
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
    await repository.upsert_games(session, [make_game("mock:g-sched", start_time=start)])
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


async def test_upsert_games_stores_and_never_wipes_broadcasts(seeded: AsyncSession) -> None:
    """Broadcast networks persist, and an empty incoming list can't erase them.

    Broadcasts follow the crest/color rule: only the scoreboard/schedule
    pass carries them, so a refresh that doesn't (a state-only pass, a
    feed that omits the arrays) must leave the stored networks alone —
    while a pass that does know them overwrites the whole list.
    """
    session = seeded
    start = datetime(2026, 6, 26, 23, 0, tzinfo=timezone.utc)
    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:g-tv",
                start_time=start,
                home_team_id=TEAM_COMETS,
                broadcasts=("Pinnacle Sports Network", "Ashport Cable 9"),
            )
        ],
    )
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:g-tv")
    assert row is not None
    assert row.broadcasts == ["Pinnacle Sports Network", "Ashport Cable 9"]

    # A refresh with no broadcast data leaves the stored list alone.
    await repository.upsert_games(session, [make_game("mock:g-tv", start_time=start)])
    await session.flush()
    session.expire_all()
    row = await repository.get_game(session, "mock:g-tv")
    assert row is not None
    assert row.broadcasts == ["Pinnacle Sports Network", "Ashport Cable 9"]

    # A refresh that knows the networks replaces the list wholesale.
    await repository.upsert_games(
        session,
        [make_game("mock:g-tv", start_time=start, broadcasts=("Pinnacle Sports Network",))],
    )
    await session.flush()
    session.expire_all()
    row = await repository.get_game(session, "mock:g-tv")
    assert row is not None
    assert row.broadcasts == ["Pinnacle Sports Network"]


async def test_upsert_games_stores_and_never_wipes_a_fight_result(
    seeded: AsyncSession,
) -> None:
    """The MMA method of victory persists, and a later refresh can't erase it.

    Only the schedule refresh enriches bouts with a method (the live
    scoreboard has no team scope), so most refreshes of a finished bout
    carry ``fight_result=None`` — which, like an omitted crest or color,
    leaves the stored value alone.  Provider strings are clipped to their
    column widths, which only postgres enforces.
    """
    session = seeded
    start = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)
    bout = make_game(
        "mock:bout-1",
        start_time=start,
        home_team_id=TEAM_COMETS,
        fight_result=domain.FightResult(
            method=domain.FightMethod.SUBMISSION,
            detail="Rear-Naked Choke",
            round=2,
            clock="4:21",
        ),
    )
    await repository.upsert_games(session, [bout])
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:bout-1")
    assert row is not None
    assert row.fight_method == "submission"
    assert row.fight_detail == "Rear-Naked Choke"
    assert row.fight_round == 2
    assert row.fight_clock == "4:21"

    # A refresh that doesn't know the method leaves the stored one alone.
    await repository.upsert_games(session, [make_game("mock:bout-1", start_time=start)])
    await session.flush()
    session.expire_all()
    row = await repository.get_game(session, "mock:bout-1")
    assert row is not None
    assert row.fight_method == "submission"
    assert row.fight_detail == "Rear-Naked Choke"

    # A refresh that does know it wins, oversized flavour text and all.
    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:bout-1",
                start_time=start,
                fight_result=domain.FightResult(
                    method=domain.FightMethod.KO,
                    detail="An Implausibly Verbose Finish Description From Some Provider's Feed",
                    round=1,
                    clock="00:58.7654321098765",
                ),
            )
        ],
    )
    await session.flush()
    session.expire_all()
    row = await repository.get_game(session, "mock:bout-1")
    assert row is not None
    assert row.fight_method == "ko"
    assert row.fight_detail == "An Implausibly Verbose Finish Description From Some Provider's F"
    assert row.fight_round == 1
    assert row.fight_clock == "00:58.7654321098"


async def test_upsert_games_merges_a_thinner_fight_result_per_field(
    seeded: AsyncSession,
) -> None:
    """A thinner incoming result must not erase a richer stored one.

    The round/time/flavour come from a separate core-API call that can
    fail on its own, leaving the schedule refresh with only what the
    scoreboard spelled out ("Final - Submission", no time).  Merging the
    four columns as a block would let that one transient failure wipe a
    stoppage time already on record, so each field merges on its own.
    """
    session = seeded
    start = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)
    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:bout-2",
                start_time=start,
                home_team_id=TEAM_COMETS,
                fight_result=domain.FightResult(
                    method=domain.FightMethod.SUBMISSION,
                    detail="Rear-Naked Choke",
                    round=2,
                    clock="4:21",
                ),
            )
        ],
    )
    await session.flush()
    session.expire_all()

    # Next day's refresh: the core call failed, so only the scoreboard's
    # own text made it through — the method and nothing else.
    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:bout-2",
                start_time=start,
                fight_result=domain.FightResult(method=domain.FightMethod.SUBMISSION),
            )
        ],
    )
    await session.flush()
    session.expire_all()

    row = await repository.get_game(session, "mock:bout-2")
    assert row is not None
    assert row.fight_method == "submission"
    assert row.fight_detail == "Rear-Naked Choke"
    assert row.fight_round == 2
    assert row.fight_clock == "4:21"


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

    assert (
        await repository.apply_game_state(
            session,
            domain.GameState(
                game_id="mock:does-not-exist",
                phase=domain.GamePhase.IN_PROGRESS,
                home_score=0,
                away_score=0,
            ),
        )
        is None
    )


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


async def test_events_between_matches_overlap_not_just_start(seeded: AsyncSession) -> None:
    """Events span days: anything OVERLAPPING the window belongs in it.

    Unlike a game (a point in time), a tournament that began before the
    window opened is still on the calendar while it runs — and a null
    ``end_time`` is a single-day event, so it is included only when its
    own start falls inside.
    """
    session = seeded
    base = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    def make_event(event_id: str, *, start: datetime, end: datetime | None = None) -> domain.Event:
        return domain.Event(
            id=event_id,
            league_id=LEAGUE_ID,
            name="Ashport Open",
            start_time=start,
            end_time=end,
            phase=domain.GamePhase.SCHEDULED,
        )

    await repository.upsert_events(
        session,
        [
            # Finished before the window opened.
            make_event("ev:before", start=base - timedelta(days=9), end=base - timedelta(hours=1)),
            # Ends exactly as the window opens — the start bound is inclusive.
            make_event("ev:ends-at-open", start=base - timedelta(days=5), end=base),
            # Began before the window, still running inside it.
            make_event(
                "ev:straddles", start=base - timedelta(days=4), end=base + timedelta(days=1)
            ),
            # Wholly inside.
            make_event("ev:inside", start=base + timedelta(days=1), end=base + timedelta(days=4)),
            # No end_time: a single day, inside.
            make_event("ev:single-day", start=base + timedelta(days=2)),
            # No end_time: a single day that has already passed.
            make_event("ev:single-day-past", start=base - timedelta(days=1)),
            # Starts exactly at the (exclusive) window end.
            make_event("ev:at-close", start=base + timedelta(days=7), end=base + timedelta(days=9)),
        ],
    )
    await session.flush()

    rows = await repository.events_between(session, base, base + timedelta(days=7))

    # Ordered by start_time, earliest first.
    assert [r.id for r in rows] == [
        "ev:ends-at-open",
        "ev:straddles",
        "ev:inside",
        "ev:single-day",
    ]


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
    assert (
        await repository.upcoming_games_for_leagues(session, [], now, now + timedelta(days=3)) == []
    )
    assert (
        await repository.upcoming_games_for_leagues(
            session, ["no-such-league"], now, now + timedelta(days=3)
        )
        == []
    )


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
            game_id=game_id,
            phase=domain.GamePhase.FINAL,
            home_score=3,
            away_score=1,
            period=4,
            period_label="Final",
        )

    await repository.upsert_games(
        session,
        [
            # FINAL within lookback, no dedupe row -> returned.
            make_game(
                "mock:fn-unsent",
                start_time=now - timedelta(hours=1),
                home_team_id=TEAM_COMETS,
                state=final("mock:fn-unsent"),
            ),
            # FINAL within lookback but already notified -> excluded.
            make_game(
                "mock:fn-sent",
                start_time=now - timedelta(hours=1),
                home_team_id=TEAM_COMETS,
                state=final("mock:fn-sent"),
            ),
            # FINAL but older than the lookback -> excluded.
            make_game(
                "mock:fn-old",
                start_time=now - timedelta(hours=24),
                home_team_id=TEAM_COMETS,
                state=final("mock:fn-old"),
            ),
            # Still in progress -> excluded.
            make_game(
                "mock:fn-live",
                start_time=now - timedelta(hours=1),
                home_team_id=TEAM_COMETS,
                state=domain.GameState(
                    game_id="mock:fn-live",
                    phase=domain.GamePhase.IN_PROGRESS,
                    home_score=1,
                    away_score=1,
                    period=2,
                    period_label="Q2",
                ),
            ),
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
            leaderboard=(domain.LeaderRow(position=1, position_label="1", name="A", score="-5"),),
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

    rows = await repository.event_finals_missing_notification(session, now, timedelta(hours=6))
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
        "newer0000000000c",  # newest published first
        "older0000000000a",
        "nopub0000000000d",  # NULL published last, newer fetched_at first
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
    await repository.upsert_games(seeded, [old_scheduled, old_live, recent_scheduled, old_final])
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


def _make_event(
    event_id: str,
    *,
    phase: domain.GamePhase,
    start_time: datetime,
    end_time: datetime | None = None,
) -> domain.Event:
    return domain.Event(
        id=event_id,
        league_id=LEAGUE_ID,
        name=event_id.replace("-", " ").title(),
        start_time=start_time,
        end_time=end_time,
        phase=phase,
    )


async def test_prune_stale_games_sweeps_stuck_events_too(seeded: AsyncSession) -> None:
    """An in_progress event whose weekend has left every fetch window would
    otherwise be polled by events_tick forever; the games rule is mirrored on
    coalesce(end_time, start_time)."""
    now = utcnow()
    await repository.upsert_events(
        seeded,
        [
            # Ended (per its own scheduled end) three days ago, still
            # in_progress: unrepairable — pruned.
            _make_event(
                "mock:ghost-event",
                phase=domain.GamePhase.IN_PROGRESS,
                start_time=now - timedelta(days=5),
                end_time=now - timedelta(days=3),
            ),
            # No end_time at all: start_time is the coalesce fallback — pruned.
            _make_event(
                "mock:ghost-dateless",
                phase=domain.GamePhase.IN_PROGRESS,
                start_time=now - timedelta(days=3),
            ),
            # Mid-weekend right now: end_time still ahead — kept.
            _make_event(
                "mock:live-event",
                phase=domain.GamePhase.IN_PROGRESS,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
            ),
            # Finished events are history, however old — kept.
            _make_event(
                "mock:old-final-event",
                phase=domain.GamePhase.FINAL,
                start_time=now - timedelta(days=10),
                end_time=now - timedelta(days=9),
            ),
        ],
    )
    await seeded.flush()

    deleted = await repository.prune_stale_games(seeded, now)

    assert deleted == 2
    assert await repository.get_event(seeded, "mock:ghost-event") is None
    assert await repository.get_event(seeded, "mock:ghost-dateless") is None
    assert await repository.get_event(seeded, "mock:live-event") is not None
    assert await repository.get_event(seeded, "mock:old-final-event") is not None


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
                rank=1,
                team_name="Ashport Comets",
                wins=9,
                losses=1,
                team_id=TEAM_COMETS,
            ),
            domain.StandingRow(
                rank=2,
                team_name="Glimmerfen Owls",
                wins=7,
                losses=3,
                team_id="glimmerfen-owls",  # stale: not a followed team
            ),
            domain.StandingRow(
                rank=3,
                team_name="Bramblewick Foxes",
                wins=2,
                losses=8,
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
# Replacing the followed set
# ---------------------------------------------------------------------------


CUP_ID = "meridian-cup"
TOUR_ID = "fairway-tour"
PREMIER_ID = "pinnacle-premier"
GOLFER_CALLOWAY = "fairway-tour-ines-calloway"
GOLFER_FINCH = "fairway-tour-orla-finch"

BASKETBALL = domain.League(
    id=LEAGUE_ID,
    sport=domain.Sport.BASKETBALL,
    name="Pinnacle Basketball League",
    provider="mock",
    provider_key="mock-basketball",
)
CUP = domain.League(
    id=CUP_ID,
    sport=domain.Sport.BASKETBALL,
    name="Meridian Cup",
    provider="mock",
    provider_key="mock-cup",
    follow_all=True,
)
TOUR = domain.League(
    id=TOUR_ID,
    sport=domain.Sport.GOLF,
    name="Fairway Tour",
    provider="mock",
    provider_key="mock-golf",
)
COMETS = domain.Team(
    id=TEAM_COMETS,
    league_id=LEAGUE_ID,
    name="Ashport Comets",
    abbreviation="ASH",
    provider_key="ashport-comets",
    color="#f59e0b",
)
STAGS = domain.Team(
    id=TEAM_STAGS,
    league_id=LEAGUE_ID,
    name="Rivermont Stags",
    abbreviation="RIV",
    provider_key="rivermont-stags",
    color="#10b981",
)
CALLOWAY = domain.Team(
    id=GOLFER_CALLOWAY,
    league_id=TOUR_ID,
    name="Ines Calloway",
    abbreviation="CAL",
    provider_key="4410",
)
FINCH = domain.Team(
    id=GOLFER_FINCH,
    league_id=TOUR_ID,
    name="Orla Finch",
    abbreviation="FIN",
    provider_key="4411",
)
# The Comets also enter the cup: a ``team_competitions`` triple.
COMETS_IN_CUP = (TEAM_COMETS, CUP_ID, "ashport-comets")

# ``updated_at`` is bumped by any write to a game/event row, including one
# that puts a value back; ignored where a test edits a row and expects the
# code under test to restore it.
_UPDATED_AT = frozenset({"updated_at"})


@pytest.fixture
async def followed(session: AsyncSession) -> AsyncSession:
    """A consistent database holding one of every row a follow can own.

    Three leagues: basketball followed through two teams, a cup followed
    in whole (``follow_all``, no teams of its own) and a golf tour
    followed through two athletes.  Committed, so every test starts from
    stored rows rather than pending ones.
    """
    now = utcnow()
    for league in (BASKETBALL, CUP, TOUR):
        await repository.upsert_league(session, league)
    await session.flush()
    for team in (COMETS, STAGS, CALLOWAY, FINCH):
        await repository.upsert_team(session, team)
    await session.flush()
    team_id, league_id, provider_key = COMETS_IN_CUP
    session.add(TeamCompetitionORM(team_id=team_id, league_id=league_id, provider_key=provider_key))

    # Enrichment and rosters: the expensive, rate-limited part of a follow.
    await repository.set_team_location(
        session, TEAM_COMETS, "Ashport Fieldhouse", 51.5074, -0.1278, capacity=18000
    )
    await repository.set_team_info(
        session,
        TEAM_COMETS,
        description="Ashport Comets are a fictional basketball club.",
        founded_year=1974,
        description_source="thesportsdb",
    )
    await repository.replace_roster(
        session,
        domain.Roster(
            team_id=TEAM_COMETS,
            players=(
                domain.Player(id="espn:9001", team_id=TEAM_COMETS, name="Juno Hollyfield"),
                domain.Player(id="espn:9002", team_id=TEAM_COMETS, name="Sable Quillan"),
            ),
            fetched_at=now,
        ),
    )
    await repository.replace_roster(
        session,
        domain.Roster(
            team_id=TEAM_STAGS,
            players=(domain.Player(id="espn:7001", team_id=TEAM_STAGS, name="Bram Tolliver"),),
            fetched_at=now,
        ),
    )
    await repository.follow_player(
        session,
        athlete_id="9001",
        name="Juno Hollyfield",
        team_id=TEAM_COMETS,
        league_id=LEAGUE_ID,
        last_notified_status="active",
    )
    await repository.follow_player(
        session, athlete_id="7001", name="Bram Tolliver", team_id=TEAM_STAGS, league_id=LEAGUE_ID
    )

    await repository.upsert_games(
        session,
        [
            # A finished derby between two followed teams, already announced.
            make_game(
                "mock:derby",
                start_time=now - timedelta(hours=2),
                home_team_id=TEAM_COMETS,
                away_team_id=TEAM_STAGS,
                state=domain.GameState(
                    game_id="mock:derby",
                    phase=domain.GamePhase.FINAL,
                    home_score=88,
                    away_score=81,
                ),
            ),
            # A per-team fetch tags one side only.
            make_game(
                "mock:comets-away",
                start_time=now + timedelta(days=1),
                away_team_id=TEAM_COMETS,
                home_name="Foxglove Sentinels",
                away_name="Ashport Comets",
            ),
            make_game(
                "mock:stags-home",
                start_time=now + timedelta(days=2),
                home_team_id=TEAM_STAGS,
                home_name="Rivermont Stags",
                away_name="Foxglove Sentinels",
            ),
            # A whole-competition fetch tags neither; the Comets' own cup
            # tie arrived through their per-team fetch as well.
            make_game(
                "mock:cup-final",
                league_id=CUP_ID,
                start_time=now + timedelta(days=3),
                home_name="Glimmerfen Owls",
                away_name="Bramblewick Foxes",
            ),
            make_game(
                "mock:cup-comets",
                league_id=CUP_ID,
                start_time=now + timedelta(days=4),
                home_team_id=TEAM_COMETS,
                away_name="Glimmerfen Owls",
            ),
        ],
    )
    await repository.mark_notified(session, "mock:derby:final")

    await repository.upsert_events(
        session,
        [
            domain.Event(
                id="mock:open",
                league_id=TOUR_ID,
                name="Fairway Open",
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=2),
                phase=domain.GamePhase.IN_PROGRESS,
                round_label="Round 2",
                leaderboard=(
                    domain.LeaderRow(1, "1", "Ines Calloway", "-9", "thru 14", GOLFER_CALLOWAY),
                    domain.LeaderRow(2, "2", "Orla Finch", "-7", "thru 15", GOLFER_FINCH),
                    domain.LeaderRow(3, "3", "Wren Abernathy", "-6", "F"),
                ),
            )
        ],
    )

    table = domain.Standings(
        league_id=LEAGUE_ID,
        season="2025-26",
        rows=(
            domain.StandingRow(
                rank=1, team_name="Ashport Comets", wins=21, losses=9, team_id=TEAM_COMETS
            ),
            domain.StandingRow(
                rank=2, team_name="Rivermont Stags", wins=18, losses=12, team_id=TEAM_STAGS
            ),
            domain.StandingRow(rank=3, team_name="Foxglove Sentinels", wins=11, losses=19),
        ),
        fetched_at=now,
    )
    await repository.save_standings(session, table)
    await repository.save_standings_archive(session, table)

    await repository.upsert_news(
        session,
        [
            domain.NewsItem(
                id="news-comets",
                team_id=TEAM_COMETS,
                title="Comets clinch a playoff berth",
                url="https://news.example/ashport/clinch",
                source="Ashport Sports Wire",
                published_at=now,
            ),
            domain.NewsItem(
                id="news-stags",
                team_id=TEAM_STAGS,
                title="Stags extend their home streak",
                url="https://news.example/rivermont/streak",
                source="Rivermont Ledger",
                published_at=now,
            ),
            # League-level: what a whole-competition follow gets instead.
            domain.NewsItem(
                id="news-cup",
                team_id=None,
                league_id=CUP_ID,
                title="Meridian Cup draw announced",
                url="https://news.example/meridian-cup/draw",
                source="Cup Dispatch",
                published_at=now,
            ),
        ],
    )

    await repository.upsert_notification_pref(session, "global", events={"intermission": False})
    await repository.upsert_notification_pref(
        session, f"team:{TEAM_COMETS}", muted=True, events={"final": False}
    )
    await repository.upsert_notification_pref(
        session, f"team:{TEAM_STAGS}", events={"game_start": False}
    )
    await repository.upsert_notification_pref(
        session, f"league:{CUP_ID}", events={"game_start": True, "period_start": False}
    )
    await repository.upsert_notification_pref(session, f"league:{TOUR_ID}", muted=True)

    # Rows no follow keys: no save and no sweep may ever touch these.
    await repository.upsert_stadium(
        session,
        "mock:ashport-comets",
        "Ashport Comets",
        venue="Ashport Fieldhouse",
        lat=51.5074,
        lon=-0.1278,
    )
    await repository.set_meta(session, "onboarded", "1")
    await session.commit()
    return session


async def _snapshot(
    session: AsyncSession, *, ignore: frozenset[str] = frozenset()
) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, as stored.

    Plain column selects bypass the session's identity map, so this is
    what was actually written: an in-place edit of a JSON column, which
    SQLAlchemy never flushes, shows up here as the old value.
    """
    snapshot: dict[str, list[tuple[Any, ...]]] = {}
    for table in Base.metadata.sorted_tables:
        columns = [column for column in table.columns if column.name not in ignore]
        result = await session.execute(select(*columns).order_by(*table.primary_key.columns))
        snapshot[table.name] = [tuple(row) for row in result]
    return snapshot


async def _stored(session: AsyncSession, column: InstrumentedAttribute[Any]) -> set[Any]:
    """One column's distinct values, read from the database (not the identity map)."""
    return set((await session.execute(select(column))).scalars().all())


async def _sides(session: AsyncSession, game_id: str) -> tuple[str | None, str | None]:
    row = (
        await session.execute(
            select(GameORM.home_team_id, GameORM.away_team_id).where(GameORM.id == game_id)
        )
    ).one()
    return (row.home_team_id, row.away_team_id)


async def test_replace_followed_keeps_rows_of_kept_follows(followed: AsyncSession) -> None:
    """Dropping some follows must cost the kept ones nothing.

    The Comets and the cup stay; the Stags, both golfers and the whole
    tour go.  Everything the Comets had accumulated survives the save —
    above all what the next refresh cannot simply fetch back: the
    geocoded venue and club history (rate-limited, multi-source lookups),
    the season archive, the user's own alert settings and player follow.
    So does the dedupe ledger, without which a game that outlives the
    save is announced a second time.
    """
    session = followed
    await repository.replace_followed(session, [BASKETBALL, CUP], [COMETS], [COMETS_IN_CUP])
    await session.commit()

    assert await _stored(session, LeagueORM.id) == {LEAGUE_ID, CUP_ID}
    assert await _stored(session, TeamORM.id) == {TEAM_COMETS}
    assert (await _snapshot(session))["team_competitions"] == [COMETS_IN_CUP]

    session.expire_all()
    comets = await repository.get_team(session, TEAM_COMETS)
    assert comets is not None
    assert comets.home_venue == "Ashport Fieldhouse"
    assert comets.venue_lat == pytest.approx(51.5074)
    assert comets.venue_capacity == 18000
    assert comets.description == "Ashport Comets are a fictional basketball club."
    assert comets.founded_year == 1974
    assert comets.roster_updated_at is not None

    assert await _stored(session, PlayerORM.id) == {"espn:9001", "espn:9002"}
    assert await repository.get_standings_archive(session, LEAGUE_ID, "2026") is not None
    follows = await repository.list_player_follows(session)
    assert [(f.athlete_id, f.team_id, f.last_notified_status) for f in follows] == [
        ("9001", TEAM_COMETS, "active")
    ]
    prefs = await repository.prefs_by_scope(session)
    assert set(prefs) == {"global", f"team:{TEAM_COMETS}", f"league:{CUP_ID}"}
    assert prefs[f"team:{TEAM_COMETS}"].muted is True
    assert prefs[f"team:{TEAM_COMETS}"].events == {"final": False}
    assert prefs["global"].events == {"intermission": False}
    assert await repository.was_notified(session, "mock:derby:final")

    # ...while what only a removed follow backed is gone with it.
    assert await _stored(session, NewsORM.id) == {"news-comets", "news-cup"}
    assert await _stored(session, EventORM.id) == set()
    # Tables no follow keys are not the save's business.
    assert await repository.get_stadium(session, "mock:ashport-comets") is not None
    assert await repository.get_meta(session, "onboarded") == "1"


async def test_replace_followed_identical_resave_changes_nothing(followed: AsyncSession) -> None:
    """Saving the set that is already stored is a no-op, column for column.

    The wizard's manage mode posts the whole selection back, so "nothing
    changed" is its most common save — and the one that used to wipe and
    re-fetch everything.
    """
    session = followed
    before = await _snapshot(session)

    await repository.replace_followed(
        session, [BASKETBALL, CUP, TOUR], [COMETS, STAGS, CALLOWAY, FINCH], [COMETS_IN_CUP]
    )
    await session.commit()

    assert await _snapshot(session) == before


async def test_replace_followed_nulls_removed_side_and_drops_unbacked_games(
    followed: AsyncSession,
) -> None:
    """A game stays while a followed side or a whole-competition league backs it.

    Results are read from stored finals only and a refresh reaches back a
    week, so a kept team's game deleted here would be gone for good.  A
    game against a removed team therefore keeps its row and loses just
    that team's id; only a game nobody follows any more is deleted.
    """
    session = followed
    now = utcnow()
    await repository.upsert_games(
        session,
        [
            # The Stags' cup tie: their id goes, the cup still backs the game.
            make_game(
                "mock:cup-stags",
                league_id=CUP_ID,
                start_time=now + timedelta(days=5),
                away_team_id=TEAM_STAGS,
            ),
            # A kept team's fixture stored under a league that leaves the
            # set (which is how a sibling competition's games are stored).
            make_game(
                "mock:tour-proam",
                league_id=TOUR_ID,
                start_time=now + timedelta(days=6),
                home_team_id=TEAM_COMETS,
            ),
        ],
    )
    await session.commit()

    await repository.replace_followed(session, [BASKETBALL, CUP], [COMETS], [COMETS_IN_CUP])
    await session.commit()

    # Gone: the Stags-only fixture (its league is not followed in whole)
    # and the one whose league left the set.
    assert await _stored(session, GameORM.id) == {
        "mock:derby",
        "mock:comets-away",
        "mock:cup-final",
        "mock:cup-comets",
        "mock:cup-stags",
    }
    assert await _sides(session, "mock:derby") == (TEAM_COMETS, None)
    assert await _sides(session, "mock:comets-away") == (None, TEAM_COMETS)
    assert await _sides(session, "mock:cup-comets") == (TEAM_COMETS, None)
    assert await _sides(session, "mock:cup-stags") == (None, None)

    session.expire_all()
    derby = await repository.get_game(session, "mock:derby")
    assert derby is not None
    assert (derby.phase, derby.home_score, derby.away_score) == ("final", 88, 81)
    assert derby.away_name == "Rivermont Stags"
    # Its FINAL was announced before the save; the surviving row must not
    # look like an unsent one to the resend query.
    assert await repository.finals_missing_notification(session, utcnow(), timedelta(hours=6)) == []


async def test_replace_followed_clears_removed_tags(followed: AsyncSession) -> None:
    """A kept league's table and leaderboard stay; only a removed follow's tag goes.

    The tags are the "this row is one of yours" marker — the highlight in
    a table, and on a leaderboard who a finish notification is about — so
    one left behind keeps pointing at a team that no longer exists.  The
    rows are JSON, read back here from the database: an edit made in
    place would never have been written.
    """
    session = followed
    rows_before = (await session.execute(select(StandingsORM.rows))).scalar_one()
    board_before, stamped_before = (
        await session.execute(select(EventORM.leaderboard, EventORM.state_updated_at))
    ).one()

    await repository.replace_followed(
        session, [BASKETBALL, CUP, TOUR], [COMETS, CALLOWAY], [COMETS_IN_CUP]
    )
    await session.commit()

    assert await _stored(session, StandingsORM.league_id) == {LEAGUE_ID}
    assert await _stored(session, EventORM.id) == {"mock:open"}

    rows_after = (await session.execute(select(StandingsORM.rows))).scalar_one()
    assert [row["team_id"] for row in rows_after] == [TEAM_COMETS, None, None]
    assert rows_after == [
        {**row, "team_id": None} if row["team_id"] == TEAM_STAGS else row for row in rows_before
    ]

    board_after, stamped_after = (
        await session.execute(select(EventORM.leaderboard, EventORM.state_updated_at))
    ).one()
    assert [row["player_id"] for row in board_after] == [GOLFER_CALLOWAY, None, None]
    assert board_after == [
        {**row, "player_id": None} if row["player_id"] == GOLFER_FINCH else row
        for row in board_before
    ]
    # Untagging is not news about the event: a fresh stamp would put a
    # finished one back inside the FINAL-resend window.
    assert stamped_after == stamped_before


async def test_replace_followed_follow_all_flips(followed: AsyncSession) -> None:
    """Switching a league between whole-competition and picked teams keeps
    exactly what the new mode still backs."""
    session = followed

    # Off: the cup is now followed only through the Comets.  The fixtures
    # and articles it held as a whole competition have no follow left;
    # the Comets' own tie does.
    await repository.replace_followed(
        session,
        [BASKETBALL, replace(CUP, follow_all=False), TOUR],
        [COMETS, STAGS, CALLOWAY, FINCH],
        [COMETS_IN_CUP],
    )
    await session.commit()

    assert await _stored(session, GameORM.id) == {
        "mock:derby",
        "mock:comets-away",
        "mock:stags-home",
        "mock:cup-comets",
    }
    assert await _stored(session, NewsORM.id) == {"news-comets", "news-stags"}

    # On: basketball becomes a whole-competition follow, which drops its
    # team picks (the wizard does that).  The league backs their games
    # now, so every one stays — minus the team ids.
    await repository.replace_followed(session, [replace(BASKETBALL, follow_all=True)], [])
    await session.commit()

    assert await _stored(session, GameORM.id) == {
        "mock:derby",
        "mock:comets-away",
        "mock:stags-home",
    }
    assert await _sides(session, "mock:derby") == (None, None)
    assert await _stored(session, TeamORM.id) == set()
    assert await _stored(session, NewsORM.id) == set()


async def test_replace_followed_repoints_team_before_deleting_old_league(
    followed: AsyncSession,
) -> None:
    """A kept team id can arrive under a new league; the old league must go last.

    Until the team row is upserted it still references its old league, so
    deleting unfollowed leagues first violates that FK (an IntegrityError:
    a 500 from the wizard).  And the row has to be updated, not replaced —
    a delete-and-insert would cost the team its enrichment.
    """
    session = followed
    premier = replace(BASKETBALL, id=PREMIER_ID, name="Pinnacle Premier Basketball")
    await repository.replace_followed(session, [premier], [replace(COMETS, league_id=PREMIER_ID)])
    await session.commit()

    assert await _stored(session, LeagueORM.id) == {PREMIER_ID}
    assert await _stored(session, TeamORM.id) == {TEAM_COMETS}
    session.expire_all()
    comets = await repository.get_team(session, TEAM_COMETS)
    assert comets is not None
    assert comets.league_id == PREMIER_ID
    assert comets.venue_lat == pytest.approx(51.5074)
    assert comets.description == "Ashport Comets are a fictional basketball club."
    assert await _stored(session, PlayerORM.id) == {"espn:9001", "espn:9002"}


async def test_replace_followed_deletes_removed_leagues_children(followed: AsyncSession) -> None:
    """Unfollowing a league must clear every row that references it.

    ``standings_archive`` is written by every standings refresh, so it is
    populated minutes after the first setup; leaving it behind once made
    ``DELETE FROM leagues`` violate its FK on postgres (a 500 from the
    wizard, permanently) and orphan stale tables on sqlite.  The test
    engine enables ``PRAGMA foreign_keys=ON``, so a forgotten child table
    fails here rather than only in production.  The cup is kept, and its
    archive must be too: a past season cannot always be fetched again.
    """
    session = followed
    await repository.save_standings_archive(
        session,
        domain.Standings(
            league_id=CUP_ID,
            season="2025-26",
            rows=(domain.StandingRow(rank=1, team_name="Glimmerfen Owls", wins=5, losses=0),),
            fetched_at=utcnow(),
        ),
    )
    await session.commit()
    assert await repository.get_standings_archive(session, LEAGUE_ID, "2026") is not None

    await repository.replace_followed(session, [CUP], [])
    await session.commit()
    session.expire_all()

    assert await repository.get_standings_archive(session, LEAGUE_ID, "2026") is None
    assert await repository.get_league(session, LEAGUE_ID) is None
    assert await repository.get_league(session, TOUR_ID) is None
    stored = await _snapshot(session)
    for name in ("standings", "events", "teams", "players"):
        assert stored[name] == [], name
    assert await _stored(session, GameORM.league_id) == {CUP_ID}
    assert await repository.get_standings_archive(session, CUP_ID, "2026") is not None


async def test_replace_followed_follow_all_only_set_keeps_league_level_rows(
    followed: AsyncSession,
) -> None:
    """With no team followed at all, rows keyed by a league alone must survive.

    The team id list is then empty, and an empty NOT IN matches every
    row, NULLs included — where a non-empty one skips them.  Unguarded,
    "team not in the set" would take each untagged cup fixture and
    league-level article with it: all a whole-competition follow has.
    """
    session = followed
    await repository.replace_followed(session, [CUP], [])
    await session.commit()

    assert await _stored(session, GameORM.id) == {"mock:cup-final", "mock:cup-comets"}
    assert await _sides(session, "mock:cup-comets") == (None, None)
    assert await _stored(session, NewsORM.id) == {"news-cup"}
    assert await _stored(session, NotificationPrefORM.scope) == {"global", f"league:{CUP_ID}"}
    assert await _stored(session, PlayerFollowORM.athlete_id) == set()
    assert (await _snapshot(session))["team_competitions"] == []


async def test_replace_followed_with_nothing_followed_spares_unkeyed_tables(
    session: AsyncSession,
) -> None:
    """Empty id sets are still valid SQL, and never reach past follow data.

    A blank database first, then one holding only rows no follow keys —
    the dedupe ledger, the ``global`` pref, a cached stadium, app meta.
    """
    await repository.replace_followed(session, [], [])
    await session.commit()
    assert not any((await _snapshot(session)).values())

    await repository.mark_notified(session, "mock:derby:final")
    await repository.upsert_notification_pref(session, "global", muted=True)
    await repository.upsert_stadium(
        session, "mock:ashport-comets", "Ashport Comets", venue="Ashport Fieldhouse"
    )
    await repository.set_meta(session, "onboarded", "1")
    await session.commit()
    before = await _snapshot(session)

    await repository.replace_followed(session, [], [])
    await session.commit()

    assert await _snapshot(session) == before


# One nation picked under two national competitions is two followed teams
# that fetch the same fixtures: each has the other's league, and the
# friendlies, as sibling competitions.  ESPN's nation ids are global, so
# both carry the same provider key.
_NATION_KEY = "478"


def _soccer_league(league_id: str) -> domain.League:
    return domain.League(
        id=league_id,
        sport=domain.Sport.SOCCER,
        name=league_id.title(),
        provider="mock",
        provider_key=f"mock-{league_id}",
    )


def _pick(team_id: str, league_id: str, provider_key: str = _NATION_KEY) -> domain.Team:
    return domain.Team(
        id=team_id,
        league_id=league_id,
        name="Verdania",
        abbreviation="VRD",
        provider_key=provider_key,
    )


def _slate(team_id: str, *league_ids: str) -> list[tuple[str, str, str]]:
    """``team_competitions`` triples: the nation's other competitions."""
    return [(team_id, league_id, _NATION_KEY) for league_id in league_ids]


def _old_final(
    game_id: str, league_id: str, *, home: str | None = None, away: str | None = None
) -> domain.Game:
    """A result from well before the week a refresh reaches back."""
    return make_game(
        game_id,
        league_id=league_id,
        start_time=utcnow() - timedelta(days=40),
        home_team_id=home,
        away_team_id=away,
        home_name="Verdania",
        away_name="Mistralia",
        state=domain.GameState(
            game_id=game_id, phase=domain.GamePhase.FINAL, home_score=2, away_score=1
        ),
    )


async def _tagged_games(session: AsyncSession) -> list[tuple[str, str | None, str | None]]:
    rows = await session.execute(
        select(GameORM.id, GameORM.home_team_id, GameORM.away_team_id).order_by(GameORM.id)
    )
    return [tuple(row) for row in rows]


async def test_replace_followed_hands_game_tags_to_same_provider_team(
    session: AsyncSession,
) -> None:
    """Dropping one of two picks of the same team costs the kept one no game.

    Both picks fetch every fixture they share, and the later fetch's id
    replaces the earlier one on the game's side (``upsert_games``), so ALL
    of them end up tagged with one pick — the one whose id sorts last.
    Unfollow that one and its side used to be nulled like any removed
    team's: the games then looked like nobody's and were deleted, with the
    kept pick still playing in them and a refresh reaching back one week.

    The tag passes to the kept pick instead — but only where that pick is
    the same team: fetched in the game's league under the same provider
    key.  The key alone is not an identity (ESPN reuses ids across
    sports), which is what the basketball team with Verdania's key is
    here for.
    """
    cup, euro, friendly, hoops = (_soccer_league(i) for i in ("cup", "euro", "friendly", "hoops"))
    euro_verdania = _pick("euro-verdania", "euro")
    cup_verdania = _pick("cup-verdania", "cup")
    hoops_478 = _pick("hoops-478", "hoops")
    euro_mistralia = _pick("euro-mistralia", "euro", "479")
    euro_slate = _slate("euro-verdania", "cup", "friendly")
    cup_slate = _slate("cup-verdania", "euro", "friendly")
    await repository.replace_followed(
        session,
        [cup, euro, friendly, hoops],
        [euro_verdania, cup_verdania, hoops_478, euro_mistralia],
        euro_slate + cup_slate,
    )
    # As the refresh leaves them: every game the two picks share carries
    # the id fetched last, here ``cup-verdania``.
    await repository.upsert_games(
        session,
        [
            _old_final("mock:cup-1", "cup", home="cup-verdania"),
            _old_final("mock:euro-1", "euro", away="cup-verdania"),
            # Against another followed team, which is unfollowed too and has
            # no second pick: an ordinary removed side.
            _old_final("mock:euro-2", "euro", home="cup-verdania", away="euro-mistralia"),
            _old_final("mock:euro-3", "euro", home="euro-mistralia"),
            _old_final("mock:friendly-1", "friendly", home="cup-verdania"),
            _old_final("mock:hoops-1", "hoops", home="hoops-478"),
        ],
    )
    await session.commit()

    # Only the Euros pick of Verdania stays.  ``hoops`` stays stored too, so
    # nothing but the missing follow can take its game.
    await repository.replace_followed(
        session, [cup, euro, friendly, hoops], [euro_verdania], euro_slate
    )
    await session.commit()

    assert await _tagged_games(session) == [
        ("mock:cup-1", "euro-verdania", None),
        ("mock:euro-1", None, "euro-verdania"),
        ("mock:euro-2", "euro-verdania", None),
        ("mock:friendly-1", "euro-verdania", None),
    ]
    session.expire_all()
    results = await repository.results_for_team(session, "euro-verdania")
    assert {game.id for game in results} == {
        "mock:cup-1",
        "mock:euro-1",
        "mock:euro-2",
        "mock:friendly-1",
    }


async def test_replace_followed_hands_game_tags_only_where_the_kept_pick_is_fetched(
    session: AsyncSession,
) -> None:
    """A tag moves to a team the next refresh would put it on, or not at all.

    Three picks of one nation, the last-sorted one removed.  A game in a
    league both kept picks are fetched in goes to the one fetched LAST
    (team-id order, as ``refresh_schedules`` walks them) — the id the next
    pass would write there anyway, so the tags do not move twice.  A game
    in a competition neither kept pick is fetched in has no heir: its side
    is nulled and, with nobody following it, the game goes.
    """
    leagues = [_soccer_league(i) for i in ("cup", "euro", "friendly", "nations", "qualifier")]
    euro_verdania = _pick("euro-verdania", "euro")
    nations_verdania = _pick("nations-verdania", "nations")
    cup_verdania = _pick("cup-verdania", "cup")
    kept_slates = _slate("euro-verdania", "friendly") + _slate(
        "nations-verdania", "euro", "friendly"
    )
    await repository.replace_followed(
        session,
        leagues,
        [euro_verdania, nations_verdania, cup_verdania],
        kept_slates + _slate("cup-verdania", "euro", "friendly", "nations", "qualifier"),
    )
    await repository.upsert_games(
        session,
        [
            _old_final("mock:euro-1", "euro", home="cup-verdania"),
            _old_final("mock:friendly-1", "friendly", home="cup-verdania"),
            _old_final("mock:nations-1", "nations", home="cup-verdania"),
            _old_final("mock:qualifier-1", "qualifier", home="cup-verdania"),
        ],
    )
    await session.commit()

    await repository.replace_followed(
        session, leagues, [euro_verdania, nations_verdania], kept_slates
    )
    await session.commit()

    assert await _tagged_games(session) == [
        ("mock:euro-1", "nations-verdania", None),
        ("mock:friendly-1", "nations-verdania", None),
        ("mock:nations-1", "nations-verdania", None),
    ]


async def test_replace_followed_hands_game_tags_to_a_team_the_same_save_adds(
    session: AsyncSession,
) -> None:
    """Swapping a nation's pick for its other competition in ONE save keeps its games.

    The heir is then a team this very save adds, and the side columns are
    foreign keys: the tags can only move once the new ``teams`` row is
    stored.  Handed over any earlier, the save fails on the constraint and
    stores nothing.
    """
    leagues = [_soccer_league(i) for i in ("cup", "euro", "friendly")]
    await repository.replace_followed(
        session,
        leagues,
        [_pick("cup-verdania", "cup")],
        _slate("cup-verdania", "euro", "friendly"),
    )
    await repository.upsert_games(
        session,
        [
            _old_final("mock:cup-1", "cup", home="cup-verdania"),
            _old_final("mock:friendly-1", "friendly", away="cup-verdania"),
        ],
    )
    await session.commit()

    await repository.replace_followed(
        session,
        leagues,
        [_pick("euro-verdania", "euro")],
        _slate("euro-verdania", "cup", "friendly"),
    )
    await session.commit()

    assert await _stored(session, TeamORM.id) == {"euro-verdania"}
    assert await _tagged_games(session) == [
        ("mock:cup-1", "euro-verdania", None),
        ("mock:friendly-1", None, "euro-verdania"),
    ]


# ---------------------------------------------------------------------------
# Sweeping rows the stored followed set does not back
# ---------------------------------------------------------------------------


async def test_prune_unbacked_is_noop_on_consistent_db(followed: AsyncSession) -> None:
    """The sweep runs before every refresh; on a healthy database it changes nothing.

    ``followed`` holds each shape it could be tempted by: an untagged
    fixture and a league-level article (in the whole-competition league),
    one-sided games, an untagged table row, prefs of all three scope
    kinds, a ``team_competitions`` row.  Compared on every column of
    every row, ``updated_at`` included — a needless rewrite would bump it.
    """
    session = followed
    before = await _snapshot(session)
    assert all(
        before[table]
        for table in (
            "games",
            "events",
            "standings",
            "standings_archive",
            "news_items",
            "players",
            "player_follows",
            "notification_prefs",
            "notifications_sent",
            "team_competitions",
        )
    )

    await repository.prune_unbacked(session)
    await session.commit()

    assert await _snapshot(session) == before


async def test_prune_unbacked_removes_leftovers(followed: AsyncSession) -> None:
    """What a write racing a save can leave behind is swept — and only that.

    A refresh pass or live tick that read the followed set before a save
    can commit after it.  None of these rows breaks a foreign key, so
    nothing else would ever remove them: an untagged fixture and a league
    article in a league not followed in whole, a pref and a player follow
    for a team that is gone, and follow tags naming it.
    """
    session = followed
    before = await _snapshot(session, ignore=_UPDATED_AT)

    await repository.upsert_games(
        session,
        [
            make_game(
                "mock:stale-fixture",
                start_time=utcnow() + timedelta(days=1),
                home_name="Glimmerfen Owls",
                away_name="Bramblewick Foxes",
            )
        ],
    )
    await repository.upsert_news(
        session,
        [
            domain.NewsItem(
                id="news-stale-league",
                team_id=None,
                league_id=LEAGUE_ID,
                title="Pinnacle league office names new commissioner",
                url="https://news.example/pinnacle/commissioner",
                source="Pinnacle Wire",
            )
        ],
    )
    await repository.upsert_notification_pref(session, "team:ghost-team", muted=True)
    await repository.upsert_notification_pref(session, "league:ghost-league", muted=True)
    await repository.follow_player(
        session, athlete_id="6001", name="Pell Ardent", team_id="ghost-team", league_id=LEAGUE_ID
    )
    table = await repository.get_standings(session, LEAGUE_ID)
    assert table is not None
    table.rows = [
        {**row, "team_id": "ghost-team"} if row["team_id"] is None else row for row in table.rows
    ]
    event = await repository.get_event(session, "mock:open")
    assert event is not None
    event.leaderboard = [
        {**row, "player_id": "ghost-team"} if row["player_id"] is None else row
        for row in event.leaderboard
    ]
    await session.commit()
    with_leftovers = await _snapshot(session, ignore=_UPDATED_AT)
    for name in ("games", "news_items", "notification_prefs", "player_follows"):
        assert len(with_leftovers[name]) > len(before[name])
    for name in ("standings", "events"):
        assert with_leftovers[name] != before[name]

    await repository.prune_unbacked(session)
    await session.commit()

    assert await _snapshot(session, ignore=_UPDATED_AT) == before


async def test_prune_unbacked_on_empty_database(session: AsyncSession) -> None:
    """Nothing followed yet (a fresh install's first refresh): every id set
    is empty, which must neither be invalid SQL nor read as "delete it all"."""
    await repository.prune_unbacked(session)
    await session.commit()
    assert not any((await _snapshot(session)).values())

    await repository.mark_notified(session, "mock:derby:final")
    await repository.upsert_notification_pref(session, "global", muted=True)
    await session.commit()
    before = await _snapshot(session)

    await repository.prune_unbacked(session)
    await session.commit()

    assert await _snapshot(session) == before


async def test_prune_unbacked_with_no_team_followed_keeps_league_level_rows(
    followed: AsyncSession,
) -> None:
    """The sweep's own "not followed" test must spare NULL as well.

    It asks the database (``NOT IN (SELECT id FROM teams)``) rather than
    comparing with a list, and with nothing but a whole competition
    followed that subquery returns no row — which a bare ``NOT IN`` reads
    as "every row, NULL included": each untagged cup fixture and
    league-level article, all a whole-competition follow has.
    """
    session = followed
    await repository.replace_followed(session, [CUP], [])
    await session.commit()
    before = await _snapshot(session)
    assert before["teams"] == []
    assert before["games"] and before["news_items"] and before["notification_prefs"]

    await repository.prune_unbacked(session)
    await session.commit()

    assert await _snapshot(session) == before


# Two the fixture does not follow.  The Sentinels are a name on two
# basketball fixtures and the untagged third row of the table; Wren
# Abernathy is the untagged third row of the leaderboard.  The saves below
# follow them while a sweep is running.
SENTINELS = domain.Team(
    id="foxglove-sentinels",
    league_id=LEAGUE_ID,
    name="Foxglove Sentinels",
    abbreviation="FOX",
    provider_key="foxglove-sentinels",
)
ABERNATHY = domain.Team(
    id="fairway-tour-wren-abernathy",
    league_id=TOUR_ID,
    name="Wren Abernathy",
    abbreviation="ABE",
    provider_key="4412",
)


async def _sweep_with_a_save_landing(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    lands_before: Callable[[Any], bool],
    save: Callable[[AsyncSession], Awaitable[None]],
) -> None:
    """Run the sweep with ``save`` committing from a second session part-way.

    It lands just before the first statement of the sweep that
    ``lands_before`` accepts.  Engine-independent: in-memory SQLite shares
    one connection between the sessions, postgres gives each its own
    (READ COMMITTED).
    """
    saved = False
    real_execute = session.execute

    async def execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal saved
        if not saved and lands_before(statement):
            saved = True
            async with async_sessionmaker(session.bind, expire_on_commit=False)() as other:
                await save(other)
                await other.commit()
        return await real_execute(statement, *args, **kwargs)

    # A pass sweeps in a session of its own.  This one may still hold the
    # fixture's rows as loaded (``expire_on_commit=False``), and a query
    # does not refresh those: the sweep would judge what the fixture
    # wrote, not what the save has just stored.
    session.expire_all()
    monkeypatch.setattr(session, "execute", execute)
    await repository.prune_unbacked(session)
    await session.commit()
    monkeypatch.undo()
    assert saved


def _is_write(statement: Any) -> bool:
    return isinstance(statement, (Delete, Update))


def _writes_prefs(statement: Any) -> bool:
    return _is_write(statement) and statement.table.name == NotificationPrefORM.__tablename__


def _reads_rows_of(entity: type[Base]) -> Callable[[Any], bool]:
    """Accepts the sweep's ``select(entity)``: whole rows, to edit their JSON tags."""

    def reads(statement: Any) -> bool:
        if not isinstance(statement, Select):
            return False
        selected = statement.column_descriptions
        # ``is``: against a column, ``==`` would build a SQL comparison.
        return len(selected) == 1 and selected[0]["expr"] is entity

    return reads


@pytest.mark.parametrize(
    "lands_before",
    [
        pytest.param(_is_write, id="the-first-delete"),
        pytest.param(_writes_prefs, id="the-prefs-delete"),
    ],
)
async def test_prune_unbacked_spares_what_a_save_commits_during_the_sweep(
    followed: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    lands_before: Callable[[Any], bool],
) -> None:
    """A save that commits while the sweep runs keeps everything it wrote.

    The sweep starts every refresh pass, the cron's and the startup one
    included, so a save can land in the middle of it.  Sweeping against
    ids read into Python beforehand, it took the new set for a leftover:
    a league just followed in whole lost the headline-only ``league:{id}``
    pref the save had seeded for it, and then notified on every event
    type.  Each statement has to judge by the set as stored when IT runs.

    The save is committed from a second session at the moment the sweep
    is about to issue a write — its first, then the prefs delete itself.
    This save only adds a league, so it is the league ids it puts to the
    test; the three tests below do the same for the whole-competition
    ids, the team ids and the ids the JSON tags are judged by.
    """
    session = followed
    before = await _snapshot(session)
    harbour = domain.League(
        id="harbour-shield",
        sport=domain.Sport.BASKETBALL,
        name="Harbour Shield",
        provider="mock",
        provider_key="mock-shield",
        follow_all=True,
    )
    seed = {"game_start": True, "final": True, "period_start": False}

    async def save(other: AsyncSession) -> None:
        await repository.replace_followed(
            other,
            [BASKETBALL, CUP, TOUR, harbour],
            [COMETS, STAGS, CALLOWAY, FINCH],
            [COMETS_IN_CUP],
        )
        await repository.upsert_notification_pref(other, f"league:{harbour.id}", events=seed)

    await _sweep_with_a_save_landing(session, monkeypatch, lands_before, save)

    session.expire_all()
    prefs = await repository.prefs_by_scope(session)
    assert set(prefs) == {scope for scope, *_ in before["notification_prefs"]} | {
        f"league:{harbour.id}"
    }
    assert prefs[f"league:{harbour.id}"].events == seed
    after = await _snapshot(session)
    assert [row[0] for row in after["leagues"]] == sorted([LEAGUE_ID, CUP_ID, TOUR_ID, harbour.id])
    for name, rows in before.items():
        if name not in ("leagues", "notification_prefs"):
            assert after[name] == rows, name


async def test_prune_unbacked_judges_whole_competitions_by_the_stored_set(
    followed: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A league a save switches to whole-competition mid-sweep keeps its games.

    The save drops both basketball teams and follows the league in whole,
    so its games are now untagged — kept only because the league is
    ``follow_all``.  Judged by the whole-competition ids as they were
    before the save, every one of them is a leftover, and the results
    among them are gone for good.
    """
    session = followed

    async def save(other: AsyncSession) -> None:
        await repository.replace_followed(
            other, [replace(BASKETBALL, follow_all=True), CUP, TOUR], [CALLOWAY, FINCH], []
        )

    await _sweep_with_a_save_landing(session, monkeypatch, _is_write, save)

    assert await _tagged_games(session) == [
        ("mock:comets-away", None, None),
        ("mock:cup-comets", None, None),
        ("mock:cup-final", None, None),
        ("mock:derby", None, None),
        ("mock:stags-home", None, None),
    ]


async def test_prune_unbacked_judges_teams_by_the_stored_set(
    followed: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows keyed by a team a save adds mid-sweep are not leftovers.

    The save follows the Sentinels; a fixture tagged with them, a pref and
    a player follow are stored with it.  Judged by the team ids as they
    were before the save, all three name a team that is not followed: the
    game is untagged and then deleted as nobody's, the other two deleted.
    """
    session = followed
    games = await _tagged_games(session)
    prefs = await _stored(session, NotificationPrefORM.scope)

    async def save(other: AsyncSession) -> None:
        await repository.replace_followed(
            other,
            [BASKETBALL, CUP, TOUR],
            [COMETS, STAGS, CALLOWAY, FINCH, SENTINELS],
            [COMETS_IN_CUP],
        )
        await repository.upsert_games(
            other,
            [
                make_game(
                    "mock:sentinels-home",
                    start_time=utcnow() + timedelta(days=5),
                    home_team_id=SENTINELS.id,
                    home_name="Foxglove Sentinels",
                    away_name="Glimmerfen Owls",
                )
            ],
        )
        await repository.upsert_notification_pref(other, f"team:{SENTINELS.id}", muted=True)
        await repository.follow_player(
            other,
            athlete_id="5001",
            name="Tamsin Rook",
            team_id=SENTINELS.id,
            league_id=LEAGUE_ID,
        )

    await _sweep_with_a_save_landing(session, monkeypatch, _is_write, save)

    assert set(await _tagged_games(session)) == {
        *games,
        ("mock:sentinels-home", SENTINELS.id, None),
    }
    assert await _stored(session, NotificationPrefORM.scope) == prefs | {f"team:{SENTINELS.id}"}
    assert await _stored(session, PlayerFollowORM.team_id) == {
        TEAM_COMETS,
        TEAM_STAGS,
        SENTINELS.id,
    }


@pytest.mark.parametrize("tagged", ["standings", "leaderboard"])
async def test_prune_unbacked_keeps_the_tag_of_a_team_followed_during_the_sweep(
    followed: AsyncSession, monkeypatch: pytest.MonkeyPatch, tagged: str
) -> None:
    """A JSON follow tag is judged by team ids read AFTER the row carrying it.

    Those tags cannot be tested in SQL: the sweep reads the rows, reads
    the team ids and compares in Python.  Were the ids read first, a team
    followed in between — and the tag a writer then puts on its row (a
    live tick writes leaderboards while a pass is running) — would reach
    the comparison as a tag naming nobody, and be cleared.

    The save and the tagged write land just before the read of the table
    that carries the tag, one case each, so the ids have to come after
    BOTH reads whichever order those are in.
    """
    session = followed
    entity, newcomer = (StandingsORM, SENTINELS) if tagged == "standings" else (EventORM, ABERNATHY)

    async def save(other: AsyncSession) -> None:
        await repository.replace_followed(
            other,
            [BASKETBALL, CUP, TOUR],
            [COMETS, STAGS, CALLOWAY, FINCH, newcomer],
            [COMETS_IN_CUP],
        )
        if tagged == "standings":
            table = await repository.get_standings(other, LEAGUE_ID)
            assert table is not None
            table.rows = [
                {**row, "team_id": newcomer.id} if row["team_id"] is None else row
                for row in table.rows
            ]
        else:
            event = await repository.get_event(other, "mock:open")
            assert event is not None
            event.leaderboard = [
                {**row, "player_id": newcomer.id} if row["player_id"] is None else row
                for row in event.leaderboard
            ]

    await _sweep_with_a_save_landing(session, monkeypatch, _reads_rows_of(entity), save)

    if tagged == "standings":
        rows = (
            await session.execute(
                select(StandingsORM.rows).where(StandingsORM.league_id == LEAGUE_ID)
            )
        ).scalar_one()
        assert [row["team_id"] for row in rows] == [TEAM_COMETS, TEAM_STAGS, SENTINELS.id]
    else:
        board = (
            await session.execute(select(EventORM.leaderboard).where(EventORM.id == "mock:open"))
        ).scalar_one()
        assert [row["player_id"] for row in board] == [
            GOLFER_CALLOWAY,
            GOLFER_FINCH,
            ABERNATHY.id,
        ]


# ---------------------------------------------------------------------------
# Team "About" facts (club history + stadium prose)
# ---------------------------------------------------------------------------


async def test_set_team_info_persists_about_facts(seeded: AsyncSession) -> None:
    """set_team_info caches the club "About" facts, including attribution."""
    await repository.set_team_info(
        seeded,
        TEAM_COMETS,
        description="Ashport Comets are a fictional basketball club.",
        founded_year=1974,
        venue_description="Ashport Fieldhouse has hosted the Comets since 1974.",
        description_source="thesportsdb",
    )
    await seeded.flush()

    stored = await repository.get_team(seeded, TEAM_COMETS)
    assert stored is not None
    assert stored.description == "Ashport Comets are a fictional basketball club."
    assert stored.founded_year == 1974
    assert stored.venue_description == "Ashport Fieldhouse has hosted the Comets since 1974."
    assert stored.description_source == "thesportsdb"


async def test_set_team_info_partial_update_never_wipes(seeded: AsyncSession) -> None:
    """A None field in a later write leaves the resolved fact untouched."""
    await repository.set_team_info(
        seeded,
        TEAM_COMETS,
        description="Ashport Comets are a fictional basketball club.",
        description_source="thesportsdb",
    )
    await seeded.flush()
    # A re-resolve that found only the founding year must not clear the rest.
    await repository.set_team_info(seeded, TEAM_COMETS, founded_year=1974)
    await seeded.flush()

    stored = await repository.get_team(seeded, TEAM_COMETS)
    assert stored is not None
    assert stored.description == "Ashport Comets are a fictional basketball club."
    assert stored.description_source == "thesportsdb"
    assert stored.founded_year == 1974


async def test_set_team_info_unknown_team_is_noop(seeded: AsyncSession) -> None:
    """An unknown team id logs and does nothing rather than raising."""
    await repository.set_team_info(seeded, "no-such-team", description="Nowhere")
    await seeded.flush()
    assert await repository.get_team(seeded, "no-such-team") is None


# ---------------------------------------------------------------------------
# Team locations (map view)
# ---------------------------------------------------------------------------


async def test_set_team_location_persists_and_lists(seeded: AsyncSession) -> None:
    """set_team_location caches venue+coords; list_teams_with_location finds it."""
    # Before resolution, no team carries coordinates.
    assert await repository.list_teams_with_location(seeded) == []

    await repository.set_team_location(seeded, TEAM_COMETS, "Ashport Fieldhouse", 51.5074, -0.1278)
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


async def test_most_common_home_venue_by_name_finds_a_side_with_no_teams_row(
    seeded: AsyncSession,
) -> None:
    """A whole-competition side has no ``teams`` row but does have fixtures.

    This is the case the id-keyed query structurally cannot answer: a
    national side followed via ``follow_all`` is stored on its games by
    name only, so without the by-name tally it has no venue fallback and
    drops off the map entirely.
    """
    now = utcnow()
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:nation-home-1",
                start_time=now + timedelta(days=1),
                home_name="Ashport Republic",
                venue="Republic Arena",
            ),
            make_game(
                "mock:nation-home-2",
                start_time=now + timedelta(days=3),
                home_name="Ashport Republic",
                venue="Republic Arena",
            ),
            # A one-off neutral host must lose the tally, not win it.
            make_game(
                "mock:nation-home-3",
                start_time=now + timedelta(days=5),
                home_name="Ashport Republic",
                venue="Harbor Neutral Ground",
            ),
        ],
    )
    await seeded.flush()

    venue = await repository.most_common_home_venue_by_name(seeded, LEAGUE_ID, "Ashport Republic")
    assert venue == "Republic Arena"


async def test_most_common_home_venue_by_name_is_case_insensitive(
    seeded: AsyncSession,
) -> None:
    """Provider casing drifts between the catalog and the fixture rows."""
    now = utcnow()
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:nation-case",
                start_time=now + timedelta(days=1),
                home_name="Ashport Republic",
                venue="Republic Arena",
            )
        ],
    )
    await seeded.flush()

    assert (
        await repository.most_common_home_venue_by_name(seeded, LEAGUE_ID, "ASHPORT REPUBLIC")
        == "Republic Arena"
    )


async def test_most_common_home_venue_by_name_ignores_away_only_sides(
    seeded: AsyncSession,
) -> None:
    """A name that only ever plays away borrows nobody else's ground."""
    now = utcnow()
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:nation-away",
                start_time=now + timedelta(days=1),
                home_name="Ashport Republic",
                away_name="Rivermont Union",
                venue="Republic Arena",
            )
        ],
    )
    await seeded.flush()

    assert (
        await repository.most_common_home_venue_by_name(seeded, LEAGUE_ID, "Rivermont Union")
        is None
    )


async def test_most_common_home_venue_by_name_stays_inside_its_league(
    seeded: AsyncSession,
) -> None:
    """Names collide across leagues; a tally must not cross that boundary."""
    now = utcnow()
    await repository.upsert_games(
        seeded,
        [
            make_game(
                "mock:nation-league",
                start_time=now + timedelta(days=1),
                home_name="Ashport Republic",
                venue="Republic Arena",
            )
        ],
    )
    await seeded.flush()

    assert (
        await repository.most_common_home_venue_by_name(
            seeded, "some-other-league", "Ashport Republic"
        )
        is None
    )


# ---------------------------------------------------------------------------
# Stadium cache keys (id spaces + legacy rows)
# ---------------------------------------------------------------------------


def _league(league_id: str, sport: domain.Sport, provider: str = "espn") -> domain.League:
    return domain.League(
        id=league_id,
        sport=sport,
        name=league_id.upper(),
        provider=provider,
        provider_key=f"{sport.value}/{league_id}",
    )


NFL = _league("nfl", domain.Sport.FOOTBALL)
NHL = _league("nhl", domain.Sport.HOCKEY)
# The two sides ESPN numbers "1" in those leagues (fictional names).
NFL_ONE = "Ashport Falcons"
NHL_ONE = "Rivermont Bruins"
LEGACY_ONE = "espn:1"  # the league-blind key an older build cached both under


def _stadium_facts(row: StadiumORM) -> dict[str, Any]:
    """Everything a stadium row stores except its key."""
    facts = {
        column.key: getattr(row, column.key)
        for column in StadiumORM.__table__.columns
        if not column.primary_key
    }
    facts["fetched_at"] = ensure_utc(facts["fetched_at"])  # sqlite hands it back naive
    return facts


async def _stored_stadiums(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Every stadium row as the database holds it, by key."""
    await session.commit()
    session.expire_all()  # read back what was written, not what is in memory
    rows = (await session.execute(select(StadiumORM))).scalars().all()
    return {row.key: _stadium_facts(row) for row in rows}


def _legacy_stadium(key: str = LEGACY_ONE, team_name: str = NFL_ONE) -> StadiumORM:
    """A located row as an older build cached it, every fact filled in."""
    return StadiumORM(
        key=key,
        team_name=team_name,
        venue="Falcon Field",
        lat=33.7554,
        lon=-84.4008,
        capacity=71000,
        opened=2017,
        image_url="https://images.example.com/falcon-field.jpg",
        location="Ashport, Georgia",
        surface="Turf",
        resolved=True,
        fetched_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_stadium_key_id_spaces() -> None:
    """A provider id names one team only inside the space it was issued in."""
    # Every US league numbers its teams from 1 — and so do the NBA and WNBA,
    # which a key scoped by sport alone would still merge.
    assert repository.stadium_key(NFL, "1") == "espn:nfl:1"
    assert repository.stadium_key(NHL, "1") == "espn:nhl:1"
    nba = _league("nba", domain.Sport.BASKETBALL)
    wnba = _league("wnba", domain.Sport.BASKETBALL)
    assert repository.stadium_key(nba, "5") != repository.stadium_key(wnba, "5")

    # A soccer side keeps its id in every competition: one key, or a club in
    # two followed competitions is resolved twice and never deduped.
    epl = _league("epl", domain.Sport.SOCCER)
    ucl = _league("ucl", domain.Sport.SOCCER)
    assert repository.stadium_key(epl, "364") == "espn:soccer:364"
    assert repository.stadium_key(ucl, "364") == "espn:soccer:364"

    # TheSportsDB fetches a team by id alone, whatever the league or sport.
    men = _league("evl-men", domain.Sport.VOLLEYBALL, provider="thesportsdb")
    women = _league("evl-women", domain.Sport.VOLLEYBALL, provider="thesportsdb")
    assert repository.stadium_key(men, "134567") == "thesportsdb:all:134567"
    assert repository.stadium_key(women, "134567") == "thesportsdb:all:134567"

    # The sweep holds domain leagues (enum sport), the map route ORM rows
    # (plain string): both must land on the same key or the two disagree
    # about which row is a team's.
    for league in (epl, NFL):
        row = LeagueORM(
            id=league.id,
            sport=league.sport.value,
            name=league.name,
            provider=league.provider,
            provider_key=league.provider_key,
        )
        assert repository.stadium_key(row, "9") == repository.stadium_key(league, "9")


def test_no_catalog_league_id_is_soccer() -> None:
    """``"soccer"`` is the scope every ESPN soccer league shares.

    Any other ESPN league is scoped by its own id, so one whose id was
    literally ``"soccer"`` would be filed into the soccer id space and trade
    stadiums with whichever club holds the same number.
    """
    leagues = (*espn_catalog.CATALOG, *espn_catalog.NATIONAL_SIBLING_LEAGUES)
    assert "soccer" not in {league.id for league in leagues}


async def test_find_team_stadium_needs_name_match(session: AsyncSession) -> None:
    """A league-blind legacy row is only ever read as the team it names.

    The NFL and NHL sides numbered 1 used to share ``espn:1``; whichever
    resolved first owned the row and the other was plotted at its ground.
    """
    legacy = _legacy_stadium()
    session.add(legacy)
    # Every fact is set, so a column the copy forgot cannot hide behind two
    # matching NULLs — a new StadiumORM column has to be seeded here first.
    assert None not in _stadium_facts(legacy).values()
    seeded_rows = await _stored_stadiums(session)

    # The other league's team: the row is not its stadium, with or without
    # leave to adopt.
    assert await repository.find_team_stadium(session, NHL, "1", NHL_ONE) is None
    assert await repository.find_team_stadium(session, NHL, "1", NHL_ONE, adopt=True) is None

    # The team it names: served as it is, and reading wrote nothing.
    found = await repository.find_team_stadium(session, NFL, "1", NFL_ONE)
    assert found is not None and found.key == LEGACY_ONE
    assert await _stored_stadiums(session) == seeded_rows

    # Adopting copies it to the team's own key and leaves the original for
    # an older build to find; a second adopt has nothing left to do.
    for _ in range(2):
        adopted = await repository.find_team_stadium(session, NFL, "1", NFL_ONE, adopt=True)
        assert adopted is not None and adopted.key == "espn:nfl:1"
        assert await _stored_stadiums(session) == {
            LEGACY_ONE: seeded_rows[LEGACY_ONE],
            "espn:nfl:1": seeded_rows[LEGACY_ONE],
        }

    # Once the scoped row exists it is the answer, whatever the legacy row
    # or the catalog name goes on to say.
    found = await repository.find_team_stadium(session, NFL, "1", "Renamed Since")
    assert found is not None and found.key == "espn:nfl:1"


async def test_find_team_stadium_reads_the_legacy_row_of_the_leagues_own_provider(
    session: AsyncSession,
) -> None:
    """The legacy key was ``"{provider}:{id}"`` for every provider, not only
    ESPN: a TheSportsDB league's old rows sit under ``thesportsdb:<id>`` and
    are adopted from there."""
    league = _league("evl-men", domain.Sport.VOLLEYBALL, provider="thesportsdb")
    session.add(_legacy_stadium(key="thesportsdb:134567", team_name="Harbor Spikers"))
    seeded_rows = await _stored_stadiums(session)

    found = await repository.find_team_stadium(session, league, "134567", "Harbor Spikers")
    assert found is not None and found.key == "thesportsdb:134567"

    adopted = await repository.find_team_stadium(
        session, league, "134567", "Harbor Spikers", adopt=True
    )
    assert adopted is not None and adopted.key == "thesportsdb:all:134567"
    assert await _stored_stadiums(session) == {
        "thesportsdb:134567": seeded_rows["thesportsdb:134567"],
        "thesportsdb:all:134567": seeded_rows["thesportsdb:134567"],
    }


async def test_find_team_stadium_adopts_a_miss_at_its_original_age(
    session: AsyncSession,
) -> None:
    """A cached miss is copied as the old lookup it is, not as a fresh one.

    The sweep retries a miss once it is older than its cool-down; a copy
    stamped "now" would hold every adopted miss off for another round.
    """
    missed_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    session.add(StadiumORM(key=LEGACY_ONE, team_name=NFL_ONE, resolved=True, fetched_at=missed_at))
    await session.flush()

    adopted = await repository.find_team_stadium(session, NFL, "1", NFL_ONE, adopt=True)

    assert adopted is not None and adopted.lat is None
    stored = await _stored_stadiums(session)
    assert stored["espn:nfl:1"]["resolved"] is True
    assert stored["espn:nfl:1"]["fetched_at"] == missed_at


@pytest.mark.parametrize(
    ("stored_name", "catalog_name", "proven"),
    [
        ("Ashport Falcons", "  ASHPORT falcons ", True),  # casing and padding drift
        ("Ashport Falcons", "Ashport Falcon", False),  # a prefix is another team
        ("", "", False),  # a row cached without a name proves nothing...
        ("", "   ", False),
        (" ", "", False),  # ...however the blank is spelled on either side
        # upsert_stadium clipped the stored name to the column width; the
        # catalog still sends all of it.
        ("F" * 128, "F" * 140, True),
    ],
)
async def test_find_team_stadium_name_proof(
    session: AsyncSession, stored_name: str, catalog_name: str, proven: bool
) -> None:
    session.add(_legacy_stadium(team_name=stored_name))
    await session.flush()

    found = await repository.find_team_stadium(session, NFL, "1", catalog_name)

    assert (found is not None) is proven
