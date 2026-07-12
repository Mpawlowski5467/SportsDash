"""Recent final scores for a team, newest first."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import GameOut
from app.services import repository
from app.services.serialize import games_to_out

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/results/{team_id}", response_model=list[GameOut])
async def results(
    team_id: str,
    limit: int = Query(default=25, ge=1),
    session: AsyncSession = Depends(get_session),
) -> list[GameOut]:
    team = await repository.get_team(session, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")

    rows = await repository.results_for_team(session, team_id, limit=limit)
    leagues_by_id = {league.id: league for league in await repository.list_leagues(session)}
    return games_to_out(rows, leagues_by_id)
