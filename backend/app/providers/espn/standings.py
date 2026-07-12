"""Standings, season labels, and tennis rankings parsers.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Mapping


from app import timeutil
from app.models.domain import (
    League,
    Sport,
    StandingRow,
    Standings,
)

from app.providers.espn.common import (
    _coerce_float,
    _coerce_int,
    _team_abbreviation,
    _team_color,
    _team_logo,
    _team_name,
)

logger = logging.getLogger(__name__)


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
