"""Golf: leaderboard Events (one tournament, a field on a board).

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date
from typing import Any


from app import timeutil
from app.models.domain import (
    Event,
    GamePhase,
    LeaderRow,
    League,
)

from app.providers.espn.common import _coerce_int, _map_phase, _parse_espn_datetime
from app.providers.espn.individual import _athlete_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Golf: a leaderboard Event (one tournament, a field on a board) — NOT a
# two-sided Game.
#
# Verified live against the real PGA feed (RBC Canadian Open, in progress):
#   * one ``events[]`` entry per tournament; ``competitions[0]`` is the
#     tournament, ``competitions[0].competitors[]`` the field (147 players);
#   * each competitor carries ``order`` (1 = leader, pre-sorted), the
#     to-par ``score`` as a STRING ("-10", "E", "+3"), the athlete under
#     ``athlete`` (whose ``id`` is null — the real ESPN athlete id is on
#     ``competitor.id``, same as tennis/MMA), and per-ROUND ``linescores``
#     (one entry per round; each round entry nests per-HOLE linescores);
#   * ties are NOT flagged by ESPN — equal scores must be collapsed to a
#     shared "T{n}" label here;
#   * the tournament round is ``competitions[0].status.type.detail``
#     ("Round 2 - Play Complete") with ``status.period`` as the round number;
#   * "thru N" is derived from the count of per-hole linescores in the
#     golfer's in-progress (last) round.
#
# IMPORTANT — followed-golfer tagging: the provider cannot know which
# golfers are followed, so it carries the ESPN athlete id transiently in
# ``LeaderRow.player_id``.  The SCHEDULER rewrites that field after fetch,
# replacing each ESPN id with the internal followed-team id (or None) before
# the Event is persisted.  Nothing downstream of the scheduler should treat
# a provider-returned ``player_id`` as an internal id.
# ---------------------------------------------------------------------------

# "Round 2 - Play Complete" / "Round 1" -> "Round 2" / "Round 1"; "Final
# Round" and "Playoff" are kept whole (no trailing status clause to trim).
_GOLF_ROUND_RE = re.compile(r"^\s*(round\s+\d+|final\s+round|final|playoff)", re.IGNORECASE)


def _golf_round_label(status: dict[str, Any], period: int) -> str:
    """Tournament round label from golf status detail (defensive).

    Prefers the leading round phrase of ``status.type.detail``
    ("Round 2 - Play Complete" -> "Round 2", "Final Round" -> "Final
    Round"); falls back to ``status.period`` ("Round {n}") when the detail
    carries no recognizable round phrase.
    """
    raw_type = status.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    for key in ("detail", "shortDetail", "description"):
        text = stype.get(key)
        if isinstance(text, str) and text:
            match = _GOLF_ROUND_RE.match(text)
            if match:
                phrase = match.group(1).strip()
                # Normalize casing to title-case round phrases.
                return " ".join(word.capitalize() for word in phrase.split())
    if period > 0:
        return f"Round {period}"
    return ""


def _golf_detail(competitor: dict[str, Any], phase: GamePhase) -> str | None:
    """Per-golfer "thru N" / "F" / round detail from round+hole linescores.

    A golfer's top-level ``linescores`` carry one entry per round; the
    in-progress round's entry nests per-HOLE linescores but no round
    ``value`` yet, so its hole count is "thru N".  A round entry WITH a
    ``value`` is complete.  When the tournament is final the golfer is "F";
    otherwise the most recent round entry decides ("thru N" mid-round, else
    the holes-completed count, else None before tee-off).
    """
    if phase is GamePhase.FINAL:
        return "F"
    linescores = competitor.get("linescores")
    if not isinstance(linescores, list) or not linescores:
        return None
    # Walk back to the most recent round entry that has real play.
    for entry in reversed(linescores):
        if not isinstance(entry, dict):
            continue
        round_value = entry.get("value")
        holes = entry.get("linescores")
        hole_count = len(holes) if isinstance(holes, list) else 0
        if round_value is None and hole_count <= 0:
            # An empty placeholder for a round not yet started — keep looking.
            continue
        if round_value is None and hole_count > 0:
            return f"thru {hole_count}" if hole_count < 18 else "F"
        # Round complete (value present): "F" if 18 holes done, else thru.
        if hole_count and hole_count < 18:
            return f"thru {hole_count}"
        return "F"
    return None


def _golf_score(competitor: dict[str, Any]) -> str:
    """The golfer's to-par/display score as a string ("-10", "E", "+3").

    ESPN ships it as a display string already; coerce non-strings and treat
    a missing/blank value as even par ("E").
    """
    raw = competitor.get("score")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = int(raw)
        if value == 0:
            return "E"
        return f"+{value}" if value > 0 else str(value)
    if isinstance(raw, dict):
        display = raw.get("displayValue")
        if isinstance(display, str) and display.strip():
            return display.strip()
    return "E"


def _leader_row(competitor: dict[str, Any], position: int, phase: GamePhase) -> LeaderRow | None:
    """One :class:`LeaderRow` from a golf competitor; None if malformed.

    ``position`` is the caller-resolved board position (``order``, or the
    sequential fallback when the feed omits it) so the numeric position
    and the label can never disagree.  The label starts bare ("3");
    :func:`_leaderboard` rewrites it to "T3" when the score ties another
    surviving row.

    ``player_id`` carries the ESPN athlete id transiently (the scheduler
    rewrites it to the internal followed-team id or None).
    """
    try:
        athlete_id = str(competitor.get("id") or "").strip()
        name = _athlete_name(competitor)
        if not name:
            raise ValueError("missing golfer name")
        return LeaderRow(
            position=position,
            position_label=str(position),
            name=name,
            score=_golf_score(competitor),
            detail=_golf_detail(competitor, phase),
            player_id=athlete_id or None,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN golf competitor", exc_info=True)
        return None


def _leaderboard(competitors: list[Any], phase: GamePhase) -> tuple[LeaderRow, ...]:
    """Build the leaderboard, deriving shared "T{n}" tie labels.

    ESPN pre-sorts competitors by ``order`` but does NOT flag ties.  The
    board is built in two passes so everything reflects the rows actually
    shown: malformed rows drop out FIRST (a skipped golfer must not tie a
    surviving one), then a score shared by two or more shown rows gets a
    ``T``-prefixed label at the position of the first golfer in the
    group ("T2"); a lone score keeps a bare position ("1").  Position
    numbers come from ``order`` so they stay stable even if a malformed
    row is skipped; when ``order`` is missing, the numeric position
    follows the same sequential board fallback as the label.
    """
    valid = [c for c in competitors if isinstance(c, dict)]
    valid.sort(key=lambda c: _coerce_int(c.get("order")) or 0)

    # Pass 1: drop malformed rows, resolving each position exactly once so
    # the numeric position and the label share the same fallback.
    rows: list[LeaderRow] = []
    for competitor in valid:
        position = _coerce_int(competitor.get("order")) or (len(rows) + 1)
        row = _leader_row(competitor, position, phase)
        if row is not None:
            rows.append(row)

    # Pass 2: count how many shown rows share each display score and mark
    # the ties.
    score_counts: dict[str, int] = {}
    for row in rows:
        score_counts[row.score] = score_counts.get(row.score, 0) + 1

    return tuple(
        replace(row, position_label=f"T{row.position}") if score_counts[row.score] > 1 else row
        for row in rows
    )


def _parse_golf_event(event: Any, league: League) -> Event | None:
    """Parse one golf tournament event into an :class:`Event`; None if bad."""
    try:
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise ValueError("missing event id")
        name = event.get("name") or event.get("shortName")
        if not (isinstance(name, str) and name):
            raise ValueError("missing event name")

        competitions = event.get("competitions")
        competition: dict[str, Any] = {}
        if isinstance(competitions, list) and competitions and isinstance(competitions[0], dict):
            competition = competitions[0]

        start_time = _parse_espn_datetime(competition.get("date")) or _parse_espn_datetime(
            event.get("date")
        )
        if start_time is None:
            raise ValueError("missing or invalid start time")
        end_time = _parse_espn_datetime(competition.get("endDate")) or _parse_espn_datetime(
            event.get("endDate")
        )

        status = competition.get("status")
        if not isinstance(status, dict):
            raw_event_status = event.get("status")
            status = raw_event_status if isinstance(raw_event_status, dict) else {}
        raw_type = status.get("type")
        stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
        phase = _map_phase(str(stype.get("state") or ""), str(stype.get("name") or ""))
        period = _coerce_int(status.get("period")) or 0
        round_label = _golf_round_label(status, period) if phase is not GamePhase.SCHEDULED else ""

        competitors = competition.get("competitors")
        leaderboard = _leaderboard(competitors, phase) if isinstance(competitors, list) else ()

        raw_venue = competition.get("venue")
        venue = raw_venue.get("fullName") if isinstance(raw_venue, dict) else None
        if not (isinstance(venue, str) and venue):
            venue = None

        return Event(
            id=f"espn:{event_id}",
            league_id=league.id,
            name=name,
            start_time=start_time,
            phase=phase,
            end_time=end_time,
            round_label=round_label,
            venue=venue,
            leaderboard=leaderboard,
            last_update=timeutil.utcnow(),
        )
    except Exception:
        logger.warning("Skipping malformed ESPN golf event for league %s", league.id, exc_info=True)
        return None


def _parse_golf_scoreboard(data: Any, league: League) -> list[Event]:
    """Parse a golf scoreboard payload into a list of tournament Events."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    return [event for raw in events if (event := _parse_golf_event(raw, league)) is not None]


def _event_overlaps_window(event: Event, start: date, end: date) -> bool:
    """True when the tournament's day span overlaps ``[start, end]`` inclusive.

    A tournament runs Thu–Sun, so filtering on the start day alone would
    drop an event whose first round preceded the window but which is still
    live within it.  The event's ``[start_time, end_time]`` (end defaults to
    the start day when ESPN omits it) is intersected with the window.
    """
    event_start = event.start_time.date()
    event_end = event.end_time.date() if event.end_time is not None else event_start
    return event_start <= end and event_end >= start
