"""Schedule chunking utilities + box score / game summary / plays / win probability / odds parsers.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Iterable


from app.models.domain import (
    Game,
    GameOdds,
    GamePlay,
    GameState,
    GameSummary,
    Goal,
    League,
    PeriodScore,
    Performer,
    Sport,
    TeamStat,
)

from app.providers.espn.common import (
    _build_state,
    _coerce_float,
    _coerce_int,
    _find_side,
    _hockey_label,
    _quarter_label,
)
from app.providers.espn.individual import _athlete_name

logger = logging.getLogger(__name__)


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
