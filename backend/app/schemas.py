"""Pydantic response models — the REST API contract.

``frontend/src/types.ts`` mirrors these shapes field-for-field; change
them together.  All datetimes serialize as ISO 8601 UTC.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LeagueOut(BaseModel):
    id: str
    sport: str
    name: str
    provider: str  # data source id ("espn", "thesportsdb") — gates season archives
    follow_all: bool = False  # followed as a whole competition (no team picks)


class TeamOut(BaseModel):
    id: str
    league_id: str
    name: str
    abbreviation: str
    logo_url: str | None = None
    color: str | None = None


class TeamsOut(BaseModel):
    leagues: list[LeagueOut]
    teams: list[TeamOut]


class GameSideOut(BaseModel):
    team_id: str | None = None  # internal slug when this side is a followed team
    name: str
    abbreviation: str | None = None
    logo_url: str | None = None  # crest / nation flag (every side, not just followed)
    color: str | None = None  # "#"-prefixed hex, when the provider has one
    score: int | None = None  # None until the game has started


class GameOut(BaseModel):
    id: str
    league_id: str
    sport: str
    home: GameSideOut
    away: GameSideOut
    start_time: datetime  # UTC
    venue: str | None = None
    series: str | None = None  # tournament/round or fight card (individual sports)
    phase: str  # scheduled | in_progress | final | postponed | canceled
    period: int
    period_label: str
    clock: str | None = None
    is_intermission: bool
    followed_team_ids: list[str]


class LeaderRowOut(BaseModel):
    position: int
    position_label: str
    name: str
    score: str
    detail: str | None = None
    player_id: str | None = None


class EventOut(BaseModel):
    id: str
    league_id: str
    sport: str
    name: str
    start_time: datetime  # UTC
    end_time: datetime | None = None
    phase: str
    round_label: str
    venue: str | None = None
    followed_player_ids: list[str]
    leaderboard: list[LeaderRowOut]


class PeriodScoreOut(BaseModel):
    label: str
    home: int
    away: int


class PerformerOut(BaseModel):
    name: str
    side: str  # "home" | "away"
    detail: str


class TeamStatOut(BaseModel):
    label: str  # e.g. "Possession", "Shots on Target"
    home: str  # display string (e.g. "53.7%", "15")
    away: str


class GoalOut(BaseModel):
    player: str
    team: str  # scoring team's display name
    minute: str | None = None
    own_goal: bool = False
    penalty: bool = False


class GamePlayOut(BaseModel):
    text: str
    period_label: str
    clock: str | None = None
    team: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    scoring: bool = False


class GameSummaryOut(BaseModel):
    game_id: str
    periods: list[PeriodScoreOut] = []
    performers: list[PerformerOut] = []
    team_stats: list[TeamStatOut] = []
    goals: list[GoalOut] = []
    home_total: int | None = None
    away_total: int | None = None
    # Home-side win probability over time (0–100, chronological).
    win_probability: list[float] = []
    # Condensed key-moment play-by-play.
    plays: list[GamePlayOut] = []


class WeatherOut(BaseModel):
    temperature: float  # current temperature, in `units`
    condition: str  # human label, e.g. "Partly cloudy"
    code: int  # WMO weather code (drives the frontend icon)
    wind_speed: float  # current wind speed, in `units`
    units: str  # "metric" | "imperial"
    high: float | None = None  # today's forecast high
    low: float | None = None  # today's forecast low
    precip_chance: int | None = None  # % chance of precipitation


class GameOddsOut(BaseModel):
    provider: str | None = None  # sportsbook name, e.g. "DraftKings"
    details: str | None = None  # display line, e.g. "ATL -175"
    home_moneyline: int | None = None  # American odds
    away_moneyline: int | None = None
    spread: float | None = None  # home point spread (negative = favored)
    over_under: float | None = None  # game total
    home_win_pct: float | None = None  # 0–100
    away_win_pct: float | None = None  # 0–100


class GameDetailOut(BaseModel):
    game: GameOut
    summary: GameSummaryOut | None = None
    # Outdoor scheduled games only: a short forecast for the home venue.
    weather: WeatherOut | None = None
    # Pre-game betting lines + win-probability; null when the provider
    # exposes neither (e.g. most soccer) or the game is past.
    odds: GameOddsOut | None = None
    # Roster-derived, sport-specific projected lineup (positional, not a
    # confirmed gameday XI); null when neither side has a stored roster.
    # Defined below (after PlayerOut); GameDetailOut.model_rebuild() resolves it.
    lineup: "GameLineupOut | None" = None


class MapTeamOut(BaseModel):
    team_id: str
    name: str
    abbreviation: str
    league_id: str
    league_name: str | None = None
    sport: str
    color: str | None = None
    logo_url: str | None = None
    venue: str | None = None
    lat: float
    lon: float
    capacity: int | None = None
    opened: int | None = None
    image_url: str | None = None
    location: str | None = None
    surface: str | None = None
    # Club "About": history paragraph + founding year (followed teams only,
    # enriched from TheSportsDB / Wikipedia); null for competition pins.
    description: str | None = None
    founded_year: int | None = None
    # The match that puts a team at this venue (host-country tournaments):
    # set when the pin is a next-match host venue rather than a home ground.
    next_opponent: str | None = None
    next_match_time: datetime | None = None  # UTC
    group: str | None = None  # standings group (e.g. "Group A"), for map filtering
    # "followed" = a team you follow; "competition" = plotted because you
    # follow its whole competition (shown only while that competition runs).
    source: str = "followed"
    # Current conditions at the venue (outdoor sports only); None otherwise.
    weather: WeatherOut | None = None


class MapGameOut(BaseModel):
    """An upcoming game placed at its venue's coordinates (map "games" mode)."""

    game_id: str
    league_id: str
    league_name: str | None = None
    sport: str
    venue: str | None = None
    lat: float
    lon: float
    home: GameSideOut
    away: GameSideOut
    start_time: datetime  # UTC
    phase: str  # scheduled | in_progress
    period_label: str = ""
    group: str | None = None  # standings group (e.g. "Group A"), when known
    followed: bool = False  # involves a team you follow directly
    # Why it's on the map: "followed" (a team you follow) vs "competition"
    # (a whole tournament/league you follow). Mirrors MapTeamOut.source.
    source: str = "followed"


