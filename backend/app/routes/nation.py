"""A competition team's mini-dashboard, resolved by name.

Whole-competition teams (e.g. the 48 World Cup nations) have no
followed-team row, so they can't use the team-detail endpoints.  This
assembles what we *do* have for one by name: its standings line + group
(from the league's stored standings) and its fixtures/results (from the
league's synced games where it is home or away).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import domain
from app.schemas import GameOut, NationOut, NationStandingOut
from app.services import repository
from app.services.serialize import game_to_out

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/nation/{league_id}/{name}", response_model=NationOut)
async def nation(
    league_id: str, name: str, session: AsyncSession = Depends(get_session)
) -> NationOut:
    league = await repository.get_league(session, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")

    target = name.strip().casefold()

    # Standings line + display metadata (crest/color/group) for the nation.
    standing: NationStandingOut | None = None
    display_name = name
    abbreviation: str | None = None
    logo_url: str | None = None
    color: str | None = None
    group: str | None = None
    standings_row = await repository.get_standings(session, league_id)
    if standings_row is not None:
        for row in standings_row.rows:
            row_name = row.get("team_name")
            if isinstance(row_name, str) and row_name.strip().casefold() == target:
                display_name = row_name
                abbreviation = row.get("abbreviation")
                logo_url = row.get("logo_url")
                color = row.get("color")
                group = row.get("group")
                standing = NationStandingOut(
                    rank=row.get("rank") or 0,
                    wins=row.get("wins") or 0,
                    draws=row.get("draws"),
                    losses=row.get("losses") or 0,
                    points=row.get("points"),
                    goal_diff=row.get("goal_diff"),
                )
                break

    # Fixtures + results from the synced games this nation appears in.
    fixtures: list[GameOut] = []
    results: list[GameOut] = []
    matched_any = standing is not None
    for game in await repository.list_league_games(session, league_id):
        names = (game.home_name.strip().casefold(), game.away_name.strip().casefold())
        if target not in names:
            continue
        matched_any = True
        # Fill display metadata from a game side if standings had none.
        if logo_url is None:
            if names[0] == target and game.home_logo_url:
                logo_url, color = game.home_logo_url, color or game.home_color
            elif names[1] == target and game.away_logo_url:
                logo_url, color = game.away_logo_url, color or game.away_color
        out = game_to_out(game, league)
        if game.phase == domain.GamePhase.FINAL.value:
            results.append(out)
        elif game.phase in (
            domain.GamePhase.SCHEDULED.value,
            domain.GamePhase.IN_PROGRESS.value,
        ):
            fixtures.append(out)

    if not matched_any:
        raise HTTPException(status_code=404, detail="Unknown team in this competition")

    fixtures.sort(key=lambda g: g.start_time)
    results.sort(key=lambda g: g.start_time, reverse=True)

    return NationOut(
        league_id=league.id,
        league_name=league.name,
        name=display_name,
        abbreviation=abbreviation,
        logo_url=logo_url,
        color=color,
        group=group,
        standing=standing,
        fixtures=fixtures,
        results=results,
    )
