/**
 * The Results view's logic, kept pure (no React) so it can be unit-tested:
 * a game as one team saw it, the form over the last few games, which
 * standings table may be asked for, and what goes in a "Next up" cell.
 *
 * Everything is derived from payloads the app already receives. Where the
 * mockup shows something the API does not carry (a pitcher of record,
 * attendance, a game's duration, a team's second colour) there is no
 * function for it.
 */

import {
  countWord,
  isIndividualSport,
  sportWords,
  type SportWords,
} from "../../lib/labels";
import { seasonKeys, seasonToken } from "../../lib/seasons";
import { dateKeyInZone, weekdayShort } from "../../lib/time";
import type {
  Game,
  GameSide,
  Sport,
  StandingRow,
  Standings,
  Team,
} from "../../types";
import {
  resultFor,
  standingColumns,
  tableName,
  type Outcome,
} from "../today/model";

export type { Outcome };

/** How many recent games the form tiles cover. */
export const FORM_GAMES = 8;
/** How many stored results are asked for, as the view always has. */
export const RESULTS_LIMIT = 50;
/** How many games the "Next up" strip holds. */
export const NEXT_UP_GAMES = 4;
/** How far ahead the strip looks: the window the backend keeps stored. */
export const NEXT_UP_DAYS = 45;
/** Past this many followed teams the toggle row gives way to a dropdown. */
export const TEAM_BUTTONS_MAX = 6;

export function teamSwitcherKind(teamCount: number): "buttons" | "select" {
  return teamCount > TEAM_BUTTONS_MAX ? "select" : "buttons";
}

// ── A game from one team's point of view ────────────────────────────────

export interface Perspective {
  mine: GameSide;
  theirs: GameSide;
  /** The team is the home side. */
  home: boolean;
}

/**
 * Which side of a game the team is. By id, as the results endpoints tag it;
 * by name as a fallback for a row that lost its tag. Null when the team is
 * not in the game at all.
 */
export function perspective(
  game: Game,
  team: Pick<Team, "id" | "name">,
): Perspective | null {
  if (game.home.team_id === team.id) {
    return { mine: game.home, theirs: game.away, home: true };
  }
  if (game.away.team_id === team.id) {
    return { mine: game.away, theirs: game.home, home: false };
  }
  if (game.home.name === team.name) {
    return { mine: game.home, theirs: game.away, home: true };
  }
  if (game.away.name === team.name) {
    return { mine: game.away, theirs: game.home, home: false };
  }
  return null;
}

/** "vs" at home, "at" on the road; people have no home, so always "vs". */
export function venueWord(sport: Sport, home: boolean): "vs" | "at" {
  return home || isIndividualSport(sport) ? "vs" : "at";
}

// ── Form ────────────────────────────────────────────────────────────────

/** "last 8 games", "last match": the stretch a form tile covers. */
export function lastGames(count: number, words: SportWords): string {
  return count === 1 ? `last ${words.game}` : `last ${count} ${words.games}`;
}

export interface FormSummary {
  /** The last few decided results, newest first: at most `window` of them. */
  form: Outcome[];
  wins: number;
  losses: number;
  draws: number;
  /** The run the team is on, from the newest decided result back. */
  streak: { outcome: Outcome; count: number } | null;
  /**
   * What the team scored and gave up over the games in `form`. Null for a
   * sport whose score is not a tally, or when any of those scores is missing.
   */
  scored: number | null;
  allowed: number | null;
}

/**
 * Form over the newest `window` decided games, and the current streak.
 * `games` is newest first, as the API sends it. A game with no result for
 * the team (a missing score, a no-contest) is skipped: it neither extends
 * nor breaks the streak, and does not use up a place in the window.
 */
