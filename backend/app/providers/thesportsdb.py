"""TheSportsDB provider adapter (volleyball — the app's second provider).

Maps TheSportsDB's free-tier (API key ``"3"``) JSON payloads into the
normalized domain models.  Like :mod:`app.providers.espn`, every parser is
a pure function over already-fetched JSON so the normalization logic is
unit-testable without any network I/O; the async protocol methods only
fetch and delegate.

``League.provider_key`` is the TheSportsDB league id (e.g. ``"5613"`` for
the Men's European Volleyball Championship); ``Team.provider_key`` is the
TheSportsDB team id.

Volleyball is two-sided and SET-scored, so it reuses the :class:`Game`
model (no new domain object):

* ``home_score`` / ``away_score`` are SETS WON (e.g. 3-1), taken from
  ``intHomeScore`` / ``intAwayScore``;
* ``period`` is the current set and ``period_label`` is ``"Set {n}"``;
* there is no game clock (``clock`` is always ``None``).

TIME ASSUMPTION: TheSportsDB ships ``dateEvent`` + ``strTime`` (and a
matching ``strTimestamp``) with no timezone.  The free tier's timestamps
are effectively UTC, so they are parsed AS UTC (documented here so the
assumption is auditable; the ``*Local`` fields are ignored).

DEFENSIVE / NEVER-RAISE: the free tier is sparse and aggressively
rate-limited — a 429 comes back as an HTML body (not JSON), and many
leagues legitimately have empty schedules/standings/rosters.  Every
network call is wrapped so a failure (HTTP error, non-JSON body, timeout)
is logged and degrades to an empty/None result rather than raising out;
the scheduler relies on this never-raise contract.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, time
from typing import Any

import httpx

from app import timeutil
from app.config import get_settings
from app.providers.http_util import get_with_retry
from app.services import tsdb_client
from app.models.domain import (
    Event,
    Game,
    GameOdds,
    GamePhase,
    GameState,
    GameSummary,
    League,
    NewsItem,
    Player,
    PlayerStatus,
    Roster,
    StandingRow,
    Standings,
    Team,
    TeamLocation,
)

logger = logging.getLogger(__name__)

# Status strings TheSportsDB reports for a finished match.  ``strStatus``
# is usually "FT"; ``strProgress`` / longer phrasings ("Match Finished",
# "Finished", "FT") are accepted too, case-insensitively.
_FINAL_TOKENS = ("FT", "MATCH FINISHED", "FINISHED", "AET", "FINAL")
# Not-started markers.  "NS" is the canonical one; longer phrasings are
# tolerated.
_SCHEDULED_TOKENS = ("NS", "NOT STARTED", "SCHEDULED", "TBD", "PST", "POSTP")


# ---------------------------------------------------------------------------
# Low-level coercion helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int | None:
    """Best-effort int from TheSportsDB's string-encoded numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _clean_str(value: Any) -> str | None:
    """A non-empty trimmed string, or None."""
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def _parse_event_datetime(event: dict[str, Any]) -> datetime | None:
    """Start time from ``dateEvent`` + ``strTime`` (treated as UTC).

    Prefers the combined ``dateEvent``/``strTime`` pair; falls back to the
    ISO-ish ``strTimestamp`` (also tz-less, also read as UTC).  A missing
    time defaults to midnight.  Returns ``None`` when no usable date is
    present.
    """
    day_text = _clean_str(event.get("dateEvent"))
    if day_text:
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            day = None
        if day is not None:
            clock = _parse_clock(_clean_str(event.get("strTime")))
            return timeutil.ensure_utc(datetime.combine(day, clock))
    timestamp = _clean_str(event.get("strTimestamp"))
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        return timeutil.ensure_utc(parsed)
    return None


