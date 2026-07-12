"""Live polling: game-state ticks with notifications and missed-final resend, live standings kicks, and leaderboard (golf) event ticks.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta


from app.config import get_settings
from app.db import session_scope
from app import background
from app.models import domain
from app.models.domain import EventType, GameEvent, GamePhase, GameState
from app.models.orm import EventORM, GameORM
from app.services import (
    notify,
    notify_prefs,
    repository,
)
from app.services.events import diff_states, starting_soon_event
from app.timeutil import ensure_utc, utcnow

from app.scheduler.common import (
    _EVENTS_LOOKAHEAD,
    _golfer_id_map,
    _league_from_row,
    _load_leagues_and_teams,
    _provider_for,
    _tag_followed_golfers,
)
from app.scheduler.refresh import refresh_standings_for_league

logger = logging.getLogger(__name__)


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
    background.spawn(_refresh_standings_for_leagues(leagues), "kicked-standings-refresh")


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
            for event in diff_states(None, state, home_name=row.home_name, away_name=row.away_name):
                if event.type is not EventType.FINAL:
                    continue
                if not notify_prefs.decide(prefs, event.type.value, team_ids, row.league_id):
                    continue
                await _notify_once(session, event)
        except Exception:
            logger.exception("live_tick: resending final for %r failed — skipping", row.id)
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
                        row.phase == GamePhase.SCHEDULED.value and ensure_utc(row.start_time) <= now
                    )
                    if new_state is None and (row.phase == GamePhase.IN_PROGRESS.value or overdue):
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
                        if prev.phase is not GamePhase.FINAL and new_state.phase is GamePhase.FINAL:
                            finalized_leagues.setdefault(league.id, league)
                    except Exception:
                        logger.exception("live_tick: failed processing %r — skipping", row.id)
                        await session.rollback()
                        continue

        # 5. Standings move when a game finishes: kick one coalesced refresh
        # per league that had a final this tick (isolated; never blocks the
        # poll).  Outside the session scope — the refresh opens its own.
        kick_standings_refresh(list(finalized_leagues.values()))
    except Exception:
        logger.exception("live_tick failed")


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
        rows = await repository.event_finals_missing_notification(session, now, lookback)
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
            followed_ids = [r.player_id for r in event_obj.leaderboard if r.player_id is not None]
            if not notify_prefs.decide(prefs, event.type.value, followed_ids, row.league_id):
                continue
            await _notify_once(session, event)
        except Exception:
            logger.exception("events_tick: resending final for %r failed — skipping", row.id)
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
                        row.id,
                        row.league_id,
                    )
                    continue
                provider = _provider_for(league)
                if provider is None:
                    continue

                try:
                    fresh = await provider.get_event_state(league, _event_provider_key(row.id))
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
                                r.player_id for r in tagged.leaderboard if r.player_id is not None
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
                    logger.exception("events_tick: failed processing %r — skipping", row.id)
                    await session.rollback()
                    continue

        # Kick one coalesced standings refresh per league that had a final.
        kick_standings_refresh(list(finalized_leagues.values()))
    except Exception:
        logger.exception("events_tick failed")
