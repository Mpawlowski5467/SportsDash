/**
 * The Today view's logic, kept pure (no React) so it can be unit-tested:
 * which side is followed, who is ahead, what a standings row says in words,
 * how a line score is laid out, what goes in each cell of the week strip.
 *
 * Everything here is derived from payloads the app already receives. Where
 * the mockup shows something the API does not carry (hits and errors, the
 * runners on base, a short city name), there is no function for it.
 */

import {
  isIndividualSport,
  shortMatchup,
  shortSide,
  showsScore,
  sideLabel,
} from "../../lib/labels";
import { ordinal, periodLabel } from "../../lib/periodLabel";
import { dateKeyInZone, formatTime, parseLocalDateKey, localKeyFromDate } from "../../lib/time";
import type {
  Game,
  GameOdds,
  GameSide,
  GameSummary,
  Sport,
  SportEvent,
  StandingRow,
  Standings,
} from "../../types";

export type SideKey = "home" | "away";
export type Outcome = "W" | "L" | "D";

export { isIndividualSport, showsScore };

// ── Sides ───────────────────────────────────────────────────────────────

export function isFollowedSide(game: Game, side: GameSide): boolean {
  return side.team_id !== null && game.followed_team_ids.includes(side.team_id);
}

/**
 * The one followed side, or null when neither side is followed (a
 * whole-competition game) or both are: then there is no single point of
 * view to call a result a win or a loss from.
 */
export function soleFollowedSide(game: Game): SideKey | null {
  const home = isFollowedSide(game, game.home);
  const away = isFollowedSide(game, game.away);
  if (home === away) return null;
  return home ? "home" : "away";
}

/** Who is ahead on the scoreboard, live or final; null level or unscored. */
export function leadingSide(game: Game): SideKey | null {
  const { home, away } = game;
  if (home.score === null || away.score === null) return null;
  if (home.score === away.score) return null;
  return home.score > away.score ? "home" : "away";
}

/** A scored game's result from one side's point of view. */
export function outcomeFor(game: Game, side: SideKey): Outcome | null {
  const mine = game[side];
  const theirs = game[side === "home" ? "away" : "home"];
  if (mine.score === null || theirs.score === null) return null;
  if (mine.score === theirs.score) return "D";
  return mine.score > theirs.score ? "W" : "L";
}

/** The result for a given team id; null when that team is not in the game. */
export function outcomeForTeam(game: Game, teamId: string): Outcome | null {
  if (game.home.team_id === teamId) return outcomeFor(game, "home");
  if (game.away.team_id === teamId) return outcomeFor(game, "away");
  return null;
}

/**
 * The result as the team saw it, or null when there is none to tell.
 *
 * A fight's score is a count of rounds won, so level counts do not mean a
 * draw: a bout is only called one when its method of victory says so, and
 * a no-contest is no result at all.
 */
export function resultFor(game: Game, teamId: string): Outcome | null {
  const outcome = outcomeForTeam(game, teamId);
  if (game.sport !== "mma") return outcome;
  const inGame = game.home.team_id === teamId || game.away.team_id === teamId;
  if (!inGame) return null;
  const method = game.fight_result?.method;
  if (method === "no_contest") return null;
  if (method === "draw") return "D";
  return outcome === "D" ? null : outcome;
}

/**
 * The result as the followed team saw it, when exactly one side is
 * followed (see `resultFor` for what counts as a result).
 */
export function followedOutcome(game: Game): Outcome | null {
  const side = soleFollowedSide(game);
  const teamId = side === null ? null : game[side].team_id;
  return teamId === null ? null : resultFor(game, teamId);
}

/**
 * The live game that gets the big treatment: the first one a followed team
 * is playing in, else simply the first. The rest are listed as rows.
 */
export function pickHero(live: readonly Game[]): Game | null {
  return (
    live.find((game) => game.followed_team_ids.length > 0) ?? live[0] ?? null
  );
}

// ── Days ────────────────────────────────────────────────────────────────

/** A "YYYY-MM-DD" key moved by whole days. */
export function dayKeyOffset(key: string, days: number): string {
  const date = parseLocalDateKey(key);
  date.setDate(date.getDate() + days);
  return localKeyFromDate(date);
}

/** Evening starts at five: a slate entirely after it is "tonight". */
const EVENING_HOUR = 17;

function allInEvening(games: readonly Game[]): boolean {
  return (
    games.length > 0 &&
    games.every((game) => new Date(game.start_time).getHours() >= EVENING_HOUR)
  );
}

/** "Tonight" only when it is: an afternoon game is not tonight. */
export function upcomingTitle(games: readonly Game[]): string {
  return allInEvening(games) ? "Tonight" : "Later today";
}