def _parse_clock(text: str | None) -> time:
    """Parse ``"18:00:00"`` / ``"18:00"`` into a ``time`` (midnight on failure)."""
    if not text:
        return time(0, 0)
    # TheSportsDB occasionally suffixes a "+00:00"; strip anything after a space.
    cleaned = text.split("+", 1)[0].strip()
    parts = cleaned.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        return time(hour % 24, minute % 60, second % 60)
    except (ValueError, IndexError):
        return time(0, 0)


# ---------------------------------------------------------------------------
# Status / phase normalization
# ---------------------------------------------------------------------------


def _map_phase(event: dict[str, Any]) -> GamePhase:
    """Map ``strStatus`` / ``strProgress`` onto a :class:`GamePhase`.

    ``FT`` / "Match Finished" -> final; ``NS`` -> scheduled; anything else
    (a live progress marker, a set number, …) is treated as in progress.
    Defensive: an empty/unknown status falls back to scheduled if no score
    is present, else final (a scored match with no live marker has ended).
    """
    status = (_clean_str(event.get("strStatus")) or "").upper()
    progress = (_clean_str(event.get("strProgress")) or "").upper()
    combined = f"{status} {progress}".strip()

    for token in _FINAL_TOKENS:
        if token in combined:
            return GamePhase.FINAL
    for token in _SCHEDULED_TOKENS:
        if token in combined:
            return GamePhase.SCHEDULED
    if combined:
        # A non-empty status that is neither final nor scheduled is a live
        # marker (a set number, "Set 3", "LIVE", …).
        return GamePhase.IN_PROGRESS
    # No status at all: infer from whether the match has a recorded score.
    if (
        _coerce_int(event.get("intHomeScore")) is not None
        or _coerce_int(event.get("intAwayScore")) is not None
    ):
        return GamePhase.FINAL
    return GamePhase.SCHEDULED


def _current_set(event: dict[str, Any], phase: GamePhase, home: int, away: int) -> int:
    """The current set number (``period``).

    Before tip-off the period is 0 (per the domain contract).  Otherwise
    the sets already won by both sides imply the set in play: at 2-1 the
    fourth set (``home + away + 1``) is live; at the final whistle the last
    decided set (``home + away``) is reported.  ``strProgress`` is used when
    it carries an explicit set number.
    """
    if phase is GamePhase.SCHEDULED:
        return 0
    decided = home + away
    if phase is GamePhase.FINAL:
        return decided if decided > 0 else 0
    # In progress: a set is underway beyond those already decided.  Honor an
    # explicit "Set N" marker if TheSportsDB provides one.
    explicit = _coerce_int(_clean_str(event.get("strProgress")))
    if explicit and explicit > 0:
        return explicit
    return decided + 1


def _build_state(game_id: str, event: dict[str, Any]) -> GameState:
    """Build a normalized volleyball :class:`GameState` from an event dict.

    Scores are sets won; ``period_label`` is ``"Set {n}"``; there is never
    a clock for volleyball.
    """
    phase = _map_phase(event)
    home = _coerce_int(event.get("intHomeScore")) or 0
    away = _coerce_int(event.get("intAwayScore")) or 0
    period = _current_set(event, phase, home, away)
    label = f"Set {period}" if period > 0 else ""
    return GameState(
        game_id=game_id,
        phase=phase,
        home_score=home,
        away_score=away,
        period=period,
        period_label=label,
        clock=None,
        is_intermission=False,
        last_update=timeutil.utcnow(),
    )


# ---------------------------------------------------------------------------
# Pure parsers (take already-fetched JSON, never raise on bad records)
# ---------------------------------------------------------------------------


