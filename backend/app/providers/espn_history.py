"""Historical (past-season) fetches from ESPN's ``?season=`` parameters.

Standalone like ``espn_leaders``/``espn_playoffs``: these are on-demand
route helpers outside the ``SportsProvider`` protocol (CONTRACTS.md), so
adding history support never touches the provider interface.  Both
functions reuse the espn package's pure parsers and NEVER raise — a
network/parse failure logs and degrades (``None`` / ``[]``) so the
history endpoints can 404 gracefully.

Team sports only: tennis "standings" are a rolling tour ranking with no
archived season tables, MMA has no table at all, and golf is a
leaderboard sport — all return the empty result.
"""

from __future__ import annotations

import logging

import httpx

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
_HEADERS = {"User-Agent": "SportsDash/1.0"}


def supports_history(sport: Sport) -> bool:
    """Whether ESPN archives make sense for this sport (team sports only)."""
    return sport not in INDIVIDUAL_SPORTS and sport not in LEADERBOARD_SPORTS


async def _get_json(url: str, params: dict[str, str]) -> dict | None:
    settings = get_settings()
    client = http_client.get_client("espn-history", timeout=_TIMEOUT, headers=_HEADERS)
    try:
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
    except (httpx.HTTPError, ValueError, TransientProviderError):
        logger.warning("espn_history: fetch failed (%s %s)", url, params, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


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

    Mirrors the live schedule fetch (including the per-sport seasontype
    param sets — e.g. baseball merges regular season + postseason) with
    ESPN's ``season`` parameter appended to every call.
    """
    if not supports_history(league.sport):
        return []
    url = f"{_SITE_BASE}/{league.provider_key}/teams/{team.provider_key}/schedule"
    param_sets = _SCHEDULE_PARAM_SETS.get(league.sport) or (None,)
    batches: list[list[Game]] = []
    for params in param_sets:
        merged = dict(params or {})
        merged["season"] = str(season)
        data = await _get_json(url, merged)
        if data is None:
            continue
        batches.append(_parse_schedule(data, league, team))
    games = _merge_games(*batches)
    finals = [
        game for game in games if game.state is not None and game.state.phase is GamePhase.FINAL
    ]
    return sorted(finals, key=lambda game: game.start_time, reverse=True)
