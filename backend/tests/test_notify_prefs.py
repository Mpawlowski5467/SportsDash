"""Notification-preference tests: pure resolution, repository merge, live gate.

Three layers, all with fictional fixture data:

1. ``decide()`` resolution — exhaustive: global default/mute/per-event,
   league-over-global, team-over-league, and the two-followed-teams rule
   (any muted team scope blocks the shared fixture).
2. ``upsert_notification_pref`` merge semantics against in-memory SQLite
   (partial updates preserve the fields they omit).
3. A ``live_tick`` integration check: a muted team's game is suppressed
   while an enabled team's game still notifies (fake provider + a spy on
   ``notify.send_event``).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models import domain
from app.models.domain import GamePhase, GameState
from app.models.orm import Base, GameORM
from app.providers import registry
from app.scheduler import jobs
from app.services import notify, notify_prefs, repository
from app.services.notify_prefs import EVENT_TYPES, decide
from app.timeutil import utcnow

FINAL = domain.EventType.FINAL.value
GAME_START = domain.EventType.GAME_START.value
STARTING_SOON = domain.EventType.STARTING_SOON.value
PERIOD_START = domain.EventType.PERIOD_START.value
INTERMISSION = domain.EventType.INTERMISSION.value

TEAM_COMETS = "ashport-comets"
TEAM_STAGS = "rivermont-stags"
LEAGUE_ID = "pinnacle-basketball"


# ---------------------------------------------------------------------------
# Plain structural stand-in for a stored preference row
# ---------------------------------------------------------------------------


@dataclass
class Pref:
    """Duck-typed ``NotificationPrefORM`` — only ``muted`` + ``events``."""

    muted: bool = False
    events: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_event_types_are_the_five_ordered() -> None:
    assert EVENT_TYPES == (
        STARTING_SOON,
        GAME_START,
        PERIOD_START,
        INTERMISSION,
        FINAL,
    )


def test_default_events_all_enabled() -> None:
    assert notify_prefs.default_events() == {et: True for et in EVENT_TYPES}


def test_follow_all_default_events_only_start_and_final() -> None:
    assert notify_prefs.follow_all_default_events() == {
        STARTING_SOON: False,
        GAME_START: True,
        PERIOD_START: False,
        INTERMISSION: False,
        FINAL: True,
    }


# ---------------------------------------------------------------------------
# decide() — global scope / no rows
# ---------------------------------------------------------------------------


def test_no_prefs_at_all_defaults_enabled() -> None:
    assert decide({}, FINAL, [TEAM_COMETS], LEAGUE_ID) is True


def test_global_default_on_for_every_event_type() -> None:
    for event_type in EVENT_TYPES:
        assert decide({}, event_type, [TEAM_COMETS], LEAGUE_ID) is True


def test_global_mute_blocks_every_event() -> None:
    prefs = {"global": Pref(muted=True)}
    for event_type in EVENT_TYPES:
        assert decide(prefs, event_type, [TEAM_COMETS], LEAGUE_ID) is False


def test_global_events_final_false_disables_only_final() -> None:
    prefs = {"global": Pref(events={FINAL: False})}
    assert decide(prefs, FINAL, [TEAM_COMETS], LEAGUE_ID) is False
    # Every other type is unaffected by the single disabled key.
    for event_type in (STARTING_SOON, GAME_START, PERIOD_START, INTERMISSION):
        assert decide(prefs, event_type, [TEAM_COMETS], LEAGUE_ID) is True


def test_global_events_explicit_true_stays_enabled() -> None:
    prefs = {"global": Pref(events={FINAL: True})}
    assert decide(prefs, FINAL, [], LEAGUE_ID) is True


def test_no_league_and_no_team_falls_through_to_global() -> None:
    prefs = {"global": Pref(events={GAME_START: False})}
    # No team ids, no league id at all -> only global applies.
    assert decide(prefs, GAME_START, [], None) is False
    assert decide(prefs, FINAL, [], None) is True


# ---------------------------------------------------------------------------
# decide() — league overrides global
# ---------------------------------------------------------------------------


def test_league_pref_overrides_global() -> None:
    prefs = {
        "global": Pref(muted=True),  # global says no...
        f"league:{LEAGUE_ID}": Pref(events={FINAL: True}),  # ...league says yes
    }
    # League scope is more specific than global and is consulted instead.
    assert decide(prefs, FINAL, [], LEAGUE_ID) is True


def test_league_mute_blocks_even_with_permissive_global() -> None:
    prefs = {
        "global": Pref(),  # global default: everything on
        f"league:{LEAGUE_ID}": Pref(muted=True),
    }
    assert decide(prefs, FINAL, [], LEAGUE_ID) is False


def test_league_event_disabled_only_affects_that_type() -> None:
    prefs = {f"league:{LEAGUE_ID}": Pref(events={PERIOD_START: False})}
    assert decide(prefs, PERIOD_START, [], LEAGUE_ID) is False
    assert decide(prefs, FINAL, [], LEAGUE_ID) is True


def test_league_pref_not_consulted_when_league_id_absent() -> None:
    # A league pref exists but this game has no league context -> global.
    prefs = {f"league:{LEAGUE_ID}": Pref(muted=True)}
    assert decide(prefs, FINAL, [], None) is True


# ---------------------------------------------------------------------------
# decide() — team overrides league
# ---------------------------------------------------------------------------


def test_team_pref_overrides_league() -> None:
    prefs = {
        f"league:{LEAGUE_ID}": Pref(muted=True),  # league says no...
        f"team:{TEAM_COMETS}": Pref(),  # ...team default: yes
    }
    # The team scope is the most specific layer; the league mute is ignored.
    assert decide(prefs, FINAL, [TEAM_COMETS], LEAGUE_ID) is True


def test_muted_team_blocks_even_if_league_enabled() -> None:
    prefs = {
        f"league:{LEAGUE_ID}": Pref(),  # league default: everything on
        f"team:{TEAM_COMETS}": Pref(muted=True),
    }
    assert decide(prefs, FINAL, [TEAM_COMETS], LEAGUE_ID) is False


def test_team_event_disabled_only_affects_that_type() -> None:
    prefs = {f"team:{TEAM_COMETS}": Pref(events={INTERMISSION: False})}
    assert decide(prefs, INTERMISSION, [TEAM_COMETS], LEAGUE_ID) is False
    assert decide(prefs, GAME_START, [TEAM_COMETS], LEAGUE_ID) is True


def test_team_with_no_row_falls_back_to_league() -> None:
    # The game's team has no team scope, so the league scope decides.
    prefs = {f"league:{LEAGUE_ID}": Pref(events={FINAL: False})}
    assert decide(prefs, FINAL, [TEAM_COMETS], LEAGUE_ID) is False
    assert decide(prefs, GAME_START, [TEAM_COMETS], LEAGUE_ID) is True


# ---------------------------------------------------------------------------
# decide() — two followed teams sharing a fixture
# ---------------------------------------------------------------------------


def test_two_followed_teams_any_muted_blocks() -> None:
    """A game with two followed teams where ONE is muted is suppressed.

    Documented choice: when several followed-team scopes apply to the same
    fixture, the team layer is consulted (league/global are not), and ANY
    blocking team scope wins.  Muting one of the two teams reliably
    silences the shared game.
    """
    prefs = {
        f"team:{TEAM_COMETS}": Pref(muted=True),  # one muted...
        f"team:{TEAM_STAGS}": Pref(),  # ...the other enabled
    }
    assert decide(prefs, FINAL, [TEAM_COMETS, TEAM_STAGS], LEAGUE_ID) is False
    # Order of the team ids must not matter.
    assert decide(prefs, FINAL, [TEAM_STAGS, TEAM_COMETS], LEAGUE_ID) is False


def test_two_followed_teams_both_enabled_fires() -> None:
    prefs = {
        f"team:{TEAM_COMETS}": Pref(),
        f"team:{TEAM_STAGS}": Pref(),
    }
    assert decide(prefs, FINAL, [TEAM_COMETS, TEAM_STAGS], LEAGUE_ID) is True


def test_two_followed_teams_one_event_disabled_blocks_only_that_type() -> None:
    prefs = {
        f"team:{TEAM_COMETS}": Pref(events={PERIOD_START: False}),
        f"team:{TEAM_STAGS}": Pref(),
    }
    assert decide(prefs, PERIOD_START, [TEAM_COMETS, TEAM_STAGS], LEAGUE_ID) is False
    # A different event type is allowed (no team scope blocks it).
    assert decide(prefs, FINAL, [TEAM_COMETS, TEAM_STAGS], LEAGUE_ID) is True


def test_two_followed_teams_one_scope_present_is_the_team_layer() -> None:
    """One team has a scope, the other doesn't: the team layer still wins.

    Because a team scope exists for the fixture, league/global are not
    consulted even though the second team has no row of its own.
    """
    prefs = {
        f"league:{LEAGUE_ID}": Pref(muted=True),  # would block via league...
        f"team:{TEAM_COMETS}": Pref(),  # ...but a team scope exists -> team layer
    }
    assert decide(prefs, FINAL, [TEAM_COMETS, TEAM_STAGS], LEAGUE_ID) is True


# ---------------------------------------------------------------------------
# Repository upsert merge semantics
# ---------------------------------------------------------------------------


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


async def test_upsert_creates_row_when_missing(session: AsyncSession) -> None:
    await repository.upsert_notification_pref(
        session, "global", muted=True, events={FINAL: False}
    )
    await session.flush()
    prefs = await repository.prefs_by_scope(session)
    assert prefs["global"].muted is True
    assert prefs["global"].events == {FINAL: False}


async def test_upsert_merges_only_provided_fields(session: AsyncSession) -> None:
    scope = f"team:{TEAM_COMETS}"
    # Seed muted + one disabled event.
    await repository.upsert_notification_pref(
        session, scope, muted=True, events={FINAL: False}
    )
    await session.flush()

    # Update only events -> muted must be preserved, events merged not replaced.
    await repository.upsert_notification_pref(
        session, scope, events={GAME_START: False}
    )
    await session.flush()
    row = await repository.prefs_by_scope(session)
    assert row[scope].muted is True, "omitted muted must be preserved"
    assert row[scope].events == {FINAL: False, GAME_START: False}

    # Update only muted -> events map untouched.
    await repository.upsert_notification_pref(session, scope, muted=False)
    await session.flush()
    row = await repository.prefs_by_scope(session)
    assert row[scope].muted is False
    assert row[scope].events == {FINAL: False, GAME_START: False}


async def test_upsert_events_overwrites_same_key(session: AsyncSession) -> None:
    scope = f"league:{LEAGUE_ID}"
    await repository.upsert_notification_pref(session, scope, events={FINAL: False})
    await session.flush()
    await repository.upsert_notification_pref(session, scope, events={FINAL: True})
    await session.flush()
    row = await repository.prefs_by_scope(session)
    assert row[scope].events == {FINAL: True}


async def test_get_notification_prefs_ordered_by_scope(session: AsyncSession) -> None:
    await repository.upsert_notification_pref(session, "global")
    await repository.upsert_notification_pref(session, f"team:{TEAM_STAGS}")
    await repository.upsert_notification_pref(session, f"league:{LEAGUE_ID}")
    await session.flush()
    scopes = [row.scope for row in await repository.get_notification_prefs(session)]
    assert scopes == sorted(scopes)


async def test_prefs_by_scope_round_trips_through_decide(session: AsyncSession) -> None:
    """A stored ORM row resolves the same way as the plain stand-in."""
    await repository.upsert_notification_pref(
        session, f"team:{TEAM_COMETS}", muted=True
    )
    await session.flush()
    prefs = await repository.prefs_by_scope(session)
    assert decide(prefs, FINAL, [TEAM_COMETS], LEAGUE_ID) is False
    assert decide(prefs, FINAL, [TEAM_STAGS], LEAGUE_ID) is True


# ---------------------------------------------------------------------------
# live_tick integration: prefs gate notifications
# ---------------------------------------------------------------------------

PROVIDER_ID = "fakeprefs"


class FakeProvider:
    """Returns canned FINAL live states so live_tick fires FINAL events."""

    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self.states: dict[str, GameState] = {}

    async def get_live_games(self, league):
        games = []
        for game_id, state in self.states.items():
            games.append(
                domain.Game(
                    id=game_id,
                    league_id=league.id,
                    home_name="Home",
                    away_name="Away",
                    start_time=utcnow() - timedelta(hours=1),
                    state=state,
                )
            )
        return games

    async def get_game_state(self, league, provider_game_key):
        return self.states.get(f"{PROVIDER_ID}:{provider_game_key}")

    async def get_schedule(self, league, team, start, end):
        return []

    async def get_competition_schedule(self, league, start, end):
        return []

    async def get_standings(self, league):
        raise NotImplementedError

    async def get_roster(self, league, team):
        raise NotImplementedError

    async def get_news(self, league, team):
        return []

    async def close(self) -> None:
        return None


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
def patched_scope(
    db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with db() as sess:
            yield sess
            await sess.commit()

    monkeypatch.setattr(jobs, "session_scope", scope)


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
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


def _in_progress_row(game_id: str, team_id: str) -> GameORM:
    """An in-progress game row whose home side is a followed team."""
    return GameORM(
        id=game_id,
        league_id=LEAGUE_ID,
        home_team_id=team_id,
        away_team_id=None,
        home_name="Home",
        away_name="Away",
        home_abbreviation="HOM",
        away_abbreviation="AWY",
        start_time=utcnow() - timedelta(hours=1),
        venue=None,
        phase=GamePhase.IN_PROGRESS.value,
        home_score=10,
        away_score=8,
        period=4,
        period_label="Q4",
    )


async def test_live_tick_muted_team_suppressed_enabled_team_fires(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A muted team's FINAL is suppressed; an enabled team's FINAL still sends."""
    muted_game = f"{PROVIDER_ID}:muted-game"
    open_game = f"{PROVIDER_ID}:open-game"

    async with db() as session:
        await repository.upsert_league(
            session,
            domain.League(
                id=LEAGUE_ID,
                sport=domain.Sport.BASKETBALL,
                name="Pinnacle Basketball League",
                provider=PROVIDER_ID,
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
            ),
        )
        session.add(_in_progress_row(muted_game, TEAM_COMETS))
        session.add(_in_progress_row(open_game, TEAM_STAGS))
        # Mute the Comets; the Stags keep the default (all on).
        await repository.upsert_notification_pref(session, f"team:{TEAM_COMETS}", muted=True)
        await session.commit()

    # Both games go final this tick.
    final_state = lambda gid: GameState(
        game_id=gid,
        phase=GamePhase.FINAL,
        home_score=11,
        away_score=8,
        period=4,
        period_label="Final",
    )
    fake_provider.states = {
        muted_game: final_state(muted_game),
        open_game: final_state(open_game),
    }

    sent: list[str] = []

    async def spy_send_event(event) -> bool:
        sent.append(event.dedupe_key)
        return True

    monkeypatch.setattr(notify, "send_event", spy_send_event)

    await jobs.live_tick()

    # Only the enabled team's FINAL was sent.
    assert f"{open_game}:final" in sent
    assert f"{muted_game}:final" not in sent

    # The suppressed event is NOT marked notified -> it can fire later if
    # the user un-mutes; the sent one is recorded.
    async with db() as session:
        assert await repository.was_notified(session, f"{open_game}:final") is True
        assert await repository.was_notified(session, f"{muted_game}:final") is False
        # State was still applied to both games regardless of the gate.
        muted_row = await repository.get_game(session, muted_game)
        assert muted_row is not None and muted_row.phase == GamePhase.FINAL.value


