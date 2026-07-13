"""One team-season of FINAL games, serialized and redis-cached.

Shared by the history route (the Results view's season picker) and the
cross-season head-to-head builder, so a season is fetched from ESPN at
most once per TTL wherever it's needed.  A finished season's results
never change; the current season's may (the head-to-head builder scans
the current year too), so it gets a shorter TTL.
"""

from __future__ import annotations

import logging

from app.models import convert
from app.models.orm import LeagueORM, TeamORM
from app.providers import espn_history
from app.schemas import GameOut
from app.services import cache
from app.services.serialize import domain_game_to_out
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

_PAST_SEASON_TTL_SECONDS = 7 * 24 * 3600
_CURRENT_SEASON_TTL_SECONDS = 6 * 3600


def _cache_key(team_id: str, season: int) -> str:
    return f"history:results:{team_id}:{season}"


async def season_results_out(
    league_row: LeagueORM, team_row: TeamORM, season: int
) -> list[GameOut]:
    """The season's FINAL games (newest first) as API shapes; ``[]`` on miss.

    Best-effort like everything provider-facing: cache first, then a live
    ESPN fetch; failures degrade to an empty list and are re-fetchable.
    """
    key = _cache_key(team_row.id, season)
    cached = await cache.cache_get_json(key)
    if isinstance(cached, list):
        try:
            return [GameOut.model_validate(entry) for entry in cached]
        except Exception:
            logger.warning(
                "season_results: ignoring unparseable cache for %s/%s", team_row.id, season
            )

    games = await espn_history.fetch_season_results(
        convert.league_from_row(league_row), convert.team_from_row(team_row), season
    )
    if not games:
        return []

    out = [domain_game_to_out(game, league_row) for game in games]
    ttl = _CURRENT_SEASON_TTL_SECONDS if season >= utcnow().year else _PAST_SEASON_TTL_SECONDS
    await cache.cache_set_json(key, [entry.model_dump(mode="json") for entry in out], ttl)
    return out