export function yesterdayTitle(games: readonly Game[]): string {
  return allInEvening(games) ? "Last night" : "Yesterday";
}

/** What the start of a game is called in each sport. */
const START_NOUN: Partial<Record<Sport, string>> = {
  baseball: "First pitch",
  basketball: "Tip-off",
  football: "Kickoff",
  soccer: "Kickoff",
  hockey: "Puck drop",
  tennis: "First serve",
  volleyball: "First serve",
  mma: "First bout",
};

/**
 * "First pitch in 12 minutes" for the next game to start; past the hour it
 * is "Kickoff at 7:10 PM". Null when nothing is still to start.
 */
export function nextStartNote(games: readonly Game[], now: Date): string | null {
  const next = games
    .filter((game) => game.phase === "scheduled")
    .sort((a, b) => a.start_time.localeCompare(b.start_time))[0];
  if (next === undefined) return null;
  const noun = START_NOUN[next.sport] ?? "Starts";
  const minutes = Math.round(
    (new Date(next.start_time).getTime() - now.getTime()) / 60_000,
  );
  if (minutes <= 0) return `${noun} any moment`;
  if (minutes < 60) {
    return `${noun} in ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  return `${noun} at ${formatTime(next.start_time)}`;
}

// ── Standings in words ──────────────────────────────────────────────────

/** "45–63", or W–D–L for soccer and W–L–OTL for hockey, as each sport writes it. */
export function recordText(row: StandingRow, sport: Sport): string {
  if (sport === "soccer") {
    return `${row.wins}–${row.draws ?? 0}–${row.losses}`;
  }
  if (sport === "hockey" && row.ot_losses !== null) {
    return `${row.wins}–${row.losses}–${row.ot_losses}`;
  }
  if (sport === "football" && row.draws !== null && row.draws > 0) {
    return `${row.wins}–${row.losses}–${row.draws}`;
  }
  return `${row.wins}–${row.losses}`;
}

/**
 * The name of the table a row is ranked in. A division where there is one;
 * otherwise the league itself, because a single-table league's `group` only
 * repeats the season's name.
 */
export function tableName(row: StandingRow, standings: Standings): string {
  if (row.subgroup) return row.subgroup;
  const groups = new Set(standings.rows.map((r) => r.group ?? ""));
  if (row.group && groups.size > 1) return row.group;
  return standings.league_name;
}

/** The row a game side refers to: by id when followed, else by name. */
export function standingRowFor(
  standings: Standings | undefined,
  side: GameSide,
): StandingRow | undefined {
  if (standings === undefined) return undefined;
  return (
    standings.rows.find(
      (row) => row.team_id !== null && row.team_id === side.team_id,
    ) ?? standings.rows.find((row) => row.team_name === side.name)
  );
}

export interface StandingColumn {
  key: string;
  label: string;
  value: (row: StandingRow) => string;
  /** The column the table is ranked by, set in bold. */
  ranked?: boolean;
}

/** ".667": three decimals, leading zero stripped. */
function pct(value: number | null): string {
  if (value === null) return "—";
  const fixed = value.toFixed(3);
  return fixed.startsWith("0") ? fixed.slice(1) : fixed;
}

function gamesBack(value: number | null): string {
  if (value === null || value === 0) return "—";
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

const count = (value: number | null): string =>
  value === null ? "—" : String(value);

/**
 * The compact table's columns for a sport, four at most so it fits the side
 * column. Null for sports with no team table (tennis rankings, MMA, golf,
 * racing): those get no standings box.
 */
export function standingColumns(sport: Sport): StandingColumn[] | null {
  const wins: StandingColumn = { key: "w", label: "W", value: (r) => String(r.wins) };
  const losses: StandingColumn = { key: "l", label: "L", value: (r) => String(r.losses) };
  const points: StandingColumn = {
    key: "pts",
    label: "Pts",
    value: (r) => count(r.points),
    ranked: true,
  };
  switch (sport) {
    case "baseball":
    case "basketball":
      return [
        wins,
        losses,
        { key: "pct", label: "PCT", value: (r) => pct(r.win_pct), ranked: true },
        { key: "gb", label: "GB", value: (r) => gamesBack(r.games_back) },
      ];
    case "soccer":
      return [
        wins,
        { key: "d", label: "D", value: (r) => count(r.draws) },
        losses,
        points,
      ];
    case "hockey":
      return [
        wins,
        losses,
        { key: "otl", label: "OTL", value: (r) => count(r.ot_losses) },
        points,
      ];
    case "football":
      return [
        wins,
        losses,
        { key: "t", label: "T", value: (r) => count(r.draws) },
        { key: "pct", label: "PCT", value: (r) => pct(r.win_pct), ranked: true },
      ];
    case "volleyball":
      return [wins, losses, points];
    default:
      return null;
  }
}

export interface StandingsSlice {
  /** The table's name, for the section tab. */
  title: string;
  /**
   * In rank order. Not always consecutive: a followed team outside the
   * window is added to it, so the drawing code looks for gaps in `rank`.
   */
  rows: StandingRow[];
  /** 1-based places of the window, and the table's size. */
  from: number;
  to: number;
  total: number;
}

/** The longest table shown whole; a longer one is cut to a window. */
const WHOLE_TABLE = 8;
const WINDOW = 7;

/**
 * The tables on Today: one per division (or single table) that holds a
 * followed team. A long single table is cut to a window around the
 * followed team, since the side column is no place for twenty rows.
 */
export function followedTables(standings: Standings): StandingsSlice[] {
  const tables = new Map<string, StandingRow[]>();
  for (const row of standings.rows) {
    const key = `${row.group ?? ""}\u0000${row.subgroup ?? ""}`;
    const rows = tables.get(key);
    if (rows === undefined) tables.set(key, [row]);
    else rows.push(row);
  }
  const slices: StandingsSlice[] = [];
  for (const rows of tables.values()) {
    const followed = rows.findIndex((row) => row.team_id !== null);
    if (followed === -1) continue;
    const sorted = [...rows].sort((a, b) => a.rank - b.rank);
    const at = sorted.findIndex((row) => row.team_id !== null);
    let start = 0;
    let end = sorted.length;
    if (sorted.length > WHOLE_TABLE) {
      start = Math.max(0, Math.min(at - Math.floor(WINDOW / 2), sorted.length - WINDOW));
      end = start + WINDOW;
    }
    slices.push({
      title: tableName(sorted[at], standings),
      // The window, plus any other followed team that falls outside it: two
      // followed teams at either end of a long table are both on the page.
      rows: sorted.filter(
        (row, index) =>
          (index >= start && index < end) || row.team_id !== null,
      ),
      from: start + 1,
      to: end,
      total: sorted.length,
    });
  }
  return slices;
}

// ── Odds and win probability ────────────────────────────────────────────

export interface Favorite {
  /** "LAD 58%", or "LAD -175" when only a moneyline is priced. */
  label: string;
  /** Both sides' numbers, for the tooltip. */
  title: string;
  /** Away and home win %, when priced: the two halves of the bar. */
  split: { away: number; home: number } | null;
}

/**
 * The favoured side before or during a game: its win probability, falling
 * back to the favoured moneyline when only a line is priced. Null when
 * there is nothing priced at all (most soccer, every individual sport).
 */
export function favoriteOf(game: Game, odds: GameOdds | undefined): Favorite | null {
  if (odds === undefined || game.phase === "final") return null;
  const home = sideLabel(game.home);
  const away = sideLabel(game.away);
  if (odds.home_win_pct !== null && odds.away_win_pct !== null) {
    const homeFavored = odds.home_win_pct >= odds.away_win_pct;
    const share = Math.round(homeFavored ? odds.home_win_pct : odds.away_win_pct);
    return {
      label: `${homeFavored ? home : away} ${share}%`,
      title:
        `Win probability: ${away} ${Math.round(odds.away_win_pct)}%, ` +
        `${home} ${Math.round(odds.home_win_pct)}%`,
      split: { away: odds.away_win_pct, home: odds.home_win_pct },
    };
  }
  const homeLine = odds.home_moneyline;
  const awayLine = odds.away_moneyline;
  if (homeLine === null && awayLine === null) return null;
  const homeFavored =
    homeLine !== null && awayLine !== null ? homeLine <= awayLine : homeLine !== null;
  const line = homeFavored ? homeLine : awayLine;
  if (line === null) return null;
  const label = homeFavored ? home : away;
  return {
    label: `${label} ${line > 0 ? "+" : ""}${line}`,
    title: odds.details ?? `${label} favored`,
    split: null,
  };
}

/**
 * The in-game win probability: the last point of the summary's series (the
 * home side's chance now). Null when the provider sent no series; the
 * pre-game projection is deliberately not used as a stand-in, because
 * mid-game it would be a different number under the same label.
 */
export function liveWinProbability(
  summary: GameSummary | null | undefined,
): { away: number; home: number } | null {
  const series = summary?.win_probability ?? [];
  const home = series[series.length - 1];
  if (home === undefined || !Number.isFinite(home)) return null;
  const clamped = Math.min(100, Math.max(0, home));
  return { away: 100 - clamped, home: clamped };
}

// ── Line score ──────────────────────────────────────────────────────────

export interface LineScoreColumn {
  label: string;
  /** Null for a period not played yet, or a half still to bat. */
  away: number | null;
  home: number | null;
  /** The period being played now. */
  current: boolean;
}

export interface LineScore {
  columns: LineScoreColumn[];
  /** "R" in baseball, "T" elsewhere. */
  totalLabel: string;
  awayTotal: number | null;
  homeTotal: number | null;
}

/** A baseball line score is always drawn nine innings wide. */
const INNINGS = 9;

/**
 * The period-by-period table for a game, or null when the provider has no
 * summary for it (individual sports, volleyball) or no period has been
 * played. Baseball is padded to nine innings with empty cells.
 *
 * The backend pads a side that has not batted with 0, which reads as a
 * scoreless half. While the game is live the period label says which half
 * is on ("Top 5", "Mid 5": the home side has not batted in the 5th), so
 * that one cell is blanked here rather than shown as a zero.
 */
export function lineScoreOf(
  game: Game,
  summary: GameSummary | null | undefined,
): LineScore | null {
  const periods = summary?.periods ?? [];
  if (summary === null || summary === undefined || periods.length === 0) {
    return null;
  }
  const live = game.phase === "in_progress";
  const baseball = game.sport === "baseball";
  const homeYetToBat = baseball && live && /^(top|mid)/i.test(game.period_label);

  const columns: LineScoreColumn[] = periods.map((period, index) => {
    const current = live && index + 1 === game.period;
    return {
      label: period.label,
      away: period.away,
      home: current && homeYetToBat ? null : period.home,
      current,
    };
  });
  if (baseball) {
    for (let inning = columns.length + 1; inning <= INNINGS; inning += 1) {
      columns.push({
        label: String(inning),
        away: null,
        home: null,
        current: live && inning === game.period,
      });
    }
  }
  return {
    columns,
    totalLabel: baseball ? "R" : "T",
    awayTotal: summary.away_total ?? game.away.score,
    homeTotal: summary.home_total ?? game.home.score,
  };
}

/**
 * Where a live game stands: "Bottom 5th", "2nd Half" with the clock beside
 * it, or "Half time" at a break that carries no label of its own.
 */
export function liveStatus(game: Game): { headline: string; detail: string | null } {
  const headline = periodLabel(game, "long") || "Live";
  // No clock at a break: it is stopped, and "Half time 45'" says two things.
  return { headline, detail: game.is_intermission ? null : game.clock };
}

export { ordinal };

// ── The week strip ──────────────────────────────────────────────────────

/**
 * "ATH vs BOS" from the followed side's point of view (it is at home),
 * "ATH at CIN" when it travels; "ARI at LAD" with no single followed side,
 * and "Sinner vs Alcaraz" for people.
 */
export function weekLine(game: Game): string {
  if (soleFollowedSide(game) === "home" && !isIndividualSport(game.sport)) {
    return `${shortSide(game, game.home)} vs ${shortSide(game, game.away)}`;
  }
  return shortMatchup(game);
}

export interface WeekDay {
  key: string;
  date: Date;
  isToday: boolean;
  /** At most MAX_LINES, in start order. */
  lines: string[];
  /** How many more there were. */
  more: number;
}

/** A cell holds four lines; a whole-competition follow can have a dozen. */
const MAX_LINES = 4;

/**
 * Seven days from today, each with its followed games and any leaderboard
 * event (a golf tournament, a race weekend) running that day. Days are the
 * server's days: `zone` is the timezone the `/today` payload reports.
 */
export function weekStrip(
  todayKey: string,
  games: readonly Game[],
  events: readonly SportEvent[],
  zone: string | undefined,
): WeekDay[] {
  const byDay = new Map<string, { at: string; text: string }[]>();
  const add = (key: string, at: string, text: string) => {
    const lines = byDay.get(key);
    if (lines === undefined) byDay.set(key, [{ at, text }]);
    else lines.push({ at, text });
  };
  for (const game of games) {
    if (game.phase === "postponed" || game.phase === "canceled") continue;
    add(dateKeyInZone(game.start_time, zone), game.start_time, weekLine(game));
  }
  const days: WeekDay[] = [];
  for (let offset = 0; offset < 7; offset += 1) {
    const key = dayKeyOffset(todayKey, offset);
    for (const event of events) {
      const first = dateKeyInZone(event.start_time, zone);
      const last = dateKeyInZone(event.end_time ?? event.start_time, zone);
      if (key >= first && key <= last) add(key, event.start_time, event.name);
    }
    const all = (byDay.get(key) ?? [])
      .sort((a, b) => a.at.localeCompare(b.at))
      .map((line) => line.text);
    days.push({
      key,
      date: parseLocalDateKey(key),
      isToday: offset === 0,
      lines: all.slice(0, MAX_LINES),
      more: Math.max(0, all.length - MAX_LINES),
    });
  }
  return days;
}
