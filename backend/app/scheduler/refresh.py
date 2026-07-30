"""Daily refresh jobs: schedules, standings, rosters (+photo backfill), news, locations, team info, and the daily_refresh orchestrator.

Split out of the original single-file jobs.py; see jobs.py (the facade).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta


from app.config import get_settings
from app.db import session_scope
from app.models import domain
from app.models.domain import INDIVIDUAL_SPORTS, EventType, GameEvent, Sport
from app.models.orm import PlayerFollowORM, PlayerORM
from app.services import (
    geocode,
    news,
    notify,
    notify_prefs,
    player_photos,
    repository,
    stadiums,
    wiki,
)
from app.services.events import player_status_label
from app.timeutil import ensure_utc, utcnow

from app.scheduler.common import (
    _athlete_id_map,
    _is_leaderboard,
    _league_from_row,
    _load_leagues_and_teams,
    _load_team_competitions,
    _provider_for,
    _tag_followed_athletes,
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

        # Pass 4: leaderboard events (golf, racing).  A followed golfer or
        # driver is a single-member team and a leaderboard follow_all league
        # pulls the whole field; either way we fetch the tour's/series'
        # Events and tag the leaderboard rows that belong to a followed
        # athlete.
        await _refresh_events(leagues, teams, start, end)
    except Exception:
        logger.exception("refresh_schedules failed")


async def _refresh_events(
    leagues: dict[str, domain.League],
    teams: list[domain.Team],
    start: date,
    end: date,
) -> None:
    """Fetch leaderboard events for followed athletes + leaderboard follow_all leagues.

    Each source is fetched once per leaderboard league (a league's Events
    are the same regardless of which followed golfer/driver triggered the
    fetch), the leaderboard rows are re-tagged ESPN-id -> internal-id, and
    the events are upserted.  Per-league failures are isolated so one bad
    tour can't abort the rest.
    """
    espn_to_internal = _athlete_id_map(teams, leagues)

    # Leaderboard leagues to pull: every league with at least one followed
    # athlete, plus every leaderboard follow_all league.  Deduplicated by
    # league id so a league with several followed athletes is fetched only
    # once.
    leaderboard_league_ids = {
        team.league_id
        for team in teams
        if (lg := leagues.get(team.league_id)) is not None and _is_leaderboard(lg)
    }
    async with session_scope() as session:
        follow_all_rows = await repository.list_follow_all_leagues(session)
    for row in follow_all_rows:
        league = leagues.get(row.id)
        if league is not None and _is_leaderboard(league):
            leaderboard_league_ids.add(league.id)

    for league_id in sorted(leaderboard_league_ids):
        league = leagues.get(league_id)
        if league is None:
            continue
        provider = _provider_for(league)
        if provider is None:
            continue
        try:
            events = await provider.get_events(league, start, end)
            tagged = [_tag_followed_athletes(event, espn_to_internal) for event in events]
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


def _bare_athlete_id(player_id: str) -> str:
    """Strip the ``"espn:"`` provider prefix off a stored roster player id."""
    return player_id.split(":", 1)[1] if ":" in player_id else player_id


@dataclass(frozen=True)
class _StatusUpdate:
    """One followed athlete's status movement, from a roster-sync diff.

    ``event`` is ``None`` for a silent baseline seed (the follow has no
    baseline yet); otherwise the alert to send.  Either way the follow's
    ``last_notified_status`` advances to ``status`` — for an alert, only
    on confirmed delivery.
    """

    athlete_id: str
    status: str
    event: GameEvent | None


def _player_status_updates(
    team_name: str,
    baselines: dict[str, str | None],
    roster: domain.Roster,
) -> list[_StatusUpdate]:
    """Status movements for followed athletes, from a roster-sync diff.

    ``baselines`` maps bare athlete id -> the follow's
    ``last_notified_status``.  The fetched roster is diffed against that
    per-follow baseline (never against the roster table, which
    ``replace_roster`` has already overwritten by delivery time), and the
    baseline is the dedupe: a repeat sync sees no difference, while a
    re-injury after a delivered recovery alert differs again and
    re-fires.  A follow with no baseline yet (``None`` — a fresh follow
    seeded outside the route, or a pre-baseline row) seeds silently —
    otherwise every new follow would fire a blast.
    """
    updates: list[_StatusUpdate] = []
    for player in roster.players:
        bare = _bare_athlete_id(player.id)
        if bare not in baselines:
            continue
        baseline = baselines[bare]
        new_status = player.status.value
        if new_status == baseline:
            continue
        if baseline is None:
            updates.append(_StatusUpdate(athlete_id=bare, status=new_status, event=None))
            continue
        message = f"{player.name} is {player_status_label(new_status)}"
        if player.status_detail:
            message = f"{message} — {player.status_detail}"
        updates.append(
            _StatusUpdate(
                athlete_id=bare,
                status=new_status,
                event=GameEvent(
                    type=EventType.PLAYER_STATUS,
                    game_id=f"player:{bare}",
                    title=team_name,
                    message=message,
                    dedupe_key=f"player:{bare}:status:{new_status}",
                ),
            )
        )
    return updates


async def _notify_player_status(team_id: str, league_id: str, updates: list[_StatusUpdate]) -> None:
    """Send roster-diff player-status alerts; best-effort, never raises.

    v1 gating: the alert rides the parent team's ``team:{id}`` scope (no
    per-player scopes yet).  The per-follow ``last_notified_status``
    baseline IS the dedupe — no :class:`NotificationSentORM` writes here
    — and it advances (each committed on its own) only on confirmed
    delivery, so a failed ntfy send leaves the baseline behind and the
    same diff re-fires on the next sync.  A muted team doesn't advance it
    either: un-muting later still alerts on the next sync if the status
    still differs.  Baseline-less follows seed silently.
    """
    if not updates:
        return
    try:
        async with session_scope() as session:
            prefs = await repository.prefs_by_scope(session)
            for update in updates:
                if update.event is not None:
                    if not notify_prefs.decide(
                        prefs, EventType.PLAYER_STATUS.value, [team_id], league_id
                    ):
                        continue
                    if not await notify.send_event(update.event):
                        logger.warning(
                            "roster sync: notification %r not delivered — will retry next sync",
                            update.event.dedupe_key,
                        )
                        continue
                row = await session.get(PlayerFollowORM, update.athlete_id)
                if row is not None:
                    row.last_notified_status = update.status
                    await session.commit()
    except Exception:
        logger.exception("roster sync: player-status notify failed for %r", team_id)


def _carry_forward_enrichment(stored: list[PlayerORM], fetched: domain.Roster) -> domain.Roster:
    """Keep stored stat lines / headshots a cheap fetch didn't ask for.

    ``replace_roster`` deletes and reinserts every player column, so a fetch
    that skipped the per-athlete stat-line pass (and the soccer photo
    backfill) would BLANK ``stat_line`` / ``career_stat_line`` /
    ``photo_url`` for the whole squad until the next daily sync — emptying
    the roster view's stat columns and the roster-derived leaders board.
    Each fetched player therefore takes the stored value for any of those
    three the fetch left empty, keyed on the roster player id; a player the
    stored roster doesn't have (a call-up, a trade) simply keeps its own,
    and the status columns always come from the fetch (they are the point of
    it).
    """
    if not stored:
        return fetched
    by_id = {row.id: row for row in stored}

    def _merge(player: domain.Player) -> domain.Player:
        row = by_id.get(player.id)
        if row is None:
            return player
        return replace(
            player,
            stat_line=player.stat_line if player.stat_line is not None else row.stat_line,
            career_stat_line=(
                player.career_stat_line
                if player.career_stat_line is not None
                else row.career_stat_line
            ),
            photo_url=player.photo_url or row.photo_url,
        )

    return replace(fetched, players=tuple(_merge(player) for player in fetched.players))


async def _sync_team_roster(
    league: domain.League,
    team: domain.Team,
    followed: dict[str, str | None],
    *,
    with_stat_lines: bool = True,
    backfill_photos: bool = True,
) -> None:
    """Fetch, store and status-diff ONE team's roster.  Never raises.

    Shared by the daily sweep (both enrichments on) and the same-day
    pre-game re-sync (both off — see :func:`refresh_pregame_rosters` for why
    neither is affordable at that cadence).  ``followed`` maps bare athlete
    id -> ``last_notified_status`` baseline for this team's followed players;
    empty means there is nothing to diff.

    Storing always goes through ``replace_roster`` — one roster writer, so a
    call-up or a trade is still picked up and ``roster_updated_at`` keeps its
    single writer and its meaning — with whatever the cheap fetch skipped
    carried forward from the stored rows first.
    """
    provider = _provider_for(league)
    if provider is None:
        return
    try:
        roster = await provider.get_roster(league, team, with_stat_lines=with_stat_lines)
        # Soccer-only photo fallback: ESPN gives soccer clubs headshots for
        # ~2 of 38 players, so backfill the rest from TheSportsDB (capped +
        # cached) before storing.
        if backfill_photos and league.sport is Sport.SOCCER:
            roster = await _attach_player_photos(roster, team, league.sport.value)
        async with session_scope() as session:
            if not (with_stat_lines and backfill_photos):
                roster = _carry_forward_enrichment(
                    await repository.get_roster(session, roster.team_id), roster
                )
            await repository.replace_roster(session, roster)
        logger.info("roster sync: %s — %d player(s)", team.id, len(roster.players))
        # Diff the FETCHED roster against each follow's own last-notified
        # baseline — never against the roster table, which the replace above
        # has already overwritten.  The baseline only advances on confirmed
        # delivery, so nothing is lost between the replace and the send.
        if followed:
            updates = _player_status_updates(team.name, followed, roster)
            await _notify_player_status(team.id, league.id, updates)
    except Exception:
        logger.exception("roster sync: failed for team %r — skipping", team.id)


async def _followed_players_by_team() -> dict[str, dict[str, str | None]]:
    """Followed players as ``{team_id: {bare athlete id: baseline}}``.

    Read ONCE per job run (not per team); the baseline is each follow's
    ``last_notified_status``, which is both the status-diff input and its
    dedupe.
    """
    followed_by_team: dict[str, dict[str, str | None]] = {}
    async with session_scope() as session:
        for follow in await repository.list_player_follows(session):
            followed_by_team.setdefault(follow.team_id, {})[follow.athlete_id] = (
                follow.last_notified_status
            )
    return followed_by_team


async def refresh_rosters() -> None:
    try:
        leagues, teams = await _load_leagues_and_teams()
        followed_by_team = await _followed_players_by_team()
        for team in teams:
            league = leagues.get(team.league_id)
            if league is None:
                logger.error(
                    "Team %r references unknown league %r — skipping", team.id, team.league_id
                )
                continue
            await _sync_team_roster(league, team, followed_by_team.get(team.id, {}))
    except Exception:
        logger.exception("refresh_rosters failed")


async def refresh_pregame_rosters() -> None:
    """Status-only roster re-sync for followed teams whose game starts soon.

    Injury and scratch designations are only final in the hours before
    kickoff, while the daily roster sync can be a whole day stale — so a
    player ruled out at noon used to be invisible until the next 5am sweep.
    This job re-fetches ONLY the roster payload (statuses ride on it) for
    teams that have a followed player and an imminent game, then runs the
    very same ``last_notified_status`` diff the daily sync runs, so the
    scratch alerts within a tick.  It also leaves ``live_tick``'s
    starting-soon "ruled out" pass reading same-day-fresh statuses.

    Cost is why this is its own job and not part of an existing one: the
    daily sync's per-athlete stat-line pass costs one request per player,
    which at this cadence would be hundreds of calls a day.  Three gates,
    cheapest first — no player follows at all means zero DB scans and zero
    provider calls; then only teams with a game inside the lookahead; then a
    per-team cooldown read off ``TeamORM.roster_updated_at`` (stamped by
    ``replace_roster``, so a sync cools itself down).  What survives costs
    exactly ONE roster call per team per cooldown window, and the stat lines
    the fetch skips are carried forward rather than re-fetched.

    Per-team failures are isolated inside :func:`_sync_team_roster` and the
    job never raises.
    """
    settings = get_settings()
    if not settings.pregame_roster_refresh_enabled:
        return
    try:
        now = utcnow()
        followed_by_team = await _followed_players_by_team()
        if not followed_by_team:
            return
        async with session_scope() as session:
            soon = await repository.team_ids_with_games_starting_within(
                session, now, timedelta(hours=settings.pregame_roster_lookahead_hours)
            )
            candidates = sorted(soon & followed_by_team.keys())
            if not candidates:
                return
            # Cooldown snapshot, taken while the session is open: sqlite
            # hands the timestamp back NAIVE, so it goes through ensure_utc
            # before any arithmetic.  Each team syncs at most once per run,
            # so one up-front read is enough.
            last_synced: dict[str, datetime | None] = {}
            for team_id in candidates:
                row = await repository.get_team(session, team_id)
                last_synced[team_id] = (
                    ensure_utc(row.roster_updated_at)
                    if row is not None and row.roster_updated_at is not None
                    else None
                )

        leagues, teams = await _load_leagues_and_teams()
        by_id = {team.id: team for team in teams}
        cooldown = timedelta(minutes=settings.pregame_roster_cooldown_minutes)
        synced = 0
        for team_id in candidates:
            team = by_id.get(team_id)
            league = leagues.get(team.league_id) if team is not None else None
            if team is None or league is None:
                continue
            if league.sport in INDIVIDUAL_SPORTS:
                # A followed athlete is a single-member "team" with no
                # roster: the fetch would come back empty and the store
                # would only delete the rows and restamp the team.
                continue
            last = last_synced.get(team_id)
            if last is not None and now - last < cooldown:
                continue
            await _sync_team_roster(
                league,
                team,
                followed_by_team[team_id],
                with_stat_lines=False,
                backfill_photos=False,
            )
            synced += 1
        if synced:
            logger.info(
                "refresh_pregame_rosters: re-synced %d of %d team(s) with an imminent game",
                synced,
                len(candidates),
            )
    except Exception:
        logger.exception("refresh_pregame_rosters failed")


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
       stored fixtures and geocode that.  This is the step that saves a
       club whose stadium TheSportsDB simply doesn't know — the enrichment
       yields no venue name at all rather than the county it does know
       (see :func:`app.services.stadiums._compose_venue`), so the club
       lands on the ground its own fixtures name instead of on a regional
       centroid.

    Returns ``None`` when nothing usable could be found, so the caller
    skips the team (and retries it on a later run) rather than pinning it
    somewhere merely plausible.
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


async def _clear_area_only_team_locations() -> None:
    """Drop cached team coordinates that are really an area centroid.

    Repairs rows written before the venue-composition fix: when the
    enrichment found no stadium name it used to hand back the bare location
    text ("Dorset, England") as the venue, which geocoded to the AREA'S
    centre and was then cached on the team row as a precise home-venue fix
    — a marker tens of km from the real ground, and one the location job
    would never revisit, because it only processes teams *without*
    coordinates.

    Clearing the two false facts (the venue name and the coordinates) while
    keeping the true ones (the area text, capacity, photo, surface) puts the
    team back in the pending set, so the very same pass re-resolves it from
    a venue name worth trusting — or leaves it off the map.  Never raises:
    a repair failure must not cost the caller its refresh.
    """
    repaired = 0
    try:
        async with session_scope() as session:
            for row in await repository.list_teams(session):
                if not stadiums.is_area_only_venue(row.home_venue, row.venue_location):
                    continue
                await repository.set_team_location(
                    session,
                    row.id,
                    None,
                    None,
                    None,
                    capacity=row.venue_capacity,
                    opened=row.venue_opened,
                    image_url=row.venue_image_url,
                    location=row.venue_location,
                    surface=row.venue_surface,
                )
                repaired += 1
                logger.info(
                    "refresh_locations: %s was pinned at the centroid of %r — "
                    "clearing for re-resolution",
                    row.id,
                    row.venue_location,
                )
    except Exception:
        logger.exception("refresh_locations: area-centroid repair failed — continuing")
    if repaired:
        logger.info("refresh_locations: cleared %d area-centroid pin(s)", repaired)


async def refresh_locations() -> None:
    """Resolve + cache home-venue coordinates for followed teams (map view).

    Only teams that do not already have coordinates are processed — a
    resolved team is never geocoded again (the lat/lon are cached on the
    team row) — except for a team cached at an area centroid, which
    :func:`_clear_area_only_team_locations` puts back in the pending set
    first.  Per-team failures are isolated so one unresolvable venue never
    aborts the rest, and the job itself never raises (the scheduler and the
    startup kick both rely on that).
    """
    try:
        await _clear_area_only_team_locations()
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


@dataclass(frozen=True)
class _AboutFacts:
    """One team's resolved "About" payload, from whichever source supplied it."""

    description: str | None = None
    founded: int | None = None
    venue_description: str | None = None  # stadium prose (TheSportsDB only)
    # Which upstream supplied ``description`` ("thesportsdb" | "wikipedia"),
    # so the profile page can attribute the text; None when there is none.
    description_source: str | None = None


