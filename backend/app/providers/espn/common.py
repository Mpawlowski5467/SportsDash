"""Shared constants, coercion helpers, and status/period normalization.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


from app import timeutil
from app.models.domain import (
    GamePhase,
    GameState,
    League,
    Sport,
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
