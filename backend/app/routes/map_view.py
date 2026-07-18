"""Stadium map view: followed teams plus active whole-competition fields.

The map assembles two sources of pins:

1. ``source="followed"`` — every followed team whose home-venue
   coordinates have already been resolved (the location-refresh job
   resolves a venue — provider coords, TheSportsDB stadium enrichment, or
   a geocode — and caches lat/lon plus the stadium facts on the team row).
   Teams without resolved coordinates are omitted, so the map renders as
   soon as any followed team has been located rather than waiting for all.

2. ``source="competition"`` — for every whole-competition follow
   (``League.follow_all``), the competition's full catalog field, each
   team resolved from the stadium cache (``StadiumORM`` keyed by
   ``"{provider}:{provider_key}"``).  The whole field is plotted in or out
   of season: while the competition is running and has fixtures at known
   host-country stadiums, each team pins at its next-match host venue;
   otherwise it falls back to its home ground.

Dedupe: a team that is both followed *and* in a followed competition
appears once, as ``"followed"`` (the user's own team wins).

ON-DEMAND: when followed teams still lack coordinates, or a followed
competition has teams not yet in the stadium cache, the route kicks the
relevant coalesced background refresh so the missing pins start resolving
— it never blocks the response on a multi-source resolve, returning what
is already located and letting the rest fill in on a later poll.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.config import get_settings
from app.db import get_session
from app.models.domain import GamePhase, Weather, WEATHER_SPORTS
from app.models.orm import GameORM, LeagueORM
from app.providers import espn_catalog
from app.schemas import MapGameOut, MapOut, MapTeamOut
from app.scheduler import jobs
from app.services import repository, venue_coords, wc_venues, weather
from app.services.serialize import game_to_out, weather_to_out

logger = logging.getLogger(__name__)

router = APIRouter()

# Phases that still count as "yet to play" when picking a team's next match.
_UPCOMING_PHASES = {GamePhase.SCHEDULED.value, GamePhase.IN_PROGRESS.value}

# Sport values (strings, as carried on MapTeamOut) eligible for venue weather.
_WEATHER_SPORT_VALUES = {sport.value for sport in WEATHER_SPORTS}


@dataclass(frozen=True)
class _NextMatch:
    """Where a team plays next (or most recently), for the host-venue pin."""

    venue: str | None
    opponent: str
    start_time: datetime


def _next_match_by_team(games: list[GameORM]) -> dict[str, _NextMatch]:
    """Map each team (by casefolded name) to its next-or-latest match.

    ``games`` is the league's full fixture list.  For each team we prefer
    the earliest still-to-play match (scheduled or live); if none remain —
    the nation is out, or the tournament is over — we fall back to its most
    recent match so it keeps a sensible pin until the whole competition
    drops off the map.  The opponent is recorded relative to the team, so
    the info panel can read "vs {opponent}".
    """
    upcoming: dict[str, _NextMatch] = {}  # earliest still-to-play, per team
    past: dict[str, _NextMatch] = {}  # latest already-played, per team
    for game in games:
        start = timeutil.ensure_utc(game.start_time)
        if start is None:
            continue
        bucket = upcoming if game.phase in _UPCOMING_PHASES else past
        keep_earliest = bucket is upcoming
        for name, opponent in (
            (game.home_name, game.away_name),
            (game.away_name, game.home_name),
        ):
            key = name.strip().casefold()
            match = _NextMatch(venue=game.venue, opponent=opponent, start_time=start)
            current = bucket.get(key)
            if (
                current is None
                or (keep_earliest and start < current.start_time)
                or (not keep_earliest and start > current.start_time)
            ):
                bucket[key] = match
    return {**past, **upcoming}  # upcoming wins when a team has both


@router.get("/map", response_model=MapOut)
async def map_view(
    days: int = Query(
        default=3,
        ge=1,
        le=30,
        description="Upcoming-games window (days from now) for the `games` list.",
    ),
    session: AsyncSession = Depends(get_session),
) -> MapOut:
    leagues = await repository.list_leagues(session)
    league_by_id = {league.id: league for league in leagues}
    sport_by_league = {league.id: league.sport for league in leagues}

    out_teams: list[MapTeamOut] = []
    # Followed teams' (provider, provider_key) identities so the competition
    # pass can skip a team the user follows directly — even when it has no
    # coordinates yet — so it never appears twice (followed wins the dedupe).
    # A competition team is keyed by the same ESPN id, so this matches across
    # the two sources regardless of differing internal slug ids.
    followed_identities: set[tuple[str, str]] = set()
    # Set when a followed team still lacks coordinates → kick a resolve.
    locations_pending = False

    # 1. Followed teams with resolved coordinates.
    teams = await repository.list_teams(session)
    for team in teams:
        league = league_by_id.get(team.league_id)
        if league is not None:
            followed_identities.add((league.provider, team.provider_key))
        if team.venue_lat is None or team.venue_lon is None:
            locations_pending = True
            continue
        out_teams.append(
            MapTeamOut(
                team_id=team.id,
                name=team.name,
                abbreviation=team.abbreviation,
                league_id=team.league_id,
                league_name=_league_name(league_by_id, team.league_id),
                sport=sport_by_league.get(team.league_id, ""),
                color=team.color,
                logo_url=team.logo_url,
                venue=team.home_venue,
                lat=team.venue_lat,
                lon=team.venue_lon,
                capacity=team.venue_capacity,
                opened=team.venue_opened,
                image_url=team.venue_image_url,
                location=team.venue_location,
                surface=team.venue_surface,
                description=team.description,
                founded_year=team.founded_year,
                source="followed",
            )
        )

    # 2. Whole-competition follows: plot each competition's full catalog
    # field from the stadium cache, in or out of season — an off-season
    # follow_all league still shows its teams at their home grounds, while
    # a running host tournament pins them at their next-match host venues.
    competition_teams, stadiums_pending = await _competition_teams(
        session, league_by_id, sport_by_league, followed_identities
    )
    out_teams.extend(competition_teams)

    # 3. Upcoming games (within `days`) placed at their venue coordinates —
    # the map's "games" mode. Independent of the home-stadium pins above.
    out_games, game_venues_pending = await _upcoming_games(session, league_by_id, days)

    # On-demand resolves (coalesced, never blocking): a just-followed team,
    # a just-activated competition, or an upcoming game at an unlocated venue
    # all start filling in for a later poll.
    if locations_pending:
        jobs.kick_locations_if_pending()
    if stadiums_pending:
        jobs.kick_competition_stadiums()
    if game_venues_pending:
        jobs.kick_game_venue_coords()

    await _attach_weather(out_teams)

    return MapOut(teams=out_teams, games=out_games, days=days)


async def _upcoming_games(
    session: AsyncSession,
    league_by_id: dict[str, LeagueORM],
    days: int,
) -> tuple[list[MapGameOut], bool]:
    """Upcoming games in ``[now, now + days]`` placed at their venue coords.

    Games (scheduled / in-progress) from every league the user follows in
    whole or in part.  Each game's venue is resolved with no network where
    possible — the World Cup host table, a followed team's own coordinates,
    or the name index of already-located teams/stadiums — falling back to the
    Redis venue-coords cache.  A game whose venue resolves nowhere is omitted
    and the returned ``pending`` flag tells the route to kick a background
    geocode, so its pin appears on a later poll rather than blocking the
    request on Nominatim.  Without Redis there is no cache to fall back to or
    warm, so an unresolvable venue is omitted with ``pending`` staying False.
    """
    now = timeutil.utcnow()
    end = now + timedelta(days=days)
    league_ids = await repository.map_relevant_league_ids(session)
    games = await repository.upcoming_games_for_leagues(session, league_ids, now, end)
    if not games:
        return [], False

    located_teams = await repository.list_teams_with_location(session)
    index = venue_coords.build_index(located_teams, await repository.list_located_stadiums(session))
    followed_coords = {team.id: (team.venue_lat, team.venue_lon) for team in located_teams}
    # Per-league standings-group lookups + per-request Redis cache, both built
    # lazily (a venue can host many games; a league's groups load once).
    group_cache: dict[str, dict[str, str]] = {}
    venue_cache: dict[str, tuple[float, float] | None] = {}
    # The venue-coords cache is Redis-backed: with no Redis configured every
    # read is a miss and every write a no-op, so an unresolvable venue must
    # NOT be flagged pending — that would kick the background geocode job on
    # every poll even though its results can never be stored or read back.
    redis_configured = bool(get_settings().redis_url)

    out: list[MapGameOut] = []
    pending = False
    for game in games:
        league = league_by_id.get(game.league_id)
        if league is None:
            continue

        coords = _game_in_memory_coords(game, index, followed_coords)
        if coords is None:
            norm = venue_coords.normalize(game.venue)
            if not norm:
                continue  # no venue name → can't place it anywhere
            if not redis_configured:
                # No venue cache to consult or warm — the game stays off the
                # map without flagging a background geocode that would
                # discard its own results.
                continue
            if norm not in venue_cache:
                venue_cache[norm] = await venue_coords.get_cached(game.venue)
            coords = venue_cache[norm]
            if coords is None:
                pending = True
                continue
        lat, lon = coords

        if game.league_id not in group_cache:
            group_cache[game.league_id] = await _group_by_team(session, game.league_id)
        groups = group_cache[game.league_id]
        group = groups.get(game.home_name.strip().casefold()) or groups.get(
            game.away_name.strip().casefold()
        )

        game_out = game_to_out(game, league)
        followed = bool(game_out.followed_team_ids)
        out.append(
            MapGameOut(
                game_id=game_out.id,
                league_id=game_out.league_id,
                league_name=league.name,
                sport=game_out.sport,
                venue=game_out.venue,
                lat=lat,
                lon=lon,
                home=game_out.home,
                away=game_out.away,
                start_time=game_out.start_time,
                phase=game_out.phase,
                period_label=game_out.period_label,
                group=group,
                followed=followed,
                source="followed" if followed else "competition",
            )
        )
    return out, pending


def _game_in_memory_coords(
    game: GameORM,
    index: dict[str, tuple[float, float]],
    followed_coords: dict[str, tuple[float | None, float | None]],
) -> tuple[float, float] | None:
    """A game's venue coordinates from in-memory sources only (no network).

    Priority: World Cup host venue → a followed *home* team's own coordinates
    (a home game) → the located-stadium/team name index.  ``None`` when none
    apply (the caller then tries the Redis venue cache).
    """
    host = wc_venues.resolve(game.venue)
    if host is not None:
        return host.lat, host.lon
    if game.home_team_id is not None:
        coords = followed_coords.get(game.home_team_id)
        if coords is not None and coords[0] is not None and coords[1] is not None:
            return coords[0], coords[1]
    norm = venue_coords.normalize(game.venue)
    if norm and norm in index:
        return index[norm]
    return None


async def _attach_weather(teams: list[MapTeamOut]) -> None:
    """Best-effort current conditions on outdoor-sport pins (in place).

    Fetches concurrently (the weather service bounds its own in-flight calls
    and caches results), and never raises — a failed lookup just leaves that
    pin's ``weather`` as ``None``.
    """
    if not get_settings().weather_enabled:
        return
    targets = [team for team in teams if team.sport in _WEATHER_SPORT_VALUES]
    if not targets:
        return
    results = await asyncio.gather(
        *(weather.fetch(team.lat, team.lon) for team in targets),
        return_exceptions=True,
    )
    for team, result in zip(targets, results, strict=False):
        if isinstance(result, Weather):
            team.weather = weather_to_out(result)


def _league_name(league_by_id, league_id: str) -> str | None:
    league = league_by_id.get(league_id)
    return league.name if league is not None else None


async def _competition_teams(
    session: AsyncSession,
    league_by_id,
    sport_by_league,
    followed_identities: set[tuple[str, str]],
) -> tuple[list[MapTeamOut], bool]:
    """Build the ``source="competition"`` pins for every follow_all league.

    For each ``follow_all`` league — in or out of season — enumerate its
    catalog field and resolve each team from the stadium cache
    (``StadiumORM`` by ``"{provider}:{provider_key}"``).  Teams whose
    stadium isn't cached yet are simply omitted — the returned ``pending``
    flag tells the route to kick a background pre-resolve so they fill in
    on a later poll, rather than geocoding the field inline.  Whenever a
    league has fixtures at a known host-country stadium (a World Cup-style
    tournament in progress), each nation pins at its next-match host venue
    instead of its home ground; off-season, with no such fixtures, the
    field falls back to home stadiums.

    Per-league failures are isolated (an unreachable catalog can't break
    the followed-team map); a competition team whose ``(provider,
    provider_key)`` the user already follows directly is skipped, so a
    followed team also in the competition is plotted once (as "followed").
    """
    follow_all_rows = await repository.list_follow_all_leagues(session)

    out: list[MapTeamOut] = []
    pending = False
    for league_row in follow_all_rows:
        catalog_league = espn_catalog.get_catalog_league(league_row.id)
        if catalog_league is None:
            continue
        try:
            catalog_teams = await espn_catalog.get_league_teams(catalog_league)
        except Exception:
            logger.exception(
                "map: could not list competition teams for %r — skipping", league_row.id
            )
            continue

        # Where each nation plays next — a host-country stadium for a World
        # Cup-style tournament.  Resolved from the league's synced fixtures so
        # the pin sits where the team actually plays, not at its home ground.
        next_by_team = _next_match_by_team(
            await repository.list_league_games(session, league_row.id)
        )
        # Standings group per team (e.g. "Group A") so the map can filter the
        # field by group; absent until standings are fetched.
        group_by_team = await _group_by_team(session, league_row.id)

        sport = sport_by_league.get(league_row.id, catalog_league.sport.value)
        for catalog_team in catalog_teams:
            # Followed wins the dedupe: skip a competition team the user
            # already follows directly (matched by ESPN provider + id, since
            # internal slug ids differ between the two sources).
            if (league_row.provider, catalog_team.provider_key) in followed_identities:
                continue
            # A competition team has no TeamORM row; synthesize a stable id
            # from the league + ESPN provider_key so the frontend has a key.
            team_id = f"{league_row.id}:{catalog_team.provider_key}"

            group = group_by_team.get(catalog_team.name.strip().casefold())

            # 1. Next-match host venue (host-country tournaments).  Only when
            #    the upcoming fixture is at a known host stadium, so ordinary
            #    competitions fall through to home-ground resolution below.
            next_match = next_by_team.get(catalog_team.name.strip().casefold())
            host = wc_venues.resolve(next_match.venue) if next_match else None
            if host is not None:
                out.append(
                    MapTeamOut(
                        team_id=team_id,
                        name=catalog_team.name,
                        abbreviation=catalog_team.abbreviation,
                        league_id=league_row.id,
                        league_name=league_row.name,
                        sport=sport,
                        color=catalog_team.color,
                        logo_url=catalog_team.logo_url,
                        venue=host.name,
                        lat=host.lat,
                        lon=host.lon,
                        capacity=host.capacity,
                        location=host.location,
                        next_opponent=next_match.opponent,
                        next_match_time=next_match.start_time,
                        group=group,
                        source="competition",
                    )
                )
                continue

            # 2. Fallback: the team's home stadium from the cache (the normal
            #    path for non-host competitions, e.g. a continental cup at home
            #    grounds).  Omit + flag a background pre-resolve until cached.
            key = f"{league_row.provider}:{catalog_team.provider_key}"
            stadium = await repository.get_stadium(session, key)
            if stadium is None or stadium.lat is None or stadium.lon is None:
                # Not resolved yet (or a cached miss): omit it and flag a
                # background pre-resolve.  A cached miss won't be re-resolved
                # hot (the job skips resolved rows) but still shouldn't block.
                if stadium is None:
                    pending = True
                continue
            out.append(
                MapTeamOut(
                    team_id=team_id,
                    name=catalog_team.name,
                    abbreviation=catalog_team.abbreviation,
                    league_id=league_row.id,
                    league_name=league_row.name,
                    sport=sport,
                    color=catalog_team.color,
                    logo_url=catalog_team.logo_url,
                    venue=stadium.venue,
                    lat=stadium.lat,
                    lon=stadium.lon,
                    capacity=stadium.capacity,
                    opened=stadium.opened,
                    image_url=stadium.image_url,
                    location=stadium.location,
                    surface=stadium.surface,
                    group=group,
                    source="competition",
                )
            )
    return out, pending


async def _group_by_team(session: AsyncSession, league_id: str) -> dict[str, str]:
    """Casefolded team name -> standings group label (e.g. "Group A").

    Read from the league's stored standings so the map can offer a
    by-group filter; empty until standings are first fetched.
    """
    standings = await repository.get_standings(session, league_id)
    if standings is None:
        return {}
    out: dict[str, str] = {}
    for row in standings.rows:
        name = row.get("team_name")
        group = row.get("group")
        if isinstance(name, str) and isinstance(group, str) and group:
            out[name.strip().casefold()] = group
    return out
