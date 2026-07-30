"""Player-follow endpoints (individual players on followed team-sport teams).

Follows key on the BARE ESPN athlete id; roster rows store the same id
``"espn:"``-prefixed, so the v1 roster-membership check joins on
``PlayerORM.id == f"espn:{athlete_id}"``.  The v1 constraint keeps a
follow on a currently-followed team's stored roster (the roster is the
picker), which means the player's games already flow through the team
follow and schedule sync needs no changes.

This is a writing route (alongside ``setup`` and ``notifications``) —
the PUT/DELETE handlers commit explicitly; read-only routes never do.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.orm import PlayerFollowORM, PlayerORM
from app.schemas import PlayerFollowIn, PlayerFollowOut
from app.services import repository
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

router = APIRouter()


def _follow_out(row: PlayerFollowORM) -> PlayerFollowOut:
    return PlayerFollowOut(
        athlete_id=row.athlete_id,
        name=row.name,
        team_id=row.team_id,
        league_id=row.league_id,
        position=row.position,
        photo_url=row.photo_url,
        followed_at=ensure_utc(row.followed_at),
    )


@router.get("/players/follows", response_model=list[PlayerFollowOut])
async def list_player_follows(
    session: AsyncSession = Depends(get_session),
) -> list[PlayerFollowOut]:
    return [_follow_out(row) for row in await repository.list_player_follows(session)]


@router.put("/players/follows/{athlete_id}", response_model=PlayerFollowOut)
async def follow_player(
    athlete_id: str,
    body: PlayerFollowIn,
    session: AsyncSession = Depends(get_session),
) -> PlayerFollowOut:
    """Follow (or re-follow) one player; validates the v1 roster constraint.

    ESPN athlete ids are only unique per sport (the same bare id can name
    both an NHL and an MLB athlete), and follows key on the bare id alone
    — so an id already followed under a DIFFERENT league is rejected with
    409 rather than silently overwriting that follow.  The roster row's
    current status seeds the follow's alert baseline, so following an
    already-injured player fires no alert.
    """
    team = await repository.get_team(session, body.team_id)
    if team is None:
        raise HTTPException(
            status_code=404,
            detail=f"Team {body.team_id!r} is not a followed team",
        )
    player = await session.get(PlayerORM, (body.team_id, f"espn:{athlete_id}"))
    if player is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Athlete {athlete_id!r} is not on the stored roster of "
                f"followed team {body.team_id!r}"
            ),
        )
    existing = await session.get(PlayerFollowORM, athlete_id)
    if existing is not None and existing.league_id != body.league_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Athlete id {athlete_id!r} is already followed in league "
                f"{existing.league_id!r} ({existing.name}); ESPN athlete ids "
                f"collide across sports — unfollow that player first"
            ),
        )
    row = await repository.follow_player(
        session,
        athlete_id=athlete_id,
        name=body.name,
        team_id=body.team_id,
        league_id=body.league_id,
        position=body.position,
        photo_url=body.photo_url,
        last_notified_status=player.status,
    )
    await session.commit()
    logger.info("Followed player %s (%s) on %s", row.name, athlete_id, row.team_id)
    return _follow_out(row)


@router.delete("/players/follows/{athlete_id}", status_code=204)
async def unfollow_player(
    athlete_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not await repository.unfollow_player(session, athlete_id):
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id!r} is not followed")
    await session.commit()
    logger.info("Unfollowed player %s", athlete_id)
    return Response(status_code=204)
