"""Roster, injuries, and season/career stat-line formatting.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping


from app import timeutil
from app.models.domain import (
    INDIVIDUAL_SPORTS,
    League,
    Player,
    PlayerStatus,
    Roster,
    Sport,
    Team,
)

from app.providers.espn.common import _coerce_float

logger = logging.getLogger(__name__)


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
