"""Whole-competition stadium cache for the map view: active follow_all fields' stadiums plus upcoming-game venue geocoding.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta


from app.config import get_settings
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
    _COMPETITION_THROTTLED_DELAY_SECONDS,
    _GAME_VENUE_LOOKAHEAD_DAYS,
    _STADIUM_MISS_RETRY,
    _TSDB_MAX_FRUITLESS_PAUSES,
    _TSDB_RATE_LIMIT_PAUSE_SECONDS,
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


async def _resolve_competition_stadium(
    league: domain.League, team: espn_catalog.CatalogTeam
) -> domain.TeamLocation | None:
    """Resolve one competition team's home stadium by name (+ facts + coords).

    This is the name-keyed twin of :func:`_resolve_team_location`:
    TheSportsDB enrichment by team name (``stadiums.lookup_stadium`` —
    venue + capacity + photo and often coordinates), then the side's own
    stored home fixtures when that yields no venue, then a geocode of
    whichever venue name came back.  Returns ``None`` when nothing usable
    resolves; the caller caches that as a miss, retried only after a
    cool-down, so it isn't re-attempted hot.  Never raises — every step is
    guarded.

    The enrichment yields nothing in two different cases, and this function
    treats them alike: TheSportsDB has no such team, or TheSportsDB could not
    be asked (a 429, a timeout).  Either way the stored fixtures are tried
    next.  The sweep therefore asks first (:func:`_tsdb_answered`) and waits
    once before calling this for a team TheSportsDB would not answer for, so
    that the fallback — whose pin is permanent and carries no capacity or
    photo — is not taken at the first refusal of a rate-limited provider.

    A competition team has no ``TeamORM`` row, so the id-keyed
    ``most_common_home_venue`` used by followed teams cannot see it — hence
    the by-name query, which is the whole reason these sides used to drop
    off the map instead of falling back like everyone else.

    A team TheSportsDB knows only by region ("Kyiv, Ukraine") resolves to no
    venue name at all — the enrichment refuses to pass a coarse area off as a
    venue (see :func:`app.services.stadiums._compose_venue`) — so it lands
    here as a miss and stays off the map rather than pinning at the region's
    centroid.
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
        # The provider does not know this side's ground, but its own stored
        # home fixtures usually name it — the same fallback a followed team
        # gets, reached by name because there is no team id to key on.
        try:
            async with session_scope() as session:
                venue = await repository.most_common_home_venue_by_name(
                    session, league.id, team.name
                )
        except Exception:
            logger.exception(
                "refresh_competition_stadiums: stored-venue lookup failed for %s — continuing",
                team.name,
            )
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


async def _tsdb_answered(league: domain.League, team: espn_catalog.CatalogTeam) -> bool:
    """Ask TheSportsDB about one team's ground and say whether it ANSWERED.

    An answer is a hit or a definitive miss.  ``stadiums`` keeps either for
    the life of the process, so the :func:`_resolve_competition_stadium` that
    follows reads it back without a second request.  ``False`` is the other
    kind of nothing — a rate-limited 429, a timeout.  Nothing was learned
    about the team and nothing was kept, so the resolve that follows sends
    the request again.  Never raises.
    """
    sport = league.sport.value
    try:
        await stadiums.lookup_stadium(team.name, sport=sport)
    except Exception:
        logger.exception(
            "refresh_competition_stadiums: enrichment failed for %s — continuing",
            team.name,
        )
    return stadiums.lookup_settled(team.name, sport=sport)


