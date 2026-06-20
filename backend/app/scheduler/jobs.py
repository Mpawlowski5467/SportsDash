"""Scheduled background jobs: schedule/standings/roster/news refresh + live polling.

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

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import session_scope
from app.models import domain
from app.models.domain import EventType, GameEvent, GamePhase, GameState, Sport
from app.models.orm import EventORM, GameORM, LeagueORM, TeamORM
from app.providers import espn_catalog, registry
from app.services import (
    geocode,
    news,
    notify,
    notify_prefs,
    player_photos,
    repository,
    stadiums,
    venue_coords,
    wc_venues,
    wiki,
)
from app.services.events import diff_states, starting_soon_event
from app.timeutil import ensure_utc, utcnow

logger = logging.getLogger(__name__)

# How often ``events_tick`` polls in-progress leaderboard events (golf).
# Leaderboards move far more slowly than a clocked game's scoreboard, so
# this is much coarser than ``live_poll_seconds``.
EVENTS_POLL_SECONDS = 180

# How far ahead ``events_tick`` reaches for events that have not started
# yet — leaderboard events span days, so this only governs how soon a
# Thursday tee-off is picked up, not how often the board refreshes.
_EVENTS_LOOKAHEAD = timedelta(minutes=30)

# A ``follow_all`` competition is ACTIVE — and therefore plotted on the map —
# while it has at least one stored game in this near window.  Once a
# tournament ends, daily-refresh stops returning near fixtures for it, the
# window empties, and its whole-field pins drop off the map automatically.
_COMPETITION_ACTIVE_BEFORE = timedelta(days=2)
_COMPETITION_ACTIVE_AFTER = timedelta(days=21)

# Politeness pause between competition-stadium resolutions: each unresolved
# team may hit TheSportsDB (searchteams) and Nominatim (geocode, ≤1 req/s),
# so the background pre-resolve paces itself to stay a good citizen and not
# trip a rate limit on a 48-nation field.
_COMPETITION_RESOLVE_DELAY_SECONDS = 1.0

# Single-flight guard for refresh_competition_stadiums.  It is reachable from
# THREE callers (startup daily_refresh, the daily cron, and the on-demand
# map kick), and the cache write is a get-then-insert; two overlapping runs
# both see "no row" for the same key and the second INSERT trips the stadium
# primary-key constraint (and doubles the external request rate, tripping
# TheSportsDB's free-tier limit).  Holding this lock makes any second caller a
# no-op so only one resolve sweep ever runs at a time.
_competition_stadiums_lock = asyncio.Lock()

# A competition stadium that resolves to a definitive MISS (no coordinates) is
# cached so the map stops asking — but the "miss" is often just a transient
# TheSportsDB 429 during a big resolve sweep, NOT a club that truly has no
# stadium.  So a miss is retried once it is older than this, instead of being
# stuck forever; a real miss simply costs one cheap retry every interval.
_STADIUM_MISS_RETRY = timedelta(minutes=15)

# How far ahead the game-venue geocoder warms its cache.  The map's
# upcoming-games slider tops out at 30 days, so pre-resolving that whole span
# means any picked window is already warm (a venue resolved once is cached for
# a week regardless of which day its game falls on).
_GAME_VENUE_LOOKAHEAD_DAYS = 30

# Per-player photo backfill (soccer only): how many photoless players per team
# to look up per roster refresh.  ESPN soccer rosters carry headshots for only
# a couple of players; the rest fall back to an initials chip.  Kept modest
# because TheSportsDB's free key is SHARED with the stadium/About enrichment —
# the backfill fails fast on a 429 and trickles (player_photos serializes +
# spaces), so a squad fills in GRADUALLY over several daily refreshes without
# exhausting the rate budget the other features need.  Hits cache for a month.
_PHOTO_BACKFILL_MAX_PLAYERS = 15


# ---------------------------------------------------------------------------
# ORM -> domain helpers (providers take domain objects)
# ---------------------------------------------------------------------------

def _league_from_row(row: LeagueORM) -> domain.League:
    return domain.League(
        id=row.id,
        sport=domain.Sport(row.sport),
        name=row.name,
        provider=row.provider,
        provider_key=row.provider_key,
    )


def _team_from_row(row: TeamORM) -> domain.Team:
    return domain.Team(
        id=row.id,
        league_id=row.league_id,
        name=row.name,
        abbreviation=row.abbreviation,
        provider_key=row.provider_key,
        logo_url=row.logo_url,
        color=row.color,
        rss_feeds=tuple(row.rss_feeds or ()),
    )


async def _load_leagues_and_teams() -> tuple[dict[str, domain.League], list[domain.Team]]:
    """Snapshot the followed leagues/teams as domain objects."""
    async with session_scope() as session:
        league_rows = await repository.list_leagues(session)
        team_rows = await repository.list_teams(session)

    leagues: dict[str, domain.League] = {}
    for row in league_rows:
        try:
            leagues[row.id] = _league_from_row(row)
        except Exception:
            logger.exception("Skipping league %r: invalid stored data", row.id)

    teams = [_team_from_row(row) for row in team_rows]
    return leagues, teams


async def _load_team_competitions() -> dict[str, list[tuple[str, str]]]:
    """Snapshot each team's sibling competitions as plain tuples.

    Returns ``{team_id: [(sibling_league_id, provider_key), ...]}`` so
    the schedule job can fan a national team's fetch across every
    competition it appears in.  Read once, detached from the session, so
    nothing lazy-loads after the scope closes.
    """
    async with session_scope() as session:
        rows = await repository.list_team_competitions(session)

    by_team: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        by_team[row.team_id].append((row.league_id, row.provider_key))
    return dict(by_team)


def _provider_for(league: domain.League):
    """Resolve the provider for a league; None (logged) when unregistered."""
    try:
        return registry.get_provider(league.provider)
    except KeyError:
        logger.error(
            "League %r references unknown provider %r — skipping", league.id, league.provider
        )
        return None


# ---------------------------------------------------------------------------
# Leaderboard (golf) helpers
# ---------------------------------------------------------------------------

def _is_golf(league: domain.League) -> bool:
    return league.sport is Sport.GOLF


def _golfer_id_map(teams: list[domain.Team], leagues: dict[str, domain.League]) -> dict[str, str]:
    """Map ESPN athlete id -> internal team id for every followed golfer.

    Each followed golfer is a single-member golf ``TeamORM`` whose
    ``provider_key`` is the ESPN athlete id, so a leaderboard row's
    ESPN id can be rewritten to the internal id when it belongs to a
    followed golfer.  Only golf-league teams contribute, so a non-golf
    team that happens to share a provider_key string can't mis-tag.
    """
    mapping: dict[str, str] = {}
    for team in teams:
        league = leagues.get(team.league_id)
        if league is not None and _is_golf(league):
            mapping[team.provider_key] = team.id
    return mapping


def _tag_followed_golfers(
    event: domain.Event, espn_to_internal: dict[str, str]
) -> domain.Event:
    """Rewrite each leaderboard row's ``player_id`` to the internal id.

    The provider carries the ESPN athlete id transiently in
    ``LeaderRow.player_id`` (it can't know who is followed).  Here we
    rewrite it to the followed golfer's internal team id, or ``None``
    when the athlete isn't followed, before persisting.
    """
    tagged_rows = tuple(
        replace(row, player_id=espn_to_internal.get(row.player_id))
        if row.player_id is not None
        else row
        for row in event.leaderboard
    )
    return replace(event, leaderboard=tagged_rows)


# ---------------------------------------------------------------------------
# Refresh jobs
# ---------------------------------------------------------------------------

async def _fetch_and_upsert_schedule(provider, league, team, start, end, *, label: str) -> None:
    """Fetch a team's schedule in one league and merge it; isolate failures.

    Shared by the team's primary league and each sibling competition a
    national team also plays in: an incoming ``None`` team-id side never
    erases a stored id (see ``upsert_games``), so the same fixture seen
    from multiple contexts accumulates rather than clobbering.
    """
    try:
        games = await provider.get_schedule(league, team, start, end)
        async with session_scope() as session:
            touched = await repository.upsert_games(session, games)
        logger.info(
            "refresh_schedules: %s — %d game(s) fetched, %d row(s) touched",
            label, len(games), touched,
        )
    except Exception:
        logger.exception("refresh_schedules: failed for %s — skipping", label)


async def refresh_schedules() -> None:
    """Pull schedules for today-7d .. today+45d.

    Three passes, each with per-source error isolation so one bad
    provider/league/team never aborts the job:
    1. every followed team's primary-league schedule;
    2. each team's sibling competitions (national teams play across
       several — e.g. a World Cup squad also in the Nations League), via
       ``get_schedule`` with the team's provider_key in that context;
    3. every whole-competition follow (``League.follow_all``), via
       ``get_competition_schedule`` (the whole fixture set, team ids null
       unless a side happens to be a followed team).
    """
    try:
        leagues, teams = await _load_leagues_and_teams()
        competitions = await _load_team_competitions()
        today: date = utcnow().date()
        start = today - timedelta(days=7)
        end = today + timedelta(days=45)

        # Passes 1 + 2: per-team primary + sibling competitions.
        for team in teams:
            league = leagues.get(team.league_id)
            if league is None:
                logger.error(
                    "Team %r references unknown league %r — skipping", team.id, team.league_id
                )
                continue
            provider = _provider_for(league)
            if provider is not None:
                await _fetch_and_upsert_schedule(
                    provider, league, team, start, end, label=team.id
                )

            for sib_league_id, provider_key in competitions.get(team.id, ()):
                sib_league = leagues.get(sib_league_id)
                if sib_league is None:
                    logger.error(
                        "Team %r references unknown competition %r — skipping",
                        team.id, sib_league_id,
                    )
                    continue
                sib_provider = _provider_for(sib_league)
                if sib_provider is None:
                    continue
                # The team's id in this competition's context (global for
                # ESPN nations) — keep its own slug/league for id-merging.
                sib_team = replace(team, league_id=sib_league_id, provider_key=provider_key)
                await _fetch_and_upsert_schedule(
                    sib_provider, sib_league, sib_team, start, end,
                    label=f"{team.id}@{sib_league_id}",
                )

        # Pass 3: whole-competition follows.
        async with session_scope() as session:
            follow_all_rows = await repository.list_follow_all_leagues(session)
        for row in follow_all_rows:
            try:
                league = _league_from_row(row)
            except Exception:
                logger.exception(
                    "refresh_schedules: invalid follow_all league row %r — skipping", row.id
                )
                continue
            provider = _provider_for(league)
            if provider is None:
                continue
            try:
                games = await provider.get_competition_schedule(league, start, end)
                async with session_scope() as session:
                    touched = await repository.upsert_games(session, games)
                logger.info(
                    "refresh_schedules: competition %s — %d game(s) fetched, %d row(s) touched",
                    league.id, len(games), touched,
                )
            except Exception:
                logger.exception(
                    "refresh_schedules: failed for competition %r — skipping", league.id
                )

        # Pass 4: leaderboard events (golf).  A followed golfer is a
        # single-member golf team and a golf follow_all league pulls the
        # whole field; either way we fetch the tour's Events and tag the
        # leaderboard rows that belong to a followed golfer.
        await _refresh_events(leagues, teams, start, end)
    except Exception:
        logger.exception("refresh_schedules failed")


async def _refresh_events(
    leagues: dict[str, domain.League],
    teams: list[domain.Team],
    start: date,
    end: date,
) -> None:
    """Fetch leaderboard events for followed golfers + golf follow_all leagues.

    Each source is fetched once per golf league (a league's Events are the
    same regardless of which followed golfer triggered the fetch), the
    leaderboard rows are re-tagged ESPN-id -> internal-id, and the events
    are upserted.  Per-league failures are isolated so one bad tour can't
    abort the rest.
    """
    espn_to_internal = _golfer_id_map(teams, leagues)

    # Golf leagues to pull: every league with at least one followed golfer,
    # plus every golf follow_all league.  Deduplicated by league id so a
    # league with several followed golfers is fetched only once.
    golf_league_ids = {
        team.league_id
        for team in teams
        if (lg := leagues.get(team.league_id)) is not None and _is_golf(lg)
    }
    async with session_scope() as session:
        follow_all_rows = await repository.list_follow_all_leagues(session)
    for row in follow_all_rows:
        league = leagues.get(row.id)
        if league is not None and _is_golf(league):
            golf_league_ids.add(league.id)

    for league_id in sorted(golf_league_ids):
        league = leagues.get(league_id)
        if league is None:
            continue
        provider = _provider_for(league)
        if provider is None:
            continue
        try:
            events = await provider.get_events(league, start, end)
            tagged = [_tag_followed_golfers(event, espn_to_internal) for event in events]
            async with session_scope() as session:
                touched = await repository.upsert_events(session, tagged)
            logger.info(
                "refresh_schedules: events %s — %d event(s) fetched, %d row(s) touched",
                league_id, len(events), touched,
            )
        except Exception:
            logger.exception(
                "refresh_schedules: failed fetching events for league %r — skipping",
                league_id,
            )


async def refresh_standings_for_league(league: domain.League) -> None:
    """Refresh ONE league's standings; fully isolated and never raises.

    Shared by the daily :func:`refresh_standings` sweep and the live
    trigger: ``live_tick``/``events_tick`` fire this when a followed
    league's game transitions to FINAL, so group/division positions move
    in near-real-time ("a country moves places and it updates").  Any
    provider/DB failure is caught and logged so a bad source can never
    abort a tick or the daily job.
    """
    provider = _provider_for(league)
    if provider is None:
        return
    try:
        standings = await provider.get_standings(league)
        async with session_scope() as session:
            await repository.save_standings(session, standings)
        logger.info(
            "refresh_standings: %s — %d row(s)", league.id, len(standings.rows)
        )
    except Exception:
        logger.exception("refresh_standings: failed for league %r — skipping", league.id)


async def refresh_standings() -> None:
    try:
        leagues, _ = await _load_leagues_and_teams()
        for league in leagues.values():
            await refresh_standings_for_league(league)
    except Exception:
        logger.exception("refresh_standings failed")


async def _attach_player_photos(
    roster: domain.Roster, team: domain.Team, sport: str
) -> domain.Roster:
    """Backfill missing soccer headshots from TheSportsDB (capped, never raises).

    ESPN soccer rosters carry a headshot for only a couple of players, so the
    rest fall back to an initials chip.  For up to
    :data:`_PHOTO_BACKFILL_MAX_PLAYERS` players still missing a ``photo_url``
    we resolve a cutout from TheSportsDB's purpose-built ``searchplayers``
    (passing the club so a shared name resolves to the right player).
    Concurrency + rate are bounded inside :func:`player_photos.lookup_photo`,
    which retries 429s and — crucially — does NOT cache a transient failure,
    so a squad fills in across a few daily refreshes instead of a brief 429
    storm denying everyone a face.  Best-effort: any miss leaves it ``None``.
    """
    targets = [p for p in roster.players if not p.photo_url][:_PHOTO_BACKFILL_MAX_PLAYERS]
    if not targets:
        return roster

    async def fetch(player: domain.Player) -> str | None:
        try:
            return await player_photos.lookup_photo(
                player.name, team_name=team.name, sport=sport
            )
        except Exception:
            logger.exception(
                "refresh_rosters: photo lookup failed for %r — skipping", player.name
            )
            return None

    photos = await asyncio.gather(*(fetch(player) for player in targets))
    by_id = {player.id: url for player, url in zip(targets, photos) if url}
    if not by_id:
        return roster

    players = tuple(
        replace(player, photo_url=by_id[player.id]) if player.id in by_id else player
        for player in roster.players
    )
    return replace(roster, players=players)


async def refresh_rosters() -> None:
    try:
        leagues, teams = await _load_leagues_and_teams()
        for team in teams:
            league = leagues.get(team.league_id)
            if league is None:
                logger.error(
                    "Team %r references unknown league %r — skipping", team.id, team.league_id
                )
                continue
            provider = _provider_for(league)
            if provider is None:
                continue
            try:
                roster = await provider.get_roster(league, team)
                # Soccer-only photo fallback: ESPN gives soccer clubs
                # headshots for ~2 of 38 players, so backfill the rest from
                # TheSportsDB (capped + cached) before storing.
                if league.sport is Sport.SOCCER:
                    roster = await _attach_player_photos(roster, team, league.sport.value)
                async with session_scope() as session:
                    await repository.replace_roster(session, roster)
                logger.info(
                    "refresh_rosters: %s — %d player(s)", team.id, len(roster.players)
                )
            except Exception:
                logger.exception("refresh_rosters: failed for team %r — skipping", team.id)
    except Exception:
        logger.exception("refresh_rosters failed")


async def refresh_news() -> None:
    try:
        new_items = await news.refresh_all_news()
        logger.info("refresh_news: %d new item(s)", new_items)
    except Exception:
        logger.exception("refresh_news failed")


def _merge_locations(
    base: domain.TeamLocation | None, extra: domain.TeamLocation | None
) -> domain.TeamLocation | None:
    """Overlay ``extra``'s facts onto ``base`` without dropping known values.

    Used to fold the TheSportsDB enrichment into the provider's location:
    the provider's venue/coords win when present, but any fact (capacity,
    opened year, photo, location text, surface) the provider lacks is taken
    from the enrichment.  Either side may be ``None``.
    """
    if base is None:
        return extra
    if extra is None:
        return base
    return replace(
        base,
        venue=base.venue or extra.venue,
        lat=base.lat if base.lat is not None else extra.lat,
        lon=base.lon if base.lon is not None else extra.lon,
        capacity=base.capacity if base.capacity is not None else extra.capacity,
        opened=base.opened if base.opened is not None else extra.opened,
        image_url=base.image_url or extra.image_url,
        location=base.location or extra.location,
        surface=base.surface or extra.surface,
    )


async def _resolve_team_location(
    provider, league: domain.League, team: domain.Team
) -> domain.TeamLocation | None:
    """Resolve one followed team's home venue to a :class:`TeamLocation`.

    Strategy, all defensive (each step degrades to the next on failure):

    1. Ask the provider for a ``TeamLocation`` (it may carry coordinates,
       a venue name, or nothing).
    2. Enrich by team name via TheSportsDB (``stadiums.lookup_stadium``):
       this is what rescues soccer clubs — ESPN gives them no venue, but
       the name lookup yields the stadium, capacity, photo and often
       coordinates.  Provider facts win; enrichment fills the gaps.
    3. Geocode a venue name (the provider's, the enrichment's, or the
       team's most-common stored-game venue) when no coordinates resolved.
    4. Stored-game venue: when the provider and enrichment had no venue
       name at all, borrow the team's most-common home-game venue from
       stored fixtures and geocode that.

    Returns ``None`` when nothing usable could be found, so the caller
    skips the team (and retries it on a later run).
    """
    # (1) Provider coords / venue.
    location: domain.TeamLocation | None = None
    try:
        location = await provider.get_team_location(league, team)
    except Exception:
        logger.exception(
            "refresh_locations: get_team_location failed for %s — continuing", team.id
        )

    # (2) TheSportsDB enrichment by team name (venue + facts + often coords).
    enrichment = await stadiums.lookup_stadium(team.name, sport=league.sport.value)
    location = _merge_locations(location, enrichment)

    venue = location.venue if location is not None else None

    # Already have coordinates (provider or enrichment) — done, with facts.
    if location is not None and location.lat is not None and location.lon is not None:
        return location

    # (3)/(4) Need a venue name to geocode; fall back to stored games.
    if not venue:
        async with session_scope() as session:
            venue = await repository.most_common_home_venue(session, team.id)
    if not venue:
        logger.info(
            "refresh_locations: no venue known for %s — skipping for now", team.id
        )
        return None

    coords = await geocode.geocode_venue(venue)
    if coords is None:
        logger.info(
            "refresh_locations: could not geocode venue %r for %s — skipping",
            venue, team.id,
        )
        return None
    lat, lon = coords
    if location is None:
        return domain.TeamLocation(venue=venue, lat=lat, lon=lon)
    return replace(location, venue=venue, lat=lat, lon=lon)


async def refresh_locations() -> None:
    """Resolve + cache home-venue coordinates for followed teams (map view).

    Only teams that do not already have coordinates are processed — a
    resolved team is never geocoded again (the lat/lon are cached on the
    team row).  Per-team failures are isolated so one unresolvable venue
    never aborts the rest, and the job itself never raises (the scheduler
    and the startup kick both rely on that).
    """
    try:
        leagues, _ = await _load_leagues_and_teams()
        async with session_scope() as session:
            team_rows = await repository.list_teams(session)
            # Snapshot which teams still need coordinates, detached from
            # the session, before fetching providers / geocoding.
            pending = [
                (_team_from_row(row), row.league_id)
                for row in team_rows
                if row.venue_lat is None or row.venue_lon is None
            ]

        resolved = 0
        for team, _league_id in pending:
            league = leagues.get(team.league_id)
            if league is None:
                logger.error(
                    "refresh_locations: team %r references unknown league %r — skipping",
                    team.id, team.league_id,
                )
                continue
            provider = _provider_for(league)
            if provider is None:
                continue
            try:
                location = await _resolve_team_location(provider, league, team)
                if location is None or location.lat is None or location.lon is None:
                    continue
                async with session_scope() as session:
                    await repository.set_team_location(
                        session,
                        team.id,
                        location.venue,
                        location.lat,
                        location.lon,
                        capacity=location.capacity,
                        opened=location.opened,
                        image_url=location.image_url,
                        location=location.location,
                        surface=location.surface,
                    )
                resolved += 1
                logger.info(
                    "refresh_locations: %s located at %r (%.4f, %.4f)",
                    team.id, location.venue, location.lat, location.lon,
                )
            except Exception:
                logger.exception(
                    "refresh_locations: failed for team %r — skipping", team.id
                )
        logger.info(
            "refresh_locations: resolved %d of %d pending team(s)",
            resolved, len(pending),
        )
    except Exception:
        logger.exception("refresh_locations failed")


async def _resolve_team_info(team: domain.Team, sport: str) -> stadiums.TeamInfo | None:
    """Resolve one team's "About" facts: TheSportsDB first, Wikipedia fallback.

    TheSportsDB's ``searchteams`` description is sport-correct and reliable;
    Wikipedia only fills a missing description (its lead paragraph).  All
    defensive — returns ``None`` when neither source has anything.
    """
    info = await stadiums.lookup_team_info(team.name, sport=sport)
    description = info.description if info is not None else None
    founded = info.founded if info is not None else None

    if description is None:
        summary = await wiki.team_summary(team.name, sport=sport)
        if summary is not None and summary.extract:
            description = summary.extract

    if description is None and founded is None:
        return None
    return stadiums.TeamInfo(description=description, founded=founded)


async def refresh_team_info() -> None:
    """Resolve + cache club "About" facts (description + founded) per team.

    Only teams still missing a description are processed (founding year is
    usually resolved in the same pass), so an enriched team is never
    re-fetched.  Per-team failures are isolated and the job never raises —
    the scheduler and the startup kick both rely on that.
    """
    try:
        leagues, _ = await _load_leagues_and_teams()
        async with session_scope() as session:
            team_rows = await repository.list_teams(session)
            pending = [
                _team_from_row(row)
                for row in team_rows
                if row.description is None
            ]

        resolved = 0
        for team in pending:
            league = leagues.get(team.league_id)
            if league is None:
                continue
            try:
                info = await _resolve_team_info(team, league.sport.value)
                if info is None:
                    continue
                async with session_scope() as session:
                    await repository.set_team_info(
                        session,
                        team.id,
                        description=info.description,
                        founded_year=info.founded,
                    )
                resolved += 1
            except Exception:
                logger.exception(
                    "refresh_team_info: failed for team %r — skipping", team.id
                )
        logger.info(
            "refresh_team_info: resolved %d of %d pending team(s)",
            resolved, len(pending),
        )
    except Exception:
        logger.exception("refresh_team_info failed")


# ---------------------------------------------------------------------------
# Whole-competition stadium cache (map view)
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
                if await repository.league_has_games_in_window(
                    session, row.id, start, end
                ):
                    active.append(_league_from_row(row))
            except Exception:
                logger.exception(
                    "active_follow_all_leagues: bad league row %r — skipping", row.id
                )
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
            logger.exception(
                "all_follow_all_leagues: bad league row %r — skipping", row.id
            )
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
        enrichment = await stadiums.lookup_stadium(
            team.name, sport=league.sport.value
        )
    except Exception:
        logger.exception(
            "refresh_competition_stadiums: enrichment failed for %s — continuing",
            team.name,
        )

    if (
        enrichment is not None
        and enrichment.lat is not None
        and enrichment.lon is not None
    ):
        return enrichment

    venue = enrichment.venue if enrichment is not None else None
    if not venue:
        return None

    try:
        coords = await geocode.geocode_venue(venue)
    except Exception:
        logger.exception(
            "refresh_competition_stadiums: geocode failed for %r — skipping", venue
        )
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
                        and utcnow() - ensure_utc(cached.fetched_at)
                        < _STADIUM_MISS_RETRY
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
                        team.name, key,
                    )
                # Pace external calls regardless of outcome.
                await asyncio.sleep(_COMPETITION_RESOLVE_DELAY_SECONDS)

            logger.info(
                "refresh_competition_stadiums: %s — %d/%d team stadium(s) located",
                league.id, resolved, len(teams),
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
            games = await repository.upcoming_games_for_leagues(
                session, league_ids, now, end
            )
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
            logger.info(
                "refresh_game_venue_coords: geocoded %d game venue(s)", len(pending)
            )
    except Exception:
        logger.exception("refresh_game_venue_coords failed")


async def daily_refresh() -> None:
    """Full refresh: schedules + standings + rosters + news + locations + cleanup."""
    logger.info("daily_refresh: starting")
    await refresh_schedules()
    await refresh_standings()
    await refresh_rosters()
    await refresh_news()
    # Club "About" facts (history + founded year) for followed teams' pages.
    await refresh_team_info()
    # After schedules, so the home-venue fallback can read stored games.
    await refresh_locations()
    # After schedules, so the host-venue path can read near fixtures —
    # pre-resolve every follow_all field's home stadiums for the map view,
    # in or out of season.
    await refresh_competition_stadiums()
    # After schedules, so the upcoming-games map has venue coordinates ready
    # (away grounds the host table / stadium index don't already cover).
    await refresh_game_venue_coords()
    try:
        # After schedules are fresh, anything still scheduled/in_progress
        # days in the past is a ghost no provider will update — drop it.
        async with session_scope() as session:
            await repository.prune_stale_games(session, utcnow())
    except Exception:
        logger.exception("daily_refresh: pruning stale games failed")
    logger.info("daily_refresh: complete")


# Strong references to kicked-off tasks: the event loop only holds weak
# references, so an otherwise-unreferenced task could be GC'd mid-flight.
_kicked_tasks: set[asyncio.Task[None]] = set()


def _kicked_task_done(task: asyncio.Task[None]) -> None:
    _kicked_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("kicked daily_refresh failed", exc_info=exc)


def kick_daily_refresh() -> None:
    """Spawn ``daily_refresh()`` as a fire-and-forget background task.

    Called by the setup routes right after the followed set changes so
    the new teams' data starts loading immediately — a route can't await
    a multi-source refresh inline (and must not import ``app.main`` for
    its task spawner: circular).
    """
    task = asyncio.create_task(daily_refresh(), name="kicked-daily-refresh")
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


def kick_refresh_locations() -> None:
    """Spawn ``refresh_locations()`` as a fire-and-forget background task.

    Kicked once on startup so the map populates from cached/provider-known
    coordinates without waiting for the next daily cron.  ``refresh_locations``
    never raises, but the done-callback still logs any unexpected escape and
    holds a strong reference so the task can't be GC'd mid-flight.
    """
    task = asyncio.create_task(refresh_locations(), name="kicked-refresh-locations")
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


def kick_team_info() -> None:
    """Spawn ``refresh_team_info()`` as a fire-and-forget background task.

    Kicked once on startup so already-followed teams' "About" sections fill
    in without waiting for the next daily cron (a team enriched once is then
    skipped).  ``refresh_team_info`` never raises; the done-callback logs any
    unexpected escape and holds a strong reference against GC.
    """
    task = asyncio.create_task(refresh_team_info(), name="kicked-refresh-team-info")
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


# A single in-flight on-demand location refresh, coalesced across concurrent
# ``GET /api/map`` calls: the map view polls, so a freshly-followed team's
# coordinates must start resolving without spawning a new refresh on every
# poll.  ``None`` (or a finished task) means none is running.
_ondemand_locations_task: asyncio.Task[None] | None = None


def kick_locations_if_pending() -> None:
    """On-demand hook for ``GET /api/map``: resolve missing coordinates soon.

    Called by the map route when followed teams still lack coordinates so a
    just-followed team isn't silently missing while the daily cron is hours
    away.  Fire-and-forget (the route must not block on a multi-source
    resolve): the newly-resolved coordinates land on a subsequent poll.
    Coalesced — if a refresh is already in flight, this is a no-op, so map
    polling can't pile up overlapping refreshes.  Never raises.
    """
    global _ondemand_locations_task
    task = _ondemand_locations_task
    if task is not None and not task.done():
        return
    task = asyncio.create_task(refresh_locations(), name="ondemand-refresh-locations")
    _ondemand_locations_task = task
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


# A single in-flight on-demand competition-stadium refresh, coalesced across
# concurrent ``GET /api/map`` calls the same way as the followed-team
# locations above: the map view polls, so an active competition's field must
# start resolving its stadiums without spawning a new refresh on every poll.
_ondemand_competition_task: asyncio.Task[None] | None = None


def kick_competition_stadiums() -> None:
    """On-demand hook for ``GET /api/map``: pre-resolve active-competition stadiums.

    Called by the map route when an active ``follow_all`` competition has
    teams whose stadiums are not yet cached, so its whole-field pins start
    filling in without the request geocoding the field inline.
    Fire-and-forget and coalesced — a refresh already in flight makes this a
    no-op, so map polling can't pile up overlapping resolves.  Never raises.
    """
    global _ondemand_competition_task
    task = _ondemand_competition_task
    if task is not None and not task.done():
        return
    task = asyncio.create_task(
        refresh_competition_stadiums(), name="ondemand-refresh-competition-stadiums"
    )
    _ondemand_competition_task = task
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


# A single in-flight on-demand game-venue geocode, coalesced across concurrent
# ``GET /api/map`` polls the same way as the two refreshes above.
_ondemand_game_venues_task: asyncio.Task[None] | None = None


def kick_game_venue_coords() -> None:
    """On-demand hook for ``GET /api/map``: geocode upcoming-game venues soon.

    Called by the map route when an upcoming game's venue isn't yet
    resolvable (not a host venue, not in the located-stadium/team index, and
    not yet cached), so its pin fills in on a later poll without the request
    geocoding inline.  Fire-and-forget and coalesced — a refresh already in
    flight makes this a no-op.  Never raises.
    """
    global _ondemand_game_venues_task
    task = _ondemand_game_venues_task
    if task is not None and not task.done():
        return
    task = asyncio.create_task(
        refresh_game_venue_coords(), name="ondemand-refresh-game-venue-coords"
    )
    _ondemand_game_venues_task = task
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


async def _refresh_standings_for_leagues(leagues: list[domain.League]) -> None:
    """Refresh several leagues' standings in turn; each one isolated.

    The coroutine spawned by :func:`kick_standings_refresh` when one or
    more followed leagues had a game go FINAL in a live tick.  Each league
    is refreshed via :func:`refresh_standings_for_league` (which never
    raises), and the wrapper itself swallows anything unexpected so the
    fire-and-forget task can't surface an error.
    """
    try:
        for league in leagues:
            await refresh_standings_for_league(league)
    except Exception:
        logger.exception("live standings refresh failed")


def kick_standings_refresh(leagues: list[domain.League]) -> None:
    """Spawn a one-shot standings refresh for ``leagues`` (live FINAL trigger).

    Called by ``live_tick``/``events_tick`` once per tick with the
    deduplicated set of followed leagues whose game(s) just transitioned to
    FINAL, so standings move in near-real-time without blocking the poll.
    No-op when ``leagues`` is empty.  A strong reference is held (the loop
    only keeps weak ones) and the done-callback logs any escape.
    """
    if not leagues:
        return
    task = asyncio.create_task(
        _refresh_standings_for_leagues(leagues), name="kicked-standings-refresh"
    )
    _kicked_tasks.add(task)
    task.add_done_callback(_kicked_task_done)


# ---------------------------------------------------------------------------
# Live polling
# ---------------------------------------------------------------------------

async def _notify_once(session, event: GameEvent) -> None:
    """Send an event unless already sent; record it only on confirmed delivery."""
    try:
        if await repository.was_notified(session, event.dedupe_key):
            return
        if await notify.send_event(event):
            await repository.mark_notified(session, event.dedupe_key)
        else:
            logger.warning(
                "Notification %r not delivered — will retry on a later tick",
                event.dedupe_key,
            )
    except Exception:
        logger.exception("Failed handling notification %r", event.dedupe_key)


def _provider_game_key(game_id: str) -> str:
    return game_id.split(":", 1)[1] if ":" in game_id else game_id


def _row_team_ids(row: GameORM) -> list[str]:
    """The game's followed-team ids (either side), Nones dropped."""
    return [tid for tid in (row.home_team_id, row.away_team_id) if tid is not None]


async def _resend_missed_finals(session, prefs, now) -> None:
    """Re-send FINAL notifications dropped when their first send failed.

    A FINAL whose ntfy send failed is committed as state but never marked
    notified, and the game then drops out of the live-poll gate — so this is
    the only place it is retried.  The FINAL event is rebuilt from the STORED
    row (``diff_states`` with ``prev=None`` re-fires FINAL — no provider call)
    and routed through :func:`_notify_once`, whose was_notified/mark_notified
    pair makes the resend idempotent: a final that actually sent on the first
    try is excluded by the query's dedupe filter, and one that still fails
    stays unmarked for the next tick.  Honors preferences like the live path.
    """
    settings = get_settings()
    lookback = timedelta(hours=settings.resend_final_lookback_hours)
    try:
        rows = await repository.finals_missing_notification(session, now, lookback)
    except Exception:
        logger.exception("live_tick: querying missed finals failed — skipping resend")
        return
    if not rows:
        return
    for row in rows:
        try:
            state = repository.state_from_row(row)
            team_ids = _row_team_ids(row)
            for event in diff_states(
                None, state, home_name=row.home_name, away_name=row.away_name
            ):
                if event.type is not EventType.FINAL:
                    continue
                if not notify_prefs.decide(
                    prefs, event.type.value, team_ids, row.league_id
                ):
                    continue
                await _notify_once(session, event)
        except Exception:
            logger.exception(
                "live_tick: resending final for %r failed — skipping", row.id
            )
    await session.commit()


async def live_tick() -> None:
    """One fast poll: only does provider work while a followed team is (nearly) playing.

    Progress is committed incrementally — after the starting-soon pass
    and after each game — so a failure mid-tick can neither discard the
    states/dedupe-marks of games already handled (which would re-send
    their notifications next tick) nor, on postgres, poison the rest of
    the tick with an aborted transaction.  Caught DB errors roll the
    session back before the loop continues.
    """
    settings = get_settings()
    try:
        now = utcnow()
        async with session_scope() as session:
            # Notification preferences are loaded once per tick and consulted
            # before every send (most-specific scope wins; see notify_prefs).
            # Loaded first because the resend step also needs them.
            prefs = await repository.prefs_by_scope(session)

            # Resend any FINAL whose first send failed — committed as state but
            # never marked notified, then dropped out of the gate below, so
            # this is the only path that retries it.  Runs every tick.
            await _resend_missed_finals(session, prefs, now)

            # 1. Cheap gate: a single indexed query; usually empty.
            rows = await repository.games_needing_live_poll(
                session, now, lead=timedelta(minutes=settings.live_lead_minutes)
            )
            if not rows:
                return

            # 2. Starting-soon notifications for scheduled games inside the window.
            soon_window = timedelta(minutes=settings.starting_soon_minutes)
            for row in rows:
                if row.phase != GamePhase.SCHEDULED.value:
                    continue
                start_utc = ensure_utc(row.start_time)
                delta = start_utc - now
                if timedelta(0) <= delta <= soon_window:
                    if not notify_prefs.decide(
                        prefs,
                        EventType.STARTING_SOON.value,
                        _row_team_ids(row),
                        row.league_id,
                    ):
                        continue
                    event = starting_soon_event(
                        start_utc,
                        row.id,
                        home_name=row.home_name,
                        away_name=row.away_name,
                        minutes_out=int(delta.total_seconds() // 60),
                    )
                    await _notify_once(session, event)
            await session.commit()

            # 3. Group rows by league; one scoreboard call per league.
            league_rows = await repository.list_leagues(session)
            leagues_by_id = {row.id: row for row in league_rows}
            rows_by_league: defaultdict[str, list[GameORM]] = defaultdict(list)
            for row in rows:
                rows_by_league[row.league_id].append(row)

            # Leagues whose game(s) transitioned to FINAL this tick — one
            # standings refresh is kicked per league at the end (coalesced),
            # so positions move in near-real-time without re-refreshing a
            # league once per finished game.
            finalized_leagues: dict[str, domain.League] = {}

            for league_id, games_rows in rows_by_league.items():
                league_row = leagues_by_id.get(league_id)
                if league_row is None:
                    logger.error("live_tick: games reference unknown league %r", league_id)
                    continue
                try:
                    league = _league_from_row(league_row)
                except Exception:
                    logger.exception("live_tick: invalid league row %r — skipping", league_id)
                    continue
                provider = _provider_for(league)
                if provider is None:
                    continue

                try:
                    live_games = await provider.get_live_games(league)
                except Exception:
                    logger.exception(
                        "live_tick: get_live_games failed for league %r — skipping", league_id
                    )
                    continue
                states_by_id: dict[str, GameState] = {
                    game.id: game.state for game in live_games if game.state is not None
                }

                for row in games_rows:
                    new_state = states_by_id.get(row.id)
                    # Fall back to a direct fetch when the scoreboard didn't
                    # cover the game: it just ended and dropped off, or it's
                    # overdue to start but listed under a different scoreboard
                    # day than the one we queried.
                    overdue = (
                        row.phase == GamePhase.SCHEDULED.value
                        and ensure_utc(row.start_time) <= now
                    )
                    if new_state is None and (
                        row.phase == GamePhase.IN_PROGRESS.value or overdue
                    ):
                        try:
                            new_state = await provider.get_game_state(
                                league, _provider_game_key(row.id)
                            )
                        except Exception:
                            logger.exception(
                                "live_tick: get_game_state failed for %r — skipping", row.id
                            )
                            continue
                    if new_state is None:
                        continue

                    # 4. Diff against the stored snapshot, persist, notify,
                    # and commit this game before moving to the next one.
                    try:
                        prev = repository.state_from_row(row)
                        events = diff_states(
                            prev,
                            new_state,
                            home_name=row.home_name,
                            away_name=row.away_name,
                        )
                        await repository.apply_game_state(session, new_state)
                        team_ids = _row_team_ids(row)
                        for event in events:
                            if not notify_prefs.decide(
                                prefs, event.type.value, team_ids, row.league_id
                            ):
                                continue
                            await _notify_once(session, event)
                        await session.commit()
                        # The game just transitioned into FINAL (a phase
                        # change this tick) — note its league so its
                        # standings get one near-real-time refresh.  Recorded
                        # only after the commit so a rolled-back game can't
                        # trigger a refresh on stale data.
                        if (
                            prev.phase is not GamePhase.FINAL
                            and new_state.phase is GamePhase.FINAL
                        ):
                            finalized_leagues.setdefault(league.id, league)
                    except Exception:
                        logger.exception(
                            "live_tick: failed processing %r — skipping", row.id
                        )
                        await session.rollback()
                        continue

        # 5. Standings move when a game finishes: kick one coalesced refresh
        # per league that had a final this tick (isolated; never blocks the
        # poll).  Outside the session scope — the refresh opens its own.
        kick_standings_refresh(list(finalized_leagues.values()))
    except Exception:
        logger.exception("live_tick failed")


# ---------------------------------------------------------------------------
# Leaderboard (golf) live polling
# ---------------------------------------------------------------------------

def _event_provider_key(event_id: str) -> str:
    return event_id.split(":", 1)[1] if ":" in event_id else event_id


def _final_event(event: domain.Event) -> GameEvent | None:
    """Build the tournament-FINAL notification for a followed golfer.

    Fires once per finished event the user follows, leading with the
    followed golfer's finishing position (the most specific information a
    leaderboard offers).  Returns ``None`` when no followed golfer is in
    the field — re-tagging has already rewritten ``player_id`` to the
    internal id (or ``None``) so a followed row is just one with a
    ``player_id`` set.  When several followed golfers played the same
    event, the best-placed one leads the headline.
    """
    followed = [row for row in event.leaderboard if row.player_id is not None]
    if not followed:
        return None
    best = min(followed, key=lambda row: row.position)
    extras = [row for row in followed if row is not best]
    message = f"{best.name} finished {best.position_label} ({best.score})"
    if extras:
        others = ", ".join(f"{row.name} {row.position_label}" for row in extras)
        message = f"{message}; also: {others}"
    return GameEvent(
        type=EventType.FINAL,
        game_id=event.id,
        title=f"Final: {event.name}",
        message=message,
        dedupe_key=f"{event.id}:final",
    )


def _event_from_row(row: EventORM) -> domain.Event:
    """Rebuild a domain.Event from a stored EventORM row (no provider call).

    The stored ``leaderboard`` dicts already carry the internal ``player_id``
    tagging the live tick wrote, so :func:`_final_event` sees the same
    followed golfers it did when the FINAL was first observed.
    """
    board = tuple(
        domain.LeaderRow(
            position=entry.get("position", 0),
            position_label=entry.get("position_label", ""),
            name=entry.get("name", ""),
            score=entry.get("score", ""),
            detail=entry.get("detail"),
            player_id=entry.get("player_id"),
        )
        for entry in (row.leaderboard or [])
    )
    return domain.Event(
        id=row.id,
        league_id=row.league_id,
        name=row.name,
        start_time=ensure_utc(row.start_time),
        phase=GamePhase(row.phase),
        end_time=ensure_utc(row.end_time) if row.end_time else None,
        round_label=row.round_label,
        venue=row.venue,
        leaderboard=board,
    )


async def _resend_missed_event_finals(session, prefs, now) -> None:
    """events_tick counterpart of :func:`_resend_missed_finals`.

    Rebuilds the tournament-FINAL notification from the STORED leaderboard
    (already internal-id tagged) via :func:`_final_event` and routes it through
    :func:`_notify_once` — same idempotency and recency guarantees.
    """
    settings = get_settings()
    lookback = timedelta(hours=settings.resend_final_lookback_hours)
    try:
        rows = await repository.event_finals_missing_notification(
            session, now, lookback
        )
    except Exception:
        logger.exception("events_tick: querying missed finals failed — skipping resend")
        return
    if not rows:
        return
    for row in rows:
        try:
            event_obj = _event_from_row(row)
            event = _final_event(event_obj)
            if event is None:
                continue
            followed_ids = [
                r.player_id for r in event_obj.leaderboard if r.player_id is not None
            ]
            if not notify_prefs.decide(
                prefs, event.type.value, followed_ids, row.league_id
            ):
                continue
            await _notify_once(session, event)
        except Exception:
            logger.exception(
                "events_tick: resending final for %r failed — skipping", row.id
            )
    await session.commit()


async def events_tick() -> None:
    """One leaderboard poll: refresh in-progress golf events, notify on FINAL.

    Cheap-gated like ``live_tick``: ``active_events`` is a single indexed
    query that is usually empty, so the job costs nothing when no followed
    golfer is mid-tournament.  For each active event we fetch the current
    state, re-tag followed golfers' rows (ESPN id -> internal id), persist
    the refreshed board, and — on transition to FINAL — fire one
    notification carrying the followed golfer's finishing position
    (dedupe ``"{event_id}:final"``, honoring notification preferences).
    Per-event failures are isolated and committed incrementally so one bad
    event can neither abort the rest nor re-send a handled notification.
    """
    try:
        now = utcnow()
        async with session_scope() as session:
            prefs = await repository.prefs_by_scope(session)

            # Resend any tournament FINAL whose first send failed — committed
            # as state, never marked notified, then dropped from active_events.
            await _resend_missed_event_finals(session, prefs, now)

            # 1. Cheap gate: in-progress (or about-to-start) events only.
            rows = await repository.active_events(session, now, _EVENTS_LOOKAHEAD)
            rows = [row for row in rows if row.phase == GamePhase.IN_PROGRESS.value]
            if not rows:
                return

            leagues, teams = await _load_leagues_and_teams()
            espn_to_internal = _golfer_id_map(teams, leagues)

            # Leagues whose event(s) transitioned to FINAL this tick — one
            # coalesced standings refresh is kicked at the end (same live
            # trigger as ``live_tick``).  The gate above only kept IN_PROGRESS
            # rows, so a fresh FINAL state is by definition a transition.
            finalized_leagues: dict[str, domain.League] = {}

            for row in rows:
                league = leagues.get(row.league_id)
                if league is None:
                    logger.error(
                        "events_tick: event %r references unknown league %r — skipping",
                        row.id, row.league_id,
                    )
                    continue
                provider = _provider_for(league)
                if provider is None:
                    continue

                try:
                    fresh = await provider.get_event_state(
                        league, _event_provider_key(row.id)
                    )
                except Exception:
                    logger.exception(
                        "events_tick: get_event_state failed for %r — skipping", row.id
                    )
                    continue
                if fresh is None:
                    continue

                try:
                    tagged = _tag_followed_golfers(fresh, espn_to_internal)
                    await repository.upsert_events(session, [tagged])
                    # The tournament just wrapped: notify each follower once
                    # with their finishing position.
                    if tagged.phase is GamePhase.FINAL:
                        event = _final_event(tagged)
                        if event is not None:
                            followed_ids = [
                                r.player_id
                                for r in tagged.leaderboard
                                if r.player_id is not None
                            ]
                            if notify_prefs.decide(
                                prefs, event.type.value, followed_ids, row.league_id
                            ):
                                await _notify_once(session, event)
                    await session.commit()
                    # Note the league for one near-real-time standings refresh
                    # — recorded only after the commit so a rolled-back event
                    # can't trigger a refresh on stale data.
                    if tagged.phase is GamePhase.FINAL:
                        finalized_leagues.setdefault(league.id, league)
                except Exception:
                    logger.exception(
                        "events_tick: failed processing %r — skipping", row.id
                    )
                    await session.rollback()
                    continue

        # Kick one coalesced standings refresh per league that had a final.
        kick_standings_refresh(list(finalized_leagues.values()))
    except Exception:
        logger.exception("events_tick failed")


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
