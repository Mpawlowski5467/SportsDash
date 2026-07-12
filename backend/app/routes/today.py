"""Today's slate of games for the configured local timezone."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.schemas import TodayOut
from app.services import repository
from app.services.serialize import events_to_out, games_to_out
from app.timeutil import local_day_bounds, local_today, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/today", response_model=TodayOut)
async def today(session: AsyncSession = Depends(get_session)) -> TodayOut:
    settings = get_settings()
    tz = settings.tzinfo
    day = local_today(tz)
    start_utc, end_utc = local_day_bounds(day, tz)

    rows = await repository.games_between(session, start_utc, end_utc)
    leagues_by_id = {league.id: league for league in await repository.list_leagues(session)}
    games = sorted(games_to_out(rows, leagues_by_id), key=lambda g: g.start_time)

    # Leaderboard events (golf, …) run for multiple days; surface any that
    # are live now or starting within the local day window.
    from datetime import timedelta

    event_rows = await repository.active_events(
        session, utcnow(), lookahead=end_utc - utcnow() if end_utc > utcnow() else timedelta(0)
    )
    events = events_to_out(event_rows, leagues_by_id)

    return TodayOut(date=day.isoformat(), timezone=settings.timezone, games=games, events=events)
