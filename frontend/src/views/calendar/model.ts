/**
 * The Calendar view's logic, kept pure (no React) so it can be unit-tested:
 * which days are on screen and how the buttons move between them, which
 * games fall on which day, what the counters and the legend say, a result
 * from the followed side's point of view, where a tournament's bar sits,
 * and the short sentences beside the section tabs.
 *
 * Everything is derived from payloads the app already receives. Where the
 * mockup shows something the API does not carry there is no function for
 * it: a team's city on its own ("Boston through Thursday"), a short team
 * name ("Diamondbacks"), a second team colour, the long form of a period
 * label for sports other than baseball.
 *
 * Days. A game is a moment, and it is placed on the VIEWER's local day, the
 * same zone its start time is written in. A tournament's days are fixed by
 * the server (the `.ics` feed already names them in its zone), so those are
 * read in the server's zone; see `eventSpans.ts`.
 */

import { fightResultOf, fightResultSummary } from "../../lib/fight";
import {
  boxScoreLabel,
  countOf,
  countWord,
  isIndividualSport,
  shortSide,
  showsScore,
  sportWords,
  type SportWords,
} from "../../lib/labels";
import { periodLabel } from "../../lib/periodLabel";
import {
  formatTime,
  localDateKey,
  localKeyFromDate,
  longDay,
  monthShort as monthShortOf,
  parseLocalDateKey,
  weekdayShort,
} from "../../lib/time";
import type {
  Game,
  GameSide,
  League,
  LeaderRow,
  Sport,
  SportEvent,
  Team,
  Weather,
} from "../../types";
import {
  dayKeyOffset,
  isFollowedSide,
  leadingSide,
  resultFor,
  soleFollowedSide,
  type Outcome,
  type SideKey,
} from "../today/model";
import { eventSpan } from "./eventSpans";
import { LEADERBOARD_SPORTS, appearsOnGameSide } from "./gameSideFollows";

export type { Outcome, SideKey };

// ── Ranges ──────────────────────────────────────────────────────────────

/**
 * The day a week starts on, as `Date.getDay()` numbers them (0 is Sunday).
 * Monday, as the design is drawn. This is the one place that says so: the
 * week's range, the month grid's columns and the weekday headings all
 * follow it.
 */
export const WEEK_STARTS_ON: 0 | 1 | 2 | 3 | 4 | 5 | 6 = 1;

/** What the previous and next buttons move by. */
export type CalendarUnit = "week" | "month";

/** A run of whole days, both ends included, as local "YYYY-MM-DD" keys. */
export interface DayRange {
  first: string;
  last: string;
}

/** The week that holds a day. */
export function weekRange(dayKey: string): DayRange {
  const back = (parseLocalDateKey(dayKey).getDay() - WEEK_STARTS_ON + 7) % 7;
  const first = dayKeyOffset(dayKey, -back);
  return { first, last: dayKeyOffset(first, 6) };
}

/** The calendar month that holds a day. */
export function monthRange(dayKey: string): DayRange {
  const date = parseLocalDateKey(dayKey);
  return {
    first: localKeyFromDate(new Date(date.getFullYear(), date.getMonth(), 1)),
    last: localKeyFromDate(new Date(date.getFullYear(), date.getMonth() + 1, 0)),
  };
}

export function rangeFor(unit: CalendarUnit, dayKey: string): DayRange {
  return unit === "week" ? weekRange(dayKey) : monthRange(dayKey);
}

/** Zero-padded keys compare as dates do. */
export function holdsDay(range: DayRange, dayKey: string): boolean {
  return dayKey >= range.first && dayKey <= range.last;
}

/** Whole days from one key to another; negative when `to` is earlier. */
export function daysBetween(from: string, to: string): number {
  // Local midnights are 23 or 25 hours apart across a clock change, so the
  // quotient is rounded rather than floored.
  return Math.round(
    (parseLocalDateKey(to).getTime() - parseLocalDateKey(from).getTime()) /
      86_400_000,
  );
}

/** Every day of a range, in order. */
export function dayKeys(range: DayRange): string[] {
  const keys: string[] = [];
  const count = daysBetween(range.first, range.last);
  for (let offset = 0; offset <= count; offset += 1) {
    keys.push(dayKeyOffset(range.first, offset));
  }
  return keys;
}

/** The range `steps` weeks or months away from one. */
export function shiftRange(
  unit: CalendarUnit,
  range: DayRange,
  steps: number,
): DayRange {
  if (unit === "week") return weekRange(dayKeyOffset(range.first, 7 * steps));
  const date = parseLocalDateKey(range.first);
  return monthRange(
    localKeyFromDate(new Date(date.getFullYear(), date.getMonth() + steps, 1)),
  );
}

/**
 * The range on screen. `anchor` is a day inside the range the user went to,
 * or null while the view follows today: then the range is today's own, and
 * moves by itself when the week or the month turns over.
 */
export function resolveRange(
  unit: CalendarUnit,
  anchor: string | null,
  todayKey: string,
): DayRange {
  return rangeFor(unit, anchor ?? todayKey);
}

/**
 * An anchor as it should be stored: null when its range holds today. A
 * range that holds today IS "this week", and a view left on this week is
 * one that follows today, however it got there.
 */
export function settleAnchor(
  unit: CalendarUnit,
  anchor: string | null,
  todayKey: string,
): string | null {
  if (anchor === null) return null;
  const range = rangeFor(unit, anchor);
  return holdsDay(range, todayKey) ? null : range.first;
}

/** The anchor after pressing previous (-1) or next (+1). */
export function stepAnchor(
  unit: CalendarUnit,
  anchor: string | null,
  todayKey: string,
  steps: number,
): string | null {
  const target = shiftRange(unit, resolveRange(unit, anchor, todayKey), steps);
  return settleAnchor(unit, target.first, todayKey);
}

/**
 * The anchor after changing what the buttons move by. A week becomes the
 * month most of it lies in (its fourth day decides, so the week of June 29
 * opens July); a month becomes the week its first day is in.
 */
export function anchorForUnit(
  to: CalendarUnit,
  from: CalendarUnit,
  anchor: string | null,
  todayKey: string,
): string | null {
  if (anchor === null || to === from) return settleAnchor(to, anchor, todayKey);
  const current = rangeFor(from, anchor);
  const pivot = from === "week" ? dayKeyOffset(current.first, 3) : current.first;
  return settleAnchor(to, pivot, todayKey);
}

/**
 * The week to open on leaving a month, as an anchor. The day chosen in the
 * month's grid when there is one; otherwise the week the month was entered
 * from, when it touches this month (Week, Month and back lands where it
 * left); otherwise today's week when the month holds today; otherwise the
 * month's first week.
 */
