/**
 * API types — mirrors backend/app/schemas.py field-for-field.
 * All datetimes are ISO 8601 UTC strings; localize at render time.
 */

export type Sport =
  | "basketball"
  | "baseball"
  | "soccer"
  | "hockey"
  | "football" // American football
  | "tennis"
  | "mma"
  | "golf"
  | "volleyball";

export type GamePhase =
  | "scheduled"
  | "in_progress"
  | "final"
  | "postponed"
  | "canceled";

export type PlayerStatus = "active" | "day_to_day" | "injured" | "out";

export interface League {
  id: string;
  sport: Sport;
  name: string;
  follow_all: boolean; // followed as a whole competition (no team picks)
}

export interface Team {
  id: string;
  league_id: string;
  name: string;
  abbreviation: string;
  logo_url: string | null;
  color: string | null;
}

export interface TeamsResponse {
  leagues: League[];
  teams: Team[];
}

export interface GameSide {
  team_id: string | null;
  name: string;
  abbreviation: string | null;
  logo_url: string | null; // crest / nation flag (every side, not just followed)
  color: string | null; // "#"-prefixed hex, when known
  score: number | null;
}

export interface Game {
  id: string;
  league_id: string;
  sport: Sport;
  home: GameSide;
  away: GameSide;
  start_time: string;
  venue: string | null;
  series: string | null; // tournament/round or fight card (individual sports)
  phase: GamePhase;
  period: number;
  period_label: string;
  clock: string | null;
  is_intermission: boolean;
  followed_team_ids: string[];
}

export interface LeaderRow {
  position: number;
  position_label: string;
  name: string;
  score: string;
  detail: string | null;
  player_id: string | null;
}

export interface SportEvent {
  id: string;
  league_id: string;
  sport: Sport;
  name: string;
  start_time: string;
  end_time: string | null;
  phase: GamePhase;
  round_label: string;
  venue: string | null;
  followed_player_ids: string[];
  leaderboard: LeaderRow[];
}

export interface TodayResponse {
  date: string; // local calendar day, YYYY-MM-DD
  timezone: string;
  games: Game[];
  events: SportEvent[]; // leaderboard events (golf, …)
}

export interface MapTeam {
  team_id: string;
  name: string;
  abbreviation: string;
  league_id: string;
  league_name: string | null;
  sport: Sport;
  color: string | null;
  logo_url: string | null;
  venue: string | null;
  lat: number;
  lon: number;
  capacity: number | null;
  opened: number | null;
  image_url: string | null;
  location: string | null;
  surface: string | null;
  description: string | null; // club "About" history paragraph (followed teams)
  founded_year: number | null; // founding year (followed teams)
  next_opponent: string | null; // set when the pin is a next-match host venue
  next_match_time: string | null; // ISO 8601 UTC
  group: string | null; // standings group (e.g. "Group A"), for map filtering
  source: "followed" | "competition";
  weather: Weather | null; // current conditions (outdoor sports only)
}

/**
 * An upcoming game placed at its venue's coordinates (map "Upcoming games"
 * mode). Mirrors backend `MapGameOut`.
 */
export interface MapGame {
  game_id: string;
  league_id: string;
  league_name: string | null;
  sport: Sport;
  venue: string | null;
  lat: number;
  lon: number;
  home: GameSide;
  away: GameSide;
  start_time: string; // ISO 8601 UTC
  phase: GamePhase;
  period_label: string;
  group: string | null; // standings group (e.g. "Group A"), when known
  followed: boolean; // involves a team you follow directly
  source: "followed" | "competition";
}

export interface MapResponse {
  teams: MapTeam[];
  games: MapGame[]; // upcoming games within `days`, at venue coordinates
  days: number; // the upcoming-games window reflected by `games`
}

export interface PeriodScore {
  label: string;
  home: number;
  away: number;
}

export interface Performer {
  name: string;
  side: "home" | "away";
  detail: string;
}

export interface TeamStat {
  label: string; // e.g. "Possession", "Shots on Target"
  home: string; // display string (e.g. "53.7%", "15")
  away: string;
}

