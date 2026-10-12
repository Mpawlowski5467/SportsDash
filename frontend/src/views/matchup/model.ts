/**
 * The Matchup view's logic, kept pure (no React) so it can be unit-tested:
 * which games are offered and in what order, what the status block says
 * about a game in each phase, the win-probability rows and the colours of
 * their bars, the upset signals, the tally of the two sides' meetings,
 * which side gets the "last few games" table, and what a standings row
 * says about a side.
 *
 * Everything is derived from payloads the app already receives. Where the
 * mockup shows something the API does not carry, there is no function for
 * it: a venue's city, the weather during a live game, a short city-style
 * team name ("Boston"), a team's second colour, the season a meeting
 * belongs to (so nothing here is called a "season series"), and a
 * play-by-play page to open.
 *
 * The pre-game win probability is the provider's predictor figure. It was
 * checked against the live feeds on 2026-10-11 and found FROZEN: it is the
 * model's last run before the game and does not move during it or after
 * it, so it is labelled "Before the start" in every phase, and the "right
 * now" figure is only ever the last point of the in-game series.
 */

import { contrastRatio, normalizeHex } from "../../lib/color";
import { fightResultOf, fightResultSummary } from "../../lib/fight";
import {
  abbrev,
  countWord,
  isIndividualSport,
  shortSide,
  showsScore,
  sportWords,
  type SportWords,
} from "../../lib/labels";
import { formatMoneyline, formatSpread } from "../../lib/odds";
import { ordinal } from "../../lib/periodLabel";
import {
  dateKeyInZone,
  formatShortDate,
  formatTime,
  localDateKey,
  localKeyFromDate,
  longDay,
  weekdayShort,
} from "../../lib/time";
import { detectSignal, type UpsetSignal } from "../../lib/whyWatch";
import type {
  Game,
  GameOdds,
  GameSide,
  GameSummary,
  HeadToHeadRecord,
  League,
  LineupSlot,
  PlayerStatus,
  Sport,
  Standings,
  TeamLineup,
  Weather,
} from "../../types";
import { hasCatalog, heroStats } from "../league/model";
import {
  formRecord,
  hasSeasonRecord,
  perspective,
  type FormSummary,
} from "../results/model";
import {
  dayKeyOffset,
  isFollowedSide,
  leadingSide,
  liveStatus,
  liveWinProbability,
  nextStartNote,
  recordText,
  resultFor,
  standingRowFor,
  tableName,
  type Outcome,
  type SideKey,
} from "../today/model";

export type { Outcome, SideKey };

/** How far ahead the picker looks, as the view always has. */
export const PREVIEW_DAYS = 14;
/**
 * How many games get a button of their own. Past one more than this the
 * rest go in a dropdown: fifteen days of a whole league is a wall of
 * buttons otherwise.
 */
export const PICKER_BUTTONS = 3;
/** How many of the followed side's finished games the table lists. */
export const LAST_GAMES = 8;
/**
 * How many meetings are drawn in full, as mocked; older ones are listed
 * compactly under them. Three keeps the sheet level with its neighbours
 * (eight results, two standings panels), which is how it was drawn.
 */
export const SERIES_GAMES = 3;
/**
 * How long after its start a game the reader chose stays on screen once it
 * is over or called off. Long enough to read the final score that evening;
 * by the next morning the page is back on what is coming up.
 */
export const ENDED_STAYS_MS = 12 * 60 * 60_000;
/** The batch odds endpoint prices this many games per request and no more. */
export const ODDS_BATCH = 60;

const otherSide = (side: SideKey): SideKey => (side === "home" ? "away" : "home");

const nameKey = (name: string): string =>
  name.trim().toLowerCase().replace(/\s+/g, " ");

/**
 * A side's code where there is only room for one: the provider's, else the
 * first letters of its name. An empty code (some providers send one) is no
 * code.
 */
export function sideCode(side: Pick<GameSide, "name" | "abbreviation">): string {
  const given = side.abbreviation?.trim() ?? "";
  return given !== "" ? given : abbrev(side.name);
}

// ── The games on offer ──────────────────────────────────────────────────

const byId = (schedule: readonly Game[], fresh: readonly Game[]): Map<string, Game> => {
  const games = new Map<string, Game>();
  for (const game of schedule) games.set(game.id, game);
  // The Today payload is the one that is polled: its copy is the newer.
  for (const game of fresh) games.set(game.id, game);
  return games;
};

/**
 * The games a preview can be opened for: those in progress, then those
 * still to start, soonest first. `fresh` is the Today payload, laid over
 * the schedule so a game turns live (or leaves, once final) without a
 * reload; it also stands in alone while the schedule has not loaded. A game
 * left marked "scheduled" on a day that has passed is not coming up.
 */
export function previewGames(
  schedule: readonly Game[],
  fresh: readonly Game[],
  todayKey: string,
  zone: string | undefined,
): Game[] {
  return [...byId(schedule, fresh).values()]
    .filter(
      (game) =>
        game.phase === "in_progress" ||
        (game.phase === "scheduled" &&
          dateKeyInZone(game.start_time, zone) >= todayKey),
    )
    .sort(
      (a, b) =>
        Number(b.phase === "in_progress") - Number(a.phase === "in_progress") ||
        a.start_time.localeCompare(b.start_time) ||
        a.id.localeCompare(b.id),
    );
}

/**
 * The game on screen. The one the reader chose while it is on offer; and
 * still that one for a while once it has ended or been called off (while
 * the loaded schedule knows it, and within `ENDED_STAYS_MS` of its start),
 * so a game being watched does not vanish from under the reader at the
 * final out. "Within" counts both ways: a game called off days ahead of
 * its start does not hold the page until that day comes. After that, and
 * with no choice made, it is the first on offer: the live game, else the
 * next to start. That is what the view always fell back to, and what a
 * kiosk nobody touches should show.
 */
export function chooseGame(
  list: readonly Game[],
  schedule: readonly Game[],
  fresh: readonly Game[],
  chosenId: string | undefined,
  now: Date,
): Game | null {
  if (chosenId !== undefined) {
    const offered = list.find((game) => game.id === chosenId);
    if (offered !== undefined) return offered;
    const known = byId(schedule, fresh).get(chosenId);
    if (
      known !== undefined &&
      known.phase !== "scheduled" &&
      Math.abs(now.getTime() - new Date(known.start_time).getTime()) <
        ENDED_STAYS_MS
    ) {
      return known;
    }
  }
  return list[0] ?? null;
}

