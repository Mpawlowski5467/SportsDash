"""ORM-row -> API-schema serialization helpers.

Keeps the response-shaping logic out of the route handlers so every
endpoint that returns games produces identical ``GameOut`` payloads.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from app.models.domain import GameLineup, GameOdds, GameSummary, TeamLineup, Weather
from app.models.orm import EventORM, GameORM, LeagueORM
from app.schemas import (
    EventOut,
    GameLineupOut,
    GameOddsOut,
    GameOut,
    GamePlayOut,
    GameSideOut,
    GameSummaryOut,
    LeaderRowOut,
    GoalOut,
    LineupSlotOut,
    PeriodScoreOut,
    PerformerOut,
    PlayerOut,
    TeamLineupOut,
    TeamStatOut,
    WeatherOut,
)
from app.timeutil import ensure_utc

logger = logging.getLogger(__name__)

#: Phases for which scores are meaningless unless a live/final state was
#: actually recorded for the game.
_NO_STATE_PHASES = frozenset({"postponed", "canceled"})


def game_to_out(row: GameORM, league: LeagueORM) -> GameOut:
    """Serialize a game row to the API shape.

    Scores are ``None`` while the game is still ``scheduled`` (the ORM
    columns default to 0, which would read as a 0-0 game), and likewise
    for postponed/canceled games that never actually started.  The
    period check matters: a postponed game arriving via schedule refresh
    *does* carry a recorded (0-0, period 0) state, but showing it as a
    0-0 result would be wrong — only a game canceled mid-play keeps its
    partial score.
    """
    hide_scores = row.phase == "scheduled" or (
        row.phase in _NO_STATE_PHASES and (row.state_updated_at is None or row.period == 0)
    )
    home_score: int | None = None if hide_scores else row.home_score
    away_score: int | None = None if hide_scores else row.away_score

    followed_team_ids = [
        team_id for team_id in (row.home_team_id, row.away_team_id) if team_id is not None
    ]

    return GameOut(
        id=row.id,
        league_id=row.league_id,
        sport=league.sport,
        home=GameSideOut(
            team_id=row.home_team_id,
            name=row.home_name,
            abbreviation=row.home_abbreviation,
            logo_url=row.home_logo_url,
            color=row.home_color,
            score=home_score,
        ),
        away=GameSideOut(
            team_id=row.away_team_id,
            name=row.away_name,
            abbreviation=row.away_abbreviation,
            logo_url=row.away_logo_url,
            color=row.away_color,
            score=away_score,
        ),
        start_time=ensure_utc(row.start_time),
        venue=row.venue,
        series=row.series,
        phase=row.phase,
        period=row.period,
        period_label=row.period_label,
        clock=row.clock,
        is_intermission=row.is_intermission,
        followed_team_ids=followed_team_ids,
    )


def summary_to_out(summary: GameSummary) -> GameSummaryOut:
    """Serialize an on-demand game summary (domain, not ORM) to the API shape.

    Summaries are fetched live from a provider and never stored, so this
    maps the :class:`~app.models.domain.GameSummary` dataclass straight to
    its response model.
    """
    return GameSummaryOut(
        game_id=summary.game_id,
        periods=[
            PeriodScoreOut(label=period.label, home=period.home, away=period.away)
            for period in summary.periods
        ],
        performers=[
            PerformerOut(name=performer.name, side=performer.side, detail=performer.detail)
            for performer in summary.performers
        ],
        team_stats=[
            TeamStatOut(label=stat.label, home=stat.home, away=stat.away)
            for stat in summary.team_stats
        ],
        goals=[
            GoalOut(
                player=goal.player,
                team=goal.team,
                minute=goal.minute,
                own_goal=goal.own_goal,
                penalty=goal.penalty,
            )
            for goal in summary.goals
        ],
        home_total=summary.home_total,
        away_total=summary.away_total,
        win_probability=list(summary.win_probability),
        plays=[
            GamePlayOut(
                text=play.text,
                period_label=play.period_label,
                clock=play.clock,
                team=play.team,
                home_score=play.home_score,
                away_score=play.away_score,
                scoring=play.scoring,
            )
            for play in summary.plays
        ],
    )


def player_to_out(player) -> PlayerOut:
    """Serialize a player (domain ``Player`` or stored ``PlayerORM`` row).

    Both carry the same read-only fields; ``status`` is a str-enum on the
    domain object and a plain string on the row, and PlayerOut accepts either.
    """
    return PlayerOut(
        id=player.id,
        name=player.name,
        position=player.position,
        jersey_number=player.jersey_number,
        status=player.status,
        status_detail=player.status_detail,
        stat_line=player.stat_line,
        career_stat_line=player.career_stat_line,
        photo_url=player.photo_url,
    )


def _team_lineup_to_out(lineup: TeamLineup) -> TeamLineupOut:
    return TeamLineupOut(
        formation=lineup.formation,
        slots=[
            LineupSlotOut(
                player=player_to_out(slot.player),
                role=slot.role,
                unit=slot.unit,
                order=slot.order,
            )
            for slot in lineup.slots
        ],
        bench=[player_to_out(player) for player in lineup.bench],
    )


def lineup_to_out(lineup: GameLineup) -> GameLineupOut:
    """Serialize a roster-derived game lineup (domain, not ORM) to the API shape."""
    return GameLineupOut(
        home=_team_lineup_to_out(lineup.home) if lineup.home is not None else None,
        away=_team_lineup_to_out(lineup.away) if lineup.away is not None else None,
    )


def weather_to_out(weather: Weather) -> WeatherOut:
    """Serialize venue weather (domain, not ORM) to the API shape."""
    return WeatherOut(
        temperature=weather.temperature,
        condition=weather.condition,
        code=weather.code,
        wind_speed=weather.wind_speed,
        units=weather.units,
        high=weather.high,
        low=weather.low,
        precip_chance=weather.precip_chance,
    )


def odds_to_out(odds: GameOdds) -> GameOddsOut:
    """Serialize game odds + win-probability (domain, not ORM) to the API shape."""
    return GameOddsOut(
        provider=odds.provider,
        details=odds.details,
        home_moneyline=odds.home_moneyline,
        away_moneyline=odds.away_moneyline,
        spread=odds.spread,
        over_under=odds.over_under,
        home_win_pct=odds.home_win_pct,
        away_win_pct=odds.away_win_pct,
    )


def event_to_out(row: EventORM, league: LeagueORM) -> EventOut:
    """Serialize a leaderboard event row to the API shape."""
    board = row.leaderboard if isinstance(row.leaderboard, list) else []
    leaderboard = [
        LeaderRowOut(
            position=entry.get("position", 0),
            position_label=entry.get("position_label", ""),
            name=entry.get("name", ""),
            score=entry.get("score", ""),
            detail=entry.get("detail"),
            player_id=entry.get("player_id"),
        )
        for entry in board
        if isinstance(entry, dict)
    ]
    followed = [r.player_id for r in leaderboard if r.player_id]
    return EventOut(
        id=row.id,
        league_id=row.league_id,
        sport=league.sport,
        name=row.name,
        start_time=ensure_utc(row.start_time),
        end_time=ensure_utc(row.end_time) if row.end_time is not None else None,
        phase=row.phase,
        round_label=row.round_label,
        venue=row.venue,
        followed_player_ids=followed,
        leaderboard=leaderboard,
    )


def events_to_out(
    rows: Sequence[EventORM], leagues_by_id: Mapping[str, LeagueORM]
) -> list[EventOut]:
    out: list[EventOut] = []
    for row in rows:
        league = leagues_by_id.get(row.league_id)
        if league is None:
            logger.warning("Skipping event %s: unknown league %s", row.id, row.league_id)
            continue
        out.append(event_to_out(row, league))
    return out


def games_to_out(rows: Sequence[GameORM], leagues_by_id: Mapping[str, LeagueORM]) -> list[GameOut]:
    """Serialize many rows, skipping (and logging) any with an unknown league.

    A missing league can't happen through normal FK-enforced writes, but a
    dashboard endpoint should degrade to "one game missing" rather than 500.
    """
    out: list[GameOut] = []
    for row in rows:
        league = leagues_by_id.get(row.league_id)
        if league is None:
            logger.warning("Skipping game %s: unknown league %s", row.id, row.league_id)
            continue
        out.append(game_to_out(row, league))
    return out
