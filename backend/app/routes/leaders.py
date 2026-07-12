"""Player stat leaders for a league.

Two sources, picked by sport:

* NBA / MLB / NHL — league-wide leaders from ESPN's per-athlete stats feed
  (``espn_leaders``), ranked by a headline stat. Players on a team you
  follow are flagged ``highlighted`` (matched by ESPN athlete id against
  your followed teams' rosters), so your guys stand out in the full board.
* Everything else (e.g. off-season soccer) — a roster-derived board built
  from your FOLLOWED teams' players (soccer's real league-wide board is the
  box-score Golden Boot in ``routes/scorers``).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.domain import Sport
from app.models.orm import TeamORM
from app.providers import espn_leaders
from app.schemas import StatLeaderOut, StatLeadersOut
from app.services import cache, repository

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL_SECONDS = 900

_LEADING_STAT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(.+?)\s*$")


def _leading_stat(stat_line: str | None) -> tuple[float, str] | None:
    """Parse a roster line's headline stat into ``(value, label)``."""
    if not stat_line:
        return None
    match = _LEADING_STAT.match(stat_line.split("·", 1)[0])
    if match is None:
        return None
    try:
        return float(match.group(1)), match.group(2).strip()
    except ValueError:
        return None


async def _followed_athletes(session: AsyncSession, teams: list[TeamORM]) -> dict[str, TeamORM]:
    """Bare ESPN athlete id -> the followed team they play for.

    Roster ids are stored provider-prefixed (``"espn:4711294"``) but the
    byathlete feed uses the bare id (``"4711294"``); strip the prefix so the
    two actually match (otherwise nothing ever highlights).
    """
    by_athlete: dict[str, TeamORM] = {}
    for team in teams:
        for player in await repository.get_roster(session, team.id):
            bare = player.id.split(":", 1)[1] if ":" in player.id else player.id
            by_athlete[bare] = team
    return by_athlete


@router.get("/leaders/{league_id}", response_model=StatLeadersOut)
async def leaders(league_id: str, session: AsyncSession = Depends(get_session)) -> StatLeadersOut:
    league = await repository.get_league(session, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")

    sport = Sport(league.sport)
    teams = [team for team in await repository.list_teams(session) if team.league_id == league_id]

    # --- League-wide leaders (NBA/MLB/NHL) from the ESPN athlete stats feed ---
    if league.provider == "espn" and espn_leaders.supports(sport):
        cache_key = f"leaders:{league_id}"
        cached = await cache.cache_get_json(cache_key)
        if cached is not None:
            return StatLeadersOut(**cached)
        entries = await espn_leaders.fetch_league_leaders(league.provider_key, sport)
        if entries:
            by_athlete = await _followed_athletes(session, teams)
            rows: list[StatLeaderOut] = []
            for index, entry in enumerate(entries):
                followed_team = by_athlete.get(entry.athlete_id)
                rows.append(
                    StatLeaderOut(
                        rank=index + 1,
                        player_id=entry.athlete_id,
                        name=entry.player,
                        position=entry.position,
                        team_id=followed_team.id if followed_team else "",
                        team_name=followed_team.name if followed_team else entry.team,
                        team_logo_url=followed_team.logo_url if followed_team else None,
                        team_color=followed_team.color if followed_team else None,
                        value=entry.value,
                        stat_label=entry.stat_label,
                        # The headline value+unit is shown prominently already;
                        # the position (below) is the extra context here.
                        detail="",
                        highlighted=followed_team is not None,
                    )
                )
            result = StatLeadersOut(
                league_id=league.id,
                league_name=league.name,
                sport=league.sport,
                stat_label=entries[0].stat_label,
                rows=rows,
            )
            await cache.cache_set_json(cache_key, result.model_dump(), _CACHE_TTL_SECONDS)
            return result
        # fall through to the roster board if the feed had nothing

    # --- Roster-derived board (your followed teams) --------------------------
    scored: list[tuple[float, StatLeaderOut]] = []
    for team in teams:
        for player in await repository.get_roster(session, team.id):
            parsed = _leading_stat(player.stat_line)
            if parsed is None:
                continue
            value, label = parsed
            scored.append(
                (
                    value,
                    StatLeaderOut(
                        rank=0,
                        player_id=player.id,
                        name=player.name,
                        position=player.position,
                        team_id=team.id,
                        team_name=team.name,
                        team_logo_url=team.logo_url,
                        team_color=team.color,
                        value=value,
                        stat_label=label,
                        detail=player.stat_line or "",
                        highlighted=False,
                    ),
                )
            )

    scored.sort(key=lambda item: (-item[0], item[1].name))
    rows = [
        row.model_copy(update={"rank": index + 1}) for index, (_, row) in enumerate(scored[:30])
    ]
    return StatLeadersOut(
        league_id=league.id,
        league_name=league.name,
        sport=league.sport,
        stat_label=rows[0].stat_label if rows else "",
        rows=rows,
    )
