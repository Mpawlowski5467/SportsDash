"""Individual sports (tennis / MMA): an athlete is a single-member 'team'.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable


from app import timeutil
from app.models.domain import (
    Game,
    GameState,
    League,
    Sport,
    Team,
)

from app.providers.espn.common import (
    _coerce_int,
    _find_side,
    _map_phase,
    _normalize_period,
    _parse_espn_datetime,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual sports (tennis / MMA): an athlete is a single-member "team".
#
# These payloads differ from the team-sport scoreboard in three ways the
# generic ``_parse_event`` can't absorb:
#   * the entity lives in ``competitor.athlete`` (not ``competitor.team``),
#     and the id to match a followed athlete against is ``competitor.id``
#     (``competitor.athlete.id`` is null on the scoreboard — verified live);
#   * tennis nests matches as ``events -> groupings -> competitions`` and
#     ships the whole tournament draw in one payload;
#   * UFC bouts carry NO ``homeAway`` and NO per-fight start time — order
#     1 -> home, 2 -> away, and every bout inherits the card's start time.
# ---------------------------------------------------------------------------


def _athlete_name(competitor: dict[str, Any]) -> str | None:
    athlete = competitor.get("athlete")
    if isinstance(athlete, dict):
        for key in ("displayName", "shortName", "fullName"):
            value = athlete.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _athlete_abbreviation(competitor: dict[str, Any]) -> str | None:
    athlete = competitor.get("athlete")
    if isinstance(athlete, dict):
        value = athlete.get("shortName")
        if isinstance(value, str) and value:
            return value
    return None


def _competitor_id(competitor: dict[str, Any]) -> str:
    """The athlete id used to match a followed athlete (``competitor.id``)."""
    return str(competitor.get("id") or "").strip()


def _individual_sides(
    competitors: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(home, away)`` competitor dicts for a 1-v-1 individual event.

    Tennis competitors carry ``homeAway`` so that wins; UFC bouts don't,
    so fall back to ``order`` (1 -> home, 2 -> away, documented).  Either
    way exactly two sides are required.
    """
    sides = [c for c in competitors if isinstance(c, dict)]
    if len(sides) != 2:
        return None
    home = _find_side(sides, "home")
    away = _find_side(sides, "away")
    if home is not None and away is not None:
        return home, away
    # No homeAway (UFC): order 1 -> home, 2 -> away.  ``order`` is an int
    # in live payloads; sort defensively and treat the lower order as home.
    ordered = sorted(sides, key=lambda c: _coerce_int(c.get("order")) or 0)
    return ordered[0], ordered[1]


def _build_individual_state(
    game_id: str, league: League, competition: dict[str, Any]
) -> GameState | None:
    """GameState for a tennis match / MMA bout (athlete competitors).

    Scores are set/round counts: the number of sets/rounds each side has
    won, taken from per-period ``linescores`` (``winner: true`` entries)
    when present, else 0.  ``_build_state`` can't be reused because it
    reads ``competitor.score`` (absent here) and assumes ``homeAway``.
    """
    sides = _individual_sides(competition.get("competitors") or [])
    if sides is None:
        return None
    home, away = sides

    status = competition.get("status")
    if not isinstance(status, dict):
        status = {}
    raw_type = status.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    status_name = str(stype.get("name") or "")
    phase = _map_phase(str(stype.get("state") or ""), status_name)
    raw_period = _coerce_int(status.get("period")) or 0
    display_clock = status.get("displayClock")
    if not (isinstance(display_clock, str) and display_clock):
        display_clock = None
    detail_text = " ".join(
        str(stype.get(key) or "") for key in ("shortDetail", "detail", "altDetail")
    )
    period, label, clock, intermission = _normalize_period(
        league.sport, phase, raw_period, status_name, display_clock, detail_text
    )
    return GameState(
        game_id=game_id,
        phase=phase,
        home_score=_sets_won(home),
        away_score=_sets_won(away),
        period=period,
        period_label=label,
        clock=clock,
        is_intermission=intermission,
        last_update=timeutil.utcnow(),
    )


def _sets_won(competitor: dict[str, Any]) -> int:
    """Count of won periods (sets/rounds) from a competitor's linescores."""
    linescores = competitor.get("linescores")
    if not isinstance(linescores, list):
        return 0
    return sum(1 for entry in linescores if isinstance(entry, dict) and entry.get("winner") is True)


def _round_label(competition: dict[str, Any]) -> str | None:
    """Tennis round name from ``competition.round`` (e.g. "Quarterfinal")."""
    raw_round = competition.get("round")
    if isinstance(raw_round, dict):
        value = raw_round.get("displayName") or raw_round.get("shortDisplayName")
        if isinstance(value, str) and value:
            return value
    return None


def _tennis_series(event: dict[str, Any], competition: dict[str, Any]) -> str | None:
    """``"Wimbledon · QF"``: tournament name + round when both are known."""
    tournament = event.get("name") or event.get("shortName")
    tournament = tournament if isinstance(tournament, str) and tournament else None
    round_name = _round_label(competition)
    if tournament and round_name:
        return f"{tournament} · {round_name}"
    return tournament or round_name


