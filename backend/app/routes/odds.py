"""Batch betting lines + win-probability for a set of games.

The game-detail modal fetches odds for one game on demand (see
``routes/games.py``).  This endpoint decorates a *list* of cards — Today,
Calendar, Results — where fetching each game's odds one modal-open at a
time would be too chatty.  ``GET /api/odds?ids=a,b,c`` returns a
``{game_id: GameOddsOut}`` map (the same per-game data the modal shows), so
a card and the modal always agree.

Each game's odds are cached in Redis individually (``odds:{game_id}``) so
overlapping views (Today and Calendar list many of the same games) reuse
the work, and an odds-less game (most soccer) is cached as a negative so it
isn't re-fetched on every poll.  The fan-out is bounded by a semaphore and
the request can never fail — a missing/odds-less game is simply absent.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import domain
from app.models.orm import GameORM, LeagueORM
from app.providers import registry
from app.models import convert
from app.services.game_detail import provider_game_key
from app.schemas import GameOddsOut
from app.services import cache, repository
from app.services.serialize import odds_to_out

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL_SECONDS = 600  # 10 min — lines drift slowly pre-game
_MAX_CONCURRENCY = 8      # parallel summary+predictor fetches per request
_MAX_IDS = 60             # bound the fan-out (each id = two upstream calls)

# Odds/win-probability are pre-game and live artifacts only.
_PRICED_PHASES = frozenset(
    {domain.GamePhase.SCHEDULED.value, domain.GamePhase.IN_PROGRESS.value}
)


@router.get("/odds", response_model=dict[str, GameOddsOut])
async def odds(
    ids: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, GameOddsOut]:
    """Best-effort odds + win-probability for the given games, keyed by id.

    ``ids`` is a comma-separated list of game ids (ids never contain a
    comma).  Only scheduled / in-progress, non-individual games are priced;
    every other game — and any that the provider has no line for — is simply
    absent from the map.  Reuses the same per-game provider call the detail
    modal uses, so a card chip and the modal always match.
    """
    game_ids = [gid for gid in ids.split(",") if gid][:_MAX_IDS]
    if not game_ids:
        return {}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    leagues: dict[str, domain.League] = {}
    targets: list[tuple[str, domain.League]] = []
    for game_id in game_ids:
        row: GameORM | None = await repository.get_game(session, game_id)
        if row is None or row.phase not in _PRICED_PHASES:
            continue
        league = leagues.get(row.league_id)
        if league is None:
            league_row: LeagueORM | None = await repository.get_league(
                session, row.league_id
            )
            if league_row is None:
                continue
            league = convert.league_from_row(league_row)
            leagues[row.league_id] = league
        if league.sport in domain.INDIVIDUAL_SPORTS:
            continue
        targets.append((game_id, league))

    async def odds_for(game_id: str, league: domain.League) -> GameOddsOut | None:
        cache_key = f"odds:{game_id}"
        cached = await cache.cache_get_json(cache_key)
        if cached is not None:
            payload = cached.get("odds")
            return GameOddsOut(**payload) if payload else None
        async with semaphore:
            try:
                provider = registry.get_provider(league.provider)
                result = await provider.get_game_odds(
                    league, provider_game_key(game_id)
                )
            except Exception:
                # Transient/circuit errors: skip without caching so the next
                # poll retries (a genuine "no line" returns None, not raises).
                logger.warning("odds: fetch failed for %s", game_id)
                return None
        out = None if result is None else odds_to_out(result)
        await cache.cache_set_json(
            cache_key,
            {"odds": out.model_dump() if out is not None else None},
            _CACHE_TTL_SECONDS,
        )
        return out

    priced = await asyncio.gather(
        *(odds_for(game_id, league) for game_id, league in targets)
    )
    return {
        game_id: value
        for (game_id, _league), value in zip(targets, priced, strict=False)
        if value is not None
    }
