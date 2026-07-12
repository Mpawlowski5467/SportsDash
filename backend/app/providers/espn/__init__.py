"""ESPN provider adapter.

Maps ESPN's public site API payloads into the normalized domain models.
All parsing is implemented as pure functions over already-fetched JSON
dicts so the normalization logic is unit-testable without any network
I/O; the async protocol methods only fetch and delegate.

``League.provider_key`` is the ESPN sport/league URL fragment (e.g.
``"basketball/nba"``); ``Team.provider_key`` is the ESPN team id.

Package layout (split from the original 3,200-line espn.py; every module
is pure parsers except ``provider``):

- ``common``        — shared constants, coercion, status/period normalization
- ``games``         — team-sport scoreboard/schedule parsing
- ``individual``    — tennis / MMA (athlete-as-team payload quirks)
- ``golf``          — leaderboard events (tournaments)
- ``summary``       — schedule chunking, box score / plays / win-prob / odds
- ``standings``     — standings, seasons, tennis rankings
- ``roster``        — rosters, injuries, stat-line formatting
- ``news_location`` — news articles, home venue / location
- ``provider``      — the EspnProvider class (fetch + delegate)

This ``__init__`` re-exports the public provider plus the parser
entry points the test suite exercises, so ``from app.providers.espn
import X`` keeps working exactly as it did for the single file.
"""

from app.providers.espn.games import _parse_event, _parse_schedule, _parse_scoreboard
from app.providers.espn.individual import _parse_individual_scoreboard
from app.providers.espn.news_location import _parse_news, _parse_team_location
from app.providers.espn.provider import EspnProvider
from app.providers.espn.roster import (
    _career_line_from_overview,
    _format_stat_line,
    _parse_athlete,
    _parse_roster,
    _stat_line_from_overview,
)
from app.providers.espn.standings import _parse_standings, _parse_tennis_rankings
from app.providers.espn.summary import (
    _chunk_date_range,
    _core_event_path,
    _merge_games,
    _parse_game_summary,
    _parse_goals,
    _parse_pickcenter,
    _parse_plays,
    _parse_predictor,
    _parse_summary_state,
    _parse_team_stats,
    _parse_win_probability,
    _play_period_label,
)

__all__ = [
    "EspnProvider",
    "_career_line_from_overview",
    "_chunk_date_range",
    "_core_event_path",
    "_format_stat_line",
    "_merge_games",
    "_parse_athlete",
    "_parse_event",
    "_parse_game_summary",
    "_parse_goals",
    "_parse_individual_scoreboard",
    "_parse_news",
    "_parse_pickcenter",
    "_parse_plays",
    "_parse_predictor",
    "_parse_roster",
    "_parse_schedule",
    "_parse_scoreboard",
    "_parse_standings",
    "_parse_summary_state",
    "_parse_team_location",
    "_parse_team_stats",
    "_parse_tennis_rankings",
    "_parse_win_probability",
    "_play_period_label",
    "_stat_line_from_overview",
]