class _UnresolvedCompetition(Exception):
    """A competition that is not yet a real, resolved 1-v-1 match.

    Tennis tour scoreboards list every bracket slot, including future
    rounds whose competitors are still TBD placeholders (athlete id
    ``-3``/``-4``, no name) and non-singles draws.  These are expected
    empty slots, not malformed data, so they are skipped quietly (no
    warning traceback) rather than logged as parse failures.
    """


def _parse_individual_competition(
    competition: Any,
    event: dict[str, Any],
    league: League,
    *,
    series: str | None,
    fallback_start: datetime | None,
    team: Team | None,
) -> Game | None:
    """Parse one tennis/MMA competition into a :class:`Game`.

    ``fallback_start`` (the card/event date) covers UFC bouts, which carry
    no per-fight start time; tennis competitions carry their own date.
    ``team`` (when given) tags the matching side's internal id.
    """
    try:
        if not isinstance(competition, dict):
            raise ValueError("competition is not an object")
        competition_id = str(competition.get("id") or "").strip()
        if not competition_id:
            raise ValueError("missing competition id")

        start_time = _parse_espn_datetime(competition.get("date")) or fallback_start
        if start_time is None:
            raise ValueError("missing or invalid start time")

        sides = _individual_sides(competition.get("competitors") or [])
        if sides is None:
            raise _UnresolvedCompetition("expected two athlete competitors")
        home, away = sides
        home_name = _athlete_name(home)
        away_name = _athlete_name(away)
        if not home_name or not away_name:
            raise _UnresolvedCompetition("competitors not yet determined")

        game_id = f"espn:{competition_id}"
        state = _build_individual_state(game_id, league, competition)

        raw_venue = competition.get("venue")
        venue = raw_venue.get("fullName") if isinstance(raw_venue, dict) else None
        if not (isinstance(venue, str) and venue):
            venue = None

        home_team_id: str | None = None
        away_team_id: str | None = None
        if team is not None:
            key = str(team.provider_key)
            if _competitor_id(home) == key:
                home_team_id = team.id
            elif _competitor_id(away) == key:
                away_team_id = team.id

        return Game(
            id=game_id,
            league_id=league.id,
            home_name=home_name,
            away_name=away_name,
            start_time=start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_abbreviation=_athlete_abbreviation(home),
            away_abbreviation=_athlete_abbreviation(away),
            venue=venue,
            series=series,
            state=state,
        )
    except _UnresolvedCompetition as exc:
        # Expected empty bracket slot (TBD competitors / non-singles draw),
        # not malformed data: skip without a noisy warning traceback.
        logger.debug(
            "Skipping unresolved ESPN %s competition for league %s: %s",
            league.sport.value,
            league.id,
            exc,
        )
        return None
    except Exception:
        logger.warning(
            "Skipping malformed ESPN %s competition for league %s",
            league.sport.value,
            league.id,
            exc_info=True,
        )
        return None


def _iter_tennis_competitions(
    event: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], str | None]]:
    """Yield ``(competition, series)`` from a tennis event's groupings.

    Tennis nests matches as ``event -> groupings[] -> competitions[]``;
    each grouping is a draw (Men's/Women's Singles, Doubles, ...).
    """
    groupings = event.get("groupings")
    if not isinstance(groupings, list):
        return
    for grouping in groupings:
        if not isinstance(grouping, dict):
            continue
        competitions = grouping.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if isinstance(competition, dict):
                yield competition, _tennis_series(event, competition)


def _parse_tennis_events(
    data: Any,
    league: League,
    team: Team | None = None,
) -> list[Game]:
    """Parse a tennis scoreboard (whole-draw payload) into Games."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    games: list[Game] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for competition, series in _iter_tennis_competitions(event):
            game = _parse_individual_competition(
                competition,
                event,
                league,
                series=series,
                fallback_start=None,
                team=team,
            )
            if game is not None:
                games.append(game)
    return games


def _parse_mma_events(
    data: Any,
    league: League,
    team: Team | None = None,
) -> list[Game]:
    """Parse a UFC scoreboard into Games (one per bout).

    Each event is a fight card with ``competitions[]`` bouts; bouts have
    no per-fight start time, so the card date is used for every bout and
    the card name becomes the series label ("UFC 320").
    """
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    games: list[Game] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        card_name = event.get("name") or event.get("shortName")
        series = card_name if isinstance(card_name, str) and card_name else None
        card_start = _parse_espn_datetime(event.get("date"))
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            game = _parse_individual_competition(
                competition,
                event,
                league,
                series=series,
                fallback_start=card_start,
                team=team,
            )
            if game is not None:
                games.append(game)
    return games


def _parse_individual_scoreboard(data: Any, league: League, team: Team | None = None) -> list[Game]:
    """Dispatch a tennis/MMA scoreboard payload to its sport parser."""
    if league.sport is Sport.TENNIS:
        return _parse_tennis_events(data, league, team)
    return _parse_mma_events(data, league, team)


def _games_for_athlete(games: Iterable[Game], team: Team) -> list[Game]:
    """Keep only the games where ``team`` (an athlete) is a competitor."""
    return [game for game in games if game.home_team_id == team.id or game.away_team_id == team.id]
