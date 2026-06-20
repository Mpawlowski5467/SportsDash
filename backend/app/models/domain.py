"""Normalized internal domain models.

Every provider adapter maps its upstream payloads into these shapes.
Nothing outside ``app/providers/`` may depend on provider-specific
fields; the rest of the app only ever sees these dataclasses.

All datetimes are timezone-aware UTC.  Localization happens only at the
API response boundary / in the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Sport(str, Enum):
    BASKETBALL = "basketball"
    BASEBALL = "baseball"
    SOCCER = "soccer"
    HOCKEY = "hockey"
    FOOTBALL = "football"   # American football
    TENNIS = "tennis"       # individual: a match is two players
    MMA = "mma"             # individual: a bout is two fighters
    GOLF = "golf"           # leaderboard: one event, a field of players
    VOLLEYBALL = "volleyball"  # two-sided, set-scored (score = sets won)


# Sports where a "team" is really one athlete (a player, fighter, or
# golfer).  The picker/label wording changes; for tennis/mma the Game
# model still has two sides, while golf uses the Event model instead.
INDIVIDUAL_SPORTS = frozenset({Sport.TENNIS, Sport.MMA, Sport.GOLF})

# Sports modeled as leaderboard Events rather than two-sided Games.
LEADERBOARD_SPORTS = frozenset({Sport.GOLF})

# Sports usually played outdoors, where venue weather is meaningful.  Indoor
# sports (basketball, hockey, volleyball, mma) skip the weather lookup.
WEATHER_SPORTS = frozenset(
    {Sport.SOCCER, Sport.BASEBALL, Sport.FOOTBALL, Sport.GOLF, Sport.TENNIS}
)


class GamePhase(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"


class PlayerStatus(str, Enum):
    ACTIVE = "active"
    DAY_TO_DAY = "day_to_day"
    INJURED = "injured"
    OUT = "out"


class EventType(str, Enum):
    STARTING_SOON = "starting_soon"
    GAME_START = "game_start"
    PERIOD_START = "period_start"
    INTERMISSION = "intermission"
    FINAL = "final"


@dataclass(frozen=True)
class League:
    id: str                # internal slug, unique across the app
    sport: Sport
    name: str
    provider: str          # provider id serving this league ("espn", "thesportsdb", ...)
    provider_key: str      # provider-specific league key (e.g. a URL path fragment)
    follow_all: bool = False   # sync every fixture, not just followed teams'


@dataclass(frozen=True)
class Team:
    id: str                # internal slug
    league_id: str
    name: str
    abbreviation: str
    provider_key: str      # provider-specific team id
    logo_url: str | None = None
    color: str | None = None           # hex color used for calendar/badges
    rss_feeds: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeamLocation:
    """Where a team plays — for the map view, plus stadium facts.

    Providers supply the venue name and, when they have them, coordinates
    and facts; the geocode service fills missing lat/lon from the venue
    name, and a stadium-enrichment source (TheSportsDB) fills facts.
    """
    venue: str | None = None
    lat: float | None = None
    lon: float | None = None
    capacity: int | None = None        # seating capacity
    opened: int | None = None          # year opened
    image_url: str | None = None       # stadium photo
    location: str | None = None        # city / country text
    surface: str | None = None         # pitch surface / roof, when known


@dataclass(frozen=True)
class Player:
    id: str                # provider-scoped stable id
    team_id: str           # internal team slug
    name: str
    position: str | None = None
    jersey_number: str | None = None
    status: PlayerStatus = PlayerStatus.ACTIVE
    status_detail: str | None = None   # e.g. "Out - ankle"
    stat_line: str | None = None       # compact season stats, sport-formatted
                                       # e.g. "24.1 PPG · 7.8 REB · 5.2 AST"
    career_stat_line: str | None = None  # same format, career totals/averages
    photo_url: str | None = None       # headshot / cutout, when the provider has one


@dataclass(frozen=True)
class GameState:
    """Live snapshot of a game, normalized across sports.

    ``period`` counts whole periods in the sport's native unit (quarter,
    half, inning) and is 0 before tip-off.  ``period_label`` is the
    human-readable form ("Q3", "2nd Half", "Top 5").  Providers are
    responsible for producing both so that downstream code (the event
    detector in particular) never needs sport-specific logic.
    """
    game_id: str
    phase: GamePhase
    home_score: int
    away_score: int
    period: int = 0
    period_label: str = ""
    clock: str | None = None       # "07:42" for clocked sports, None otherwise
    is_intermission: bool = False  # halftime / between periods, per provider
    last_update: datetime | None = None


@dataclass(frozen=True)
class Game:
    id: str                        # internal id: f"{provider}:{provider_game_key}"
    league_id: str
    home_name: str
    away_name: str
    start_time: datetime           # UTC, tz-aware
    home_team_id: str | None = None    # internal slug when the side is a followed team
    away_team_id: str | None = None
    home_abbreviation: str | None = None
    away_abbreviation: str | None = None
    home_logo_url: str | None = None   # team crest / nation flag
    away_logo_url: str | None = None
    home_color: str | None = None      # "#"-prefixed hex
    away_color: str | None = None
    venue: str | None = None
    series: str | None = None   # tournament/round or card context, e.g. "Wimbledon · QF"
    state: GameState | None = None

    @property
    def provider_game_key(self) -> str:
        return self.id.split(":", 1)[1]


@dataclass(frozen=True)
class StandingRow:
    rank: int
    team_name: str
    wins: int
    losses: int
    team_id: str | None = None      # internal slug when it's a followed team
    # Display metadata carried per-row so EVERY team in a table shows its
    # crest — not just the handful the user follows (followed teams resolve
    # these via /teams; everyone else has no TeamORM row, so the provider
    # supplies them here).
    logo_url: str | None = None
    abbreviation: str | None = None
    color: str | None = None        # "#"-prefixed hex, when the provider has one
    draws: int | None = None        # soccer
    points: int | None = None       # soccer
    goal_diff: int | None = None    # soccer
    win_pct: float | None = None    # basketball / baseball / football
    games_back: float | None = None # basketball / baseball
    ot_losses: int | None = None    # hockey (W-L-OTL records)
    group: str | None = None        # top grouping: conference / league / table name
    subgroup: str | None = None     # nested grouping: division within a conference


@dataclass(frozen=True)
class Standings:
    league_id: str
    season: str
    rows: tuple[StandingRow, ...]
    fetched_at: datetime


@dataclass(frozen=True)
class Roster:
    team_id: str
    players: tuple[Player, ...]
    fetched_at: datetime


@dataclass(frozen=True)
class LineupSlot:
    """One placed starter in a game lineup, with layout hints for the diagram."""
    player: Player
    role: str            # position label shown on the slot ("GK","CB","PG","SS","OH")
    unit: str            # coarse group the frontend lays out by (sport-specific:
                         # soccer GK/DEF/MID/FWD; baseball P/C/IF/OF/DH; etc.)
    order: int | None = None  # 1-based sequence within the lineup, when meaningful


@dataclass(frozen=True)
class TeamLineup:
    formation: str | None              # soccer outfield shape e.g. "4-3-3"; None otherwise
    slots: tuple[LineupSlot, ...]      # the arranged starting group
    bench: tuple[Player, ...]          # remaining squad members


@dataclass(frozen=True)
class GameLineup:
    """Roster-derived, sport-specific projected lineup for a game.

    No free provider exposes confirmed gameday lineups (ESPN's box score
    only names the top performers), so a "lineup" is built from each side's
    stored roster — the real players arranged into the sport's starting
    shape by their listed positions.  It is a positional / depth-chart view,
    NOT a confirmed starting XI.  Either side is ``None`` when that team has
    no roster (an unfollowed opponent / a whole-competition team).
    """
    game_id: str
    sport: Sport
    home: TeamLineup | None = None
    away: TeamLineup | None = None


@dataclass(frozen=True)
class LeaderRow:
    """One competitor's line on a leaderboard Event (e.g. a golf field)."""
    position: int               # leaderboard rank (1 = leader); ties share via display
    position_label: str         # display form: "T3", "1", "CUT", "WD"
    name: str
    score: str                  # to-par or total, provider's display ("-10", "E", "+3")
    detail: str | None = None   # "thru 14", "F", "Round 2", tee time
    player_id: str | None = None  # internal team/athlete id when followed