export interface PickerSplit {
  /** Games with a button each, in list order. */
  buttons: Game[];
  /** The rest, for the dropdown. */
  rest: Game[];
}

/**
 * Which games get a button. All of them while they fit; otherwise the
 * first few, with the game on screen always among them (it takes the last
 * button), so the pressed state is never hidden inside the dropdown.
 */
export function pickerSplit(
  list: readonly Game[],
  current: Game | null,
  max: number = PICKER_BUTTONS,
): PickerSplit {
  const extra =
    current !== null && !list.some((game) => game.id === current.id)
      ? current
      : null;
  if (list.length + (extra === null ? 0 : 1) <= max + 1) {
    return { buttons: extra === null ? [...list] : [...list, extra], rest: [] };
  }
  let buttons = list.slice(0, max);
  if (current !== null && !buttons.some((game) => game.id === current.id)) {
    buttons = [...list.slice(0, max - 1), current];
  }
  const shown = new Set(buttons.map((game) => game.id));
  return { buttons, rest: list.filter((game) => !shown.has(game.id)) };
}

export interface PickerWhen {
  /** The game is in progress: drawn as the spot chip. */
  live: boolean;
  /** "Live now", "10:10 PM", "Fri Jul 31", "Final". */
  text: string;
}

/**
 * The viewer's own calendar day, "YYYY-MM-DD". The day words ("Tonight",
 * "Tomorrow", a bare start time meaning today) are decided on it, because
 * the clock times beside them are printed in the viewer's zone. The
 * server's day can be a different one for an hour or more every night (a
 * server on Eastern time, a viewer on Central), and a game at 7:00 PM
 * tomorrow must not read "Tonight 7:00 PM". The server's day is only used
 * for which games are still on offer (`previewGames`).
 */
export function viewerDay(now: Date): string {
  return localKeyFromDate(now);
}

/**
 * The second line of a picker button. `today` is the viewer's day (see
 * `viewerDay`): a bare time means today on the clock the time is read on.
 */
export function pickerWhen(game: Game, today: string): PickerWhen {
  switch (game.phase) {
    case "in_progress":
      return { live: true, text: "Live now" };
    case "final":
      return { live: false, text: "Final" };
    case "postponed":
      return { live: false, text: "Postponed" };
    case "canceled":
      return { live: false, text: "Canceled" };
    default:
      return {
        live: false,
        text:
          localDateKey(game.start_time) === today
            ? formatTime(game.start_time)
            : `${weekdayShort(game.start_time)} ${formatShortDate(game.start_time)}`,
      };
  }
}

/** "at" between teams; people have no home, so "vs". */
export function joiner(sport: Sport): "at" | "vs" {
  return isIndividualSport(sport) ? "vs" : "at";
}

/** "Boston Red Sox at Athletics", "Jannik Sinner vs Carlos Alcaraz". */
export function matchupNames(game: Game): string {
  return `${game.away.name} ${joiner(game.sport)} ${game.home.name}`;
}

/** A picker button's accessible name: the blocks inside it are only codes. */
export function pickerLabel(game: Game, when: PickerWhen): string {
  return `${matchupNames(game)}, ${when.live ? "live now" : when.text}`;
}

/** A side in one word: a team's code, a person's surname. */
export function sideWord(game: Game, side: GameSide): string {
  return isIndividualSport(game.sport) ? shortSide(game, side) : sideCode(side);
}

/** "ARI at LAD" for teams; "Sinner vs Alcaraz" for people, who have no home. */
export function shortPairing(game: Game): string {
  return `${sideWord(game, game.away)} ${joiner(game.sport)} ${sideWord(game, game.home)}`;
}

/** A dropdown option: "ARI at LAD · Fri Jul 31". */
export function optionLabel(game: Game, when: PickerWhen): string {
  return `${shortPairing(game)} · ${when.text}`;
}

/** "11 more games": what the dropdown says while none of its games is chosen. */
export function moreGamesLabel(count: number): string {
  return `${count} more ${count === 1 ? "game" : "games"}`;
}

/**
 * The newest of several copies of one thing, each with the time its query
 * last answered. The same game arrives in four payloads (the schedule,
 * Today, the box score, the preview), and only some of them are polled.
 */
export function freshest<T>(
  copies: readonly { value: T | null | undefined; at: number }[],
): T | undefined {
  let best: { value: T; at: number } | undefined;
  for (const copy of copies) {
    if (copy.value === null || copy.value === undefined) continue;
    if (best === undefined || copy.at > best.at) {
      best = { value: copy.value, at: copy.at };
    }
  }
  return best?.value;
}

// ── The game section ────────────────────────────────────────────────────

/** Evening starts at five, as on Today: a game after it is "tonight". */
const EVENING_HOUR = 17;

/**
 * The section tab: what state the game on screen is in. `today` is the
 * viewer's day (see `viewerDay`).
 */
export function sectionTitle(game: Game, today: string): string {
  switch (game.phase) {
    case "in_progress":
      return "Live now";
    case "final":
      return "Final";
    case "postponed":
      return "Postponed";
    case "canceled":
      return "Canceled";
    default:
      if (localDateKey(game.start_time) !== today) return "Upcoming";
      return new Date(game.start_time).getHours() >= EVENING_HOUR
        ? "Tonight"
        : "Today";
  }
}

/**
 * The note beside the tab: "MLB · Thursday, July 30 · Boston Red Sox at
 * Athletics". A tournament round or fight card ("Wimbledon · QF") goes
 * here too; for people it stands in place of the pairing, which the two
 * rows under it already spell out.
 */
export function sectionNote(game: Game, leagueName: string | undefined): string {
  const series = game.series !== null && game.series !== "" ? game.series : null;
  return [
    leagueName ?? null,
    longDay(game.start_time),
    series,
    isIndividualSport(game.sport) && series !== null ? null : matchupNames(game),
  ]
    .filter((part): part is string => part !== null && part !== "")
    .join(" · ");
}

/** "run", "goal", "point", "set": what a score counts, or null (a fight). */
function scoreUnit(sport: Sport): { one: string; many: string } | null {
  const noun = sportWords(sport).tally?.noun.toLowerCase();
  if (noun === undefined) return null;
  return { one: noun.replace(/s$/, ""), many: noun };
}