export function weekFromMonth(
  month: DayRange,
  pickedDay: string | null,
  cameFrom: string | null,
  todayKey: string,
): string | null {
  const left = cameFrom === null ? null : weekRange(cameFrom);
  const day =
    pickedDay !== null && holdsDay(month, pickedDay)
      ? pickedDay
      : left !== null && left.first <= month.last && left.last >= month.first
        ? left.first
        : holdsDay(month, todayKey)
          ? todayKey
          : month.first;
  return settleAnchor("week", day, todayKey);
}

/**
 * The days to ask the API for. The backend reads `start` and `end` as days
 * in ITS zone and these keys are the browser's, so an exact window can lose
 * the games on its edge days when the two differ: always one extra day on
 * each side. Whatever that brings in from outside the range is dropped
 * again by `gamesInRange`.
 */
export function fetchWindow(range: DayRange): { start: string; end: string } {
  return {
    start: dayKeyOffset(range.first, -1),
    end: dayKeyOffset(range.last, 1),
  };
}

/** A month as whole weeks: rows of seven day keys covering it. */
export function monthWeeks(month: DayRange): string[][] {
  const grid: DayRange = {
    first: weekRange(month.first).first,
    last: weekRange(month.last).last,
  };
  const keys = dayKeys(grid);
  const weeks: string[][] = [];
  for (let index = 0; index < keys.length; index += 7) {
    weeks.push(keys.slice(index, index + 7));
  }
  return weeks;
}

// ── Dates in words ──────────────────────────────────────────────────────

const SHORT_DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const MONTH_LONG = new Intl.DateTimeFormat("en-US", { month: "long" });
const MONTH_DAY_LONG = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric" });
const WEEKDAY_LONG = new Intl.DateTimeFormat("en-US", { weekday: "long" });

/** "Jul 27" for a day key. */
export function shortDay(dayKey: string): string {
  return SHORT_DAY.format(parseLocalDateKey(dayKey));
}

/** "Jul". */
export function monthShort(dayKey: string): string {
  return monthShortOf(parseLocalDateKey(dayKey));
}

/** "Thursday". */
export function weekdayLong(dayKey: string): string {
  return WEEKDAY_LONG.format(parseLocalDateKey(dayKey));
}

/** "Thu". */
export function weekdayOf(dayKey: string): string {
  return weekdayShort(parseLocalDateKey(dayKey));
}

/** "Thursday, July 30", with ", today" when it is. */
export function daySpoken(dayKey: string, todayKey: string): string {
  const day = longDay(parseLocalDateKey(dayKey));
  return dayKey === todayKey ? `${day}, today` : day;
}

const yearOf = (dayKey: string): number => parseLocalDateKey(dayKey).getFullYear();
const monthOf = (dayKey: string): number => parseLocalDateKey(dayKey).getMonth();
const dayOfMonth = (dayKey: string): number => parseLocalDateKey(dayKey).getDate();

export { dayOfMonth };

/** "Jul 27 – Aug 2": a run of days with no year. */
export function daySpan(range: DayRange): string {
  if (range.first === range.last) return shortDay(range.first);
  const sameMonth =
    monthOf(range.first) === monthOf(range.last) &&
    yearOf(range.first) === yearOf(range.last);
  return sameMonth
    ? `${shortDay(range.first)} – ${dayOfMonth(range.last)}`
    : `${shortDay(range.first)} – ${shortDay(range.last)}`;
}

/**
 * The label between the previous and next buttons: "Jul 27 – Aug 2, 2026",
 * "Aug 3 – 9, 2026", "Dec 28, 2026 – Jan 3, 2027"; a month is "July 2026".
 */
export function rangeLabel(unit: CalendarUnit, range: DayRange): string {
  if (unit === "month") {
    return `${MONTH_LONG.format(parseLocalDateKey(range.first))} ${yearOf(range.first)}`;
  }
  if (yearOf(range.first) !== yearOf(range.last)) {
    return `${shortDay(range.first)}, ${yearOf(range.first)} – ${shortDay(range.last)}, ${yearOf(range.last)}`;
  }
  return `${daySpan(range)}, ${yearOf(range.last)}`;
}

/** How far a range is from the one that holds today, in weeks or months. */
function stepsFromToday(
  unit: CalendarUnit,
  range: DayRange,
  todayKey: string,
): number {
  const current = rangeFor(unit, todayKey);
  if (unit === "week") return daysBetween(current.first, range.first) / 7;
  return (
    (yearOf(range.first) - yearOf(current.first)) * 12 +
    (monthOf(range.first) - monthOf(current.first))
  );
}

/**
 * The section tab over the range: "This week", "Next week", "Last week",
 * otherwise "Week of Jul 27"; a month is "This month" or its name (with
 * the year when it is not this one).
 */
export function rangeTitle(
  unit: CalendarUnit,
  range: DayRange,
  todayKey: string,
): string {
  const steps = stepsFromToday(unit, range, todayKey);
  if (steps === 0) return `This ${unit}`;
  if (steps === 1) return `Next ${unit}`;
  if (steps === -1) return `Last ${unit}`;
  if (unit === "week") return `Week of ${shortDay(range.first)}`;
  const month = MONTH_LONG.format(parseLocalDateKey(range.first));
  return yearOf(range.first) === yearOf(todayKey)
    ? month
    : `${month} ${yearOf(range.first)}`;
}

/**
 * The range inside a sentence: "this week", "next month", and "that week"
 * for anything further off, where the label beside the buttons says which.
 */
export function rangePhrase(
  unit: CalendarUnit,
  range: DayRange,
  todayKey: string,
): string {
  const steps = stepsFromToday(unit, range, todayKey);
  if (steps === 0) return `this ${unit}`;
  if (steps === 1) return `next ${unit}`;
  if (steps === -1) return `last ${unit}`;
  return `that ${unit}`;
}

/** The section's accessible name: "Week of July 27 to August 2", "July 2026". */
export function rangeSpoken(unit: CalendarUnit, range: DayRange): string {
  if (unit === "month") return rangeLabel(unit, range);
  const first = MONTH_DAY_LONG.format(parseLocalDateKey(range.first));
  const last = MONTH_DAY_LONG.format(parseLocalDateKey(range.last));
  return `Week of ${first} to ${last}`;
}

// ── Games on days ───────────────────────────────────────────────────────

/** The viewer's local day a game starts on. A late game stays on that day. */
export function gameDay(game: Game): string {
  return localDateKey(game.start_time);
}

function byStart(a: Game, b: Game): number {
  return a.start_time.localeCompare(b.start_time) || a.id.localeCompare(b.id);
}

