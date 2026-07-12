"""ESPN provider adapter.

Maps ESPN's public site API payloads into the normalized domain models.
All parsing is implemented as pure functions over already-fetched JSON
dicts so the normalization logic is unit-testable without any network
I/O; the async protocol methods only fetch and delegate.

``League.provider_key`` is the ESPN sport/league URL fragment (e.g.
``"basketball/nba"``); ``Team.provider_key`` is the ESPN team id.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import httpx

from app import timeutil
from app.config import get_settings
from app.providers.http_util import TransientProviderError, get_with_retry
from app.models.domain import (
    INDIVIDUAL_SPORTS,
    LEADERBOARD_SPORTS,
    Event,
    Game,
    GameOdds,
    GamePhase,
    GamePlay,
    GameState,
    GameSummary,
    Goal,
    LeaderRow,
    League,
    NewsItem,
    PeriodScore,
    Performer,
    Player,
    PlayerStatus,
    Roster,
    Sport,
    StandingRow,
    Standings,
    Team,
    TeamLocation,
    TeamStat,
)

logger = logging.getLogger(__name__)

_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports"
# The athlete "overview" endpoint returns a compact, ESPN-resolved
# current-season line (labeled ``statistics.names`` + per-split values)
# in a single call per athlete, with no season/seasontype to guess.
_WEB_BASE = "https://site.web.api.espn.com/apis/common/v3/sports"
# The core API hosts the per-game win-probability "predictor" feed
# (``.../events/{id}/competitions/{id}/predictor``), on a different host
# than the site API but reachable through the same shared httpx client.
_CORE_BASE = "https://sports.core.api.espn.com/v2/sports"

# Cap how many athletes a single roster refresh probes for a stat line,
# and how many overview calls run at once, to keep the daily refresh
# bounded even for deep rosters across many followed teams.
_STAT_LINE_MAX_ATHLETES = 40
_STAT_LINE_CONCURRENCY = 6

# ESPN buckets scoreboard days by US/Eastern, not UTC.  Asking for the
# UTC date after 8pm ET would return the next day's slate and silently
# miss every live evening game.
_SCOREBOARD_TZ = ZoneInfo("America/New_York")

_BASEBALL_HALF_RE = re.compile(r"\b(top|bottom|bot|mid|middle|end)\b[^0-9]*?(\d+)", re.IGNORECASE)

# Hockey shootouts as ESPN spells them in status detail/altDetail:
# "Final/SO", altDetail "SO" (verified live).  Word-bounded so it never
# matches inside longer tokens ("SEASON", team abbreviations, ...).
_HOCKEY_SHOOTOUT_RE = re.compile(r"\bSO\b")


# ---------------------------------------------------------------------------
# Low-level coercion helpers
# ---------------------------------------------------------------------------


def _parse_espn_datetime(value: Any) -> datetime | None:
    """Parse ESPN's ISO-ish timestamps (e.g. ``2026-06-11T23:30Z``)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return timeutil.ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    """Best-effort int from ESPN's many score encodings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    if isinstance(value, dict):
        inner = value.get("value")
        if inner is None:
            inner = value.get("displayValue")
        return _coerce_int(inner)
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _team_name(team_obj: dict[str, Any]) -> str | None:
    for key in ("displayName", "shortDisplayName", "name", "location"):
        value = team_obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _team_abbreviation(team_obj: dict[str, Any]) -> str | None:
    value = team_obj.get("abbreviation")
    if isinstance(value, str) and value:
        return value
    return None


def _team_logo(team_obj: dict[str, Any]) -> str | None:
    """First ``team.logos[].href`` (or a bare ``team.logo``), else None.

    ESPN standings/scoreboard team objects carry the same ``logos`` array
    the catalog reads, so club crests and national-team flags are available
    on every standings row — not just followed teams.
    """
    logos = team_obj.get("logos")
    if isinstance(logos, list) and logos and isinstance(logos[0], dict):
        href = logos[0].get("href")
        if isinstance(href, str) and href:
            return href
    single = team_obj.get("logo")
    if isinstance(single, str) and single:
        return single
    return None


def _team_color(team_obj: dict[str, Any]) -> str | None:
    """ESPN colors are bare hex ("1d428a"); normalize to "#"-prefixed."""
    value = team_obj.get("color")
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("#"):
        return text
    if re.fullmatch(r"[0-9a-fA-F]{3,8}", text):
        return f"#{text}"
    return None


def _find_side(competitors: list[Any], side: str) -> dict[str, Any] | None:
    for competitor in competitors:
        if isinstance(competitor, dict) and competitor.get("homeAway") == side:
            return competitor
    return None


def _linescore_periods(competitors: list[Any]) -> int:
    """Periods played, inferred from per-period competitor linescores.

    Summary headers omit ``status.period`` for finished games across all
    sports; the linescores (one entry per quarter/half/inning) still
    reveal how many periods were played.
    """
    periods = 0
    for competitor in competitors:
        if isinstance(competitor, dict):
            linescores = competitor.get("linescores")
            if isinstance(linescores, list):
                periods = max(periods, len(linescores))
    return periods


# ---------------------------------------------------------------------------
# Status / period normalization
# ---------------------------------------------------------------------------


def _map_phase(state: str, type_name: str) -> GamePhase:
    upper = type_name.upper()
    if "POSTPON" in upper:
        return GamePhase.POSTPONED
    if "CANCEL" in upper:
        return GamePhase.CANCELED
    if state == "pre":
        return GamePhase.SCHEDULED
    if state == "in":
        return GamePhase.IN_PROGRESS
    if state == "post":
        return GamePhase.FINAL
    # Fall back to the status name when ``state`` is missing.
    if "FINAL" in upper:
        return GamePhase.FINAL
    if "HALFTIME" in upper or "PROGRESS" in upper:
        return GamePhase.IN_PROGRESS
    return GamePhase.SCHEDULED


def _quarter_label(period: int) -> str:
    """Quarter labels shared by basketball and American football."""
    if period <= 0:
        return ""
    if period <= 4:
        return f"Q{period}"
    overtime = period - 4
    return "OT" if overtime == 1 else f"{overtime}OT"


def _hockey_label(period: int) -> str:
    if period <= 0:
        return ""
    if period <= 3:
        return f"P{period}"
    overtime = period - 3
    return "OT" if overtime == 1 else f"{overtime}OT"


def _normalize_period(
    sport: Sport,
    phase: GamePhase,
    period: int,
    status_name: str,
    display_clock: str | None,
    detail_text: str,
) -> tuple[int, str, str | None, bool]:
    """Return ``(period, period_label, clock, is_intermission)`` per sport."""
    if phase in (GamePhase.SCHEDULED, GamePhase.POSTPONED, GamePhase.CANCELED):
        # Real ESPN payloads report ``period: 1`` for pre-game baseball
        # (and postponed games), which would leak a bogus "Inning 1"
        # label; the domain contract wants period 0 before tip-off.
        return 0, "", None, False
    live = phase is GamePhase.IN_PROGRESS
    upper = status_name.upper()

    if sport in (Sport.BASKETBALL, Sport.FOOTBALL):
        # Football quarters map exactly like basketball ("Q1".."Q4",
        # period 5 → "OT"); STATUS_HALFTIME / quarter-break statuses are
        # intermissions for both.
        intermission = live and (
            "HALFTIME" in upper or "END_PERIOD" in upper or "END_OF_PERIOD" in upper
        )
        clock = display_clock if (live and not intermission) else None
        return period, _quarter_label(period), clock, intermission

    if sport is Sport.HOCKEY:
        upper_detail = detail_text.upper()
        # Shootout: prefer the detail/altDetail spelling ("Final/SO",
        # "SO") or an explicit shootout status over the raw period —
        # period 5 is a shootout in the regular season but 2OT in the
        # playoffs, and only the detail text disambiguates.
        shootout = (
            "SHOOTOUT" in upper
            or "SHOOTOUT" in upper_detail
            or _HOCKEY_SHOOTOUT_RE.search(upper_detail) is not None
        )
        # Between-period breaks: live + an END_PERIOD-style status name,
        # or a detail like "End of 1st Period".  (Defensive: the live
        # intermission shape is unverified until the next live window.)
        intermission = (
            live
            and not shootout
            and ("END_PERIOD" in upper or "END_OF_PERIOD" in upper or "END OF" in upper_detail)
        )
        label = "SO" if shootout else _hockey_label(period)
        clock = display_clock if (live and not intermission) else None
        return period, label, clock, intermission

    if sport is Sport.SOCCER:
        intermission = live and "HALFTIME" in upper
        if period <= 0:
            label = ""
        elif period == 1:
            label = "1st Half"
        elif period == 2:
            label = "2nd Half"
        else:
            label = "Extra Time"
        clock = display_clock if live else None
        return period, label, clock, intermission

    if sport is Sport.TENNIS:
        # Tennis: a "period" is a set ("Set 2"); no clock, no
        # intermission (sets have no break ESPN reports as a state).
        label = f"Set {period}" if period > 0 else ""
        return period, label, None, False

    if sport is Sport.MMA:
        # MMA: a "period" is a round ("R3"); clock counts down while live.
        label = f"R{period}" if period > 0 else ""
        clock = display_clock if live else None
        return period, label, clock, False

    if sport is Sport.BASEBALL:
        # Baseball: period == inning, label derived from shortDetail/detail.
        intermission = False
        match = _BASEBALL_HALF_RE.search(detail_text)
        if match:
            half = match.group(1).lower()
            inning = int(match.group(2))
            if inning > 0:
                period = inning
            if half == "top":
                label = f"Top {inning}"
            elif half in ("bot", "bottom"):
                label = f"Bot {inning}"
            elif half in ("mid", "middle"):
                label = f"Mid {inning}"
                intermission = live
            else:
                label = f"End {inning}"
                intermission = live
        elif period > 0:
            label = f"Inning {period}"
        else:
            label = ""
        return period, label, None, intermission

    # Unknown sport (future enum member without a branch yet): degrade to
    # a generic period label instead of borrowing another sport's rules.
    label = f"Period {period}" if period > 0 else ""
    return period, label, display_clock if live else None, False


