"""API route aggregation.

``app.main`` includes this router under the ``/api`` prefix; the modules
here define provider- and storage-agnostic endpoints.  Read-only routes
never commit; the setup routes are the lone writers (they commit
explicitly).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import (
    bracket,
    calendar,
    events,
    games,
    health,
    leaders,
    map_view,
    matchup,
    meta,
    nation,
    news,
    notifications,
    odds,
    results,
    roster,
    schedule,
    scorers,
    setup,
    standings,
    teams,
    today,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(meta.router)
router.include_router(teams.router)
router.include_router(today.router)
router.include_router(schedule.router)
router.include_router(standings.router)
router.include_router(leaders.router)
router.include_router(scorers.router)
router.include_router(nation.router)
router.include_router(bracket.router)
router.include_router(roster.router)
router.include_router(results.router)
router.include_router(news.router)
router.include_router(calendar.router)
router.include_router(events.router)
router.include_router(games.router)
router.include_router(odds.router)
router.include_router(matchup.router)
router.include_router(map_view.router)
router.include_router(setup.router)
router.include_router(notifications.router)

__all__ = ["router"]
