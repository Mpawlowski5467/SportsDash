"""A followed team's past games that fell on today's calendar date.

Walks the team's recent seasons through the redis-cached season-results
service — the same sequential scan as the head-to-head builder, so the
upstream cost is shared and paid at most once per TTL — and keeps the
games whose LOCAL calendar month-day matches today's, excluding actual
today (that slate belongs to the Today view).  The assembled per-team
list gets its own day-long cache on top so repeated dashboard polls cost
nothing; like everything cache-adjacent it is best-effort and
correctness never depends on Redis.
"""

from __future__ import annotations

import logging
from datetime import date
from zoneinfo import ZoneInfo

from app.models.orm import LeagueORM, TeamORM
from app.schemas import GameOut
from app.services import cache, season_results
from app.timeutil import to_local

logger = logging.getLogger(__name__)

# Same scan depth as the head-to-head builder: the current calendar year
# resolves to the in-progress (or just-finished) season key in every
# sport, so this covers roughly the last five seasons.
SEASONS_BACK = 5
_TTL_SECONDS = 24 * 3600


def _cache_key(team_id: str, month_day: str) -> str:
    return f"onthisday:{team_id}:{month_day}"


async def team_games_on_day(
    league_row: LeagueORM, team_row: TeamORM, today: date, tz: ZoneInfo
) -> list[GameOut]:
    """The team's past games on ``today``'s month-day, newest first.

    Callers gate on provider/sport archive support before calling.  Never
    raises: any failure degrades to an empty list (which is also the
    normal "no anniversary today" state).
    """
    month_day = f"{today.month:02d}-{today.day:02d}"
    key = _cache_key(team_row.id, month_day)
    cached = await cache.cache_get_json(key)
    if isinstance(cached, list):
        try:
            return [GameOut.model_validate(entry) for entry in cached]
        except Exception:
            logger.warning(
                "on_this_day: ignoring unparseable cache for %s/%s", team_row.id, month_day
            )

    matches: list[GameOut] = []
    try:
        # Anchor the scan on the LOCAL year: ``today`` is a local calendar
        # day, and around New Year utcnow().year is already one ahead in
        # timezones behind UTC — which would shift the whole window and
        # silently drop the oldest anniversary.
        now_year = today.year
        # Sequential like head_to_head — season_results is redis-cached and
        # never raises, and ESPN is never hit concurrently.  Season keys are
        # ENDING years, so a cross-year season's Aug–Dec games surface under
        # key year+1; scanning keys and filtering by month-day handles that
        # without any date→season mapping.
        for season in range(now_year, now_year - SEASONS_BACK, -1):
            for game in await season_results.season_results_out(league_row, team_row, season):
                local_day = to_local(game.start_time, tz).date()
                if (local_day.month, local_day.day) != (today.month, today.day):
                    continue
                if local_day == today:
                    continue  # actual today is the live Today view's job
                matches.append(game)
    except Exception:
        logger.exception("on_this_day: season scan failed for %s", team_row.id)
        return []

    matches.sort(key=lambda game: game.start_time, reverse=True)
    await cache.cache_set_json(
        key, [entry.model_dump(mode="json") for entry in matches], _TTL_SECONDS
    )
    return matches