export function summarize(
  games: readonly Game[],
  team: Pick<Team, "id" | "name">,
  sport: Sport | undefined,
  window: number = FORM_GAMES,
): FormSummary {
  const summary: FormSummary = {
    form: [],
    wins: 0,
    losses: 0,
    draws: 0,
    streak: null,
    scored: null,
    allowed: null,
  };
  const tallies = sportWords(sport).tally !== null;
  let scoredSum = 0;
  let allowedSum = 0;
  let tallied = tallies;
  let streakOpen = true;

  for (const game of games) {
    const outcome = resultFor(game, team.id);
    if (outcome === null) continue;

    if (streakOpen) {
      if (summary.streak === null) summary.streak = { outcome, count: 1 };
      else if (summary.streak.outcome === outcome) summary.streak.count += 1;
      else streakOpen = false;
    }

    if (summary.form.length < window) {
      summary.form.push(outcome);
      if (outcome === "W") summary.wins += 1;
      else if (outcome === "L") summary.losses += 1;
      else summary.draws += 1;
      const view = perspective(game, team);
      if (view === null || view.mine.score === null || view.theirs.score === null) {
        tallied = false;
      } else {
        scoredSum += view.mine.score;
        allowedSum += view.theirs.score;
      }
    } else if (!streakOpen) {
      break;
    }
  }

  if (tallied && summary.form.length > 0) {
    summary.scored = scoredSum;
    summary.allowed = allowedSum;
  }
  return summary;
}

/**
 * The record over the form window, written the way the sport writes one:
 * W–D–L for soccer, W–L elsewhere, with a third figure only where a tie
 * actually happened (the order `recordText` uses for the season record).
 */
export function formRecord(summary: FormSummary, sport: Sport | undefined): string {
  if (sport === "soccer") {
    return `${summary.wins}–${summary.draws}–${summary.losses}`;
  }
  if (summary.draws > 0) {
    return `${summary.wins}–${summary.losses}–${summary.draws}`;
  }
  return `${summary.wins}–${summary.losses}`;
}

/** "L1", "W12". */
export function streakFigure(streak: { outcome: Outcome; count: number }): string {
  return `${streak.outcome}${streak.count}`;
}

/** "Lost last game", "Won last 3 games", "Drew last 2 matches". */
export function streakWords(
  streak: { outcome: Outcome; count: number },
  words: SportWords,
): string {
  const verb =
    streak.outcome === "W" ? "Won" : streak.outcome === "L" ? "Lost" : "Drew";
  return `${verb} ${lastGames(streak.count, words)}`;
}

// ── Standings ───────────────────────────────────────────────────────────

const time = (game: Game): number => new Date(game.start_time).getTime();

/**
 * Whether a season's list holds the team's newest stored result in its own
 * league: the proof that the list is the season being played, and so the
 * one the live standings are for. Without a stored result there is no
 * proof either way.
 */
export function holdsNewestResult(
  season: readonly Game[] | undefined,
  stored: readonly Game[] | undefined,
  leagueId: string,
): boolean {
  const newest = (stored ?? []).find((game) => game.league_id === leagueId);
  if (newest === undefined || season === undefined) return false;
  return season.some((game) => game.id === newest.id);
}

/**
 * The year the provider files the season being played under, read from the
 * label of the live table: a soccer season by the year it starts in
 * ("2026-27" is 2026), a basketball or hockey season by the year it ends
 * in, a one-year label by that year.
 *
 * One key, and no second guess. The other year of a two-year label is never
 * the season in play: for soccer it is next season, for the rest it is last
 * season, and the backend answers "no such season" both for a season with
 * no finished game yet and for a provider failure. A list fetched for the
 * other year would put last season's games under this season's name the
 * first time the right key came back empty.
 *
 * With no label to read (the live table did not load) the calendar year is
 * the guess, except where it can be that same wrong answer: a season keyed
 * by its ending year is filed under next year from October to December, so
 * this year's list is last season's. There is no key then, and the stored
 * results stand alone.
 */
export function seasonKey(
  label: string | undefined,
  sport: Sport | undefined,
  thisYear: number,
): number | undefined {
  const endYearKeyed = sport === "basketball" || sport === "hockey";
  if (endYearKeyed && seasonToken(label) === null) return undefined;
  return seasonKeys(label, sport, thisYear)[0];
}

/**
 * Whether the season picked is the one being played: always for "Current
 * season" (no `year`), and for a year when it is the live table's own key.
 * By the key, not by which games a list holds: the list of a season is
 * fetched once and is soon a game behind the stored results, and a season
 * that stopped counting as the live one the moment a game ended would lose
 * its standings and its newest result just then.
 */
export function isSeasonInPlay(
  year: number | undefined,
  liveKey: number | undefined,
): boolean {
  return year === undefined || year === liveKey;
}

