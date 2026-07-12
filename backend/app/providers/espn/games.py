"""Team-sport scoreboard/schedule parsers (pure functions over fetched JSON).

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
from typing import Any


from app.models.domain import (
    Game,
    League,
    Team,
)

from app.providers.espn.common import (
    _build_state,
    _find_side,
    _parse_espn_datetime,
    _team_abbreviation,
    _team_color,
    _team_logo,
    _team_name,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure parsers (take already-fetched JSON, never raise on bad records)
# ---------------------------------------------------------------------------


def _parse_event(event: Any, league: League, team: Team | None = None) -> Game | None:
    """Parse a single scoreboard/schedule event; None (+warning) if malformed."""
    try:
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise ValueError("missing event id")
        competitions = event.get("competitions")
        if (
            not isinstance(competitions, list)
            or not competitions
            or not isinstance(competitions[0], dict)
        ):
            raise ValueError("missing competitions")
        competition = competitions[0]

        start_time = _parse_espn_datetime(competition.get("date")) or _parse_espn_datetime(
            event.get("date")
        )
        if start_time is None:
            raise ValueError("missing or invalid start time")

        competitors = competition.get("competitors")
        if not isinstance(competitors, list):
            raise ValueError("missing competitors")
        home = _find_side(competitors, "home")
        away = _find_side(competitors, "away")
        if home is None or away is None:
            raise ValueError("missing home/away competitors")
        raw_home_team = home.get("team")
        raw_away_team = away.get("team")
        home_team: dict[str, Any] = raw_home_team if isinstance(raw_home_team, dict) else {}
        away_team: dict[str, Any] = raw_away_team if isinstance(raw_away_team, dict) else {}
        home_name = _team_name(home_team)
        away_name = _team_name(away_team)
        if not home_name or not away_name:
            raise ValueError("missing team names")

        game_id = f"espn:{event_id}"
        state = _build_state(game_id, league, competition, fallback_status=event.get("status"))

        raw_venue = competition.get("venue")
        venue = raw_venue.get("fullName") if isinstance(raw_venue, dict) else None
        if not (isinstance(venue, str) and venue):
            venue = None

        home_team_id: str | None = None
        away_team_id: str | None = None
        if team is not None:
            if str(home_team.get("id")) == str(team.provider_key):
                home_team_id = team.id
            elif str(away_team.get("id")) == str(team.provider_key):
                away_team_id = team.id

        return Game(
            id=game_id,
            league_id=league.id,
            home_name=home_name,
            away_name=away_name,
            start_time=start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_abbreviation=_team_abbreviation(home_team),
            away_abbreviation=_team_abbreviation(away_team),
            home_logo_url=_team_logo(home_team),
            away_logo_url=_team_logo(away_team),
            home_color=_team_color(home_team),
            away_color=_team_color(away_team),
            venue=venue,
            state=state,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN event for league %s", league.id, exc_info=True)
        return None


def _parse_scoreboard(data: Any, league: League) -> list[Game]:
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    return [game for event in events if (game := _parse_event(event, league)) is not None]


def _parse_schedule(data: Any, league: League, team: Team) -> list[Game]:
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    return [game for event in events if (game := _parse_event(event, league, team)) is not None]