def _parse_event(event: Any, league: League, team: Team | None = None) -> Game | None:
    """Parse a single TheSportsDB event; None (+warning) if malformed."""
    try:
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        event_id = _clean_str(event.get("idEvent"))
        if not event_id:
            raise ValueError("missing idEvent")
        home_name = _clean_str(event.get("strHomeTeam"))
        away_name = _clean_str(event.get("strAwayTeam"))
        if not home_name or not away_name:
            raise ValueError("missing team names")
        start_time = _parse_event_datetime(event)
        if start_time is None:
            raise ValueError("missing or invalid start time")

        game_id = f"thesportsdb:{event_id}"
        state = _build_state(game_id, event)

        venue = _clean_str(event.get("strVenue"))

        home_team_id: str | None = None
        away_team_id: str | None = None
        if team is not None:
            key = str(team.provider_key)
            if _clean_str(event.get("idHomeTeam")) == key:
                home_team_id = team.id
            elif _clean_str(event.get("idAwayTeam")) == key:
                away_team_id = team.id

        return Game(
            id=game_id,
            league_id=league.id,
            home_name=home_name,
            away_name=away_name,
            start_time=start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            venue=venue,
            state=state,
        )
    except Exception:
        logger.warning(
            "Skipping malformed TheSportsDB event for league %s", league.id, exc_info=True
        )
        return None


def _parse_events(data: Any, league: League, team: Team | None = None) -> list[Game]:
    """Parse an ``events`` list (events*league.php) into Games.

    A missing/null ``events`` key (an empty league window) yields ``[]``.
    """
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    return [game for event in events if (game := _parse_event(event, league, team)) is not None]


def _games_for_team(games: list[Game], team: Team) -> list[Game]:
    """Keep only games where ``team`` is the home or away side.

    Matching is by the internal team id (set on the matching side during
    parsing) so it tolerates name changes / aliases.
    """
    return [game for game in games if game.home_team_id == team.id or game.away_team_id == team.id]


def _parse_standing_row(
    entry: Any, league: League, team: Team | None = None
) -> tuple[int, StandingRow] | None:
    """Parse one ``table`` entry into ``(provider_rank, row)``; None if bad."""
    try:
        if not isinstance(entry, dict):
            raise ValueError("standings entry is not an object")
        name = _clean_str(entry.get("strTeam"))
        if not name:
            raise ValueError("missing team name")
        rank = _coerce_int(entry.get("intRank")) or 0
        wins = _coerce_int(entry.get("intWin")) or 0
        losses = _coerce_int(entry.get("intLoss")) or 0
        points = _coerce_int(entry.get("intPoints"))
        internal_id: str | None = None
        if team is not None and _clean_str(entry.get("idTeam")) == str(team.provider_key):
            internal_id = team.id
        row = StandingRow(
            rank=0,
            team_name=name,
            wins=wins,
            losses=losses,
            team_id=internal_id,
            points=points,
            group=None,
        )
        return rank, row
    except Exception:
        logger.warning(
            "Skipping malformed TheSportsDB standings entry for league %s",
            league.id,
            exc_info=True,
        )
        return None


def _parse_standings(data: Any, league: League, season: str, team: Team | None = None) -> Standings:
    """Parse a ``lookuptable.php`` payload into :class:`Standings`.

    Volleyball tables are flat (no conferences), so ``group`` is always
    ``None``.  Rows are ordered by the provider rank (then by wins); a
    missing/empty ``table`` (the common sparse-free-tier case) yields an
    empty Standings.
    """
    table = data.get("table") if isinstance(data, dict) else None
    parsed: list[tuple[int, StandingRow]] = []
    if isinstance(table, list):
        for entry in table:
            result = _parse_standing_row(entry, league, team)
            if result is not None:
                parsed.append(result)
    parsed.sort(key=lambda item: (item[0] <= 0, item[0], -item[1].wins, item[1].team_name))
    rows = tuple(replace(row, rank=index + 1) for index, (_, row) in enumerate(parsed))
    return Standings(
        league_id=league.id,
        season=season,
        rows=rows,
        fetched_at=timeutil.utcnow(),
    )


def _parse_player_status(text: str | None) -> PlayerStatus:
    """Map TheSportsDB's ``strStatus`` onto :class:`PlayerStatus`."""
    lowered = (text or "").lower()
    if not lowered or lowered == "active":
        return PlayerStatus.ACTIVE
    if "out" in lowered or "injur" in lowered or "reserve" in lowered:
        return PlayerStatus.OUT
    if "day" in lowered or "questionable" in lowered or "doubt" in lowered:
        return PlayerStatus.DAY_TO_DAY
    return PlayerStatus.ACTIVE


