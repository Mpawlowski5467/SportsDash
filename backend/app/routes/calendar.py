"""iCalendar export of stored games + events (-30d .. +60d around now).

By default the feed covers every followed game plus every leaderboard
competition (a golf tournament; golf is the only sport modeled as an
``Event``) overlapping the window, the latter as multi-day all-day
entries.  An optional ``?team_id=`` narrows the feed to a single team,
which (paired with the frontend "Subscribe" control) lets a user
subscribe to a live per-team calendar in Apple/Google Calendar; a
leaderboard event has no team side, so a team-filtered feed carries that
team's games only.

That rule leaves ONE follow with nothing to serve: a golfer, who is a
single-member golf ``TeamORM`` row ("athlete-as-team") and therefore
never appears on either side of a game.  We do not special-case it here.
The only link from a stored event back to a followed golfer is a tagged
``leaderboard[].player_id``, which exists only once the provider
publishes a field — future tournaments arrive from the season calendar
with an empty board, so a "golfer's tournaments" filter would carry the
events they have already played and none of the ones they are about to,
which is the wrong half for a subscription.  The frontend instead omits
leaderboard-sport follows from its per-team Subscribe menu and points
them at the all-teams feed, which carries every tournament.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import repository
from app.services.ics import games_to_ics
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

PAST_WINDOW = timedelta(days=30)
FUTURE_WINDOW = timedelta(days=60)


@router.get("/calendar.ics")
async def calendar_ics(
    team_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if team_id is not None and await repository.get_team(session, team_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_id}")

    now = utcnow()
    window_start = now - PAST_WINDOW
    window_end = now + FUTURE_WINDOW
    rows = await repository.games_between(session, window_start, window_end, team_id=team_id)
    # Events belong to no team, so a per-team feed omits them entirely
    # rather than repeating every tournament in every team's calendar
    # (see the module docstring for why a golfer isn't special-cased).
    event_rows = (
        []
        if team_id is not None
        else await repository.events_between(session, window_start, window_end)
    )
    leagues_by_id = {league.id: league for league in await repository.list_leagues(session)}
    filename = f"sportsdash-{team_id}.ics" if team_id is not None else "sportsdash.ics"
    return Response(
        content=games_to_ics(rows, leagues_by_id, events=event_rows),
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
