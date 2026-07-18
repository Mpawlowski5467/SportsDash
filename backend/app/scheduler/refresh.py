"""Daily refresh jobs: schedules, standings, rosters (+photo backfill), news, locations, team info, and the daily_refresh orchestrator.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, timedelta


from app.db import session_scope
from app.models import domain
from app.models.domain import Sport
from app.services import (
    geocode,
    news,
    player_photos,
    repository,
    stadiums,
    wiki,
)
from app.timeutil import utcnow

from app.scheduler.common import (
    _golfer_id_map,
    _is_golf,
    _league_from_row,
    _load_leagues_and_teams,
    _load_team_competitions,
    _provider_for,
    _tag_followed_golfers,
    _team_from_row,
)
from app.scheduler.stadium_cache import (
    refresh_competition_stadiums,
    refresh_game_venue_coords,
)

logger = logging.getLogger(__name__)


# Per-player photo backfill (soccer only): how many photoless players per team
# to look up per roster refresh.  ESPN soccer rosters carry headshots for only
# a couple of players; the rest fall back to an initials chip.  Kept modest
# because TheSportsDB's free key is SHARED with the stadium/About enrichment —
# the backfill fails fast on a 429 and trickles (player_photos serializes +
# spaces), so a squad fills in GRADUALLY over several daily refreshes without
# exhausting the rate budget the other features need.  Hits cache for a month.
_PHOTO_BACKFILL_MAX_PLAYERS = 15


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
            label,
            len(games),
            touched,
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
                await _fetch_and_upsert_schedule(provider, league, team, start, end, label=team.id)

            for sib_league_id, provider_key in competitions.get(team.id, ()):
                sib_league = leagues.get(sib_league_id)
                if sib_league is None:
                    logger.error(
                        "Team %r references unknown competition %r — skipping",
                        team.id,
                        sib_league_id,
                    )
                    continue
                sib_provider = _provider_for(sib_league)
                if sib_provider is None:
                    continue
                # The team's id in this competition's context (global for
                # ESPN nations) — keep its own slug/league for id-merging.
                sib_team = replace(team, league_id=sib_league_id, provider_key=provider_key)
                await _fetch_and_upsert_schedule(
                    sib_provider,
                    sib_league,
                    sib_team,
                    start,
                    end,
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
                    league.id,
                    len(games),
                    touched,
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
                league_id,
                len(events),
                touched,
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
            # Keep the season history: the same table upserts into the
            # archive keyed by season, so when the league rolls over the
            # final table of the finished season is preserved.
            await repository.save_standings_archive(session, standings)
        logger.info("refresh_standings: %s — %d row(s)", league.id, len(standings.rows))
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
            return await player_photos.lookup_photo(player.name, team_name=team.name, sport=sport)
        except Exception:
            logger.exception("refresh_rosters: photo lookup failed for %r — skipping", player.name)
            return None

    photos = await asyncio.gather(*(fetch(player) for player in targets))
    by_id = {player.id: url for player, url in zip(targets, photos, strict=False) if url}
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
                logger.info("refresh_rosters: %s — %d player(s)", team.id, len(roster.players))
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
        logger.exception("refresh_locations: get_team_location failed for %s — continuing", team.id)

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
        logger.info("refresh_locations: no venue known for %s — skipping for now", team.id)
        return None

    coords = await geocode.geocode_venue(venue)
    if coords is None:
        logger.info(
            "refresh_locations: could not geocode venue %r for %s — skipping",
            venue,
            team.id,
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
                    team.id,
                    team.league_id,
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
                    team.id,
                    location.venue,
                    location.lat,
                    location.lon,
                )
            except Exception:
                logger.exception("refresh_locations: failed for team %r — skipping", team.id)
        logger.info(
            "refresh_locations: resolved %d of %d pending team(s)",
            resolved,
            len(pending),
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
            pending = [_team_from_row(row) for row in team_rows if row.description is None]

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
                logger.exception("refresh_team_info: failed for team %r — skipping", team.id)
        logger.info(
            "refresh_team_info: resolved %d of %d pending team(s)",
            resolved,
            len(pending),
        )
    except Exception:
        logger.exception("refresh_team_info failed")


# Single-flight guard for daily_refresh.  It is reachable from THREE callers
# (the daily cron, the startup spawn in main.py, and the setup-wizard kick),
# and its upserts are SELECT-then-insert: two overlapping runs both see "no
# row" for the same key, so the loser double-inserts and logs an
# IntegrityError traceback.  Holding this lock makes any second caller a
# logged no-op (skip, don't queue) — the same pattern as
# ``_competition_stadiums_lock`` in common.py.
_daily_refresh_lock = asyncio.Lock()


async def daily_refresh() -> None:
    """Full refresh: schedules + standings + rosters + news + locations + cleanup.

    Single-flight: a run already in progress makes any overlapping caller
    (cron, startup spawn, or setup-wizard kick) a no-op rather than racing
    the upserts; nothing queues behind a running refresh.
    """
    if _daily_refresh_lock.locked():
        logger.info("daily_refresh: a refresh is already running — skipping")
        return
    async with _daily_refresh_lock:
        await _daily_refresh()


async def _daily_refresh() -> None:
    """Body of :func:`daily_refresh`, run under its single-flight lock."""
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
