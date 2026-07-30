"""Historical archives: a followed team's past-season results.

``GET /history/results/{team_id}?season=YYYY`` fetches the season's
FINAL games live from ESPN (never stored in the games table — that
stays the near-window working set) and caches the serialized payload in
Redis, long-lived for finished seasons.  Standings archives live on the
standings route (``GET /standings/{league_id}?season=``).
``GET /history/on-this-day`` scans the same season archives for every
followed team's past games on today's local month-day.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.domain import Sport
from app.providers import espn_history
from app.schemas import GameOut, OnThisDayOut, OnThisDayTeamOut
from app.services import on_this_day, repository, season_results
from app.timeutil import local_today

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/history/results/{team_id}", response_model=list[GameOut])
async def season_results_route(
    team_id: str,
    season: int = Query(ge=1990, le=2100),
    session: AsyncSession = Depends(get_session),
) -> list[GameOut]:
    team_row = await repository.get_team(session, team_id)
    if team_row is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    league_row = await repository.get_league(session, team_row.league_id)
    if league_row is None:
        raise HTTPException(status_code=404, detail="Team has no league")
    if league_row.provider != "espn" or not espn_history.supports_history(Sport(league_row.sport)):
        raise HTTPException(status_code=404, detail="No season archive for this league")

    out = await season_results.season_results_out(league_row, team_row, season)
    if not out:
        raise HTTPException(status_code=404, detail="Season not available from the provider")
    return out


@router.get("/history/on-this-day", response_model=OnThisDayOut)
async def on_this_day_route(session: AsyncSession = Depends(get_session)) -> OnThisDayOut:
    """Every followed team's past games on today's local month-day.

    Always 200: an empty ``teams`` list is the normal no-anniversary
    state, and teams on providers/sports without archives are skipped
    silently rather than erroring the whole payload.
    """
    settings = get_settings()
    tz = settings.tzinfo
    today = local_today(tz)

    leagues_by_id = {league.id: league for league in await repository.list_leagues(session)}
    teams: list[OnThisDayTeamOut] = []
    for team_row in await repository.list_teams(session):
        league_row = leagues_by_id.get(team_row.league_id)
        # Same archive gate as the results route, applied before any fetch.
        if league_row is None or league_row.provider != "espn":
            continue
        if not espn_history.supports_history(Sport(league_row.sport)):
            continue
        games = await on_this_day.team_games_on_day(league_row, team_row, today, tz)
        if not games:
            continue
        teams.append(OnThisDayTeamOut(team_id=team_row.id, team_name=team_row.name, games=games))
    return OnThisDayOut(date=today.isoformat(), teams=teams)
