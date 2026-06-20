"""iCalendar export of stored games (-30d .. +60d around now).

By default the feed covers every followed game.  An optional
``?team_id=`` narrows the feed to a single team, which (paired with the
frontend "Subscribe" control) lets a user subscribe to a live per-team
calendar in Apple/Google Calendar.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import repository
from app.services.ics import games_to_ics
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

PAST_WINDOW = timedelta(days=30)
FUTURE_WINDOW = timedelta(days=60)


@router.get("/calendar.ics")
async def calendar_ics(
    team_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if team_id is not None and await repository.get_team(session, team_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_id}")

    now = utcnow()
    rows = await repository.games_between(
        session, now - PAST_WINDOW, now + FUTURE_WINDOW, team_id=team_id
    )
    leagues_by_id = {
        league.id: league for league in await repository.list_leagues(session)
    }
    filename = f"sportsdash-{team_id}.ics" if team_id is not None else "sportsdash.ics"
    return Response(
        content=games_to_ics(rows, leagues_by_id),
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
