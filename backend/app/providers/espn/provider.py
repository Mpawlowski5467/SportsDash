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
    Game,
    GameOdds,
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
    _SCOREBOARD_TZ,
    _SITE_BASE,
    _STANDINGS_BASE,
    _STAT_LINE_CONCURRENCY,
    _STAT_LINE_MAX_ATHLETES,
    _WEB_BASE,
)
from app.providers.espn.games import _parse_schedule, _parse_scoreboard
from app.providers.espn.golf import _event_overlaps_window, _parse_golf_scoreboard
from app.providers.espn.individual import _games_for_athlete, _parse_individual_scoreboard
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
        """
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Game]] = []
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
        if not batches and errors:
            raise errors[0]
        return _games_for_athlete(_merge_games(*batches), team)

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

    async def get_events(self, league: League, start: date, end: date) -> list[Event]:
        """Leaderboard tournaments in ``[start, end]`` for a golf league.

        Golf is the only leaderboard sport today; every other sport returns
        ``[]``.  ESPN's golf scoreboard exposes the current/most-recent
        tournament (and the season calendar around it) — a single
        ``?dates=YYYYMMDD-YYYYMMDD`` call covers the window (chunked by
        month for long ranges, like the competition scoreboard), and each
        tournament becomes one :class:`Event` with a populated leaderboard.
        Each :class:`LeaderRow` carries the golfer's ESPN athlete id in
        ``player_id`` TRANSIENTLY; the scheduler rewrites it to the internal
        followed-team id (or None) before persisting.  Tournaments are kept
        when their span overlaps ``[start, end]`` (a multi-day event that
        merely brackets the window still counts).
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return []
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        batches: list[list[Event]] = []
        errors: list[Exception] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end):
            dates = f"{chunk_start.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
            try:
                data = await self._get_json(url, params={"dates": dates, "limit": "400"})
            except Exception as exc:
                logger.warning(
                    "ESPN golf scoreboard call failed for league %s (dates=%s): %s",
                    league.id,
                    dates,
                    exc,
                )
                errors.append(exc)
                continue
            batches.append(_parse_golf_scoreboard(data, league))
        if not batches and errors:
            raise errors[0]
        # Merge by event id (first batch wins on duplicates) and keep any
        # tournament whose [start_time, end_time] span overlaps the window.
        merged: dict[str, Event] = {}
        for batch in batches:
            for event in batch:
                merged.setdefault(event.id, event)
        kept = [event for event in merged.values() if _event_overlaps_window(event, start, end)]
        return sorted(kept, key=lambda event: event.start_time)

    async def get_event_state(self, league: League, provider_event_key: str) -> Event | None:
        """Current state of a single golf tournament, or None if unknown.

        Golf's ``/summary`` endpoint is unreliable (returns a non-JSON error
        body for an event id — verified live), so the current scoreboard is
        scanned for the tournament whose id matches ``provider_event_key``.
        Non-leaderboard leagues always return None.
        """
        if league.sport not in LEADERBOARD_SPORTS:
            return None
        target = f"espn:{provider_event_key}"
        url = f"{_SITE_BASE}/{league.provider_key}/scoreboard"
        data = await self._get_json(url, params={"limit": "400"})
        for event in _parse_golf_scoreboard(data, league):
            if event.id == target:
                return event
        return None

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

    async def get_roster(self, league: League, team: Team) -> Roster:
        self._register(league, team)
        if league.sport in INDIVIDUAL_SPORTS:
            # An athlete is a single-member "team"; there is no roster.
            return Roster(team_id=team.id, players=(), fetched_at=timeutil.utcnow())
        url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/roster"
        data = await self._get_json(url)
        roster = _parse_roster(data, league, team)
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
                except (httpx.HTTPError, TransientProviderError):
                    # Stat lines are best-effort garnish: a per-athlete blip
                    # (incl. a transient outage) just leaves the line None and
                    # must not trip the provider breaker for the whole app.
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