def _build_state(
    game_id: str,
    league: League,
    competition: dict[str, Any],
    fallback_status: Any = None,
) -> GameState | None:
    """Build a normalized :class:`GameState` from an ESPN competition dict."""
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        return None
    home = _find_side(competitors, "home")
    away = _find_side(competitors, "away")
    if home is None or away is None:
        return None

    status = competition.get("status")
    if not isinstance(status, dict):
        status = fallback_status if isinstance(fallback_status, dict) else {}
    raw_type = status.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}

    status_name = str(stype.get("name") or "")
    phase = _map_phase(str(stype.get("state") or ""), status_name)
    raw_period = _coerce_int(status.get("period")) or 0
    if raw_period <= 0 and phase is GamePhase.FINAL:
        raw_period = _linescore_periods(competitors)
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
        home_score=_coerce_int(home.get("score")) or 0,
        away_score=_coerce_int(away.get("score")) or 0,
        period=period,
        period_label=label,
        clock=clock,
        is_intermission=intermission,
        last_update=timeutil.utcnow(),
    )


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


def _leader_row(
    competitor: dict[str, Any], position_label: str, phase: GamePhase
) -> LeaderRow | None:
    """One :class:`LeaderRow` from a golf competitor; None if malformed.

    ``player_id`` carries the ESPN athlete id transiently (the scheduler
    rewrites it to the internal followed-team id or None).
    """
    try:
        athlete_id = str(competitor.get("id") or "").strip()
        name = _athlete_name(competitor)
        if not name:
            raise ValueError("missing golfer name")
        position = _coerce_int(competitor.get("order")) or 0
        return LeaderRow(
            position=position,
            position_label=position_label,
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

    ESPN pre-sorts competitors by ``order`` but does NOT flag ties.  Rows
    are grouped by identical display ``score`` in finishing order: a score
    shared by two or more golfers gets a ``T``-prefixed label at the
    position of the first golfer in the group ("T2"); a lone score keeps a
    bare position ("1").  Position numbers come from ``order`` so they stay
    stable even if a malformed row is skipped.
    """
    valid = [c for c in competitors if isinstance(c, dict)]
    valid.sort(key=lambda c: _coerce_int(c.get("order")) or 0)

    # Count how many golfers share each display score so ties can be marked.
    score_counts: dict[str, int] = {}
    for competitor in valid:
        score_counts[_golf_score(competitor)] = score_counts.get(_golf_score(competitor), 0) + 1

    rows: list[LeaderRow] = []
    for competitor in valid:
        position = _coerce_int(competitor.get("order")) or (len(rows) + 1)
        score = _golf_score(competitor)
        tied = score_counts.get(score, 0) > 1
        label = f"T{position}" if tied else str(position)
        row = _leader_row(competitor, label, phase)
        if row is not None:
            rows.append(row)
    return tuple(rows)


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


def _merge_games(*batches: Iterable[Game]) -> list[Game]:
    """Merge game lists by game id, preserving first-seen order.

    On duplicate ids the EARLIER batch wins: callers pass the
    completed-results batch first so a game that just finished keeps its
    final state even if a stale fixtures payload still lists it as
    scheduled.
    """
    merged: dict[str, Game] = {}
    for batch in batches:
        for game in batch:
            merged.setdefault(game.id, game)
    return list(merged.values())


# A single ranged scoreboard call comfortably returns a whole tournament
# (verified: the full World Cup span — 104 matches over ~39 days — comes
# back in one ``?dates=YYYYMMDD-YYYYMMDD&limit=400`` call).  Domestic
# seasons span months, though, so ranges longer than this are split into
# month-aligned chunks to bound each payload.
_COMPETITION_CHUNK_DAYS = 45


def _add_month(day: date) -> date:
    """First day of the calendar month after ``day``'s month."""
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _chunk_date_range(
    start: date, end: date, max_days: int = _COMPETITION_CHUNK_DAYS
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into inclusive sub-ranges, by month when long.

    A range spanning at most ``max_days`` is returned unchanged as a
    single chunk.  Longer ranges are cut on calendar-month boundaries so
    each ESPN ``dates=YYYYMMDD-YYYYMMDD`` call covers at most one month —
    payloads stay small and the chunks tile ``[start, end]`` exactly with
    no gaps or overlaps.  An inverted range (``end < start``) yields no
    chunks.
    """
    if end < start:
        return []
    if (end - start).days <= max_days:
        return [(start, end)]
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        month_end = _add_month(cursor) - timedelta(days=1)
        chunk_end = month_end if month_end < end else end
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


# Sports whose ESPN team schedules are split across multiple calls; each
# entry lists the query param sets to fetch and merge (first set wins on
# duplicate game ids).  Soccer: the bare endpoint returns completed
# results only, ``?fixture=true`` the upcoming fixtures only.  Hockey and
# football: the bare endpoint defaults to the CURRENT season phase only
# (playoffs-only during the playoffs — verified live), so regular season
# (seasontype 2) and postseason (3) must both be requested explicitly;
# out of season one of them is simply empty.
_SCHEDULE_PARAM_SETS: dict[Sport, tuple[dict[str, str] | None, ...]] = {
    Sport.SOCCER: (None, {"fixture": "true"}),
    Sport.HOCKEY: ({"seasontype": "2"}, {"seasontype": "3"}),
    Sport.FOOTBALL: ({"seasontype": "2"}, {"seasontype": "3"}),
}


def _parse_summary_state(data: Any, league: League, provider_game_key: str) -> GameState | None:
    if not isinstance(data, dict):
        return None
    header = data.get("header")
    if not isinstance(header, dict):
        return None
    competitions = header.get("competitions")
    if (
        not isinstance(competitions, list)
        or not competitions
        or not isinstance(competitions[0], dict)
    ):
        return None
    try:
        return _build_state(f"espn:{provider_game_key}", league, competitions[0])
    except Exception:
        logger.warning("Failed to parse ESPN summary for game %s", provider_game_key, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Box score / game summary (on-demand drill-down, never stored)
#
# The summary endpoint already used for ``get_game_state`` also carries the
# per-period line score (``header.competitions[0].competitors[].linescores``)
# and a top-level ``leaders[]`` block (per team, per stat category).  This
# parses both, best-effort: a missing/empty boxscore yields empty
# ``periods``; missing leaders yield empty ``performers``.  Nothing here
# raises — the provider returns ``None`` on any failure (see
# ``get_game_summary``).
# ---------------------------------------------------------------------------


def _box_period_label(sport: Sport, index: int, count: int) -> str:
    """Column label for the ``index``-th period (0-based) of ``count`` total.

    Per sport: basketball/football ``Q1..Q4`` then ``OT`` (regulation is 4
    quarters), hockey ``P1..P3`` then ``OT``, soccer ``1st``/``2nd`` halves
    then ``ET``, tennis/volleyball ``Set n``, baseball the inning number.
    Anything beyond regulation collapses to a single ``OT`` (or ``2OT`` …
    when several overtime columns are present), matching the live-state
    ``period_label`` convention.
    """
    number = index + 1
    if sport in (Sport.BASKETBALL, Sport.FOOTBALL):
        # _quarter_label maps period 5 -> "OT", 6 -> "2OT": reuse it so the
        # box score and the live header agree.
        return _quarter_label(number)
    if sport is Sport.HOCKEY:
        return _hockey_label(number)
    if sport is Sport.SOCCER:
        if number == 1:
            return "1st"
        if number == 2:
            return "2nd"
        # A third+ column is extra time (penalties show up as a final col).
        extra = number - 2
        return "ET" if extra == 1 else f"ET{extra}"
    if sport in (Sport.TENNIS, Sport.VOLLEYBALL):
        return f"Set {number}"
    if sport is Sport.BASEBALL:
        return str(number)
    # Unknown sport: generic 1-based period label.
    return str(number)


def _parse_period_scores(competition: dict[str, Any], sport: Sport) -> list[PeriodScore]:
    """Per-period line scores for both sides from the summary header.

    ESPN ships one ``linescores`` entry per period on each competitor; the
    two sides are aligned by index and zipped into a :class:`PeriodScore`
    with a per-sport column label.  A side that played fewer periods (e.g.
    a home team that skips the bottom of the 9th) is padded with 0 so the
    columns stay aligned.  Returns ``[]`` when no usable linescores exist.
    """
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        return []
    home = _find_side(competitors, "home")
    away = _find_side(competitors, "away")
    if home is None or away is None:
        return []
    home_lines = _linescore_values(home)
    away_lines = _linescore_values(away)
    count = max(len(home_lines), len(away_lines))
    if count <= 0:
        return []
    periods: list[PeriodScore] = []
    for index in range(count):
        home_pts = home_lines[index] if index < len(home_lines) else 0
        away_pts = away_lines[index] if index < len(away_lines) else 0
        periods.append(
            PeriodScore(
                label=_box_period_label(sport, index, count),
                home=home_pts,
                away=away_pts,
            )
        )
    return periods


def _linescore_values(competitor: dict[str, Any]) -> list[int]:
    """A competitor's per-period scores as ints (``value``/``displayValue``)."""
    linescores = competitor.get("linescores")
    if not isinstance(linescores, list):
        return []
    values: list[int] = []
    for entry in linescores:
        if isinstance(entry, dict):
            points = _coerce_int(entry.get("value"))
            if points is None:
                points = _coerce_int(entry.get("displayValue"))
            values.append(points or 0)
        else:
            values.append(_coerce_int(entry) or 0)
    return values


def _leader_detail(leader: dict[str, Any], category: dict[str, Any]) -> str | None:
    """A short stat line for one leader, e.g. ``"31 PTS"`` / ``"2 Goals"``.

    Prefers ESPN's ``mainStat`` (``{value, label}`` -> "31 PTS"); falls
    back to the leader ``displayValue`` paired with the category's
    ``displayName``/``name`` ("2 Points"), then to the free-text
    ``summary``.  Returns ``None`` when nothing usable is present.
    """
    main_stat = leader.get("mainStat")
    if isinstance(main_stat, dict):
        value = main_stat.get("value")
        label = main_stat.get("label")
        value_text = str(value).strip() if value not in (None, "") else ""
        label_text = str(label).strip() if isinstance(label, str) else ""
        if value_text and label_text:
            return f"{value_text} {label_text}"
        if value_text:
            return value_text
    display_value = leader.get("displayValue")
    value_text = str(display_value).strip() if display_value not in (None, "") else ""
    if value_text:
        label = category.get("displayName") or category.get("name")
        label_text = str(label).strip() if isinstance(label, str) and label else ""
        return f"{value_text} {label_text}".strip() or None
    summary = leader.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


def _top_performers_for_team(
    team_block: dict[str, Any], side: str, limit: int = 3
) -> list[Performer]:
    """Up to ``limit`` notable performers for one team's leaders block.

    ESPN groups a team's leaders by stat category ("points", "goals",
    "assists", …); we take the top leader of each category in order,
    deduped by athlete (a player leading two categories appears once with
    the first), up to ``limit``.  Returns ``[]`` when the block has no
    usable named leader.
    """
    categories = team_block.get("leaders")
    if not isinstance(categories, list):
        return []
    out: list[Performer] = []
    seen: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            continue
        leaders = category.get("leaders")
        if not isinstance(leaders, list):
            continue
        for leader in leaders:
            if not isinstance(leader, dict):
                continue
            name = _athlete_name(leader)
            if not name or name in seen:
                continue
            detail = _leader_detail(leader, category)
            if detail is None:
                continue
            out.append(Performer(name=name, side=side, detail=detail))
            seen.add(name)
            break  # one (top) leader per category
        if len(out) >= limit:
            break
    return out


# Curated, ordered team box-score stats per sport: ESPN stat ``name`` ->
# display label.  Only these (when present on both sides) render as the
# comparison table, in this order; sports without an entry fall back to the
# provider's own first handful of two-sided stats.
_TEAM_STAT_SPECS: dict[Sport, list[tuple[str, str]]] = {
    Sport.SOCCER: [
        ("possessionPct", "Possession"),
        ("totalShots", "Shots"),
        ("shotsOnTarget", "Shots on Target"),
        ("wonCorners", "Corners"),
        ("foulsCommitted", "Fouls"),
        ("offsides", "Offsides"),
        ("yellowCards", "Yellow Cards"),
        ("redCards", "Red Cards"),
        ("saves", "Saves"),
    ],
}


def _team_stat_value(raw: str | None, name: str) -> str:
    """Display form for a stat value — append "%" to percentage stats."""
    if raw is None or raw == "":
        return "—"
    if name.endswith("Pct") and not raw.endswith("%"):
        return f"{raw}%"
    return raw


def _parse_team_stats(boxscore: Any, sport: Sport) -> list[TeamStat]:
    """Team-vs-team comparison stats from ``boxscore.teams[].statistics``.

    Each team block carries a ``statistics`` list of ``{name, label,
    displayValue}``; the two sides are aligned by ``homeAway`` and zipped
    into ``(label, home, away)`` rows.  Only sports with a curated spec
    (``_TEAM_STAT_SPECS`` — soccer today) produce a comparison; other sports
    nest their team stats under category groups with no flat value, so we
    emit nothing rather than empty rows.  Returns ``[]`` otherwise.
    """
    if not isinstance(boxscore, dict):
        return []
    teams = boxscore.get("teams")
    if not isinstance(teams, list):
        return []
    by_side: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        side = team.get("homeAway")
        if side not in ("home", "away"):
            continue
        stats: dict[str, tuple[str | None, str | None]] = {}
        for stat in team.get("statistics") or []:
            if not isinstance(stat, dict) or not isinstance(stat.get("name"), str):
                continue
            value = stat.get("displayValue")
            stats[stat["name"]] = (
                stat.get("label"),
                str(value) if value is not None else None,
            )
        by_side[side] = stats
    home = by_side.get("home")
    away = by_side.get("away")
    if not home or not away:
        return []

    # Only emit the comparison for sports we have a curated spec for: other
    # sports (e.g. baseball) nest their team stats under category groups
    # ("batting"/"pitching") with no flat displayValue, which a generic pass
    # would render as empty "—" rows.  Better to show no table than garbage.
    spec = _TEAM_STAT_SPECS.get(sport)
    if not spec:
        return []
    out: list[TeamStat] = []
    for name, label in spec:
        h = home.get(name)
        a = away.get(name)
        if h is None and a is None:
            continue
        out.append(
            TeamStat(
                label=label,
                home=_team_stat_value(h[1] if h else None, name),
                away=_team_stat_value(a[1] if a else None, name),
            )
        )
    return out


def _parse_performers(data: dict[str, Any], competition: dict[str, Any]) -> list[Performer]:
    """Top performers from the summary's top-level ``leaders[]`` block.

    Each ``leaders[]`` entry is one team's leaders; the team carries no
    ``homeAway`` flag there, so its side is resolved by matching the team
    id against the header competitors.  One performer per team (the
    headline-category leader) is emitted, home first.  A block whose team
    can't be matched, or which has no usable leader, is skipped.
    """
    blocks = data.get("leaders")
    if not isinstance(blocks, list) or not blocks:
        return []
    competitors = competition.get("competitors")
    side_by_team_id: dict[str, str] = {}
    if isinstance(competitors, list):
        for competitor in competitors:
            if not isinstance(competitor, dict):
                continue
            side = competitor.get("homeAway")
            raw_team = competitor.get("team")
            team_id = str(raw_team.get("id")) if isinstance(raw_team, dict) else None
            if side in ("home", "away") and team_id:
                side_by_team_id[team_id] = side

    home_performers: list[Performer] = []
    away_performers: list[Performer] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_team = block.get("team")
        team_id = str(raw_team.get("id")) if isinstance(raw_team, dict) else None
        side = side_by_team_id.get(team_id) if team_id else None
        if side not in ("home", "away"):
            continue
        performers = _top_performers_for_team(block, side)
        if side == "home" and not home_performers:
            home_performers = performers
        elif side == "away" and not away_performers:
            away_performers = performers
    return home_performers + away_performers


def _parse_goals(data: Any) -> list[Goal]:
    """Goal events from a soccer summary's ``keyEvents`` (scorer + team).

    Each scoring play carries the scorer in ``participants[0].athlete`` and
    the team in ``team``; own goals (detected from the event text) are kept
    but flagged so the Golden Boot can exclude them. Returns ``[]`` for
    sports/payloads without goal events.
    """
    if not isinstance(data, dict):
        return []
    events = data.get("keyEvents")
    if not isinstance(events, list):
        return []
    goals: list[Goal] = []
    for event in events:
        if not isinstance(event, dict) or not event.get("scoringPlay"):
            continue
        type_obj = event.get("type")
        if not (isinstance(type_obj, dict) and type_obj.get("type") == "goal"):
            continue
        participants = event.get("participants")
        player: str | None = None
        if isinstance(participants, list) and participants:
            athlete = participants[0].get("athlete") if isinstance(participants[0], dict) else None
            if isinstance(athlete, dict):
                name = athlete.get("displayName")
                if isinstance(name, str) and name:
                    player = name
        if not player:
            continue
        team_obj = event.get("team")
        team = team_obj.get("displayName") if isinstance(team_obj, dict) else None
        if not (isinstance(team, str) and team):
            continue
        clock = event.get("clock")
        minute = clock.get("displayValue") if isinstance(clock, dict) else None
        text = f"{event.get('text', '')} {event.get('shortText', '')}".lower()
        goals.append(
            Goal(
                player=player,
                team=team,
                minute=minute if isinstance(minute, str) and minute else None,
                own_goal="own goal" in text,
                penalty="penalty" in text,
            )
        )
    return goals


# Bound the win-probability series + play-by-play so a long game can't bloat
# the summary payload (the chart/timeline only need a readable resolution).
_WIN_PROB_MAX_POINTS = 160
_PLAYS_MAX = 50


def _parse_win_probability(data: Any) -> tuple[float, ...]:
    """Home-side win-probability series (0–100) from a summary payload.

    ESPN ships an inline ``winprobability`` array (one entry per play, in
    chronological order) with ``homeWinPercentage`` as a 0–1 fraction.
    Converted to 0–100 and evenly downsampled so a long game stays a
    readable chart.  ``()`` when the feed has no series.
    """
    if not isinstance(data, dict):
        return ()
    series = data.get("winprobability")
    if not isinstance(series, list) or not series:
        return ()
    points: list[float] = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        pct = _coerce_float(entry.get("homeWinPercentage"))
        if pct is None:
            continue
        points.append(round(pct * 100.0, 1))
    if len(points) <= _WIN_PROB_MAX_POINTS:
        return tuple(points)
    # Evenly downsample, always keeping the first and last point.
    step = len(points) / _WIN_PROB_MAX_POINTS
    sampled = [points[int(i * step)] for i in range(_WIN_PROB_MAX_POINTS)]
    sampled[-1] = points[-1]
    return tuple(sampled)


def _play_period_label(period: Any) -> str:
    """Human period label from a play/keyEvent ``period`` object."""
    if not isinstance(period, dict):
        return ""
    display = period.get("displayValue")
    if isinstance(display, str) and display:
        return display
    type_value = period.get("type")
    number = period.get("number")
    parts = [str(p) for p in (type_value, number) if p not in (None, "")]
    return " ".join(parts)


def _parse_plays(data: Any, sport: Sport) -> list[GamePlay]:
    """Condensed key-moment play-by-play from a summary payload.

    Soccer reads ``keyEvents`` (goals, cards, subs); other sports read the
    ``scoringPlays`` list (falling back to scoring entries in ``plays``).
    Capped to the most recent ``_PLAYS_MAX`` moments so the timeline stays
    glanceable.  ``[]`` when the feed exposes none.
    """
    if not isinstance(data, dict):
        return []
    plays: list[GamePlay] = []
    if sport is Sport.SOCCER:
        events = data.get("keyEvents")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                text = event.get("text") or event.get("shortText")
                type_obj = event.get("type")
                label = type_obj.get("text") if isinstance(type_obj, dict) else None
                if not (isinstance(text, str) and text):
                    text = label
                if not (isinstance(text, str) and text):
                    continue
                clock = event.get("clock")
                team_obj = event.get("team")
                plays.append(
                    GamePlay(
                        text=text,
                        period_label=_play_period_label(event.get("period")),
                        clock=(clock.get("displayValue") if isinstance(clock, dict) else None),
                        team=(team_obj.get("displayName") if isinstance(team_obj, dict) else None),
                        scoring=bool(event.get("scoringPlay")),
                    )
                )
    else:
        raw = data.get("scoringPlays")
        if not (isinstance(raw, list) and raw):
            all_plays = data.get("plays")
            raw = (
                [p for p in all_plays if isinstance(p, dict) and p.get("scoringPlay")]
                if isinstance(all_plays, list)
                else []
            )
        for play in raw if isinstance(raw, list) else []:
            if not isinstance(play, dict):
                continue
            text = play.get("text")
            if not (isinstance(text, str) and text):
                continue
            team_obj = play.get("team")
            plays.append(
                GamePlay(
                    text=text,
                    period_label=_play_period_label(play.get("period")),
                    clock=(
                        play.get("clock", {}).get("displayValue")
                        if isinstance(play.get("clock"), dict)
                        else None
                    ),
                    team=(team_obj.get("displayName") if isinstance(team_obj, dict) else None),
                    home_score=_coerce_int(play.get("homeScore")),
                    away_score=_coerce_int(play.get("awayScore")),
                    scoring=bool(play.get("scoringPlay")),
                )
            )
    return plays[-_PLAYS_MAX:]


def _parse_pickcenter(
    data: Any,
) -> tuple[str | None, str | None, int | None, int | None, float | None, float | None]:
    """Read a sportsbook line off a summary payload's ``pickcenter``.

    Returns ``(provider, details, home_moneyline, away_moneyline, spread,
    over_under)`` — each element independently ``None``.  ESPN fills
    ``pickcenter`` for US sports; soccer typically leaves it empty/null, so
    every lookup is defensive.  ``spread`` is ESPN's home-relative point
    spread (negative = home favored); ``details`` (e.g. ``"ATL -175"``) is
    the unambiguous display line.
    """
    none6 = (None, None, None, None, None, None)
    if not isinstance(data, dict):
        return none6
    pickcenter = data.get("pickcenter")
    if not isinstance(pickcenter, list):
        return none6
    entry = next((p for p in pickcenter if isinstance(p, dict)), None)
    if entry is None:
        return none6
    provider_obj = entry.get("provider")
    provider = provider_obj.get("name") if isinstance(provider_obj, dict) else None
    details = entry.get("details")
    home_odds = entry.get("homeTeamOdds")
    away_odds = entry.get("awayTeamOdds")
    return (
        provider if isinstance(provider, str) else None,
        details if isinstance(details, str) else None,
        _coerce_int(home_odds.get("moneyLine")) if isinstance(home_odds, dict) else None,
        _coerce_int(away_odds.get("moneyLine")) if isinstance(away_odds, dict) else None,
        _coerce_float(entry.get("spread")),
        _coerce_float(entry.get("overUnder")),
    )


def _gameprojection(side: Any) -> float | None:
    """Win-probability percentage from one side of the predictor feed."""
    if not isinstance(side, dict):
        return None
    stats = side.get("statistics")
    if not isinstance(stats, list):
        return None
    for stat in stats:
        if isinstance(stat, dict) and stat.get("name") == "gameProjection":
            return _coerce_float(stat.get("displayValue"))
    return None


def _parse_predictor(data: Any) -> tuple[float | None, float | None]:
    """``(home_win_pct, away_win_pct)`` from the core predictor payload."""
    if not isinstance(data, dict):
        return (None, None)
    return (
        _gameprojection(data.get("homeTeam")),
        _gameprojection(data.get("awayTeam")),
    )


def _core_event_path(provider_key: str) -> str:
    """Core-API ``{sport}/leagues/{league}`` path for a ``{sport}/{league}`` key.

    The site API addresses a league as ``{sport}/{league}`` (e.g.
    ``baseball/mlb``), but the core API inserts a ``leagues`` segment
    (``baseball/leagues/mlb``).  ``provider_key`` carries the site form, so
    the predictor URL has to re-insert it or the endpoint 404s.
    """
    sport, _, league = provider_key.partition("/")
    return f"{sport}/leagues/{league}" if league else provider_key


def _odds_has_signal(odds: GameOdds) -> bool:
    """Whether any odds/win-prob field carries a value worth returning."""
    return any(
        value is not None
        for value in (
            odds.provider,
            odds.details,
            odds.home_moneyline,
            odds.away_moneyline,
            odds.spread,
            odds.over_under,
            odds.home_win_pct,
            odds.away_win_pct,
        )
    )


def _parse_game_summary(data: Any, league: League, provider_game_key: str) -> GameSummary | None:
    """Build a :class:`GameSummary` from a fetched ESPN summary payload.

    Period lines come from the header competitors' linescores; totals from
    the competitors' final score (the live-state score), and best-effort
    performers from the top-level ``leaders``.  Returns ``None`` only when
    the payload has no usable header competition at all; an otherwise-valid
    payload with no boxscore yields a summary with empty ``periods``.
    """
    if not isinstance(data, dict):
        return None
    header = data.get("header")
    if not isinstance(header, dict):
        return None
    competitions = header.get("competitions")
    if (
        not isinstance(competitions, list)
        or not competitions
        or not isinstance(competitions[0], dict)
    ):
        return None
    competition = competitions[0]
    try:
        competitors = competition.get("competitors")
        home = away = None
        if isinstance(competitors, list):
            home = _find_side(competitors, "home")
            away = _find_side(competitors, "away")
        if home is None or away is None:
            return None
        periods = _parse_period_scores(competition, league.sport)
        performers = _parse_performers(data, competition)
        team_stats = _parse_team_stats(data.get("boxscore"), league.sport)
        goals = _parse_goals(data) if league.sport is Sport.SOCCER else []
        return GameSummary(
            game_id=f"espn:{provider_game_key}",
            periods=tuple(periods),
            performers=tuple(performers),
            team_stats=tuple(team_stats),
            goals=tuple(goals),
            home_total=_coerce_int(home.get("score")),
            away_total=_coerce_int(away.get("score")),
            win_probability=_parse_win_probability(data),
            plays=tuple(_parse_plays(data, league.sport)),
        )
    except Exception:
        logger.warning(
            "Failed to parse ESPN game summary for game %s",
            provider_game_key,
            exc_info=True,
        )
        return None


def _parse_season(data: Any) -> str:
    if isinstance(data, dict):
        season = data.get("season")
        if isinstance(season, dict):
            for key in ("displayName", "name", "year"):
                value = season.get(key)
                if value:
                    return str(value)
        elif isinstance(season, (int, str)) and season:
            return str(season)
        seasons = data.get("seasons")
        if isinstance(seasons, list) and seasons and isinstance(seasons[0], dict):
            for key in ("displayName", "name", "year"):
                value = seasons[0].get(key)
                if value:
                    return str(value)
    return str(timeutil.utcnow().year)


def _parse_standing_entry(
    entry: Any, league: League, team_ids: Mapping[str, str] | None = None
) -> tuple[int, StandingRow] | None:
    """Parse one standings entry into ``(provider_rank, row)``; None if malformed.

    ``team_ids`` maps ESPN team ids to internal followed-team slugs so
    followed teams can be highlighted in standings.
    """
    try:
        if not isinstance(entry, dict):
            raise ValueError("entry is not an object")
        raw_team = entry.get("team")
        team_obj: dict[str, Any] = raw_team if isinstance(raw_team, dict) else {}
        name = _team_name(team_obj)
        if not name:
            raise ValueError("missing team name")
        internal_id = (team_ids or {}).get(str(team_obj.get("id")))
        # Per-row crest/color/abbr so every team in the table shows a logo,
        # not just the followed ones (which have no bearing on standings).
        logo_url = _team_logo(team_obj)
        abbreviation = _team_abbreviation(team_obj)
        color = _team_color(team_obj)

        stats: dict[str, dict[str, Any]] = {}
        raw_stats = entry.get("stats")
        if isinstance(raw_stats, list):
            for stat in raw_stats:
                if isinstance(stat, dict) and isinstance(stat.get("name"), str):
                    stats[stat["name"]] = stat

        def stat_value(*names: str) -> float | None:
            for stat_name in names:
                stat = stats.get(stat_name)
                if stat is None:
                    continue
                value = stat.get("value")
                if value is None:
                    value = stat.get("displayValue")
                number = _coerce_float(value)
                if number is not None:
                    return number
            return None

        wins = int(stat_value("wins") or 0)
        losses = int(stat_value("losses") or 0)
        rank = int(stat_value("rank", "playoffSeed") or 0)

        if league.sport is Sport.SOCCER:
            draws = stat_value("ties", "draws")
            points = stat_value("points")
            goal_diff = stat_value(
                "pointDifferential", "pointsDiff", "goalDifferential", "differential"
            )
            row = StandingRow(
                rank=0,
                team_name=name,
                wins=wins,
                losses=losses,
                team_id=internal_id,
                logo_url=logo_url,
                abbreviation=abbreviation,
                color=color,
                draws=int(draws) if draws is not None else None,
                points=int(points) if points is not None else None,
                goal_diff=int(goal_diff) if goal_diff is not None else None,
            )
        elif league.sport is Sport.HOCKEY:
            # NHL payloads carry the same value under both "otLosses" and
            # "overtimeLosses" (verified live); accept either spelling.
            ot_losses = stat_value("otLosses", "overtimeLosses")
            points = stat_value("points")
            row = StandingRow(
                rank=0,
                team_name=name,
                wins=wins,
                losses=losses,
                team_id=internal_id,
                logo_url=logo_url,
                abbreviation=abbreviation,
                color=color,
                points=int(points) if points is not None else None,
                ot_losses=int(ot_losses) if ot_losses is not None else None,
            )
        elif league.sport is Sport.FOOTBALL:
            draws = stat_value("ties")
            win_pct = stat_value("winPercent", "winPercentage")
            if win_pct is None and wins + losses + (draws or 0) > 0:
                # NFL convention: a tie counts as half a win.
                win_pct = (wins + (draws or 0) / 2) / (wins + losses + (draws or 0))
            row = StandingRow(
                rank=0,
                team_name=name,
                wins=wins,
                losses=losses,
                team_id=internal_id,
                logo_url=logo_url,
                abbreviation=abbreviation,
                color=color,
                draws=int(draws) if draws is not None else None,
                win_pct=round(win_pct, 3) if win_pct is not None else None,
            )
        else:
            win_pct = stat_value("winPercent", "leagueWinPercent", "winPercentage")
            if win_pct is None and wins + losses > 0:
                win_pct = wins / (wins + losses)
            games_back = stat_value("gamesBehind", "gamesBack")
            row = StandingRow(
                rank=0,
                team_name=name,
                wins=wins,
                losses=losses,
                team_id=internal_id,
                logo_url=logo_url,
                abbreviation=abbreviation,
                color=color,
                win_pct=round(win_pct, 3) if win_pct is not None else None,
                games_back=games_back,
            )
        return rank, row
    except Exception:
        logger.warning(
            "Skipping malformed ESPN standings entry for league %s",
            league.id,
            exc_info=True,
        )
        return None


def _standing_group_label(node: dict[str, Any]) -> str | None:
    """Group/subgroup display label for a standings ``children[]`` node."""
    raw = node.get("name") or node.get("abbreviation")
    return str(raw) if raw else None


def _parse_standings(
    data: Any, league: League, team_ids: Mapping[str, str] | None = None
) -> Standings:
    # (group, subgroup, entries) blocks in payload order, ranked within the
    # FINEST grouping that carries entries.  Three shapes coexist:
    #   * an un-nested top-level ``standings`` block (group/subgroup None);
    #   * a ``children[]`` node whose own ``standings.entries`` is the table
    #     (conference/single-table -> group = its name, subgroup None);
    #   * a ``children[]`` node with nested ``children`` carrying the
    #     entries (division depth via ``?level=3``) -> group = the outer
    #     node (conference/league), subgroup = the inner node (division).
    blocks: list[tuple[str | None, str | None, list[Any]]] = []
    if isinstance(data, dict):
        block = data.get("standings")
        if isinstance(block, dict) and isinstance(block.get("entries"), list):
            blocks.append((None, None, block["entries"]))
        children = data.get("children")
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                group = _standing_group_label(child)
                block = child.get("standings")
                if isinstance(block, dict) and isinstance(block.get("entries"), list):
                    blocks.append((group, None, block["entries"]))
                grandchildren = child.get("children")
                if isinstance(grandchildren, list):
                    for grandchild in grandchildren:
                        if not isinstance(grandchild, dict):
                            continue
                        sub_block = grandchild.get("standings")
                        if isinstance(sub_block, dict) and isinstance(
                            sub_block.get("entries"), list
                        ):
                            subgroup = _standing_group_label(grandchild)
                            blocks.append((group, subgroup, sub_block["entries"]))

    rows: list[StandingRow] = []
    for group, subgroup, entries in blocks:
        parsed: list[tuple[int, StandingRow]] = []
        for entry in entries:
            result = _parse_standing_entry(entry, league, team_ids)
            if result is not None:
                parsed.append(result)
        # Within each (finest) group: rank-ordered first (unranked last),
        # then by wins.
        parsed.sort(key=lambda item: (item[0] <= 0, item[0], -item[1].wins, item[1].team_name))
        rows.extend(
            replace(row, rank=index + 1, group=group, subgroup=subgroup)
            for index, (_, row) in enumerate(parsed)
        )
    return Standings(
        league_id=league.id,
        season=_parse_season(data),
        rows=tuple(rows),
        fetched_at=timeutil.utcnow(),
    )


def _parse_ranking_entry(
    entry: Any, athlete_ids: Mapping[str, str] | None
) -> tuple[int, StandingRow] | None:
    """Parse one tennis ``rankings[].ranks[]`` entry into ``(rank, row)``.

    Tennis "standings" are a tour ranking: the player's name, current
    rank and ranking points map onto :class:`StandingRow` (no W/L), with
    a followed athlete tagged via ``athlete_ids`` (ESPN athlete id ->
    internal slug).
    """
    try:
        if not isinstance(entry, dict):
            raise ValueError("ranking entry is not an object")
        athlete = entry.get("athlete")
        athlete_obj: dict[str, Any] = athlete if isinstance(athlete, dict) else {}
        name: str | None = None
        for key in ("displayName", "fullName", "shortName"):
            value = athlete_obj.get(key)
            if isinstance(value, str) and value:
                name = value
                break
        if not name:
            raise ValueError("missing athlete name")
        rank = _coerce_int(entry.get("current")) or 0
        points = _coerce_int(entry.get("points"))
        internal_id = (athlete_ids or {}).get(str(athlete_obj.get("id")))
        row = StandingRow(
            rank=0,
            team_name=name,
            wins=0,
            losses=0,
            team_id=internal_id,
            points=points,
        )
        return rank, row
    except Exception:
        logger.warning("Skipping malformed ESPN tennis ranking entry", exc_info=True)
        return None


def _parse_tennis_rankings(
    data: Any, league: League, athlete_ids: Mapping[str, str] | None = None
) -> Standings:
    """Parse the tennis rankings endpoint into per-tour Standings.

    The payload is ``rankings[0].ranks[]`` (one ranking table per tour).
    Rows are ordered by rank; the tour name becomes the single group.
    """
    rankings = data.get("rankings") if isinstance(data, dict) else None
    block: dict[str, Any] = {}
    if isinstance(rankings, list) and rankings and isinstance(rankings[0], dict):
        block = rankings[0]
    group = block.get("name") or block.get("shortName")
    group = str(group) if group else None
    raw_ranks = block.get("ranks")
    parsed: list[tuple[int, StandingRow]] = []
    if isinstance(raw_ranks, list):
        for entry in raw_ranks:
            result = _parse_ranking_entry(entry, athlete_ids)
            if result is not None:
                parsed.append(result)
    parsed.sort(key=lambda item: (item[0] <= 0, item[0], item[1].team_name))
    rows = tuple(replace(row, rank=index + 1, group=group) for index, (_, row) in enumerate(parsed))
    return Standings(
        league_id=league.id,
        season=_parse_season(data),
        rows=rows,
        fetched_at=timeutil.utcnow(),
    )


def _status_from_text(text: str) -> PlayerStatus:
    """Map a non-active ESPN status string onto :class:`PlayerStatus`."""
    lowered = text.lower()
    if "day" in lowered or "questionable" in lowered:
        return PlayerStatus.DAY_TO_DAY
    if lowered == "out":
        return PlayerStatus.OUT
    return PlayerStatus.INJURED


def _parse_injury(injuries: Any) -> tuple[PlayerStatus, str | None]:
    if not isinstance(injuries, list) or not injuries or not isinstance(injuries[0], dict):
        return PlayerStatus.ACTIVE, None
    injury = injuries[0]
    raw_status = str(injury.get("status") or "").strip()
    if not raw_status:
        return PlayerStatus.ACTIVE, None
    status = _status_from_text(raw_status)
    details = injury.get("details")
    detail_type = details.get("type") if isinstance(details, dict) else None
    status_detail = f"{raw_status} - {detail_type}" if detail_type else raw_status
    return status, status_detail


def _parse_player_status(athlete: dict[str, Any]) -> tuple[PlayerStatus, str | None]:
    """Injuries-first player status derivation (all sports).

    NHL and NFL rosters keep ``status.type`` at ``"active"`` even while
    ``injuries[]`` says Out / Injured Reserve / Questionable (verified
    live), so the injury list wins; ``status.type`` is only consulted
    when no injury entry says otherwise.
    """
    status, status_detail = _parse_injury(athlete.get("injuries"))
    if status is not PlayerStatus.ACTIVE:
        return status, status_detail
    raw_status = athlete.get("status")
    if isinstance(raw_status, dict):
        type_text = str(raw_status.get("type") or "").strip()
        if type_text and type_text.lower() != "active":
            raw_name = raw_status.get("name")
            detail = raw_name if isinstance(raw_name, str) and raw_name else type_text
            return _status_from_text(type_text), detail
    return PlayerStatus.ACTIVE, None


def _parse_athlete(athlete: dict[str, Any], team: Team) -> Player | None:
    try:
        athlete_id = str(athlete.get("id") or "").strip()
        name: str | None = None
        for key in ("fullName", "displayName", "name"):
            value = athlete.get(key)
            if isinstance(value, str) and value:
                name = value
                break
        if not athlete_id or not name:
            raise ValueError("missing athlete id or name")

        raw_position = athlete.get("position")
        position: str | None = None
        if isinstance(raw_position, dict):
            value = raw_position.get("abbreviation") or raw_position.get("name")
            position = str(value) if value else None
        elif isinstance(raw_position, str) and raw_position:
            position = raw_position

        jersey = athlete.get("jersey")
        jersey_number = str(jersey) if isinstance(jersey, (str, int)) and str(jersey) else None

        headshot = athlete.get("headshot")
        photo_url: str | None = None
        if isinstance(headshot, dict):
            href = headshot.get("href")
            photo_url = href if isinstance(href, str) and href else None

        status, status_detail = _parse_player_status(athlete)
        return Player(
            id=f"espn:{athlete_id}",
            team_id=team.id,
            name=name,
            position=position,
            jersey_number=jersey_number,
            status=status,
            status_detail=status_detail,
            photo_url=photo_url,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN athlete for team %s", team.id, exc_info=True)
        return None


def _parse_roster(data: Any, league: League, team: Team) -> Roster:
    athletes: list[dict[str, Any]] = []
    raw = data.get("athletes") if isinstance(data, dict) else None
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            items = entry.get("items")
            if isinstance(items, list):
                # Grouped form: [{"position": "...", "items": [athlete, ...]}, ...]
                athletes.extend(item for item in items if isinstance(item, dict))
            else:
                athletes.append(entry)
    players: list[Player] = []
    for athlete in athletes:
        player = _parse_athlete(athlete, team)
        if player is not None:
            players.append(player)
    return Roster(team_id=team.id, players=tuple(players), fetched_at=timeutil.utcnow())


def _overview_path(sport: Sport, provider_key: str) -> str | None:
    """ESPN ``{sport}/{league}`` path fragment for the overview endpoint.

    ``League.provider_key`` already carries it for the team sports
    (``basketball/nba``, ``soccer/eng.1`` ...).  Individual/leaderboard
    sports have no athlete-overview line.
    """
    if sport in INDIVIDUAL_SPORTS:
        return None
    return provider_key if "/" in provider_key else None


def _athlete_id(player: Player) -> str | None:
    """Recover the bare ESPN athlete id from a ``espn:{id}`` player id."""
    prefix = "espn:"
    if player.id.startswith(prefix):
        athlete_id = player.id[len(prefix) :].strip()
        return athlete_id or None
    return None


def _overview_stat_split(data: Any) -> dict[str, Any] | None:
    """Pick the best ``statistics`` split from an athlete overview payload.

    Returns ``{name: displayValue}`` for the season line, preferring the
    "Regular Season" split, else the first split.  ``None`` when the
    payload carries no usable per-season statistics (pitchers and soccer
    players frequently omit ``names`` -> no line, which is acceptable).
    """
    if not isinstance(data, dict):
        return None
    stats = data.get("statistics")
    if not isinstance(stats, dict):
        return None
    names = stats.get("names")
    splits = stats.get("splits")
    if not (isinstance(names, list) and isinstance(splits, list) and names and splits):
        return None
    chosen: dict[str, Any] | None = None
    for split in splits:
        if not isinstance(split, dict):
            continue
        values = split.get("stats")
        if not isinstance(values, list):
            continue
        label = str(split.get("displayName") or "")
        mapping = {
            str(name): values[index] for index, name in enumerate(names) if index < len(values)
        }
        if label.lower() == "regular season":
            return mapping
        if chosen is None:
            chosen = mapping
    return chosen


def _overview_career_split(data: Any) -> dict[str, Any] | None:
    """Pick the ``"Career"`` ``statistics`` split from an overview payload.

    Same ``{name: displayValue}`` shape as :func:`_overview_stat_split`, but
    selects the career-totals split (the overview carries "Regular Season",
    "Postseason" and "Career" for team-sport athletes).  ``None`` when the
    payload has no career split (so the line just stays absent).
    """
    if not isinstance(data, dict):
        return None
    stats = data.get("statistics")
    if not isinstance(stats, dict):
        return None
    names = stats.get("names")
    splits = stats.get("splits")
    if not (isinstance(names, list) and isinstance(splits, list) and names and splits):
        return None
    for split in splits:
        if not isinstance(split, dict):
            continue
        values = split.get("stats")
        if not isinstance(values, list):
            continue
        if str(split.get("displayName") or "").lower() != "career":
            continue
        return {str(name): values[index] for index, name in enumerate(names) if index < len(values)}
    return None


def _trim_number(text: Any) -> str | None:
    """ESPN display value as a clean string (keeps commas, drops empties)."""
    if text is None:
        return None
    value = str(text).strip()
    return value or None


def _format_stat_line(sport: Sport, stats: Mapping[str, Any]) -> str | None:
    """Compact, sport-appropriate season line from an overview split.

    Format mirrors the contract examples; ``None`` when the split lacks
    the stats a line needs (e.g. a defender's offensive-only block).
    """

    def has(*names: str) -> bool:
        return any(_trim_number(stats.get(name)) is not None for name in names)

    def get(name: str) -> str | None:
        return _trim_number(stats.get(name))

    if sport is Sport.BASKETBALL:
        points, rebounds, assists = get("avgPoints"), get("avgRebounds"), get("avgAssists")
        if points is None:
            return None
        parts = [f"{points} PPG"]
        if rebounds is not None:
            parts.append(f"{rebounds} REB")
        if assists is not None:
            parts.append(f"{assists} AST")
        return " · ".join(parts)

    if sport is Sport.HOCKEY:
        goals, assists, points = get("goals"), get("assists"), get("points")
        if goals is None and assists is None and points is None:
            return None
        parts = []
        if goals is not None:
            parts.append(f"{goals} G")
        if assists is not None:
            parts.append(f"{assists} A")
        if points is not None:
            parts.append(f"{points} PTS")
        return " · ".join(parts)

    if sport is Sport.BASEBALL:
        # Batters carry ``atBats``; pitchers carry ``innings``/``earnedRuns``
        # (and ESPN reuses ``homeRuns``/``strikeouts`` for HR-allowed and
        # K-thrown, so the discriminator is at-bats vs innings, not those).
        if has("atBats"):
            home_runs, rbis = get("homeRuns"), get("RBIs")
            avg = _batting_average(stats)
            parts = []
            if avg is not None:
                parts.append(f"{avg} AVG")
            if home_runs is not None:
                parts.append(f"{home_runs} HR")
            if rbis is not None:
                parts.append(f"{rbis} RBI")
            return " · ".join(parts) or None
        if has("innings"):
            era = _earned_run_average(stats)
            strikeouts = get("strikeouts")
            parts = []
            if era is not None:
                parts.append(f"{era} ERA")
            if strikeouts is not None:
                parts.append(f"{strikeouts} K")
            return " · ".join(parts) or None
        return None

    if sport is Sport.FOOTBALL:
        if has("passingTouchdowns", "passingYards"):
            tds, ints, yds = (
                get("passingTouchdowns"),
                get("interceptions"),
                get("passingYards"),
            )
            parts = []
            if tds is not None:
                parts.append(f"{tds} TD")
            if ints is not None:
                parts.append(f"{ints} INT")
            if yds is not None:
                parts.append(f"{yds} YDS")
            return " · ".join(parts) or None
        if (
            has("rushingYards")
            and _nonzero(stats.get("rushingAttempts"))
            and not _receiving_dominant(stats)
        ):
            yds, tds = get("rushingYards"), get("rushingTouchdowns")
            parts = []
            if yds is not None:
                parts.append(f"{yds} YDS")
            if tds is not None:
                parts.append(f"{tds} TD")
            return " · ".join(parts) or None
        if has("receivingYards") and _nonzero(stats.get("receptions")):
            rec, yds, tds = (
                get("receptions"),
                get("receivingYards"),
                get("receivingTouchdowns"),
            )
            parts = []
            if rec is not None:
                parts.append(f"{rec} REC")
            if yds is not None:
                parts.append(f"{yds} YDS")
            if tds is not None:
                parts.append(f"{tds} TD")
            return " · ".join(parts) or None
        return None

    if sport is Sport.SOCCER:
        goals, assists = get("totalGoals") or get("goals"), get("goalAssists") or get("assists")
        parts = []
        if goals is not None:
            parts.append(f"{goals} G")
        if assists is not None:
            parts.append(f"{assists} A")
        return " · ".join(parts) or None

    return None


def _nonzero(value: Any) -> bool:
    number = _coerce_float(str(value).replace(",", "")) if value is not None else None
    return number is not None and number != 0


def _yards(value: Any) -> float:
    """A yardage display value as a float (commas stripped); 0.0 if unparseable."""
    if value is None:
        return 0.0
    number = _coerce_float(str(value).replace(",", ""))
    return number if number is not None else 0.0


def _receiving_dominant(stats: Mapping[str, Any]) -> bool:
    """True when a player's receiving outweighs their rushing production.

    A pass-catcher (TE/WR) often has a handful of trick-play rushing
    attempts, so a stats-only formatter would otherwise render their
    *career* line from those few rushing yards (e.g. "8 YDS") instead of
    their real receiving line.  Comparing the two yardages picks the role
    that actually represents the player — without needing a position.
    """
    return _yards(stats.get("receivingYards")) > _yards(stats.get("rushingYards"))


def _batting_average(stats: Mapping[str, Any]) -> str | None:
    """``.291``-style average from hits / at-bats (no ESPN ``avg`` name)."""
    avg = _trim_number(stats.get("avg"))
    if avg is not None:
        return avg if avg.startswith(".") else avg.lstrip("0") or avg
    hits = _coerce_float(str(stats.get("hits", "")).replace(",", ""))
    at_bats = _coerce_float(str(stats.get("atBats", "")).replace(",", ""))
    if hits is None or not at_bats:
        return None
    return f"{hits / at_bats:.3f}".lstrip("0")


def _innings_pitched(value: Any) -> float | None:
    """Convert ESPN innings notation (``27.2`` == 27 + 2/3) to a float."""
    text = _trim_number(value)
    if text is None:
        return None
    if "." in text:
        whole, _, frac = text.partition(".")
        whole_num = _coerce_float(whole) or 0.0
        thirds = {"0": 0.0, "1": 1.0 / 3.0, "2": 2.0 / 3.0}.get(frac[:1])
        if thirds is None:  # not the .1/.2 thirds notation -> plain decimal
            return _coerce_float(text)
        return whole_num + thirds
    return _coerce_float(text)


def _earned_run_average(stats: Mapping[str, Any]) -> str | None:
    """``3.12``-style ERA from earned runs and innings pitched."""
    era = _trim_number(stats.get("ERA"))
    if era is not None:
        return era
    earned = _coerce_float(str(stats.get("earnedRuns", "")).replace(",", ""))
    innings = _innings_pitched(stats.get("innings"))
    if earned is None or not innings:
        return None
    return f"{earned * 9.0 / innings:.2f}"


def _stat_line_from_overview(data: Any, sport: Sport) -> str | None:
    """End-to-end: overview payload -> compact stat line (``None`` if none)."""
    split = _overview_stat_split(data)
    if split is None:
        return None
    try:
        return _format_stat_line(sport, split)
    except Exception:
        logger.warning("Failed to format ESPN stat line", exc_info=True)
        return None


def _career_line_from_overview(data: Any, sport: Sport) -> str | None:
    """End-to-end: overview payload -> compact CAREER line (``None`` if none)."""
    split = _overview_career_split(data)
    if split is None:
        return None
    try:
        return _format_stat_line(sport, split)
    except Exception:
        logger.warning("Failed to format ESPN career line", exc_info=True)
        return None


def _parse_article(
    article: Any, *, team: Team | None = None, league_id: str | None = None
) -> NewsItem | None:
    """Parse one news article; None for premium or malformed entries.

    Tag with ``team.id`` for a followed-team feed, or ``league_id`` for a
    whole-competition feed (``team`` is then None and ``team_id`` stays None).
    """
    scope = team.id if team is not None else league_id
    try:
        if not isinstance(article, dict):
            raise ValueError("article is not an object")
        if article.get("premium"):
            # Paywalled content is useless in the dashboard; skip quietly.
            return None
        links = article.get("links")
        web = links.get("web") if isinstance(links, dict) else None
        href = web.get("href") if isinstance(web, dict) else None
        if not (isinstance(href, str) and href):
            raise ValueError("missing web link")
        title = article.get("headline")
        if not (isinstance(title, str) and title):
            raise ValueError("missing headline")
        summary = article.get("description")
        if not (isinstance(summary, str) and summary):
            summary = None
        image_url: str | None = None
        images = article.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            raw_url = images[0].get("url")
            if isinstance(raw_url, str) and raw_url:
                image_url = raw_url
        return NewsItem(
            id=hashlib.sha1(href.encode("utf-8")).hexdigest()[:16],
            team_id=team.id if team is not None else None,
            title=title,
            url=href,
            source="ESPN",
            published_at=_parse_espn_datetime(article.get("published")),
            summary=summary,
            image_url=image_url,
            league_id=league_id,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN article for %s", scope, exc_info=True)
        return None


def _parse_news(
    data: Any, *, team: Team | None = None, league_id: str | None = None
) -> list[NewsItem]:
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []
    return [
        item
        for article in articles
        if (item := _parse_article(article, team=team, league_id=league_id)) is not None
    ]


def _venue_geocode_query(venue: dict[str, Any]) -> str | None:
    """A geocodable venue string from an ESPN ``venue`` object.

    Combines the venue ``fullName`` with its ``address`` city/state/country
    so the downstream geocoder (Nominatim) has enough context to resolve a
    stadium with a common name (e.g. "Wembley Stadium, London, England").
    Returns ``None`` when the object carries no usable name.
    """
    if not isinstance(venue, dict):
        return None
    name = venue.get("fullName") or venue.get("shortName")
    if not isinstance(name, str) or not name.strip():
        return None
    parts = [name.strip()]
    address = venue.get("address")
    if isinstance(address, dict):
        for key in ("city", "state", "country"):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return ", ".join(parts)


def _parse_team_location(data: Any, provider_key: str | None = None) -> TeamLocation | None:
    """Home-venue location for the map view from a ``/teams/{id}`` payload.

    ESPN keeps the home venue in different places by sport: US franchise
    sports (NBA/NFL/MLB/NHL) carry it under ``team.franchise.venue`` while
    a few leagues expose a top-level ``team.venue``; soccer teams have no
    venue on this endpoint at all.  For those we fall back to the team's
    ``nextEvent`` fixture, whose competition ``venue`` is the *home* side's
    ground — used only when ``provider_key`` is the home competitor, so a
    club with an away next match isn't stranded at the opponent's stadium
    (the scheduler still has the stored-game / enrichment fallbacks beyond
    that).  ESPN does not ship usable coordinates here — only a ``$ref`` to
    a venue resource — so lat/lon are left ``None`` for the geocode service
    to fill.  Returns ``None`` when no venue name is present.
    """
    if not isinstance(data, dict):
        return None
    team = data.get("team")
    if not isinstance(team, dict):
        return None
    venue = team.get("venue")
    if not isinstance(venue, dict) or not venue.get("fullName"):
        franchise = team.get("franchise")
        if isinstance(franchise, dict) and isinstance(franchise.get("venue"), dict):
            venue = franchise["venue"]
    query = _venue_geocode_query(venue) if isinstance(venue, dict) else None
    if query is None:
        # Soccer clubs expose no team/franchise venue here; their next
        # fixture usually carries one (the home side's ground).
        query = _home_venue_from_next_event(team, provider_key)
    if query is None:
        return None
    # Coordinates are deliberately None: the team endpoint exposes only a
    # venue resource reference, never lat/lon.  The geocode service resolves
    # the combined venue string.
    return TeamLocation(venue=query, lat=None, lon=None)


def _home_venue_from_next_event(team: dict[str, Any], provider_key: str | None) -> str | None:
    """Geocodable home-venue string from ``team.nextEvent``, when this team hosts.

    A competition's ``venue`` is the home side's stadium, so this is only
    trusted when ``provider_key`` matches the home competitor — otherwise we
    can't tell whose ground it is and return ``None`` rather than risk
    placing the club at an opponent's stadium.
    """
    if provider_key is None:
        return None
    key = str(provider_key)
    events = team.get("nextEvent")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if not isinstance(competition, dict):
                continue
            venue = competition.get("venue")
            if not isinstance(venue, dict):
                continue
            if not _is_home_competitor(competition, key):
                continue
            query = _venue_geocode_query(venue)
            if query is not None:
                return query
    return None


def _is_home_competitor(competition: dict[str, Any], provider_key: str) -> bool:
    """Whether ``provider_key`` is the ``homeAway == "home"`` competitor."""
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        return False
    for competitor in competitors:
        if not isinstance(competitor, dict) or competitor.get("homeAway") != "home":
            continue
        cid = competitor.get("id")
        if cid is None and isinstance(competitor.get("team"), dict):
            cid = competitor["team"].get("id")
        if cid is not None and str(cid) == provider_key:
            return True
    return False


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class EspnProvider:
    """``SportsProvider`` adapter for ESPN's public site API."""

    provider_id = "espn"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        # ESPN team id -> internal slug, learned from the (league, team)
        # arguments of schedule/roster/news calls.  get_standings only receives
        # a League, and the scheduler always refreshes schedules first, so
        # by the time standings are fetched the mapping is populated and
        # followed teams can be tagged in standings rows.
        self._known_teams: dict[str, dict[str, str]] = {}

    def _register(self, league: League, team: Team) -> None:
        self._known_teams.setdefault(league.id, {})[str(team.provider_key)] = team.id

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(get_settings().provider_timeout_seconds),
                headers={"User-Agent": "SportsDash/1.0"},
                follow_redirects=True,
            )
        return self._client

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        settings = get_settings()
        response = await get_with_retry(
            self._get_client(),
            url,
            params=params,
            max_retries=settings.provider_max_retries,
            backoff_base=settings.provider_backoff_base,
            label="espn",
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def get_schedule(self, league: League, team: Team, start: date, end: date) -> list[Game]:
        self._register(league, team)
        if league.sport in INDIVIDUAL_SPORTS:
            # An athlete has no ``/teams/{id}/schedule`` endpoint; scan the
            # tour/card scoreboard for the competitions they appear in.
            games = await self._get_individual_schedule(league, team, start, end)
        else:
            url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/schedule"
            param_sets = _SCHEDULE_PARAM_SETS.get(league.sport)
            if param_sets is not None:
                games = await self._get_merged_schedule(url, league, team, param_sets)
            else:
                games = _parse_schedule(await self._get_json(url), league, team)
        return sorted(
            (game for game in games if start <= game.start_time.date() <= end),
            key=lambda game: game.start_time,
        )

    async def _get_individual_schedule(
        self, league: League, team: Team, start: date, end: date
    ) -> list[Game]:
        """A followed athlete's matches/bouts in ``[start, end]``.

        Tennis ships the whole tournament draw on the tour scoreboard;
        ``?dates=YYYYMMDD-YYYYMMDD`` returns every event overlapping the
        range, so one ranged call (chunked by month for long windows)
        covers the window.  UFC schedules at month granularity
        (``?dates=YYYYMM``), so each calendar month in the window is
        fetched and the bouts the athlete appears in are kept.  Each chunk
        is fetched independently; a failed chunk logs and is skipped.
        """
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Game]] = []
        errors: list[Exception] = []
        for params in self._individual_scoreboard_params(league.sport, start, end):
            try:
                data = await self._get_json(url, params=params)
            except Exception as exc:
                logger.warning(
                    "ESPN %s schedule call failed for athlete %s (params=%s): %s",
                    league.sport.value,
                    team.id,
                    params,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_individual_scoreboard(data, league, team))
        if not batches and errors:
            raise errors[0]
        return _games_for_athlete(_merge_games(*batches), team)

    @staticmethod
    def _individual_scoreboard_params(sport: Sport, start: date, end: date) -> list[dict[str, str]]:
        """Scoreboard ``dates`` params covering ``[start, end]`` per sport.

        Tennis uses ranged ``YYYYMMDD-YYYYMMDD`` dates (chunked by month
        for long windows).  UFC takes a ``YYYYMM`` month bucket, so one
        param is emitted per calendar month spanned by the window.
        """
        if sport is Sport.MMA:
            params: list[dict[str, str]] = []
            cursor = date(start.year, start.month, 1)
            while cursor <= end:
                params.append({"dates": cursor.strftime("%Y%m"), "limit": "400"})
                cursor = _add_month(cursor)
            return params
        return [
            {
                "dates": (f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"),
                "limit": "400",
            }
            for chunk_start, chunk_end in _chunk_date_range(start, end)
        ]

    async def _get_merged_schedule(
        self,
        url: str,
        league: League,
        team: Team,
        param_sets: tuple[dict[str, str] | None, ...],
    ) -> list[Game]:
        """Fetch + merge the halves of a split ESPN team schedule.

        ESPN splits some team schedules across multiple calls (see
        ``_SCHEDULE_PARAM_SETS``).  The calls are independent — if one
        fails, log it and use the others (a half schedule beats none);
        only when all fail does the error propagate.  Duplicate game ids
        keep the earlier param set's version (see ``_merge_games``).
        """
        batches: list[list[Game]] = []
        errors: list[Exception] = []
        for params in param_sets:
            try:
                data = await self._get_json(url, params=params)
            except Exception as exc:
                logger.warning(
                    "ESPN schedule call failed for team %s (params=%s): %s",
                    team.id,
                    params,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_schedule(data, league, team))
        if not batches:
            raise errors[0]
        return _merge_games(*batches)

    async def get_live_games(self, league: League) -> list[Game]:
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        if league.sport in INDIVIDUAL_SPORTS:
            # Tour-wide: tennis ships the live draw, UFC the current month's
            # cards.  live_tick matches the returned games to followed rows
            # by id, so scanning the whole scoreboard is fine (no team scope).
            now = timeutil.utcnow().astimezone(_SCOREBOARD_TZ)
            dates = now.strftime("%Y%m") if league.sport is Sport.MMA else now.strftime("%Y%m%d")
            data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            return _parse_individual_scoreboard(data, league)
        today = timeutil.utcnow().astimezone(_SCOREBOARD_TZ).strftime("%Y%m%d")
        # ESPN silently caps scoreboards at 100 events without a limit;
        # busy soccer days (every league shares one scoreboard) exceed it.
        data = await self._get_json(url, params={"dates": today, "limit": "400"})
        return _parse_scoreboard(data, league)

    async def get_competition_schedule(self, league: League, start: date, end: date) -> list[Game]:
        """Every fixture in ``league`` between ``[start, end]`` (UTC dates).

        Whole-competition follows (``League.follow_all``) have no team to
        scope a per-team schedule call, so the ranged scoreboard endpoint
        (``?dates=YYYYMMDD-YYYYMMDD&limit=400``) is used instead — one
        call returns every fixture in the window (verified: the full
        World Cup season, 104 matches, in a single call).  ``limit=400``
        is load-bearing: without it ESPN silently caps at 100 events.

        Ranges longer than ~45 days are split on month boundaries
        (``_chunk_date_range``) to keep each payload small; the chunks are
        fetched independently so one failing call can't lose the rest, and
        their results are merged by game id.  Scoreboard buckets are keyed
        by US/Eastern, so the requested dates are formatted in ET to match
        ESPN's day boundaries; the final ``[start, end]`` filter is applied
        to each game's tz-aware UTC start date.
        """
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Game]] = []
        errors: list[Exception] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end):
            dates = f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
            try:
                data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            except Exception as exc:
                logger.warning(
                    "ESPN competition scoreboard call failed for league %s (dates=%s): %s",
                    league.id,
                    dates,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_scoreboard(data, league))
        if not batches and errors:
            raise errors[0]
        games = _merge_games(*batches)
        return sorted(
            (game for game in games if start <= game.start_time.date() <= end),
            key=lambda game: game.start_time,
        )

    async def get_events(self, league: League, start: date, end: date) -> list[Event]:
        """Leaderboard tournaments in ``[start, end]`` for a golf league.

        Golf is the only leaderboard sport today; every other sport returns
        ``[]``.  ESPN's golf scoreboard exposes the current/most-recent
        tournament (and the season calendar around it) — a single
        ``?dates=YYYYMMDD-YYYYMMDD`` call covers the window (chunked by
        month for long ranges, like the competition scoreboard), and each
        tournament becomes one :class:`Event` with a populated leaderboard.
        Each :class:`LeaderRow` carries the golfer's ESPN athlete id in
        ``player_id`` TRANSIENTLY; the scheduler rewrites it to the internal
        followed-team id (or None) before persisting.  Tournaments are kept
        when their span overlaps ``[start, end]`` (a multi-day event that
        merely brackets the window still counts).
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return []
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Event]] = []
        errors: list[Exception] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end):
            dates = f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
            try:
                data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            except Exception as exc:
                logger.warning(
                    "ESPN golf scoreboard call failed for league %s (dates=%s): %s",
                    league.id,
                    dates,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_golf_scoreboard(data, league))
        if not batches and errors:
            raise errors[0]
        # Merge by event id (first batch wins on duplicates) and keep any
        # tournament whose [start_time, end_time] span overlaps the window.
        merged: dict[str, Event] = {}
        for batch in batches:
            for event in batch:
                merged.setdefault(event.id, event)
        kept = [event for event in merged.values() if _event_overlaps_window(event, start, end)]
        return sorted(kept, key=lambda event: event.start_time)

    async def get_event_state(self, league: League, provider_event_key: str) -> Event | None:
        """Current state of a single golf tournament, or None if unknown.

        Golf's ``/summary`` endpoint is unreliable (returns a non-JSON error
        body for an event id — verified live), so the current scoreboard is
        scanned for the tournament whose id matches ``provider_event_key``.
        Non-leaderboard leagues always return None.
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return None
        target = f"espn:{provider_event_key}"
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        data = await self._get_json(url, params={"limit": "400"})
        for event in _parse_golf_scoreboard(data, league):
            if event.id == target:
                return event
        return None

    async def get_game_state(self, league: League, provider_game_key: str) -> GameState | None:
        if league.sport in INDIVIDUAL_SPORTS:
            return await self._get_individual_game_state(league, provider_game_key)
        url = f"{_SITE_BASE}/{league.provider_key}/summary"
        try:
            data = await self._get_json(url, params={"event": provider_game_key})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return _parse_summary_state(data, league, provider_game_key)

    async def _get_individual_game_state(
        self, league: League, provider_game_key: str
    ) -> GameState | None:
        """State of one tennis match / UFC bout via the tour scoreboard.

        The ``/summary`` endpoint is unreliable for individual sports
        (returns an error body for a bout id, expects the parent event id
        for tennis), so the live tour/card scoreboard is scanned for the
        competition whose id matches ``provider_game_key``.  ``Game.state``
        is already populated by the scoreboard parse, so the matching
        game's state is returned directly.
        """
        target = f"espn:{provider_game_key}"
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        now = timeutil.utcnow().astimezone(_SCOREBOARD_TZ)
        dates = now.strftime("%Y%m") if league.sport is Sport.MMA else now.strftime("%Y%m%d")
        data = await self._get_json(url, params={"dates": dates, "limit": "400"})
        for game in _parse_individual_scoreboard(data, league):
            if game.id == target:
                return game.state
        return None

    async def get_game_summary(self, league: League, provider_game_key: str) -> GameSummary | None:
        """On-demand box score (period lines + performers) for one game.

        Reuses the same ``/summary`` endpoint as ``get_game_state`` and
        parses its header linescores + top-level ``leaders``.  Best-effort
        and never raises: individual sports (tennis/MMA/golf), whose
        ``/summary`` endpoint is unreliable, return ``None`` up front; any
        HTTP failure or unparseable body also degrades to ``None``.
        """
        if league.sport in INDIVIDUAL_SPORTS:
            # The summary endpoint is unreliable for individual sports (see
            # _get_individual_game_state); no box score is offered.
            return None
        url = f"{_SITE_BASE}/{league.provider_key}/summary"
        try:
            data = await self._get_json(url, params={"event": provider_game_key})
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "ESPN game summary call failed for game %s (league %s): %s",
                provider_game_key,
                league.id,
                exc,
            )
            return None
        return _parse_game_summary(data, league, provider_game_key)

    async def get_game_odds(self, league: League, provider_game_key: str) -> GameOdds | None:
        """On-demand betting lines + win-probability for one game.

        Odds come from the same ``/summary`` payload as the box score
        (``pickcenter``); win-probability from the core API's per-game
        ``predictor`` feed.  The two live on different hosts, so they're
        fetched concurrently and each half degrades independently — a normal
        predictor 404 (no projection for this game) still yields the odds,
        and vice-versa.  Individual sports return ``None`` up front; an
        all-empty result also collapses to ``None``.
        """
        if league.sport in INDIVIDUAL_SPORTS:
            return None
        summary_url = f"{_SITE_BASE}/{league.provider_key}/summary"
        predictor_url = (
            f"{_CORE_BASE}/{_core_event_path(league.provider_key)}"
            f"/events/{provider_game_key}/competitions/{provider_game_key}/predictor"
        )
        summary_data, predictor_data = await asyncio.gather(
            self._get_json(summary_url, params={"event": provider_game_key}),
            self._get_json(predictor_url),
            return_exceptions=True,
        )
        if isinstance(summary_data, dict):
            provider, details, home_ml, away_ml, spread, over_under = _parse_pickcenter(
                summary_data
            )
        else:
            if isinstance(summary_data, TransientProviderError):
                raise summary_data
            provider = details = home_ml = away_ml = spread = over_under = None
        if isinstance(predictor_data, dict):
            home_pct, away_pct = _parse_predictor(predictor_data)
        else:
            # A 404 (no projection) is expected and benign; let only a
            # transient error surface so the breaker can react.
            if isinstance(predictor_data, TransientProviderError):
                raise predictor_data
            home_pct = away_pct = None
        odds = GameOdds(
            provider=provider,
            details=details,
            home_moneyline=home_ml,
            away_moneyline=away_ml,
            spread=spread,
            over_under=over_under,
            home_win_pct=home_pct,
            away_win_pct=away_pct,
        )
        return odds if _odds_has_signal(odds) else None

    async def get_standings(self, league: League) -> Standings:
        if league.sport is Sport.TENNIS:
            # Tennis "standings" are the tour ranking (rank + points).
            url = f"{_SITE_BASE}/{league.provider_key}/rankings"
            data = await self._get_json(url)
            return _parse_tennis_rankings(data, league, self._known_teams.get(league.id))
        if league.sport is Sport.MMA:
            # MMA has no league table; expose an empty Standings.
            return Standings(
                league_id=league.id,
                season=str(timeutil.utcnow().year),
                rows=(),
                fetched_at=timeutil.utcnow(),
            )
        url = f"{_STANDINGS_BASE}/{league.provider_key}/standings"
        # ``level=3`` exposes division depth (conference -> division ->
        # entries) for NBA/MLB/NHL/NFL; single-table leagues (soccer)
        # ignore it and still return one flat child.
        data = await self._get_json(url, params={"level": "3"})
        return _parse_standings(data, league, self._known_teams.get(league.id))

    async def get_roster(self, league: League, team: Team) -> Roster:
        self._register(league, team)
        if league.sport in INDIVIDUAL_SPORTS:
            # An athlete is a single-member "team"; there is no roster.
            return Roster(team_id=team.id, players=(), fetched_at=timeutil.utcnow())
        url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/roster"
        data = await self._get_json(url)
        roster = _parse_roster(data, league, team)
        return await self._attach_stat_lines(league, roster)

    async def _attach_stat_lines(self, league: League, roster: Roster) -> Roster:
        """Populate ``Player.stat_line`` + ``career_stat_line`` from overviews.

        One overview fetch per athlete yields both the current-season line and
        the career line (the payload carries "Regular Season" and "Career"
        splits side by side).  Best-effort and bounded: caps the athlete count
        and concurrency, and swallows every per-athlete failure (a missing line
        just stays ``None``).  Never raises.
        """
        overview_path = _overview_path(league.sport, league.provider_key)
        if overview_path is None or not roster.players:
            return roster

        targets = roster.players[:_STAT_LINE_MAX_ATHLETES]
        semaphore = asyncio.Semaphore(_STAT_LINE_CONCURRENCY)

        async def fetch(player: Player) -> tuple[str | None, str | None]:
            athlete_id = _athlete_id(player)
            if athlete_id is None:
                return None, None
            url = f"{_WEB_BASE}/{overview_path}/athletes/{athlete_id}/overview"
            async with semaphore:
                try:
                    data = await self._get_json(url)
                except (httpx.HTTPError, TransientProviderError):
                    # Stat lines are best-effort garnish: a per-athlete blip
                    # (incl. a transient outage) just leaves the line None and
                    # must not trip the provider breaker for the whole app.
                    return None, None
            return (
                _stat_line_from_overview(data, league.sport),
                _career_line_from_overview(data, league.sport),
            )

        lines = await asyncio.gather(*(fetch(player) for player in targets))
        by_id = {player.id: line for player, line in zip(targets, lines, strict=False)}

        def _apply(player: Player) -> Player:
            season, career = by_id.get(player.id, (None, None))
            if season is None and career is None:
                return player
            return replace(
                player,
                stat_line=season if season is not None else player.stat_line,
                career_stat_line=career if career is not None else player.career_stat_line,
            )

        players = tuple(_apply(player) for player in roster.players)
        return replace(roster, players=players)

    async def get_news(self, league: League, team: Team) -> list[NewsItem]:
        self._register(league, team)
        url = f"{_SITE_BASE}/{league.provider_key}/news"
        # Individual sports have no per-team news scope on this endpoint;
        # fetch league news best-effort (the news service still merges in
        # the athlete's Google News query).  Any failure yields no items.
        params = (
            {"limit": "20"}
            if league.sport in INDIVIDUAL_SPORTS
            else {"team": str(team.provider_key), "limit": "20"}
        )
        try:
            data = await self._get_json(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "ESPN news call failed for team %s (league %s): %s",
                team.id,
                league.id,
                exc,
            )
            return []
        return _parse_news(data, team=team)

    async def get_league_news(self, league: League) -> list[NewsItem]:
        """Competition-wide news for a whole-league (``follow_all``) follow.

        The same ``{provider_key}/news`` endpoint, called without a ``team``
        filter, returns the whole competition's articles.  Items carry
        ``league_id=league.id`` and ``team_id=None``.
        """
        url = f"{_SITE_BASE}/{league.provider_key}/news"
        try:
            data = await self._get_json(url, params={"limit": "50"})
        except httpx.HTTPError as exc:
            logger.warning("ESPN league news call failed for league %s: %s", league.id, exc)
            return []
        return _parse_news(data, league_id=league.id)

    async def get_team_location(self, league: League, team: Team) -> TeamLocation | None:
        """Home venue for the map view via ``/teams/{provider_key}``.

        Returns a ``TeamLocation`` carrying the venue name (plus city/state
        for geocode context) with ``lat``/``lon`` left ``None`` — ESPN does
        not expose usable coordinates on this endpoint, so the geocode
        service resolves them.  Never raises: an HTTP failure, an
        unparseable body, or a team with no venue all degrade to ``None``.
        """
        self._register(league, team)
        url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}"
        try:
            data = await self._get_json(url)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "ESPN team-location call failed for team %s (league %s): %s",
                team.id,
                league.id,
                exc,
            )
            return None
        return _parse_team_location(data, provider_key=str(team.provider_key))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