async def refresh_competition_stadiums(should_stop: Callable[[], bool] | None = None) -> None:
    """Pre-resolve every ``follow_all`` competition's teams into the stadium cache.

    For each whole-competition follow — in or out of season — enumerate the
    catalog teams (``espn_catalog.get_league_teams``) and resolve any not
    already cached (under ``repository.stadium_key``) through the Phase 11
    enrichment/geocode pipeline, caching the result — coordinates *or* a
    miss — in ``StadiumORM``.  This keeps ``GET /api/map`` fast:
    the request reads resolved stadiums straight from the cache instead of
    geocoding a 48-nation field inline.  Off-season leagues are resolved too
    so the map can plot their whole field at home grounds the moment it's
    asked — not only while a tournament is running.

    A row an older build cached under the league-blind key is adopted rather
    than looked up again — copied to the team's own key when its name proves
    it is that team's (``repository.find_team_stadium``) — so an upgrade
    spends lookups only on the teams whose old row was really another
    team's.  Each league's summary line says how many were adopted and how
    many went through a fresh lookup.

    A miss row is what stops the map asking: it kicks this sweep only for
    a team with NO row, and a miss is retried only once it is older than
    ``_STADIUM_MISS_RETRY``.  So a miss must not be written lightly.  A team
    TheSportsDB has no record of is a miss at once.  A team TheSportsDB
    would not answer for (a 429, a timeout) is not: the sweep waits
    ``_TSDB_RATE_LIMIT_PAUSE_SECONDS`` — holding no database session — and
    resolves it once more, and whatever that second attempt gets is
    recorded as before, a miss included.  A team is never waited on twice,
    and once ``_TSDB_MAX_FRUITLESS_PAUSES`` pauses have changed nothing the
    pass stops waiting altogether.

    Rate-limited (a short pause between resolves, since each may hit
    TheSportsDB + Nominatim; a longer one for the rest of the pass once
    TheSportsDB has refused a lookup) and never-raises: per-league and
    per-team failures are isolated so one bad source can't abort the rest,
    and the job itself swallows anything unexpected (the daily cron + the
    startup kick both rely on that).

    Single-flight: if a sweep is already running (another caller — startup,
    cron, or the map kick), this returns immediately rather than racing it on
    the shared stadium cache.

    ``should_stop`` lets a caller with something more urgent to do cut the
    sweep short.  ``daily_refresh`` passes its supersede check: a long sweep
    is minutes of pacing sleeps (tens of minutes once throttled), and the
    pass a save in the setup wizard asks for cannot start until this stage
    returns.  It is asked before each league, before each team and after a
    rate-limit pause, so the sweep leaves within one team of being told to.
    The rows already written stay, and the next sweep takes up from the
    first team without one.  The map kick passes nothing and always runs to
    the end.
    """
    if _competition_stadiums_lock.locked():
        logger.debug("refresh_competition_stadiums: a sweep is already running — skipping")
        return
    async with _competition_stadiums_lock:
        await _refresh_competition_stadiums(should_stop)