/**
 * The season named beside the team in the page head: the picked year, or
 * for "Current season" the live table's own ("2026-27"). The table's label
 * is only printed when the list of that season is what is on screen
 * (`listed`): the provider moves a table on to next season before the
 * calendar does, with this season's records still in it, and without the
 * list nothing says the label and the games agree. A league with no lists
 * to ask for (`archives` false) has only the label, which is kept.
 */
export function deckSeason(
  year: number | undefined,
  label: string | undefined,
  archives: boolean,
  listed: boolean,
): string | null {
  if (year !== undefined) return String(year);
  return !archives || listed ? seasonToken(label) : null;
}

/**
 * The season being played: the provider's list for the live table's season
 * key, when it has games in it. Undefined when there is none to show (the
 * request failed, or the season has no finished game yet): the stored
 * results then stand alone, and nothing on the page is called a season.
 */
export function currentSeason(
  list: readonly Game[] | undefined,
): readonly Game[] | undefined {
  return list !== undefined && list.length > 0 ? list : undefined;
}

/**
 * The longest two games of one season are taken to be apart. A regular
 * season runs into its postseason within days and no mid-season break is
 * this long, while an off-season is longer (some soccer ones are under two
 * months, which is why this is not more generous).
 */
export const SEASON_GAP_DAYS = 21;
const SEASON_GAP_MS = SEASON_GAP_DAYS * 24 * 60 * 60 * 1000;

/**
 * The season so far: the provider's list of the season's finished games,
 * with the stored results laid over it, newest first.
 *
 * Neither is enough alone. The stored results are only what finished while
 * the app was running, so a week with the app off leaves a hole that
 * nothing in the payload shows, and a streak read across it is wrong. The
 * season's list is complete but can be hours behind. Together they are
 * complete and current. A stored copy replaces the listed one (it carries
 * both sides' follows and can open a box score).
 *
 * The list speaks only for the team's own league (`leagueId`), so only that
 * league's stored games are measured against it. One from before the
 * list's first game is from an earlier season and is left out, unless it
 * leads straight up to that first game: for some sports the provider lists
 * only the postseason once one has begun, and the regular season before it
 * is the same season. So the stored games are followed back from the list's
 * first for as long as each is within `SEASON_GAP_DAYS` of the one after it.
 * Stored games from other competitions (a cup tie, a national team's
 * friendlies and tournaments) are all kept: nothing here says which season
 * of theirs is which.
 */
export function mergeSeason(
  season: readonly Game[],
  stored: readonly Game[],
  leagueId: string,
): Game[] {
  if (season.length === 0) return [...stored];
  const first = Math.min(...season.map(time));
  const byId = new Map<string, Game>();
  for (const game of season) byId.set(game.id, game);

  const earlier: Game[] = [];
  for (const game of stored) {
    if (
      game.league_id !== leagueId ||
      byId.has(game.id) ||
      time(game) >= first
    ) {
      byId.set(game.id, game);
    } else {
      earlier.push(game);
    }
  }
  // Back from the season's first listed game, until the trail goes cold.
  earlier.sort((a, b) => time(b) - time(a));
  let reached = first;
  for (const game of earlier) {
    if (reached - time(game) > SEASON_GAP_MS) break;
    byId.set(game.id, game);
    reached = time(game);
  }

  return [...byId.values()].sort(
    (a, b) => time(b) - time(a) || a.id.localeCompare(b.id),
  );
}

/** The team's row: by id when the table tags it, else by name. */
export function teamRow(
  standings: Standings | undefined,
  team: Pick<Team, "id" | "name">,
): StandingRow | undefined {
  if (standings === undefined) return undefined;
  return (
    standings.rows.find((row) => row.team_id === team.id) ??
    standings.rows.find((row) => row.team_name === team.name)
  );
}

/** Whether the sport has a won-lost record to show (a ranking does not). */
export function hasSeasonRecord(sport: Sport): boolean {
  return standingColumns(sport) !== null;
}

/** Games a standings row accounts for. */
export function gamesPlayed(row: StandingRow): number {
  return row.wins + row.losses + (row.draws ?? 0) + (row.ot_losses ?? 0);
}

/**
 * The team's row in the live table, when that table is about the games on
 * screen. `seasonIsLive` is false for a past season picked by year: its
 * table would have to be asked for by year, which stores the answer for
 * good, and nothing here can tell for every sport whether a year is the
 * season still being played. So a past season gets no standings rather
 * than a frozen or mislabelled table. A row with no games played, while
 * there are results on screen, is the table having moved on to a season
 * the team has not started: not these games' record either.
 */
