"""Historical (past-season) fetches from ESPN's ``?season=`` parameters.

Standalone like ``espn_leaders``/``espn_playoffs``: these are on-demand
route helpers outside the ``SportsProvider`` protocol (CONTRACTS.md), so
adding history support never touches the provider interface.  Both
functions reuse the espn package's pure parsers.  A network/parse failure
logs and degrades: the standings fetch never raises (``None``, so the
history endpoint can 404 gracefully), and the results fetch raises only
:class:`EspnHistoryError`, which carries the finals it did get — a caller
shows them but must not keep them as the season.

Team sports only: tennis "standings" are a rolling tour ranking with no
archived season tables, MMA has no table at all, and golf is a
leaderboard sport — all return the empty result.
"""

from __future__ import annotations

import logging

import httpx

from app import useragent
from app.config import get_settings
from app.models.domain import (
    INDIVIDUAL_SPORTS,
    LEADERBOARD_SPORTS,
    Game,
    GamePhase,
    League,
    Sport,
    Standings,
    Team,
)
from app.providers.espn.common import _SITE_BASE, _STANDINGS_BASE
from app.providers.espn.games import _parse_schedule
from app.providers.espn.standings import _parse_standings
from app.providers.espn.summary import _SCHEDULE_PARAM_SETS, _merge_games
from app.providers.http_util import TransientProviderError, get_with_retry
from app.services import http_client

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0)
_HEADERS = useragent.headers()
# What a failed fetch raises: a status or transport error, an undecodable
# body, or retries that ran out.
_FETCH_ERRORS = (httpx.HTTPError, ValueError, TransientProviderError)


class EspnHistoryError(Exception):
    """A season fetch in which at least one schedule call failed.

    ``games`` holds the finals from the calls that did answer.  They are
    worth showing, but they are not the season — hockey, football and
    baseball split it into a regular-season call and a playoff call — so a
    caller must not cache them as one.
    """

    def __init__(self, games: list[Game]) -> None:
        super().__init__("ESPN season schedule fetch was incomplete")
        self.games = games


def supports_history(sport: Sport) -> bool:
    """Whether ESPN archives make sense for this sport (team sports only)."""
    return sport not in INDIVIDUAL_SPORTS and sport not in LEADERBOARD_SPORTS


async def _fetch_json(url: str, params: dict[str, str]) -> dict | None:
    """The decoded body, ``None`` for an empty one; raises on a failed fetch."""
    settings = get_settings()
    client = http_client.get_client("espn-history", timeout=_TIMEOUT, headers=_HEADERS)
    response = await get_with_retry(
        client,
        url,
        params=params,
        max_retries=settings.provider_max_retries,
        backoff_base=settings.provider_backoff_base,
        label="espn-history",
    )
    response.raise_for_status()
    if not response.content or not response.text.strip():
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def _get_json(url: str, params: dict[str, str]) -> dict | None:
    """:func:`_fetch_json`, with a failed fetch logged and turned into ``None``."""
    try:
        return await _fetch_json(url, params)
    except _FETCH_ERRORS:
        logger.warning("espn_history: fetch failed (%s %s)", url, params, exc_info=True)
        return None


async def fetch_season_standings(league: League, season: int) -> Standings | None:
    """One past season's final table; ``None`` when unavailable.

    ``season`` is ESPN's numeric season key — the ENDING year for
    cross-year seasons (2026 -> the 2025-26 NBA season).  Archive rows
    carry no followed-team tags (a past table doesn't reference the
    current follow set).
    """
    if not supports_history(league.sport):
        return None
    url = f"{_STANDINGS_BASE}/{league.provider_key}/standings"
    # level=3 exposes division depth, exactly like the live fetch.
    data = await _get_json(url, {"level": "3", "season": str(season)})
    if data is None:
        return None
    standings = _parse_standings(data, league)
    return standings if standings.rows else None


async def fetch_season_results(league: League, team: Team, season: int) -> list[Game]:
    """A team's FINAL games from one past season, newest first; ``[]`` on miss.

    Mirrors the live schedule fetch (including the per-sport param sets
    of ``_SCHEDULE_PARAM_SETS`` — e.g. hockey, football and baseball merge
    regular season + postseason; a sport with no entry there makes one
    bare call) with ESPN's ``season`` parameter appended to every call.

    Raises :class:`EspnHistoryError` when any of those calls failed, with
    the finals the others returned on it: half a season must not pass for
    the whole one.  A call that ANSWERED with nothing is not a failure.
    """
    if not supports_history(league.sport):
        return []
    url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/schedule"
    param_sets = _SCHEDULE_PARAM_SETS.get(league.sport) or (None,)
    batches: list[list[Game]] = []
    failed = False
    for params in param_sets:
        merged = dict(params or {})
        merged["season"] = str(season)
        try:
            data = await _fetch_json(url, merged)
        except _FETCH_ERRORS:
            logger.warning("espn_history: fetch failed (%s %s)", url, merged, exc_info=True)
            failed = True
            continue
        if data is None:
            continue
        batches.append(_parse_schedule(data, league, team))
    games = _merge_games(*batches)
    finals = sorted(
        (game for game in games if game.state is not None and game.state.phase is GamePhase.FINAL),
        key=lambda game: game.start_time,
        reverse=True,
    )
    if failed:
        raise EspnHistoryError(finals)
    return finals
