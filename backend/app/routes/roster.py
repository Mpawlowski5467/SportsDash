"""Team roster (last fetched snapshot), players ordered by name."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import RosterOut
from app.services import repository
from app.services.serialize import player_to_out
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/roster/{team_id}", response_model=RosterOut)
async def roster(team_id: str, session: AsyncSession = Depends(get_session)) -> RosterOut:
    team = await repository.get_team(session, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")

    players = sorted(await repository.get_roster(session, team_id), key=lambda p: p.name)
    fetched_at = ensure_utc(team.roster_updated_at) if team.roster_updated_at is not None else None
    return RosterOut(
        team_id=team.id,
        team_name=team.name,
        fetched_at=fetched_at,
        players=[player_to_out(player) for player in players],
    )