/**
 * The schedule with the Today payload laid over it. Today is polled while a
 * game is live and the schedule is not, so its copy of a game is the newer
 * one: that is how a card turns live, changes its score and goes final
 * without the schedule being asked again.
 */
export function overlayLatest(
  schedule: readonly Game[],
  fresh: readonly Game[],
): Game[] {
  if (fresh.length === 0) return [...schedule];
  const latest = new Map(fresh.map((game) => [game.id, game]));
  return schedule.map((game) => latest.get(game.id) ?? game);
}

/** The same for tournaments: Today's copy of a leaderboard is the live one. */
export function overlayLatestEvents(
  events: readonly SportEvent[],
  fresh: readonly SportEvent[],
): SportEvent[] {
  if (fresh.length === 0) return [...events];
  const latest = new Map(fresh.map((event) => [event.id, event]));
  return events.map((event) => latest.get(event.id) ?? event);
}

/** Whether a team is on either side of a game. */
export function playsIn(game: Game, teamId: string): boolean {
  return game.home.team_id === teamId || game.away.team_id === teamId;
}

/**
 * The games that belong on screen: those that start on a day of the range
 * (the fetch brings a day more on each side), for one team when the filter
 * names it, in start order.
 */
export function gamesInRange(
  games: readonly Game[],
  range: DayRange,
  teamFilter: string | null = null,
): Game[] {
  return games
    .filter(
      (game) =>
        holdsDay(range, gameDay(game)) &&
        (teamFilter === null || playsIn(game, teamFilter)),
    )
    .sort(byStart);
}

/** Games by the day they start on. Days with none are absent. */
export function gamesByDay(games: readonly Game[]): Map<string, Game[]> {
  const days = new Map<string, Game[]>();
  for (const game of [...games].sort(byStart)) {
    const key = gameDay(game);
    const day = days.get(key);
    if (day === undefined) days.set(key, [game]);
    else day.push(game);
  }
  return days;
}

export interface Tally {
  final: number;
  live: number;
  upcoming: number;
  /** In none of the three: they are counted apart, and only when there are any. */
  postponed: number;
  canceled: number;
}

/** The counters beside the range label. Pass games already cut to the range. */
export function tally(games: readonly Game[]): Tally {
  const counts: Tally = { final: 0, live: 0, upcoming: 0, postponed: 0, canceled: 0 };
  for (const game of games) {
    if (game.phase === "final") counts.final += 1;
    else if (game.phase === "in_progress") counts.live += 1;
    else if (game.phase === "scheduled") counts.upcoming += 1;
    else if (game.phase === "postponed") counts.postponed += 1;
    else counts.canceled += 1;
  }
  return counts;
}

/**
 * The games in none of the three counters, as a line under them: "1
 * postponed · 2 canceled". Null when there are none, which is nearly always.
 */