export function seasonRow(
  standings: Standings | undefined,
  team: Pick<Team, "id" | "name">,
  seasonIsLive: boolean,
  hasResults: boolean,
): StandingRow | undefined {
  if (!seasonIsLive || standings === undefined) return undefined;
  const row = teamRow(standings, team);
  if (
    row !== undefined &&
    hasResults &&
    hasSeasonRecord(standings.sport) &&
    gamesPlayed(row) === 0
  ) {
    return undefined;
  }
  return row;
}

const plural = (count: number, one: string, many: string): string =>
  `${count} ${count === 1 ? one : many}`;

/** "45 wins, 63 losses", with the draws or overtime losses a sport counts. */
export function recordWords(row: StandingRow, sport: Sport): string {
  const wins = plural(row.wins, "win", "wins");
  const losses = plural(row.losses, "loss", "losses");
  if (sport === "soccer") {
    return `${wins}, ${plural(row.draws ?? 0, "draw", "draws")}, ${losses}`;
  }
  if (sport === "hockey" && row.ot_losses !== null) {
    return `${wins}, ${losses}, ${plural(row.ot_losses, "overtime loss", "overtime losses")}`;
  }
  if (sport === "football" && row.draws !== null && row.draws > 0) {
    return `${wins}, ${losses}, ${plural(row.draws, "tie", "ties")}`;
  }
  return `${wins}, ${losses}`;
}

export interface StandingFigure {
  /** What the tile is called: only a division is known to be one. */
  label: "Division" | "Standing" | "Ranking";
  rank: number;
  /** The table the rank is in: "AL West", "Premier League". */
  table: string;
  /** The figure a sport ranks by, beside the place; null when it has none. */
  second: { value: string; label: string } | null;
}

function decimal(value: number): string {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

/**
 * Where the team stands, and the one figure its sport ranks by: games back
 * in baseball and basketball, points in soccer, hockey and volleyball, win
 * percentage in football, ranking points in tennis. A leader has no games
 * back to show. Null for a sport with no table (MMA, golf, racing).
 */
export function standingFigure(
  row: StandingRow,
  standings: Standings,
): StandingFigure | null {
  const sport = standings.sport;
  if (!hasSeasonRecord(sport) && sport !== "tennis") return null;
  let second: StandingFigure["second"] = null;
  switch (sport) {
    case "baseball":
    case "basketball":
      if (row.games_back !== null && row.games_back > 0) {
        second = {
          value: decimal(row.games_back),
          label: row.games_back === 1 ? "game back" : "games back",
        };
      }
      break;
    case "football":
      if (row.win_pct !== null) {
        const fixed = row.win_pct.toFixed(3);
        second = {
          value: fixed.startsWith("0") ? fixed.slice(1) : fixed,
          label: "win pct",
        };
      }
      break;
    default:
      if (row.points !== null) {
        second = {
          value: decimal(row.points),
          label: row.points === 1 ? "point" : "points",
        };
      }
  }
  return {
    label:
      sport === "tennis" ? "Ranking" : row.subgroup ? "Division" : "Standing",
    rank: row.rank,
    table: tableName(row, standings),
    second,
  };
}

// ── The list ────────────────────────────────────────────────────────────

export interface MonthGroup {
  /** "2026-07": stable across locales. */
  key: string;
  /** "July 2026": in English like every date here, in the viewer's time zone. */
  label: string;
  games: Game[];
}

const MONTH_YEAR = new Intl.DateTimeFormat("en-US", {
  month: "long",
  year: "numeric",
});

/** Consecutive games sharing a local month, in the order given (newest first). */
export function monthGroups(games: readonly Game[]): MonthGroup[] {
  const groups: MonthGroup[] = [];
  for (const game of games) {
    const date = new Date(game.start_time);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    const last = groups[groups.length - 1];
    if (last !== undefined && last.key === key) {
      last.games.push(game);
    } else {
      groups.push({
        key,
        label: MONTH_YEAR.format(date),
        games: [game],
      });
    }
  }
  return groups;
}

const MONTH_SHORT = new Intl.DateTimeFormat("en-US", { month: "short" });
const MONTH_DAY = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
});

