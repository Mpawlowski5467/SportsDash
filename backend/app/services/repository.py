"""Data-access layer: every DB read/write the app performs.

All functions take an ``AsyncSession`` as the first argument and never
commit — callers own the transaction (``session_scope()`` commits on
success; route handlers that write commit explicitly).

Upserts are implemented as SELECT-then-insert/update.  This is a
single-user app with no write concurrency, so the portable approach is
preferred over dialect-specific ``ON CONFLICT`` clauses (must run on
both sqlite and postgres).
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import domain
from app.models.orm import (
    AppMetaORM,
    EventORM,
    GameORM,
    LeagueORM,
    NewsORM,
    NotificationPrefORM,
    NotificationSentORM,
    PlayerORM,
    StadiumORM,
    StandingsArchiveORM,
    StandingsORM,
    TeamCompetitionORM,
    TeamORM,
)
from app.timeutil import ensure_utc, utcnow

logger = logging.getLogger(__name__)

_SCHEDULED_PHASE = domain.GamePhase.SCHEDULED.value
_FINAL_PHASE = domain.GamePhase.FINAL.value
_IN_PROGRESS_PHASE = domain.GamePhase.IN_PROGRESS.value
_INACTIVE_PHASES = (
    domain.GamePhase.POSTPONED.value,
    domain.GamePhase.CANCELED.value,
)


def _clip(value: str | None, limit: int) -> str | None:
    """Clamp provider strings to their column lengths.

    Postgres enforces VARCHAR(N) (sqlite does not); an over-long
    period_label or feed title must degrade to truncation, never to a
    DataError that aborts a whole refresh transaction.
    """
    if value is None or len(value) <= limit:
        return value
    return value[:limit]


# ---------------------------------------------------------------------------
# Leagues / teams
# ---------------------------------------------------------------------------


async def list_leagues(session: AsyncSession) -> list[LeagueORM]:
    result = await session.execute(select(LeagueORM).order_by(LeagueORM.id))
    return list(result.scalars().all())


async def list_teams(session: AsyncSession, league_id: str | None = None) -> list[TeamORM]:
    stmt = select(TeamORM).order_by(TeamORM.id)
    if league_id is not None:
        stmt = stmt.where(TeamORM.league_id == league_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_team(session: AsyncSession, team_id: str) -> TeamORM | None:
    return await session.get(TeamORM, team_id)


async def get_league(session: AsyncSession, league_id: str) -> LeagueORM | None:
    return await session.get(LeagueORM, league_id)


async def upsert_league(session: AsyncSession, league: domain.League) -> None:
    row = await session.get(LeagueORM, league.id)
    if row is None:
        row = LeagueORM(id=league.id)
        session.add(row)
    row.sport = league.sport.value
    row.name = league.name
    row.provider = league.provider
    row.provider_key = league.provider_key
    row.follow_all = league.follow_all


async def upsert_team(session: AsyncSession, team: domain.Team) -> None:
    row = await session.get(TeamORM, team.id)
    if row is None:
        row = TeamORM(id=team.id)
        session.add(row)
    row.league_id = team.league_id
    row.name = team.name
    row.abbreviation = team.abbreviation
    row.provider_key = team.provider_key
    row.logo_url = team.logo_url
    row.color = team.color
    row.rss_feeds = list(team.rss_feeds)


async def set_team_location(
    session: AsyncSession,
    team_id: str,
    venue: str | None,
    lat: float | None,
    lon: float | None,
    *,
    capacity: int | None = None,
    opened: int | None = None,
    image_url: str | None = None,
    location: str | None = None,
    surface: str | None = None,
) -> None:
    """Cache a team's resolved home-venue name + coordinates + facts on its row.

    Written by the location-refresh job once a venue is resolved so the
    map view can read coordinates and stadium facts straight off the team
    and the job never re-resolves an already-located team.  No-op (logged)
    when the team is unknown.  String facts are clipped to their column
    widths; the stadium-facts keyword arguments default to ``None`` so the
    coordinate-only callers (and existing callers/tests) are unaffected.
    """
    row = await session.get(TeamORM, team_id)
    if row is None:
        logger.warning("set_team_location: unknown team %s — skipping", team_id)
        return
    row.home_venue = _clip(venue, 256)
    row.venue_lat = lat
    row.venue_lon = lon
    row.venue_capacity = capacity
    row.venue_opened = opened
    row.venue_image_url = image_url
    row.venue_location = _clip(location, 256)
    row.venue_surface = _clip(surface, 64)


async def set_team_info(
    session: AsyncSession,
    team_id: str,
    *,
    description: str | None = None,
    founded_year: int | None = None,
    venue_description: str | None = None,
    description_source: str | None = None,
) -> None:
    """Cache a team's "About" facts (history paragraph + founding year).

    Written by the team-info refresh job once resolved (TheSportsDB /
    Wikipedia); the description columns are ``Text`` so they aren't clipped.
    ``venue_description`` is the stadium's own prose and ``description_source``
    records which upstream supplied ``description`` ("thesportsdb" |
    "wikipedia") so the profile can attribute it.  No-op (logged) when the
    team is unknown.  A value already set is only overwritten by a
    non-``None`` replacement, so a later source with less data never wipes
    resolved facts.
    """
    row = await session.get(TeamORM, team_id)
    if row is None:
        logger.warning("set_team_info: unknown team %s — skipping", team_id)
        return
    if description is not None:
        row.description = description
    if founded_year is not None:
        row.founded_year = founded_year
    if venue_description is not None:
        row.venue_description = venue_description
    if description_source is not None:
        row.description_source = description_source


async def list_teams_with_location(session: AsyncSession) -> list[TeamORM]:
    """Followed teams that have resolved coordinates (for the map view)."""
    stmt = (
        select(TeamORM)
        .where(TeamORM.venue_lat.is_not(None), TeamORM.venue_lon.is_not(None))
        .order_by(TeamORM.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Stadium cache (decoupled from TeamORM)
# ---------------------------------------------------------------------------


async def list_located_stadiums(session: AsyncSession) -> list[StadiumORM]:
    """Every cached stadium that resolved to coordinates (for the venue index)."""
    stmt = (
        select(StadiumORM)
        .where(StadiumORM.lat.is_not(None), StadiumORM.lon.is_not(None))
        .order_by(StadiumORM.key)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_stadium(session: AsyncSession, key: str) -> StadiumORM | None:
    """Fetch a cached stadium by ``"{provider}:{provider_key}"`` key.

    The cache is decoupled from ``TeamORM`` so a whole-competition follow
    (e.g. every World Cup nation) can plot teams that have no followed-team
    row of their own.  Returns the row whether it resolved to coordinates
    or was cached as a definitive miss (``resolved=True, lat/lon None``) —
    the caller checks ``lat``/``lon``.
    """
    return await session.get(StadiumORM, key)


async def upsert_stadium(
    session: AsyncSession,
    key: str,
    team_name: str,
    *,
    venue: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    capacity: int | None = None,
    opened: int | None = None,
    image_url: str | None = None,
    location: str | None = None,
    surface: str | None = None,
    resolved: bool = True,
) -> StadiumORM:
    """Insert-or-update one cached stadium; returns the row.

    Written by the competition-stadium refresh (and the on-request map
    fallback) once a team's venue is resolved — or definitively missed — so
    the map view reads coordinates straight from the cache without
    re-running the multi-source enrichment/geocode pipeline on every poll.
    ``resolved`` is stored ``True`` even for a miss (no coords) so a
    known-missing team is not re-resolved hot; a later run can still
    overwrite it once data appears.  String facts are clipped to their
    column widths.
    """
    row = await session.get(StadiumORM, key)
    if row is None:
        row = StadiumORM(key=key)
        session.add(row)
    row.team_name = _clip(team_name, 128) or ""
    row.venue = _clip(venue, 256)
    row.lat = lat
    row.lon = lon
    row.capacity = capacity
    row.opened = opened
    row.image_url = image_url
    row.location = _clip(location, 256)
    row.surface = _clip(surface, 64)
    row.resolved = resolved
    row.fetched_at = utcnow()
    return row


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


def _apply_state_columns(row: GameORM, state: domain.GameState) -> None:
    row.phase = state.phase.value
    row.home_score = state.home_score
    row.away_score = state.away_score
    row.period = state.period
    row.period_label = _clip(state.period_label, 32) or ""
    row.clock = _clip(state.clock, 16)
    row.is_intermission = state.is_intermission
    row.state_updated_at = utcnow()


def _should_apply_schedule_state(stored_phase: str, incoming: domain.GameState) -> bool:
    """Whether schedule-refresh data may overwrite a row's live-state columns.

    Live polling owns state transitions, so schedule data normally must
    not regress them.  The exceptions exist so rows can never get STUCK:

    - a 'scheduled' row accepts any incoming state (first live snapshot,
      or a scheduled -> postponed/canceled move),
    - an incoming FINAL is authoritative regardless of the stored phase —
      it is how a game that ended while the app was down (stored
      'in_progress' forever otherwise) gets its result on the next
      daily refresh,
    - a postponed/canceled row may be reinstated when the provider
      reports the fixture as scheduled or live again.
    """
    if stored_phase == _SCHEDULED_PHASE:
        return True
    if incoming.phase is domain.GamePhase.FINAL and stored_phase != _FINAL_PHASE:
        return True
    if stored_phase in _INACTIVE_PHASES and incoming.phase in (
        domain.GamePhase.SCHEDULED,
        domain.GamePhase.IN_PROGRESS,
    ):
        return True
    return False


async def upsert_games(session: AsyncSession, games: Sequence[domain.Game]) -> int:
    """Insert-or-update games by id; returns the number of rows touched.

    Schedule fields (start_time, venue, names, abbreviations, league_id)
    are always refreshed.  Team-id sides are *merged*: an incoming
    ``None`` never erases a stored id, so two followed teams fetching
    the same game each contribute their own side.  Live-state columns
    follow :func:`_should_apply_schedule_state` — live polling owns them,
    but finals are authoritative and postponed fixtures can come back.
    """
    touched = 0
    for game in games:
        row = await session.get(GameORM, game.id)
        if row is None:
            row = GameORM(
                id=game.id,
                league_id=game.league_id,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                home_name=_clip(game.home_name, 128) or "",
                away_name=_clip(game.away_name, 128) or "",
                home_abbreviation=_clip(game.home_abbreviation, 8),
                away_abbreviation=_clip(game.away_abbreviation, 8),
                home_logo_url=game.home_logo_url,
                away_logo_url=game.away_logo_url,
                home_color=_clip(game.home_color, 16),
                away_color=_clip(game.away_color, 16),
                start_time=ensure_utc(game.start_time),
                venue=_clip(game.venue, 256),
                series=_clip(game.series, 128),
            )
            if game.state is not None:
                _apply_state_columns(row, game.state)
            session.add(row)
            touched += 1
            continue

        # Schedule fields always refresh.
        row.league_id = game.league_id
        row.home_name = _clip(game.home_name, 128) or ""
        row.away_name = _clip(game.away_name, 128) or ""
        row.home_abbreviation = _clip(game.home_abbreviation, 8)
        row.away_abbreviation = _clip(game.away_abbreviation, 8)
        row.start_time = ensure_utc(game.start_time)
        row.venue = _clip(game.venue, 256)
        row.series = _clip(game.series, 128)
        # Crest/color refresh with the schedule, but never let an incoming
        # blank wipe a previously-resolved logo (some feeds omit it).
        if game.home_logo_url:
            row.home_logo_url = game.home_logo_url
        if game.away_logo_url:
            row.away_logo_url = game.away_logo_url
        if game.home_color:
            row.home_color = _clip(game.home_color, 16)
        if game.away_color:
            row.away_color = _clip(game.away_color, 16)

        # Merge team ids: never overwrite a stored id with None.
        if game.home_team_id is not None:
            row.home_team_id = game.home_team_id
        if game.away_team_id is not None:
            row.away_team_id = game.away_team_id

        if game.state is not None and _should_apply_schedule_state(row.phase, game.state):
            _apply_state_columns(row, game.state)

        touched += 1
    return touched


async def apply_game_state(session: AsyncSession, state: domain.GameState) -> GameORM | None:
    row = await session.get(GameORM, state.game_id)
    if row is None:
        return None
    _apply_state_columns(row, state)
    return row


def state_from_row(row: GameORM) -> domain.GameState:
    return domain.GameState(
        game_id=row.id,
        phase=domain.GamePhase(row.phase),
        home_score=row.home_score,
        away_score=row.away_score,
        period=row.period,
        period_label=row.period_label,
        clock=row.clock,
        is_intermission=row.is_intermission,
        last_update=(
            ensure_utc(row.state_updated_at) if row.state_updated_at is not None else None
        ),
    )


async def get_game(session: AsyncSession, game_id: str) -> GameORM | None:
    return await session.get(GameORM, game_id)


# ---------------------------------------------------------------------------
# Events (leaderboard competitions, e.g. golf)
# ---------------------------------------------------------------------------


def _leader_row_to_dict(row: domain.LeaderRow) -> dict:
    """Shape matches ``schemas.LeaderRowOut`` exactly."""
    return {
        "position": row.position,
        "position_label": row.position_label,
        "name": row.name,
        "score": row.score,
        "detail": row.detail,
        "player_id": row.player_id,
    }


async def upsert_events(session: AsyncSession, events: Sequence[domain.Event]) -> int:
    """Insert-or-update leaderboard events by id; returns rows touched.

    Like :func:`upsert_games`, an incoming FINAL is authoritative and a
    fresh leaderboard always overwrites the stored one (the provider is
    the source of truth for the board); schedule-only refreshes that
    arrive with an empty leaderboard never wipe a populated one.
    """
    touched = 0
    for event in events:
        row = await session.get(EventORM, event.id)
        board = [_leader_row_to_dict(r) for r in event.leaderboard]
        if row is None:
            row = EventORM(
                id=event.id,
                league_id=event.league_id,
                name=_clip(event.name, 256) or "",
                start_time=ensure_utc(event.start_time),
                end_time=ensure_utc(event.end_time) if event.end_time else None,
                venue=_clip(event.venue, 256),
                phase=event.phase.value,
                round_label=_clip(event.round_label, 48) or "",
                leaderboard=board,
                state_updated_at=utcnow() if board else None,
            )
            session.add(row)
            touched += 1
            continue

        row.league_id = event.league_id
        row.name = _clip(event.name, 256) or ""
        row.start_time = ensure_utc(event.start_time)
        row.end_time = ensure_utc(event.end_time) if event.end_time else None
        row.venue = _clip(event.venue, 256)
        # Don't regress a finished event back to scheduled from a stale
        # schedule-only fetch; otherwise the latest phase wins.
        if not (row.phase == _FINAL_PHASE and event.phase is not domain.GamePhase.FINAL):
            row.phase = event.phase.value
        row.round_label = _clip(event.round_label, 48) or ""
        if board:
            row.leaderboard = board
            row.state_updated_at = utcnow()
        touched += 1
    return touched


async def get_event(session: AsyncSession, event_id: str) -> EventORM | None:
    return await session.get(EventORM, event_id)


async def events_between(
    session: AsyncSession, start_utc: datetime, end_utc: datetime
) -> list[EventORM]:
    """Events overlapping [start_utc, end_utc): start before the window
    ends and (end_time or start_time) at/after the window start."""
    stmt = (
        select(EventORM)
        .where(
            EventORM.start_time < end_utc,
            func.coalesce(EventORM.end_time, EventORM.start_time) >= start_utc,
        )
        .order_by(EventORM.start_time.asc(), EventORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def active_events(
    session: AsyncSession, now_utc: datetime, lookahead: timedelta
) -> list[EventORM]:
    """In-progress events plus those starting within ``lookahead``."""
    stmt = (
        select(EventORM)
        .where(
            or_(
                EventORM.phase == _IN_PROGRESS_PHASE,
                (EventORM.phase == _SCHEDULED_PHASE)
                & (EventORM.start_time <= now_utc + lookahead)
                & (func.coalesce(EventORM.end_time, EventORM.start_time) >= now_utc),
            )
        )
        .order_by(EventORM.start_time.asc(), EventORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def games_between(
    session: AsyncSession,
    start_utc: datetime,
    end_utc: datetime,
    team_id: str | None = None,
) -> list[GameORM]:
    stmt = (
        select(GameORM)
        .where(GameORM.start_time >= start_utc, GameORM.start_time < end_utc)
        .order_by(GameORM.start_time.asc(), GameORM.id.asc())
    )
    if team_id is not None:
        stmt = stmt.where(or_(GameORM.home_team_id == team_id, GameORM.away_team_id == team_id))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upcoming_games_for_leagues(
    session: AsyncSession,
    league_ids: list[str],
    start_utc: datetime,
    end_utc: datetime,
) -> list[GameORM]:
    """Games happening now or starting in ``[start, end)`` for ``league_ids``.

    Drives the map's "upcoming games" mode: every *in-progress* game
    (whenever it kicked off — a live game must show even though it started
    before ``start``) plus *scheduled* games starting within the window.
    Finals / postponed / canceled are excluded.  Drawn from the leagues the
    user follows in whole or in part.  Empty ``league_ids`` short-circuits to
    ``[]`` (an empty ``IN ()`` is a SQL error on some backends).  Ordered
    earliest-first for stable rendering.
    """
    if not league_ids:
        return []
    stmt = (
        select(GameORM)
        .where(
            GameORM.league_id.in_(league_ids),
            GameORM.phase.in_([_SCHEDULED_PHASE, _IN_PROGRESS_PHASE]),
            GameORM.start_time < end_utc,
            or_(
                GameORM.phase == _IN_PROGRESS_PHASE,
                GameORM.start_time >= start_utc,
            ),
        )
        .order_by(GameORM.start_time.asc(), GameORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_league_games(session: AsyncSession, league_id: str) -> list[GameORM]:
    """Every stored game for a league, oldest first.

    Used by the map to place each whole-competition team at the venue of
    its next match (a host-country stadium) rather than its home ground.
    """
    stmt = (
        select(GameORM)
        .where(GameORM.league_id == league_id)
        .order_by(GameORM.start_time.asc(), GameORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def results_for_team(session: AsyncSession, team_id: str, limit: int = 25) -> list[GameORM]:
    stmt = (
        select(GameORM)
        .where(
            GameORM.phase == domain.GamePhase.FINAL.value,
            or_(GameORM.home_team_id == team_id, GameORM.away_team_id == team_id),
        )
        .order_by(GameORM.start_time.desc(), GameORM.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def head_to_head(
    session: AsyncSession,
    league_id: str,
    name_a: str,
    name_b: str,
    limit: int = 5,
) -> list[GameORM]:
    """Recent FINAL meetings between two sides in a league, newest first.

    Matched by display name (so it works for whole-competition sides that
    have no followed-team row, e.g. World Cup nations).
    """
    stmt = (
        select(GameORM)
        .where(
            GameORM.league_id == league_id,
            GameORM.phase == domain.GamePhase.FINAL.value,
            or_(
                and_(GameORM.home_name == name_a, GameORM.away_name == name_b),
                and_(GameORM.home_name == name_b, GameORM.away_name == name_a),
            ),
        )
        .order_by(GameORM.start_time.desc(), GameORM.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def recent_finals_for_name(
    session: AsyncSession, league_id: str, name: str, limit: int = 6
) -> list[GameORM]:
    """A side's recent FINAL games in a league (by display name), newest first."""
    stmt = (
        select(GameORM)
        .where(
            GameORM.league_id == league_id,
            GameORM.phase == domain.GamePhase.FINAL.value,
            or_(GameORM.home_name == name, GameORM.away_name == name),
        )
        .order_by(GameORM.start_time.desc(), GameORM.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def most_common_home_venue(session: AsyncSession, team_id: str) -> str | None:
    """The venue a team hosts at most often, from stored home games.

    A geocode fallback for the location job: when a provider yields no
    venue name, the team's own home fixtures usually carry one.  Only
    home games count (the away venue belongs to the opponent), non-null
    venues are tallied, and the most frequent wins (id as a stable
    tiebreaker).  ``None`` when no home game has a venue.
    """
    stmt = (
        select(GameORM.venue, func.count().label("n"))
        .where(
            GameORM.home_team_id == team_id,
            GameORM.venue.is_not(None),
        )
        .group_by(GameORM.venue)
        .order_by(func.count().desc(), GameORM.venue.asc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    return row[0] if row is not None else None


async def league_has_games_in_window(
    session: AsyncSession,
    league_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> bool:
    """Whether a league has at least one stored game in ``[start, end)``.

    The "is this competition still running" test for the map view: a
    ``follow_all`` league is ACTIVE while it has a near-window fixture in
    the DB (default window now-2d .. now+21d at the call site).  When a
    tournament ends, daily-refresh stops returning near games for it, the
    window empties, and the league's whole-field pins drop off the map
    automatically — "disappears after the tournament".  A single cheap
    ``EXISTS``-style query (LIMIT 1) over the same index ``games_between``
    uses.
    """
    stmt = (
        select(GameORM.id)
        .where(
            GameORM.league_id == league_id,
            GameORM.start_time >= start_utc,
            GameORM.start_time < end_utc,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.first() is not None


async def games_needing_live_poll(
    session: AsyncSession,
    now_utc: datetime,
    lead: timedelta,
    max_age: timedelta = timedelta(hours=8),
) -> list[GameORM]:
    """Rows worth polling fast: live games plus imminent scheduled ones.

    - in_progress games whose start_time is newer than ``now - max_age``
      (stale 'in_progress' rows from a dead feed eventually drop out)
    - scheduled games with start_time in ``[now - max_age, now + lead]``
    """
    oldest = now_utc - max_age
    horizon = now_utc + lead
    stmt = (
        select(GameORM)
        .where(
            or_(
                (GameORM.phase == domain.GamePhase.IN_PROGRESS.value)
                & (GameORM.start_time > oldest),
                (GameORM.phase == _SCHEDULED_PHASE)
                & (GameORM.start_time >= oldest)
                & (GameORM.start_time <= horizon),
            )
        )
        .order_by(GameORM.start_time.asc(), GameORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def finals_missing_notification(
    session: AsyncSession,
    now_utc: datetime,
    lookback: timedelta,
) -> list[GameORM]:
    """Recently-FINAL games whose FINAL notification was never recorded.

    A game can be stored FINAL yet have no ``"{id}:final"`` row in
    :class:`NotificationSentORM` when its first send failed: ``live_tick``
    commits the FINAL state but only marks notified on a confirmed send, and
    the game then drops out of :func:`games_needing_live_poll`, so this is the
    only path that retries it.  Bounded to games whose ``start_time`` is in
    ``[now - lookback, now]`` (a clocked game's start is close to its finish)
    so an old/backfilled final never floods the user, and filtered by a
    correlated NOT EXISTS against the dedupe ledger so an already-sent final
    is excluded.
    """
    oldest = now_utc - lookback
    not_sent = ~(
        select(NotificationSentORM.dedupe_key)
        .where(NotificationSentORM.dedupe_key == GameORM.id + ":final")
        .exists()
    )
    stmt = (
        select(GameORM)
        .where(
            GameORM.phase == _FINAL_PHASE,
            GameORM.start_time >= oldest,
            GameORM.start_time <= now_utc,
            not_sent,
        )
        .order_by(GameORM.start_time.asc(), GameORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def event_finals_missing_notification(
    session: AsyncSession,
    now_utc: datetime,
    lookback: timedelta,
) -> list[EventORM]:
    """Recently-active FINAL leaderboard events whose FINAL notification is unrecorded.

    The :class:`EventORM` counterpart to :func:`finals_missing_notification`.
    Recency is measured on ``state_updated_at`` (set whenever the live tick
    writes a non-empty board) rather than ``end_time``: a tournament's
    ``end_time`` is its *scheduled* final-round end — often at/after ``now``
    when it first finalizes, or absent entirely — so it is the wrong "recently
    active" signal for a multi-day event.
    """
    oldest = now_utc - lookback
    not_sent = ~(
        select(NotificationSentORM.dedupe_key)
        .where(NotificationSentORM.dedupe_key == EventORM.id + ":final")
        .exists()
    )
    stmt = (
        select(EventORM)
        .where(
            EventORM.phase == _FINAL_PHASE,
            EventORM.state_updated_at.is_not(None),
            EventORM.state_updated_at >= oldest,
            not_sent,
        )
        .order_by(EventORM.start_time.asc(), EventORM.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def prune_stale_games(session: AsyncSession, now_utc: datetime) -> int:
    """Drop ghost rows no provider will ever update again.

    A 'scheduled' game whose start passed days ago, or an 'in_progress'
    game from days ago, would have been finalized (or reinstated) by the
    daily schedule refresh if any provider still knew about it — see
    :func:`_should_apply_schedule_state`.  What remains is detritus from
    vanished fixtures; without pruning it lingers in the calendar and
    today views forever.  Returns the number of rows deleted.
    """
    stmt = delete(GameORM).where(
        or_(
            (GameORM.phase == _SCHEDULED_PHASE)
            & (GameORM.start_time < now_utc - timedelta(days=3)),
            (GameORM.phase == _IN_PROGRESS_PHASE)
            & (GameORM.start_time < now_utc - timedelta(days=2)),
        )
    )
    result = await session.execute(stmt)
    deleted = result.rowcount or 0
    if deleted:
        logger.info("prune_stale_games: removed %d ghost game row(s)", deleted)
    return deleted


# ---------------------------------------------------------------------------
# Standings / rosters
# ---------------------------------------------------------------------------


def _standing_row_to_dict(row: domain.StandingRow) -> dict:
    """Shape matches ``schemas.StandingRowOut`` exactly."""
    return {
        "rank": row.rank,
        "team_name": row.team_name,
        "team_id": row.team_id,
        "logo_url": row.logo_url,
        "abbreviation": row.abbreviation,
        "color": row.color,
        "wins": row.wins,
        "losses": row.losses,
        "draws": row.draws,
        "points": row.points,
        "goal_diff": row.goal_diff,
        "win_pct": row.win_pct,
        "games_back": row.games_back,
        "ot_losses": row.ot_losses,
        "group": row.group,
        "subgroup": row.subgroup,
    }


async def save_standings(session: AsyncSession, standings: domain.Standings) -> None:
    # Providers tag rows with internal team ids from an in-process map
    # that can outlive a ``replace_followed`` (e.g. the user re-ran the
    # setup wizard); only keep tags that reference a team we still
    # follow so stored rows never point at deleted teams.  (Queried
    # before the StandingsORM row is added — autoflush would otherwise
    # flush a half-built row.)
    known_ids = set((await session.execute(select(TeamORM.id))).scalars().all())
    row = await session.get(StandingsORM, standings.league_id)
    if row is None:
        row = StandingsORM(league_id=standings.league_id)
        session.add(row)
    row.season = standings.season
    row.rows = [
        _standing_row_to_dict(
            r if (r.team_id is None or r.team_id in known_ids) else replace(r, team_id=None)
        )
        for r in standings.rows
    ]
    row.fetched_at = ensure_utc(standings.fetched_at)


async def get_standings(session: AsyncSession, league_id: str) -> StandingsORM | None:
    return await session.get(StandingsORM, league_id)


_SEASON_LABEL_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")


def season_key_from_label(label: str) -> str | None:
    """Numeric season key from a provider label; None when unparseable.

    Cross-year seasons key on the ENDING year ("2025-26" -> "2026") so a
    season has one key whichever way a provider spells it.
    """
    match = _SEASON_LABEL_RE.match((label or "").strip())
    if match is None:
        return None
    start, end2 = match.groups()
    if end2 is None:
        return start
    return start[:2] + end2


async def save_standings_archive(
    session: AsyncSession,
    standings: domain.Standings,
    *,
    season_key: str | None = None,
) -> StandingsArchiveORM | None:
    """Upsert one season's table into the archive; None if the key is unknowable.

    Archive rows deliberately drop the followed-team ``team_id`` tags —
    they reference the CURRENT follow set, which is meaningless for a
    past season (and would dangle after a re-follow).
    """
    key = season_key or season_key_from_label(standings.season)
    if key is None:
        return None
    row = await session.get(StandingsArchiveORM, (standings.league_id, key))
    if row is None:
        row = StandingsArchiveORM(league_id=standings.league_id, season=key)
        session.add(row)
    row.season_label = _clip(standings.season, 32) or key
    row.rows = [_standing_row_to_dict(replace(r, team_id=None)) for r in standings.rows]
    row.fetched_at = ensure_utc(standings.fetched_at)
    return row


async def get_standings_archive(
    session: AsyncSession, league_id: str, season_key: str
) -> StandingsArchiveORM | None:
    return await session.get(StandingsArchiveORM, (league_id, season_key))


async def replace_roster(session: AsyncSession, roster: domain.Roster) -> None:
    await session.execute(delete(PlayerORM).where(PlayerORM.team_id == roster.team_id))
    for player in roster.players:
        session.add(
            PlayerORM(
                team_id=roster.team_id,
                id=_clip(player.id, 64) or "",
                name=_clip(player.name, 128) or "",
                position=_clip(player.position, 32),
                jersey_number=_clip(player.jersey_number, 8),
                status=player.status.value,
                status_detail=_clip(player.status_detail, 256),
                stat_line=_clip(player.stat_line, 256),
                career_stat_line=_clip(player.career_stat_line, 256),
                photo_url=player.photo_url,
            )
        )
    team = await session.get(TeamORM, roster.team_id)
    if team is not None:
        team.roster_updated_at = ensure_utc(roster.fetched_at)
    else:
        logger.warning(
            "replace_roster: unknown team %s; roster stored without timestamp",
            roster.team_id,
        )


async def get_roster(session: AsyncSession, team_id: str) -> list[PlayerORM]:
    stmt = select(PlayerORM).where(PlayerORM.team_id == team_id).order_by(PlayerORM.name.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


async def upsert_news(session: AsyncSession, items: Sequence[domain.NewsItem]) -> int:
    """Insert news items that are not yet stored; returns new-item count."""
    inserted = 0
    seen_this_batch: set[str] = set()
    for item in items:
        if item.id in seen_this_batch:
            continue
        seen_this_batch.add(item.id)
        existing = await session.get(NewsORM, item.id)
        if existing is not None:
            continue
        session.add(
            NewsORM(
                id=item.id,
                team_id=item.team_id,
                league_id=item.league_id,
                title=item.title,
                url=item.url,
                source=_clip(item.source, 128) or "",
                published_at=(
                    ensure_utc(item.published_at) if item.published_at is not None else None
                ),
                summary=item.summary,
                image_url=item.image_url,
                fetched_at=utcnow(),
            )
        )
        # Make the new row visible to session.get() within this batch
        # without committing (callers own the transaction).
        await session.flush()
        inserted += 1
    return inserted


async def list_news(
    session: AsyncSession,
    team_id: str | None = None,
    league_id: str | None = None,
    limit: int = 50,
) -> list[NewsORM]:
    # Portable NULLs-last ordering: an IS NULL expression sorts False(0)
    # before True(1) on both sqlite and postgres.
    stmt = (
        select(NewsORM)
        .order_by(
            NewsORM.published_at.is_(None).asc(),
            NewsORM.published_at.desc(),
            NewsORM.fetched_at.desc(),
            NewsORM.id.asc(),
        )
        .limit(limit)
    )
    # team_id scopes to one followed team; league_id to one whole-competition
    # follow. With neither, return everything stored — which is already the
    # current followed set, since replace_followed() wipes news on re-follow.
    if team_id is not None:
        stmt = stmt.where(NewsORM.team_id == team_id)
    if league_id is not None:
        stmt = stmt.where(NewsORM.league_id == league_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# App meta / setup
# ---------------------------------------------------------------------------


async def get_meta(session: AsyncSession, key: str) -> str | None:
    row = await session.get(AppMetaORM, key)
    return row.value if row is not None else None


async def set_meta(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppMetaORM, key)
    if row is None:
        session.add(AppMetaORM(key=key, value=value))
    else:
        row.value = value


async def replace_followed(
    session: AsyncSession,
    leagues: list[domain.League],
    teams: list[domain.Team],
    competitions: list[tuple[str, str, str]] | None = None,
) -> None:
    """Replace the followed set wholesale (setup wizard / demo install).

    All cached sports data is disposable — it derives entirely from
    provider fetches keyed off the followed set — so it is wiped along
    with the old teams/leagues (children before parents, for FK
    integrity on postgres) and re-fetched by the next refresh.

    ``competitions`` are ``(team_id, league_id, provider_key)`` triples:
    extra leagues whose fixtures a (national) team should also be pulled
    from.  Notification prefs are wiped too — they key off scopes that
    no longer exist after a re-follow.
    """
    for table in (
        NotificationSentORM,
        NotificationPrefORM,
        TeamCompetitionORM,
        NewsORM,
        PlayerORM,
        GameORM,
        EventORM,
        StandingsORM,
        TeamORM,
        LeagueORM,
    ):
        await session.execute(delete(table))
    for league in leagues:
        await upsert_league(session, league)
    # Parents must reach the database before children reference them:
    # without ORM relationships SQLAlchemy gives no cross-table INSERT
    # ordering inside one flush, and postgres (unlike sqlite's default)
    # enforces the FKs — the delete loop above already goes children-first
    # for the same reason.
    await session.flush()
    for team in teams:
        await upsert_team(session, team)
    await session.flush()
    for team_id, league_id, provider_key in competitions or ():
        session.add(
            TeamCompetitionORM(team_id=team_id, league_id=league_id, provider_key=provider_key)
        )


async def list_team_competitions(
    session: AsyncSession, team_id: str | None = None
) -> list[TeamCompetitionORM]:
    stmt = select(TeamCompetitionORM)
    if team_id is not None:
        stmt = stmt.where(TeamCompetitionORM.team_id == team_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_follow_all_leagues(session: AsyncSession) -> list[LeagueORM]:
    """Leagues followed in whole-competition mode."""
    result = await session.execute(
        select(LeagueORM).where(LeagueORM.follow_all.is_(True)).order_by(LeagueORM.id)
    )
    return list(result.scalars().all())


async def map_relevant_league_ids(session: AsyncSession) -> list[str]:
    """League ids the map's "upcoming games" mode should draw games from.

    A league qualifies when it's followed in whole (``follow_all``) OR has
    at least one followed team — i.e. the user follows games there in whole
    or in part.  Sorted, de-duplicated.
    """
    follow_all = await session.execute(select(LeagueORM.id).where(LeagueORM.follow_all.is_(True)))
    with_teams = await session.execute(select(TeamORM.league_id).distinct())
    ids = set(follow_all.scalars().all()) | set(with_teams.scalars().all())
    return sorted(ids)


# ---------------------------------------------------------------------------
# Notification dedupe ledger
# ---------------------------------------------------------------------------


async def was_notified(session: AsyncSession, dedupe_key: str) -> bool:
    return await session.get(NotificationSentORM, dedupe_key) is not None


async def mark_notified(session: AsyncSession, dedupe_key: str) -> None:
    if await session.get(NotificationSentORM, dedupe_key) is None:
        session.add(NotificationSentORM(dedupe_key=dedupe_key, sent_at=utcnow()))


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


async def get_notification_prefs(session: AsyncSession) -> list[NotificationPrefORM]:
    """Every stored per-scope preference row, ordered by scope."""
    result = await session.execute(select(NotificationPrefORM).order_by(NotificationPrefORM.scope))
    return list(result.scalars().all())


async def upsert_notification_pref(
    session: AsyncSession,
    scope: str,
    muted: bool | None = None,
    events: dict[str, bool] | None = None,
) -> NotificationPrefORM:
    """Insert-or-merge one scope's preferences; returns the row.

    Only fields that are not ``None`` are applied: ``muted`` overwrites
    the stored flag when given, and ``events`` is *merged* into the stored
    map (per-type keys it omits are preserved).  A scope with no row yet
    starts from the disabled-mute / empty-events defaults before merging.
    """
    row = await session.get(NotificationPrefORM, scope)
    if row is None:
        row = NotificationPrefORM(scope=scope, muted=False, events={})
        session.add(row)
    if muted is not None:
        row.muted = muted
    if events is not None:
        # Reassign a new dict so SQLAlchemy reliably detects the change to
        # the JSON column (in-place mutation of a JSON value is not tracked).
        row.events = {**(row.events or {}), **events}
    return row


async def prefs_by_scope(session: AsyncSession) -> dict[str, NotificationPrefORM]:
    """All preferences keyed by scope, loaded in one query.

    A single cheap read so a live tick can resolve every game's
    notification policy without re-querying per event.
    """
    return {row.scope: row for row in await get_notification_prefs(session)}
