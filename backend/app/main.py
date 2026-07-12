"""SportsDash FastAPI application entrypoint.

Startup: create tables, seed leagues/teams from YAML, start the
APScheduler, and kick one immediate full refresh in the background so a
fresh install has data within seconds without blocking startup.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Coroutine

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import dispose_engine, init_db
from app.providers import registry
from app.routes import router as api_router
from app.scheduler.jobs import (
    daily_refresh,
    refresh_locations,
    refresh_team_info,
    setup_scheduler,
)
from app.seed import seed_from_config
from app.services import cache, tsdb_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Strong references so startup tasks aren't garbage-collected mid-flight.
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn_logged(coro: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
    """create_task with exception logging (background jobs must never die silently)."""

    async def _runner() -> None:
        try:
            await coro
        except Exception:
            logger.exception("Background task %r failed", name)

    task = asyncio.create_task(_runner(), name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await seed_from_config()
    scheduler = setup_scheduler()
    scheduler.start()
    # Initial refresh in the background — don't block startup on providers.
    # daily_refresh already includes the news pass; spawning refresh_news
    # separately would race it with duplicate inserts.
    _spawn_logged(daily_refresh(), "startup-daily-refresh")
    # Resolve home-venue coordinates for the map view straight away so it
    # populates from cached/provider-known locations without waiting for
    # the daily cron.  daily_refresh runs it again after schedules (for the
    # stored-games venue fallback); refresh_locations skips already-resolved
    # teams, so the two runs don't duplicate geocoding work.
    _spawn_logged(refresh_locations(), "startup-refresh-locations")
    # Club "About" facts (history + founded year) for already-followed teams'
    # pages — light (one cached lookup per team) and skips enriched teams, so
    # it fills in fast without waiting for the heavier daily_refresh pass.
    _spawn_logged(refresh_team_info(), "startup-refresh-team-info")
    logger.info("SportsDash started (timezone=%s)", get_settings().timezone)
    try:
        yield
    finally:
        # Stop background work BEFORE tearing down its dependencies —
        # an in-flight startup refresh would otherwise recreate a fresh
        # engine after dispose_engine().
        for task in list(_background_tasks):
            task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)
        scheduler.shutdown(wait=False)
        await registry.close_all()
        await cache.close_cache()
        await tsdb_client.close_client()
        await dispose_engine()
        logger.info("SportsDash shut down")


app = FastAPI(title="SportsDash", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
