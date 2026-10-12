"""One team-season of FINAL games, serialized and cached (``services/cache``).

Shared by the history route (the Results view's season picker) and the
cross-season head-to-head builder, so a season is fetched from ESPN at
most once per TTL wherever it's needed.  A finished season's results
never change; a season still being played does change (the head-to-head
builder scans the current year too), so it gets a shorter TTL.  Only a
season every schedule call answered for is cached at all.
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
# For this year's key and last year's: either can be the season in play.
_CURRENT_SEASON_TTL_SECONDS = 6 * 3600


def _cache_key(team_id: str, season: int) -> str:
    return f"history:results:{team_id}:{season}"


async def season_results_out(
    league_row: LeagueORM, team_row: TeamORM, season: int
) -> list[GameOut]:
    """The season's FINAL games (newest first) as API shapes; ``[]`` on miss.

    Best-effort like everything provider-facing: cache first, then a live
    ESPN fetch.  A fetch that failed outright degrades to an empty list; one
    that failed in part returns the games it did get.  Neither is cached, so
    both are fetched again on the next call.
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

    complete = True
    try:
        games = await espn_history.fetch_season_results(
            convert.league_from_row(league_row), convert.team_from_row(team_row), season
        )
    except espn_history.EspnHistoryError as exc:
        # One of the season's schedule calls failed (hockey, football and
        # baseball fetch the regular season and the playoffs separately).
        # What did come back is still worth showing, but it is not the
        # season: cached, it would stand in for it for up to a week.
        games, complete = exc.games, False
    if not games:
        return []

    out = [domain_game_to_out(game, league_row) for game in games]
    if complete:
        # ESPN keys a cross-year season by its ENDING year in some sports
        # (the NBA's and NHL's 2025-26 is 2026) and by its STARTING year in
        # others (soccer's and the NFL's 2025-26 is 2025; all four checked
        # live 2026-10-11).  So from 1 January the season in play can be
        # LAST year's key, and it must not be filed away as finished for a
        # week.  The price is that a last-year key which really is finished
        # is fetched again every 6 hours it is asked for, not every 7 days.
        in_play = season >= utcnow().year - 1
        ttl = _CURRENT_SEASON_TTL_SECONDS if in_play else _PAST_SEASON_TTL_SECONDS
        await cache.cache_set_json(key, [entry.model_dump(mode="json") for entry in out], ttl)
    return out
