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
from typing import Any, Sequence

from sqlalchemy import Select, and_, delete, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.models import domain
from app.models.orm import (
    AppMetaORM,
    EventORM,
    GameORM,
    LeagueORM,
    NewsORM,
    NotificationPrefORM,
    NotificationSentORM,
    PlayerFollowORM,
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


async def clear_unverified_descriptions(session: AsyncSession) -> int:
    """Drop every club description not attributed to TheSportsDB; returns the count.

    That is the Wikipedia ones, and the ones stored before the source was
    recorded — either upstream may have supplied those.  Only the club prose
    and its attribution go: the founding year and the stadium prose come
    from TheSportsDB alone.  A cleared team has no description, which is
    what puts it back in the team-info job's pending set.
    """
    result = await session.execute(
        update(TeamORM)
        .where(
            TeamORM.description.is_not(None),
            or_(
                TeamORM.description_source.is_(None),
                TeamORM.description_source != "thesportsdb",
            ),
        )
        .values(description=None, description_source=None)
    )
    return result.rowcount or 0


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


def stadium_key(league: domain.League | LeagueORM, team_provider_key: str) -> str:
    """The stadium-cache key for a team: ``"{provider}:{scope}:{team_provider_key}"``.

    ``scope`` names the id space the provider numbers that team in, because
    a bare provider id is not an identity:

    - TheSportsDB: ``"all"`` — the provider looks a team up by ``idTeam``
      alone, no league involved, so the whole provider is one space.
    - ESPN soccer: ``"soccer"`` — a club or nation keeps its id in every
      competition (France is 478 in each of them), so a side in two followed
      competitions must be one row and one identity.
    - anything else: the league's own id — the NFL, NHL, NBA, WNBA and MLB
      each number their teams from 1, and the athlete ids of an
      individual-sport league followed in whole are kept apart the same way.

    The stadium cache and the map's followed-team dedupe both build their
    key here, so "the same team" means the same thing in both.  Accepts the
    domain league or its ORM row: ``Sport`` is a ``str`` enum, so the soccer
    test reads the same on either.
    """
    if league.provider == "thesportsdb":
        scope = "all"
    elif league.provider == "espn" and league.sport == domain.Sport.SOCCER:
        scope = "soccer"
    else:
        scope = league.id
    return f"{league.provider}:{scope}:{team_provider_key}"


async def get_stadium(session: AsyncSession, key: str) -> StadiumORM | None:
    """Fetch a cached stadium by its exact key (see :func:`stadium_key`).

    The cache is decoupled from ``TeamORM`` so a whole-competition follow
    (e.g. every World Cup nation) can plot teams that have no followed-team
    row of their own.  Returns the row whether it resolved to coordinates
    or was cached as a definitive miss (``resolved=True, lat/lon None``) —
    the caller checks ``lat``/``lon``.
    """
    return await session.get(StadiumORM, key)


async def find_team_stadium(
    session: AsyncSession,
    league: domain.League | LeagueORM,
    team_provider_key: str,
    team_name: str,
    *,
    adopt: bool = False,
) -> StadiumORM | None:
    """A competition team's cached stadium, wherever an older build left it.

    Rows used to be keyed ``"{provider}:{team_provider_key}"`` with no id
    space, so two teams sharing a provider id in different leagues shared one
    row — whichever was resolved first — and the other was plotted at its
    ground.  This is the only code that still knows that spelling.

    The row under :func:`stadium_key` always wins.  Without one, a legacy row
    is believed only when its ``team_name`` is this team's catalog name
    (trimmed, case-insensitive, never empty): one upsert wrote the name and
    the facts together from the name the lookup used, so a matching name
    proves the facts are this team's.  A row that fails the proof belongs to
    another league's team, or to this one before a rename, and is ignored —
    the team then resolves fresh.

    ``adopt`` is for the single-flight stadium sweep, the cache's only
    writer: it COPIES a proven legacy row to the scoped key and returns the
    copy.  The legacy row is deliberately left in place — an older build run
    against the same database (a rollback, an older desktop app) still finds
    every row it wrote, where a re-keyed table would look empty to it and
    send the whole field back through the rate-limited lookups.  Adopting
    twice copies nothing: the scoped row is found before the legacy key is
    consulted.  Without ``adopt`` (the map route, which never writes) the
    proven legacy row itself is returned, read-only, so pins survive the
    gap between an upgrade and the first sweep.
    """
    key = stadium_key(league, team_provider_key)
    row = await session.get(StadiumORM, key)
    if row is not None:
        return row

    legacy_key = f"{league.provider}:{team_provider_key}"
    legacy = await session.get(StadiumORM, legacy_key)
    if legacy is None:
        return None
    # Compared as stored: upsert_stadium clips the name to the column width.
    wanted = (_clip(team_name, 128) or "").strip().casefold()
    if not wanted or (legacy.team_name or "").strip().casefold() != wanted:
        # How a cross-league id collision or a renamed team shows up in the
        # log.  INFO only for the sweep, which writes the team's own row
        # straight afterwards and so reports each row once; the map route
        # would repeat the line on every poll until then.
        logger.log(
            logging.INFO if adopt else logging.DEBUG,
            "find_team_stadium: legacy row %s holds %r, not %r — ignored for %s",
            legacy_key,
            legacy.team_name,
            team_name,
            key,
        )
        return None
    if not adopt:
        return legacy

    row = StadiumORM(
        key=key,
        team_name=legacy.team_name,
        venue=legacy.venue,
        lat=legacy.lat,
        lon=legacy.lon,
        capacity=legacy.capacity,
        opened=legacy.opened,
        image_url=legacy.image_url,
        location=legacy.location,
        surface=legacy.surface,
        # The copy is the same lookup, not a new one: a cached miss keeps its
        # age, so the sweep's retry cool-down runs from when it really missed.
        resolved=legacy.resolved,
        fetched_at=legacy.fetched_at,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "find_team_stadium: adopted legacy row %s as %s (%s)", legacy_key, key, legacy.team_name
    )
    return row


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

    Written by the competition-stadium refresh (its only caller; the map
    route reads the cache and never writes it) once a team's venue is
    resolved — or definitively missed — so the map view reads coordinates
    straight from the cache without re-running the multi-source
    enrichment/geocode pipeline on every poll.
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
                fight_method=(
                    game.fight_result.method.value if game.fight_result is not None else None
                ),
                fight_detail=(
                    _clip(game.fight_result.detail, 64) if game.fight_result is not None else None
                ),
                fight_round=game.fight_result.round if game.fight_result is not None else None,
                fight_clock=(
                    _clip(game.fight_result.clock, 16) if game.fight_result is not None else None
                ),
                broadcasts=list(game.broadcasts),
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
        # Broadcasts follow the crest/color rule: refresh with the schedule,
        # but an empty incoming list (a pass that didn't carry them) never
        # wipes a stored one.  Always REASSIGN a fresh list — SQLAlchemy
        # does not change-track in-place mutation of a JSON column.
        if game.broadcasts:
            row.broadcasts = list(game.broadcasts)

        # A method of victory only ever arrives with the final result, and a
        # later refresh may not carry it — never let an incoming None wipe a
        # stored one.  The merge is PER FIELD, not per block: only the method
        # is guaranteed to be there, and the round/time/flavour come from a
        # separate (core-API) call that can fail on its own, so a thinner
        # incoming result must not erase a richer stored one.  Same overlay
        # rule the provider applies in ``_merge_fight_results``.
        if game.fight_result is not None:
            row.fight_method = game.fight_result.method.value
            if game.fight_result.detail is not None:
                row.fight_detail = _clip(game.fight_result.detail, 64)
            if game.fight_result.round is not None:
                row.fight_round = game.fight_result.round
            if game.fight_result.clock is not None:
                row.fight_clock = _clip(game.fight_result.clock, 16)

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


async def most_common_home_venue_by_name(
    session: AsyncSession, league_id: str, team_name: str
) -> str | None:
    """The venue a *named* side hosts at most often, from stored home games.

    The same tally as :func:`most_common_home_venue`, keyed by name within
    one league instead of by ``team_id``.  A whole-competition team (a
    national side followed via ``follow_all``) has no ``teams`` row, so the
    id-keyed query can never find it — yet its fixtures are stored like any
    other and carry the ground it plays on.  Without this, a competition
    team whose venue the provider does not know had no fallback at all and
    simply left the map.

    Names are compared through SQL's own ``lower()`` on both sides so the
    two agree whatever the backend does with non-ASCII (SQLite's is
    ASCII-only, Postgres' is not) — matching a name to itself must not
    depend on that.  ``None`` when no home game under that name has a venue.
    """
    stmt = (
        select(GameORM.venue, func.count().label("n"))
        .where(
            GameORM.league_id == league_id,
            func.lower(GameORM.home_name) == func.lower(team_name),
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


async def team_ids_with_games_starting_within(
    session: AsyncSession,
    now_utc: datetime,
    lookahead: timedelta,
) -> set[str]:
    """Team ids with a SCHEDULED game starting in ``[now, now + lookahead)``.

    The scope query for the same-day pre-game roster re-sync: both sides of
    every imminent fixture, with the null side (a competition fixture whose
    team isn't followed) dropped.  Deliberately returns plain ids rather than
    rows — the caller fetches providers between DB scopes, and a detached
    ``GameORM`` would lazy-load from a sync context the moment it was touched
    afterwards (MissingGreenlet).  Unlike
    :func:`games_needing_live_poll` nothing already started is included: a
    roster fetched after kickoff is too late to be worth a call.
    """
    horizon = now_utc + lookahead
    stmt = select(GameORM.home_team_id, GameORM.away_team_id).where(
        GameORM.phase == _SCHEDULED_PHASE,
        GameORM.start_time >= now_utc,
        GameORM.start_time < horizon,
    )
    result = await session.execute(stmt)
    return {team_id for row in result for team_id in row if team_id is not None}


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
    """Drop ghost game/event rows no provider will ever update again.

    A 'scheduled' game whose start passed days ago, or an 'in_progress'
    game from days ago, would have been finalized (or reinstated) by the
    daily schedule refresh if any provider still knew about it — see
    :func:`_should_apply_schedule_state`.  What remains is detritus from
    vanished fixtures; without pruning it lingers in the calendar and
    today views forever.  Leaderboard events get the mirror rule: an
    'in_progress' event whose weekend has left both the dateless current
    scoreboard and the daily fetch window can never be finalized again,
    so ``events_tick`` would otherwise poll it forever.  Returns the
    number of rows deleted.
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
    event_stmt = delete(EventORM).where(
        (EventORM.phase == _IN_PROGRESS_PHASE)
        & (func.coalesce(EventORM.end_time, EventORM.start_time) < now_utc - timedelta(days=2))
    )
    event_result = await session.execute(event_stmt)
    stale_events = event_result.rowcount or 0
    if stale_events:
        logger.info("prune_stale_games: removed %d ghost event row(s)", stale_events)
    return deleted + stale_events


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
    # that can outlive a ``replace_followed`` (e.g. the user unfollowed
    # a team in the setup wizard); only keep tags that reference a team
    # we still follow so stored rows never point at deleted teams.
    # (Queried before the StandingsORM row is added — autoflush would
    # otherwise flush a half-built row.)
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

    Archive rows deliberately drop the followed-team ``team_id`` tags:
    they mark rows belonging to the CURRENT follow set, which says nothing
    about a past season's table and would render it with today's
    highlighting.  The stored season stands on its own, by name.  (So an
    edit of the followed set has nothing to go stale here either —
    :func:`replace_followed` leaves a kept league's archive untouched and
    drops only that of a league no longer followed.)
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
# Player follows
# ---------------------------------------------------------------------------


async def list_player_follows(session: AsyncSession) -> list[PlayerFollowORM]:
    """Every followed player, ordered by name (athlete id as tiebreaker)."""
    stmt = select(PlayerFollowORM).order_by(
        PlayerFollowORM.name.asc(), PlayerFollowORM.athlete_id.asc()
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def follow_player(
    session: AsyncSession,
    *,
    athlete_id: str,
    name: str,
    team_id: str,
    league_id: str,
    position: str | None = None,
    photo_url: str | None = None,
    last_notified_status: str | None = None,
) -> PlayerFollowORM:
    """Insert-or-update one player follow (SELECT-then-upsert); returns the row.

    Re-following an already-followed athlete refreshes the display fields
    (name/team/position/photo) but keeps the original ``followed_at``.
    ``last_notified_status`` (re)seeds the roster-diff alert baseline —
    the route passes the roster row's current status so a follow never
    alerts about a designation the user could already see; ``None``
    leaves seeding to the next roster sync (a silent first sighting).
    """
    row = await session.get(PlayerFollowORM, athlete_id)
    if row is None:
        row = PlayerFollowORM(athlete_id=athlete_id, followed_at=utcnow())
        session.add(row)
    row.name = _clip(name, 128) or ""
    row.team_id = _clip(team_id, 64) or ""
    row.league_id = _clip(league_id, 64) or ""
    row.position = _clip(position, 32)
    row.photo_url = _clip(photo_url, 512)
    row.last_notified_status = _clip(last_notified_status, 24)
    return row


async def unfollow_player(session: AsyncSession, athlete_id: str) -> bool:
    """Delete one player follow; False when the athlete wasn't followed."""
    row = await session.get(PlayerFollowORM, athlete_id)
    if row is None:
        return False
    await session.delete(row)
    return True


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
    # current followed set, since replace_followed() deletes the news of
    # every team and league it unfollows.
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


# The ids a follow set consists of, as the rules below take them: the ids
# themselves, or a query selecting them, for a caller that needs each
# statement to judge by the set as stored when that statement runs (see
# :func:`prune_unbacked`).
_Ids = set[str] | Select[tuple[str]]


def _outside(column: InstrumentedAttribute[Any], ids: _Ids) -> ColumnElement[bool]:
    """Rows whose ``column`` names an id that is not in ``ids``.

    NULL names nothing, so it is never "outside".  A bare ``NOT IN``
    would not say that reliably: against a non-empty list a NULL compares
    as unknown and the row is skipped, but against an empty one it
    matches — and an untagged game or a league-level article must not
    vanish just because no team happens to be followed.  The empty case
    is spelled out instead of left to each dialect's empty-``IN`` SQL.

    ``ids`` given as a query becomes ``NOT IN (SELECT …)`` and keeps the
    same guard, for the same reason: a subquery that returns no row is
    the empty list again.  (The queries passed select primary keys, so
    the opposite trap — a NULL *inside* the list — cannot arise.)
    """
    if isinstance(ids, Select):
        return column.is_not(None) & column.not_in(ids)
    if not ids:
        return column.is_not(None)
    return column.is_not(None) & column.not_in(sorted(ids))


def _prefixed(prefix: str, ids: _Ids) -> _Ids:
    """``ids`` as notification-pref scopes: each behind ``prefix`` (``"team:"``)."""
    if isinstance(ids, Select):
        (column,) = ids.selected_columns
        return ids.with_only_columns(literal(prefix) + column)
    return {f"{prefix}{value}" for value in ids}


def _without_stale_tags(entries: Any, key: str, team_ids: set[str]) -> list | None:
    """``entries`` with ``key`` cleared wherever it names a team outside ``team_ids``.

    For the follow tags inside a JSON column (a standings row's
    ``team_id``, a leaderboard row's ``player_id``).  Returns ``None``
    when nothing is stale so the caller leaves the row alone; otherwise a
    NEW list — SQLAlchemy does not change-track in-place mutation of a
    JSON column, so editing the stored one would silently never be
    written.
    """
    if not isinstance(entries, list):
        return None
    cleared = [
        {**entry, key: None}
        if isinstance(entry, dict) and entry.get(key) is not None and entry[key] not in team_ids
        else entry
        for entry in entries
    ]
    return cleared if cleared != entries else None


async def _drop_unbacked_children(
    session: AsyncSession,
    team_ids: _Ids,
    league_ids: _Ids,
    follow_all_ids: _Ids,
) -> None:
    """Delete or untag every child row the given follow set does not back.

    The single statement of what a follow set entitles the database to
    hold: a team-keyed row needs its team, a league-keyed row its league,
    and a game its league plus either a followed side or a
    whole-competition (``follow_all``) league.  Shared by
    :func:`replace_followed` (the ids about to be the set, as sets) and
    :func:`prune_unbacked` (the ids that are the set, as queries on the
    tables that hold it), so a save and the later sweep can never
    disagree about what is a leftover.

    Rows are kept wherever the set still backs them: a game between a
    kept and a removed team stays with the removed side nulled, and a
    kept league's table or leaderboard stays with the removed team's
    highlight tag cleared.  ``teams``, ``leagues`` and
    ``team_competitions`` are the set itself and are left to the caller;
    ``notifications_sent``, ``stadiums`` and ``app_meta`` are not keyed
    by a follow and are never touched.
    """
    await session.execute(delete(PlayerORM).where(_outside(PlayerORM.team_id, team_ids)))

    await session.execute(delete(NewsORM).where(_outside(NewsORM.team_id, team_ids)))
    await session.execute(delete(NewsORM).where(_outside(NewsORM.league_id, league_ids)))
    # League-level articles exist only for a whole-competition follow.
    await session.execute(
        delete(NewsORM).where(
            NewsORM.team_id.is_(None), _outside(NewsORM.league_id, follow_all_ids)
        )
    )

    # Sides first: only once a removed team's id is nulled can the last
    # delete tell a game nobody followed any more (both sides NULL, league
    # not followed in whole) from one a kept team still plays in.
    await session.execute(
        update(GameORM).where(_outside(GameORM.home_team_id, team_ids)).values(home_team_id=None)
    )
    await session.execute(
        update(GameORM).where(_outside(GameORM.away_team_id, team_ids)).values(away_team_id=None)
    )
    await session.execute(delete(GameORM).where(_outside(GameORM.league_id, league_ids)))
    await session.execute(
        delete(GameORM).where(
            GameORM.home_team_id.is_(None),
            GameORM.away_team_id.is_(None),
            _outside(GameORM.league_id, follow_all_ids),
        )
    )

    await session.execute(delete(EventORM).where(_outside(EventORM.league_id, league_ids)))
    await session.execute(delete(StandingsORM).where(_outside(StandingsORM.league_id, league_ids)))
    await session.execute(
        delete(StandingsArchiveORM).where(_outside(StandingsArchiveORM.league_id, league_ids))
    )

    # The follow tags inside the JSON columns cannot be judged in SQL: the
    # rows are read, edited here and written back.  So when the team ids
    # are a query they are read only now, AFTER the rows — a tag on a row
    # then always names a team that existed when the row was read, and one
    # still followed is still in ids read later.  Read the other way
    # round, a row written for a team followed in between would lose its
    # tag.
    #
    # ``state_updated_at`` is deliberately not re-stamped with the board:
    # it is the recency signal of :func:`event_finals_missing_notification`,
    # and clearing a tag is not news about the event.
    events = (await session.execute(select(EventORM))).scalars().all()
    tables = (await session.execute(select(StandingsORM))).scalars().all()
    tagged_ids = (
        set((await session.execute(team_ids)).scalars().all())
        if isinstance(team_ids, Select)
        else team_ids
    )
    for event in events:
        board = _without_stale_tags(event.leaderboard, "player_id", tagged_ids)
        if board is not None:
            event.leaderboard = board
    for standings in tables:
        rows = _without_stale_tags(standings.rows, "team_id", tagged_ids)
        if rows is not None:
            standings.rows = rows

    # No FKs on these two, so nothing but this keeps them in step with the
    # set: a follow of a removed team's player, and pref rows for scopes
    # the editor can no longer show (which would silently reapply if the
    # same id were ever followed again).  Every scope goes except
    # ``global``, ``team:{id}`` of a followed team and ``league:{id}`` of
    # a stored league.
    await session.execute(
        delete(PlayerFollowORM).where(_outside(PlayerFollowORM.team_id, team_ids))
    )
    await session.execute(
        delete(NotificationPrefORM).where(
            NotificationPrefORM.scope != "global",
            _outside(NotificationPrefORM.scope, _prefixed("team:", team_ids)),
            _outside(NotificationPrefORM.scope, _prefixed("league:", league_ids)),
        )
    )


async def _hand_over_game_tags(
    session: AsyncSession,
    teams: list[domain.Team],
    competitions: Sequence[tuple[str, str, str]],
) -> None:
    """Pass a removed team's game tags to a kept team that is the same team.

    One real team can be followed twice: a nation picked under two
    national competitions is two ``teams`` rows, each holding the other's
    league as a sibling competition, and both fetch the same fixtures.
    ``upsert_games`` lets a later non-NULL side id replace an earlier
    one, so every game they share carries ONE of the two ids — whichever
    was fetched last.  Unfollow that pick and nulling its side like any
    removed team's leaves those games looking like nobody's: they were
    deleted with the kept pick still playing in them, and for good, since
    a refresh only reaches back a week.

    So, before any side is nulled, the tag moves to a kept team that is
    fetched in the game's league under the same provider key — the one
    test that makes it the same team in that game.  The key alone is not
    an identity (ESPN reuses ids across sports), and a kept team that is
    not fetched in that league never tags a game there.  A removed team
    with no such heir is untagged as before.

    Must run after the parents are upserted (the heir may be a team this
    very save adds, and the side columns are foreign keys) and before
    ``team_competitions`` is replaced: the removed team's stored rows are
    what say which leagues it was fetched in.
    """
    removed = (
        await session.execute(
            select(TeamORM.id, TeamORM.league_id, TeamORM.provider_key).where(
                _outside(TeamORM.id, {team.id for team in teams})
            )
        )
    ).all()
    if not removed:
        return

    # What each kept team is fetched as: its own league, then its sibling
    # competitions.  Where several are fetched alike the heir is the one
    # with the last id, since ``refresh_schedules`` walks teams in id
    # order and the last fetch's tag is the one that stays — the next
    # pass would move the tag there anyway.
    fetched_as = [(team.id, team.league_id, team.provider_key) for team in teams]
    heirs: dict[tuple[str, str], str] = {}
    for team_id, league_id, provider_key in sorted([*fetched_as, *competitions]):
        heirs[(league_id, provider_key)] = team_id
    if not heirs:
        return

    # Plain columns, like the teams above: nothing here is edited, and
    # every one of these rows is about to be bulk-deleted — no reason to
    # bring them into the session.
    stored_competitions = (
        await session.execute(
            select(
                TeamCompetitionORM.team_id,
                TeamCompetitionORM.league_id,
                TeamCompetitionORM.provider_key,
            )
        )
    ).all()
    for gone_id, gone_league_id, gone_key in removed:
        was_fetched_as = {(gone_league_id, gone_key)} | {
            (league_id, provider_key)
            for team_id, league_id, provider_key in stored_competitions
            if team_id == gone_id
        }
        for league_id, provider_key in sorted(was_fetched_as):
            heir = heirs.get((league_id, provider_key))
            if heir is None:
                continue
            for side in (GameORM.home_team_id, GameORM.away_team_id):
                await session.execute(
                    update(GameORM)
                    .where(GameORM.league_id == league_id, side == gone_id)
                    .values({side: heir})
                )


async def replace_followed(
    session: AsyncSession,
    leagues: list[domain.League],
    teams: list[domain.Team],
    competitions: list[tuple[str, str, str]] | None = None,
) -> None:
    """Reconcile the stored followed set with the setup wizard's save.

    Afterwards ``leagues``, ``teams`` and ``team_competitions`` equal the
    arguments, and any other row survives only if the new set still backs
    it (:func:`_drop_unbacked_children` holds the rules).  Everything a
    kept team or league already has is left exactly as it is: an edit
    used to wipe and re-fetch the lot, which cost every kept team its
    rosters, venue and "About" enrichment, prefs and player follows — and
    its results for good, since a refresh only reaches back a week.

    The dedupe ledger is never deleted either.  It is what stops a game
    that survives the edit (or comes back with the next refresh) from
    being announced a second time.

    A removed team's id is nulled on the games it played, with one
    exception: where a kept team is the same team in that game — a second
    pick of the same nation — it takes the tag over first
    (:func:`_hand_over_game_tags`), so the games the two shared do not go
    with whichever pick happened to hold their tags.

    ``competitions`` are ``(team_id, league_id, provider_key)`` triples:
    extra leagues whose fixtures a (national) team should also be pulled
    from.  They are derived afresh by every save, so that table alone is
    still replaced wholesale.
    """
    # Parents FIRST, and upserted rather than re-created.  ``upsert_team``
    # writes identity columns only, so a kept team keeps its enrichment;
    # and a kept team id whose league changed is re-pointed at the new
    # league here, before the old league is deleted below — deleting
    # first would trip the FK it still held.  The flushes are because,
    # without ORM relationships, SQLAlchemy gives no cross-table INSERT
    # ordering inside one flush, and both postgres and (via its PRAGMA)
    # sqlite enforce the FKs.
    for league in leagues:
        await upsert_league(session, league)
    await session.flush()
    for team in teams:
        await upsert_team(session, team)
    await session.flush()

    team_ids = {team.id for team in teams}
    league_ids = {league.id for league in leagues}
    follow_all_ids = {league.id for league in leagues if league.follow_all}

    # While the removed teams and their sibling competitions are still
    # stored, and before the rules below null a removed team's sides.
    await _hand_over_game_tags(session, teams, competitions or ())

    # Children before the parents they reference, then teams before
    # leagues — the same FK reasoning, in the delete direction.
    await session.execute(delete(TeamCompetitionORM))
    await _drop_unbacked_children(session, team_ids, league_ids, follow_all_ids)
    await session.execute(delete(TeamORM).where(_outside(TeamORM.id, team_ids)))
    await session.execute(delete(LeagueORM).where(_outside(LeagueORM.id, league_ids)))

    for team_id, league_id, provider_key in competitions or ():
        session.add(
            TeamCompetitionORM(team_id=team_id, league_id=league_id, provider_key=provider_key)
        )


async def prune_unbacked(session: AsyncSession) -> None:
    """Sweep child rows the followed set, as stored, does not back.

    :func:`replace_followed` applies these rules at save time, but a
    refresh pass or live tick that read the old set can commit a write
    just after the save's deletes — an untagged fixture or a league
    article for a competition that just stopped being followed in whole,
    a table still tagged with a team that is gone.  Nothing else would
    ever remove such a row, so every refresh pass starts by running the
    same rules against the set that is actually stored.

    With foreign keys enforced that can only reach what no FK guards
    (untagged games and league-level news of a league not followed in
    whole, player follows and prefs of a missing team or league, stale
    JSON tags), and on a consistent database it changes nothing.

    The set is handed to the rules as queries, never read into Python
    first, so every statement tests against ``teams`` and ``leagues`` as
    they stand when that statement runs.  A sweep starts every pass — the
    cron's and the startup one too — so a save can commit in the middle of
    it; judged by ids read beforehand, what that save had just added was
    a leftover, and a league newly followed in whole lost the
    headline-only pref seeded for it (and then notified on everything).
    """
    await _drop_unbacked_children(
        session,
        select(TeamORM.id),
        select(LeagueORM.id),
        select(LeagueORM.id).where(LeagueORM.follow_all.is_(True)),
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
