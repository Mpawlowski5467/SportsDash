"""Scheduler job tests against a throwaway in-memory SQLite database.

Focus: ``refresh_schedules`` Phase-3a fan-out — a national team's
sibling competitions and whole-competition (``follow_all``) follows are
fetched in addition to each team's primary-league schedule, and one
failing source never aborts the rest.

The engine is built directly here (never via ``app.db``); ``session_scope``
is monkeypatched into the ``jobs`` module so writes hit this throwaway
database.  Providers are exercised through a fake registered via the real
registry, so this test depends on no other agent's adapter.  All fixture
data is fictional.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.models import domain
from tests.db_engine import create_test_schema, make_test_engine
from app.models.orm import EventORM, TeamCompetitionORM, TeamORM
from app.providers import registry
from app import background
from app.scheduler import common as scheduler_common
from app.scheduler import jobs
from app.scheduler import live as scheduler_live
from app.scheduler import refresh as scheduler_refresh
from app.scheduler import stadium_cache as scheduler_stadium_cache
from app.services import repository, stadiums, wiki
from app.timeutil import utcnow

PROVIDER_ID = "faketest"

# League ids (real-app data is normally fine, but tests stay fictional).
PRIMARY_LEAGUE = "harborline-soccer"  # national team's home competition
SIBLING_LEAGUE = "coastal-cup"  # extra competition it also plays in
FOLLOW_ALL_LEAGUE = "continental-open"  # whole-competition follow
BROKEN_FOLLOW_ALL = "tempest-trophy"  # follow_all league whose fetch raises

TEAM_ID = "harborline-northshore-united"

# Golf (leaderboard Event model) fixtures.
GOLF_LEAGUE = "fairway-masters-tour"  # a followed golfer's tour
GOLF_FOLLOW_ALL = "links-invitational"  # whole-field golf follow
GOLFER_TEAM_ID = "fairway-masters-tour-rowan-ashgrove"
GOLFER_ESPN_ID = "espn-athlete-7781"  # carried on LeaderRow.player_id by the provider


def _league(league_id: str, *, follow_all: bool = False) -> domain.League:
    return domain.League(
        id=league_id,
        sport=domain.Sport.SOCCER,
        name=league_id.replace("-", " ").title(),
        provider=PROVIDER_ID,
        provider_key=f"soccer/{league_id}",
        follow_all=follow_all,
    )


def _golf_league(league_id: str, *, follow_all: bool = False) -> domain.League:
    return domain.League(
        id=league_id,
        sport=domain.Sport.GOLF,
        name=league_id.replace("-", " ").title(),
        provider=PROVIDER_ID,
        provider_key=f"golf/{league_id}",
        follow_all=follow_all,
    )


def _golf_team(team_id: str, league_id: str, *, espn_id: str) -> domain.Team:
    """A followed golfer: a single-member golf team whose provider_key is
    the ESPN athlete id."""
    return domain.Team(
        id=team_id,
        league_id=league_id,
        name="Rowan Ashgrove",
        abbreviation="ASH",
        provider_key=espn_id,
    )


def _event(
    event_id: str,
    league_id: str,
    *,
    phase: domain.GamePhase,
    round_label: str = "",
    leaderboard: tuple[domain.LeaderRow, ...] = (),
) -> domain.Event:
    """A fictional golf tournament Event.

    The provider carries each competitor's ESPN athlete id transiently in
    ``LeaderRow.player_id`` — the scheduler rewrites it to the internal
    followed-team id (or ``None``) before persisting.
    """
    return domain.Event(
        id=event_id,
        league_id=league_id,
        name=event_id.replace("-", " ").title(),
        start_time=utcnow() - timedelta(days=1),
        end_time=utcnow() + timedelta(days=2),
        phase=phase,
        round_label=round_label,
        leaderboard=leaderboard,
    )


def _board(*rows: tuple[int, str, str, str, str | None]) -> tuple[domain.LeaderRow, ...]:
    """Build a leaderboard from ``(position, label, name, score, espn_id)`` tuples.

    The provider populates ``player_id`` with the ESPN athlete id; the
    scheduler rewrites it.  ``None`` espn_id means an un-carried row.
    """
    return tuple(
        domain.LeaderRow(
            position=pos,
            position_label=label,
            name=name,
            score=score,
            detail="F",
            player_id=espn_id,
        )
        for pos, label, name, score, espn_id in rows
    )


def _game(
    game_id: str,
    league_id: str,
    *,
    home_team_id: str | None = None,
    away_team_id: str | None = None,
    home_name: str = "Home Side",
    away_name: str = "Away Side",
) -> domain.Game:
    return domain.Game(
        id=game_id,
        league_id=league_id,
        home_name=home_name,
        away_name=away_name,
        start_time=utcnow() + timedelta(days=2),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )


class FakeProvider:
    """In-memory ``SportsProvider`` returning canned, fictional fixtures.

    ``schedules`` is keyed by ``(league_id, team_provider_key)`` and
    ``competition`` by ``league_id``; a key mapped to the ``RAISES``
    sentinel makes that call blow up so error-isolation can be asserted.
    """

    provider_id = PROVIDER_ID
    RAISES = object()

    def __init__(self) -> None:
        self.schedules: dict[tuple[str, str], object] = {}
        self.competition: dict[str, object] = {}
        self.competition_calls: list[str] = []
        # Leaderboard (golf) events: ``events`` is keyed by league id and
        # returned by ``get_events``; ``event_states`` is keyed by
        # provider_event_key and returned by ``get_event_state``.
        self.events: dict[str, object] = {}
        self.events_calls: list[str] = []
        self.event_states: dict[str, object] = {}
        self.event_state_calls: list[str] = []
        # Map view: ``locations`` keyed by team provider_key returns a
        # ``TeamLocation`` (or the ``RAISES`` sentinel) from
        # ``get_team_location``; calls are recorded for skip assertions.
        self.locations: dict[str, object] = {}
        self.location_calls: list[str] = []
        # Live polling (clocked games): ``live_games`` keyed by league id is
        # returned by ``get_live_games``; ``standings`` keyed by league id is
        # returned by ``get_standings`` and ``standings_calls`` records every
        # standings refresh so the live FINAL trigger can be asserted.
        self.live_games: dict[str, object] = {}
        self.standings: dict[str, object] = {}
        self.standings_calls: list[str] = []

    async def get_schedule(self, league, team, start: date, end: date):
        result = self.schedules.get((league.id, team.provider_key), [])
        if result is self.RAISES:
            raise RuntimeError(f"boom: schedule {league.id}/{team.provider_key}")
        return list(result)

    async def get_competition_schedule(self, league, start: date, end: date):
        self.competition_calls.append(league.id)
        result = self.competition.get(league.id, [])
        if result is self.RAISES:
            raise RuntimeError(f"boom: competition {league.id}")
        return list(result)

    async def get_live_games(self, league):
        result = self.live_games.get(league.id, [])
        if result is self.RAISES:
            raise RuntimeError(f"boom: live_games {league.id}")
        return list(result)

    async def get_game_state(self, league, provider_game_key):
        return None

    async def get_events(self, league, start: date, end: date):
        self.events_calls.append(league.id)
        result = self.events.get(league.id, [])
        if result is self.RAISES:
            raise RuntimeError(f"boom: events {league.id}")
        return list(result)

    async def get_event_state(self, league, provider_event_key):
        self.event_state_calls.append(provider_event_key)
        result = self.event_states.get(provider_event_key)
        if result is self.RAISES:
            raise RuntimeError(f"boom: event_state {provider_event_key}")
        return result

    async def get_team_location(self, league, team):
        self.location_calls.append(team.provider_key)
        result = self.locations.get(team.provider_key)
        if result is self.RAISES:
            raise RuntimeError(f"boom: location {team.provider_key}")
        return result

    async def get_standings(self, league):
        self.standings_calls.append(league.id)
        result = self.standings.get(league.id)
        if result is self.RAISES:
            raise RuntimeError(f"boom: standings {league.id}")
        if result is not None:
            return result
        return domain.Standings(
            league_id=league.id,
            season="2026",
            rows=(),
            fetched_at=utcnow(),
        )

    async def get_roster(self, league, team):
        raise NotImplementedError

    async def get_news(self, league, team):
        return []

    async def close(self) -> None:
        return None


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
    """Point ``jobs.session_scope`` at the throwaway database."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    # The job modules each bind session_scope at import time, so the
    # test scope is patched into every one of them.
    for module in (scheduler_common, scheduler_refresh, scheduler_stadium_cache, scheduler_live):
        monkeypatch.setattr(module, "session_scope", scope)


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """Register a fake provider and restore the registry afterward."""
    provider = FakeProvider()
    saved = registry._providers.get(PROVIDER_ID)
    registry.register_provider(provider)
    try:
        yield provider
    finally:
        if saved is not None:
            registry._providers[PROVIDER_ID] = saved
        else:
            registry._providers.pop(PROVIDER_ID, None)