/**
 * The accessible name of a row's "Box score" button. Every row has one, so
 * each says which game it opens and how it went: "Box score, July 29 vs
 * Boston Red Sox, lost 2–4".
 */
export function detailLabel(game: Game, team: Pick<Team, "id" | "name">): string {
  const day = MONTH_DAY.format(new Date(game.start_time));
  const view = perspective(game, team);
  if (view === null) {
    return `Box score, ${day}, ${game.away.name} at ${game.home.name}`;
  }
  const lead = `Box score, ${day} ${venueWord(game.sport, view.home)} ${view.theirs.name}`;
  const outcome = resultFor(game, team.id);
  if (outcome === null) return lead;
  const verb = outcome === "W" ? "won" : outcome === "L" ? "lost" : "drew";
  const tallies = sportWords(game.sport).tally !== null;
  if (!tallies || view.mine.score === null || view.theirs.score === null) {
    return `${lead}, ${verb}`;
  }
  return `${lead}, ${verb} ${view.mine.score}–${view.theirs.score}`;
}

/**
 * The followed team an opponent is, if it is one. Stored results tag both
 * sides with their team id; an archived season tags only the team asked
 * for, so there the opponent is matched by name within the same league.
 */
export function followedOpponent(
  side: GameSide,
  teams: readonly Team[],
  self: Pick<Team, "id" | "league_id">,
): Team | undefined {
  if (side.team_id !== null) {
    return teams.find((team) => team.id === side.team_id && team.id !== self.id);
  }
  return teams.find(
    (team) =>
      team.id !== self.id &&
      team.league_id === self.league_id &&
      team.name === side.name,
  );
}

/**
 * Colours the payloads on screen do carry, by team name. A game reached
 * through a team's own schedule, or a season's list, has no colour on
 * either side, while the same opponent in a stored row may have one: this
 * lets every row for a team draw the same block. First colour seen wins.
 */
export function colorsByName(games: readonly Game[]): Record<string, string> {
  const colors: Record<string, string> = {};
  for (const game of games) {
    for (const side of [game.home, game.away]) {
      if (side.color && colors[side.name] === undefined) {
        colors[side.name] = side.color;
      }
    }
  }
  return colors;
}

// ── Next up ─────────────────────────────────────────────────────────────

/**
 * The team's next few games: the one in progress, then those still to
 * start, in start order. `fresh` is the Today payload, which is polled
 * while a game is live; its copy of a game replaces the schedule's, so a
 * cell turns live (and a finished game leaves) without a reload. Postponed
 * and canceled games are not coming up, and neither is a game left marked
 * "scheduled" on a day that has passed.
 */
export function nextUp(
  schedule: readonly Game[],
  fresh: readonly Game[],
  todayKey: string,
  zone: string | undefined,
  limit: number = NEXT_UP_GAMES,
): Game[] {
  const latest = new Map(fresh.map((game) => [game.id, game]));
  return schedule
    .map((game) => latest.get(game.id) ?? game)
    .filter(
      (game) =>
        game.phase === "in_progress" ||
        (game.phase === "scheduled" &&
          dateKeyInZone(game.start_time, zone) >= todayKey),
    )
    .sort((a, b) => a.start_time.localeCompare(b.start_time))
    .slice(0, limit);
}

/**
 * Whether the schedule holds a game in progress that the Today payload does
 * not carry. Today holds the games that start on its day, so a game still
 * going after midnight has left it, and with it the only copy that is
 * polled: nothing would ever say that the game ended. While this is true
 * the schedule itself has to be read again on a beat.
 */
export function hasUnwatchedLive(
  schedule: readonly Game[],
  fresh: readonly Game[],
): boolean {
  const carried = new Set(fresh.map((game) => game.id));
  return schedule.some(
    (game) => game.phase === "in_progress" && !carried.has(game.id),
  );
}

/**
 * The team's games that have finished and are not among the stored results
 * on screen (`listed`): each has just left "Next up", and the list it now
 * belongs in has to be fetched again. Read from both places a finished game
 * can turn up: the Today payload (every followed team's games, so only the
 * team's own count) and the team's own schedule, which is the only one that
 * has a game that began before midnight.
 */