export interface GamePlay {
  text: string;
  period_label: string;
  clock: string | null;
  team: string | null;
  home_score: number | null;
  away_score: number | null;
  scoring: boolean;
}

export interface GameSummary {
  game_id: string;
  periods: PeriodScore[];
  performers: Performer[];
  team_stats: TeamStat[];
  home_total: number | null;
  away_total: number | null;
  win_probability: number[]; // home win % over time (0–100), chronological
  plays: GamePlay[]; // condensed key-moment play-by-play
}

export interface Weather {
  temperature: number; // current, in `units`
  condition: string; // human label, e.g. "Partly cloudy"
  code: number; // WMO weather code (drives the icon)
  wind_speed: number; // current, in `units`
  units: string; // "metric" | "imperial"
  high: number | null; // today's forecast high
  low: number | null; // today's forecast low
  precip_chance: number | null; // % chance of precipitation
}

export interface LineupSlot {
  player: Player;
  role: string; // position label on the slot ("GK","PG","SS","OH")
  unit: string; // coarse layout group (sport-specific: GK/DEF/MID/FWD, …)
  order: number | null; // 1-based sequence within the lineup, when meaningful
}

export interface TeamLineup {
  formation: string | null; // soccer outfield shape e.g. "4-3-3"; null otherwise
  slots: LineupSlot[];
  bench: Player[];
}

/**
 * Roster-derived, sport-specific projected lineup (positional, NOT confirmed
 * starters). Either side is null when that team has no stored roster.
 */
export interface GameLineup {
  home: TeamLineup | null;
  away: TeamLineup | null;
}

/**
 * Pre-game betting lines + win-probability. Every field is independently
 * nullable: soccer often has no line, a past game has no projection, and a
 * provider may expose one half but not the other. Moneylines are American
 * (+144 / -175); `spread` is the home side's point spread; win % are 0–100.
 */
export interface GameOdds {
  provider: string | null; // sportsbook name, e.g. "DraftKings"
  details: string | null; // display line, e.g. "ATL -175"
  home_moneyline: number | null;
  away_moneyline: number | null;
  spread: number | null; // home point spread (negative = favored)
  over_under: number | null; // game total
  home_win_pct: number | null; // 0–100
  away_win_pct: number | null; // 0–100
}

export interface GameDetail {
  game: Game;
  summary: GameSummary | null;
  weather: Weather | null; // outdoor scheduled games only
  lineup: GameLineup | null; // roster-derived projected lineup
  odds: GameOdds | null; // pre-game lines + win-probability
}

/**
 * A pre-game preview assembled from existing data (odds/win-prob, weather,
 * projected lineups, recent form, head-to-head, injuries). Sides that aren't
 * followed teams come back with empty lineup/injuries.
 */
export interface Matchup {
  game: Game;
  odds: GameOdds | null;
  weather: Weather | null;
  lineup: GameLineup | null;
  home_form: string[]; // recent results "W"/"L"/"D", newest first
  away_form: string[];
  head_to_head: Game[]; // past meetings, newest first
  home_injuries: Player[]; // followed-team players not active
  away_injuries: Player[];
}

export interface StandingRow {
  rank: number;
  team_name: string;
  team_id: string | null;
  logo_url: string | null; // crest/flag for every row, not just followed teams
  abbreviation: string | null; // short code for the fallback chip
  color: string | null; // "#"-prefixed brand hex, when known
  wins: number;
  losses: number;
  draws: number | null;
  points: number | null;
  goal_diff: number | null;
  win_pct: number | null;
  games_back: number | null;
  ot_losses: number | null; // hockey W-L-OTL
  group: string | null; // top grouping: conference / league / table
  subgroup: string | null; // nested grouping: division
}

export interface Standings {
  league_id: string;
  league_name: string;
  sport: Sport;
  season: string;
  fetched_at: string | null;
  rows: StandingRow[];
  is_stale: boolean; // last refresh older than the staleness window
}

export interface Player {
  id: string;
  name: string;
  position: string | null;
  jersey_number: string | null;
  status: PlayerStatus;
  status_detail: string | null;
  stat_line: string | null; // compact season stats, sport-formatted
  career_stat_line: string | null; // same format, career totals/averages
  photo_url: string | null; // headshot / cutout, when available
}