def _parse_player(player: Any, team: Team) -> Player | None:
    """Parse one ``player`` entry into a :class:`Player`; None if malformed."""
    try:
        if not isinstance(player, dict):
            raise ValueError("player is not an object")
        player_id = _clean_str(player.get("idPlayer"))
        name = _clean_str(player.get("strPlayer"))
        if not player_id or not name:
            raise ValueError("missing player id or name")
        status_text = _clean_str(player.get("strStatus"))
        status = _parse_player_status(status_text)
        # Cutout (transparent headshot) reads best in the roster; fall back to
        # the square thumb, then the action render.
        photo_url = (
            _clean_str(player.get("strCutout"))
            or _clean_str(player.get("strThumb"))
            or _clean_str(player.get("strRender"))
        )
        return Player(
            id=f"thesportsdb:{player_id}",
            team_id=team.id,
            name=name,
            position=_clean_str(player.get("strPosition")),
            jersey_number=_clean_str(player.get("strNumber")),
            status=status,
            status_detail=status_text if status is not PlayerStatus.ACTIVE else None,
            photo_url=photo_url,
        )
    except Exception:
        logger.warning("Skipping malformed TheSportsDB player for team %s", team.id, exc_info=True)
        return None


def _parse_roster(data: Any, team: Team) -> Roster:
    """Parse a ``lookup_all_players.php`` payload into a :class:`Roster`.

    A missing/null ``player`` list (the common empty national-team case)
    yields an empty roster — never a crash.
    """
    players_raw = data.get("player") if isinstance(data, dict) else None
    players: list[Player] = []
    if isinstance(players_raw, list):
        for entry in players_raw:
            player = _parse_player(entry, team)
            if player is not None:
                players.append(player)
    return Roster(team_id=team.id, players=tuple(players), fetched_at=timeutil.utcnow())


def _resolve_season(league: League, data: Any) -> str:
    """Best season label for a standings call.

    Prefers a season carried by the payload, else the current year.
    """
    if isinstance(data, dict):
        table = data.get("table")
        if isinstance(table, list) and table and isinstance(table[0], dict):
            season = _clean_str(table[0].get("strSeason"))
            if season:
                return season
    return str(timeutil.utcnow().year)