export function unlistedFinals(
  schedule: readonly Game[],
  fresh: readonly Game[],
  teamId: string,
  listed: ReadonlySet<string>,
): string[] {
  const ids = new Set<string>();
  const consider = (game: Game) => {
    if (game.phase === "final" && !listed.has(game.id)) ids.add(game.id);
  };
  for (const game of fresh) {
    if (game.home.team_id === teamId || game.away.team_id === teamId) {
      consider(game);
    }
  }
  for (const game of schedule) consider(game);
  return [...ids];
}

/** "Next four games, all at home"; "Next match" for a single one. */
export function nextUpNote(
  games: readonly Game[],
  team: Pick<Team, "id" | "name">,
  sport: Sport | undefined,
): string {
  const words = sportWords(sport);
  const count = games.length;
  const lead =
    count === 1
      ? `Next ${words.game}`
      : `Next ${count === 0 ? "no" : countWord(count)} ${words.games}`;
  if (count === 0 || sport === undefined || isIndividualSport(sport)) return lead;
  const sides = games.map((game) => perspective(game, team));
  if (sides.some((side) => side === null)) return lead;
  const home = sides.filter((side) => side?.home).length;
  const away = count - home;
  if (away === 0) return count === 1 ? `${lead}, at home` : `${lead}, all at home`;
  if (home === 0) {
    return count === 1 ? `${lead}, on the road` : `${lead}, all on the road`;
  }
  return `${lead}, ${countWord(home)} at home and ${countWord(away)} on the road`;
}

/** Evening starts at five, as on Today: a game after it is "tonight". */
const EVENING_HOUR = 17;

export interface NextUpDay {
  /** The game is on the server's today: the day Today is showing. */
  isToday: boolean;
  /** "Tonight", "Today", or "Fri · Jul". */
  label: string;
  /** Day of the month, where the viewer is. */
  day: number;
}

/**
 * What goes over a "Next up" cell. Which day counts as today is the
 * server's, as on Today. The date the cell shows is the viewer's: a start
 * time is a moment, and the cell's clock time, its spoken text and the
 * results row the game later becomes are all in the viewer's zone, so a
 * late game on the West coast is "Sat 17" for a viewer there even when the
 * server's calendar already says Sunday.
 */
export function nextUpDay(
  game: Game,
  todayKey: string,
  zone: string | undefined,
): NextUpDay {
  const start = new Date(game.start_time);
  const isToday = dateKeyInZone(game.start_time, zone) === todayKey;
  const label = isToday
    ? start.getHours() >= EVENING_HOUR
      ? "Tonight"
      : "Today"
    : `${weekdayShort(start)} · ${MONTH_SHORT.format(start)}`;
  return { isToday, label, day: start.getDate() };
}

// ── Fitting a figure to its tile ────────────────────────────────────────

/**
 * Digit widths in Barlow Condensed Bold, in ems, as the tiles set them:
 * proportional, like the mockup's, so a 1 is far narrower than a 4.
 * Measured in the browser and rounded up.
 */
const DIGIT_EMS: Record<string, number> = {
  "0": 0.46,
  "1": 0.29,
  "2": 0.44,
  "3": 0.44,
  "4": 0.49,
  "5": 0.44,
  "6": 0.44,
  "7": 0.41,
  "8": 0.44,
  "9": 0.44,
};

/**
 * The width of a figure set in Barlow Condensed Bold, in ems (measured, and
 * rounded up: the digits as above, an en dash 0.38em, a full stop 0.23em, a
 * capital between 0.43em and 0.69em).
 */
export function figureEms(text: string): number {
  let ems = 0;
  for (const char of text) {
    const digit = DIGIT_EMS[char];
    if (digit !== undefined) ems += digit;
    else if (char === "–" || char === "-") ems += 0.38;
    else if (char === "." || char === "," || char === " ") ems += 0.23;
    else if (char === "W" || char === "M") ems += 0.69;
    else ems += 0.5;
  }
  return ems;
}

/**
 * The font size for a tile's figure: the design's 80px, or less when that
 * would not fit the tile. A three-part record ("18–9–11") or two
 * three-figure totals are wider than the tile at full size, and a figure
 * that overflows its tile is worse than a smaller one. In container units,
 * so it follows the tile at any width; `ems` is the figure's width at 1em.
 */
export function figureSize(ems: number): string {
  if (ems <= 0) return "5rem";
  // 96%: a little air, so rounding never puts the last digit on the edge.
  return `min(5rem, ${(96 / ems).toFixed(1)}cqi)`;
}
