/**
 * The League screen's logic, kept pure (no React) so it can be unit-tested:
 * how a standings payload is cut into tables, what a table's head note says,
 * which columns a sport gets, how a leaders board is measured against its
 * leader, what the followed-team panel prints, how a bracket is split.
 *
 * Everything here is derived from payloads the app already receives. Where
 * the mockup shows something the API does not carry (a short city name, a
 * second team colour, a clinch flag), there is no function for it.
 */

import { countWord } from "../../lib/labels";
import { ordinal } from "../../lib/periodLabel";
import { seasonKeys, seasonToken } from "../../lib/seasons";
import { formatShortDate, localDateKey, localKeyFromDate } from "../../lib/time";
import type {
  BracketSeries,
  Game,
  League,
  Scorers,
  Sport,
  StandingRow,
  Standings,
  StatLeaders,
} from "../../types";
import {
  outcomeForTeam,
  standingColumns,
  type Outcome,
  type StandingColumn,
} from "../today/model";

// ── Words ───────────────────────────────────────────────────────────────

const plural = (n: number, one: string, many: string): string =>
  n === 1 ? one : many;

/** "2.5", "10": a gap in games or points, without a trailing ".0". */
export function formatGap(value: number): string {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

// ── The season picker ───────────────────────────────────────────────────

/**
 * The archive key the season in progress is filed under, read from the live
 * table's own season string: "2026" for the 2026-27 Premier League (soccer
 * is keyed by the year a season starts in), "2027" for the 2026-27 NBA.
 *
 * Null while the live table has not loaded, or when its string names no
 * year: there is then nothing to say which key is the live season, and a
 * guess from the calendar would take a real past season off the list.
 */
export function liveSeasonKey(
  liveSeason: string | null | undefined,
  sport: Sport,
  thisYear: number,
): string | null {
  const label = liveSeason ?? undefined;
  if (seasonToken(label) === null) return null;
  return String(seasonKeys(label, sport, thisYear)[0]);
}

/**
 * The season picker's choices without the archive copy of the season in
 * progress. That key is the live option again under another name ("2026
 * season" beside "2026-27 season"), and what it opens is a stored snapshot
 * of the table the live option already shows. It stays while it is the one
 * picked, so the trigger still finds its option.
 */
export function withoutLiveSeason<T extends { id: string }>(
  choices: readonly T[],
  liveKey: string | null,
  selected: string,
): T[] {
  return choices.filter(
    (choice) => choice.id !== liveKey || choice.id === selected,
  );
}

// ── Standings: tables ───────────────────────────────────────────────────

/**
 * Whether a provider group label is just the league's own name again. ESPN
 * labels an ungrouped table with a restatement of the competition
 * ("2026-27 English Premier League" under "Premier League"); a real
 * grouping ("Eastern Conference", "AL West") is not an echo.
 */
export function echoesLeagueName(group: string, league: string): boolean {
  const norm = (value: string) =>
    value.toLowerCase().replace(/[^a-z]+/g, " ").trim();
  const name = norm(league);
  return name !== "" && norm(group).includes(name);
}

export interface StandingTable {
  key: string;
  /** The section tab: a division, a group, or the league itself. */
  title: string;
  /** In rank order. */
  rows: StandingRow[];
}

export interface StandingsLayout {
  /** The conference toggle's options; empty when there is no toggle. */
  groups: string[];
  /** The conference shown; null when there is no toggle. */
  active: string | null;
  tables: StandingTable[];
  /** What first place in a table is called in the legend. */
  leaderWord: string;
}

interface Bucket {
  name: string | null;
  nested: boolean;
  subs: { name: string | null; rows: StandingRow[] }[];
}

/** Rows by `group`, then `subgroup`, in the order the API sent them. */
function bucket(rows: readonly StandingRow[]): Bucket[] {
  const byGroup = new Map<string | null, Map<string | null, StandingRow[]>>();
  for (const row of rows) {
    let subs = byGroup.get(row.group);
    if (subs === undefined) {
      subs = new Map();
      byGroup.set(row.group, subs);
    }
    const list = subs.get(row.subgroup);
    if (list === undefined) subs.set(row.subgroup, [row]);
    else list.push(row);
  }
  return [...byGroup.entries()].map(([name, subs]) => ({
    name,
    nested: [...subs.keys()].some((sub) => sub !== null),
    subs: [...subs.entries()].map(([subName, subRows]) => ({
      name: subName,
      rows: [...subRows].sort((a, b) => a.rank - b.rank),
    })),
  }));
}

/**
 * How a standings payload is laid out on the page.
 *
 * A league with conferences AND divisions shows one conference at a time,
 * a table per division, behind a toggle (as mocked). A league with flat
 * groups (a cup's Group A to L, conferences without divisions) shows every
 * group at once: there is one table each and a toggle would only hide them.
 * A league with one table shows that table under the league's own name.
 *
 * `preferred` is the conference the user picked; when it is not one of this
 * league's, the conference holding a followed team is shown, else the first.
 */
export function layoutStandings(
  standings: Standings,
  preferred: string | null,
  isFollowed: (row: StandingRow) => boolean,
): StandingsLayout {
  const league = standings.league_name;
  const buckets = bucket(standings.rows);
  const label = (name: string | null): string =>
    name === null || echoesLeagueName(name, league) ? league : name;
  const nested = buckets.some((group) => group.nested);

  if (nested) {
    const groups = buckets.map((group) => label(group.name));
    const toggle = buckets.length > 1;
    let shown = buckets;
    let active: string | null = null;
    if (toggle) {
      const followedAt = buckets.findIndex((group) =>
        group.subs.some((sub) => sub.rows.some(isFollowed)),
      );
      const preferredAt = preferred === null ? -1 : groups.indexOf(preferred);
      const at = preferredAt !== -1 ? preferredAt : Math.max(0, followedAt);
      shown = [buckets[at]];
      active = groups[at];
    }
    return {
      groups: toggle ? groups : [],
      active,
      tables: shown.flatMap((group) =>
        group.subs.map((sub) => ({
          key: `${group.name ?? ""}\u0000${sub.name ?? ""}`,
          title: sub.name ?? label(group.name),
          rows: sub.rows,
        })),
      ),
      leaderWord: "Division leader",
    };
  }

  if (buckets.length <= 1) {
    // The league's one table goes under the league's own name, whatever
    // the provider labels it: that label is the season's sentence, which
    // only sometimes restates the league ("2026", "2026-2027 Portuguese
    // Liga" under "Liga Portugal").
    return {
      groups: [],
      active: null,
      tables: buckets.map((group) => ({
        key: group.name ?? "",
        title: league,
        rows: group.subs[0]?.rows ?? [],
      })),
      leaderWord: "League leader",
    };
  }
  const tables = buckets.map((group) => ({
    key: group.name ?? "",
    title: label(group.name),
    rows: group.subs[0]?.rows ?? [],
  }));
  const every = (pattern: RegExp) =>
    tables.every((table) => pattern.test(table.title));
  const leaderWord = every(/^group\b/i)
    ? "Group leader"
    : every(/conference/i)
      ? "Conference leader"
      : "Table leader";
  return { groups: [], active: null, tables, leaderWord };
}

/** The sports whose leagues are split into conferences and divisions. */
const DIVISION_SPORTS: ReadonlySet<Sport> = new Set<Sport>([
  "baseball",
  "basketball",
  "hockey",
  "football",
]);

/**
 * Whether a league's standings are most likely one table, before they have
 * loaded. Only the placement of the loading placeholder depends on it (so
 * the blocks around it do not jump when the table arrives); what is drawn
 * once the payload is in is decided by the payload alone.
 */
export function expectsOneTable(sport: Sport): boolean {
  return !DIVISION_SPORTS.has(sport);
}

// ── Standings: columns ──────────────────────────────────────────────────

export interface LeagueColumn extends StandingColumn {
  /** Width in rem, sized to what the column holds. */
  width: number;
}

/**
 * Column widths by what they hold: two or three digits for a count, a
 * signed two-digit goal difference, ".574", "13.5". W, L, PCT and GB are
 * the mockup's (28, 30, 44 and 40px); the rest are measured the same way,
 * and kept tight enough that soccer's five columns leave a team name its
 * room at phone width.
 */
const COLUMN_REM: Record<string, number> = {
  w: 1.75,
  l: 1.875,
  d: 1.625,
  t: 1.625,
  otl: 2.25,
  gd: 2.25,
  pct: 2.75,
  gb: 2.5,
  pts: 2.25,
};

const signed = (value: number | null): string =>
  value === null ? "—" : value > 0 ? `+${value}` : String(value);

/** Tennis rankings carry only a points total, in the thousands. */
const RANKING_POINTS: LeagueColumn = {
  key: "pts",
  label: "Pts",
  value: (row) => (row.points === null ? "—" : String(row.points)),
  ranked: true,
  width: 4,
};

/**
 * The full table's columns for a sport. The compact Today set where it is
 * the whole story; soccer adds goal difference; tennis is a ranking with
 * one number; anything else falls to wins, losses, percentage and games
 * back, as the standings view always has.
 */
export function leagueColumns(sport: Sport): LeagueColumn[] {
  if (sport === "tennis") return [RANKING_POINTS];
  const base = standingColumns(sport) ?? standingColumns("baseball") ?? [];
  const columns: StandingColumn[] =
    sport === "soccer"
      ? [
          ...base.slice(0, -1),
          { key: "gd", label: "GD", value: (row) => signed(row.goal_diff) },
          ...base.slice(-1),
        ]
      : base;
  return columns.map((column) => ({
    ...column,
    width: COLUMN_REM[column.key] ?? 2.5,
  }));
}

/** Rankings list people: no team block, no team page, no race to lead. */
export function isRankingTable(sport: Sport): boolean {
  return sport === "tennis" || sport === "mma";
}

/** Sports with a team table; the followed-team panel is drawn only for these. */
export function hasTeamTable(sport: Sport): boolean {
  return standingColumns(sport) !== null;
}

// ── Standings: who leads, and by how much ───────────────────────────────

/** What a table's race is counted in; null when it is not a race (rankings). */
export function raceUnit(sport: Sport): "games" | "points" | null {
  switch (sport) {
    case "baseball":
    case "basketball":
    case "football":
      return "games";
    case "soccer":
    case "hockey":
    case "volleyball":
      return "points";
    default:
      return null;
  }
}

/**
 * How far `row` is behind `leader`, in the sport's unit; null when the
 * payload cannot say.
 *
 * Games behind is the provider's own figure when it is measured from this
 * table's leader (the leader's own figure is then zero or absent), and is
 * otherwise worked out from the two records, ((W1 - W) + (L - L1)) / 2,
 * which is what the figure means. Points sports are a plain difference.
 */
export function gapBehind(
  row: StandingRow,
  leader: StandingRow,
  sport: Sport,
): number | null {
  const unit = raceUnit(sport);
  if (unit === "points") {
    if (row.points === null || leader.points === null) return null;
    return leader.points - row.points;
  }
  if (unit === "games") {
    if (row.games_back !== null && (leader.games_back ?? 0) === 0) {
      return row.games_back;
    }
    return (leader.wins - row.wins + (row.losses - leader.losses)) / 2;
  }
  return null;
}

/**
 * The note beside a table's tab: "Tampa Bay Rays ahead by 2.5 games ·
 * Athletics 10 back". Points for the sports ranked on points. The followed
 * teams in the table are named with their own gap. Null when the table is
 * not a race, has one row, or the payload cannot say.
 *
 * "Ahead by", not "leads by": only full names are sent, and a verb would
 * have to agree with "Rays" and with "Manchester City" alike.
 */
export function leadNote(
  rows: readonly StandingRow[],
  sport: Sport,
  isFollowed: (row: StandingRow) => boolean,
): string | null {
  const unit = raceUnit(sport);
  const [leader, second] = rows;
  if (unit === null || leader === undefined || second === undefined) return null;
  const one = unit === "games" ? "game" : "point";
  const parts: string[] = [];
  const named = new Set<StandingRow>([leader]);

  const lead = gapBehind(second, leader, sport);
  if (lead !== null && lead > 0) {
    parts.push(
      `${leader.team_name} ahead by ${formatGap(lead)} ${plural(lead, one, `${one}s`)}`,
    );
  } else if (lead === 0) {
    named.add(second);
    parts.push(
      unit === "points" && leader.points !== null
        ? `${leader.team_name} and ${second.team_name} level on ${leader.points} ${plural(leader.points, "point", "points")}`
        : `${leader.team_name} and ${second.team_name} level`,
    );
  }

  for (const row of rows) {
    if (named.has(row) || !isFollowed(row)) continue;
    const back = gapBehind(row, leader, sport);
    if (back === null || back < 0) continue;
    parts.push(
      back === 0 ? `${row.team_name} level` : `${row.team_name} ${formatGap(back)} back`,
    );
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

// ── Team identity ───────────────────────────────────────────────────────

export interface TeamIdentity {
  name: string;
  abbreviation: string | null;
  color: string | null;
}

/** One source of team identities; later sources fill what earlier ones lack. */
export type IdentitySource = readonly {
  name: string;
  abbreviation?: string | null;
  color?: string | null;
}[];

/** Sports whose "teams" are people: there is no catalog of clubs to join. */
const NO_CATALOG: ReadonlySet<Sport> = new Set<Sport>([
  "tennis",
  "mma",
  "golf",
  "racing",
]);

/**
 * Whether the setup catalog (`/setup/teams/{league}`, every team's colour)
 * exists for a league: ESPN team sports only. The one statement of that
 * rule, for every view that joins the catalog to colour its blocks.
 */
export function hasCatalog(league: Pick<League, "provider" | "sport">): boolean {
  return league.provider === "espn" && !NO_CATALOG.has(league.sport);
}

const nameKey = (name: string): string =>
  name.trim().toLowerCase().replace(/\s+/g, " ");

export type TeamLookup = (
  name: string,
  abbreviation?: string | null,
) => TeamIdentity | null;

/**
 * A lookup of the teams in one league, by name or by code, merged from
 * every payload that describes them: the standings rows (every team's code),
 * the followed teams (their colour) and the setup catalog (every team's
 * colour). A standings row for a team that is not followed carries no
 * colour of its own, which is why the block needs the join.
 *
 * A leaders board names a team that is not followed by its code alone
 * ("HOU"), so a name that is itself a known code resolves too.
 */
export function teamLookup(sources: readonly IdentitySource[]): TeamLookup {
  const byName = new Map<string, TeamIdentity>();
  const byCode = new Map<string, TeamIdentity>();
  for (const source of sources) {
    for (const entry of source) {
      const key = nameKey(entry.name);
      if (key === "") continue;
      let identity = byName.get(key);
      if (identity === undefined) {
        identity = { name: entry.name, abbreviation: null, color: null };
        byName.set(key, identity);
      }
      identity.abbreviation ??= entry.abbreviation || null;
      identity.color ??= entry.color || null;
      const code = identity.abbreviation?.toUpperCase();
      if (code !== undefined && !byCode.has(code)) byCode.set(code, identity);
    }
  }
  return (name, abbreviation) =>
    byName.get(nameKey(name)) ??
    (abbreviation ? byCode.get(abbreviation.toUpperCase()) : undefined) ??
    byCode.get(name.trim().toUpperCase()) ??
    null;
}

// ── Leaders ─────────────────────────────────────────────────────────────

/** Drop a trailing ".0" so an integer stat reads "3", not "3.0". */
export function formatStat(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** A value as a share of the leader's, in percent to one decimal. */
export function shareOfLeader(value: number, leader: number): number {
  if (!(leader > 0) || !Number.isFinite(value)) return 0;
  const share = Math.round((value / leader) * 1000) / 10;
  return Math.min(100, Math.max(0, share));
}

export interface BoardRow {
  key: string;
  rank: number;
  name: string;
  /** The team as the payload names it: a full name, or a bare code. */
  team: string;
  /** The followed team's id; null for a team that is not followed. */
  teamId: string | null;
  teamColor: string | null;
  position: string | null;
  photoUrl: string | null;
  value: number;
  /**
   * What the value counts. A board built from rosters takes each player's
   * own leading stat, so two rows of one board can count different things.
   */
  unit: string;
  /** The full stat line, on boards built from rosters. */
  detail: string | null;
  /** On a team you follow. */
  highlighted: boolean;
  /** The player is followed. */
  followed: boolean;
}

export interface Board {
  /** `scorers` is goals tallied from finished matches; `stat` the stat board. */
  kind: "scorers" | "stat";
  /** The section tab. */
  title: string;
  /** The value column's header, and the unit beside a value. */
  unit: string;
  /**
   * Where the board comes from, in a few words: the league for a board the
   * provider ranks league-wide, otherwise what it was built from.
   */
  source: string;
  /** Ranked across the whole league, not built from what is followed. */
  leagueWide: boolean;
  /** Player photos are part of the stat board only. */
  photos: boolean;
  rows: BoardRow[];
}

/** The stat a board is ranked by, named, where the unit alone is a code. */
const STAT_TITLES: Record<string, string> = {
  "baseball:HR": "Home run leaders",
  "basketball:PPG": "Scoring leaders",
  "hockey:PTS": "Points leaders",
};

function statTitle(sport: Sport, unit: string): string {
  const known = STAT_TITLES[`${sport}:${unit.toUpperCase()}`];
  if (known !== undefined) return known;
  return unit === "" ? "Leaders" : `${unit} leaders`;
}

/**
 * The league's leaders board: the Golden Boot when finished matches have
 * goals to count, otherwise the stat board. Null when neither has a row.
 *
 * The stat board is league-wide only for the leagues the provider ranks
 * (it then lists players of teams that are not followed, whose `team_id`
 * is empty); otherwise it is built from the followed teams' rosters, and
 * the note says so rather than implying a league ranking.
 */
export function leadersBoard(
  scorers: Scorers | undefined,
  leaders: StatLeaders | undefined,
): Board | null {
  if (scorers !== undefined && scorers.rows.length > 0) {
    const games = scorers.games_counted;
    return {
      kind: "scorers",
      title: "Golden Boot",
      unit: "Goals",
      source: `${games} ${plural(games, "match", "matches")} counted`,
      leagueWide: false,
      photos: false,
      rows: scorers.rows.map((row) => ({
        key: `${row.team}-${row.player}`,
        rank: row.rank,
        name: row.player,
        team: row.team,
        teamId: null,
        teamColor: null,
        position: null,
        photoUrl: null,
        value: row.goals,
        unit: row.goals === 1 ? "goal" : "goals",
        detail: null,
        highlighted: row.highlighted,
        followed: false,
      })),
    };
  }
  if (leaders === undefined || leaders.rows.length === 0) return null;
  const leagueWide = leaders.rows.some((row) => row.team_id === "");
  return {
    kind: "stat",
    title: statTitle(leaders.sport, leaders.stat_label),
    unit: leaders.stat_label,
    source: leagueWide ? leaders.league_name : "Your teams' rosters",
    leagueWide,
    photos: true,
    rows: leaders.rows.map((row) => ({
      key: `${row.team_id}-${row.player_id}`,
      rank: row.rank,
      name: row.name,
      team: row.team_name,
      teamId: row.team_id === "" ? null : row.team_id,
      teamColor: row.team_color,
      position: row.position,
      photoUrl: row.photo_url,
      value: row.value,
      unit: row.stat_label,
      detail: row.detail === "" ? null : row.detail,
      highlighted: row.highlighted,
      followed: row.followed,
    })),
  };
}

/**
 * The rows the Standings page shows with bars: the top of the board, as far
 * as the rows can be measured against its leader. A league-wide board and
 * the Golden Boot count one thing throughout. A roster-built board can mix
 * stats (a quarterback's yards, a kicker's field goals), and a bar that
 * compared those would mean nothing, so only the rows counting what the
 * leader counts are kept, numbered in their own order.
 */
export function topOfBoard(board: Board, limit: number): BoardRow[] {
  if (board.kind === "scorers") return board.rows.slice(0, limit);
  const comparable = board.rows.filter((row) => row.unit === board.unit);
  if (comparable.length === board.rows.length) return board.rows.slice(0, limit);
  return comparable
    .slice(0, limit)
    .map((row, index) => ({ ...row, rank: index + 1 }));
}

/**
 * The note beside the top-of-the-board block on the Standings page: "MLB
 * top ten · each bar is measured against the leader's 35". Only a board
 * ranked across the league is called the league's top ten.
 */
export function boardNote(board: Board, shown: number): string {
  const top = `top ${countWord(shown)}`;
  const first = topOfBoard(board, 1)[0];
  const lead = first === undefined ? "" : formatStat(first.value);
  const who = board.leagueWide ? `${board.source} ${top}` : `${board.source} · ${top}`;
  return `${who} · each bar is measured against the leader's ${lead}`;
}

/**
 * The value column's width in rem for the 40px numerals: wide enough for
 * the longest value shown, never narrower than the mockup's two digits.
 */
export function statColumnRem(values: readonly string[]): number {
  const widest = values.reduce((max, text) => {
    const digits = text.replace(/[^0-9]/g, "").length;
    return Math.max(max, digits * 1.1 + (text.length - digits) * 0.5);
  }, 0);
  return Math.max(3.5, Math.ceil(widest * 4) / 4);
}

// ── The followed-team panel ─────────────────────────────────────────────

/** Evening starts at five, as on Today: a game after it is "tonight". */
const EVENING_HOUR = 17;

/** "Tonight", "Today" or "Jul 31": when a fixture is, against the clock. */
export function fixtureDay(startTime: string, now: Date): string {
  if (localDateKey(startTime) !== localKeyFromDate(now)) {
    return formatShortDate(startTime);
  }
  return new Date(startTime).getHours() >= EVENING_HOUR ? "Tonight" : "Today";
}

/** Whether the team is at home in a game; null when it is not in it. */
export function venueSide(game: Game, teamId: string): "home" | "away" | null {
  if (game.home.team_id === teamId) return "home";
  if (game.away.team_id === teamId) return "away";
  return null;
}

/** The games still to come, soonest first: scheduled or in progress. */
export function upcomingFixtures(
  schedule: readonly Game[],
  teamId: string,
  limit: number,
): Game[] {
  return schedule
    .filter(
      (game) =>
        (game.phase === "scheduled" || game.phase === "in_progress") &&
        venueSide(game, teamId) !== null,
    )
    .sort((a, b) => a.start_time.localeCompare(b.start_time))
    .slice(0, limit);
}

/** "4 home · 2 away" for the fixtures listed. */
export function venueSummary(fixtures: readonly Game[], teamId: string): string {
  const home = fixtures.filter((game) => venueSide(game, teamId) === "home").length;
  const away = fixtures.filter((game) => venueSide(game, teamId) === "away").length;
  return [home > 0 ? `${home} home` : null, away > 0 ? `${away} away` : null]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** The results that have one, from the team's point of view, in order. */
export function teamOutcomes(results: readonly Game[], teamId: string): Outcome[] {
  return results
    .map((game) => outcomeForTeam(game, teamId))
    .filter((outcome): outcome is Outcome => outcome !== null);
}

/** "1 win · 4 losses", with draws where there are any. */
export function outcomeSummary(outcomes: readonly Outcome[]): string {
  const count = (letter: Outcome) =>
    outcomes.filter((outcome) => outcome === letter).length;
  const wins = count("W");
  const draws = count("D");
  const losses = count("L");
  return [
    wins > 0 ? `${wins} ${plural(wins, "win", "wins")}` : null,
    draws > 0 ? `${draws} ${plural(draws, "draw", "draws")}` : null,
    losses > 0 ? `${losses} ${plural(losses, "loss", "losses")}` : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** "Last five, most recent first": the label follows how many there are. */
export function formLabel(count: number): string {
  return count === 1 ? "Last game" : `Last ${countWord(count)}, most recent first`;
}

/** "2–4": a final score with the team's own figure first; null unscored. */
export function scoreFor(game: Game, teamId: string): string | null {
  const side = venueSide(game, teamId);
  if (side === null) return null;
  const mine = game[side].score;
  const theirs = game[side === "home" ? "away" : "home"].score;
  return mine === null || theirs === null ? null : `${mine}–${theirs}`;
}

export interface HeroStat {
  figure: string;
  label: string;
}

/**
 * The two figures beside a followed team's record: its place, and where it
 * stands in the race in the sport's own terms. Games back for baseball and
 * basketball (games ahead for the leader); points for the sports ranked on
 * them; win percentage for football, which has no games-back figure.
 */
export function heroStats(
  row: StandingRow,
  table: readonly StandingRow[],
  sport: Sport,
  tableTitle: string,
): HeroStat[] {
  const stats: HeroStat[] = [
    { figure: ordinal(row.rank), label: `in ${tableTitle}` },
  ];
  const unit = raceUnit(sport);
  if (unit === "points") {
    if (row.points !== null) {
      stats.push({
        figure: String(row.points),
        label: plural(row.points, "point", "points"),
      });
    }
    return stats;
  }
  if (sport === "football") {
    if (row.win_pct !== null) {
      const fixed = row.win_pct.toFixed(3);
      stats.push({
        figure: fixed.startsWith("0") ? fixed.slice(1) : fixed,
        label: "win pct",
      });
    }
    return stats;
  }
  if (unit !== "games") return stats;
  const [leader, second] = table;
  if (leader === undefined) return stats;
  if (row === leader) {
    const lead = second === undefined ? null : gapBehind(second, leader, sport);
    if (lead !== null && lead > 0) {
      stats.push({
        figure: formatGap(lead),
        label: `${plural(lead, "game", "games")} ahead`,
      });
    }
    return stats;
  }
  const back = gapBehind(row, leader, sport);
  if (back !== null && back >= 0) {
    stats.push({
      figure: formatGap(back),
      label: `${plural(back, "game", "games")} back`,
    });
  }
  return stats;
}

/**
 * The record's size in rem. 72px as mocked for "45–63"; a longer record
 * (a three-part soccer or hockey one) steps down so the figures beside it
 * keep their place on the line.
 */
export function recordRem(record: string): number {
  if (record.length <= 6) return 4.5;
  return record.length === 7 ? 3.75 : 3;
}

/** "in the AL West", "in LaLiga": the article only where the name takes one. */
export function inTable(title: string): string {
  const takesArticle =
    /\b(east|west|central|north|south|northeast|northwest|southeast|southwest|atlantic|pacific|metropolitan|division|conference|league)$/i.test(
      title.trim(),
    );
  return takesArticle ? `in the ${title}` : `in ${title}`;
}

// ── Bracket ─────────────────────────────────────────────────────────────

export type RoundKey = "r32" | "r16" | "qf" | "sf" | "third" | "final";

export const ROUNDS: readonly { key: RoundKey; label: string }[] = [
  { key: "r32", label: "Round of 32" },
  { key: "r16", label: "Round of 16" },
  { key: "qf", label: "Quarterfinals" },
  { key: "sf", label: "Semifinals" },
  { key: "third", label: "Third Place" },
  { key: "final", label: "Final" },
];

/** Which soccer knockout round a fixture belongs to, from its slot names. */
export function roundOf(game: Game): RoundKey | null {
  const text = `${game.home.name} ${game.away.name}`;
  if (/semifinal/i.test(text)) return /loser/i.test(text) ? "third" : "final";
  if (/quarterfinal/i.test(text)) return "sf";
  if (/round of 16/i.test(text)) return "qf";
  if (/round of 32/i.test(text)) return "r16";
  if (/group|place/i.test(text)) return "r32";
  return null;
}

/** Split an ordered list into [left, right] halves for a symmetric draw. */
export function halve<T>(items: readonly T[]): { left: T[]; right: T[] } {
  const mid = Math.ceil(items.length / 2);
  return { left: items.slice(0, mid), right: items.slice(mid) };
}

/**
 * Split one playoff round's series into left (East) / right (West) / center
 * (the championship). Uses the conference label when the provider sends it;
 * a round with no labels is a lone series in the center (the final) or is
 * halved by order, so the bracket stays two-sided on sparse data.
 */
export function splitRound(series: readonly BracketSeries[]): {
  left: BracketSeries[];
  right: BracketSeries[];
  center: BracketSeries[];
} {
  const east = series.filter((s) => s.conference === "East");
  const west = series.filter((s) => s.conference === "West");
  const unlabeled = series.filter(
    (s) => s.conference !== "East" && s.conference !== "West",
  );
  if (east.length > 0 || west.length > 0) {
    return { left: east, right: west, center: unlabeled };
  }
  if (series.length <= 1) {
    return { left: [], right: [], center: [...series] };
  }
  const { left, right } = halve(series);
  return { left, right, center: [] };
}

/** Trim the provider's slot text ("Round of 32 1 Winner" to "R32 #1"). */
export function shortSlot(name: string): string {
  const r32 = name.match(/^Round of 32 (\d+) Winner$/i);
  if (r32) return `R32 #${r32[1]}`;
  const r16 = name.match(/^Round of 16 (\d+) Winner$/i);
  if (r16) return `R16 #${r16[1]}`;
  const qf = name.match(/^Quarterfinal (\d+) Winner$/i);
  if (qf) return `QF #${qf[1]}`;
  const sf = name.match(/^Semifinal (\d+) (Winner|Loser)$/i);
  if (sf) return `SF${sf[1]} ${sf[2] === "Winner" ? "W" : "L"}`;
  return name;
}

/** The sports whose playoffs the provider sends as best-of-N series. */
const SERIES_SPORTS: ReadonlySet<Sport> = new Set<Sport>([
  "basketball",
  "baseball",
  "hockey",
]);

/**
 * What kind of bracket a league can have: a cup whose knockout fixtures are
 * in the schedule, a playoff of series, or none. A cup is known only by its
 * fixtures, so a soccer league with none drawn has no bracket to show.
 */
export function bracketKind(
  sport: Sport,
  hasKnockoutFixtures: boolean,
): "cup" | "playoff" | null {
  if (hasKnockoutFixtures) return "cup";
  return SERIES_SPORTS.has(sport) ? "playoff" : null;
}
