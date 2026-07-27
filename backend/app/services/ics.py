"""Hand-rolled RFC 5545 (iCalendar) export of the followed-teams schedule.

Only the small subset of the spec we need: a single ``VCALENDAR`` with one
``VEVENT`` per game, UTC timestamps in basic format, TEXT escaping, CRLF
line endings, and folding of lines longer than 75 octets.

Leaderboard competitions (:class:`EventORM` — a golf tournament; golf is
the only sport modeled as an ``Event`` rather than a two-sided ``Game``)
render as ALL-DAY ``VEVENT``s spanning their start..end days, so a
subscribed calendar shows them as a bar across the tournament rather than
a point in time.  Note the vocabulary collision: an iCalendar "VEVENT" is
one calendar entry, which here is either a *game* or a domain *Event*.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models.orm import EventORM, GameORM, LeagueORM
from app.timeutil import ensure_utc, to_local, utcnow

logger = logging.getLogger(__name__)

_MAX_LINE_OCTETS = 75

#: UID prefix for a leaderboard event's entry, mirroring the calendar grid's
#: ``SPAN_ID_PREFIX`` (``views/calendar/eventSpans.ts``) and applied for the
#: same reason: a game id and an event id are both ``"{provider}:{key}"``
#: strings, and one ``VCALENDAR`` holds both kinds side by side, so nothing
#: but the prefix keeps their identities apart.  A UID is what a subscribed
#: client uses to match an entry across refreshes, so a collision would have
#: one kind silently overwrite the other.
_EVENT_UID_PREFIX = "event:"

#: Sport-typical event durations used for DTEND.
_DURATIONS: dict[str, timedelta] = {
    "basketball": timedelta(hours=3),
    "baseball": timedelta(hours=3),
    "soccer": timedelta(hours=2),
    "hockey": timedelta(hours=3),
    "football": timedelta(hours=3, minutes=30),
}
_DEFAULT_DURATION = timedelta(hours=3)


def _escape_text(value: str) -> str:
    """Escape a TEXT property value per RFC 5545 section 3.3.11."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _format_utc(dt: datetime) -> str:
    """UTC basic format: ``YYYYMMDDTHHMMSSZ``."""
    return ensure_utc(dt).strftime("%Y%m%dT%H%M%SZ")


def _format_date(day: date) -> str:
    """DATE value (basic format): ``YYYYMMDD``."""
    return day.strftime("%Y%m%d")


def _fold(line: str) -> Iterator[str]:
    """Fold a content line so no physical line exceeds 75 octets.

    Continuation lines begin with a single space (which counts toward
    their 75-octet budget).  Splits never land inside a multi-byte UTF-8
    sequence because we accumulate whole characters.
    """
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        yield line
        return

    first = True
    chunk: list[str] = []
    chunk_octets = 0
    for char in line:
        char_octets = len(char.encode("utf-8"))
        budget = _MAX_LINE_OCTETS if first else _MAX_LINE_OCTETS - 1
        if chunk_octets + char_octets > budget:
            yield ("" if first else " ") + "".join(chunk)
            first = False
            chunk = [char]
            chunk_octets = char_octets
        else:
            chunk.append(char)
            chunk_octets += char_octets
    if chunk:
        yield ("" if first else " ") + "".join(chunk)


def _game_lines(row: GameORM, league: LeagueORM | None, dtstamp: str) -> list[str]:
    """One timed ``VEVENT`` for a game (start + a sport-typical duration)."""
    sport = league.sport if league is not None else ""
    league_name = league.name if league is not None else row.league_id

    start = ensure_utc(row.start_time)
    end = start + _DURATIONS.get(sport, _DEFAULT_DURATION)

    away_label = row.away_abbreviation or row.away_name
    home_label = row.home_abbreviation or row.home_name
    summary = f"{away_label} @ {home_label} ({league_name})"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{row.id}@sportsdash",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{_format_utc(start)}",
        f"DTEND:{_format_utc(end)}",
        f"SUMMARY:{_escape_text(summary)}",
    ]
    if row.venue:
        lines.append(f"LOCATION:{_escape_text(row.venue)}")
    if row.phase == "final":
        description = f"Final: {row.away_name} {row.away_score}, {row.home_name} {row.home_score}"
        lines.append(f"DESCRIPTION:{_escape_text(description)}")
    lines.append("END:VEVENT")
    return lines


