"""The EspnProvider class: fetches and delegates to the pure parsers.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date
from typing import Any

import httpx

from app import timeutil
from app.config import get_settings
from app.providers.http_util import TransientProviderError, get_with_retry
from app.models.domain import (
    INDIVIDUAL_SPORTS,
    LEADERBOARD_SPORTS,
    Event,
    FightResult,
    Game,
    GameOdds,
    GamePhase,
    GameState,
    GameSummary,
    League,
    NewsItem,
    Player,
    Roster,
    Sport,
    Standings,
    Team,
    TeamLocation,
)

from app.providers.espn.common import (
    _CORE_BASE,
    _FIGHT_RESULT_CONCURRENCY,
    _FIGHT_RESULT_MAX_BOUTS,
    _SCOREBOARD_TZ,
    _SITE_BASE,
    _STANDINGS_BASE,
    _STAT_LINE_CONCURRENCY,
    _STAT_LINE_MAX_ATHLETES,
    _WEB_BASE,
)
from app.providers.espn.games import _parse_schedule, _parse_scoreboard
from app.providers.espn.golf import _event_overlaps_window, _parse_golf_scoreboard
from app.providers.espn.racing import (
    _ENRICH_CONCURRENCY,
    _ENRICH_MAX_CARS,
    _ENRICH_MEMO_MAX,
    _ORDER_LAST,
    _RacingCar,
    _RacingCarFacts,
    _RacingCarStats,
    _apply_racing_facts,
    _parse_racing_car_status,
    _parse_racing_scoreboard,
    _parse_racing_stats,
    _racing_competition_statistics_url,
    _racing_core_event_url,
    _racing_enrich_targets,
    _racing_facts,
    _racing_field,
    _racing_lap_label,
    _racing_race_distance,
)
from app.providers.espn.individual import (
    _games_for_athlete,
    _merge_fight_results,
    _mma_status_urls,
    _parse_bout_status,
    _parse_individual_scoreboard,
)
from app.providers.espn.news_location import _parse_news, _parse_team_location
from app.providers.espn.roster import (
    _athlete_id,
    _career_line_from_overview,
    _overview_path,
    _parse_roster,
    _stat_line_from_overview,
)
from app.providers.espn.standings import _parse_standings, _parse_tennis_rankings
from app.providers.espn.summary import (
    _SCHEDULE_PARAM_SETS,
    _add_month,
    _chunk_date_range,
    _core_event_path,
    _merge_games,
    _odds_has_signal,
    _parse_game_summary,
    _parse_pickcenter,
    _parse_predictor,
    _parse_summary_state,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class EspnProvider:
    """``SportsProvider`` adapter for ESPN's public site API."""

    provider_id = "espn"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        # ESPN team id -> internal slug, learned from the (league, team)
        # arguments of schedule/roster/news calls.  get_standings only receives
        # a League, and the scheduler always refreshes schedules first, so
        # by the time standings are fetched the mapping is populated and
        # followed teams can be tagged in standings rows.
        self._known_teams: dict[str, dict[str, str]] = {}
        # Racing enrichment memos, keyed by provider event key (see
        # _enrich_racing_event).  ``_racing_facts_memo`` holds the rendered
        # score/detail of a race that has gone FINAL — a finished
        # classification never changes, so re-polling it must not re-spend
        # the per-car fan-out.  ``_racing_laps`` holds each event's
        # scheduled race distance, a constant the site scoreboard omits.
        self._racing_facts_memo: dict[str, dict[str, _RacingCarFacts]] = {}
        self._racing_laps: dict[str, int | None] = {}

    def _register(self, league: League, team: Team) -> None:
        self._known_teams.setdefault(league.id, {})[str(team.provider_key)] = team.id

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(get_settings().provider_timeout_seconds),
                headers={"User-Agent": "SportsDash/1.0"},
                follow_redirects=True,
            )
        return self._client

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        settings = get_settings()
        response = await get_with_retry(
            self._get_client(),
            url,
            params=params,
            max_retries=settings.provider_max_retries,
            backoff_base=settings.provider_backoff_base,
            label="espn",
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def get_schedule(self, league: League, team: Team, start: date, end: date) -> list[Game]:
        self._register(league, team)
        if league.sport in LEADERBOARD_SPORTS:
            # A leaderboard sport has no two-sided games by definition — its
            # tournaments/race weekends come from get_events.  Scanning the
            # tour scoreboard here only ever produced "Skipping malformed"
            # log noise, so the fetch is skipped outright.
            return []
        if league.sport in INDIVIDUAL_SPORTS:
            # An athlete has no ``/teams/{id}/schedule`` endpoint; scan the
            # tour/card scoreboard for the competitions they appear in.
            games = await self._get_individual_schedule(league, team, start, end)
        else:
            url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/schedule"
            param_sets = _SCHEDULE_PARAM_SETS.get(league.sport)
            if param_sets is not None:
                games = await self._get_merged_schedule(url, league, team, param_sets)
            else:
                games = _parse_schedule(await self._get_json(url), league, team)
        return sorted(
            (game for game in games if start <= game.start_time.date() <= end),
            key=lambda game: game.start_time,
        )

    async def _get_individual_schedule(
        self, league: League, team: Team, start: date, end: date
    ) -> list[Game]:
        """A followed athlete's matches/bouts in ``[start, end]``.

        Tennis ships the whole tournament draw on the tour scoreboard;
        ``?dates=YYYYMMDD-YYYYMMDD`` returns every event overlapping the
        range, so one ranged call (chunked by month for long windows)
        covers the window.  UFC schedules at month granularity
        (``?dates=YYYYMM``), so each calendar month in the window is
        fetched and the bouts the athlete appears in are kept.  Each chunk
        is fetched independently; a failed chunk logs and is skipped.

        MMA additionally collects each bout's core-API status URL so the
        followed fighter's FINISHED bouts can be enriched with their method
        of victory (see :meth:`_attach_fight_results`).
        """
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        core_league_base = f"{_CORE_BASE}/{_core_event_path(league.provider_key)}"
        batches: list[list[Game]] = []
        status_urls: dict[str, str] = {}
        errors: list[Exception] = []
        for params in self._individual_scoreboard_params(league.sport, start, end):
            try:
                data = await self._get_json(url, params=params)
            except Exception as exc:
                logger.warning(
                    "ESPN %s schedule call failed for athlete %s (params=%s): %s",
                    league.sport.value,
                    team.id,
                    params,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_individual_scoreboard(data, league, team))
            if league.sport is Sport.MMA:
                status_urls.update(_mma_status_urls(data, core_league_base=core_league_base))
        if not batches and errors:
            raise errors[0]
        games = _games_for_athlete(_merge_games(*batches), team)
        if league.sport is Sport.MMA:
            games = await self._attach_fight_results(
                league, games, status_urls, window=(start, end)
            )
        return games

    async def _attach_fight_results(
        self,
        league: League,
        games: list[Game],
        status_urls: dict[str, str],
        *,
        window: tuple[date, date],
    ) -> list[Game]:
        """Fill in the method of victory for a followed fighter's finished bouts.

        ESPN's scoreboard rarely spells out HOW a bout ended, so each
        finished bout needs one core-API status call; the fan-out is
        bounded by ``_FIGHT_RESULT_MAX_BOUTS`` /
        ``_FIGHT_RESULT_CONCURRENCY`` and restricted to bouts that are
        FINAL, involve the followed fighter, and fall inside the requested
        window (the caller drops the rest, so they aren't worth a call).
        The live scoreboard path is deliberately NOT enriched: it has no
        team scope, so it could only fan out over whole cards.

        Best-effort exactly like the roster stat-line backfill: a per-bout
        failure — a transient outage, or a 200 that isn't JSON — leaves
        that bout with whatever the scoreboard already knew rather than
        failing the whole schedule or tripping the provider breaker.
        """
        start, end = window
        targets = [
            game
            for game in games
            if game.state is not None
            and game.state.phase is GamePhase.FINAL
            and (game.home_team_id is not None or game.away_team_id is not None)
            and start <= game.start_time.date() <= end
            and game.id in status_urls
        ]
        if not targets:
            return games
        targets.sort(key=lambda game: game.start_time, reverse=True)
        targets = targets[:_FIGHT_RESULT_MAX_BOUTS]
        semaphore = asyncio.Semaphore(_FIGHT_RESULT_CONCURRENCY)

        async def fetch(game: Game) -> FightResult | None:
            async with semaphore:
                try:
                    data = await self._get_json(status_urls[game.id])
                # ValueError covers ``json.JSONDecodeError``: the core API
                # answers an outage with an HTML error page under a 200, and
                # a best-effort garnish must degrade on that exactly as it
                # does on a connection blip.
                except (httpx.HTTPError, TransientProviderError, ValueError) as exc:
                    logger.warning(
                        "ESPN bout-status call failed for %s (league %s): %s",
                        game.id,
                        league.id,
                        exc,
                    )
                    return None
            return _parse_bout_status(data)

        fetched = await asyncio.gather(*(fetch(game) for game in targets))
        by_id = {
            game.id: result
            for game, result in zip(targets, fetched, strict=False)
            if result is not None
        }
        if not by_id:
            return games
        return [
            (
                replace(game, fight_result=_merge_fight_results(game.fight_result, by_id[game.id]))
                if game.id in by_id
                else game
            )
            for game in games
        ]

    @staticmethod
    def _individual_scoreboard_params(sport: Sport, start: date, end: date) -> list[dict[str, str]]:
        """Scoreboard ``dates`` params covering ``[start, end]`` per sport.

        Tennis uses ranged ``YYYYMMDD-YYYYMMDD`` dates (chunked by month
        for long windows).  UFC takes a ``YYYYMM`` month bucket, so one
        param is emitted per calendar month spanned by the window.
        """
        if sport is Sport.MMA:
            params: list[dict[str, str]] = []
            cursor = date(start.year, start.month, 1)
            while cursor <= end:
                params.append({"dates": cursor.strftime("%Y%m"), "limit": "400"})
                cursor = _add_month(cursor)
            return params
        return [
            {
                "dates": (f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"),
                "limit": "400",
            }
            for chunk_start, chunk_end in _chunk_date_range(start, end)
        ]

    async def _get_merged_schedule(
        self,
        url: str,
        league: League,
        team: Team,
        param_sets: tuple[dict[str, str] | None, ...],
    ) -> list[Game]:
        """Fetch + merge the halves of a split ESPN team schedule.

        ESPN splits some team schedules across multiple calls (see
        ``_SCHEDULE_PARAM_SETS``).  The calls are independent — if one
        fails, log it and use the others (a half schedule beats none);
        only when all fail does the error propagate.  Duplicate game ids
        keep the earlier param set's version (see ``_merge_games``).
        """
        batches: list[list[Game]] = []
        errors: list[Exception] = []
        for params in param_sets:
            try:
                data = await self._get_json(url, params=params)
            except Exception as exc:
                logger.warning(
                    "ESPN schedule call failed for team %s (params=%s): %s",
                    team.id,
                    params,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_schedule(data, league, team))
        if not batches:
            raise errors[0]
        return _merge_games(*batches)

    async def get_live_games(self, league: League) -> list[Game]:
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        if league.sport in INDIVIDUAL_SPORTS:
            # Tour-wide: tennis ships the live draw, UFC the current month's
            # cards.  live_tick matches the returned games to followed rows
            # by id, so scanning the whole scoreboard is fine (no team scope).
            # No fight-result enrichment here for exactly that reason: with
            # no team to scope it to, it could only fan out over every bout
            # on every card.  The schedule refresh owns that (see
            # _attach_fight_results).
            now = timeutil.utcnow().astimezone(_SCOREBOARD_TZ)
            dates = now.strftime("%Y%m") if league.sport is Sport.MMA else now.strftime("%Y%m%d")
            data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            return _parse_individual_scoreboard(data, league)
        today = timeutil.utcnow().astimezone(_SCOREBOARD_TZ).strftime("%Y%m%d")
        # ESPN silently caps scoreboards at 100 events without a limit;
        # busy soccer days (every league shares one scoreboard) exceed it.
        data = await self._get_json(url, params={"dates": today, "limit": "400"})
        return _parse_scoreboard(data, league)

    async def get_competition_schedule(self, league: League, start: date, end: date) -> list[Game]:
        """Every fixture in ``league`` between ``[start, end]`` (UTC dates).

        Whole-competition follows (``League.follow_all``) have no team to
        scope a per-team schedule call, so the ranged scoreboard endpoint
        (``?dates=YYYYMMDD-YYYYMMDD&limit=400``) is used instead — one
        call returns every fixture in the window (verified: the full
        World Cup season, 104 matches, in a single call).  ``limit=400``
        is load-bearing: without it ESPN silently caps at 100 events.

        Ranges longer than ~45 days are split on month boundaries
        (``_chunk_date_range``) to keep each payload small; the chunks are
        fetched independently so one failing call can't lose the rest, and
        their results are merged by game id.  Scoreboard buckets are keyed
        by US/Eastern, so the requested dates are formatted in ET to match
        ESPN's day boundaries; the final ``[start, end]`` filter is applied
        to each game's tz-aware UTC start date.
        """
        if league.sport in LEADERBOARD_SPORTS:
            # A leaderboard follow_all league (a golf tour, a racing series)
            # has no fixtures on the games path; its Events come from
            # get_events (see get_schedule's identical gate).
            return []
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Game]] = []
        errors: list[Exception] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end):
            dates = f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
            try:
                data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            except Exception as exc:
                logger.warning(
                    "ESPN competition scoreboard call failed for league %s (dates=%s): %s",
                    league.id,
                    dates,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_scoreboard(data, league))
        if not batches and errors:
            raise errors[0]
        games = _merge_games(*batches)
        return sorted(
            (game for game in games if start <= game.start_time.date() <= end),
            key=lambda game: game.start_time,
        )

    @staticmethod
    def _event_scoreboard_parser(league: League):
        """The leaderboard scoreboard parser for ``league``'s sport.

        Golf and racing share the Event pipeline but not a payload shape
        (golf: one competition with linescored golfers; racing: one
        competition per session with thin competitors), so each leaderboard
        sport brings its own parser.
        """
        return _parse_racing_scoreboard if league.sport is Sport.RACING else _parse_golf_scoreboard

    async def get_events(self, league: League, start: date, end: date) -> list[Event]:
        """Leaderboard events in ``[start, end]`` for a golf/racing league.

        Non-leaderboard sports return ``[]``.  ESPN's tour/series scoreboard
        exposes the current/most-recent event (and the season calendar
        around it) — a single ``?dates=YYYYMMDD-YYYYMMDD`` call covers the
        window (chunked by month for long ranges, like the competition
        scoreboard), and each tournament/race weekend becomes one
        :class:`Event` with a populated leaderboard.  Each
        :class:`LeaderRow` carries the athlete's ESPN id in ``player_id``
        TRANSIENTLY; the scheduler rewrites it to the internal
        followed-team id (or None) before persisting.  Events are kept
        when their span overlaps ``[start, end]`` (a multi-day event that
        merely brackets the window still counts).
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return []
        parse = self._event_scoreboard_parser(league)
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Event]] = []
        errors: list[Exception] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end):
            dates = f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
            try:
                data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            except Exception as exc:
                logger.warning(
                    "ESPN leaderboard scoreboard call failed for league %s (dates=%s): %s",
                    league.id,
                    dates,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(parse(data, league))
        if not batches and errors:
            raise errors[0]
        # Merge by event id (first batch wins on duplicates) and keep any
        # tournament whose [start_time, end_time] span overlaps the window.
        merged: dict[str, Event] = {}
        for batch in batches:
            for event in batch:
                merged.setdefault(event.id, event)
        kept = [event for event in merged.values() if _event_overlaps_window(event, start, end)]
        return await self._enrich_racing(league, sorted(kept, key=lambda e: e.start_time))

    async def get_event_state(self, league: League, provider_event_key: str) -> Event | None:
        """Current state of a single leaderboard event, or None if unknown.

        The ``/summary`` endpoint is unreliable for leaderboard sports
        (golf returns a non-JSON error body for an event id; racing's 404s
        outright — both verified live), so the current scoreboard is
        scanned for the event whose id matches ``provider_event_key``.
        Non-leaderboard leagues always return None.
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return None
        parse = self._event_scoreboard_parser(league)
        target = f"espn:{provider_event_key}"
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        data = await self._get_json(url, params={"limit": "400"})
        for event in parse(data, league):
            if event.id == target:
                enriched = await self._enrich_racing(league, [event])
                return enriched[0]
        return None

    # -- racing leaderboard enrichment ------------------------------------

    async def _enrich_racing(self, league: League, events: list[Event]) -> list[Event]:
        """Fill racing boards' ``score``/``detail`` from ESPN's core API.

        Racing only: a golf board is already scored by the site scoreboard,
        and no other sport reaches here.  The fan-out is bounded by
        :func:`_racing_enrich_targets` (which also skips scheduled weekends,
        whose entry lists ESPN has not published yet) and every failure
        degrades to the un-enriched board — a core-API outage must never
        cost us the leaderboard the site scoreboard already gave us.
        """
        if league.sport is not Sport.RACING:
            return events
        targets = _racing_enrich_targets(events)
        if not targets:
            return events
        # Sequential across events (at most _ENRICH_MAX_EVENTS of them);
        # the per-car fan-out INSIDE an event is what runs concurrently.
        enriched: dict[str, Event] = {}
        for event in targets:
            try:
                fresh = await self._enrich_racing_event(league, event)
            except Exception:
                # Belt and braces around the fetch-level degrade below: an
                # unannounced shape change in these feeds must cost us the
                # garnish, never the board (or the whole league's fetch).
                logger.exception(
                    "ESPN racing enrichment failed for %s (league %s) — keeping the plain board",
                    event.id,
                    league.id,
                )
                continue
            if fresh is not None:
                enriched[event.id] = fresh
        if not enriched:
            return events
        return [enriched.get(event.id, event) for event in events]

    async def _enrich_racing_event(self, league: League, event: Event) -> Event | None:
        """One race weekend enriched, or None when nothing could be added.

        Tier A (always): the core event root, for each car's constructor,
        number and grid slot — plus, on a live race, the scheduled distance
        that turns "Lap 44" into "Lap 44 of 70".  Tier B (only once the race
        is FINAL): the per-car statistics/status N+1 behind the race time,
        gap and retirements.  A FINAL race's rendered facts are memoized, so
        the second poll of a finished race costs zero calls.
        """
        key = event.provider_event_key
        final = event.phase is GamePhase.FINAL
        memo = self._racing_facts_memo.get(key) if final else None
        if memo is not None:
            return replace(event, leaderboard=_apply_racing_facts(event.leaderboard, memo))

        data = await self._racing_json(_racing_core_event_url(league.provider_key, key), key)
        if data is None:
            return None
        competition_id, cars = _racing_field(data)
        if not cars:
            return None

        stats: dict[str, _RacingCarStats] = {}
        statuses: dict[str, tuple[str, int | None]] = {}
        if final:
            stats, statuses = await self._racing_car_details(cars, key)
        facts = _racing_facts(cars, stats, statuses)
        if final and facts:
            self._memoize(self._racing_facts_memo, key, facts)

        board = _apply_racing_facts(event.leaderboard, facts)
        label = event.round_label
        if event.phase is GamePhase.IN_PROGRESS and competition_id is not None:
            label = _racing_lap_label(
                label, await self._racing_distance(league, key, competition_id)
            )
        if board == event.leaderboard and label == event.round_label:
            return None
        return replace(event, leaderboard=board, round_label=label)

    async def _racing_car_details(
        self, cars: dict[str, _RacingCar], event_key: str
    ) -> tuple[dict[str, _RacingCarStats], dict[str, tuple[str, int | None]]]:
        """Tier B: per-car statistics (+ status where it exists), bounded.

        ESPN cannot expand those refs in bulk, so this is a genuine N+1 —
        run once per race, at FINAL, over at most ``_ENRICH_MAX_CARS`` cars
        (front of the field first, so a truncated fan-out still covers the
        rows anyone reads) with at most ``_ENRICH_CONCURRENCY`` calls in
        flight.  The status call is issued only when the feed offered a
        status ref: stock-car and IndyCar competitors have none, and that
        endpoint answers 400 for them.  A per-car failure leaves that car
        with its tier-A facts alone — plus, for an F1 car whose status call
        is the half that failed, whatever its statistics can be trusted to
        say about a retirement (see ``_racing_render_outcome``).
        """
        targets = sorted(cars.items(), key=lambda item: item[1].order or _ORDER_LAST)
        targets = targets[:_ENRICH_MAX_CARS]
        semaphore = asyncio.Semaphore(_ENRICH_CONCURRENCY)

        async def fetch(
            athlete_id: str, car: _RacingCar
        ) -> tuple[str, _RacingCarStats | None, tuple[str, int | None]]:
            async with semaphore:
                stats_data = (
                    await self._racing_json(car.statistics_url, event_key)
                    if car.statistics_url is not None
                    else None
                )
                status_data = (
                    await self._racing_json(car.status_url, event_key)
                    if car.status_url is not None
                    else None
                )
            return (
                athlete_id,
                _parse_racing_stats(stats_data),
                _parse_racing_car_status(status_data),
            )

        fetched = await asyncio.gather(*(fetch(aid, car) for aid, car in targets))
        stats = {aid: parsed for aid, parsed, _ in fetched if parsed is not None}
        statuses = {aid: status for aid, _, status in fetched}
        return stats, statuses

    async def _racing_distance(
        self, league: League, event_key: str, competition_id: str
    ) -> int | None:
        """The race's scheduled lap count — one call per event, then memoized.

        The distance is a constant for the event's whole lifetime, so even a
        parse miss is remembered (a live race polls every few minutes); only
        a failed FETCH is left to retry next tick.
        """
        if event_key in self._racing_laps:
            return self._racing_laps[event_key]
        data = await self._racing_json(
            _racing_competition_statistics_url(league.provider_key, event_key, competition_id),
            event_key,
        )
        if data is None:
            return None
        laps = _racing_race_distance(data)
        self._memoize(self._racing_laps, event_key, laps)
        return laps

    async def _racing_json(self, url: str, event_key: str) -> dict[str, Any] | None:
        """A core-API GET for the racing enrichment; any failure yields None.

        Everything degrades here, including a transient outage that
        exhausted its retries: the board parsed from the site scoreboard is
        the product, and this is garnish that must never take it down or
        trip the breaker.  A 404 is the NORMAL answer for a canceled race
        and for one whose entry list is not published yet; a 200 carrying an
        HTML error page (``ValueError`` from the JSON decode) is how the
        core API answers its own outages.
        """
        try:
            return await self._get_json(url)
        except (httpx.HTTPError, TransientProviderError, ValueError) as exc:
            logger.warning(
                "ESPN racing enrichment call failed for event %s (%s): %s", event_key, url, exc
            )
            return None

    @staticmethod
    def _memoize(store: dict[str, Any], key: str, value: Any) -> None:
        """Store ``value`` in a bounded, insertion-ordered memo."""
        store[key] = value
        while len(store) > _ENRICH_MEMO_MAX:
            store.pop(next(iter(store)))

    async def get_game_state(self, league: League, provider_game_key: str) -> GameState | None:
        if league.sport in INDIVIDUAL_SPORTS:
            return await self._get_individual_game_state(league, provider_game_key)
        url = f"{_SITE_BASE}/{league.provider_key}/summary"
        try:
            data = await self._get_json(url, params={"event": provider_game_key})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return _parse_summary_state(data, league, provider_game_key)

    async def _get_individual_game_state(
        self, league: League, provider_game_key: str
    ) -> GameState | None:
        """State of one tennis match / UFC bout via the tour scoreboard.

        The ``/summary`` endpoint is unreliable for individual sports
        (returns an error body for a bout id, expects the parent event id
        for tennis), so the live tour/card scoreboard is scanned for the
        competition whose id matches ``provider_game_key``.  ``Game.state``
        is already populated by the scoreboard parse, so the matching
        game's state is returned directly.
        """
        target = f"espn:{provider_game_key}"
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        now = timeutil.utcnow().astimezone(_SCOREBOARD_TZ)
        dates = now.strftime("%Y%m") if league.sport is Sport.MMA else now.strftime("%Y%m%d")
        data = await self._get_json(url, params={"dates": dates, "limit": "400"})
        for game in _parse_individual_scoreboard(data, league):
            if game.id == target:
                return game.state
        return None

    async def get_game_summary(self, league: League, provider_game_key: str) -> GameSummary | None:
        """On-demand box score (period lines + performers) for one game.

        Reuses the same ``/summary`` endpoint as ``get_game_state`` and
        parses its header linescores + top-level ``leaders``.  Best-effort
        and never raises: individual sports (tennis/MMA/golf), whose
        ``/summary`` endpoint is unreliable, return ``None`` up front; any
        HTTP failure or unparseable body also degrades to ``None``.
        """
        if league.sport in INDIVIDUAL_SPORTS:
            # The summary endpoint is unreliable for individual sports (see
            # _get_individual_game_state); no box score is offered.
            return None
        url = f"{_SITE_BASE}/{league.provider_key}/summary"
        try:
            data = await self._get_json(url, params={"event": provider_game_key})
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "ESPN game summary call failed for game %s (league %s): %s",
                provider_game_key,
                league.id,
                exc,
            )
            return None
        return _parse_game_summary(data, league, provider_game_key)

    async def get_game_odds(self, league: League, provider_game_key: str) -> GameOdds | None:
        """On-demand betting lines + win-probability for one game.

        Odds come from the same ``/summary`` payload as the box score
        (``pickcenter``); win-probability from the core API's per-game
        ``predictor`` feed.  The two live on different hosts, so they're
        fetched concurrently and each half degrades independently — a normal
        predictor 404 (no projection for this game) still yields the odds,
        and vice-versa.  Individual sports return ``None`` up front; an
        all-empty result also collapses to ``None``.
        """
        if league.sport in INDIVIDUAL_SPORTS:
            return None
        summary_url = f"{_SITE_BASE}/{league.provider_key}/summary"
        predictor_url = (
            f"{_CORE_BASE}/{_core_event_path(league.provider_key)}"
            f"/events/{provider_game_key}/competitions/{provider_game_key}/predictor"
        )
        summary_data, predictor_data = await asyncio.gather(
            self._get_json(summary_url, params={"event": provider_game_key}),
            self._get_json(predictor_url),
            return_exceptions=True,
        )
        if isinstance(summary_data, dict):
            provider, details, home_ml, away_ml, spread, over_under = _parse_pickcenter(
                summary_data
            )
        else:
            if isinstance(summary_data, TransientProviderError):
                raise summary_data
            provider = details = home_ml = away_ml = spread = over_under = None
        if isinstance(predictor_data, dict):
            home_pct, away_pct = _parse_predictor(predictor_data)
        else:
            # A 404 (no projection) is expected and benign; let only a
            # transient error surface so the breaker can react.
            if isinstance(predictor_data, TransientProviderError):
                raise predictor_data
            home_pct = away_pct = None
        odds = GameOdds(
            provider=provider,
            details=details,
            home_moneyline=home_ml,
            away_moneyline=away_ml,
            spread=spread,
            over_under=over_under,
            home_win_pct=home_pct,
            away_win_pct=away_pct,
        )
        return odds if _odds_has_signal(odds) else None

    async def get_standings(self, league: League) -> Standings:
        if league.sport is Sport.TENNIS:
            # Tennis "standings" are the tour ranking (rank + points).
            url = f"{_SITE_BASE}/{league.provider_key}/rankings"
            data = await self._get_json(url)
            return _parse_tennis_rankings(data, league, self._known_teams.get(league.id))
        if league.sport is Sport.MMA:
            # MMA has no league table; expose an empty Standings.
            return Standings(
                league_id=league.id,
                season=str(timeutil.utcnow().year),
                rows=(),
                fetched_at=timeutil.utcnow(),
            )
        url = f"{_STANDINGS_BASE}/{league.provider_key}/standings"
        # ``level=3`` exposes division depth (conference -> division ->
        # entries) for NBA/MLB/NHL/NFL; single-table leagues (soccer)
        # ignore it and still return one flat child.
        data = await self._get_json(url, params={"level": "3"})
        return _parse_standings(data, league, self._known_teams.get(league.id))

    async def get_roster(
        self, league: League, team: Team, *, with_stat_lines: bool = True
    ) -> Roster:
        """A team's roster; ``with_stat_lines=False`` is the one-call variant.

        The stat-line pass below costs one athlete-overview request PER
        player, which the same-day pre-game re-sync runs far too often to
        afford — and it needs none of it: injury/scratch designations ride on
        the roster payload this single call already returns.
        """
        self._register(league, team)
        if league.sport in INDIVIDUAL_SPORTS:
            # An athlete is a single-member "team"; there is no roster.
            return Roster(team_id=team.id, players=(), fetched_at=timeutil.utcnow())
        url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/roster"
        data = await self._get_json(url)
        roster = _parse_roster(data, league, team)
        if not with_stat_lines:
            return roster
        return await self._attach_stat_lines(league, roster)

    async def _attach_stat_lines(self, league: League, roster: Roster) -> Roster:
        """Populate ``Player.stat_line`` + ``career_stat_line`` from overviews.

        One overview fetch per athlete yields both the current-season line and
        the career line (the payload carries "Regular Season" and "Career"
        splits side by side).  Best-effort and bounded: caps the athlete count
        and concurrency, and swallows every per-athlete failure (a missing line
        just stays ``None``).  Never raises.
        """
        overview_path = _overview_path(league.sport, league.provider_key)
        if overview_path is None or not roster.players:
            return roster

        targets = roster.players[:_STAT_LINE_MAX_ATHLETES]
        semaphore = asyncio.Semaphore(_STAT_LINE_CONCURRENCY)

        async def fetch(player: Player) -> tuple[str | None, str | None]:
            athlete_id = _athlete_id(player)
            if athlete_id is None:
                return None, None
            url = f"{_WEB_BASE}/{overview_path}/athletes/{athlete_id}/overview"
            async with semaphore:
                try:
                    data = await self._get_json(url)
                except (httpx.HTTPError, TransientProviderError, ValueError):
                    # Stat lines are best-effort garnish: a per-athlete blip
                    # (a transient outage, or a 200 whose body isn't JSON —
                    # ``json.JSONDecodeError`` is a ValueError) just leaves the
                    # line None and must not trip the breaker for the whole app.
                    return None, None
            return (
                _stat_line_from_overview(data, league.sport),
                _career_line_from_overview(data, league.sport),
            )

        lines = await asyncio.gather(*(fetch(player) for player in targets))
        by_id = {player.id: line for player, line in zip(targets, lines, strict=False)}

        def _apply(player: Player) -> Player:
            season, career = by_id.get(player.id, (None, None))
            if season is None and career is None:
                return player
            return replace(
                player,
                stat_line=season if season is not None else player.stat_line,
                career_stat_line=career if career is not None else player.career_stat_line,
            )

        players = tuple(_apply(player) for player in roster.players)
        return replace(roster, players=players)

    async def get_news(self, league: League, team: Team) -> list[NewsItem]:
        self._register(league, team)
        url = f"{_SITE_BASE}/{league.provider_key}/news"
        # Individual sports have no per-team news scope on this endpoint;
        # fetch league news best-effort (the news service still merges in
        # the athlete's Google News query).  Any failure yields no items.
        params = (
            {"limit": "20"}
            if league.sport in INDIVIDUAL_SPORTS
            else {"team": str(team.provider_key), "limit": "20"}
        )
        try:
            data = await self._get_json(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "ESPN news call failed for team %s (league %s): %s",
                team.id,
                league.id,
                exc,
            )
            return []
        return _parse_news(data, team=team)

    async def get_league_news(self, league: League) -> list[NewsItem]:
        """Competition-wide news for a whole-league (``follow_all``) follow.

        The same ``{provider_key}/news`` endpoint, called without a ``team``
        filter, returns the whole competition's articles.  Items carry
        ``league_id=league.id`` and ``team_id=None``.
        """
        url = f"{_SITE_BASE}/{league.provider_key}/news"
        try:
            data = await self._get_json(url, params={"limit": "50"})
        except httpx.HTTPError as exc:
            logger.warning("ESPN league news call failed for league %s: %s", league.id, exc)
            return []
        return _parse_news(data, league_id=league.id)

    async def get_team_location(self, league: League, team: Team) -> TeamLocation | None:
        """Home venue for the map view via ``/teams/{provider_key}``.

        Returns a ``TeamLocation`` carrying the venue name (plus city/state
        for geocode context) with ``lat``/``lon`` left ``None`` — ESPN does
        not expose usable coordinates on this endpoint, so the geocode
        service resolves them.  Never raises: an HTTP failure, an
        unparseable body, or a team with no venue all degrade to ``None``.
        """
        self._register(league, team)
        url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}"
        try:
            data = await self._get_json(url)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "ESPN team-location call failed for team %s (league %s): %s",
                team.id,
                league.id,
                exc,
            )
            return None
        return _parse_team_location(data, provider_key=str(team.provider_key))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