/** Whether a sport's score is a count of sets won. */
const countsSets = (sport: Sport): boolean =>
  sport === "tennis" || sport === "volleyball";

/**
 * Teams take the plural verb, as a sports page writes them ("the Red Sox
 * lead", "the Heat lead", "Chelsea lead"); a person takes the singular.
 */
export function leadVerb(sport: Sport): "lead" | "leads" {
  return isIndividualSport(sport) ? "leads" : "lead";
}

const margin = (count: number, unit: { one: string; many: string }): string =>
  `${countWord(count)} ${count === 1 ? unit.one : unit.many}`;

/**
 * One sentence on where the score stands, in the sport's own words:
 * "Athletics lead by one run", "Level at 1–1", "Jannik Sinner leads by one
 * set", and once it is over "Athletics won by two runs" or "Drawn 1–1".
 *
 * Null wherever it cannot be said truthfully: a game with no score yet, a
 * postponed one, and a fight in progress (its "score" is a count of rounds
 * and says nothing about who is ahead). A finished fight gives its method
 * of victory, which is its result.
 */
export function leadSentence(game: Game): string | null {
  if (game.sport === "mma") {
    const fight = fightResultOf(game);
    if (fight === null) return null;
    const summary = fightResultSummary(fight);
    const winner = leadingSide(game);
    if (
      winner === null ||
      fight.method === "draw" ||
      fight.method === "no_contest"
    ) {
      return summary;
    }
    return `${game[winner].name} won · ${summary}`;
  }
  if (!showsScore(game)) return null;
  const unit = scoreUnit(game.sport);
  if (unit === null) return null;
  const home = game.home.score as number;
  const away = game.away.score as number;
  const final = game.phase === "final";
  const leader = leadingSide(game);

  if (leader === null) {
    if (final) {
      return game.sport === "soccer" ? `Drawn ${away}–${home}` : `Tied ${away}–${home}`;
    }
    if (countsSets(game.sport)) {
      return home === 0 ? null : `Level at ${margin(home, unit)} each`;
    }
    return game.sport === "soccer" || game.sport === "hockey"
      ? `Level at ${away}–${home}`
      : `Tied at ${home}`;
  }
  const by = margin(Math.abs(home - away), unit);
  const name = game[leader].name;
  return final ? `${name} won by ${by}` : `${name} ${leadVerb(game.sport)} by ${by}`;
}

export interface GameStatus {
  /** The game is in progress: the spot chip reads "Live". */
  live: boolean;
  /** The small line over the headline: the day, for a game not in progress. */
  eyebrow: string | null;
  /** "Bottom 5th", "10:10 PM", "Final", "Postponed". */
  headline: string;
  /** The clock beside a period that has one. */
  detail: string | null;
  sentence: string | null;
}

/**
 * The status block for every phase. The mockup draws a live game only; a
 * game still to come shows its day and start time where the live one shows
 * the inning, and a finished or called-off one says so. "Tonight", "Today"
 * and "Tomorrow" are the viewer's (see `viewerDay`), like the time under
 * them.
 */
export function gameStatus(game: Game, now: Date): GameStatus {
  if (game.phase === "in_progress") {
    const status = liveStatus(game);
    return {
      live: true,
      eyebrow: null,
      headline: status.headline,
      detail: status.detail,
      sentence: leadSentence(game),
    };
  }
  if (game.phase === "scheduled") {
    const key = localDateKey(game.start_time);
    const today = viewerDay(now);
    const eyebrow =
      key === today
        ? new Date(game.start_time).getHours() >= EVENING_HOUR
          ? "Tonight"
          : "Today"
        : key === dayKeyOffset(today, 1)
          ? "Tomorrow"
          : longDay(game.start_time);
    const minutes =
      (new Date(game.start_time).getTime() - now.getTime()) / 60_000;
    return {
      live: false,
      eyebrow,
      headline: formatTime(game.start_time),
      detail: null,
      // Only inside the hour: further out it would repeat the time above it.
      sentence: minutes < 60 ? nextStartNote([game], now) : null,
    };
  }
  return {
    live: false,
    eyebrow: longDay(game.start_time),
    headline:
      game.phase === "final"
        ? "Final"
        : game.phase === "postponed"
          ? "Postponed"
          : "Canceled",
    detail: null,
    sentence: game.phase === "final" ? leadSentence(game) : null,
  };
}

/**
 * How much weight a side's name and score carry: the side in front (or the
 * winner) is set in ink and bold and the other steps back, as mocked. With
 * no score to compare, or a level one, both stand equal.
 */
export function sideTone(game: Game, side: SideKey): "lead" | "trail" | "even" {
  if (!showsScore(game) && fightResultOf(game) === null) return "even";
  const leader = leadingSide(game);
  if (leader === null) return "even";
  return leader === side ? "lead" : "trail";
}