@dataclass(frozen=True)
class Event:
    """A leaderboard competition (golf tournament, etc.) — not two-sided.

    The Event model is the leaderboard counterpart to :class:`Game`:
    one competition with a field of competitors ranked on a board,
    rather than a home/away pairing.
    """
    id: str                     # internal id: f"{provider}:{provider_event_key}"
    league_id: str
    name: str
    start_time: datetime        # UTC, tz-aware
    phase: GamePhase
    end_time: datetime | None = None
    round_label: str = ""       # "Round 2", "Final Round", "" before start
    venue: str | None = None
    leaderboard: tuple[LeaderRow, ...] = ()
    last_update: datetime | None = None

    @property
    def provider_event_key(self) -> str:
        return self.id.split(":", 1)[1]


@dataclass(frozen=True)
class PeriodScore:
    """One period's scoring line for both sides (a box-score column)."""
    label: str             # "Q1", "1st Half", "Top 5", "Set 2", "P1", ...
    home: int
    away: int


@dataclass(frozen=True)
class Performer:
    """A notable performer in a game's box score."""
    name: str
    side: str              # "home" | "away"
    detail: str            # stat line, e.g. "28 PTS, 11 REB" / "2 G, 1 A"


@dataclass(frozen=True)
class TeamStat:
    """One team-vs-team box-score stat, as display strings.

    Values keep the provider's display form (so "53.7%", "15", "0.1")
    rather than being coerced to a number.
    """
    label: str             # e.g. "Possession", "Shots on Target"
    home: str
    away: str


