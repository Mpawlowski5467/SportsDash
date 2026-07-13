"""Pre-game matchup preview — one assembled view per upcoming game.

Pure assembly: every piece (odds, win-probability, weather, projected
lineups, recent form, head-to-head, injuries) is reused from data the app
already has, so this adds no new provider call beyond the odds/weather
look-ups the detail modal already makes.  Best-effort throughout — a side
that isn't a followed team simply comes back with empty lineup/injuries,
and any sub-fetch failure degrades that field rather than the request.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import domain
from app.models.orm import GameORM, LeagueORM
from app.models import convert
from app.services.game_detail import fetch_lineup, fetch_odds, fetch_weather
from app.schemas import MatchupOut, PlayerOut
from app.services import head_to_head, repository
from app.services.serialize import game_to_out, player_to_out

logger = logging.getLogger(__name__)

router = APIRouter()

_FORM_GAMES = 6  # recent results per side in the form strip
_H2H_GAMES = 5  # past meetings to show


def _outcome(row: GameORM, name: str) -> str | None:
    """W/L/D for ``name`` in a finished game, or None if it didn't play."""
    if row.home_name == name:
        mine, theirs = row.home_score, row.away_score
    elif row.away_name == name:
        mine, theirs = row.away_score, row.home_score
    else:
        return None
    if mine > theirs:
        return "W"
    if mine < theirs:
        return "L"
    return "D"


async def _form(session: AsyncSession, league_id: str, name: str) -> list[str]:
    """Recent-results strip (W/L/D, newest first) for a side by name."""
    games = await repository.recent_finals_for_name(session, league_id, name, _FORM_GAMES)
    return [o for o in (_outcome(g, name) for g in games) if o is not None]


async def _injuries(session: AsyncSession, team_id: str | None) -> list[PlayerOut]:
    """A followed team's non-active players (injured / day-to-day / out)."""
    if team_id is None:
        return []
    players = await repository.get_roster(session, team_id)
    return [
        player_to_out(p)
        for p in players
        if p.status and p.status != domain.PlayerStatus.ACTIVE.value
    ]


@router.get("/matchup/{game_id}", response_model=MatchupOut)
async def matchup(game_id: str, session: AsyncSession = Depends(get_session)) -> MatchupOut:
    row: GameORM | None = await repository.get_game(session, game_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game_id}")
    league_row: LeagueORM | None = await repository.get_league(session, row.league_id)
    if league_row is None:
        raise HTTPException(status_code=404, detail="Game has no league")

    league = convert.league_from_row(league_row)
    game = game_to_out(row, league_row)
    odds = await fetch_odds(league, row.id)
    weather = await fetch_weather(session, league, row)
    lineup = await fetch_lineup(session, league, row)
    home_form = await _form(session, row.league_id, row.home_name)
    away_form = await _form(session, row.league_id, row.away_name)
    h2h_rows = await repository.head_to_head(
        session, row.league_id, row.home_name, row.away_name, _H2H_GAMES
    )
    head_to_head = [game_to_out(g, league_row) for g in h2h_rows]
    record = await _cross_season_record(session, row, league_row)
    home_injuries = await _injuries(session, row.home_team_id)
    away_injuries = await _injuries(session, row.away_team_id)

    return MatchupOut(
        game=game,
        odds=odds,
        weather=weather,
        lineup=lineup,
        home_form=home_form,
        away_form=away_form,
        head_to_head=head_to_head,
        head_to_head_record=record,
        home_injuries=home_injuries,
        away_injuries=away_injuries,
    )


async def _cross_season_record(session, row, league_row):
    """Cross-season W-D-L from a followed side's perspective, or None.

    Prefers the home side when both are followed. Best-effort like every
    other matchup enrichment — the builder itself never raises.
    """
    for team_id, opponent_name in (
        (row.home_team_id, row.away_name),
        (row.away_team_id, row.home_name),
    ):
        if team_id is None:
            continue
        team_row = await repository.get_team(session, team_id)
        if team_row is None:
            continue
        return await head_to_head.build_record(league_row, team_row, opponent_name)
    return None
