"""Hand-rolled RFC 5545 (iCalendar) export of the followed-teams schedule.

Only the small subset of the spec we need: a single ``VCALENDAR`` with one
``VEVENT`` per game, UTC timestamps in basic format, TEXT escaping, CRLF
line endings, and folding of lines longer than 75 octets.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterator, Mapping, Sequence

from app.models.orm import GameORM, LeagueORM
from app.timeutil import ensure_utc, utcnow

logger = logging.getLogger(__name__)

_MAX_LINE_OCTETS = 75

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


def _event_lines(row: GameORM, league: LeagueORM | None, dtstamp: str) -> list[str]:
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


def games_to_ics(rows: Sequence[GameORM], leagues_by_id: Mapping[str, LeagueORM]) -> str:
    """Render games as an iCalendar document (CRLF-terminated string)."""
    dtstamp = _format_utc(utcnow())
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
        lines.extend(_event_lines(row, league, dtstamp))
    lines.append("END:VCALENDAR")

    folded = [physical for logical in lines for physical in _fold(logical)]
    return "\r\n".join(folded) + "\r\n"