/** "Away" / "Home", with "Followed team" after it; people have no home. */
export function sideCaption(game: Game, side: SideKey): string {
  const followed = isFollowedSide(game, game[side]);
  const individual = isIndividualSport(game.sport);
  return [
    individual ? null : side === "home" ? "Home" : "Away",
    followed ? (individual ? "Followed player" : "Followed team") : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

export interface SideFigure {
  label: string;
  value: string;
}

/**
 * The two figures beside a side's name: its record and its place in its
 * table ("AL East", "3rd"). A ranking (tennis) has a place and no record.
 * Empty when the side has no row: a cup tie, a fighter, a table not loaded.
 */
export function sideFigures(
  side: GameSide,
  standings: Standings | undefined,
): SideFigure[] {
  const row = standingRowFor(standings, side);
  if (row === undefined || standings === undefined) return [];
  const figures: SideFigure[] = [];
  if (hasSeasonRecord(standings.sport)) {
    figures.push({ label: "Record", value: recordText(row, standings.sport) });
  }
  figures.push({ label: tableName(row, standings), value: ordinal(row.rank) });
  return figures;
}

/**
 * The width, in rem, of one figure column beside the two names, so the two
 * rows line up and neither figure wraps: the mockup's 128px for a record
 * like "58–51", and wider for "10–0–2" or "52–22–10" (each character of the
 * 40px condensed figure is about 1.2rem wide, after a 1.25rem inset).
 */
export function figureColumnRem(values: readonly string[]): number {
  const longest = Math.max(0, ...values.map((value) => value.length));
  return Math.max(8, Math.ceil((longest * 1.2 + 1.25) * 4) / 4);
}

/**
 * The forecast for a game still to start: "High 88°F · Low 63°F · 5%
 * chance of precipitation". Only the figures that are for the day the game
 * starts in, as on the Calendar's cards. The payload's temperature,
 * condition and wind are the venue's weather at the moment the server
 * asked, which is not the weather of a game tomorrow or next week, so they
 * are never printed. Null when the day has none of the three.
 *
 * That day is the server's: the UTC date of the start, which is not always
 * the date at the venue. So the cell is labelled "Forecast" and nothing
 * here calls the figures the game day's (see `weatherLine` in the
 * Calendar's model).
 */
export function forecastFact(weather: Weather): string | null {
  const unit = weather.units === "imperial" ? "°F" : "°C";
  const parts = [
    weather.high !== null ? `High ${Math.round(weather.high)}${unit}` : null,
    weather.low !== null ? `Low ${Math.round(weather.low)}${unit}` : null,
    weather.precip_chance !== null
      ? `${Math.round(weather.precip_chance)}% chance of precipitation`
      : null,
  ].filter((part): part is string => part !== null);
  return parts.length === 0 ? null : parts.join(" · ");
}

// ── Win probability ─────────────────────────────────────────────────────

export interface Shares {
  away: number;
  home: number;
}

/** Sports whose games cannot end level, so one side's chance fixes the other's. */
const NO_TIES = new Set<Sport>(["baseball", "basketball"]);

/**
 * The model's projection from before the start, as the provider's
 * predictor gives it (it does not move once the game is under way: see the
 * predictor check of 2026-10-11). Both sides' figures as sent; when the
 * feed sends only one (seen for basketball) the other is its complement,
 * but only in a sport with no ties, where that is arithmetic and not a
 * guess. Football carries a small tie share, so its two never have to sum
 * to 100 and neither is derived from the other.
 */
export function pregameShares(
  odds: GameOdds | null | undefined,
  sport: Sport,
): Shares | null {
  if (odds === null || odds === undefined) return null;
  const { home_win_pct: home, away_win_pct: away } = odds;
  const usable = (value: number | null): value is number =>
    value !== null && Number.isFinite(value) && value >= 0 && value <= 100;
  if (usable(home) && usable(away)) return { away, home };
  if (!NO_TIES.has(sport)) return null;
  if (usable(home)) return { away: 100 - home, home };
  if (usable(away)) return { away, home: 100 - away };
  return null;
}

export interface ProbabilityRow {
  key: "before" | "now";
  /** "Before the start" / "Right now": sport-neutral on purpose. */
  title: string;
  /** Carries the Live chip. */
  live: boolean;
  shares: Shares;
  /** Whole percentages, as printed. */
  percent: Shares;
  favored: SideKey | null;
}

/**
 * Two shares as whole percentages. When they make up the whole (within
 * rounding) the smaller is what the larger leaves, so 50.5 and 49.5 never
 * print as 51% and 50%; shares that do not sum to 100 (football's, with
 * its tie share) are each rounded on their own.
 */
export function percentPair(shares: Shares): Shares {
  const total = shares.away + shares.home;
  if (Math.abs(total - 100) > 0.5) {
    return { away: Math.round(shares.away), home: Math.round(shares.home) };
  }
  if (shares.home >= shares.away) {
    const home = Math.round(shares.home);
    return { away: 100 - home, home };
  }
  const away = Math.round(shares.away);
  return { away, home: 100 - away };
}

function probabilityRow(
  key: ProbabilityRow["key"],
  shares: Shares,
): ProbabilityRow {
  return {
    key,
    title: key === "before" ? "Before the start" : "Right now",
    live: key === "now",
    shares,
    percent: percentPair(shares),
    favored:
      shares.away === shares.home
        ? null
        : shares.home > shares.away
          ? "home"
          : "away",
  };
}

/**
 * The rows of the Win probability sheet for the game's phase. Before the
 * start: the projection alone. In progress: the projection, and "Right
 * now" from the last point of the in-game series when the provider sends
 * one. Finished: the projection alone (the series ends at 100 or 0, which
 * the score already says). A row with no data is not drawn, and a
 * postponed or canceled game has none.
 */
export function probabilityRows(
  game: Game,
  odds: GameOdds | null | undefined,
  summary: GameSummary | null | undefined,
): ProbabilityRow[] {
  if (game.phase === "postponed" || game.phase === "canceled") return [];
  const rows: ProbabilityRow[] = [];
  const before = pregameShares(odds, game.sport);
  if (before !== null) rows.push(probabilityRow("before", before));
  if (game.phase === "in_progress") {
    const now = liveWinProbability(summary);
    if (now !== null) rows.push(probabilityRow("now", now));
  }
  return rows;
}

/** "team", "player", "fighter": what one side of a game is, in the sport's word. */
export function sideNoun(sport: Sport): "team" | "player" | "fighter" {
  if (sport === "mma") return "fighter";
  return isIndividualSport(sport) ? "player" : "team";
}

/** The note beside the tab, for the rows that are there. */
export function probabilityNote(
  rows: readonly ProbabilityRow[],
  sport: Sport,
): string {
  const each = `Each ${sideNoun(sport)}’s chance to win`;
  const before = rows.some((row) => row.key === "before");
  const now = rows.some((row) => row.key === "now");
  if (before && now) return `${each}, before the start and now`;
  if (now) return `${each} right now`;
  return `${each}, as projected before the start`;
}

/** "Boston Red Sox favored"; "Even" when the model cannot split them. */
export function favoredLine(game: Game, row: ProbabilityRow): string {
  return row.favored === null ? "Even" : `${game[row.favored].name} favored`;
}

/** What a bar says aloud: "Before the start: Boston Red Sox 62 percent, Athletics 38 percent". */
export function barLabel(game: Game, row: ProbabilityRow): string {
  return `${row.title}: ${game.away.name} ${row.percent.away} percent, ${game.home.name} ${row.percent.home} percent`;
}

/** Under this ratio against the sheet a fill dissolves into it. */
const BAR_VISIBLE_RATIO = 1.5;
/** Under this distance two colours read as the same one in a bar. */
const BAR_DISTINCT = 100;

/** A cheap perceptual distance between two `#rrggbb` colours (0 to about 765). */
export function colorDistance(a: string, b: string): number {
  const parse = (hex: string) =>
    [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16));
  const [r1, g1, b1] = parse(a);
  const [r2, g2, b2] = parse(b);
  const mean = (r1 + r2) / 2;
  const dr = r1 - r2;
  const dg = g1 - g2;
  const db = b1 - b2;
  return Math.sqrt(
    (2 + mean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - mean) / 256) * db * db,
  );
}

