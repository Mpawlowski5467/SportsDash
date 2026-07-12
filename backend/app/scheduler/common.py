"""Shared scheduler plumbing: tuning constants, ORM→domain loaders, and golf helpers.

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
from app.models.domain import Sport
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


def _tag_followed_golfers(event: domain.Event, espn_to_internal: dict[str, str]) -> domain.Event:
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
