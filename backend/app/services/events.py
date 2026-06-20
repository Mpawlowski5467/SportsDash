"""Sport-agnostic game transition detection.

Pure functions only: no I/O, no database access, and no sport-specific
logic.  Providers normalize period semantics (``period`` /
``period_label`` / ``is_intermission``) before states reach this module,
so a basketball quarter, a soccer half, and a baseball inning are all
just "a period" here.

:func:`diff_states` compares two snapshots of the same game and returns
the notification-worthy transitions in rule order.  Every event carries
a ``dedupe_key`` so callers can guarantee at-most-once delivery across
polls.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.models.domain import EventType, GameEvent, GamePhase, GameState
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

__all__ = ["diff_states", "starting_soon_event"]


def _title(home_name: str, away_name: str) -> str:
    return f"{away_name} @ {home_name}"


def _score_line(state: GameState, *, home_name: str, away_name: str) -> str:
    """Away-first score line, matching the ``away @ home`` title order."""
    return f"{away_name} {state.away_score}, {home_name} {state.home_score}"


def _label_or_period(label: str, period: int) -> str:
    """Prefer the provider's human label, fall back to a generic one."""
    return label or f"Period {period}"


def diff_states(
    prev: GameState | None,
    new: GameState,
    *,
    home_name: str,
    away_name: str,
) -> list[GameEvent]:
    """Detect notification-worthy transitions between two snapshots.

    ``prev`` is the previously stored snapshot (``None`` when the game
    has never been observed live before); ``new`` is the snapshot just
    fetched from the provider.  Multiple events can fire from a single
    diff (e.g. a period bump and the final whistle observed in the same
    poll); they are returned in rule order: GAME_START, PERIOD_START,
    INTERMISSION, FINAL.  Identical snapshots produce no events.
    """
    events: list[GameEvent] = []
    title = _title(home_name, away_name)
    game_id = new.game_id

    # GAME_START: scheduled -> in_progress, or first sighting is live.
    if new.phase is GamePhase.IN_PROGRESS and (
        prev is None or prev.phase is GamePhase.SCHEDULED
    ):
        events.append(
            GameEvent(
                type=EventType.GAME_START,
                game_id=game_id,
                title=title,
                message="The game has started.",
                dedupe_key=f"{game_id}:start",
            )
        )

    # PERIOD_START: the period counter advanced while the game was in
    # progress.  Period 1 is covered by GAME_START.  Requires a previous
    # snapshot — there is nothing to compare against on first sighting.
    if (
        prev is not None
        and (
            prev.phase is GamePhase.IN_PROGRESS
            or new.phase is GamePhase.IN_PROGRESS
        )
        and new.period > prev.period
        and new.period > 1
    ):
        label = _label_or_period(new.period_label, new.period)
        events.append(
            GameEvent(
                type=EventType.PERIOD_START,
                game_id=game_id,
                title=title,
                message=f"{label} is underway.",
                dedupe_key=f"{game_id}:period:{new.period}",
            )
        )

    # INTERMISSION: the intermission flag flipped on while the game is
    # in progress (halftime, between quarters, ...).  The message names
    # the period that just ended, so it needs the previous label.
    if (
        prev is not None
        and prev.phase is GamePhase.IN_PROGRESS
        and new.phase is GamePhase.IN_PROGRESS
        and not prev.is_intermission
        and new.is_intermission
    ):
        prev_label = _label_or_period(prev.period_label, prev.period)
        score = _score_line(new, home_name=home_name, away_name=away_name)
        events.append(
            GameEvent(
                type=EventType.INTERMISSION,
                game_id=game_id,
                title=title,
                message=f"End of {prev_label}. {score}.",
                dedupe_key=f"{game_id}:intermission:{prev.period}",
            )
        )

    # FINAL: the game ended, coming from anything non-final.
    if new.phase is GamePhase.FINAL and (
        prev is None or prev.phase is not GamePhase.FINAL
    ):
        score = _score_line(new, home_name=home_name, away_name=away_name)
        events.append(
            GameEvent(
                type=EventType.FINAL,
                game_id=game_id,
                title=title,
                message=f"Final: {score}.",
                dedupe_key=f"{game_id}:final",
            )
        )

    return events


def starting_soon_event(
    game_row_start_utc: datetime,
    game_id: str,
    *,
    home_name: str,
    away_name: str,
    minutes_out: int,
) -> GameEvent:
    """Build the pre-game "starting soon" event for a scheduled game.

    The message is the one place this module localizes a time: it goes
    straight to the user's phone, which makes it a response boundary —
    a UTC clock reading there would be noise.
    """
    from app.config import get_settings  # local import keeps module import-light

    local_start = ensure_utc(game_row_start_utc).astimezone(get_settings().tzinfo)
    clock = local_start.strftime("%I:%M %p").lstrip("0")
    tz_abbr = local_start.tzname() or ""
    when = f"{clock} {tz_abbr}".rstrip()
    unit = "minute" if minutes_out == 1 else "minutes"
    return GameEvent(
        type=EventType.STARTING_SOON,
        game_id=game_id,
        title=_title(home_name, away_name),
        message=f"Starts in about {minutes_out} {unit} ({when}).",
        dedupe_key=f"{game_id}:soon",
    )
