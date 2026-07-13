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
from app.models import convert
from app.models.domain import Sport
from app.providers import espn_history
from app.schemas import GameOut
from app.services import cache, repository
from app.services.serialize import domain_game_to_out
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

# A finished season's results never change; the current season's might
# (this endpoint is offered for past seasons, but guard the TTL anyway).
_PAST_SEASON_TTL_SECONDS = 7 * 24 * 3600
_CURRENT_SEASON_TTL_SECONDS = 6 * 3600


def _cache_key(team_id: str, season: int) -> str:
    return f"history:results:{team_id}:{season}"


@router.get("/history/results/{team_id}", response_model=list[GameOut])
async def season_results(
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

    cached = await cache.cache_get_json(_cache_key(team_id, season))
    if isinstance(cached, list):
        try:
            return [GameOut.model_validate(entry) for entry in cached]
        except Exception:
            logger.warning("history: ignoring unparseable cache for %s/%s", team_id, season)

    games = await espn_history.fetch_season_results(
        convert.league_from_row(league_row), convert.team_from_row(team_row), season
    )
    if not games:
        raise HTTPException(status_code=404, detail="Season not available from the provider")

    out = [domain_game_to_out(game, league_row) for game in games]
    ttl = _CURRENT_SEASON_TTL_SECONDS if season >= utcnow().year else _PAST_SEASON_TTL_SECONDS
    await cache.cache_set_json(
        _cache_key(team_id, season), [entry.model_dump(mode="json") for entry in out], ttl
    )
    return out
