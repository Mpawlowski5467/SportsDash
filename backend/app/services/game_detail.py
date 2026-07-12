"""On-demand, best-effort per-game enrichments (box score, odds, weather,
lineups).

Shared by the games, matchup, odds, and schedule routes — these helpers
used to live as underscore-privates in ``routes/games.py`` with the
sibling routes importing them across route modules, which inverted the
routes → services layering.

Every fetch here is strictly additive to whatever endpoint calls it: an
unknown provider, a provider with no data for the game, or any exception
collapses to ``None`` so a detail endpoint never 500s because of an
enrichment lookup.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import domain
from app.models.domain import GameLineup, TeamLineup
from app.models.orm import GameORM
from app.providers import registry
from app.schemas import GameLineupOut, GameOddsOut, GameSummaryOut, WeatherOut
from app.services import lineup as lineup_service
from app.services import repository, wc_venues, weather
from app.services.serialize import (
    lineup_to_out,
    odds_to_out,
    summary_to_out,
    weather_to_out,
)
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)


def provider_game_key(game_id: str) -> str:
    """Strip the ``"<provider>:"`` prefix from an internal game id."""
    return game_id.split(":", 1)[1] if ":" in game_id else game_id


async def fetch_summary(league: domain.League, game_id: str) -> GameSummaryOut | None:
    """Resolve the league's provider and fetch the box score on demand.

    Best-effort: an unknown provider, a provider that has no summary for
    the game, or any exception during the fetch all collapse to ``None``
    so the detail endpoint never fails because of the summary.
    """
    try:
        provider = registry.get_provider(league.provider)
    except KeyError:
        logger.warning(
            "No provider %r registered for league %s; summary omitted",
            league.provider,
            league.id,
        )
        return None
    try:
        summary = await provider.get_game_summary(league, provider_game_key(game_id))
    except Exception:
        logger.exception("Provider %r failed to summarize game %s", league.provider, game_id)
        return None
    if summary is None:
        return None
    return summary_to_out(summary)


async def fetch_odds(league: domain.League, game_id: str) -> GameOddsOut | None:
    """Resolve the league's provider and fetch betting lines + win-prob.

    Best-effort, exactly like :func:`fetch_summary`: an unknown provider, a
    provider with no odds for the game, or any exception all collapse to
    ``None`` so the detail endpoint never fails because of the odds lookup.
    """
    try:
        provider = registry.get_provider(league.provider)
    except KeyError:
        return None
    try:
        odds = await provider.get_game_odds(league, provider_game_key(game_id))
    except Exception:
        logger.exception("Provider %r failed to price game %s", league.provider, game_id)
        return None
    if odds is None:
        return None
    return odds_to_out(odds)


async def _home_venue_coords(session: AsyncSession, row: GameORM) -> tuple[float, float] | None:
    """Resolve the home venue's coordinates for a weather lookup.

    A followed home team carries resolved stadium coordinates on its row; a
    whole-competition home (no followed-team row) may instead play at a known
    host venue (World Cup table).  ``None`` when neither resolves.
    """
    if row.home_team_id is not None:
        team = await repository.get_team(session, row.home_team_id)
        if team is not None and team.venue_lat is not None and team.venue_lon is not None:
            return team.venue_lat, team.venue_lon
    host = wc_venues.resolve(row.venue)
    if host is not None:
        return host.lat, host.lon
    return None


async def fetch_weather(
    session: AsyncSession, league: domain.League, row: GameORM
) -> WeatherOut | None:
    """Best-effort venue forecast for an outdoor SCHEDULED game.

    Gated to outdoor sports and scheduled games (a pre-game artifact); skips
    when weather is disabled or no coordinates resolve.  Never raises — any
    failure collapses to ``None`` so the detail endpoint can't 500 on it.
    """
    if not get_settings().weather_enabled:
        return None
    if league.sport not in domain.WEATHER_SPORTS:
        return None
    if row.phase != domain.GamePhase.SCHEDULED.value:
        return None
    coords = await _home_venue_coords(session, row)
    if coords is None:
        return None
    # Forecast for the game's own UTC day, not today's. ensure_utc is required
    # because SQLite returns the tz-aware start_time as a naive datetime.
    target_date = ensure_utc(row.start_time).date()
    try:
        result = await weather.fetch(*coords, target_date=target_date)
    except Exception:
        logger.exception("weather fetch failed for game %s", row.id)
        return None
    return weather_to_out(result) if result is not None else None


async def _team_lineup(
    session: AsyncSession, sport: domain.Sport, team_id: str | None
) -> TeamLineup | None:
    """Roster-derived lineup for one side, or None when it has no stored roster."""
    if team_id is None:
        return None
    players = await repository.get_roster(session, team_id)
    if not players:
        return None
    return lineup_service.build_team_lineup(sport, players)


async def fetch_lineup(
    session: AsyncSession, league: domain.League, row: GameORM
) -> GameLineupOut | None:
    """Best-effort, sport-specific projected lineup for a game.

    Each side is built from its stored roster (real players arranged into the
    sport's starting shape by position) — a positional / depth-chart view, NOT
    a confirmed gameday XI (no free provider supplies those).  Skipped for
    individual / leaderboard sports (the two competitors are already in the
    header; golf is a leaderboard); a side with no roster comes back null.
    Never raises — any failure collapses to ``None``.
    """
    sport = league.sport
    if sport in domain.INDIVIDUAL_SPORTS or sport in domain.LEADERBOARD_SPORTS:
        return None
    try:
        home = await _team_lineup(session, sport, row.home_team_id)
        away = await _team_lineup(session, sport, row.away_team_id)
    except Exception:
        logger.exception("lineup build failed for game %s", row.id)
        return None
    if home is None and away is None:
        return None
    return lineup_to_out(GameLineup(game_id=row.id, sport=sport, home=home, away=away))