@dataclass(frozen=True)
class Goal:
    """A single goal in a match (soccer), for box scores + the Golden Boot."""
    player: str
    team: str               # scoring team's display name
    minute: str | None = None   # display clock, e.g. "28'"
    own_goal: bool = False  # credited against the scorer's own side
    penalty: bool = False


@dataclass(frozen=True)
class GamePlay:
    """One notable moment in a game's run of play (a play-by-play row).

    A condensed timeline of KEY moments (scoring plays, goals, cards) — not
    every pitch/possession — built from a provider's summary feed.  Scores
    are the running totals after the play, when the feed carries them.
    """
    text: str                       # human description, e.g. "Goal! ..."
    period_label: str               # "2nd Half", "Top 5", "Q3", ...
    clock: str | None = None        # display clock, e.g. "67'", "4:21"
    team: str | None = None         # team credited with the play, when known
    home_score: int | None = None   # running score after the play
    away_score: int | None = None
    scoring: bool = False           # whether this play changed the score


@dataclass(frozen=True)
class GameSummary:
    """On-demand box score for a single game (never stored).

    Built from a provider's summary endpoint; populated best-effort, so
    any field may be empty when the provider doesn't expose it.
    """
    game_id: str
    periods: tuple[PeriodScore, ...] = ()
    performers: tuple[Performer, ...] = ()
    team_stats: tuple[TeamStat, ...] = ()
    goals: tuple[Goal, ...] = ()
    home_total: int | None = None
    away_total: int | None = None
    # Home-side win probability over the course of the game (0–100), in
    # chronological order — drives the live win-probability chart.
    win_probability: tuple[float, ...] = ()
    # Condensed key-moment play-by-play (scoring plays / goals / cards).
    plays: tuple[GamePlay, ...] = ()


@dataclass(frozen=True)
class Weather:
    """Current conditions + a one-day forecast for a venue (Open-Meteo).

    Best-effort and never stored; values are already in the requested
    ``units`` so the frontend only picks the unit label (°C/°F, km·h/mph).
    """
    temperature: float          # current temperature, in the requested units
    condition: str              # human label, e.g. "Partly cloudy"
    code: int                   # WMO weather code (drives the frontend icon)
    wind_speed: float           # current wind speed, in the requested units
    units: str                  # "metric" | "imperial"
    high: float | None = None   # today's forecast high
    low: float | None = None    # today's forecast low
    precip_chance: int | None = None  # % chance of precipitation (daily max)


@dataclass(frozen=True)
class GameOdds:
    """Pre-game betting lines + win-probability for a single game (never stored).

    Odds come from the provider's summary ``pickcenter`` (a sportsbook
    consensus line); win-probability from the provider's projection feed.
    Both are best-effort and volatile, so every field is independently
    nullable — soccer often has no lines, a finished game has no projection,
    and a provider may expose one half but not the other.  Moneylines are
    American (+144 / -175); ``spread`` is the home side's point spread;
    ``over_under`` is the total; win percentages are 0–100.
    """
    provider: str | None = None        # sportsbook name, e.g. "DraftKings"
    details: str | None = None         # display line, e.g. "ATL -175"
    home_moneyline: int | None = None
    away_moneyline: int | None = None
    spread: float | None = None        # home point spread (negative = favored)
    over_under: float | None = None    # game total
    home_win_pct: float | None = None  # 0–100
    away_win_pct: float | None = None  # 0–100


@dataclass(frozen=True)
class NewsItem:
    id: str                # stable hash of the url
    team_id: str | None    # set for a followed-team article
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    summary: str | None = None
    image_url: str | None = None
    league_id: str | None = None   # set for a whole-competition (follow_all) article


@dataclass(frozen=True)
class GameEvent:
    """A notification-worthy transition, with ready-to-send text."""
    type: EventType
    game_id: str
    title: str         # notification title
    message: str       # notification body
    dedupe_key: str    # globally unique per logical event
