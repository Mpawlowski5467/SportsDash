"""Tournament top scorers (the Golden Boot), aggregated from match feeds.

ESPN has no league-wide scorer feed for these competitions, but every
finished match's summary lists its goals (scorer + team).  This walks the
league's completed games, pulls each summary (the first on its own, the
rest in parallel), tallies goals per player (own goals excluded), and ranks
them.  The result is cached (``services/cache``, short TTL) because the
walk is N summary fetches — the first request after a cache miss pays for
it, the rest are instant — and N is capped to the most recent
:data:`_MAX_GAMES` finals, since finished games are never pruned and the
walk would otherwise grow season over season.

A summary that cannot be fetched counts as no goals, so a partly failed
walk still answers.  Two walks do not: one in which EVERY fetch failed,
whose tally would be an empty board that says nothing about the
competition, and one the provider's circuit breaker cut short by turning
fetches away unasked, whose tally would be however few matches got in
before it opened.  Both answer 503 and are not cached: the table already on
screen stays, and the next request walks again.

Soccer only: goals are the one summary field parsed exclusively for
:data:`~app.models.domain.Sport.SOCCER` (see ``providers/espn/summary``),
so for any other sport the whole walk is upstream traffic spent on a
guaranteed-empty tally.  Those leagues short-circuit to an empty board.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import convert, domain
from app.providers import registry
from app.schemas import ScorerOut, ScorersOut
from app.services import cache, circuit_breaker, repository

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL_SECONDS = 1800  # 30 min — scorers only change as matches finish
_MAX_CONCURRENCY = 8  # parallel summary fetches per request
# Bound the fan-out: one upstream summary call per game, and finished games
# are never pruned, so an uncapped walk grows every matchweek and again every
# season — a burst that size is exactly what rate-limits ESPN into an open
# circuit breaker for the whole app.  120 holds the same upstream budget as
# /api/odds (60 ids x 2 calls each), covers a whole World Cup (64 matches) or
# a couple of seasons of a followed team's fixtures, and caps a cold request
# at 15 rounds of _MAX_CONCURRENCY.
_MAX_GAMES = 120


_league_from_row = convert.league_from_row


@router.get("/scorers/{league_id}", response_model=ScorersOut)
async def scorers(league_id: str, session: AsyncSession = Depends(get_session)) -> ScorersOut:
    league_row = await repository.get_league(session, league_id)
    if league_row is None:
        raise HTTPException(status_code=404, detail="Unknown league")

    league = _league_from_row(league_row)
    # Only soccer summaries carry goals, so every other sport would pay one
    # upstream call per finished game to tally nothing.  Answer before the
    # fan-out (and before the cache: there is nothing worth storing).
    if league.sport is not domain.Sport.SOCCER:
        return ScorersOut(league_id=league.id, league_name=league.name, games_counted=0)

    final_games = [
        game
        for game in await repository.list_league_games(session, league_id)
        if game.phase == domain.GamePhase.FINAL.value
    ]
    # Keyed on the FULL final count so a newly finished match still busts the
    # cache once the cap below has bitten.
    cache_key = f"scorers:{league_id}:{len(final_games)}"
    cached = await cache.cache_get_json(cache_key)
    if cached is not None:
        return ScorersOut(**cached)

    # Rows come back oldest first, so the cap keeps the most RECENT matches:
    # a truncated tally should describe the current run of the competition,
    # not its opening weeks.  ``games_counted`` reports what was walked.
    final_games = final_games[-_MAX_GAMES:]

    # Team crest by display name, so each scorer can carry their flag.
    logo_by_team: dict[str, str] = {}
    for game in final_games:
        if game.home_logo_url:
            logo_by_team.setdefault(game.home_name, game.home_logo_url)
        if game.away_logo_url:
            logo_by_team.setdefault(game.away_name, game.away_logo_url)

    # Teams you follow in this league, so their scorers stand out.
    followed_names = {
        team.name.strip().casefold()
        for team in await repository.list_teams(session)
        if team.league_id == league_id
    }

    try:
        provider = registry.get_provider(league.provider)
    except KeyError:
        return ScorersOut(league_id=league.id, league_name=league.name, games_counted=0)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    # Fetches the provider's circuit breaker turned away without asking.
    turned_away = 0

    async def goals_for(game_id: str) -> list[domain.Goal] | None:
        """The game's goals; ``None`` when the summary could not be fetched."""
        nonlocal turned_away
        async with semaphore:
            try:
                summary = await provider.get_game_summary(
                    league, game_id.split(":", 1)[1] if ":" in game_id else game_id
                )
            except circuit_breaker.ProviderUnavailable:
                turned_away += 1
                return None
            except Exception:
                logger.warning("scorers: summary fetch failed for %s", game_id)
                return None
            return list(summary.goals) if summary is not None else []

    # The first summary goes alone.  After an outage the breaker is half-open
    # and admits exactly one call: fired together, that one would succeed and
    # every other be turned away, leaving a "board" of a single match.  Alone,
    # it closes the breaker and the rest of the walk goes through.
    per_game: list[list[domain.Goal] | None] = []
    if final_games:
        per_game.append(await goals_for(final_games[0].id))
        per_game.extend(await asyncio.gather(*(goals_for(g.id) for g in final_games[1:])))

    # Nothing at all came back, or the breaker cut the walk short: that is an
    # outage, not a goalless (or few-match) competition.  Raised before the
    # cache write, so the failure is never stored.
    if turned_away:
        logger.warning(
            "scorers: walk of %s cut short — the provider's circuit breaker "
            "turned %d of %d summary fetch(es) away",
            league_id,
            turned_away,
            len(per_game),
        )
    if per_game and (turned_away or all(goals is None for goals in per_game)):
        raise HTTPException(status_code=503, detail="Upstream provider unavailable")

    # Tally goals per player (own goals don't count toward a scorer).
    tally: dict[tuple[str, str], int] = defaultdict(int)
    for goals in per_game:
        for goal in goals or ():
            if goal.own_goal:
                continue
            tally[(goal.player, goal.team)] += 1

    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0][0]))
    rows = [
        ScorerOut(
            rank=index + 1,
            player=player,
            team=team,
            team_logo_url=logo_by_team.get(team),
            goals=count,
            highlighted=team.strip().casefold() in followed_names,
        )
        for index, ((player, team), count) in enumerate(ranked[:30])
    ]

    result = ScorersOut(
        league_id=league.id,
        league_name=league.name,
        games_counted=len(final_games),
        rows=rows,
    )
    await cache.cache_set_json(cache_key, result.model_dump(), _CACHE_TTL_SECONDS)
    return result