def _winner_line(row: EventORM) -> str | None:
    """DESCRIPTION line naming a finished tournament's winner, or None.

    The stored leaderboard is already ordered by position (the provider
    ranks it and the scheduler stores it verbatim), so the first entry is
    the winner.  Defensive about the JSON column's shape for the same
    reason :func:`serialize.event_to_out` is.
    """
    if row.phase != "final":
        return None
    board = row.leaderboard if isinstance(row.leaderboard, list) else []
    for entry in board:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        score = str(entry.get("score") or "").strip()
        return f"Winner: {name} ({score})" if score else f"Winner: {name}"
    return None


def _event_span_lines(
    row: EventORM, league: LeagueORM | None, dtstamp: str, tz: ZoneInfo
) -> list[str]:
    """One ALL-DAY ``VEVENT`` spanning a leaderboard competition's days.

    A tournament runs over calendar days rather than a clocked window, so
    it uses ``VALUE=DATE`` values in the configured timezone (the same
    response-boundary conversion every other endpoint applies).  RFC 5545
    makes ``DTEND`` EXCLUSIVE for DATE values, so it is the day *after*
    the last day: a Thursday–Sunday major is ``20260716``..``20260720``.
    A null ``end_time`` (or one that predates the start — bad provider
    data) collapses to a single day.
    """
    league_name = league.name if league is not None else row.league_id

    first_day = to_local(row.start_time, tz).date()
    last_day = to_local(row.end_time, tz).date() if row.end_time is not None else first_day
    if last_day < first_day:
        last_day = first_day

    lines = [
        "BEGIN:VEVENT",
        f"UID:{_EVENT_UID_PREFIX}{row.id}@sportsdash",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{_format_date(first_day)}",
        f"DTEND;VALUE=DATE:{_format_date(last_day + timedelta(days=1))}",
        f"SUMMARY:{_escape_text(f'{row.name} ({league_name})')}",
    ]
    if row.venue:
        lines.append(f"LOCATION:{_escape_text(row.venue)}")
    description = _winner_line(row)
    if description is not None:
        lines.append(f"DESCRIPTION:{_escape_text(description)}")
    lines.append("END:VEVENT")
    return lines


def games_to_ics(
    rows: Sequence[GameORM],
    leagues_by_id: Mapping[str, LeagueORM],
    *,
    events: Sequence[EventORM] = (),
) -> str:
    """Render the feed as an iCalendar document (CRLF-terminated string).

    Games become timed ``VEVENT``s; the optional ``events`` (leaderboard
    competitions) become all-day multi-day ones.  ``events`` defaults to
    empty so every existing caller keeps producing a games-only feed.
    """
    dtstamp = _format_utc(utcnow())
    tz = get_settings().tzinfo
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "PRODID:-//SportsDash//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
    ]
    for row in rows:
        league = leagues_by_id.get(row.league_id)
        if league is None:
            logger.warning(
                "Calendar export: game %s references unknown league %s",
                row.id,
                row.league_id,
            )
        lines.extend(_game_lines(row, league, dtstamp))
    for event_row in events:
        event_league = leagues_by_id.get(event_row.league_id)
        if event_league is None:
            logger.warning(
                "Calendar export: event %s references unknown league %s",
                event_row.id,
                event_row.league_id,
            )
        lines.extend(_event_span_lines(event_row, event_league, dtstamp, tz))
    lines.append("END:VCALENDAR")

    folded = [physical for logical in lines for physical in _fold(logical)]
    return "\r\n".join(folded) + "\r\n"