async def _resolve_team_info(team: domain.Team, sport: str) -> _AboutFacts | None:
    """Resolve one team's "About" facts: TheSportsDB first, Wikipedia fallback.

    TheSportsDB's ``searchteams`` description is sport-correct and reliable,
    and the team's venue record adds the stadium's own prose; Wikipedia only
    fills a missing club description (its lead paragraph), never the venue
    text.  All defensive — returns ``None`` when neither source has anything.
    """
    info = await stadiums.lookup_team_info(team.name, sport=sport)
    description = info.description if info is not None else None
    founded = info.founded if info is not None else None
    venue_description = info.venue_description if info is not None else None
    source = "thesportsdb" if description is not None else None

    if description is None:
        summary = await wiki.team_summary(team.name, sport=sport)
        if summary is not None and summary.extract:
            description = summary.extract
            source = "wikipedia"

    if description is None and founded is None and venue_description is None:
        return None
    return _AboutFacts(
        description=description,
        founded=founded,
        venue_description=venue_description,
        description_source=source,
    )


async def refresh_team_info() -> None:
    """Resolve + cache club "About" facts (description + founded + venue prose).

    Only teams still missing the club description OR their venue's prose are
    processed (both usually resolve in the same pass), so an enriched team is
    never re-fetched — while a team enriched before venue prose existed still
    gets it backfilled.  Per-team failures are isolated and the job never
    raises — the scheduler and the startup kick both rely on that.
    """
    try:
        leagues, _ = await _load_leagues_and_teams()
        async with session_scope() as session:
            team_rows = await repository.list_teams(session)
            pending = [
                _team_from_row(row)
                for row in team_rows
                if row.description is None or row.venue_description is None
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
                        venue_description=info.venue_description,
                        description_source=info.description_source,
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
    # Club "About" facts (history, founded year, stadium prose) for followed
    # teams' pages.
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
