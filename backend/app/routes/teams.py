"""Followed leagues and teams."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import LeagueOut, TeamOut, TeamsOut
from app.services import repository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/teams", response_model=TeamsOut)
async def teams(session: AsyncSession = Depends(get_session)) -> TeamsOut:
    leagues = await repository.list_leagues(session)
    team_rows = await repository.list_teams(session)
    return TeamsOut(
        leagues=[
            LeagueOut(
                id=league.id,
                sport=league.sport,
                name=league.name,
                follow_all=league.follow_all,
            )
            for league in leagues
        ],
        teams=[
            TeamOut(
                id=team.id,
                league_id=team.league_id,
                name=team.name,
                abbreviation=team.abbreviation,
                logo_url=team.logo_url,
                color=team.color,
            )
            for team in team_rows
        ],
    )