/**
 * The two fills of a win-probability bar, or null when the teams' own
 * colours cannot be told apart: one is missing, one would dissolve into
 * the sheet it is drawn on (white on the light sheet, navy on the dark
 * one), or the two are near enough the same colour. The bar is then drawn
 * without colour, the favoured share solid and the other outlined, which
 * is distinct in every theme.
 */
export function barFills(
  away: string | null | undefined,
  home: string | null | undefined,
  sheet: string,
): { away: string; home: string } | null {
  const awayFill = normalizeHex(away);
  const homeFill = normalizeHex(home);
  if (awayFill === null || homeFill === null) return null;
  if (
    contrastRatio(awayFill, sheet) < BAR_VISIBLE_RATIO ||
    contrastRatio(homeFill, sheet) < BAR_VISIBLE_RATIO
  ) {
    return null;
  }
  if (colorDistance(awayFill, homeFill) < BAR_DISTINCT) return null;
  return { away: awayFill, home: homeFill };
}

export interface KeyValue {
  label: string;
  value: string;
}

/**
 * The betting lines, as the view always showed them: both moneylines, the
 * home side's spread, the total, and whose lines they are. Only what the
 * sportsbook priced; empty when it priced nothing (the name alone is not a
 * line), and for a game that was postponed or canceled.
 */
export function bettingLines(game: Game, odds: GameOdds | null | undefined): KeyValue[] {
  if (odds === null || odds === undefined) return [];
  // A game that is off has no lines: what was priced was the game not played.
  if (game.phase === "postponed" || game.phase === "canceled") return [];
  const lines: KeyValue[] = [];
  const away = sideCode(game.away);
  const home = sideCode(game.home);
  if (odds.away_moneyline !== null || odds.home_moneyline !== null) {
    lines.push({
      label: "Moneyline",
      value: `${away} ${formatMoneyline(odds.away_moneyline)} · ${home} ${formatMoneyline(odds.home_moneyline)}`,
    });
  }
  if (odds.spread !== null) {
    lines.push({ label: "Spread", value: `${home} ${formatSpread(odds.spread)}` });
  }
  if (odds.over_under !== null) {
    lines.push({ label: "Total", value: `O/U ${odds.over_under}` });
  }
  if (lines.length > 0 && odds.provider !== null && odds.provider !== "") {
    lines.push({ label: "Lines from", value: odds.provider });
  }
  return lines;
}

/**
 * The note beside "Betting lines" when the lines stand alone. They are the
 * sportsbook's pre-game lines (the API's own description of the field) and
 * do not move with the score, so once the game is under way or over the
 * note says when they were priced rather than reading as current.
 */
export function linesNote(game: Game): string {
  return game.phase === "scheduled"
    ? "What the sportsbook has priced for this game"
    : "What the sportsbook priced before the start";
}

// ── Upset radar ─────────────────────────────────────────────────────────

export interface LiveUpset {
  /** The side in front. */
  leader: SideKey;
  /** Its projected chance before the start, as a whole percentage under 50. */
  pct: number;
}

/**
 * A live game running against the pre-game projection: the side in front
 * on the scoreboard was given less than an even chance before the start.
 * Needs the leader's own projected figure (see `pregameShares`), so it
 * never fires in a sport with no predictor. The figure is the one the
 * "Before the start" row prints (`percentPair`), so the card and the row
 * never disagree, and a side that rounds to 50% is not called an underdog.
 */
export function liveUpset(game: Game, odds: GameOdds | null | undefined): LiveUpset | null {
  if (game.phase !== "in_progress" || !showsScore(game)) return null;
  const leader = leadingSide(game);
  if (leader === null) return null;
  const shares = pregameShares(odds, game.sport);
  if (shares === null) return null;
  const mine = shares[leader];
  const theirs = shares[otherSide(leader)];
  if (!(mine < 50) || !(mine < theirs)) return null;
  const pct = percentPair(shares)[leader];
  return pct < 50 ? { leader, pct } : null;
}

export interface RadarSignal {
  game: Game;
  /** The model's favourite is the betting underdog (the signal there always was). */
  market: UpsetSignal | null;
  /** The live leader was the pre-game underdog. */
  live: LiveUpset | null;
}

/** Every flagged game, in the order the games are given. */
export function radarSignals(
  games: readonly Game[],
  oddsById: Record<string, GameOdds> | undefined,
): RadarSignal[] {
  if (oddsById === undefined) return [];
  const signals: RadarSignal[] = [];
  for (const game of games) {
    const odds = oddsById[game.id];
    if (odds === undefined) continue;
    const market = detectSignal(game, odds);
    const live = liveUpset(game, odds);
    if (market !== null || live !== null) signals.push({ game, market, live });
  }
  return signals;
}

/** "Athletics lead after starting as a 38% underdog." */
export function liveUpsetSentence(game: Game, upset: LiveUpset): string {
  return `${game[upset.leader].name} ${leadVerb(game.sport)} after starting as a ${upset.pct}% underdog.`;
}

/** "Model likes ATH (55%) — line has them at +144": the copy the radar always had. */
export function marketSentence(signal: UpsetSignal): string {
  return `Model likes ${signal.underdogAbbr} (${signal.winPct}%) — line has them at ${formatMoneyline(signal.moneyline)}`;
}

/** "1 signal across your games". */
export function radarNote(count: number): string {
  return `${count} ${count === 1 ? "signal" : "signals"} across your games`;
}

/**
 * The line under the cards: which card is the game on screen, and what a
 * signal is. Both kinds are named, because both are flagged.
 */
export function radarFootnote(viewing: boolean, count: number): string {
  const what =
    "A game is flagged when the model’s favorite is the betting underdog, or when the side in front of a live game was the underdog before the start.";
  if (!viewing) return what;
  return `${count === 1 ? "You are viewing this game." : "You are viewing the highlighted game."} ${what}`;
}