async def _game_ids(db: async_sessionmaker[AsyncSession]) -> set[str]:
    async with db() as session:
        rows = await repository.games_between(
            session, utcnow() - timedelta(days=30), utcnow() + timedelta(days=60)
        )
    return {row.id for row in rows}


async def _get_event(db: async_sessionmaker[AsyncSession], event_id: str) -> EventORM | None:
    async with db() as session:
        return await repository.get_event(session, event_id)


async def test_team_with_sibling_competition_gets_games_from_both_leagues(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A national team's sibling competition is fetched alongside its primary league."""
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_league(session, _league(SIBLING_LEAGUE))
        await repository.upsert_team(
            session,
            domain.Team(
                id=TEAM_ID,
                league_id=PRIMARY_LEAGUE,
                name="Northshore United",
                abbreviation="NSU",
                provider_key="nsu-global",
            ),
        )
        await session.flush()  # parents before children: postgres enforces FKs
        # The sibling competition the national team also plays in.
        session.add(
            TeamCompetitionORM(
                team_id=TEAM_ID,
                league_id=SIBLING_LEAGUE,
                provider_key="nsu-global",
            )
        )
        await session.commit()

    # Same provider_key in both contexts (ESPN nations are global).
    fake_provider.schedules[(PRIMARY_LEAGUE, "nsu-global")] = [
        _game(f"{PROVIDER_ID}:primary-1", PRIMARY_LEAGUE, home_team_id=TEAM_ID)
    ]
    fake_provider.schedules[(SIBLING_LEAGUE, "nsu-global")] = [
        _game(f"{PROVIDER_ID}:sibling-1", SIBLING_LEAGUE, away_team_id=TEAM_ID)
    ]

    await jobs.refresh_schedules()

    ids = await _game_ids(db)
    assert f"{PROVIDER_ID}:primary-1" in ids, "primary-league game missing"
    assert f"{PROVIDER_ID}:sibling-1" in ids, "sibling-competition game missing"


async def test_follow_all_league_gets_whole_fixture_set(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A ``follow_all`` league is fetched via get_competition_schedule, team ids null."""
    async with db() as session:
        await repository.upsert_league(session, _league(FOLLOW_ALL_LEAGUE, follow_all=True))
        await session.commit()

    fake_provider.competition[FOLLOW_ALL_LEAGUE] = [
        _game(
            f"{PROVIDER_ID}:comp-{i}",
            FOLLOW_ALL_LEAGUE,
            home_name=f"Side {i}A",
            away_name=f"Side {i}B",
        )
        for i in range(4)
    ]

    await jobs.refresh_schedules()

    assert fake_provider.competition_calls == [FOLLOW_ALL_LEAGUE]
    ids = await _game_ids(db)
    assert {f"{PROVIDER_ID}:comp-{i}" for i in range(4)} <= ids

    # Whole-competition games carry no followed-team ids.
    async with db() as session:
        rows = await repository.games_between(
            session, utcnow() - timedelta(days=30), utcnow() + timedelta(days=60)
        )
    for row in rows:
        assert row.home_team_id is None
        assert row.away_team_id is None


async def test_one_failing_source_does_not_stop_the_others(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A provider that raises for one source leaves every healthy source intact."""
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_league(session, _league(SIBLING_LEAGUE))
        await repository.upsert_league(session, _league(FOLLOW_ALL_LEAGUE, follow_all=True))
        await repository.upsert_league(session, _league(BROKEN_FOLLOW_ALL, follow_all=True))
        await repository.upsert_team(
            session,
            domain.Team(
                id=TEAM_ID,
                league_id=PRIMARY_LEAGUE,
                name="Northshore United",
                abbreviation="NSU",
                provider_key="nsu-global",
            ),
        )
        await session.flush()  # parents before children: postgres enforces FKs
        session.add(
            TeamCompetitionORM(
                team_id=TEAM_ID,
                league_id=SIBLING_LEAGUE,
                provider_key="nsu-global",
            )
        )
        await session.commit()

    # Primary league fetch blows up; everything else is healthy.
    fake_provider.schedules[(PRIMARY_LEAGUE, "nsu-global")] = FakeProvider.RAISES
    fake_provider.schedules[(SIBLING_LEAGUE, "nsu-global")] = [
        _game(f"{PROVIDER_ID}:sibling-ok", SIBLING_LEAGUE, away_team_id=TEAM_ID)
    ]
    # One follow_all league raises, the other returns its fixtures.
    fake_provider.competition[BROKEN_FOLLOW_ALL] = FakeProvider.RAISES
    fake_provider.competition[FOLLOW_ALL_LEAGUE] = [
        _game(f"{PROVIDER_ID}:comp-ok", FOLLOW_ALL_LEAGUE)
    ]

    # Must not raise despite the failing sources.
    await jobs.refresh_schedules()

    ids = await _game_ids(db)
    # The failing primary fetch contributed nothing...
    assert not any(i.startswith(f"{PROVIDER_ID}:primary") for i in ids)
    # ...but the sibling competition and the healthy follow_all both landed,
    # and both follow_all leagues were attempted (failure isolated).
    assert f"{PROVIDER_ID}:sibling-ok" in ids
    assert f"{PROVIDER_ID}:comp-ok" in ids
    assert set(fake_provider.competition_calls) == {
        FOLLOW_ALL_LEAGUE,
        BROKEN_FOLLOW_ALL,
    }


# ---------------------------------------------------------------------------
# Phase 5: golf / leaderboard Events
# ---------------------------------------------------------------------------


async def test_refresh_schedules_tags_followed_golfer_by_espn_id(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A followed golfer's ESPN athlete id on a leaderboard row is rewritten
    to that golfer's internal team id; other competitors stay untagged."""
    async with db() as session:
        await repository.upsert_league(session, _golf_league(GOLF_LEAGUE))
        await repository.upsert_team(
            session, _golf_team(GOLFER_TEAM_ID, GOLF_LEAGUE, espn_id=GOLFER_ESPN_ID)
        )
        await session.commit()

    # The provider returns a board carrying ESPN athlete ids on player_id.
    # Only one competitor (GOLFER_ESPN_ID) is the followed golfer.
    fake_provider.events[GOLF_LEAGUE] = [
        _event(
            f"{PROVIDER_ID}:tournament-1",
            GOLF_LEAGUE,
            phase=domain.GamePhase.IN_PROGRESS,
            round_label="Round 2",
            leaderboard=_board(
                (1, "1", "Mara Quillfield", "-9", "espn-athlete-1001"),
                (2, "T2", "Rowan Ashgrove", "-7", GOLFER_ESPN_ID),
                (2, "T2", "Devin Holloway", "-7", "espn-athlete-1002"),
            ),
        )
    ]

    await jobs.refresh_schedules()

    assert fake_provider.events_calls == [GOLF_LEAGUE]
    row = await _get_event(db, f"{PROVIDER_ID}:tournament-1")
    assert row is not None
    assert row.phase == domain.GamePhase.IN_PROGRESS.value
    assert row.round_label == "Round 2"

    # The followed golfer's row now carries the INTERNAL id; everyone else None.
    tagged = {entry["name"]: entry["player_id"] for entry in row.leaderboard}
    assert tagged["Rowan Ashgrove"] == GOLFER_TEAM_ID
    assert tagged["Mara Quillfield"] is None
    assert tagged["Devin Holloway"] is None


async def test_refresh_schedules_fetches_golf_follow_all_league(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A golf ``follow_all`` league is fetched via get_events with no followed
    golfer; every leaderboard row stays untagged (player_id None)."""
    async with db() as session:
        await repository.upsert_league(session, _golf_league(GOLF_FOLLOW_ALL, follow_all=True))
        await session.commit()

    fake_provider.events[GOLF_FOLLOW_ALL] = [
        _event(
            f"{PROVIDER_ID}:open-1",
            GOLF_FOLLOW_ALL,
            phase=domain.GamePhase.IN_PROGRESS,
            leaderboard=_board(
                (1, "1", "Mara Quillfield", "-12", "espn-athlete-1001"),
                (2, "2", "Devin Holloway", "-10", "espn-athlete-1002"),
            ),
        )
    ]

    await jobs.refresh_schedules()

    assert fake_provider.events_calls == [GOLF_FOLLOW_ALL]
    row = await _get_event(db, f"{PROVIDER_ID}:open-1")
    assert row is not None
    # No golfer is followed here, so no row is tagged with an internal id.
    assert all(entry["player_id"] is None for entry in row.leaderboard)


async def test_events_tick_refreshes_board_and_fires_one_final_notification(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """events_tick re-polls an in-progress event, persists the updated board,
    and on FINAL fires exactly one notification with the golfer's finish."""
    event_id = f"{PROVIDER_ID}:tournament-9"
    provider_key = "tournament-9"

    async with db() as session:
        await repository.upsert_league(session, _golf_league(GOLF_LEAGUE))
        await repository.upsert_team(
            session, _golf_team(GOLFER_TEAM_ID, GOLF_LEAGUE, espn_id=GOLFER_ESPN_ID)
        )
        # Seed an in-progress event with the followed golfer already tagged
        # (mid-tournament, as refresh_schedules would have left it).
        await repository.upsert_events(
            session,
            [
                _event(
                    event_id,
                    GOLF_LEAGUE,
                    phase=domain.GamePhase.IN_PROGRESS,
                    round_label="Round 4",
                    leaderboard=_board(
                        (1, "1", "Mara Quillfield", "-15", None),
                        (2, "2", "Rowan Ashgrove", "-13", GOLFER_TEAM_ID),
                    ),
                )
            ],
        )
        await session.commit()

    # Spy on the notification send so we can prove it fires exactly once.
    sent: list[domain.GameEvent] = []

    async def fake_send_event(event: domain.GameEvent) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(jobs.notify, "send_event", fake_send_event)

    # The fresh state: the tournament is now FINAL, the golfer climbed to T2,
    # and the board is updated.  The provider carries ESPN ids again.
    fake_provider.event_states[provider_key] = _event(
        event_id,
        GOLF_LEAGUE,
        phase=domain.GamePhase.FINAL,
        round_label="Final Round",
        leaderboard=_board(
            (1, "1", "Mara Quillfield", "-18", "espn-athlete-1001"),
            (2, "T2", "Rowan Ashgrove", "-16", GOLFER_ESPN_ID),
            (2, "T2", "Devin Holloway", "-16", "espn-athlete-1002"),
        ),
    )

    await jobs.events_tick()

    # The provider was asked for this event's state (by provider key).
    assert fake_provider.event_state_calls == [provider_key]

    # The board was upserted with the fresh, re-tagged leaderboard.
    row = await _get_event(db, event_id)
    assert row is not None
    assert row.phase == domain.GamePhase.FINAL.value
    assert row.round_label == "Final Round"
    tagged = {entry["name"]: entry["player_id"] for entry in row.leaderboard}
    assert tagged["Rowan Ashgrove"] == GOLFER_TEAM_ID
    assert tagged["Mara Quillfield"] is None

    # Exactly one FINAL notification, carrying the golfer's finishing position.
    assert len(sent) == 1
    event = sent[0]
    assert event.type is domain.EventType.FINAL
    assert event.dedupe_key == f"{event_id}:final"
    assert "Rowan Ashgrove" in event.message
    assert "T2" in event.message

    # A second tick must NOT re-send (dedupe ledger): the event is now FINAL
    # so it drops out of active_events; even if re-polled, the key is marked.
    fake_provider.event_state_calls.clear()
    await jobs.events_tick()
    assert len(sent) == 1


async def test_events_tick_resends_missed_final_with_future_end_time(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tournament FINAL whose first send failed is resent from stored state.

    The event has a FUTURE end_time (ESPN's scheduled final-round end) — the
    resend must still fire, proving recency is bounded on ``state_updated_at``
    (set when the live tick wrote the board), not ``end_time``.
    """
    event_id = f"{PROVIDER_ID}:missed-tournament"

    async with db() as session:
        await repository.upsert_league(session, _golf_league(GOLF_LEAGUE))
        await repository.upsert_team(
            session, _golf_team(GOLFER_TEAM_ID, GOLF_LEAGUE, espn_id=GOLFER_ESPN_ID)
        )
        # Stored FINAL (as the live tick committed it) with the followed golfer
        # already tagged to the internal id and a future end_time; NO dedupe row
        # because the first send failed.  upsert_events stamps state_updated_at.
        await repository.upsert_events(
            session,
            [
                _event(
                    event_id,
                    GOLF_LEAGUE,
                    phase=domain.GamePhase.FINAL,
                    round_label="Final Round",
                    leaderboard=_board(
                        (1, "1", "Mara Quillfield", "-18", None),
                        (2, "T2", "Rowan Ashgrove", "-16", GOLFER_TEAM_ID),
                    ),
                )
            ],
        )
        await session.commit()

    sent: list[domain.GameEvent] = []

    async def fake_send_event(event: domain.GameEvent) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(jobs.notify, "send_event", fake_send_event)

    await jobs.events_tick()

    # The resend rebuilt the FINAL from STORED state — no provider call, since
    # the event is FINAL and never enters active_events.
    assert fake_provider.event_state_calls == []
    assert len(sent) == 1
    assert sent[0].dedupe_key == f"{event_id}:final"
    assert "Rowan Ashgrove" in sent[0].message
    async with db() as session:
        assert await repository.was_notified(session, f"{event_id}:final") is True

    # Second tick: the dedupe ledger prevents a duplicate resend.
    await jobs.events_tick()
    assert len(sent) == 1


async def test_events_tick_no_active_events_makes_no_provider_call(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """The cheap gate: with nothing in progress, events_tick does no work."""
    async with db() as session:
        await repository.upsert_league(session, _golf_league(GOLF_LEAGUE))
        await session.commit()

    await jobs.events_tick()

    assert fake_provider.event_state_calls == []


# ---------------------------------------------------------------------------
# Phase 11: live standings refresh on a game's transition to FINAL
# ---------------------------------------------------------------------------
#
# ``live_tick`` polls clocked games; when a followed league's game transitions
# into FINAL during a tick it kicks one coalesced standings refresh for that
# league (debounced per league), so group/division positions move in near-
# real-time.  No final → no refresh.  The refresh runs as a fire-and-forget
# background task, so the tests await it via ``_drain_kicked_tasks``.


async def _drain_kicked_tasks() -> None:
    """Await every fire-and-forget task ``live_tick`` spawned this test.

    ``kick_standings_refresh`` schedules the standings refresh as a
    background task (so the poll never blocks); the tests need it to have
    run before asserting.  A snapshot is awaited so the set can mutate as
    tasks complete and pop themselves out via the done-callback.
    """
    for task in list(background._tasks):
        await asyncio.gather(task, return_exceptions=True)


def _live_game(
    game_id: str,
    league_id: str,
    *,
    phase: domain.GamePhase,
    home_team_id: str | None = None,
    away_team_id: str | None = None,
    home_score: int = 0,
    away_score: int = 0,
) -> domain.Game:
    """A clocked game carrying a live ``GameState`` (what a scoreboard returns)."""
    return domain.Game(
        id=game_id,
        league_id=league_id,
        home_name="Home Side",
        away_name="Away Side",
        start_time=utcnow() - timedelta(hours=1),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        state=domain.GameState(
            game_id=game_id,
            phase=phase,
            home_score=home_score,
            away_score=away_score,
            period=4,
            period_label="Final" if phase is domain.GamePhase.FINAL else "Q4",
        ),
    )


async def _seed_in_progress_game(
    db: async_sessionmaker[AsyncSession], game_id: str, league_id: str, *, team_id: str
) -> None:
    """Store one IN_PROGRESS game so the live-poll gate picks it up."""
    async with db() as session:
        await repository.upsert_games(
            session,
            [
                _live_game(
                    game_id,
                    league_id,
                    phase=domain.GamePhase.IN_PROGRESS,
                    home_team_id=team_id,
                    home_score=80,
                    away_score=78,
                )
            ],
        )
        await session.commit()


async def test_live_tick_refreshes_standings_when_a_game_goes_final(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A followed game transitioning to FINAL kicks exactly one standings
    refresh for its league, and the fresh standings are persisted."""
    game_id = f"{PROVIDER_ID}:nsu-vs-rivals"
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        await session.commit()
    await _seed_in_progress_game(db, game_id, PRIMARY_LEAGUE, team_id=TEAM_ID)

    # The scoreboard now reports the game FINAL — a transition this tick.
    fake_provider.live_games[PRIMARY_LEAGUE] = [
        _live_game(
            game_id,
            PRIMARY_LEAGUE,
            phase=domain.GamePhase.FINAL,
            home_team_id=TEAM_ID,
            home_score=88,
            away_score=85,
        )
    ]
    # Canned standings the live trigger should fetch + persist.
    fake_provider.standings[PRIMARY_LEAGUE] = domain.Standings(
        league_id=PRIMARY_LEAGUE,
        season="2026",
        rows=(domain.StandingRow(rank=1, team_name="Northshore United", wins=10, losses=2),),
        fetched_at=utcnow(),
    )

    await jobs.live_tick()
    await _drain_kicked_tasks()

    # Exactly one standings refresh, for the league whose game finalized.
    assert fake_provider.standings_calls == [PRIMARY_LEAGUE]

    # The game is stored FINAL and the fresh standings landed.
    async with db() as session:
        row = await repository.get_game(session, game_id)
        assert row is not None and row.phase == domain.GamePhase.FINAL.value
        standings = await repository.get_standings(session, PRIMARY_LEAGUE)
        assert standings is not None
        assert standings.rows[0]["team_name"] == "Northshore United"


async def test_live_tick_no_final_does_not_refresh_standings(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A game that is still in progress (no FINAL transition) triggers no
    standings refresh."""
    game_id = f"{PROVIDER_ID}:nsu-still-playing"
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        await session.commit()
    await _seed_in_progress_game(db, game_id, PRIMARY_LEAGUE, team_id=TEAM_ID)

    # Still in progress, just a score change — no phase transition to FINAL.
    fake_provider.live_games[PRIMARY_LEAGUE] = [
        _live_game(
            game_id,
            PRIMARY_LEAGUE,
            phase=domain.GamePhase.IN_PROGRESS,
            home_team_id=TEAM_ID,
            home_score=90,
            away_score=85,
        )
    ]

    await jobs.live_tick()
    await _drain_kicked_tasks()

    # No final this tick → no standings refresh.
    assert fake_provider.standings_calls == []


async def test_live_tick_coalesces_one_refresh_per_league(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """Two games in the same league finalizing in one tick still kick exactly
    one standings refresh for that league (debounced/coalesced)."""
    game_a = f"{PROVIDER_ID}:nsu-game-a"
    game_b = f"{PROVIDER_ID}:nsu-game-b"
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        await session.commit()
    await _seed_in_progress_game(db, game_a, PRIMARY_LEAGUE, team_id=TEAM_ID)
    await _seed_in_progress_game(db, game_b, PRIMARY_LEAGUE, team_id=TEAM_ID)

    fake_provider.live_games[PRIMARY_LEAGUE] = [
        _live_game(game_a, PRIMARY_LEAGUE, phase=domain.GamePhase.FINAL, home_team_id=TEAM_ID),
        _live_game(game_b, PRIMARY_LEAGUE, phase=domain.GamePhase.FINAL, home_team_id=TEAM_ID),
    ]

    await jobs.live_tick()
    await _drain_kicked_tasks()

    # Both games finalized but the league's standings refreshed only once.
    assert fake_provider.standings_calls == [PRIMARY_LEAGUE]


async def test_live_tick_standings_failure_does_not_raise(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A failing standings refresh is isolated: the tick still finalizes the
    game and never raises."""
    game_id = f"{PROVIDER_ID}:nsu-final-bad-standings"
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        await session.commit()
    await _seed_in_progress_game(db, game_id, PRIMARY_LEAGUE, team_id=TEAM_ID)

    fake_provider.live_games[PRIMARY_LEAGUE] = [
        _live_game(game_id, PRIMARY_LEAGUE, phase=domain.GamePhase.FINAL, home_team_id=TEAM_ID)
    ]
    fake_provider.standings[PRIMARY_LEAGUE] = FakeProvider.RAISES

    # Must not raise despite the standings source blowing up.
    await jobs.live_tick()
    await _drain_kicked_tasks()

    # The refresh was attempted (then failed, isolated)...
    assert fake_provider.standings_calls == [PRIMARY_LEAGUE]
    # ...and the game was still finalized.
    async with db() as session:
        row = await repository.get_game(session, game_id)
        assert row is not None and row.phase == domain.GamePhase.FINAL.value


# ---------------------------------------------------------------------------
# refresh_locations (map view)
# ---------------------------------------------------------------------------

# A second followed team whose provider gives a venue name but no coords —
# its location comes from the geocode service.
TEAM_WITH_VENUE = "harborline-saltford-rangers"


def _plain_team(team_id: str, league_id: str, provider_key: str) -> domain.Team:
    return domain.Team(
        id=team_id,
        league_id=league_id,
        name=team_id.replace("-", " ").title(),
        abbreviation="TST",
        provider_key=provider_key,
    )


async def test_refresh_locations_stores_provider_coords_and_geocodes_the_rest(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider coords are stored directly; a coord-less venue is geocoded.

    One team's provider returns full coordinates (no geocode needed); the
    other returns only a venue name, which is resolved through the geocode
    service.  Both end up cached on their team rows for the map view.
    """
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        await repository.upsert_team(
            session, _plain_team(TEAM_WITH_VENUE, PRIMARY_LEAGUE, "rangers-global")
        )
        await session.commit()

    # First team: provider already knows the coordinates.
    fake_provider.locations["nsu-global"] = domain.TeamLocation(
        venue="Northshore Park", lat=53.4084, lon=-2.9916
    )
    # Second team: provider knows only the venue name → must be geocoded.
    fake_provider.locations["rangers-global"] = domain.TeamLocation(
        venue="Saltford Ground", lat=None, lon=None
    )

    geocoded: list[str] = []

    async def fake_geocode(query: str) -> tuple[float, float] | None:
        geocoded.append(query)
        return (51.4816, -3.1791)

    monkeypatch.setattr(jobs.geocode, "geocode", fake_geocode)

    # The Phase 11 strategy adds a TheSportsDB stadium-enrichment step (by
    # team name) between provider coords and geocode.  These teams are
    # fictional, so stub it out to keep the test offline and assert the
    # provider-coords / geocode fallbacks still behave.
    async def no_enrichment(team_name: str, *, sport: str | None = None):
        return None

    monkeypatch.setattr(jobs.stadiums, "lookup_stadium", no_enrichment)

    await jobs.refresh_locations()

    # Only the coord-less venue was geocoded.
    assert geocoded == ["Saltford Ground"]

    async with db() as session:
        located = {row.id: row for row in await repository.list_teams_with_location(session)}
    assert set(located) == {TEAM_ID, TEAM_WITH_VENUE}

    direct = located[TEAM_ID]
    assert direct.home_venue == "Northshore Park"
    assert direct.venue_lat == pytest.approx(53.4084)
    assert direct.venue_lon == pytest.approx(-2.9916)

    via_geocode = located[TEAM_WITH_VENUE]
    assert via_geocode.home_venue == "Saltford Ground"
    assert via_geocode.venue_lat == pytest.approx(51.4816)
    assert via_geocode.venue_lon == pytest.approx(-3.1791)


async def test_refresh_locations_skips_already_resolved_teams(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team that already has coordinates is neither re-fetched nor re-geocoded."""
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(session, _plain_team(TEAM_ID, PRIMARY_LEAGUE, "nsu-global"))
        # Pre-resolve it: the cached coords mean the job should leave it be.
        await repository.set_team_location(session, TEAM_ID, "Northshore Park", 53.4084, -2.9916)
        await session.commit()

    async def fail_geocode(query: str) -> tuple[float, float] | None:
        raise AssertionError("geocode must not be called for a resolved team")

    monkeypatch.setattr(jobs.geocode, "geocode", fail_geocode)

    await jobs.refresh_locations()

    # The provider's location endpoint was never consulted for this team.
    assert fake_provider.location_calls == []

    # The cached coordinates are untouched.
    async with db() as session:
        row = await repository.get_team(session, TEAM_ID)
    assert row is not None
    assert row.venue_lat == pytest.approx(53.4084)
    assert row.venue_lon == pytest.approx(-2.9916)


async def test_refresh_locations_enrichment_rescues_offseason_soccer_club(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An off-season soccer club resolves from the TheSportsDB enrichment.

    Reproduces the off-season EPL case (e.g. following Arsenal in June): the
    provider exposes no venue, and there is no upcoming home fixture and no
    stored game to borrow one from — so the only thing that can locate the
    club is the name-based stadium enrichment.  As long as that lookup is not
    poisoned by a transient miss (see ``test_stadiums``), it returns the
    stadium + coordinates and the club plots on the map.
    """
    async with db() as session:
        await repository.upsert_league(session, _league(PRIMARY_LEAGUE))
        await repository.upsert_team(
            session, _plain_team(TEAM_WITH_VENUE, PRIMARY_LEAGUE, "offseason-global")
        )
        await session.commit()

    # Provider has no venue (fake_provider.locations left empty → None), and
    # the enrichment supplies the stadium WITH coordinates (TheSportsDB's DMS
    # map reference), so no geocode is needed.
    async def enrich(team_name: str, *, sport: str | None = None):
        assert sport == "soccer"
        return domain.TeamLocation(
            venue="Emirates Stadium, Holloway, London",
            lat=51.55667,
            lon=-0.10611,
            capacity=60338,
            opened=2006,
        )

    monkeypatch.setattr(jobs.stadiums, "lookup_stadium", enrich)

    async def fail_geocode(query: str) -> tuple[float, float] | None:
        raise AssertionError("enrichment already had coords — no geocode expected")

    monkeypatch.setattr(jobs.geocode, "geocode", fail_geocode)

    await jobs.refresh_locations()

    async with db() as session:
        located = {row.id: row for row in await repository.list_teams_with_location(session)}
    assert set(located) == {TEAM_WITH_VENUE}
    row = located[TEAM_WITH_VENUE]
    assert row.home_venue == "Emirates Stadium, Holloway, London"
    assert row.venue_lat == pytest.approx(51.55667)
    assert row.venue_lon == pytest.approx(-0.10611)
    assert row.venue_capacity == 60338


# ---------------------------------------------------------------------------
# refresh_rosters — soccer TheSportsDB photo backfill
# ---------------------------------------------------------------------------


async def test_attach_player_photos_backfills_missing_only_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only photoless players are looked up, capped, and a no-match stays None."""
    monkeypatch.setattr(scheduler_refresh, "_PHOTO_BACKFILL_MAX_PLAYERS", 2)

    looked_up: list[str] = []

    async def fake_lookup_photo(name, *, team_name=None, sport=None):
        looked_up.append(name)
        assert team_name == "Chelsea" and sport == "soccer"
        return None if name == "No Article" else f"https://img/{name}.jpg"

    monkeypatch.setattr(jobs.player_photos, "lookup_photo", fake_lookup_photo)

    team = domain.Team(
        id="chelsea",
        league_id="eng.1",
        name="Chelsea",
        abbreviation="CHE",
        provider_key="363",
    )
    players = (
        domain.Player(
            id="p0",
            team_id="chelsea",
            name="Has Photo",
            photo_url="https://espn/x.jpg",
        ),
        domain.Player(id="p1", team_id="chelsea", name="No Article"),
        domain.Player(id="p2", team_id="chelsea", name="Cole Palmer"),
        domain.Player(id="p3", team_id="chelsea", name="Beyond Cap"),
    )
    roster = domain.Roster(team_id="chelsea", players=players, fetched_at=utcnow())

    result = await jobs._attach_player_photos(roster, team, "soccer")
    by_id = {p.id: p.photo_url for p in result.players}

    # Existing ESPN headshot is left untouched (not re-looked-up).
    assert "Has Photo" not in looked_up
    assert by_id["p0"] == "https://espn/x.jpg"
    # First two photoless players are queried; a no-match returns None.
    assert looked_up == ["No Article", "Cole Palmer"]
    assert by_id["p1"] is None
    assert by_id["p2"] == "https://img/Cole Palmer.jpg"
    # Beyond the cap — never looked up, stays photoless.
    assert by_id["p3"] is None


# ---------------------------------------------------------------------------
# daily_refresh single-flight guard
# ---------------------------------------------------------------------------
#
# daily_refresh is reachable from the daily cron, the startup spawn, and the
# setup-wizard kick, and its upserts are SELECT-then-insert — overlapping
# runs double-insert and the loser logs an IntegrityError.  The module-level
# lock makes an overlapping caller a no-op; these tests stub the sub-jobs
# and prove the guard holds across two of the entry points.


def _stub_daily_refresh_subjobs(
    monkeypatch: pytest.MonkeyPatch,
    started: asyncio.Event,
    release: asyncio.Event,
    calls: list[str],
) -> None:
    """Replace daily_refresh's sub-jobs with a controllable barrier.

    refresh_schedules stands in as the instrumented sub-job (records the
    call, signals it has started, then blocks until released); everything
    else is a no-op so the test exercises only the guard.  Stale-game
    pruning still runs — against the throwaway database via patched_scope.
    """

    async def fake_refresh_schedules() -> None:
        calls.append("schedules")
        started.set()
        await release.wait()

    async def noop() -> None:
        return None

    monkeypatch.setattr(scheduler_refresh, "refresh_schedules", fake_refresh_schedules)
    for name in (
        "refresh_standings",
        "refresh_rosters",
        "refresh_news",
        "refresh_team_info",
        "refresh_locations",
        "refresh_competition_stadiums",
        "refresh_game_venue_coords",
    ):
        monkeypatch.setattr(scheduler_refresh, name, noop)


async def test_daily_refresh_skips_an_overlapping_run(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlapping daily_refresh is a no-op, not a queued second run."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    _stub_daily_refresh_subjobs(monkeypatch, started, release, calls)

    first = asyncio.create_task(jobs.daily_refresh())
    try:
        await started.wait()
        # A second call while the first holds the lock returns immediately
        # without running the sub-jobs again (skip, don't queue).
        await jobs.daily_refresh()
        assert calls == ["schedules"]
    finally:
        release.set()
    await first
    assert calls == ["schedules"]

    # The lock leaves with the run: a later, non-overlapping call proceeds.
    await jobs.daily_refresh()
    assert calls == ["schedules", "schedules"]


async def test_kick_daily_refresh_is_single_flight_across_kicks(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping setup-wizard kicks run the refresh body only once."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    _stub_daily_refresh_subjobs(monkeypatch, started, release, calls)

    jobs.kick_daily_refresh()
    try:
        await started.wait()
        jobs.kick_daily_refresh()  # overlaps the in-flight run
    finally:
        release.set()
    await _drain_kicked_tasks()
    assert calls == ["schedules"]


def test_refresh_news_interval_job_is_single_instance() -> None:
    """The hourly news job is capped at one instance (it also runs inside
    daily_refresh), with the same coalesce semantics as the live polls."""
    scheduler = jobs.setup_scheduler()
    news_job = scheduler.get_job("refresh_news")
    assert news_job is not None
    assert news_job.max_instances == 1
    assert news_job.coalesce is True

    live_job = scheduler.get_job("live_tick")
    assert live_job is not None
    assert live_job.max_instances == 1


# ---------------------------------------------------------------------------
# refresh_team_info — club "About" enrichment (TheSportsDB + Wikipedia fallback)
# ---------------------------------------------------------------------------
#
# The job caches the club's history paragraph, founding year and stadium prose
# on the team row, recording which upstream supplied the club description so
# the profile page can attribute it.  All fixtures are fictional; the
# TheSportsDB / Wikipedia lookups are stubbed on their modules.

ABOUT_LEAGUE = "cinder-league"
ABOUT_TEAM = "cinder-foxes"


async def _seed_about_team(
    db: async_sessionmaker[AsyncSession],
    *,
    description: str | None = None,
    venue_description: str | None = None,
    description_source: str | None = None,
) -> None:
    """A followed fictional club, optionally pre-enriched."""
    async with db() as session:
        await repository.upsert_league(session, _league(ABOUT_LEAGUE))
        await repository.upsert_team(
            session,
            domain.Team(
                id=ABOUT_TEAM,
                league_id=ABOUT_LEAGUE,
                name="Cinder Foxes",
                abbreviation="CIN",
                provider_key="cinder-foxes",
            ),
        )
        await session.flush()  # parents before children: postgres enforces FKs
        row = await session.get(TeamORM, ABOUT_TEAM)
        assert row is not None
        row.description = description
        row.venue_description = venue_description
        row.description_source = description_source
        await session.commit()


def _stub_about_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tsdb_info: stadiums.TeamInfo | None,
    wiki_summary: wiki.WikiSummary | None = None,
) -> dict[str, list[str]]:
    """Stub both "About" upstreams; returns recorded call names per source."""
    calls: dict[str, list[str]] = {"tsdb": [], "wiki": []}

    async def fake_lookup_team_info(name: str, *, sport: str | None = None):
        calls["tsdb"].append(name)
        return tsdb_info

    async def fake_team_summary(name: str, *, sport: str | None = None):
        calls["wiki"].append(name)
        return wiki_summary

    monkeypatch.setattr(scheduler_refresh.stadiums, "lookup_team_info", fake_lookup_team_info)
    monkeypatch.setattr(scheduler_refresh.wiki, "team_summary", fake_team_summary)
    return calls


async def _about_row(db: async_sessionmaker[AsyncSession]) -> TeamORM:
    async with db() as session:
        row = await session.get(TeamORM, ABOUT_TEAM)
        assert row is not None
        return row


async def test_refresh_team_info_writes_tsdb_facts_with_source(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full TheSportsDB hit lands on the row, attributed to TheSportsDB."""
    await _seed_about_team(db)
    calls = _stub_about_sources(
        monkeypatch,
        tsdb_info=stadiums.TeamInfo(
            description="Cinder Foxes are a fictional club from Emberfall.",
            founded=1921,
            venue_description="Cinder Arena has hosted the Foxes since 1921.",
        ),
    )

    await scheduler_refresh.refresh_team_info()

    row = await _about_row(db)
    assert row.description == "Cinder Foxes are a fictional club from Emberfall."
    assert row.founded_year == 1921
    assert row.venue_description == "Cinder Arena has hosted the Foxes since 1921."
    assert row.description_source == "thesportsdb"
    # A TheSportsDB description means Wikipedia is never consulted.
    assert calls["tsdb"] == ["Cinder Foxes"]
    assert calls["wiki"] == []


async def test_refresh_team_info_falls_back_to_wikipedia(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TheSportsDB without a club description → Wikipedia's lead fills it.

    The venue prose still comes from TheSportsDB (Wikipedia is only ever the
    club-description fallback), so one refresh can mix sources on a row.
    """
    await _seed_about_team(db)
    calls = _stub_about_sources(
        monkeypatch,
        tsdb_info=stadiums.TeamInfo(
            founded=1921,
            venue_description="Cinder Arena has hosted the Foxes since 1921.",
        ),
        wiki_summary=wiki.WikiSummary(
            title="Cinder Foxes",
            extract="The Cinder Foxes are an Emberfall football club.",
        ),
    )

    await scheduler_refresh.refresh_team_info()

    row = await _about_row(db)
    assert row.description == "The Cinder Foxes are an Emberfall football club."
    assert row.description_source == "wikipedia"
    assert row.founded_year == 1921
    assert row.venue_description == "Cinder Arena has hosted the Foxes since 1921."
    assert calls["wiki"] == ["Cinder Foxes"]


async def test_refresh_team_info_neither_source_leaves_row_untouched(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No facts anywhere → nothing is written, and the job does not raise."""
    await _seed_about_team(db)
    _stub_about_sources(monkeypatch, tsdb_info=None, wiki_summary=None)

    await scheduler_refresh.refresh_team_info()

    row = await _about_row(db)
    assert row.description is None
    assert row.founded_year is None
    assert row.venue_description is None
    assert row.description_source is None


async def test_refresh_team_info_skips_fully_enriched_team(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team with club description AND venue prose is never re-fetched."""
    await _seed_about_team(
        db,
        description="Cinder Foxes are a fictional club from Emberfall.",
        venue_description="Cinder Arena has hosted the Foxes since 1921.",
        description_source="thesportsdb",
    )
    calls = _stub_about_sources(
        monkeypatch,
        tsdb_info=stadiums.TeamInfo(description="stale"),
        wiki_summary=wiki.WikiSummary(title="Cinder Foxes", extract="stale"),
    )

    await scheduler_refresh.refresh_team_info()

    assert calls == {"tsdb": [], "wiki": []}
    row = await _about_row(db)
    assert row.description == "Cinder Foxes are a fictional club from Emberfall."


async def test_refresh_team_info_backfills_venue_prose_for_legacy_row(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A club enriched before venue prose existed is still pending.

    Its description is already set, but a missing ``venue_description`` keeps
    the team in the job's pending set, so the next refresh backfills the
    stadium prose (and the previously-untracked description source).
    """
    await _seed_about_team(db, description="Cinder Foxes are a fictional club from Emberfall.")
    calls = _stub_about_sources(
        monkeypatch,
        tsdb_info=stadiums.TeamInfo(
            description="Cinder Foxes are a fictional club from Emberfall.",
            founded=1921,
            venue_description="Cinder Arena has hosted the Foxes since 1921.",
        ),
    )

    await scheduler_refresh.refresh_team_info()

    assert calls["tsdb"] == ["Cinder Foxes"]
    row = await _about_row(db)
    assert row.venue_description == "Cinder Arena has hosted the Foxes since 1921."
    assert row.description_source == "thesportsdb"