export interface StatLeader {
  rank: number;
  player_id: string;
  name: string;
  position: string | null;
  team_id: string;
  team_name: string;
  team_logo_url: string | null;
  team_color: string | null;
  value: number;
  stat_label: string; // headline stat unit (e.g. "G", "PPG")
  detail: string; // full stat_line
  highlighted: boolean; // on a team you follow
}

export interface StatLeaders {
  league_id: string;
  league_name: string;
  sport: Sport;
  stat_label: string;
  rows: StatLeader[];
}

export interface Scorer {
  rank: number;
  player: string;
  team: string;
  team_logo_url: string | null;
  goals: number;
  highlighted: boolean; // plays for a team you follow
}

export interface Scorers {
  league_id: string;
  league_name: string;
  games_counted: number;
  rows: Scorer[];
}

export interface BracketSide {
  name: string;
  abbreviation: string | null;
  logo_url: string | null;
}

export interface BracketSeries {
  team1: BracketSide;
  team2: BracketSide;
  summary: string; // "NY leads series 3-0"
  conference: string | null; // "East" | "West"; null = championship (center)
}

export interface BracketRound {
  name: string;
  series: BracketSeries[];
}

export interface Bracket {
  league_id: string;
  league_name: string;
  rounds: BracketRound[];
}

export interface NationStanding {
  rank: number;
  wins: number;
  draws: number | null;
  losses: number;
  points: number | null;
  goal_diff: number | null;
}

export interface Nation {
  league_id: string;
  league_name: string;
  name: string;
  abbreviation: string | null;
  logo_url: string | null;
  color: string | null;
  group: string | null;
  standing: NationStanding | null;
  fixtures: Game[];
  results: Game[];
}

export interface Roster {
  team_id: string;
  team_name: string;
  fetched_at: string | null;
  players: Player[];
}

export interface NewsItem {
  id: string;
  team_id: string | null; // set for a followed-team article
  league_id: string | null; // set for a whole-competition article
  title: string;
  url: string;
  source: string;
  published_at: string | null;
  summary: string | null;
  image_url: string | null;
}

/** Scope for the news feed: one followed team, or one whole-competition follow. */
export interface NewsScope {
  teamId?: string;
  leagueId?: string;
}

/** Result of POST /api/news/refresh — count of newly stored articles. */
export interface NewsRefreshResult {
  inserted: number;
}

export interface Meta {
  timezone: string;
  live_poll_seconds: number;
  version: string;
}

// --- Setup & onboarding (mirrors the /setup routes) -----------------------

export interface SetupStatus {
  onboarded: boolean;
  followed_team_count: number;
}

export interface CatalogLeague {
  id: string;
  name: string;
  sport: Sport;
  provider: string;
  national: boolean; // national-team competition
  supports_follow_all: boolean; // offer "follow the whole competition"
  entity_noun: string; // "team" | "player" | "fighter"
  logo_url: string | null; // league logo for the picker
}

export interface CatalogLeaguesResponse {
  leagues: CatalogLeague[];
}

export interface CatalogTeam {
  provider_key: string;
  name: string;
  abbreviation: string;
  logo_url: string | null;
  color: string | null;
}

export interface CatalogTeamsResponse {
  league_id: string;
  teams: CatalogTeam[];
}

export interface FollowSelection {
  league_id: string;
  team_provider_keys: string[];
  follow_all?: boolean; // follow the entire competition
}

export interface FollowRequest {
  selections: FollowSelection[];
}

// --- Notification preferences (Phase 3b) ----------------------------------

export interface NotificationPref {
  scope: string; // "global" | "team:{id}" | "league:{id}"
  label: string;
  muted: boolean;
  events: Record<string, boolean>;
}

export interface NotificationPrefsResponse {
  event_types: string[];
  prefs: NotificationPref[];
}

export interface NotificationPrefUpdate {
  scope: string;
  muted?: boolean;
  events?: Record<string, boolean>;
}
