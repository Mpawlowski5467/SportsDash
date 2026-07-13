"""Historical archives: a followed team's past-season results.

``GET /history/results/{team_id}?season=YYYY`` fetches the season's
FINAL games live from ESPN (never stored in the games table — that
stays the near-window working set) and caches the serialized payload in
Redis, long-lived for finished seasons.  Standings archives live on the
standings route (``GET /standings/{league_id}?season=``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.domain import Sport
from app.providers import espn_history
from app.schemas import GameOut
from app.services import repository, season_results

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
