"""Playoff bracket for a league (NBA/MLB/NHL series).

Soccer cup knockouts are derived on the frontend from synced fixtures; this
serves the US leagues' best-of-N playoff series from ESPN
(``espn_playoffs``), grouped into rounds.  Returns empty rounds when the
league isn't a supported sport or isn't currently in its playoffs (e.g. MLB
in June) — the frontend shows a clean "no active bracket" state then.
Cached in Redis since playoff series change only between games.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.domain import Sport
from app.providers import espn_playoffs
from app.schemas import (
    BracketOut,
    BracketRoundOut,
    BracketSeriesOut,
    BracketSideOut,
)
from app.services import cache, repository

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL_SECONDS = 1800


@router.get("/bracket/{league_id}", response_model=BracketOut)
async def bracket(
    league_id: str, session: AsyncSession = Depends(get_session)
) -> BracketOut:
    league = await repository.get_league(session, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")

    sport = Sport(league.sport)
    if league.provider != "espn" or not espn_playoffs.supports(sport):
        return BracketOut(league_id=league.id, league_name=league.name, rounds=[])

    cache_key = f"bracket:{league_id}"
    cached = await cache.cache_get_json(cache_key)
    if cached is not None:
        return BracketOut(**cached)

    rounds = await espn_playoffs.fetch_playoff_bracket(league.provider_key, sport)
    result = BracketOut(
        league_id=league.id,
        league_name=league.name,
        rounds=[
            BracketRoundOut(
                name=rnd.name,
                series=[
                    BracketSeriesOut(
                        team1=BracketSideOut(
                            name=s.team1.name,
                            abbreviation=s.team1.abbreviation,
                            logo_url=s.team1.logo_url,
                        ),
                        team2=BracketSideOut(
                            name=s.team2.name,
                            abbreviation=s.team2.abbreviation,
                            logo_url=s.team2.logo_url,
                        ),
                        summary=s.summary,
                        conference=s.conference,
                    )
                    for s in rnd.series
                ],
            )
            for rnd in rounds
        ],
    )
    # Only cache a non-empty bracket; an empty result may just be a transient
    # fetch miss and should be retried on the next request.
    if result.rounds:
        await cache.cache_set_json(cache_key, result.model_dump(), _CACHE_TTL_SECONDS)
    return result
