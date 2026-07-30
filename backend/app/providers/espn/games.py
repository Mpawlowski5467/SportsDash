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

# "Where to watch" ordering: the national feed leads, then the home call,
# then the away call.  Unranked/unknown markets sort after all three.
_BROADCAST_MARKET_ORDER = {"national": 0, "home": 1, "away": 2}
_BROADCASTS_MAX = 6


def _parse_broadcasts(competition: dict[str, Any]) -> tuple[str, ...]:
    """Broadcast network names for a competition, best-known-first.

    Two payload shapes exist upstream.  Scoreboards carry rich entries
    (structured market plus region/lang, so foreign feeds can be
    filtered out) under ``geoBroadcasts`` alongside a flat
    ``broadcasts[].names`` summary; team-schedule payloads have NO
    ``geoBroadcasts`` and instead put the same rich entries directly in
    ``broadcasts``.  So: collect rich US-English entries'
    ``media.shortName`` from BOTH keys, ordered National → Home → Away.
    When that yields nothing, fall back to the flat
    ``broadcasts[].names`` (national market entries first).  Deduped
    preserving order, capped; malformed entries are skipped, never
    fatal.
    """
    ranked: list[tuple[int, str]] = []
    for key in ("geoBroadcasts", "broadcasts"):
        source = competition.get(key)
        if not isinstance(source, list):
            continue
        for entry in source:
            if not isinstance(entry, dict):
                continue
            if entry.get("region") != "us" or entry.get("lang") != "en":
                continue
            media = entry.get("media")
            name = media.get("shortName") if isinstance(media, dict) else None
            if not (isinstance(name, str) and name.strip()):
                continue
            market = entry.get("market")
            market_type = market.get("type") if isinstance(market, dict) else None
            market_key = market_type.strip().lower() if isinstance(market_type, str) else ""
            rank = _BROADCAST_MARKET_ORDER.get(market_key, len(_BROADCAST_MARKET_ORDER))
            ranked.append((rank, name.strip()))

    if not ranked:
        flat = competition.get("broadcasts")
        if isinstance(flat, list):
            for entry in flat:
                if not isinstance(entry, dict):
                    continue
                market = entry.get("market")
                market_key = market.strip().lower() if isinstance(market, str) else ""
                rank = 0 if market_key == "national" else 1
                raw_names = entry.get("names")
                if not isinstance(raw_names, list):
                    continue
                for raw in raw_names:
                    if isinstance(raw, str) and raw.strip():
                        ranked.append((rank, raw.strip()))

    names: list[str] = []
    # sort() is stable, so the feed's own order survives within a market.
    for _, name in sorted(ranked, key=lambda item: item[0]):
        if name not in names:
            names.append(name)
    return tuple(names[:_BROADCASTS_MAX])


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

        broadcasts = _parse_broadcasts(competition)

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
            broadcasts=broadcasts,
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