// ── Meetings ────────────────────────────────────────────────────────────

const time = (game: Game): number => new Date(game.start_time).getTime();

/**
 * The two sides' past meetings, newest first, from both places they come
 * from: the stored finals (current, and the only ones a box score can be
 * opened for) and the provider's season lists behind the cross-season
 * record (complete where the stored ones have holes from time the app was
 * off, but hours behind). A stored copy replaces the listed one.
 */
export function mergeMeetings(
  stored: readonly Game[],
  record: HeadToHeadRecord | null,
): Game[] {
  const games = new Map<string, Game>();
  for (const game of record?.meetings ?? []) games.set(game.id, game);
  for (const game of stored) games.set(game.id, game);
  return [...games.values()].sort(
    (a, b) => time(b) - time(a) || a.id.localeCompare(b.id),
  );
}

/** Who won a meeting; "draw" when nobody did; null when it has no result. */
export function meetingWinner(game: Game): SideKey | "draw" | null {
  if (game.sport === "mma") {
    const method = game.fight_result?.method;
    if (method === "no_contest") return null;
    if (method === "draw") return "draw";
    return leadingSide(game);
  }
  if (game.home.score === null || game.away.score === null) return null;
  return leadingSide(game) ?? "draw";
}

export interface Series {
  /** The meetings drawn in full, OLDEST first, as mocked. */
  shown: Game[];
  /** Older meetings, newest first, listed compactly. */
  earlier: Game[];
}

/** The newest few meetings in full and the rest behind them. */
export function splitSeries(
  meetings: readonly Game[],
  max: number = SERIES_GAMES,
): Series {
  return {
    shown: meetings.slice(0, max).reverse(),
    earlier: meetings.slice(max),
  };
}

export interface SeriesTally {
  /** The side of the game on screen that has won more of them; null level. */
  leader: SideKey | null;
  /** Wins of the side in front (either side's, when level) and of the other. */
  ahead: number;
  behind: number;
  draws: number;
  /** Meetings counted. */
  played: number;
}

/**
 * Wins for each side of the game on screen across the meetings given.
 * Sides swap home and away from one meeting to the next, so a meeting's
 * winner is matched to today's sides by name (or by team id).
 */
export function seriesTally(game: Game, meetings: readonly Game[]): SeriesTally {
  const wins: Record<SideKey, number> = { home: 0, away: 0 };
  let draws = 0;
  let played = 0;
  const sideOf = (winner: GameSide): SideKey | null => {
    for (const key of ["home", "away"] as const) {
      const side = game[key];
      if (
        (winner.team_id !== null && winner.team_id === side.team_id) ||
        nameKey(winner.name) === nameKey(side.name)
      ) {
        return key;
      }
    }
    return null;
  };
  for (const meeting of meetings) {
    const winner = meetingWinner(meeting);
    if (winner === null) continue;
    if (winner === "draw") {
      draws += 1;
      played += 1;
      continue;
    }
    const key = sideOf(meeting[winner]);
    if (key === null) continue;
    wins[key] += 1;
    played += 1;
  }
  const leader =
    wins.home === wins.away ? null : wins.home > wins.away ? "home" : "away";
  return {
    leader,
    ahead: Math.max(wins.home, wins.away),
    behind: Math.min(wins.home, wins.away),
    draws,
    played,
  };
}

const drawsText = (draws: number): string =>
  draws === 0 ? "" : `, ${countWord(draws)} drawn`;

/**
 * The note beside the tab: "Boston Red Sox lead 2–1", "Level at 1–1", with
 * the draws after it. A single meeting has a winner, not a leader.
 */
export function seriesNote(game: Game, tally: SeriesTally): string | null {
  if (tally.played === 0) return null;
  if (tally.leader === null) {
    return tally.ahead === 0
      ? `${countWord(tally.draws).replace(/^./, (c) => c.toUpperCase())} drawn`
      : `Level at ${tally.ahead}–${tally.behind}${drawsText(tally.draws)}`;
  }
  const name = game[tally.leader].name;
  if (tally.played === 1) return `${name} won the last one`;
  return `${name} ${leadVerb(game.sport)} ${tally.ahead}–${tally.behind}${drawsText(tally.draws)}`;
}

/**
 * The two lines beside the big tally: "Boston Red Sox lead" over what it
 * covers. `drawn` is how many meetings are listed under it: one with no
 * result (a no-contest, a missing score) is listed but not counted, and
 * the line then says so ("over two of the three meetings below").
 */
export function seriesHeadline(
  game: Game,
  tally: SeriesTally,
  drawn: number = tally.played,
): { headline: string; sub: string } {
  const span =
    drawn > tally.played
      ? `${countWord(tally.played)} of the ${countWord(drawn)} meetings below`
      : tally.played === 1
        ? "the meeting below"
        : `the ${countWord(tally.played)} meetings below`;
  const draws = drawsText(tally.draws);
  if (tally.leader === null) {
    return { headline: "Level", sub: `over ${span}${draws}` };
  }
  const name = game[tally.leader].name;
  return tally.played === 1
    ? { headline: `${name} won`, sub: span }
    : { headline: `${name} ${leadVerb(game.sport)}`, sub: `over ${span}${draws}` };
}

/**
 * "All three games at Sutter Health Park", only when it is true: at least
 * two meetings, every one with a venue, and all the same one.
 */
export function venueSentence(
  meetings: readonly Game[],
  words: SportWords,
): string | null {
  if (meetings.length < 2) return null;
  const venue = meetings[0].venue;
  if (venue === null || venue.trim() === "") return null;
  if (meetings.some((meeting) => meeting.venue !== venue)) return null;
  return meetings.length === 2
    ? `Both ${words.games} at ${venue}`
    : `All ${countWord(meetings.length)} ${words.games} at ${venue}`;
}

/**
 * What a meeting of two people adds under their names: the tournament and
 * round or the fight card it was ("Wimbledon · Final"), and how a fight
 * ended. Null for teams, whose meetings are all league games.
 */
export function meetingDetail(meeting: Game): string | null {
  if (!isIndividualSport(meeting.sport)) return null;
  const fight = fightResultOf(meeting);
  const parts = [
    meeting.series !== null && meeting.series.trim() !== "" ? meeting.series : null,
    fight === null ? null : fightResultSummary(fight),
  ].filter((part): part is string => part !== null);
  return parts.length === 0 ? null : parts.join(" · ");
}

