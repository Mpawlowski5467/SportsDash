"""Aggregated team news (from each team's RSS feeds), newest first."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import NewsItemOut, NewsRefreshOut
from app.services import news as news_service
from app.services import repository
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/news", response_model=list[NewsItemOut])
async def news(
    team_id: str | None = Query(default=None),
    league_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
    session: AsyncSession = Depends(get_session),
) -> list[NewsItemOut]:
    items = await repository.list_news(
        session, team_id=team_id, league_id=league_id, limit=limit
    )
    return [
        NewsItemOut(
            id=item.id,
            team_id=item.team_id,
            league_id=item.league_id,
            title=item.title,
            url=item.url,
            source=item.source,
            published_at=(
                ensure_utc(item.published_at)
                if item.published_at is not None
                else None
            ),
            summary=item.summary,
            image_url=item.image_url,
        )
        for item in items
    ]


@router.post("/news/refresh", response_model=NewsRefreshOut)
async def refresh_news(
    team_id: str | None = Query(default=None),
    league_id: str | None = Query(default=None),
) -> NewsRefreshOut:
    """Pull fresh articles right now (provider + RSS + Google News).

    Scoped to ``team_id`` (one followed team) or ``league_id`` (one
    whole-competition follow) when given — fast — else every followed team
    and competition. The GET /news poll only reads the DB, which the hourly
    scheduler populates; this is the manual "refresh" button so a stale feed
    isn't stuck waiting for the next cron tick.
    """
    inserted = await news_service.trigger_refresh(team_id, league_id)
    logger.info(
        "manual news refresh (%s): %d new item(s)",
        team_id or (f"league:{league_id}" if league_id else "all"),
        inserted,
    )
    return NewsRefreshOut(inserted=inserted)
