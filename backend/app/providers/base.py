"""Provider adapter interface.

Each external data source implements :class:`SportsProvider` and
normalizes everything into the domain models in ``app.models.domain``.
Swapping or adding a source must never require touching services,
routes, or the scheduler.

Error contract
--------------
Implementations should follow EspnProvider's convention: **raise** on
transport errors and 5xx (so the registry's circuit breaker records the
failure and can trip), and **degrade quietly** (return ``None`` / ``[]``)
only for genuine not-found or legitimately-empty results.  Raising
:class:`~app.providers.http_util.TransientProviderError` marks a failure
as retry-worthy.

Known deviation: TheSportsDbProvider is documented never-raise and
swallows HTTP errors internally, so its breaker only sees failures from
exhausted retries — new providers should NOT copy that; prefer the
raise-on-failure contract above.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from app.models.domain import (
    Event,
    Game,
    GameOdds,
    GameState,
    GameSummary,
    League,
    NewsItem,
    Roster,
    Standings,
    Team,
    TeamLocation,
)


@runtime_checkable
class SportsProvider(Protocol):
    provider_id: str

    async def get_schedule(self, league: League, team: Team, start: date, end: date) -> list[Game]:
        """All games for ``team`` with start dates in [start, end] (UTC dates)."""
        ...

    async def get_live_games(self, league: League) -> list[Game]:
        """Today's scoreboard for the league; each game carries a populated state."""
        ...

    async def get_competition_schedule(self, league: League, start: date, end: date) -> list[Game]:
        """Every fixture in the league between [start, end] (UTC dates).

        Used for whole-competition follows (``League.follow_all``) where
        no single team scopes the fetch.  Providers without bulk
        scheduling may return ``[]``.
        """
        ...

    async def get_game_state(self, league: League, provider_game_key: str) -> GameState | None:
        """Current state of a single game, or None if unknown to the provider."""
        ...

    async def get_game_summary(self, league: League, provider_game_key: str) -> GameSummary | None:
        """On-demand box score (period lines + performers) for one game.

        Best-effort: providers without a summary source return ``None``.
        Never stored — fetched live when the user opens a game's detail.
        """
        ...

    async def get_game_odds(self, league: League, provider_game_key: str) -> GameOdds | None:
        """On-demand betting lines + win-probability for one game.

        Best-effort and never stored, like :meth:`get_game_summary`.
        Providers without odds/projection sources — and individual or
        leaderboard sports — return ``None``.
        """
        ...

    async def get_standings(self, league: League) -> Standings: ...

    async def get_roster(self, league: League, team: Team) -> Roster: ...

    async def get_team_location(self, league: League, team: Team) -> TeamLocation | None:
        """Home venue (name + optional coordinates) for the map view.

        Providers return what they know — at minimum a venue name; the
        geocode service fills missing lat/lon. ``None`` when unknown.
        """
        ...

    async def get_events(self, league: League, start: date, end: date) -> list[Event]:
        """Leaderboard events (golf tournaments, …) in [start, end] (UTC dates).

        Each carries a populated leaderboard.  Providers/leagues that are
        not leaderboard sports return ``[]``.
        """
        ...

    async def get_event_state(self, league: League, provider_event_key: str) -> Event | None:
        """Current state of a single leaderboard event, or None if unknown."""
        ...

    async def get_news(self, league: League, team: Team) -> list[NewsItem]:
        """Provider-sourced news for a team.

        Providers without a news source return ``[]``; the news service
        merges this with RSS / Google News feeds.
        """
        ...

    async def get_league_news(self, league: League) -> list[NewsItem]:
        """Competition-wide news for a whole-league (``follow_all``) follow.

        Used when a user follows a whole competition (e.g. the World Cup)
        without picking a team — there is no team to scope by.  Returned
        items carry ``league_id=league.id`` and ``team_id=None``.  Providers
        without a league news source return ``[]``.
        """
        ...

    async def close(self) -> None:
        """Release any held resources (HTTP clients etc.)."""
        ...
