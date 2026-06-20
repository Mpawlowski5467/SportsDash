"""Unit tests for the sport-agnostic transition detector.

All team/league names are fictional.  ``diff_states`` and
``starting_soon_event`` are pure functions, so these tests need no
database, network, or event loop.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.domain import EventType, GamePhase, GameState
from app.services.events import diff_states, starting_soon_event

# --------------------------------------------------------------------------
# Fictional fixtures
# --------------------------------------------------------------------------

BBALL_GAME_ID = "mock:pbl-2026-0417"
BBALL_HOME = "Ashport Comets"
BBALL_AWAY = "Bramblewick Larks"

SOCCER_GAME_ID = "mock:hfl-2026-0042"
SOCCER_HOME = "Veldhaven SC"
SOCCER_AWAY = "Karst City FC"

BASEBALL_GAME_ID = "mock:cbc-2026-0913"
BASEBALL_HOME = "Drywell Badgers"
BASEBALL_AWAY = "Copperline Crows"


def make_state(
    phase: GamePhase = GamePhase.IN_PROGRESS,
    *,
    game_id: str = BBALL_GAME_ID,
    home: int = 0,
    away: int = 0,
    period: int = 0,
    label: str = "",
    clock: str | None = None,
    intermission: bool = False,
) -> GameState:
    return GameState(
        game_id=game_id,
        phase=phase,
        home_score=home,
        away_score=away,
        period=period,
        period_label=label,
        clock=clock,
        is_intermission=intermission,
    )


def bball_diff(prev: GameState | None, new: GameState) -> list:
    return diff_states(prev, new, home_name=BBALL_HOME, away_name=BBALL_AWAY)


def soccer_diff(prev: GameState | None, new: GameState) -> list:
    return diff_states(prev, new, home_name=SOCCER_HOME, away_name=SOCCER_AWAY)


def baseball_diff(prev: GameState | None, new: GameState) -> list:
    return diff_states(prev, new, home_name=BASEBALL_HOME, away_name=BASEBALL_AWAY)


# --------------------------------------------------------------------------
# Quarter-based flow (basketball-shaped)
# --------------------------------------------------------------------------


class TestQuarterFlow:
    def test_scheduled_to_in_progress_fires_game_start_only(self) -> None:
        """Tip-off jumps period 0 -> 1 but must fire GAME_START only."""
        prev = make_state(GamePhase.SCHEDULED, period=0, label="")
        new = make_state(period=1, label="Q1", clock="11:32")

        events = bball_diff(prev, new)

        assert len(events) == 1
        event = events[0]
        assert event.type is EventType.GAME_START
        assert event.game_id == BBALL_GAME_ID
        assert event.title == "Bramblewick Larks @ Ashport Comets"
        assert event.dedupe_key == f"{BBALL_GAME_ID}:start"

    @pytest.mark.parametrize("period", [2, 3, 4])
    def test_later_quarters_fire_period_start(self, period: int) -> None:
        prev = make_state(
            period=period - 1, label=f"Q{period - 1}", home=20, away=18, clock="00:00"
        )
        new = make_state(
            period=period, label=f"Q{period}", home=20, away=18, clock="12:00"
        )

        events = bball_diff(prev, new)

        assert len(events) == 1
        event = events[0]
        assert event.type is EventType.PERIOD_START
        assert f"Q{period}" in event.message
        assert event.dedupe_key == f"{BBALL_GAME_ID}:period:{period}"

    def test_overtime_fires_period_start_with_label(self) -> None:
        prev = make_state(period=4, label="Q4", home=98, away=98, clock="00:00")
        new = make_state(period=5, label="OT", home=98, away=98, clock="05:00")

        events = bball_diff(prev, new)

        assert [e.type for e in events] == [EventType.PERIOD_START]
        assert "OT" in events[0].message
        assert events[0].dedupe_key == f"{BBALL_GAME_ID}:period:5"

    def test_intermission_carries_prev_label_and_score(self) -> None:
        prev = make_state(period=2, label="Q2", home=51, away=48, clock="00:14")
        new = make_state(
            period=2, label="Q2", home=55, away=50, clock=None, intermission=True
        )

        events = bball_diff(prev, new)

        assert len(events) == 1
        event = events[0]
        assert event.type is EventType.INTERMISSION
        assert "End of Q2" in event.message
        assert "55" in event.message and "50" in event.message
        assert BBALL_HOME in event.message and BBALL_AWAY in event.message
        assert event.dedupe_key == f"{BBALL_GAME_ID}:intermission:2"

    def test_final_fires_with_score(self) -> None:
        prev = make_state(period=4, label="Q4", home=99, away=96, clock="00:03")
        new = make_state(GamePhase.FINAL, period=4, label="Q4", home=101, away=96)

        events = bball_diff(prev, new)

        assert len(events) == 1
        event = events[0]
        assert event.type is EventType.FINAL
        assert "101" in event.message and "96" in event.message
        assert BBALL_HOME in event.message and BBALL_AWAY in event.message
        assert event.dedupe_key == f"{BBALL_GAME_ID}:final"

    def test_score_change_alone_fires_nothing(self) -> None:
        prev = make_state(period=3, label="Q3", home=60, away=58, clock="07:42")
        new = make_state(period=3, label="Q3", home=64, away=61, clock="05:10")

        assert bball_diff(prev, new) == []

    def test_intermission_ending_fires_nothing(self) -> None:
        prev = make_state(period=2, label="Q2", home=55, away=50, intermission=True)
        new = make_state(period=2, label="Q2", home=55, away=50, intermission=False)

        assert bball_diff(prev, new) == []

    def test_period_regression_fires_nothing(self) -> None:
        """A provider glitch rolling the period back must stay silent."""
        prev = make_state(period=3, label="Q3", home=60, away=58)
        new = make_state(period=2, label="Q2", home=60, away=58)

        assert bball_diff(prev, new) == []


# --------------------------------------------------------------------------
# Halves flow (soccer-shaped)
# --------------------------------------------------------------------------


class TestHalvesFlow:
    def test_kickoff_fires_game_start(self) -> None:
        prev = make_state(GamePhase.SCHEDULED, game_id=SOCCER_GAME_ID)
        new = make_state(
            game_id=SOCCER_GAME_ID, period=1, label="1st Half", clock="03:12"
        )

        events = soccer_diff(prev, new)

        assert [e.type for e in events] == [EventType.GAME_START]
        assert events[0].title == "Karst City FC @ Veldhaven SC"
        assert events[0].dedupe_key == f"{SOCCER_GAME_ID}:start"

    def test_halftime_fires_intermission_with_score(self) -> None:
        prev = make_state(
            game_id=SOCCER_GAME_ID, period=1, label="1st Half", home=1, away=0
        )
        new = make_state(
            game_id=SOCCER_GAME_ID,
            period=1,
            label="1st Half",
            home=1,
            away=0,
            intermission=True,
        )

        events = soccer_diff(prev, new)

        assert len(events) == 1
        event = events[0]
        assert event.type is EventType.INTERMISSION
        assert "End of 1st Half" in event.message
        assert SOCCER_HOME in event.message and SOCCER_AWAY in event.message
        assert event.dedupe_key == f"{SOCCER_GAME_ID}:intermission:1"

    def test_second_half_fires_period_start(self) -> None:
        prev = make_state(
            game_id=SOCCER_GAME_ID,
            period=1,
            label="1st Half",
            home=1,
            away=0,
            intermission=True,
        )
        new = make_state(
            game_id=SOCCER_GAME_ID, period=2, label="2nd Half", home=1, away=0
        )

        events = soccer_diff(prev, new)

        assert [e.type for e in events] == [EventType.PERIOD_START]
        assert "2nd Half" in events[0].message
        assert events[0].dedupe_key == f"{SOCCER_GAME_ID}:period:2"

    def test_full_time_fires_final_with_score(self) -> None:
        prev = make_state(
            game_id=SOCCER_GAME_ID, period=2, label="2nd Half", home=2, away=1
        )
        new = make_state(
            GamePhase.FINAL,
            game_id=SOCCER_GAME_ID,
            period=2,
            label="2nd Half",
            home=2,
            away=1,
        )

        events = soccer_diff(prev, new)

        assert [e.type for e in events] == [EventType.FINAL]
        message = events[0].message
        assert "Karst City FC 1" in message
        assert "Veldhaven SC 2" in message
        assert events[0].dedupe_key == f"{SOCCER_GAME_ID}:final"


# --------------------------------------------------------------------------
# Innings flow (baseball-shaped)
# --------------------------------------------------------------------------


class TestInningsFlow:
    def test_half_inning_flip_same_period_fires_nothing(self) -> None:
        """'Top 5' -> 'Bot 5' keeps period=5: must NOT fire PERIOD_START."""
        prev = make_state(
            game_id=BASEBALL_GAME_ID, period=5, label="Top 5", home=2, away=3
        )
        new = make_state(
            game_id=BASEBALL_GAME_ID, period=5, label="Bot 5", home=2, away=4
        )

        assert baseball_diff(prev, new) == []

    def test_new_inning_fires_period_start(self) -> None:
        prev = make_state(
            game_id=BASEBALL_GAME_ID, period=5, label="Bot 5", home=2, away=4
        )
        new = make_state(
            game_id=BASEBALL_GAME_ID, period=6, label="Top 6", home=2, away=4
        )

        events = baseball_diff(prev, new)

        assert [e.type for e in events] == [EventType.PERIOD_START]
        assert "Top 6" in events[0].message
        assert events[0].dedupe_key == f"{BASEBALL_GAME_ID}:period:6"

    def test_final_after_nine_fires_final(self) -> None:
        prev = make_state(
            game_id=BASEBALL_GAME_ID, period=9, label="Bot 9", home=5, away=4
        )
        new = make_state(
            GamePhase.FINAL,
            game_id=BASEBALL_GAME_ID,
            period=9,
            label="Bot 9",
            home=6,
            away=4,
        )

        events = baseball_diff(prev, new)

        assert [e.type for e in events] == [EventType.FINAL]
        assert "6" in events[0].message and "4" in events[0].message
        assert events[0].dedupe_key == f"{BASEBALL_GAME_ID}:final"


# --------------------------------------------------------------------------
# prev=None (first sighting)
# --------------------------------------------------------------------------


class TestPrevNone:
    def test_none_to_live_fires_game_start(self) -> None:
        new = make_state(period=1, label="Q1", home=4, away=2, clock="09:55")

        events = bball_diff(None, new)

        assert [e.type for e in events] == [EventType.GAME_START]
        assert events[0].dedupe_key == f"{BBALL_GAME_ID}:start"

    def test_none_to_live_mid_game_fires_game_start_only(self) -> None:
        """No previous period to compare against: no PERIOD_START."""
        new = make_state(period=3, label="Q3", home=58, away=61, clock="06:00")

        events = bball_diff(None, new)

        assert [e.type for e in events] == [EventType.GAME_START]

    def test_none_to_scheduled_fires_nothing(self) -> None:
        new = make_state(GamePhase.SCHEDULED)

        assert bball_diff(None, new) == []

    def test_none_to_final_fires_final(self) -> None:
        """None is 'anything non-final', so a first-sighting final fires."""
        new = make_state(GamePhase.FINAL, period=4, label="Q4", home=110, away=104)

        events = bball_diff(None, new)

        assert [e.type for e in events] == [EventType.FINAL]
        assert events[0].dedupe_key == f"{BBALL_GAME_ID}:final"


# --------------------------------------------------------------------------
# Multiple simultaneous transitions in one diff
# --------------------------------------------------------------------------


class TestMultipleTransitions:
    def test_missed_start_fires_game_start_and_period_start(self) -> None:
        """Scheduled straight to Q2: both events, in rule order."""
        prev = make_state(GamePhase.SCHEDULED, period=0, label="")
        new = make_state(period=2, label="Q2", home=28, away=25, clock="10:00")

        events = bball_diff(prev, new)

        assert [e.type for e in events] == [
            EventType.GAME_START,
            EventType.PERIOD_START,
        ]
        assert events[0].dedupe_key == f"{BBALL_GAME_ID}:start"
        assert events[1].dedupe_key == f"{BBALL_GAME_ID}:period:2"
        assert "Q2" in events[1].message

    def test_period_bump_and_final_in_same_poll(self) -> None:
        """Q3 -> final-in-Q4 in one poll: PERIOD_START then FINAL."""
        prev = make_state(period=3, label="Q3", home=70, away=72, clock="00:30")
        new = make_state(GamePhase.FINAL, period=4, label="Q4", home=95, away=99)

        events = bball_diff(prev, new)

        assert [e.type for e in events] == [
            EventType.PERIOD_START,
            EventType.FINAL,
        ]
        assert events[0].dedupe_key == f"{BBALL_GAME_ID}:period:4"
        assert events[1].dedupe_key == f"{BBALL_GAME_ID}:final"
        assert "95" in events[1].message and "99" in events[1].message

    def test_intermission_with_final_phase_fires_final_only(self) -> None:
        """An intermission flag on a final snapshot must not fire INTERMISSION."""
        prev = make_state(period=4, label="Q4", home=88, away=84)
        new = make_state(
            GamePhase.FINAL, period=4, label="Q4", home=90, away=84, intermission=True
        )

        events = bball_diff(prev, new)

        assert [e.type for e in events] == [EventType.FINAL]


# --------------------------------------------------------------------------
# Idempotence + non-events
# --------------------------------------------------------------------------


class TestIdempotence:
    @pytest.mark.parametrize(
        "snapshot",
        [
            make_state(GamePhase.SCHEDULED),
            make_state(period=2, label="Q2", home=40, away=37, clock="04:30"),
            make_state(period=2, label="Q2", home=44, away=39, intermission=True),
            make_state(GamePhase.FINAL, period=4, label="Q4", home=100, away=92),
            make_state(GamePhase.POSTPONED),
            make_state(GamePhase.CANCELED),
        ],
        ids=["scheduled", "in_progress", "intermission", "final", "postponed", "canceled"],
    )
    def test_identical_states_fire_nothing(self, snapshot: GameState) -> None:
        assert bball_diff(snapshot, snapshot) == []

    def test_scheduled_to_postponed_fires_nothing(self) -> None:
        prev = make_state(GamePhase.SCHEDULED)
        new = make_state(GamePhase.POSTPONED)

        assert bball_diff(prev, new) == []

    def test_scheduled_to_canceled_fires_nothing(self) -> None:
        prev = make_state(GamePhase.SCHEDULED)
        new = make_state(GamePhase.CANCELED)

        assert bball_diff(prev, new) == []


# --------------------------------------------------------------------------
# Dedupe keys, verbatim per contract
# --------------------------------------------------------------------------


class TestDedupeKeys:
    def test_all_dedupe_keys_exact(self) -> None:
        gid = BBALL_GAME_ID

        start = bball_diff(
            make_state(GamePhase.SCHEDULED),
            make_state(period=1, label="Q1"),
        )[0]
        assert start.dedupe_key == f"{gid}:start"

        period = bball_diff(
            make_state(period=2, label="Q2"),
            make_state(period=3, label="Q3"),
        )[0]
        assert period.dedupe_key == f"{gid}:period:3"

        intermission = bball_diff(
            make_state(period=3, label="Q3"),
            make_state(period=3, label="Q3", intermission=True),
        )[0]
        assert intermission.dedupe_key == f"{gid}:intermission:3"

        final = bball_diff(
            make_state(period=4, label="Q4"),
            make_state(GamePhase.FINAL, period=4, label="Q4"),
        )[0]
        assert final.dedupe_key == f"{gid}:final"

        soon = starting_soon_event(
            datetime(2026, 6, 11, 23, 30, tzinfo=timezone.utc),
            gid,
            home_name=BBALL_HOME,
            away_name=BBALL_AWAY,
            minutes_out=15,
        )
        assert soon.dedupe_key == f"{gid}:soon"


# --------------------------------------------------------------------------
# starting_soon_event shape
# --------------------------------------------------------------------------


class TestStartingSoon:
    def test_shape(self) -> None:
        start = datetime(2026, 6, 11, 23, 30, tzinfo=timezone.utc)

        event = starting_soon_event(
            start,
            BBALL_GAME_ID,
            home_name=BBALL_HOME,
            away_name=BBALL_AWAY,
            minutes_out=15,
        )

        assert event.type is EventType.STARTING_SOON
        assert event.game_id == BBALL_GAME_ID
        assert event.title == "Bramblewick Larks @ Ashport Comets"
        assert "15" in event.message
        assert event.dedupe_key == f"{BBALL_GAME_ID}:soon"

    def test_accepts_naive_datetime(self) -> None:
        """SQLite rows yield naive datetimes; ensure_utc must absorb them."""
        naive_start = datetime(2026, 6, 11, 23, 30)

        event = starting_soon_event(
            naive_start,
            SOCCER_GAME_ID,
            home_name=SOCCER_HOME,
            away_name=SOCCER_AWAY,
            minutes_out=1,
        )

        assert event.type is EventType.STARTING_SOON
        assert "1 minute" in event.message
        assert event.dedupe_key == f"{SOCCER_GAME_ID}:soon"