/**
 * A meeting's date in two lines: the weekday over "Jul 27" for one this
 * year, the year over it for an older one (a weekday says nothing about a
 * game three seasons ago).
 */
export function meetingDate(
  startTime: string,
  now: Date,
): { top: string; date: string } {
  const year = new Date(startTime).getFullYear();
  return {
    top: year === now.getFullYear() ? weekdayShort(startTime) : String(year),
    date: formatShortDate(startTime),
  };
}

const counted = (count: number, one: string, many: string): string =>
  `${count} ${count === 1 ? one : many}`;

/**
 * The cross-season record in words, from the followed side's point of
 * view: "Chelsea: 4 wins, 2 draws, 2 losses". Draws are named for soccer
 * and wherever there was one.
 */
export function recordLine(
  record: HeadToHeadRecord,
  sport: Sport,
): KeyValue {
  const parts = [
    counted(record.wins, "win", "wins"),
    sport === "soccer" || record.draws > 0
      ? counted(record.draws, "draw", "draws")
      : null,
    counted(record.losses, "loss", "losses"),
  ].filter((part): part is string => part !== null);
  return {
    label: `Last ${countWord(record.seasons)} seasons`,
    value: `${record.team_name}: ${parts.join(", ")}`,
  };
}

// ── "{Team}, last N" ────────────────────────────────────────────────────

/** Whether both sides of a game are followed teams. */
export function bothFollowed(game: Game): boolean {
  return isFollowedSide(game, game.home) && isFollowedSide(game, game.away);
}

/**
 * The side whose finished games are tabled. The followed one; with both
 * followed, the favourite team if it is playing and otherwise the home
 * side, unless the reader has switched (`chosen`). Null when neither side
 * is followed: results are only kept for followed teams.
 */
export function focusSide(
  game: Game,
  favoriteId: string | null,
  chosen?: SideKey,
): SideKey | null {
  const home = isFollowedSide(game, game.home);
  const away = isFollowedSide(game, game.away);
  if (!home && !away) return null;
  if (home !== away) return home ? "home" : "away";
  if (chosen !== undefined) return chosen;
  if (favoriteId !== null && game.away.team_id === favoriteId) return "away";
  return "home";
}

export interface LastGame {
  game: Game;
  outcome: Outcome;
  /** The team was at home. */
  home: boolean;
  opponent: GameSide;
  mine: number | null;
  theirs: number | null;
}

/**
 * The team's newest decided games, newest first: at most `limit`. A game
 * with no result for the team (a missing score, a no-contest) is skipped,
 * as the form figures skip it.
 */
export function lastGames(
  games: readonly Game[],
  team: { id: string; name: string },
  limit: number = LAST_GAMES,
): LastGame[] {
  const rows: LastGame[] = [];
  for (const game of games) {
    if (rows.length >= limit) break;
    const outcome = resultFor(game, team.id);
    const view = perspective(game, team);
    if (outcome === null || view === null) continue;
    rows.push({
      game,
      outcome,
      home: view.home,
      opponent: view.theirs,
      mine: view.mine.score,
      theirs: view.theirs.score,
    });
  }
  return rows;
}

/** "Athletics, last 8"; "Athletics, last game" for a single one. */
export function lastTitle(name: string, count: number, words: SportWords): string {
  return count === 1 ? `${name}, last ${words.game}` : `${name}, last ${count}`;
}

/** "Athletics'" and "Chelsea's". */
export function possessive(name: string): string {
  return name.endsWith("s") ? `${name}’` : `${name}’s`;
}

/** "Athletics' runs first": which way round the score column reads. */
export function scoreOrderNote(name: string, words: SportWords): string | null {
  if (words.tally === null) return null;
  return `${possessive(name)} ${words.tally.noun.toLowerCase()} first`;
}

/**
 * The three figures under the table: the record over the games listed, and
 * what the team scored and gave up in them, in the sport's own noun. The
 * tallies are left out for a sport whose score is not one (a fight).
 */
export function lastTotals(
  summary: FormSummary,
  sport: Sport,
  words: SportWords,
): KeyValue[] {
  const played = summary.form.length;
  if (played === 0) return [];
  const totals: KeyValue[] = [
    {
      label: played === 1 ? `In the last ${words.game}` : `In the last ${played}`,
      value: formRecord(summary, sport),
    },
  ];
  if (words.tally !== null && summary.scored !== null && summary.allowed !== null) {
    totals.push(
      {
        label: `${words.tally.noun} ${words.tally.forLabel.toLowerCase()}`,
        value: String(summary.scored),
      },
      {
        label: `${words.tally.noun} ${words.tally.againstLabel.toLowerCase()}`,
        value: String(summary.allowed),
      },
    );
  }
  return totals;
}

/** The form letters the API sends, narrowed: anything but W, L or D is dropped. */
export function formLetters(form: readonly string[]): Outcome[] {
  return form
    .map((letter) => letter.toUpperCase())
    .filter((letter): letter is Outcome => letter === "W" || letter === "L" || letter === "D");
}

/**
 * What a strip of form letters really is. The letters are the side's
 * stored finals, newest first. For a side that is not followed, in a
 * league followed team by team, the only games stored are the ones it
 * played against followed sides: that is said, not passed off as its form.
 */
export function formCaption(
  count: number,
  followed: boolean,
  league: Pick<League, "follow_all"> | undefined,
  sport?: Sport,
): string {
  const whole = followed || league?.follow_all === true;
  const span = count === 1 ? "Last result" : `Last ${countWord(count)} results`;
  if (whole) return `${span} on record, newest first`;
  const whom = sport === undefined ? "team" : sideNoun(sport);
  return `${span} against ${whom}s you follow, newest first`;
}

/**
 * A result's date in the table: "Jul 29", with the year once it is not this
 * year's ("Mar 1, 2025"). A fighter's last four bouts span years, and so do
 * a team's last eight in the first week of a season.
 */
export function resultDate(startTime: string, now: Date): string {
  const year = new Date(startTime).getFullYear();
  const date = formatShortDate(startTime);
  return year === now.getFullYear() ? date : `${date}, ${year}`;
}

