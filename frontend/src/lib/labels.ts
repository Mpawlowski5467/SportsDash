import type { Game, GameSide, Sport } from "../types";
import { fightResultOf, fightResultSummary } from "./fight";
import { periodLabel } from "./periodLabel";
import { formatTime } from "./time";

/** Up-to-three-char abbreviation fallback for sides without one. */
export function abbrev(name: string): string {
  return name.slice(0, 3).toUpperCase();
}

/** A side's display label: its abbreviation, else derived from the name. */
export function sideLabel(side: GameSide): string {
  return side.abbreviation ?? abbrev(side.name);
}

/**
 * A name split before its last character, so that character can be kept on
 * one line with a marker that follows it: however the name wraps (even
 * inside a word too long for its column), the marker stays on the end of
 * the name it marks and never drops onto a line of its own.
 *
 * "Character" is a grapheme, so an accent written as a combining mark
 * stays with its letter.
 */
export function splitLastGlyph(name: string): { head: string; last: string } {
  const trimmed = name.trim();
  const glyphs =
    typeof Intl !== "undefined" && "Segmenter" in Intl
      ? [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(trimmed)].map(
          (part) => part.segment,
        )
      : Array.from(trimmed);
  const last = glyphs.pop() ?? "";
  return { head: glyphs.join(""), last };
}

/** Sports whose two "sides" are people rather than teams. */
export function isIndividualSport(sport: Game["sport"]): boolean {
  return sport === "tennis" || sport === "mma";
}

/**
 * A side where there is only room for a word: a team's code, a person's
 * surname. Athletes arrive with a clipped short name for a code ("J. Sinne"
 * for Jannik Sinner), which is neither a code nor a name.
 */
export function shortSide(game: Game, side: GameSide): string {
  if (!isIndividualSport(game.sport)) return sideLabel(side);
  const words = side.name.trim().split(/\s+/);
  return words[words.length - 1] || side.name;
}

/** "ARI at LAD" for teams; "Sinner vs Alcaraz" for people, who have no home. */
export function shortMatchup(game: Game): string {
  const joiner = isIndividualSport(game.sport) ? "vs" : "at";
  return `${shortSide(game, game.away)} ${joiner} ${shortSide(game, game.home)}`;
}

/**
 * A fight's "score" is a count of rounds and a live bout's means nothing,
 * so combat sports never show score numerals.
 */
export function showsScore(game: Game): boolean {
  return (
    game.sport !== "mma" &&
    (game.phase === "in_progress" || game.phase === "final") &&
    game.home.score !== null &&
    game.away.score !== null
  );
}

/**
 * The accessible name of a game row, button or score-line cell. Such a
 * control is one button, so its name replaces what is inside it for a
 * screen reader: the name therefore carries the state and the score as well
 * as who is playing, not only where the button leads.
 */
export function boxScoreLabel(game: Game): string {
  const { away, home } = game;
  const joiner = isIndividualSport(game.sport) ? "vs" : "at";
  if (showsScore(game)) {
    const state =
      game.phase === "final"
        ? "final"
        : ["live", periodLabel(game, "long"), game.clock]
            .filter(Boolean)
            .join(" ");
    return `Box score: ${away.name} ${away.score}, ${home.name} ${home.score}, ${state}`;
  }
  // A finished fight: who won and how, which is what its row shows.
  const fight = fightResultOf(game);
  if (fight !== null) {
    const summary = fightResultSummary(fight);
    const awayAhead = (away.score ?? 0) > (home.score ?? 0);
    const level = away.score === null || home.score === null || away.score === home.score;
    return level
      ? `Box score: ${away.name} ${joiner} ${home.name}, ${summary}`
      : `Box score: ${(awayAhead ? away : home).name} beat ${(awayAhead ? home : away).name}, ${summary}`;
  }
  const state =
    game.phase === "scheduled"
      ? formatTime(game.start_time)
      : game.phase === "postponed"
        ? "postponed"
        : game.phase === "canceled"
          ? "canceled"
          : game.phase === "final"
            ? "final"
            : "live";
  return `Box score: ${away.name} ${joiner} ${home.name}, ${state}`;
}

// ── The words each sport uses ───────────────────────────────────────────

export interface Tally {
  /** What is counted: "Runs", "Goals", "Points", "Sets". */
  noun: string;
  /** The label under each figure. */
  forLabel: string;
  againstLabel: string;
}

export interface SportWords {
  game: string;
  games: string;
  /** Null where a score is not a tally of anything (a fight's round count). */
  tally: Tally | null;
}

const scored = (noun: string): Tally => ({
  noun,
  forLabel: "Scored",
  againstLabel: "Allowed",
});

export function sportWords(sport: Sport | undefined): SportWords {
  switch (sport) {
    case "baseball":
      return { game: "game", games: "games", tally: scored("Runs") };
    case "basketball":
    case "football":
      return { game: "game", games: "games", tally: scored("Points") };
    case "hockey":
      return { game: "game", games: "games", tally: scored("Goals") };
    case "soccer":
      return {
        game: "match",
        games: "matches",
        tally: { noun: "Goals", forLabel: "Scored", againstLabel: "Conceded" },
      };
    case "volleyball":
    case "tennis":
      return {
        game: "match",
        games: "matches",
        tally: { noun: "Sets", forLabel: "Won", againstLabel: "Lost" },
      };
    case "mma":
      return { game: "bout", games: "bouts", tally: null };
    default:
      return { game: "game", games: "games", tally: null };
  }
}

/** "8 games", "1 match". */
export function countOf(count: number, words: SportWords): string {
  return `${count} ${count === 1 ? words.game : words.games}`;
}

const COUNT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** "five" up to ten, digits beyond: counts read as words in a short note. */
export function countWord(n: number): string {
  return Number.isInteger(n) && n >= 0 && n <= 10 ? COUNT_WORDS[n] : String(n);
}