class MapOut(BaseModel):
    teams: list[MapTeamOut]  # followed teams with resolved coordinates
    games: list[MapGameOut] = []  # upcoming games (within `days`) at venue coords
    days: int = 3  # the upcoming-games window reflected by `games`


class TodayOut(BaseModel):
    date: str  # local calendar day, YYYY-MM-DD
    timezone: str
    games: list[GameOut]  # sorted by start_time ascending
    events: list[EventOut] = []  # active/today leaderboard events (golf, …)


class StandingRowOut(BaseModel):
    rank: int
    team_name: str
    team_id: str | None = None
    logo_url: str | None = None  # team crest/flag (every row, not just followed)
    abbreviation: str | None = None  # short code for the fallback chip
    color: str | None = None  # "#"-prefixed brand hex, when known
    wins: int
    losses: int
    draws: int | None = None
    points: int | None = None
    goal_diff: int | None = None
    win_pct: float | None = None
    games_back: float | None = None
    ot_losses: int | None = None  # hockey W-L-OTL
    group: str | None = None  # top grouping: conference / league / table
    subgroup: str | None = None  # nested grouping: division


class StandingsOut(BaseModel):
    league_id: str
    league_name: str
    sport: str
    season: str
    fetched_at: datetime | None = None
    rows: list[StandingRowOut]
    # True when the data's last refresh is older than the staleness window
    # (or it's being served from the last-good cache after a fetch failure).
    is_stale: bool = False


class PlayerOut(BaseModel):
    id: str
    name: str
    position: str | None = None
    jersey_number: str | None = None
    status: str  # active | day_to_day | injured | out
    status_detail: str | None = None
    stat_line: str | None = None  # compact season stats, sport-formatted
    career_stat_line: str | None = None  # same format, career totals/averages
    photo_url: str | None = None  # headshot / cutout, when available


class RosterOut(BaseModel):
    team_id: str
    team_name: str
    fetched_at: datetime | None = None
    players: list[PlayerOut]


class LineupSlotOut(BaseModel):
    player: PlayerOut
    role: str  # position label on the slot ("GK","PG","SS","OH")
    unit: str  # coarse layout group (sport-specific: GK/DEF/MID/FWD, …)
    order: int | None = None  # 1-based sequence within the lineup, when meaningful


class TeamLineupOut(BaseModel):
    formation: str | None = None  # soccer outfield shape e.g. "4-3-3"; null otherwise
    slots: list[LineupSlotOut] = []
    bench: list[PlayerOut] = []


class GameLineupOut(BaseModel):
    """Roster-derived projected lineup (positional, NOT confirmed starters).

    Either side is null when that team has no stored roster (an unfollowed
    opponent / a whole-competition team).
    """

    home: TeamLineupOut | None = None
    away: TeamLineupOut | None = None


# GameDetailOut.lineup forward-references GameLineupOut (defined here, after
# PlayerOut); resolve it now that the lineup models exist.
GameDetailOut.model_rebuild()


class MatchupOut(BaseModel):
    """A pre-game preview, assembled from already-available pieces.

    Everything here is reused from existing endpoints (odds, weather,
    lineups, results, rosters) — no new provider call.  Form is the recent
    results strip ("W"/"L"/"D", newest first); head-to-head is past meetings
    between the two sides; injuries are each side's stored roster players who
    are not active.  Sides that aren't followed teams (e.g. World Cup
    nations) simply come back with empty lineups/injuries.
    """

    game: GameOut
    odds: GameOddsOut | None = None
    weather: WeatherOut | None = None
    lineup: GameLineupOut | None = None
    home_form: list[str] = []
    away_form: list[str] = []
    head_to_head: list[GameOut] = []
    home_injuries: list[PlayerOut] = []
    away_injuries: list[PlayerOut] = []