export function offNote(counts: Tally): string | null {
  const parts = [
    counts.postponed > 0 ? `${counts.postponed} postponed` : null,
    counts.canceled > 0 ? `${counts.canceled} canceled` : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * The word for a set of games: "match" when every one is a match, "bout"
 * when every one is a bout, and plain "game" for a mixture.
 */
export function wordsFor(games: readonly Game[]): SportWords {
  const first = games[0];
  if (first === undefined) return sportWords(undefined);
  const words = sportWords(first.sport);
  return games.every((game) => sportWords(game.sport).game === words.game)
    ? words
    : sportWords(undefined);
}

/**
 * The word for a range's games wherever the page has to say it: the count
 * strip's name, an empty day, "No matches are stored". What the games on
 * screen are when there are any; with none, what the followed competitions
 * play when they all play the same thing (somebody who follows only soccer
 * reads "matches" in an empty week too). Golf and racing have no games and
 * do not count.
 */
export function wordsForRange(
  games: readonly Game[],
  leagues: readonly League[],
): SportWords {
  if (games.length > 0) return wordsFor(games);
  const kinds = leagues
    .filter((league) => !LEADERBOARD_SPORTS.has(league.sport))
    .map((league) => sportWords(league.sport));
  const first = kinds[0];
  return first !== undefined && kinds.every((words) => words.game === first.game)
    ? first
    : sportWords(undefined);
}

/**
 * At most `cap` games, in start order, keeping every game a followed team
 * plays in before any other: under a whole-competition follow a day can
 * hold ten games, and the followed team's must not be the one cut.
 */
export function pickGames(
  games: readonly Game[],
  cap: number,
): { shown: Game[]; more: number } {
  const ordered = [...games].sort(byStart);
  if (ordered.length <= cap) return { shown: ordered, more: 0 };
  const followed = ordered.filter((game) => game.followed_team_ids.length > 0);
  const rest = ordered.filter((game) => game.followed_team_ids.length === 0);
  const shown = [...followed, ...rest].slice(0, cap).sort(byStart);
  return { shown, more: ordered.length - shown.length };
}

/** A day holds this many full cards; a busier day switches to compact ones. */
export const FULL_CARDS_MAX = 3;

export function isBusyDay(count: number): boolean {
  return count > FULL_CARDS_MAX;
}

// ── A card ──────────────────────────────────────────────────────────────

export type CardStatus =
  | { kind: "time"; text: string; iso: string }
  | { kind: "live"; text: string; title: string }
  | { kind: "final" }
  | { kind: "off"; text: "Postponed" | "Canceled"; short: "PPD" | "Canc" };

/**
 * A live game's place in it, short enough for a card. Soccer is told by its
 * minute alone ("71'"), which is how it is said and all a card has room
 * for; a break has no clock.
 */
export function liveShort(game: Game): string {
  if (game.is_intermission) return periodLabel(game, "short");
  if (game.sport === "soccer" && game.clock) return game.clock;
  return [periodLabel(game, "short"), game.clock].filter(Boolean).join(" ");
}

export function cardStatus(game: Game): CardStatus {
  switch (game.phase) {
    case "in_progress":
      return {
        kind: "live",
        text: liveShort(game),
        title: [periodLabel(game, "long"), game.is_intermission ? null : game.clock]
          .filter(Boolean)
          .join(" "),
      };
    case "final":
      return { kind: "final" };
    case "postponed":
      return { kind: "off", text: "Postponed", short: "PPD" };
    case "canceled":
      return { kind: "off", text: "Canceled", short: "Canc" };
    default:
      return { kind: "time", text: formatTime(game.start_time), iso: game.start_time };
  }
}

/** The side that won a finished fight, when it had a winner. */
function fightWinner(game: Game): SideKey | null {
  if (game.sport !== "mma" || game.phase !== "final") return null;
  const method = fightResultOf(game)?.method;
  if (method === "draw" || method === "no_contest") return null;
  return leadingSide(game);
}

export type SideValue =
  | { kind: "score"; value: number; lead: boolean }
  | { kind: "tag"; text: string }
  | null;

/**
 * What stands at the end of a side's row: its score once there is one (the
 * side in front, or both when level, set forward), "Away" / "Home" for a
 * team game still to start, "Won" beside the winner of a fight (whose
 * score is a count of rounds and is never shown), and nothing otherwise:
 * people have no home, and a game that is off has neither.
 */
export function sideValue(game: Game, key: SideKey): SideValue {
  const score = game[key].score;
  if (showsScore(game) && score !== null) {
    const leader = leadingSide(game);
    return { kind: "score", value: score, lead: leader === null || leader === key };
  }
  if (fightWinner(game) === key) return { kind: "tag", text: "Won" };
  if (game.phase === "scheduled" && !isIndividualSport(game.sport)) {
    return { kind: "tag", text: key === "home" ? "Home" : "Away" };
  }
  return null;
}

/**
 * Whose game it is, for telling a result: the filtered team when it is
 * playing, otherwise the one followed side. Null when neither side is
 * followed (a whole-competition game) or both are and no filter chooses.
 */
export function pointOfView(game: Game, teamFilter: string | null): SideKey | null {
  if (teamFilter !== null) {
    if (game.home.team_id === teamFilter) return "home";
    if (game.away.team_id === teamFilter) return "away";
  }
  return soleFollowedSide(game);
}

export interface CardOutcome {
  outcome: Outcome;
  /** "Athletics lost", for the square's accessible name. */
  spoken: string;
}

const OUTCOME_VERB: Record<Outcome, string> = { W: "won", L: "lost", D: "drew" };

/** The W / L / D square of a finished game; null when there is none to show. */
export function cardOutcome(game: Game, teamFilter: string | null): CardOutcome | null {
  if (game.phase !== "final") return null;
  const key = pointOfView(game, teamFilter);
  const side = key === null ? null : game[key];
  if (side === null || side.team_id === null) return null;
  const outcome = resultFor(game, side.team_id);
  if (outcome === null) return null;
  return { outcome, spoken: `${side.name} ${OUTCOME_VERB[outcome]}` };
}

/**
 * The accessible name of a game's button. The name replaces the card for a
 * screen reader, so it carries everything the card or row says: the state,
 * the score and the result, where it is played, what it belongs to (the
 * competition or the round, as `contextLine` has it), and the forecast when
 * one is shown.
 */
export function cardLabel(
  game: Game,
  teamFilter: string | null,
  forecast: WeatherLine | null = null,
  league?: League,
): string {
  return [
    boxScoreLabel(game),
    cardOutcome(game, teamFilter)?.spoken,
    game.venue,
    contextLine(game, league),
    forecast?.spoken,
  ]
    .filter((part): part is string => typeof part === "string" && part !== "")
    .join(", ");
}

/**
 * Who "your ..." is in the key under the grid: a team, as the mockup has
 * it, unless the followed sides on screen are people (tennis, MMA).
 */
export function followedNoun(
  games: readonly Game[],
): "team" | "athlete" | "team or athlete" {
  let teams = false;
  let people = false;
  for (const game of games) {
    if (!isFollowedSide(game, game.home) && !isFollowedSide(game, game.away)) continue;
    if (isIndividualSport(game.sport)) people = true;
    else teams = true;
  }
  if (teams && people) return "team or athlete";
  return people ? "athlete" : "team";
}

/**
 * The line that says what a game belongs to: the competition, for a game
 * no followed team is in (nothing else explains why it is here), then the
 * round or the card.
 */
export function contextLine(game: Game, league: League | undefined): string | null {
  const parts = [
    game.followed_team_ids.length === 0 ? (league?.name ?? null) : null,
    game.series,
  ].filter((part): part is string => part !== null && part !== "");
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * How a finished fight ended ("Submission · Rear-Naked Choke · R2 4:21"):
 * a bout's result, where another sport has a score. A line of its own, so
 * a long card name can never push it out of sight. Null for anything else.
 */
export function fightLine(game: Game): string | null {
  const fight = fightResultOf(game);
  if (fight === null) return null;
  return fightResultSummary(fight) || null;
}

/** The competition's name, for a game no followed team is in. */
export function competitionName(game: Game, league: League | undefined): string | null {
  return game.followed_team_ids.length === 0 ? (league?.name ?? null) : null;
}

/** A side in a narrow card: a person's surname; a team is told by its block. */
export function cardSideName(game: Game, side: GameSide): string {
  return isIndividualSport(game.sport) ? shortSide(game, side) : side.name;
}

// ── A row of a list (Coming up, Agenda, a month's day) ──────────────────

export interface RowSides {
  first: GameSide;
  second: GameSide;
  firstKey: SideKey;
  secondKey: SideKey;
  /** Before the second side: "vs" or "at". */
  word: "vs" | "at";
  /** Where the first side is playing; null for people, and with no point of view. */
  place: "Home" | "Away" | null;
  /** The row is written from one side's point of view: that side leads it. */
  viewed: boolean;
}

/**
 * How a list row names its two sides. From a point of view the followed
 * team comes first and the opponent after "vs" or "at", as the mockup's
 * "Coming up" rows are written; with none it is away "at" home, the order
 * the cards use. People have no home: always "vs", and no place.
 */
export function rowSides(game: Game, teamFilter: string | null): RowSides {
  const individual = isIndividualSport(game.sport);
  const view = pointOfView(game, teamFilter);
  if (view === null) {
    return {
      first: game.away,
      second: game.home,
      firstKey: "away",
      secondKey: "home",
      word: individual ? "vs" : "at",
      place: null,
      viewed: false,
    };
  }
  const other: SideKey = view === "home" ? "away" : "home";
  return {
    first: game[view],
    second: game[other],
    firstKey: view,
    secondKey: other,
    word: view === "home" || individual ? "vs" : "at",
    place: individual ? null : view === "home" ? "Home" : "Away",
    viewed: true,
  };
}

// ── Weather ─────────────────────────────────────────────────────────────

/** Sports played outdoors: the ones a forecast is asked for. Mirrors the backend's list, without golf. */
const OUTDOOR_SPORTS: ReadonlySet<Sport> = new Set<Sport>([
  "soccer",
  "baseball",
  "football",
  "tennis",
]);

/** The backend reads at most this many ids from one forecast request. */
export const WEATHER_BATCH = 80;

/**
 * The games to ask a forecast for: outdoor ones still to start, the
 * soonest first, as many as one request carries.
 */
export function weatherGameIds(games: readonly Game[]): string[] {
  return games
    .filter((game) => game.phase === "scheduled" && OUTDOOR_SPORTS.has(game.sport))
    .sort(byStart)
    .slice(0, WEATHER_BATCH)
    .map((game) => game.id);
}

export interface WeatherLine {
  /** "High 72° · 30% precip" */
  text: string;
  /** "Forecast: high 72°F, low 58°F, 30% chance of precipitation" */
  spoken: string;
}

/**
 * A game's forecast in words. Only the figures that are FOR the game's day:
 * the day's high and low and its chance of precipitation. The payload's
 * temperature and condition are the venue's weather right now, which is not
 * the weather of a game three days off, so they are not used.
 *
 * It is called a "forecast" and not a "game-day forecast": the backend's
 * day is the UTC date of the start, which for an evening game in the
 * Americas is mostly the day after at the venue. The view cannot tell
 * which case it is in (it has no venue timezone), so it does not claim it.
 */
export function weatherLine(weather: Weather | undefined): WeatherLine | null {
  if (weather === undefined) return null;
  const unit = weather.units === "imperial" ? "°F" : "°C";
  const text: string[] = [];
  const spoken: string[] = [];
  if (weather.high !== null) {
    text.push(`High ${Math.round(weather.high)}°`);
    spoken.push(`high ${Math.round(weather.high)}${unit}`);
  }
  if (weather.low !== null) spoken.push(`low ${Math.round(weather.low)}${unit}`);
  if (weather.precip_chance !== null) {
    text.push(`${Math.round(weather.precip_chance)}% precip`);
    spoken.push(`${Math.round(weather.precip_chance)}% chance of precipitation`);
  }
  if (text.length === 0) return null;
  return { text: text.join(" · "), spoken: `Forecast: ${spoken.join(", ")}` };
}

// ── Tournaments ─────────────────────────────────────────────────────────

/** A leaderboard event and the days it runs over, both ends included. */
export interface EventDays {
  event: SportEvent;
  first: string;
  last: string;
}

/**
 * Each tournament with its days, in the server's zone (see `eventSpan`),
 * ordered by start, the longer first, then by name: the order lanes are
 * handed out in, so the same tournament keeps its lane from week to week.
 */
export function eventDays(
  events: readonly SportEvent[],
  zone: string | undefined,
): EventDays[] {
  return events
    .map((event) => {
      const span = eventSpan(event, zone);
      // `end` is the day after the last one.
      return { event, first: span.start, last: dayKeyOffset(span.end, -1) };
    })
    .sort(
      (a, b) =>
        a.first.localeCompare(b.first) ||
        b.last.localeCompare(a.last) ||
        a.event.name.localeCompare(b.event.name),
    );
}

/** The tournaments running on a day. */
export function eventsOn(placed: readonly EventDays[], dayKey: string): EventDays[] {
  return placed.filter((entry) => dayKey >= entry.first && dayKey <= entry.last);
}

/** "Day 2 of 4" for a tournament on one of its days; null for a single day. */
export function eventDayOf(entry: EventDays, dayKey: string): string | null {
  const total = daysBetween(entry.first, entry.last) + 1;
  if (total <= 1) return null;
  return `Day ${daysBetween(entry.first, dayKey) + 1} of ${total}`;
}

export interface EventBar {
  entry: EventDays;
  /** 0-based row of the band; overlapping tournaments get one each. */
  lane: number;
  /** 0-based day of the range the bar starts on, and how many days it covers. */
  column: number;
  span: number;
  /** The tournament began before the range, or runs on past it. */
  startsBefore: boolean;
  endsAfter: boolean;
}

/**
 * Where each tournament's bar sits over a run of days: cut to the range,
 * and dropped into the first lane that is free for all of its days.
 */
export function layoutBars(
  placed: readonly EventDays[],
  range: DayRange,
): { lanes: number; bars: EventBar[] } {
  const bars: EventBar[] = [];
  /** The last day taken in each lane so far. */
  const taken: string[] = [];
  for (const entry of placed) {
    if (entry.last < range.first || entry.first > range.last) continue;
    const first = entry.first < range.first ? range.first : entry.first;
    const last = entry.last > range.last ? range.last : entry.last;
    let lane = taken.findIndex((day) => day < first);
    if (lane === -1) lane = taken.length;
    taken[lane] = last;
    bars.push({
      entry,
      lane,
      column: daysBetween(range.first, first),
      span: daysBetween(first, last) + 1,
      startsBefore: entry.first < range.first,
      endsAfter: entry.last > range.last,
    });
  }
  return { lanes: taken.length, bars };
}

/** "Golf" or "Racing": what kind of event a bar is, in a word. */
export function eventKind(sport: Sport): string {
  if (sport === "golf") return "Golf";
  if (sport === "racing") return "Racing";
  return sport.charAt(0).toUpperCase() + sport.slice(1);
}

/** "Live", "Final", "Postponed", "Canceled"; null before it starts. */
export function eventState(event: SportEvent): string | null {
  switch (event.phase) {
    case "in_progress":
      return "Live";
    case "final":
      return "Final";
    case "postponed":
      return "Postponed";
    case "canceled":
      return "Canceled";
    default:
      return null;
  }
}

/** The followed athletes on a tournament's board (a non-null id is a follow). */
export function followedRows(event: SportEvent): LeaderRow[] {
  return event.leaderboard.filter((row) => row.player_id !== null);
}

/** The accessible name of a tournament's button. */
export function eventLabel(entry: EventDays): string {
  const { event } = entry;
  const state =
    event.phase === "in_progress"
      ? ["live", event.round_label].filter(Boolean).join(", ")
      : (eventState(event)?.toLowerCase() ?? null);
  return [
    `Leaderboard: ${event.name}`,
    eventKind(event.sport).toLowerCase(),
    daySpan({ first: entry.first, last: entry.last }),
    state,
  ]
    .filter((part): part is string => part !== null && part !== "")
    .join(", ");
}

/**
 * Whether a tournament's board says where anybody stands. Before the first
 * round the provider already lists the field, every score level, and the
 * backend numbers those rows in the order they came: that is an entry
 * list, not a standing, so no place or score is read from it.
 */
export function hasStandings(event: SportEvent): boolean {
  return event.phase === "in_progress" || event.phase === "final";
}

/**
 * The day a tournament in progress is being played on: today, held to the
 * tournament's own days (they are the server's, and the viewer's today can
 * fall a day outside them).
 */
function liveDay(entry: EventDays, todayKey: string): string {
  if (todayKey < entry.first) return entry.first;
  return todayKey > entry.last ? entry.last : todayKey;
}

export interface EventRowView {
  /** The row of the day a tournament in progress is being played on. */
  live: boolean;
  /**
   * "Final", "Postponed", "Canceled"; null for one still to start, and for
   * every other day of one in progress (the row for a day not yet played
   * must not say it is live in round one).
   */
  state: string | null;
  /** "Day 2 of 4"; null for a single day. */
  dayOf: string | null;
  /** The followed athletes on the board; no place or score until there are standings. */
  followed: { name: string; place: string | null; score: string | null }[];
  /** The accessible name of the row's button: everything the row says. */
  label: string;
}

/**
 * A tournament as a row of a list, on one of its days. A list shows a
 * tournament on each day it runs, so the row is about THAT day: the Live
 * chip, the round and the followed athletes' places stand only on the day
 * being played, and the other days of a tournament in progress say which
 * day of it they are.
 */
export function eventRow(
  entry: EventDays,
  dayKey: string,
  todayKey: string,
): EventRowView {
  const { event } = entry;
  const inProgress = event.phase === "in_progress";
  const live = inProgress && dayKey === liveDay(entry, todayKey);
  const state = inProgress ? null : eventState(event);
  const dayOf = eventDayOf(entry, dayKey);
  const standings = hasStandings(event);
  const followed =
    inProgress && !live
      ? []
      : followedRows(event).map((row) => ({
          name: row.name,
          place: standings ? row.position_label || String(row.position) : null,
          score: standings ? row.score.trim() || null : null,
        }));
  const label = [
    `Leaderboard: ${event.name}`,
    eventKind(event.sport).toLowerCase(),
    daySpan({ first: entry.first, last: entry.last }),
    dayOf?.toLowerCase(),
    live ? ["live", event.round_label].filter(Boolean).join(", ") : state?.toLowerCase(),
    event.venue,
    ...followed.map((row) =>
      row.place === null
        ? `${row.name} in the field`
        : [`${row.place} ${row.name}`, row.score].filter(Boolean).join(", "),
    ),
  ]
    .filter((part): part is string => typeof part === "string" && part !== "")
    .join(", ");
  return { live, state, dayOf, followed, label };
}

// ── Legend ──────────────────────────────────────────────────────────────

/** "the 3M Open", and "The Genesis Invitational" as it is: many names carry their own article. */
function withArticle(name: string): string {
  return /^the\s/i.test(name) ? name : `the ${name}`;
}

export interface LegendRow {
  key: string;
  name: string;
  abbreviation: string | null;
  color: string | null;
  /** What is followed: a team, a person (tennis, MMA, golf, racing) or a whole competition. */
  kind: "team" | "athlete" | "competition";
  /** A followed team or athlete (marked); false for a whole competition. */
  marked: boolean;
  /** "7 games this week", "No matches this week", "T14 at the 3M Open". */
  line: string;
  /** Results inside the range, oldest first. */
  results: Outcome[];
  /** Nothing on in the range: the row is set back. */
  quiet: boolean;
}

function countLine(count: number, words: SportWords, phrase: string): string {
  return count === 0
    ? `No ${words.games} ${phrase}`
    : `${countOf(count, words)} ${phrase}`;
}

/**
 * One row per thing followed: each team with its games in the range and
 * the results so far; each golfer or driver with their place on a board
 * that lists them; each whole competition with its games.
 *
 * `games` are the range's games, cut to it and NOT filtered by team, and
 * `events` the tournaments running in it, or why there are none to read:
 * a list still on its way, or one that failed, is not an empty one, and
 * "Not on a leaderboard" would be a claim nothing supports.
 */
export function legendRows(
  teams: readonly Team[],
  leagues: readonly League[],
  games: readonly Game[],
  events: readonly SportEvent[] | "loading" | "failed",
  phrase: string,
): LegendRow[] {
  const leaguesById: Record<string, League> = {};
  for (const league of leagues) leaguesById[league.id] = league;
  const rows: LegendRow[] = [];

  for (const team of teams) {
    const sport = leaguesById[team.league_id]?.sport;
    if (!appearsOnGameSide(team, leaguesById)) {
      // A golfer or a driver: on a board or not, and where. A board that
      // has no standings yet (see `hasStandings`) only says they are in.
      const places =
        typeof events === "string"
          ? []
          : events
              // One that was called off has no field to be in.
              .filter((event) => event.phase !== "canceled")
              .flatMap((event) =>
                event.leaderboard
                  .filter((row) => row.player_id === team.id)
                  .map((row) =>
                    hasStandings(event)
                      ? `${row.position_label || String(row.position)} at ${withArticle(event.name)}`
                      : `In the field at ${withArticle(event.name)}`,
                  ),
              );
      const unknown =
        events === "loading"
          ? "Loading tournaments…"
          : events === "failed"
            ? "Couldn’t load tournaments"
            : null;
      rows.push({
        key: team.id,
        name: team.name,
        abbreviation: team.abbreviation,
        color: team.color,
        kind: "athlete",
        marked: true,
        line:
          unknown ??
          (places.length > 0 ? places.join(" · ") : `Not on a leaderboard ${phrase}`),
        results: [],
        quiet: places.length === 0,
      });
      continue;
    }
    const mine = games.filter(
      (game) =>
        playsIn(game, team.id) &&
        game.phase !== "postponed" &&
        game.phase !== "canceled",
    );
    const results = mine
      .filter((game) => game.phase === "final")
      .sort(byStart)
      .map((game) => resultFor(game, team.id))
      .filter((outcome): outcome is Outcome => outcome !== null);
    rows.push({
      key: team.id,
      name: team.name,
      abbreviation: team.abbreviation,
      color: team.color,
      kind: isIndividualSport(sport ?? mine[0]?.sport ?? "baseball") ? "athlete" : "team",
      marked: true,
      line: countLine(mine.length, sportWords(sport ?? mine[0]?.sport), phrase),
      results,
      quiet: mine.length === 0,
    });
  }

  for (const league of leagues) {
    if (!league.follow_all || LEADERBOARD_SPORTS.has(league.sport)) continue;
    const count = games.filter(
      (game) =>
        game.league_id === league.id &&
        game.phase !== "postponed" &&
        game.phase !== "canceled",
    ).length;
    rows.push({
      key: `league:${league.id}`,
      name: league.name,
      abbreviation: null,
      color: null,
      kind: "competition",
      marked: false,
      line: countLine(count, sportWords(league.sport), phrase),
      results: [],
      quiet: count === 0,
    });
  }
  return rows;
}

// ── The sentences beside the tabs ───────────────────────────────────────

/**
 * The team a caption is about: the filtered one; otherwise the only
 * followed team with a game in the range; otherwise the favourite, when it
 * has one. Null when no single team stands for the range.
 */
export function captionTeam(
  teams: readonly Team[],
  games: readonly Game[],
  teamFilter: string | null,
  favoriteId: string | null,
): Team | null {
  const playing = teams.filter((team) =>
    games.some((game) => playsIn(game, team.id)),
  );
  if (teamFilter !== null) {
    return playing.find((team) => team.id === teamFilter) ?? null;
  }
  if (playing.length === 1) return playing[0];
  return playing.find((team) => team.id === favoriteId) ?? null;
}

/**
 * What one team's stretch of games looks like, in a line: "Athletics: 7
 * games, all at home · Boston Red Sox through Thursday, Detroit Tigers from
 * Friday". Built only from what the games say: how many, which are at home,
 * and who they are against (by full name; the API has no city on its own).
 *
 * `weekdays` adds the "through Thursday" half, which only reads right when
 * the games are within one week. Null when the team has no game.
 */
export function teamCaption(
  team: Pick<Team, "id" | "name">,
  games: readonly Game[],
  sport: Sport | undefined,
  weekdays: boolean,
): string | null {
  const mine = games
    .filter(
      (game) =>
        playsIn(game, team.id) &&
        game.phase !== "postponed" &&
        game.phase !== "canceled",
    )
    .sort(byStart);
  if (mine.length === 0) return null;
  const words = sportWords(sport ?? mine[0].sport);
  const individual = isIndividualSport(sport ?? mine[0].sport);
  const opponentOf = (game: Game): GameSide =>
    game.home.team_id === team.id ? game.away : game.home;
  const atHome = (game: Game): boolean => game.home.team_id === team.id;

  if (mine.length === 1) {
    const game = mine[0];
    const word = atHome(game) || individual ? "vs" : "at";
    const day = weekdays
      ? weekdayLong(gameDay(game))
      : MONTH_DAY_LONG.format(parseLocalDateKey(gameDay(game)));
    return `${team.name}: ${word} ${opponentOf(game).name} on ${day}`;
  }

  const lead = `${team.name}: ${countOf(mine.length, words)}`;
  // Runs of games against the same opponent: a series, in the sports that
  // have them.
  const runs: { name: string; first: Game; last: Game }[] = [];
  for (const game of mine) {
    const name = opponentOf(game).name;
    const run = runs[runs.length - 1];
    if (run !== undefined && run.name === name) run.last = game;
    else runs.push({ name, first: game, last: game });
  }
  const against = runs.length === 1 ? ` against ${runs[0].name}` : "";
  // Two opponents in a week: who until when, and who from when. A single
  // game is "on" its day; "through Tuesday" would promise a series.
  const stretch = (run: (typeof runs)[number], opens: boolean): string => {
    if (run.first === run.last) {
      return `${run.name} on ${weekdayLong(gameDay(run.first))}`;
    }
    return opens
      ? `${run.name} through ${weekdayLong(gameDay(run.last))}`
      : `${run.name} from ${weekdayLong(gameDay(run.first))}`;
  };
  const series =
    weekdays && runs.length === 2
      ? ` · ${stretch(runs[0], true)}, ${stretch(runs[1], false)}`
      : "";
  if (individual) return `${lead}${against}${series}`;

  const home = mine.filter(atHome).length;
  const away = mine.length - home;
  // Both as words or both as figures: "12 at home and 8 on the road".
  const figure = (count: number): string =>
    home > 10 || away > 10 ? String(count) : countWord(count);
  const where =
    away === 0
      ? ", all at home"
      : home === 0
        ? ", all on the road"
        : `, ${figure(home)} at home and ${figure(away)} on the road`;
  return `${lead}${where}${against}${series}`;
}

/**
 * A caption for a range no single team stands for: "22 matches across six
 * days", "5 matches, all on Saturday". Null when there is nothing on.
 *
 * `weekdays` names a single day by its weekday, which only reads right
 * inside one week; a month has four or five Saturdays, so there the day is
 * given by its date ("2 matches, all on November 21").
 */
export function plainCaption(
  games: readonly Game[],
  weekdays: boolean = true,
): string | null {
  const on = games.filter(
    (game) => game.phase !== "postponed" && game.phase !== "canceled",
  );
  if (on.length === 0) return null;
  const days = [...new Set(on.map(gameDay))];
  const count = countOf(on.length, wordsFor(on));
  if (days.length === 1) {
    const day = weekdays
      ? weekdayLong(days[0])
      : MONTH_DAY_LONG.format(parseLocalDateKey(days[0]));
    return on.length === 1 ? `${count}, on ${day}` : `${count}, all on ${day}`;
  }
  return `${count} across ${countWord(days.length)} days`;
}

/** The note beside the range's tab. */
export function rangeCaption(
  teams: readonly Team[],
  leagues: readonly League[],
  games: readonly Game[],
  teamFilter: string | null,
  favoriteId: string | null,
  unit: CalendarUnit,
): string | null {
  const weekdays = unit === "week";
  const team = captionTeam(teams, games, teamFilter, favoriteId);
  if (team === null) return plainCaption(games, weekdays);
  const sport = leagues.find((league) => league.id === team.league_id)?.sport;
  return teamCaption(team, games, sport, weekdays) ?? plainCaption(games, weekdays);
}

// ── Coming up ───────────────────────────────────────────────────────────

/** How many days "Coming up" looks over, and how many rows it shows. */
export const COMING_UP_DAYS = 7;
export const COMING_UP_ROWS = 5;

/**
 * The days "Coming up" covers: the week after the range on screen, or the
 * week from today when the range is in the past (what came after a week
 * long gone is not coming up).
 */
export function comingUpWindow(range: DayRange, todayKey: string): DayRange {
  const after = dayKeyOffset(range.last, 1);
  const first = after > todayKey ? after : todayKey;
  return { first, last: dayKeyOffset(first, COMING_UP_DAYS - 1) };
}

/**
 * The games still to start inside the window, for the filtered team when
 * there is a filter. `shown` is at most `cap` of them (a followed team's
 * game is never the one cut); `more` is how many were.
 */
export function comingUp(
  games: readonly Game[],
  window: DayRange,
  teamFilter: string | null,
  cap: number = COMING_UP_ROWS,
): { shown: Game[]; more: number; all: Game[]; moreFrom: string | null } {
  const all = gamesInRange(games, window, teamFilter).filter(
    (game) => game.phase === "scheduled",
  );
  const picked = pickGames(all, cap);
  const listed = new Set(picked.shown.map((game) => game.id));
  const unlisted = all.find((game) => !listed.has(game.id));
  return {
    ...picked,
    all,
    // The day of the earliest game that got no row.
    moreFrom: unlisted === undefined ? null : gameDay(unlisted),
  };
}

export interface ComingUpFoot {
  /** "6 more matches through Aug 9" */
  line: string;
  /** The button: "Show next week", "Show week of Aug 10". */
  action: string;
  /** The week the button opens: the one the earliest unlisted game is in. */
  week: DayRange;
}

/**
 * The foot of "Coming up" when its days hold more games than it has rows:
 * how many more, up to which day, and a way to see them. The button opens
 * a WEEK, and the window is not always one (after a month, or from today),
 * so it names the week it opens rather than promising "the rest".
 */
export function comingUpFoot(
  up: { more: number; all: readonly Game[]; moreFrom: string | null },
  window: DayRange,
  todayKey: string,
): ComingUpFoot | null {
  if (up.more <= 0 || up.moreFrom === null) return null;
  const words = wordsFor(up.all);
  const week = weekRange(up.moreFrom);
  const title = rangeTitle("week", week, todayKey);
  return {
    line: `${up.more} more ${up.more === 1 ? words.game : words.games} through ${shortDay(window.last)}`,
    action: `Show ${title.charAt(0).toLowerCase()}${title.slice(1)}`,
    week,
  };
}

/**
 * The tournaments that START inside the window: one that is already under
 * way is on the grid above, not coming up.
 */
export function comingUpEvents(
  placed: readonly EventDays[],
  window: DayRange,
): EventDays[] {
  return placed.filter(
    (entry) =>
      holdsDay(window, entry.first) &&
      entry.event.phase !== "final" &&
      entry.event.phase !== "canceled",
  );
}

/** "Next week" when the window is exactly that; otherwise its days. */
export function comingUpTitle(window: DayRange, todayKey: string): string {
  const next = shiftRange("week", weekRange(todayKey), 1);
  return window.first === next.first && window.last === next.last
    ? "Next week"
    : daySpan(window);
}

// ── A day of a month ────────────────────────────────────────────────────

/** How many games a month's day cell lists before "+N more". */
export const MONTH_CELL_GAMES = 4;

/** How many games a narrow month cell marks with a square each. */
export const MONTH_CELL_DOTS = 3;

/**
 * The squares of a narrow month cell: one per game up to three. A busier
 * day shows two and a "+", because three squares and the "+" are wider
 * than a phone's cell and the "+" would drop to a line of its own.
 */
export function cellDots(games: readonly Game[]): { shown: Game[]; more: number } {
  return pickGames(
    games,
    games.length > MONTH_CELL_DOTS ? MONTH_CELL_DOTS - 1 : MONTH_CELL_DOTS,
  );
}

/**
 * Beside the month's grid the chosen day's list is a narrow column of tall
 * rows, and holds about this many before it outgrows the grid and leaves
 * blank page under it.
 */
export const DAY_LIST_BESIDE_MAX = 4;

/**
 * The most rows (games and tournaments) any one day of a range holds. The
 * month uses it to put the day's list under the grid, at full width, when
 * some day is too busy for the column beside it. It is the busiest day of
 * the MONTH and not the chosen one, so the grid does not change width
 * under the pointer as days are chosen.
 */
export function busiestDayRows(
  range: DayRange,
  games: readonly Game[],
  events: readonly EventDays[],
): number {
  const byDay = gamesByDay(games);
  let most = 0;
  for (const key of dayKeys(range)) {
    const rows = (byDay.get(key)?.length ?? 0) + eventsOn(events, key).length;
    if (rows > most) most = rows;
  }
  return most;
}

/**
 * What stands beside a game's two blocks in a month cell: its score once
 * there is one (away first, as the blocks are), otherwise its start time or
 * that it is off.
 */
export function pairFigure(game: Game): { text: string; live: boolean } {
  const live = game.phase === "in_progress";
  if (showsScore(game)) {
    return { text: `${game.away.score}–${game.home.score}`, live };
  }
  switch (game.phase) {
    case "in_progress":
      return { text: "Live", live: true };
    case "final":
      return { text: "Final", live: false };
    case "postponed":
      return { text: "PPD", live: false };
    case "canceled":
      return { text: "Canc", live: false };
    default:
      return { text: formatTime(game.start_time), live: false };
  }
}

/**
 * A day's accessible name in the month grid: "Saturday, October 10, 5
 * matches: 2 final, 1 live, 2 upcoming, and the 3M Open".
 */
export function dayCellLabel(
  dayKey: string,
  todayKey: string,
  games: readonly Game[],
  events: readonly EventDays[],
): string {
  const parts = [daySpoken(dayKey, todayKey)];
  if (games.length === 0 && events.length === 0) {
    parts.push("nothing on");
    return parts.join(", ");
  }
  if (games.length > 0) {
    const counts = tally(games);
    const split = [
      counts.final > 0 ? `${counts.final} final` : null,
      counts.live > 0 ? `${counts.live} live` : null,
      counts.upcoming > 0 ? `${counts.upcoming} upcoming` : null,
      counts.postponed > 0 ? `${counts.postponed} postponed` : null,
      counts.canceled > 0 ? `${counts.canceled} canceled` : null,
    ].filter((part): part is string => part !== null);
    parts.push(`${countOf(games.length, wordsFor(games))}: ${split.join(", ")}`);
  }
  for (const entry of events) parts.push(entry.event.name);
  return parts.join(", ");
}

/**
 * The day a month opens on: today when the month holds it, otherwise its
 * first day with anything on, otherwise its first day.
 */
export function defaultDay(
  month: DayRange,
  todayKey: string,
  games: readonly Game[],
  events: readonly EventDays[],
): string {
  if (holdsDay(month, todayKey)) return todayKey;
  const busy = new Set(games.map(gameDay));
  return (
    dayKeys(month).find(
      (key) => busy.has(key) || eventsOn(events, key).length > 0,
    ) ?? month.first
  );
}

// ── Freshness ───────────────────────────────────────────────────────────

/**
 * Whether the schedule has to be read again on a beat: a game on screen is
 * in progress. Most live games are also in the Today payload, which is
 * polled and laid over the schedule; reading the schedule as well is what
 * catches the one that is not (a game still going after midnight), and
 * what writes a final score into the schedule's own copy.
 */
export function hasLiveGame(games: readonly Game[]): boolean {
  return games.some((game) => game.phase === "in_progress");
}

/** Which sides of a game are followed, for the key under the grid. */
export function hasFollowedSide(game: Game): boolean {
  return isFollowedSide(game, game.home) || isFollowedSide(game, game.away);
}