async def _seed_basketball_league_and_team(
    db: async_sessionmaker[AsyncSession],
) -> None:
    async with db() as session:
        await repository.upsert_league(
            session,
            domain.League(
                id=LEAGUE_ID,
                sport=domain.Sport.BASKETBALL,
                name="Pinnacle Basketball League",
                provider=PROVIDER_ID,
                provider_key="mock-basketball",
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
            ),
        )
        await session.commit()


async def test_live_tick_resends_final_after_first_send_fails(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FINAL whose first send fails is retried by a later tick's resend."""
    game = f"{PROVIDER_ID}:retry-game"
    await _seed_basketball_league_and_team(db)
    async with db() as session:
        session.add(_in_progress_row(game, TEAM_STAGS))
        await session.commit()

    fake_provider.states = {
        game: GameState(
            game_id=game, phase=GamePhase.FINAL,
            home_score=11, away_score=8, period=4, period_label="Final",
        )
    }

    attempts: dict[str, int] = {}

    async def spy(event) -> bool:
        attempts[event.dedupe_key] = attempts.get(event.dedupe_key, 0) + 1
        # The first send for any key fails; later attempts succeed.
        return attempts[event.dedupe_key] > 1

    monkeypatch.setattr(notify, "send_event", spy)

    # Tick 1: the game goes FINAL, the send fails, the state commits but the
    # notification is NOT recorded — and the row is now FINAL so it drops out
    # of the live-poll gate.
    await jobs.live_tick()
    assert attempts.get(f"{game}:final") == 1
    async with db() as session:
        assert await repository.was_notified(session, f"{game}:final") is False
        row = await repository.get_game(session, game)
        assert row is not None and row.phase == GamePhase.FINAL.value

    # Tick 2: the provider no longer offers it (it's final), so only the resend
    # step can deliver it — and it does.
    fake_provider.states = {}
    await jobs.live_tick()
    assert attempts.get(f"{game}:final") == 2
    async with db() as session:
        assert await repository.was_notified(session, f"{game}:final") is True


async def test_live_tick_does_not_resend_when_first_send_succeeded(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FINAL that sent on the first try is never resent (dedupe filter)."""
    game = f"{PROVIDER_ID}:once-game"
    await _seed_basketball_league_and_team(db)
    async with db() as session:
        session.add(_in_progress_row(game, TEAM_STAGS))
        await session.commit()

    fake_provider.states = {
        game: GameState(
            game_id=game, phase=GamePhase.FINAL,
            home_score=11, away_score=8, period=4, period_label="Final",
        )
    }

    attempts: dict[str, int] = {}

    async def spy(event) -> bool:
        attempts[event.dedupe_key] = attempts.get(event.dedupe_key, 0) + 1
        return True

    monkeypatch.setattr(notify, "send_event", spy)

    await jobs.live_tick()  # sends + marks
    fake_provider.states = {}
    await jobs.live_tick()  # resend query excludes the already-sent final

    assert attempts.get(f"{game}:final") == 1


async def test_live_tick_does_not_resend_ancient_final(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FINAL older than the lookback is not resent (no first-deploy flood)."""
    game = f"{PROVIDER_ID}:ancient-game"
    await _seed_basketball_league_and_team(db)
    async with db() as session:
        row = _in_progress_row(game, TEAM_STAGS)
        row.phase = GamePhase.FINAL.value
        row.start_time = utcnow() - timedelta(hours=24)  # older than the 6h lookback
        session.add(row)
        await session.commit()

    sent: list[str] = []

    async def spy(event) -> bool:
        sent.append(event.dedupe_key)
        return True

    monkeypatch.setattr(notify, "send_event", spy)

    await jobs.live_tick()

    assert sent == []  # the recency bound excluded it
    async with db() as session:
        assert await repository.was_notified(session, f"{game}:final") is False
