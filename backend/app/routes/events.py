"""Leaderboard events (golf tournaments, …) over a local-day window.

Mirrors the schedule route's date handling: ``start``/``end`` are local
calendar days, inclusive on both ends, translated to a UTC window in the
configured timezone.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.schemas import EventOut
from app.services import repository
from app.services.serialize import event_to_out, events_to_out
from app.timeutil import local_day_bounds, local_today

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PAST_DAYS = 7
DEFAULT_FUTURE_DAYS = 45


@router.get("/events", response_model=list[EventOut])
async def events(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    settings = get_settings()
    tz = settings.tzinfo
    today = local_today(tz)
    if start is None:
        start = today - timedelta(days=DEFAULT_PAST_DAYS)
    if end is None:
        end = today + timedelta(days=DEFAULT_FUTURE_DAYS)

    start_utc = local_day_bounds(start, tz)[0]
    end_utc = local_day_bounds(end, tz)[1]  # end day inclusive

    rows = await repository.events_between(session, start_utc, end_utc)
    leagues_by_id = {
        league.id: league for league in await repository.list_leagues(session)
    }
    return events_to_out(rows, leagues_by_id)


@router.get("/events/{event_id}", response_model=EventOut)
async def event_detail(
    event_id: str, session: AsyncSession = Depends(get_session)
) -> EventOut:
    row = await repository.get_event(session, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown event: {event_id}")
    league = await repository.get_league(session, row.league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Event has no league")
    return event_to_out(row, league)