// ── Standings context ───────────────────────────────────────────────────

export interface StandingsPanel {
  side: SideKey;
  followed: boolean;
  /** "4th" and "in AL West". */
  place: SideFigure;
  /** The figure the sport ranks by; null when it has none to show. */
  second: SideFigure | null;
  /** "45–63"; null for a ranking, which has no record. */
  record: string | null;
  /** The conference the row is in, for "Full standings" to open on. */
  group: string | null;
}

function panelFor(
  game: Game,
  side: SideKey,
  standings: Standings,
): StandingsPanel | null {
  const row = standingRowFor(standings, game[side]);
  if (row === undefined) return null;
  const table = standings.rows
    .filter((entry) => entry.group === row.group && entry.subgroup === row.subgroup)
    .sort((a, b) => a.rank - b.rank);
  const title = tableName(row, standings);
  const [place, raceFigure] = heroStats(row, table, standings.sport, title);
  let second: SideFigure | null =
    raceFigure === undefined
      ? null
      : { value: raceFigure.figure, label: raceFigure.label };
  if (second === null && standings.sport === "tennis" && row.points !== null) {
    second = {
      value: row.points.toLocaleString("en-US"),
      label: row.points === 1 ? "ranking point" : "ranking points",
    };
  }
  return {
    side,
    followed: isFollowedSide(game, game[side]),
    place: { value: place.figure, label: place.label },
    second,
    record: hasSeasonRecord(standings.sport)
      ? recordText(row, standings.sport)
      : null,
    group: row.group,
  };
}

/**
 * A panel per side that has a row in the table, the followed side first
 * (away before home otherwise). The second figure is the one the sport
 * ranks by: games back in baseball and basketball (games ahead for a
 * leader), points in soccer, hockey and volleyball, win percentage in
 * football, ranking points in tennis.
 */
export function standingsPanels(
  game: Game,
  standings: Standings | undefined,
): StandingsPanel[] {
  if (standings === undefined) return [];
  return (["away", "home"] as const)
    .map((side) => panelFor(game, side, standings))
    .filter((panel): panel is StandingsPanel => panel !== null)
    .sort((a, b) => Number(b.followed) - Number(a.followed));
}

/** The note beside the tab: what the second figure is. */
export function standingsNote(sport: Sport): string | undefined {
  switch (sport) {
    case "baseball":
    case "basketball":
      return "Games back of first place";
    case "soccer":
    case "hockey":
    case "volleyball":
      return "Place and points in the table";
    case "football":
      return "Place and win percentage";
    case "tennis":
      return "Place and points in the rankings";
    default:
      return undefined;
  }
}

// ── Squads ──────────────────────────────────────────────────────────────

/** A player's availability in words. Never a colour: an injury is not an alert. */
export function statusLabel(status: PlayerStatus): string {
  switch (status) {
    case "day_to_day":
      return "Day-to-day";
    case "injured":
      return "Injured";
    case "out":
      return "Out";
    default:
      return "Active";
  }
}

/**
 * The rows of a projected lineup in each sport, back to front, as the
 * lineup view has always grouped them. A sport with no list here is
 * grouped by the unit names its slots carry, in the order they come.
 */
const LINEUP_UNITS: Partial<Record<Sport, readonly (readonly [string, string])[]>> = {
  soccer: [
    ["GK", "Goalkeeper"],
    ["DEF", "Defence"],
    ["MID", "Midfield"],
    ["FWD", "Attack"],
  ],
  basketball: [
    ["G", "Guards"],
    ["F", "Forwards"],
    ["C", "Center"],
  ],
  hockey: [
    ["G", "Goalie"],
    ["D", "Defense"],
    ["F", "Forwards"],
  ],
  volleyball: [
    ["S", "Setter"],
    ["L", "Libero"],
    ["MB", "Middle"],
    ["OH", "Outside"],
    ["OPP", "Opposite"],
  ],
  football: [
    ["OFF", "Offense"],
    ["DEF", "Defense"],
    ["ST", "Special Teams"],
  ],
};

export interface LineupGroup {
  /** "Defence", "Batting order"; the unit's own key when the sport has no name for it. */
  label: string;
  slots: LineupSlot[];
}

/**
 * A side's projected lineup as groups of players. Baseball is one group,
 * the batting order as sent; the other sports are grouped by unit, and a
 * slot whose unit the sport does not name is kept in a group of its own
 * rather than dropped.
 */
export function lineupGroups(sport: Sport, lineup: TeamLineup): LineupGroup[] {
  if (lineup.slots.length === 0) return [];
  if (sport === "baseball") {
    return [{ label: "Batting order", slots: [...lineup.slots] }];
  }
  const named = LINEUP_UNITS[sport] ?? [];
  const groups: LineupGroup[] = [];
  for (const [key, label] of named) {
    const slots = lineup.slots.filter((slot) => slot.unit === key);
    if (slots.length > 0) groups.push({ label, slots });
  }
  const known = new Set(named.map(([key]) => key));
  for (const slot of lineup.slots) {
    if (known.has(slot.unit)) continue;
    const label = slot.unit === "" ? "Lineup" : slot.unit;
    const group = groups.find((entry) => entry.label === label);
    if (group !== undefined) group.slots.push(slot);
    else groups.push({ label, slots: [slot] });
  }
  return groups;
}

/** The number in a lineup row's first column: the batting order, where there is one. */
export function slotOrder(slot: LineupSlot, index: number, sport: Sport): string {
  if (sport !== "baseball") return "";
  return String(slot.order ?? index + 1);
}

// ── Team identity ───────────────────────────────────────────────────────

/** The most leagues whose catalogs are asked for at once. */
export const CATALOG_LEAGUES = 6;

// Whether a league has a setup catalog at all is the League view's rule.
export { hasCatalog };

/**
 * The leagues whose team colours are worth fetching: those of the games on
 * offer, in the order the games come, a handful at most.
 */
export function catalogLeagues(
  games: readonly Game[],
  leagues: readonly League[],
  max: number = CATALOG_LEAGUES,
): string[] {
  const ids: string[] = [];
  for (const game of games) {
    if (ids.length >= max) break;
    if (ids.includes(game.league_id)) continue;
    const league = leagues.find((entry) => entry.id === game.league_id);
    if (league !== undefined && hasCatalog(league)) ids.push(league.id);
  }
  return ids;
}
