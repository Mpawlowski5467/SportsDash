"""Whole-competition stadium cache for the map view: active follow_all fields' stadiums plus upcoming-game venue geocoding.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta


from app.db import session_scope
from app.models import domain
from app.providers import espn_catalog
from app.services import (
    geocode,
    repository,
    stadiums,
    venue_coords,
    wc_venues,
)
from app.timeutil import ensure_utc, utcnow

from app.scheduler.common import (
    _COMPETITION_ACTIVE_AFTER,
    _COMPETITION_ACTIVE_BEFORE,
    _COMPETITION_RESOLVE_DELAY_SECONDS,
    _GAME_VENUE_LOOKAHEAD_DAYS,
    _STADIUM_MISS_RETRY,
    _competition_stadiums_lock,
    _league_from_row,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------


def _competition_active_window(now=None) -> tuple[datetime, datetime]:
    """The near window a ``follow_all`` league must have a game in to be active."""
    now = now or utcnow()
    return now - _COMPETITION_ACTIVE_BEFORE, now + _COMPETITION_ACTIVE_AFTER


async def active_follow_all_leagues() -> list[domain.League]:
    """Snapshot the ``follow_all`` leagues that are currently ACTIVE.

    A whole-competition follow is active while it has at least one stored
    game in the near window (so the World Cup plots its nations during the
    tournament and drops them once it ends).  Returned as detached domain
    objects so callers can resolve providers/catalog teams without holding
    the session open.  Never raises — an invalid stored row is skipped.
    """
    start, end = _competition_active_window()
    active: list[domain.League] = []
    async with session_scope() as session:
        rows = await repository.list_follow_all_leagues(session)
        for row in rows:
            try:
                if await repository.league_has_games_in_window(session, row.id, start, end):
                    active.append(_league_from_row(row))
            except Exception:
                logger.exception("active_follow_all_leagues: bad league row %r — skipping", row.id)
    return active


async def all_follow_all_leagues() -> list[domain.League]:
    """Snapshot EVERY ``follow_all`` league, in or out of season.

    The stadium pre-resolve uses this rather than
    :func:`active_follow_all_leagues` so an off-season competition's home
    grounds are resolved and cached too — the map plots a ``follow_all``
    league's whole field whether or not it has a near-window fixture, so the
    cache must be warmed regardless of activity.  Returned as detached domain
    objects; never raises — an invalid stored row is skipped.
    """
    leagues: list[domain.League] = []
    async with session_scope() as session:
        rows = await repository.list_follow_all_leagues(session)
    for row in rows:
        try:
            leagues.append(_league_from_row(row))
        except Exception:
            logger.exception("all_follow_all_leagues: bad league row %r — skipping", row.id)
    return leagues


def _stadium_key(league: domain.League, provider_key: str) -> str:
    """The stadium-cache key for a competition team: ``"{provider}:{provider_key}"``."""
    return f"{league.provider}:{provider_key}"


async def _resolve_competition_stadium(
    league: domain.League, team: espn_catalog.CatalogTeam
) -> domain.TeamLocation | None:
    """Resolve one competition team's home stadium by name (+ facts + coords).

    Competition teams have no ``TeamORM`` row and no stored fixtures to
    borrow a venue from, so this is the name-only slice of
    :func:`_resolve_team_location`: TheSportsDB enrichment by team name
    (``stadiums.lookup_stadium`` — venue + capacity + photo and often
    coordinates), then a geocode of the venue name when no coordinates came
    back.  Returns ``None`` when nothing usable resolves; the caller still
    caches that as a definitive miss so it isn't re-attempted hot.  Never
    raises — every step is guarded.
    """
    enrichment: domain.TeamLocation | None = None
    try:
        enrichment = await stadiums.lookup_stadium(team.name, sport=league.sport.value)
    except Exception:
        logger.exception(
            "refresh_competition_stadiums: enrichment failed for %s — continuing",
            team.name,
        )

    if enrichment is not None and enrichment.lat is not None and enrichment.lon is not None:
        return enrichment

    venue = enrichment.venue if enrichment is not None else None
    if not venue:
        return None

    try:
        coords = await geocode.geocode_venue(venue)
    except Exception:
        logger.exception("refresh_competition_stadiums: geocode failed for %r — skipping", venue)
        return None
    if coords is None:
        return None
    lat, lon = coords
    if enrichment is None:
        return domain.TeamLocation(venue=venue, lat=lat, lon=lon)
    return replace(enrichment, venue=venue, lat=lat, lon=lon)


async def refresh_competition_stadiums() -> None:
    """Pre-resolve every ``follow_all`` competition's teams into the stadium cache.

    For each whole-competition follow — in or out of season — enumerate the
    catalog teams (``espn_catalog.get_league_teams``) and resolve any not
    already cached (by ``"{provider}:{provider_key}"``) through the Phase 11
    enrichment/geocode pipeline, caching the result — coordinates *or* a
    definitive miss — in ``StadiumORM``.  This keeps ``GET /api/map`` fast:
    the request reads resolved stadiums straight from the cache instead of
    geocoding a 48-nation field inline.  Off-season leagues are resolved too
    so the map can plot their whole field at home grounds the moment it's
    asked — not only while a tournament is running.

    Rate-limited (a short pause between resolves, since each may hit
    TheSportsDB + Nominatim) and never-raises: per-league and per-team
    failures are isolated so one bad source can't abort the rest, and the
    job itself swallows anything unexpected (the daily cron + the startup
    kick both rely on that).

    Single-flight: if a sweep is already running (another caller — startup,
    cron, or the map kick), this returns immediately rather than racing it on
    the shared stadium cache.
    """
    if _competition_stadiums_lock.locked():
        logger.debug("refresh_competition_stadiums: a sweep is already running — skipping")
        return
    async with _competition_stadiums_lock:
        await _refresh_competition_stadiums()


async def _refresh_competition_stadiums() -> None:
    """Body of :func:`refresh_competition_stadiums`, run under its lock."""
    try:
        leagues = await all_follow_all_leagues()
        if not leagues:
            return

        for league in leagues:
            try:
                catalog_league = espn_catalog.get_catalog_league(league.id)
                if catalog_league is None:
                    # A follow_all league not in the catalog (e.g. a test or
                    # legacy id) — nothing to enumerate; skip quietly.
                    continue
                teams = await espn_catalog.get_league_teams(catalog_league)
            except Exception:
                logger.exception(
                    "refresh_competition_stadiums: could not list teams for %r — skipping",
                    league.id,
                )
                continue

            resolved = 0
            for team in teams:
                key = _stadium_key(league, team.provider_key)
                try:
                    async with session_scope() as session:
                        cached = await repository.get_stadium(session, key)
                    if cached is not None and cached.lat is not None:
                        # Already located — a resolved coordinate is permanent.
                        continue
                    if (
                        cached is not None
                        and cached.resolved
                        and cached.fetched_at is not None
                        and utcnow() - ensure_utc(cached.fetched_at) < _STADIUM_MISS_RETRY
                    ):
                        # Missed recently (often a transient TheSportsDB 429,
                        # not a real "no stadium") — retry after the cooldown
                        # rather than treating it as a permanent miss.
                        continue

                    location = await _resolve_competition_stadium(league, team)
                    async with session_scope() as session:
                        await repository.upsert_stadium(
                            session,
                            key,
                            team.name,
                            venue=location.venue if location else None,
                            lat=location.lat if location else None,
                            lon=location.lon if location else None,
                            capacity=location.capacity if location else None,
                            opened=location.opened if location else None,
                            image_url=location.image_url if location else None,
                            location=location.location if location else None,
                            surface=location.surface if location else None,
                            resolved=True,
                        )
                    if location is not None and location.lat is not None:
                        resolved += 1
                except Exception:
                    logger.exception(
                        "refresh_competition_stadiums: failed resolving %s (%s) — skipping",
                        team.name,
                        key,
                    )
                # Pace external calls regardless of outcome.
                await asyncio.sleep(_COMPETITION_RESOLVE_DELAY_SECONDS)

            logger.info(
                "refresh_competition_stadiums: %s — %d/%d team stadium(s) located",
                league.id,
                resolved,
                len(teams),
            )
    except Exception:
        logger.exception("refresh_competition_stadiums failed")


async def refresh_game_venue_coords() -> None:
    """Geocode + cache upcoming-game venue names the map can't resolve in-memory.

    The map's "upcoming games" mode resolves most venues for free — the World
    Cup host table, a followed team's own coordinates, or the name index built
    from located teams/stadiums.  A followed team's *away* game can still sit
    at a ground we've never located; this pre-geocodes those venue names
    (paced by the geocoder's own ≤1 req/s limit) and caches the coordinates —
    or a miss — in Redis (:mod:`app.services.venue_coords`) so the request
    never geocodes inline.

    No-op without Redis (every cache write is a no-op, so there's nothing to
    warm).  Never raises: the daily cron and the on-demand kick rely on it.
    """
    try:
        now = utcnow()
        end = now + timedelta(days=_GAME_VENUE_LOOKAHEAD_DAYS)
        async with session_scope() as session:
            league_ids = await repository.map_relevant_league_ids(session)
            if not league_ids:
                return
            games = await repository.upcoming_games_for_leagues(session, league_ids, now, end)
            index = venue_coords.build_index(
                await repository.list_teams_with_location(session),
                await repository.list_located_stadiums(session),
            )

        # Distinct venue names not already resolvable in-memory or cached.
        pending: list[str] = []
        seen: set[str] = set()
        for game in games:
            venue = game.venue
            norm = venue_coords.normalize(venue)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            if wc_venues.resolve(venue) is not None or norm in index:
                continue
            if await venue_coords.has_entry(venue):
                continue
            pending.append(venue)

        for venue in pending:
            coords = await geocode.geocode_venue(venue)
            await venue_coords.set_coords(venue, coords)
        if pending:
            logger.info("refresh_game_venue_coords: geocoded %d game venue(s)", len(pending))
    except Exception:
        logger.exception("refresh_game_venue_coords failed")