async def _refresh_competition_stadiums(should_stop: Callable[[], bool] | None = None) -> None:
    """Body of :func:`refresh_competition_stadiums`, run under its lock."""

    def stopping() -> bool:
        if should_stop is None or not should_stop():
            return False
        # Logged once: every caller returns on True.  The wording is
        # daily_refresh's reason, the only caller that hands a check in.
        logger.info(
            "refresh_competition_stadiums: the followed set changed — leaving the "
            "rest of the sweep to the next pass"
        )
        return True

    try:
        leagues = await all_follow_all_leagues()
        if not leagues:
            return

        # Both last for the whole pass, across leagues: what TheSportsDB limits
        # is this process's requests, whichever competition they are for.
        throttled = False  # it has refused a lookup: resolve at the slow pace
        fruitless_pauses = 0  # pauses after which it still did not answer

        for league in leagues:
            if stopping():
                return
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
            adopted = 0
            looked_up = 0
            for team in teams:
                if stopping():
                    return
                key = repository.stadium_key(league, team.provider_key)
                try:
                    async with session_scope() as session:
                        # Read first only so the league's summary line can
                        # tell a legacy row adopted on this pass from one
                        # already cached under the team's own key.
                        was_cached = await repository.get_stadium(session, key) is not None
                        cached = await repository.find_team_stadium(
                            session, league, team.provider_key, team.name, adopt=True
                        )
                    if cached is not None and not was_cached:
                        adopted += 1
                    # A cached row whose "venue" is only its location text was
                    # pinned at the CENTRE of a region rather than at a ground
                    # (a marker tens of km out, drawn as confidently as a
                    # correct one).  It has to be re-resolved despite both
                    # skips below — a real venue then replaces it, or a miss
                    # drops it off the map, which is the honest answer.
                    area_only = cached is not None and stadiums.is_area_only_venue(
                        cached.venue, cached.location
                    )
                    if area_only:
                        logger.info(
                            "refresh_competition_stadiums: %s (%s) was pinned at the "
                            "centroid of %r — re-resolving",
                            team.name,
                            key,
                            cached.location,
                        )
                    if cached is not None and cached.lat is not None and not area_only:
                        # Already located — a resolved coordinate is permanent.
                        continue
                    if (
                        cached is not None
                        and cached.resolved
                        and cached.fetched_at is not None
                        and not area_only
                        and utcnow() - ensure_utc(cached.fetched_at) < _STADIUM_MISS_RETRY
                    ):
                        # Missed recently.  Not necessarily a real "no
                        # stadium" — TheSportsDB refusing twice is recorded
                        # the same way — so it is retried rather than kept
                        # for good, but only after the cool-down.
                        continue

                    looked_up += 1
                    paused = False
                    if fruitless_pauses < _TSDB_MAX_FRUITLESS_PAUSES and not await _tsdb_answered(
                        league, team
                    ):
                        # No answer, which is most often the rate limit a
                        # long sweep talks itself into.  Resolving on would
                        # record a miss — or pin the team for good from a
                        # geocoded fixture venue, without capacity or photo —
                        # for a team nothing was learned about.  So slow down
                        # for the rest of the pass, wait the limit out, and
                        # let the resolve below ask once more.  Whatever THAT
                        # attempt gets is recorded as it always was: a team is
                        # never waited on twice, and the map still gets the
                        # row that stops it kicking a sweep on every poll.
                        throttled = paused = True
                        logger.info(
                            "refresh_competition_stadiums: TheSportsDB is rate limiting "
                            "(no answer for %s) — pausing %.0fs before asking once more, "
                            "and resolving every %.0fs for the rest of this pass",
                            team.name,
                            _TSDB_RATE_LIMIT_PAUSE_SECONDS,
                            _COMPETITION_THROTTLED_DELAY_SECONDS,
                        )
                        # No session is open here: the read scope above has
                        # closed and the write scope below has not begun.
                        await asyncio.sleep(_TSDB_RATE_LIMIT_PAUSE_SECONDS)
                        if stopping():
                            # The pause is the longest wait in the sweep, so
                            # it is where a save most often lands.  Nothing
                            # has been written for this team: the next sweep
                            # asks about it afresh.
                            return
                    location = await _resolve_competition_stadium(league, team)
                    if paused and not stadiums.lookup_settled(team.name, sport=league.sport.value):
                        fruitless_pauses += 1
                        if fruitless_pauses == _TSDB_MAX_FRUITLESS_PAUSES:
                            logger.info(
                                "refresh_competition_stadiums: TheSportsDB still gave no "
                                "answer after %d pauses — not waiting on it again this pass",
                                fruitless_pauses,
                            )
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
                await asyncio.sleep(
                    _COMPETITION_THROTTLED_DELAY_SECONDS
                    if throttled
                    else _COMPETITION_RESOLVE_DELAY_SECONDS
                )

            logger.info(
                "refresh_competition_stadiums: %s — %d/%d team stadium(s) located "
                "(%d adopted from legacy rows, %d resolved fresh)",
                league.id,
                resolved,
                len(teams),
                adopted,
                looked_up,
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

    No-op without Redis (this cache is Redis-only, so every write to it is a
    no-op and there's nothing to warm).  Never raises: the daily cron and the
    on-demand kick rely on it.
    """
    if not get_settings().redis_url:
        # The venue-coords cache is Redis-backed, so without Redis every write
        # below is discarded — geocoding here would spend Nominatim budget
        # (≤1 req/s) warming a cache nothing can read back.
        return
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
