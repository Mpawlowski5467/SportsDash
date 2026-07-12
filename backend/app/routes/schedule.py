"""Schedule over a local-day date range, optionally filtered to one team.

``start``/``end`` query params are local calendar days (YYYY-MM-DD) and the
range is inclusive on both ends: the UTC window runs from the start of the
``start`` day to the end of the ``end`` day in the configured timezone.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.orm import LeagueORM
from app.models import convert
from app.services.game_detail import fetch_weather
from app.schemas import GameOut, WeatherOut
from app.services import repository
from app.services.serialize import games_to_out
from app.timeutil import local_day_bounds, local_today

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PAST_DAYS = 7
DEFAULT_FUTURE_DAYS = 45


async def _schedule_games(
    session: AsyncSession,
    start: date | None,
    end: date | None,
    team_id: str | None,
) -> list[GameOut]:
    settings = get_settings()
    tz = settings.tzinfo
    today = local_today(tz)
    if start is None:
        start = today - timedelta(days=DEFAULT_PAST_DAYS)
    if end is None:
        end = today + timedelta(days=DEFAULT_FUTURE_DAYS)

    start_utc = local_day_bounds(start, tz)[0]
    end_utc = local_day_bounds(end, tz)[1]  # end day inclusive

    rows = await repository.games_between(session, start_utc, end_utc, team_id=team_id)
    leagues_by_id = {league.id: league for league in await repository.list_leagues(session)}
    return games_to_out(rows, leagues_by_id)


@router.get("/schedule", response_model=list[GameOut])
async def schedule(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    team_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[GameOut]:
    return await _schedule_games(session, start, end, team_id)


# Declared BEFORE "/schedule/{team_id}" so the literal "weather" segment is
# matched here rather than captured as a team id.
@router.get("/schedule/weather", response_model=dict[str, WeatherOut])
async def schedule_weather(
    ids: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, WeatherOut]:
    """Best-effort venue forecasts for the given games, keyed by game id.

    ``ids`` is a comma-separated list of game ids (game ids never contain a
    comma).  The map includes ONLY games that are outdoor, scheduled, have
    resolvable venue coordinates, and returned a forecast — every other game
    is simply absent (the calendar shows no glyph for it).  Reuses the same
    per-game ``game_detail.fetch_weather`` the detail endpoint uses, so a calendar
    glyph and the modal forecast always agree.  Never fails the request.
    """
    out: dict[str, WeatherOut] = {}
    leagues: dict[str, LeagueORM] = {}
    # Bound the fan-out: a month view can list many games, but only outdoor
    # scheduled ones get a forecast and the frontend pre-filters to those.
    for game_id in [gid for gid in ids.split(",") if gid][:80]:
        row = await repository.get_game(session, game_id)
        if row is None:
            continue
        league_row = leagues.get(row.league_id)
        if league_row is None:
            league_row = await repository.get_league(session, row.league_id)
            if league_row is None:
                continue
            leagues[row.league_id] = league_row
        forecast = await fetch_weather(session, convert.league_from_row(league_row), row)
        if forecast is not None:
            out[game_id] = forecast
    return out


@router.get("/schedule/{team_id}", response_model=list[GameOut])
async def schedule_for_team(
    team_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[GameOut]:
    if await repository.get_team(session, team_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_id}")
    return await _schedule_games(session, start, end, team_id)
