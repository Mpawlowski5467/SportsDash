"""Shared scheduler plumbing: tuning constants, ORM→domain loaders, and leaderboard helpers.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta


from app.db import session_scope
from app.models import convert, domain
from app.models.domain import LEADERBOARD_SPORTS
from app.providers import registry
from app.services import (
    repository,
)

logger = logging.getLogger(__name__)


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

# A long pre-resolve trips TheSportsDB's rate limit all the same.  Measured
# on a copy of a real database (two US leagues and 18 soccer leagues followed
# in whole): some 50 lookups at the pace above were answered, and the next 29
# teams were each refused with HTTP 429.  A refusal says nothing about the
# team, so recording it as a miss took 29 real stadiums off the map until a
# later sweep.  Instead the sweep waits this long and asks about the team
# once more ...
_TSDB_RATE_LIMIT_PAUSE_SECONDS = 30.0
# ... and from that first refusal on spaces its resolves this far apart
# instead, for the rest of the pass, so it stops tripping the limit.
_COMPETITION_THROTTLED_DELAY_SECONDS = 5.0
# A pause the retry came back from still unanswered bought nothing, and a few
# of those mean TheSportsDB is blocking rather than counting — it has refused
# every lookup for a whole session before.  No pause in this pass will
# outlast that, and each one keeps the sweep half a minute longer.  As a
# stage of daily_refresh the sweep does step aside for a save in the setup
# wizard (it is handed the pass's supersede check — see
# refresh_competition_stadiums), so the pass that save asks for is not what
# a pause holds up.  The rest of an undisturbed pass is: the venue geocoding
# and the stale-game prune run after this stage, and the status's
# `refreshing` flag cannot clear until they have.  So after this many
# fruitless pauses the pass stops waiting and records what it gets, as it
# always did.
_TSDB_MAX_FRUITLESS_PAUSES = 3

# Single-flight guard for refresh_competition_stadiums.  It is reachable from
# THREE callers (startup daily_refresh, the daily cron, and the on-demand
# map kick), and the cache write is a get-then-insert; two overlapping runs
# both see "no row" for the same key and the second INSERT trips the stadium
# primary-key constraint (and doubles the external request rate, tripping
# TheSportsDB's free-tier limit).  Holding this lock makes any second caller a
# no-op so only one resolve sweep ever runs at a time.
_competition_stadiums_lock = asyncio.Lock()

# A competition stadium that resolves to a MISS (no coordinates) is cached so
# the map stops asking — but a "miss" is not always a club that truly has no
# stadium: it is also what is recorded when TheSportsDB refused the lookup
# and refused it again after the pause above.  So a miss is retried once it
# is older than this, instead of being stuck forever; a real miss simply
# costs one cheap retry every interval.
_STADIUM_MISS_RETRY = timedelta(minutes=15)

# How far ahead the game-venue geocoder warms its cache.  The map's
# upcoming-games slider tops out at 30 days, so pre-resolving that whole span
# means any picked window is already warm (a venue resolved once is cached for
# a week regardless of which day its game falls on).
_GAME_VENUE_LOOKAHEAD_DAYS = 30


# ---------------------------------------------------------------------------

_league_from_row = convert.league_from_row
_team_from_row = convert.team_from_row


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
# Leaderboard (golf, racing) helpers
# ---------------------------------------------------------------------------


def _is_leaderboard(league: domain.League) -> bool:
    return league.sport in LEADERBOARD_SPORTS


def _athlete_id_map(teams: list[domain.Team], leagues: dict[str, domain.League]) -> dict[str, str]:
    """Map ESPN athlete id -> internal team id for every followed golfer/driver.

    Each followed leaderboard athlete is a single-member ``TeamORM`` whose
    ``provider_key`` is the ESPN athlete id, so a leaderboard row's
    ESPN id can be rewritten to the internal id when it belongs to a
    followed athlete.  Only leaderboard-league teams contribute, so a
    game-sport team that happens to share a provider_key string can't
    mis-tag.
    """
    mapping: dict[str, str] = {}
    for team in teams:
        league = leagues.get(team.league_id)
        if league is not None and _is_leaderboard(league):
            mapping[team.provider_key] = team.id
    return mapping


def _tag_followed_athletes(event: domain.Event, espn_to_internal: dict[str, str]) -> domain.Event:
    """Rewrite each leaderboard row's ``player_id`` to the internal id.

    The provider carries the ESPN athlete id transiently in
    ``LeaderRow.player_id`` (it can't know who is followed).  Here we
    rewrite it to the followed golfer's/driver's internal team id, or
    ``None`` when the athlete isn't followed, before persisting.
    """
    tagged_rows = tuple(
        replace(row, player_id=espn_to_internal.get(row.player_id))
        if row.player_id is not None
        else row
        for row in event.leaderboard
    )
    return replace(event, leaderboard=tagged_rows)
