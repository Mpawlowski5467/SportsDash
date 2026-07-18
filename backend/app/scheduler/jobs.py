"""Scheduled background jobs — the package facade.

The implementation lives in sibling modules (split from the original
single-file jobs.py): ``common`` (constants/loaders/golf helpers),
``refresh`` (daily refresh jobs), ``stadium_cache`` (map-view venue
cache), and ``live`` (live/event polling + notifications). This module
keeps the original import surface: the route-facing ``kick_*``
functions, ``setup_scheduler``, and re-exports of every job. Original
design notes:

Every job is defensive end-to-end: provider failures are caught and
logged per league / per team so one bad source never kills a whole job,
and the job coroutines themselves never raise (APScheduler would only
log it anyway, but we want our own context-rich messages).

Writes go through ``app.services.repository`` inside
``db.session_scope()`` (which commits on success).  Refresh jobs use one
short write scope per source so a failing source can't poison the
transaction of a healthy one.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app import background

# Kept importable on the facade: tests patch service behavior via
# ``jobs.stadiums`` / ``jobs.geocode`` / ``jobs.notify`` / ``jobs.player_photos``
# (module objects are shared, so a patch here is a patch everywhere).
from app.services import geocode, notify, player_photos, stadiums  # noqa: F401

from app.scheduler.common import (  # noqa: F401 — re-exported package surface
    EVENTS_POLL_SECONDS,
    _load_leagues_and_teams,
    _load_team_competitions,
    _provider_for,
)
from app.scheduler.live import (  # noqa: F401
    events_tick,
    kick_standings_refresh,
    live_tick,
)
from app.scheduler.refresh import (  # noqa: F401
    _attach_player_photos,
    daily_refresh,
    refresh_locations,
    refresh_news,
    refresh_rosters,
    refresh_schedules,
    refresh_standings,
    refresh_standings_for_league,
    refresh_team_info,
)
from app.scheduler.stadium_cache import (  # noqa: F401
    active_follow_all_leagues,
    all_follow_all_leagues,
    refresh_competition_stadiums,
    refresh_game_venue_coords,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduler wiring
# ---------------------------------------------------------------------------


def setup_scheduler() -> AsyncIOScheduler:
    """Build the scheduler with all jobs registered.  Caller starts it."""
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
    scheduler.add_job(
        daily_refresh,
        CronTrigger(hour=settings.daily_refresh_hour, minute=0, timezone=settings.tzinfo),
        id="daily_refresh",
        name="Daily schedules/standings/rosters/news refresh",
        # APScheduler's default grace is 1 second: a host that is busy or
        # asleep at the trigger instant would silently skip the whole
        # day's refresh.  Give it an hour.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        refresh_news,
        IntervalTrigger(minutes=settings.news_refresh_minutes),
        id="refresh_news",
        name="RSS news refresh",
        # refresh_news also runs inside daily_refresh; cap instances so two
        # hourly runs can never stack up and race the insert path.
        max_instances=1,
        misfire_grace_time=300,
        coalesce=True,
    )
    scheduler.add_job(
        live_tick,
        IntervalTrigger(seconds=settings.live_poll_seconds),
        id="live_tick",
        name="Live score poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        events_tick,
        IntervalTrigger(seconds=EVENTS_POLL_SECONDS),
        id="events_tick",
        name="Leaderboard (golf) poll",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def kick_daily_refresh() -> None:
    """Spawn ``daily_refresh()`` as a fire-and-forget background task.

    Called by the setup routes right after the followed set changes so
    the new teams' data starts loading immediately — a route can't await
    a multi-source refresh inline (and must not import ``app.main`` for
    its task spawner: circular).
    """
    background.spawn(daily_refresh(), "kicked-daily-refresh")


def kick_refresh_locations() -> None:
    """Spawn ``refresh_locations()`` as a fire-and-forget background task.

    Kicked once on startup so the map populates from cached/provider-known
    coordinates without waiting for the next daily cron.  ``refresh_locations``
    never raises, but the done-callback still logs any unexpected escape and
    holds a strong reference so the task can't be GC'd mid-flight.
    """
    background.spawn(refresh_locations(), "kicked-refresh-locations")


def kick_team_info() -> None:
    """Spawn ``refresh_team_info()`` as a fire-and-forget background task.

    Kicked once on startup so already-followed teams' "About" sections fill
    in without waiting for the next daily cron (a team enriched once is then
    skipped).  ``refresh_team_info`` never raises; the done-callback logs any
    unexpected escape and holds a strong reference against GC.
    """
    background.spawn(refresh_team_info(), "kicked-refresh-team-info")


# A single in-flight on-demand location refresh, coalesced across concurrent
def kick_locations_if_pending() -> None:
    """On-demand hook for ``GET /api/map``: resolve missing coordinates soon.

    Called by the map route when followed teams still lack coordinates so a
    just-followed team isn't silently missing while the daily cron is hours
    away.  Fire-and-forget (the route must not block on a multi-source
    resolve): the newly-resolved coordinates land on a subsequent poll.
    Coalesced — if a refresh is already in flight, this is a no-op, so map
    polling can't pile up overlapping refreshes.  Never raises.
    """
    background.spawn_coalesced("ondemand-refresh-locations", refresh_locations())


def kick_competition_stadiums() -> None:
    """On-demand hook for ``GET /api/map``: pre-resolve active-competition stadiums.

    Called by the map route when an active ``follow_all`` competition has
    teams whose stadiums are not yet cached, so its whole-field pins start
    filling in without the request geocoding the field inline.
    Fire-and-forget and coalesced — a refresh already in flight makes this a
    no-op, so map polling can't pile up overlapping resolves.  Never raises.
    """
    background.spawn_coalesced(
        "ondemand-refresh-competition-stadiums", refresh_competition_stadiums()
    )


def kick_game_venue_coords() -> None:
    """On-demand hook for ``GET /api/map``: geocode upcoming-game venues soon.

    Called by the map route when an upcoming game's venue isn't yet
    resolvable (not a host venue, not in the located-stadium/team index, and
    not yet cached), so its pin fills in on a later poll without the request
    geocoding inline.  Fire-and-forget and coalesced — a refresh already in
    flight makes this a no-op.  Never raises.
    """
    background.spawn_coalesced("ondemand-refresh-game-venue-coords", refresh_game_venue_coords())
