"""League standings (last fetched snapshot).

Standings are refreshed by the scheduler (daily + when a game finals) and
read here straight from the DB, so they already survive a provider outage —
the stored snapshot just stops advancing.  To make that visible, the
response carries ``is_stale`` (set once the snapshot is older than the
configured window), and a best-effort Redis "last-good" copy backs the
empty case so a wiped/never-fetched table can still serve the previous
snapshot rather than a blank board.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.schemas import StandingRowOut, StandingsOut
from app.models import convert
from app.models.domain import Sport
from app.providers import espn_history
from app.services import cache, repository
from app.timeutil import ensure_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

# Long-lived so the last-good snapshot survives an extended outage.
_LAST_GOOD_TTL_SECONDS = 7 * 24 * 3600


def _last_good_key(league_id: str) -> str:
    return f"standings:lastgood:{league_id}"


def _is_stale(fetched_at) -> bool:
    """Whether a snapshot's age exceeds the configured staleness window."""
    if fetched_at is None:
        return True
    window = timedelta(minutes=get_settings().data_stale_after_minutes)
    return utcnow() - fetched_at > window


@router.get("/standings/{league_id}", response_model=StandingsOut)
async def standings(
    league_id: str,
    season: int | None = Query(default=None, ge=1990, le=2100),
    session: AsyncSession = Depends(get_session),
) -> StandingsOut:
    league = await repository.get_league(session, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")

    if season is not None:
        return await _season_standings(session, league, season)

    row = await repository.get_standings(session, league_id)
    if row is not None and row.rows:
        fetched_at = ensure_utc(row.fetched_at) if row.fetched_at is not None else None
        out = StandingsOut(
            league_id=league.id,
            league_name=league.name,
            sport=league.sport,
            season=row.season,
            fetched_at=fetched_at,
            rows=[StandingRowOut.model_validate(entry) for entry in row.rows],
            is_stale=_is_stale(fetched_at),
        )
        # Stash the last-good snapshot for the empty-fallback path below.
        await cache.cache_set_json(
            _last_good_key(league_id),
            out.model_dump(mode="json"),
            _LAST_GOOD_TTL_SECONDS,
        )
        return out

    # No usable DB snapshot (never fetched, or wiped) — serve the last-good
    # cached copy if we have one, flagged stale, rather than a blank board.
    cached = await cache.cache_get_json(_last_good_key(league_id))
    if isinstance(cached, dict):
        cached["is_stale"] = True
        try:
            return StandingsOut.model_validate(cached)
        except Exception:
            logger.warning("standings: ignoring unparseable last-good cache for %s", league_id)

    # Nothing anywhere: an empty (not "stale") table, as before.
    return StandingsOut(
        league_id=league.id,
        league_name=league.name,
        sport=league.sport,
        season="",
        fetched_at=None,
        rows=[],
    )


async def _season_standings(session: AsyncSession, league, season: int) -> StandingsOut:
    """A past season's table: archive first, then a live ESPN backfill.

    Fetched seasons are stored in the archive, so each one costs a single
    upstream call ever.  Leagues whose provider has no season archives
    (TheSportsDB volleyball, tennis rankings, MMA) 404 — the frontend
    only offers the picker where history is supported.
    """
    key = str(season)
    row = await repository.get_standings_archive(session, league.id, key)
    if row is None:
        sport = Sport(league.sport)
        if league.provider != "espn" or not espn_history.supports_history(sport):
            raise HTTPException(status_code=404, detail="No season archive for this league")
        fetched = await espn_history.fetch_season_standings(convert.league_from_row(league), season)
        if fetched is None:
            raise HTTPException(status_code=404, detail="Season not available from the provider")
        row = await repository.save_standings_archive(session, fetched, season_key=key)
        if row is None:  # pragma: no cover — key is explicit, save can't miss
            raise HTTPException(status_code=404, detail="Season not available from the provider")
        await session.commit()

    fetched_at = ensure_utc(row.fetched_at) if row.fetched_at is not None else None
    return StandingsOut(
        league_id=league.id,
        league_name=league.name,
        sport=league.sport,
        season=row.season_label,
        fetched_at=fetched_at,
        rows=[StandingRowOut.model_validate(entry) for entry in row.rows],
        is_stale=False,
    )
