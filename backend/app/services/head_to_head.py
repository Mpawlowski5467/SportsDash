"""Cross-season head-to-head record for the Matchup preview.

The matchup's stored "last meetings" only cover the near window the
games table keeps; this walks the followed team's past seasons (via the
redis-cached season-results service, so the upstream cost is paid once)
and tallies every meeting with the given opponent.

Matched by display name, exactly like ``repository.head_to_head`` — the
opponent is usually not a followed team and has no row of its own.
Never raises: any failure degrades to ``None`` and the matchup simply
omits the record.
"""

from __future__ import annotations

import logging

from app.models.domain import Sport
from app.models.orm import LeagueORM, TeamORM
from app.providers import espn_history
from app.schemas import GameOut, HeadToHeadRecordOut
from app.services import season_results
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

# How many season keys to scan, counting back from the current year. The
# current calendar year resolves to the in-progress (or just-finished)
# season, so this covers roughly the last five seasons in every sport.
SEASONS_BACK = 5
_MAX_MEETINGS = 8


def _name_key(name: str) -> str:
    return (name or "").strip().casefold()


def _team_side(game: GameOut, team_id: str, team_key: str) -> str | None:
    """Which side ("home"/"away") is the perspective team; None if neither."""
    if game.home.team_id == team_id or _name_key(game.home.name) == team_key:
        return "home"
    if game.away.team_id == team_id or _name_key(game.away.name) == team_key:
        return "away"
    return None


async def build_record(
    league_row: LeagueORM, team_row: TeamORM, opponent_name: str
) -> HeadToHeadRecordOut | None:
    """W-D-L vs ``opponent_name`` across recent seasons, from the team's side.

    ``None`` when the league has no season archives, the scan finds no
    meetings, or anything fails — the matchup renders without the section.
    """
    try:
        if league_row.provider != "espn" or not espn_history.supports_history(
            Sport(league_row.sport)
        ):
            return None

        team_key = _name_key(team_row.name)
        opponent_key = _name_key(opponent_name)
        if not opponent_key or opponent_key == team_key:
            return None

        wins = losses = draws = 0
        meetings: list[GameOut] = []
        now_year = utcnow().year
        # Newest season first, and season results are newest-first too, so
        # `meetings` stays globally newest-first without a re-sort.
        for season in range(now_year, now_year - SEASONS_BACK, -1):
            for game in await season_results.season_results_out(league_row, team_row, season):
                side = _team_side(game, team_row.id, team_key)
                if side is None:
                    continue
                us = game.home if side == "home" else game.away
                them = game.away if side == "home" else game.home
                if _name_key(them.name) != opponent_key:
                    continue
                if us.score is None or them.score is None:
                    continue
                if us.score > them.score:
                    wins += 1
                elif us.score < them.score:
                    losses += 1
                else:
                    draws += 1
                if len(meetings) < _MAX_MEETINGS:
                    meetings.append(game)

        if wins + losses + draws == 0:
            return None
        return HeadToHeadRecordOut(
            team_id=team_row.id,
            team_name=team_row.name,
            opponent_name=opponent_name,
            seasons=SEASONS_BACK,
            wins=wins,
            losses=losses,
            draws=draws,
            meetings=meetings,
        )
    except Exception:
        logger.exception(
            "head_to_head: record build failed for %s vs %r", team_row.id, opponent_name
        )
        return None