def _coerce_coord(value: Any) -> float | None:
    """Best-effort float coordinate; rejects the out-of-range 0/empty sentinel.

    TheSportsDB encodes coordinates (when present at all) as strings and
    uses ``"0"`` / ``""`` as a "no data" placeholder, so a literal zero is
    treated as missing rather than as the Gulf-of-Guinea null island.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number == 0.0 or not (-90.0 <= number <= 180.0):
        return None
    return number


def _parse_team_location(data: Any) -> TeamLocation | None:
    """Home venue (+ any coordinates) from a ``lookupteam.php`` payload.

    TheSportsDB's free tier ships a stadium NAME (``strStadium``) and a
    free-text location string (``strLocation`` — usually "Area, City,
    Country") that gives the geocoder useful context, but no coordinates.
    Older/paid schemas occasionally include ``strStadiumLocation`` and
    latitude/longitude fields, so all are read defensively: when lat/lon
    are present they are returned, otherwise the venue name (plus the
    location string for geocode context) comes back with ``None`` coords.
    Returns ``None`` when the team carries no venue at all.
    """
    teams = data.get("teams") if isinstance(data, dict) else None
    if not isinstance(teams, list) or not teams or not isinstance(teams[0], dict):
        return None
    team = teams[0]
    name = _clean_str(team.get("strStadium")) or _clean_str(team.get("strStadiumLocation"))
    location = _clean_str(team.get("strLocation"))
    if name is None and location is None:
        return None
    # Build a geocodable venue string: the stadium name, enriched with the
    # location text when both are present (e.g. "Emirates Stadium,
    # Holloway, London, England").  When only one is known — or the two are
    # the same string — use it alone.
    if name and location and name.casefold() != location.casefold():
        venue: str | None = f"{name}, {location}"
    else:
        venue = name or location
    lat = _coerce_coord(team.get("strStadiumLat") or team.get("intStadiumLat"))
    lon = _coerce_coord(team.get("strStadiumLng") or team.get("intStadiumLng"))
    return TeamLocation(venue=venue, lat=lat, lon=lon)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class TheSportsDbProvider:
    """``SportsProvider`` adapter for TheSportsDB's free-tier API (volleyball).

    Every network method degrades to an empty/None result on any failure
    (HTTP error, non-JSON 429 body, timeout) — the scheduler depends on
    the provider never raising out.
    """

    provider_id = "thesportsdb"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=tsdb_client.TSDB_BASE_URL,
                timeout=httpx.Timeout(get_settings().provider_timeout_seconds),
                headers={"User-Agent": "SportsDash/1.0"},
                follow_redirects=True,
            )
        return self._client

    async def _get_json(
        self, endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Fetch + JSON-decode one endpoint, returning None on any failure.

        The free tier answers a rate-limited request with an HTML body
        (HTTP 429) and an empty body for some empty results, so JSON
        decoding is guarded; every failure is logged and returns None so
        callers can degrade to an empty/None result without raising.
        Transient errors (timeouts, 429/5xx) are retried with backoff first.
        """
        settings = get_settings()
        try:
            # Take the process-wide TSDB pacing slot — this provider shares
            # the free key with the catalog/stadium/photo lookups.
            await tsdb_client.acquire_slot()
            response = await get_with_retry(
                self._get_client(),
                endpoint,
                params=params,
                max_retries=settings.provider_max_retries,
                backoff_base=settings.provider_backoff_base,
                label="thesportsdb",
            )
            response.raise_for_status()
            if not response.content or not response.text.strip():
                return None
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("TheSportsDB request failed (%s params=%s): %s", endpoint, params, exc)
            return None
        return data if isinstance(data, dict) else None

    async def get_schedule(self, league: League, team: Team, start: date, end: date) -> list[Game]:
        """``team``'s games in ``[start, end]`` from the league's past + next feeds.

        Both ``eventspastleague`` (completed) and ``eventsnextleague``
        (upcoming) are fetched and merged, then filtered to the team and
        window.  Either call failing is tolerated — a half schedule beats
        none — and both failing simply yields ``[]`` (never raises).
        """
        params = {"id": str(league.provider_key)}
        past = await self._get_json("eventspastleague.php", params)
        upcoming = await self._get_json("eventsnextleague.php", params)
        games: dict[str, Game] = {}
        for data in (past, upcoming):
            if data is None:
                continue
            for game in _parse_events(data, league, team):
                games.setdefault(game.id, game)
        in_window = [
            game
            for game in _games_for_team(list(games.values()), team)
            if start <= game.start_time.date() <= end
        ]
        return sorted(in_window, key=lambda game: game.start_time)

    async def get_live_games(self, league: League) -> list[Game]:
        """Today's events for the league, each with a populated state.

        The free tier is largely finals-after-the-fact, so this scans the
        same past + next feeds and keeps games starting on the current UTC
        day; whatever exists comes back with its parsed state (often a
        final).  Returns ``[]`` on failure.
        """
        today = timeutil.utcnow().date()
        params = {"id": str(league.provider_key)}
        past = await self._get_json("eventspastleague.php", params)
        upcoming = await self._get_json("eventsnextleague.php", params)
        games: dict[str, Game] = {}
        for data in (past, upcoming):
            if data is None:
                continue
            for game in _parse_events(data, league):
                games.setdefault(game.id, game)
        return [game for game in games.values() if game.start_time.date() == today]

    async def get_competition_schedule(self, league: League, start: date, end: date) -> list[Game]:
        """Whole-competition follows are not supported here; return ``[]``.

        Volleyball leagues are followed by team, not whole-competition, and
        the free tier has no bulk-by-range endpoint beyond the past/next
        feeds already used by :meth:`get_schedule`.
        """
        return []

    async def get_game_state(self, league: League, provider_game_key: str) -> GameState | None:
        """Current state of a single event via ``lookupevent.php``.

        Returns None when the event is unknown or the call fails.
        """
        data = await self._get_json("lookupevent.php", {"id": str(provider_game_key)})
        if data is None:
            return None
        events = data.get("events")
        if not isinstance(events, list) or not events or not isinstance(events[0], dict):
            return None
        return _build_state(f"thesportsdb:{provider_game_key}", events[0])

    async def get_game_summary(self, league: League, provider_game_key: str) -> GameSummary | None:
        """No box-score drill-down on the free tier; always ``None``.

        TheSportsDB's free tier exposes no per-period / per-performer box
        score for volleyball, so the on-demand game detail is unavailable
        here (the route degrades to showing the game's state only).
        """
        return None

    async def get_game_odds(self, league: League, provider_game_key: str) -> GameOdds | None:
        """No betting lines / win-probability on the free tier; always ``None``."""
        return None

    async def get_standings(self, league: League) -> Standings:
        """League table via ``lookuptable.php`` (W/L/points, ``group=None``).

        A sparse/empty table (the common free-tier case) or a failed call
        yields an empty Standings — never a crash.
        """
        params = {"l": str(league.provider_key)}
        season = self._season_hint(league)
        if season:
            params["s"] = season
        data = await self._get_json("lookuptable.php", params)
        if data is None:
            return Standings(
                league_id=league.id,
                season=season or str(timeutil.utcnow().year),
                rows=(),
                fetched_at=timeutil.utcnow(),
            )
        resolved_season = season or _resolve_season(league, data)
        return _parse_standings(data, league, resolved_season)

    @staticmethod
    def _season_hint(league: League) -> str:
        """A best-effort season string for the standings/table call.

        TheSportsDB ``lookuptable.php`` wants an ``s=`` season; without it
        many tables return nothing.  There is no per-league season in the
        domain model, so the current calendar year is used (the provider
        still degrades gracefully when the guess is wrong — an empty table
        just yields empty Standings).
        """
        return str(timeutil.utcnow().year)

    async def get_roster(self, league: League, team: Team) -> Roster:
        """Team roster via ``lookup_all_players.php``.

        National-team rosters are often empty on the free tier; an empty or
        failed response yields an empty Roster, never a crash.
        """
        data = await self._get_json("lookup_all_players.php", {"id": str(team.provider_key)})
        if data is None:
            return Roster(team_id=team.id, players=(), fetched_at=timeutil.utcnow())
        return _parse_roster(data, team)

    async def get_team_location(self, league: League, team: Team) -> TeamLocation | None:
        """Home venue (+ any coordinates) via ``lookupteam.php``.

        Returns the stadium name plus a location string for geocode context
        (the free tier carries no coordinates, so ``lat``/``lon`` are
        usually ``None`` and the geocode service resolves them).  Never
        raises: a failed/empty/coord-less response degrades to ``None`` or a
        venue-name-only ``TeamLocation``.
        """
        data = await self._get_json("lookupteam.php", {"id": str(team.provider_key)})
        if data is None:
            return None
        return _parse_team_location(data)

    async def get_events(self, league: League, start: date, end: date) -> list[Event]:
        """Volleyball is not a leaderboard sport; no Events."""
        return []

    async def get_event_state(self, league: League, provider_event_key: str) -> Event | None:
        """Volleyball is not a leaderboard sport; no Event state."""
        return None

    async def get_news(self, league: League, team: Team) -> list[NewsItem]:
        """No provider news source; the news service covers volleyball via
        Google News (Phase 1, ``news_locale``)."""
        return []

    async def get_league_news(self, league: League) -> list[NewsItem]:
        """No provider league news source."""
        return []

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
