"""Batch betting lines + win-probability for a set of games.

The game-detail modal fetches odds for one game on demand (see
``routes/games.py``).  This endpoint decorates a *list* of cards — Today,
Calendar, Results — where fetching each game's odds one modal-open at a
time would be too chatty.  ``GET /api/odds?ids=a,b,c`` returns a
``{game_id: GameOddsOut}`` map (the same per-game data the modal shows), so
a card and the modal always agree.

Each game's odds are cached individually (``services/cache``,
``odds:{game_id}``) so overlapping views (Today and Calendar list many of
the same games) reuse the work, and an odds-less game (most soccer) is
cached as a negative so it isn't re-fetched on every poll.  The fan-out is
bounded by a semaphore — after one fetch per provider has gone ahead on its
own — and a missing/odds-less game is simply absent.

One failure is not "absent": when every game the request had to price
failed upstream — nothing from the cache, no provider answer at all — an
empty map would report an outage as "no lines", so that answers 503.  Any
cached or answered game keeps the response a 200.

What the 503 saves is a REFETCH: the client asking again for the id list it
already holds keeps that list's chips, because a failed refetch leaves a
query's data in place.  A request for a NEW id list — the list changes
whenever a game kicks off or goes final — has nothing of its own to keep:
the client shows the previous list's chips only while that first request is
in flight, and once it fails they are gone until a request succeeds.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
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
_MAX_CONCURRENCY = 8  # parallel summary+predictor fetches per request
_MAX_IDS = 60  # bound the fan-out (each id = two upstream calls)

# Odds/win-probability are pre-game and live artifacts only.
_PRICED_PHASES = frozenset({domain.GamePhase.SCHEDULED.value, domain.GamePhase.IN_PROGRESS.value})


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
            league_row: LeagueORM | None = await repository.get_league(session, row.league_id)
            if league_row is None:
                continue
            league = convert.league_from_row(league_row)
            leagues[row.league_id] = league
        if league.sport in domain.INDIVIDUAL_SPORTS:
            continue
        targets.append((game_id, league))

    # Games whose upstream fetch raised — as opposed to a cache hit or a
    # provider that answered, even with "no line".
    failed: list[str] = []
    # Providers one upstream fetch of this request has already come back
    # from, whatever its outcome.
    settled: set[str] = set()

    async def odds_for(game_id: str, league: domain.League) -> GameOddsOut | None:
        cache_key = f"odds:{game_id}"
        cached = await cache.cache_get_json(cache_key)
        if cached is not None:
            payload = cached.get("odds")
            return GameOddsOut(**payload) if payload else None
        async with semaphore:
            try:
                provider = registry.get_provider(league.provider)
            except KeyError:
                # No such provider registered: nothing can price this game,
                # and nothing upstream failed either.
                logger.warning("odds: no provider %r for %s", league.provider, game_id)
                return None
            try:
                result = await provider.get_game_odds(league, provider_game_key(game_id))
            except Exception:
                # Transient/circuit errors: skip without caching so the next
                # poll retries (a genuine "no line" returns None, not raises).
                logger.warning("odds: fetch failed for %s", game_id)
                failed.append(game_id)
                return None
            finally:
                settled.add(league.provider)
        out = None if result is None else odds_to_out(result)
        await cache.cache_set_json(
            cache_key,
            {"odds": out.model_dump() if out is not None else None},
            _CACHE_TTL_SECONDS,
        )
        return out

    # One fetch per provider goes first, alone.  After an outage the breaker
    # is half-open and admits exactly one call: fired together, that one would
    # be priced and every other turned away.  Alone, it closes the breaker and
    # the rest of the batch goes through.  A cache hit is not a fetch, so the
    # walk passes over it and keeps looking for the provider's first one.
    priced_by_id: dict[str, GameOddsOut | None] = {}
    rest: list[tuple[str, domain.League]] = []
    for game_id, league in targets:
        if league.provider in settled:
            rest.append((game_id, league))
        else:
            priced_by_id[game_id] = await odds_for(game_id, league)
    rest_priced = await asyncio.gather(*(odds_for(game_id, league) for game_id, league in rest))
    for (game_id, _league), value in zip(rest, rest_priced, strict=True):
        priced_by_id[game_id] = value
    priced = [priced_by_id[game_id] for game_id, _league in targets]
    # Every target was a fetch and every fetch raised: an outage, which must
    # not pass for "no lines".  One cache hit or one answer keeps it a 200.
    if failed and len(failed) == len(targets):
        raise HTTPException(status_code=503, detail="Upstream provider unavailable")
    return {
        game_id: value
        for (game_id, _league), value in zip(targets, priced, strict=False)
        if value is not None
    }