class StatLeaderOut(BaseModel):
    rank: int
    player_id: str
    name: str
    position: str | None = None
    team_id: str = ""  # internal slug when on a followed team, else ""
    team_name: str
    team_logo_url: str | None = None
    team_color: str | None = None
    value: float  # the ranked headline-stat value
    stat_label: str  # the headline stat's unit (e.g. "G", "PPG")
    detail: str  # the player's full stat_line
    highlighted: bool = False  # the player is on a team you follow


class StatLeadersOut(BaseModel):
    league_id: str
    league_name: str
    sport: str
    # The headline stat the board is ranked by (the leading stat of the
    # roster line); "" when no followed team in the league has stat lines.
    stat_label: str = ""
    rows: list[StatLeaderOut] = []


class ScorerOut(BaseModel):
    rank: int
    player: str
    team: str
    team_logo_url: str | None = None
    goals: int
    highlighted: bool = False  # the scorer plays for a team you follow


class ScorersOut(BaseModel):
    league_id: str
    league_name: str
    games_counted: int  # finished matches the tally is built from
    rows: list[ScorerOut] = []


class NationStandingOut(BaseModel):
    rank: int
    wins: int
    draws: int | None = None
    losses: int
    points: int | None = None
    goal_diff: int | None = None


class BracketSideOut(BaseModel):
    name: str
    abbreviation: str | None = None
    logo_url: str | None = None


class BracketSeriesOut(BaseModel):
    team1: BracketSideOut
    team2: BracketSideOut
    summary: str  # human series status, e.g. "NY leads series 3-0"
    # "East" / "West" for two-sided placement; None for the league
    # championship (center) and sports without conferences.
    conference: str | None = None


class BracketRoundOut(BaseModel):
    name: str
    series: list[BracketSeriesOut] = []


class BracketOut(BaseModel):
    league_id: str
    league_name: str
    rounds: list[BracketRoundOut] = []  # empty when the league has no playoffs


class NationOut(BaseModel):
    """A competition team's mini-dashboard (e.g. a World Cup nation).

    Built by name, since whole-competition teams have no followed-team row.
    """

    league_id: str
    league_name: str
    name: str
    abbreviation: str | None = None
    logo_url: str | None = None
    color: str | None = None
    group: str | None = None
    standing: NationStandingOut | None = None
    fixtures: list[GameOut] = []  # upcoming (scheduled / in progress)
    results: list[GameOut] = []  # finished, most recent first


class NewsItemOut(BaseModel):
    id: str
    team_id: str | None = None  # set for a followed-team article
    league_id: str | None = None  # set for a whole-competition article
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    summary: str | None = None
    image_url: str | None = None


class NewsRefreshOut(BaseModel):
    """Result of a manual ``POST /api/news/refresh``."""

    inserted: int


class MetaOut(BaseModel):
    timezone: str
    live_poll_seconds: int
    version: str


# ---------------------------------------------------------------------------
# Setup & onboarding
# ---------------------------------------------------------------------------


class SetupStatusOut(BaseModel):
    onboarded: bool
    followed_team_count: int


class CatalogLeagueOut(BaseModel):
    id: str
    name: str
    sport: str
    provider: str
    national: bool = False  # national-team competition (wizard grouping)
    supports_follow_all: bool = False  # offer "follow the whole competition"
    entity_noun: str = "team"  # what a pickable item is: team | player | fighter
    logo_url: str | None = None  # league logo for the picker


class CatalogLeaguesOut(BaseModel):
    leagues: list[CatalogLeagueOut]


class CatalogTeamOut(BaseModel):
    provider_key: str
    name: str
    abbreviation: str
    logo_url: str | None = None
    color: str | None = None


class CatalogTeamsOut(BaseModel):
    league_id: str
    teams: list[CatalogTeamOut]


class FollowSelection(BaseModel):
    league_id: str
    team_provider_keys: list[str] = []
    follow_all: bool = False  # follow the entire competition (no team picks needed)


class FollowRequest(BaseModel):
    selections: list[FollowSelection]


# ---------------------------------------------------------------------------
# Notification preferences (Phase 3b)
# ---------------------------------------------------------------------------


class NotificationPrefOut(BaseModel):
    scope: str  # "global" | "team:{id}" | "league:{id}"
    label: str  # human label (team/league name or "All notifications")
    muted: bool
    events: dict[str, bool]  # event_type -> enabled


class NotificationPrefsOut(BaseModel):
    event_types: list[str]  # the orderable set of notifiable event types
    prefs: list[NotificationPrefOut]


class NotificationPrefUpdate(BaseModel):
    scope: str
    muted: bool | None = None
    events: dict[str, bool] | None = None
