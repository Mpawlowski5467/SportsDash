"""Single-game detail with an on-demand box-score drill-down.

``GET /games/{game_id}`` returns the game (the same ``GameOut`` every
other endpoint serves) plus a best-effort box-score summary fetched live
from the league's provider — never stored.  The summary is strictly
additive: any provider/network failure degrades the response to
``summary: null`` rather than failing the whole request, so opening a
game's detail can never 500 because of the summary lookup.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import domain
from app.models.orm import GameORM, LeagueORM
from app.providers import registry
from app.schemas import (
    GameDetailOut,
    GameLineupOut,
    GameOddsOut,
    GameSummaryOut,
    WeatherOut,
)
from app.services import lineup as lineup_service
from app.services import repository, wc_venues, weather
from app.services.serialize import (
    game_to_out,
    lineup_to_out,
    odds_to_out,
    summary_to_out,
    weather_to_out,
)
from app.models.domain import GameLineup, TeamLineup
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

router = APIRouter()


def _league_from_row(row: LeagueORM) -> domain.League:
    return domain.League(
        id=row.id,
        sport=domain.Sport(row.sport),
        name=row.name,
        provider=row.provider,
        provider_key=row.provider_key,
        follow_all=row.follow_all,
    )


def _provider_game_key(game_id: str) -> str:
    """Strip the ``"<provider>:"`` prefix from an internal game id."""
    return game_id.split(":", 1)[1] if ":" in game_id else game_id


async def _fetch_summary(
    league: domain.League, game_id: str
) -> GameSummaryOut | None:
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
        summary = await provider.get_game_summary(
            league, _provider_game_key(game_id)
        )
    except Exception:
        logger.exception(
            "Provider %r failed to summarize game %s", league.provider, game_id
        )
        return None
    if summary is None:
        return None
    return summary_to_out(summary)


async def _fetch_odds(
    league: domain.League, game_id: str
) -> GameOddsOut | None:
    """Resolve the league's provider and fetch betting lines + win-prob.

    Best-effort, exactly like ``_fetch_summary``: an unknown provider, a
    provider with no odds for the game, or any exception all collapse to
    ``None`` so the detail endpoint never fails because of the odds lookup.
    """
    try:
        provider = registry.get_provider(league.provider)
    except KeyError:
        return None
    try:
        odds = await provider.get_game_odds(league, _provider_game_key(game_id))
    except Exception:
        logger.exception(
            "Provider %r failed to price game %s", league.provider, game_id
        )
        return None
    if odds is None:
        return None
    return odds_to_out(odds)


async def _home_venue_coords(
    session: AsyncSession, row: GameORM
) -> tuple[float, float] | None:
    """Resolve the home venue's coordinates for a weather lookup.

    A followed home team carries resolved stadium coordinates on its row; a
    whole-competition home (no followed-team row) may instead play at a known
    host venue (World Cup table).  ``None`` when neither resolves.
    """
    if row.home_team_id is not None:
        team = await repository.get_team(session, row.home_team_id)
        if (
            team is not None
            and team.venue_lat is not None
            and team.venue_lon is not None
        ):
            return team.venue_lat, team.venue_lon
    host = wc_venues.resolve(row.venue)
    if host is not None:
        return host.lat, host.lon
    return None


async def _fetch_weather(
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


async def _fetch_lineup(
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
    return lineup_to_out(
        GameLineup(game_id=row.id, sport=sport, home=home, away=away)
    )


@router.get("/games/{game_id}", response_model=GameDetailOut)
async def game_detail(
    game_id: str, session: AsyncSession = Depends(get_session)
) -> GameDetailOut:
    row: GameORM | None = await repository.get_game(session, game_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game_id}")

    league_row = await repository.get_league(session, row.league_id)
    if league_row is None:
        # FK-enforced in normal writes; degrade rather than 500.
        raise HTTPException(status_code=404, detail="Game has no league")

    league = _league_from_row(league_row)
    game = game_to_out(row, league_row)
    summary = await _fetch_summary(league, row.id)
    weather_out = await _fetch_weather(session, league, row)
    lineup_out = await _fetch_lineup(session, league, row)
    odds_out = await _fetch_odds(league, row.id)
    return GameDetailOut(
        game=game,
        summary=summary,
        weather=weather_out,
        lineup=lineup_out,
        odds=odds_out,
    )
