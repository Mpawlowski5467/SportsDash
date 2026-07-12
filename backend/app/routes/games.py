"""Single-game detail with an on-demand box-score drill-down.

``GET /games/{game_id}`` returns the game (the same ``GameOut`` every
other endpoint serves) plus best-effort enrichments (box-score summary,
weather, projected lineup, odds) fetched live from the league's provider
— never stored.  Every enrichment is strictly additive: any
provider/network failure degrades that field to ``null`` rather than
failing the whole request.  The fetch helpers live in
``app.services.game_detail`` (shared with the matchup, odds, and
schedule routes).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import convert
from app.models.orm import GameORM
from app.schemas import GameDetailOut
from app.services import game_detail, repository
from app.services.serialize import game_to_out

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/games/{game_id}", response_model=GameDetailOut)
async def game_detail_route(
    game_id: str, session: AsyncSession = Depends(get_session)
) -> GameDetailOut:
    row: GameORM | None = await repository.get_game(session, game_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game_id}")

    league_row = await repository.get_league(session, row.league_id)
    if league_row is None:
        # FK-enforced in normal writes; degrade rather than 500.
        raise HTTPException(status_code=404, detail="Game has no league")

    league = convert.league_from_row(league_row)
    game = game_to_out(row, league_row)
    summary = await game_detail.fetch_summary(league, row.id)
    weather_out = await game_detail.fetch_weather(session, league, row)
    lineup_out = await game_detail.fetch_lineup(session, league, row)
    odds_out = await game_detail.fetch_odds(league, row.id)
    return GameDetailOut(
        game=game,
        summary=summary,
        weather=weather_out,
        lineup=lineup_out,
        odds=odds_out,
    )
