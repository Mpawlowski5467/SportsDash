/**
 * "Why watch" scoring for the game cards.
 *
 * Two pure pieces live here, out of the components so they are
 * unit-testable:
 *
 * - `detectSignal` — the upset radar's contrarian read (market underdog
 *   that the model favors), shared by UpsetRadar and the card chip.
 * - `watchability` — a small deterministic score over signals the card
 *   already has in hand (the `Game` plus the batched odds); no fetches.
 *
 * The chip is meant to be RARE: a game must carry at least one primary
 * signal (tight late / upset watch / toss-up) to show at all, so a slate of
 * ordinary games renders no fire at all.
 */
import { abbrev } from "./labels";
import type { Game, GameOdds, Sport } from "../types";

/**
 * A flagged "upset signal": a game where the betting market's underdog (the
 * side with the higher / more-positive moneyline) is also the model's favorite
 * (its win probability is above 50%). The two views of the same game disagree,
 * which is exactly the contrarian spot a previews browser wants surfaced.
 */
export interface UpsetSignal {
  gameId: string;
  /** The market underdog the model likes — its short code, win %, and line. */
  underdogAbbr: string;
  winPct: number;
  moneyline: number;
}

/**
 * Decide whether a game is an upset signal. Needs BOTH win probabilities AND
 * both moneylines: the market underdog is the side with the higher moneyline
 * (in American odds, more positive = longer odds = underdog), and it's flagged
 * only when that same side is the model favorite (its win % > 50). Returns null
 * when the data is incomplete or the two views agree.
 */
export function detectSignal(game: Game, odds: GameOdds): UpsetSignal | null {
  const { home_win_pct, away_win_pct, home_moneyline, away_moneyline } = odds;
  if (
    home_win_pct === null ||
    away_win_pct === null ||
    home_moneyline === null ||
    away_moneyline === null
  ) {
    return null;
  }

  // Market underdog = the side priced with the longer (more positive) line.
  const homeIsUnderdog = home_moneyline > away_moneyline;
  const underdogSide = homeIsUnderdog ? game.home : game.away;
  const underdogWinPct = homeIsUnderdog ? home_win_pct : away_win_pct;
  const underdogMoneyline = homeIsUnderdog ? home_moneyline : away_moneyline;

  // Only a signal when the model favors the side the market fades.
  if (underdogWinPct <= 50) {
    return null;
  }

  return {
    gameId: game.id,
    underdogAbbr: underdogSide.abbreviation ?? abbrev(underdogSide.name),
    winPct: Math.round(underdogWinPct),
    moneyline: underdogMoneyline,
  };
}

/** The card chip: a score (for debugging/tuning) and one short phrase. */
export interface WhyWatch {
  score: number;
  reason: string;
}

/**
 * The weights. Primary signals each clear the threshold on their own; the
 * both-sides-followed bonus never does — a game you happen to follow twice
 * still needs a reason to be worth flagging.
 *
 * - Tight late (60): a live one-score game in the closing period — the
 *   strongest pull there is, it's happening right now.
 * - Upset watch (45): the market's underdog is the model's favorite.
 * - Toss-up (35): the model can't split the sides (win prob within
 *   TOSSUP_MAX_GAP of 50/50).
 * - Both sides followed (+15): a bonus on top of a primary signal.
 */
const TIGHT_LATE_SCORE = 60;
const UPSET_SCORE = 45;
const TOSSUP_SCORE = 35;
const BOTH_FOLLOWED_BONUS = 15;
const WATCHABILITY_THRESHOLD = 35;

/** |home_win_pct − 50| this small reads as a genuine coin flip. */
const TOSSUP_MAX_GAP = 5;

/**
 * The period at which a game counts as "late" — the final regulation
 * period/half/set, or the 8th inning on for baseball. Sports without an
 * entry (clocked in unfamiliar units) fall back to a conservative 4, which
 * in practice means they never flag early.
 */
const LATE_PERIOD: Partial<Record<Sport, number>> = {
  basketball: 4,
  football: 4,
  hockey: 3,
  soccer: 2,
  baseball: 8,
  volleyball: 4, // a 4th set means the match is on the line (best-of-5)
};
const DEFAULT_LATE_PERIOD = 4;

/**
 * "One score" is sport-specific: a 3 covers a field goal or a deep three,
 * one goal decides hockey and soccer. Everything else gets a conservative 2.
 */
const CLOSE_MARGIN: Partial<Record<Sport, number>> = {
  basketball: 3,
  football: 3,
  hockey: 1,
  soccer: 1,
};
const DEFAULT_CLOSE_MARGIN = 2;

/** A live game within one score in (or past) the final regulation period. */
function isTightLate(game: Game): boolean {
  if (game.phase !== "in_progress") return false;
  const home = game.home.score;
  const away = game.away.score;
  if (home === null || away === null) return false;
  const margin = Math.abs(home - away);
  return (
    margin <= (CLOSE_MARGIN[game.sport] ?? DEFAULT_CLOSE_MARGIN) &&
    game.period >= (LATE_PERIOD[game.sport] ?? DEFAULT_LATE_PERIOD)
  );
}

/**
 * Score a game's watch appeal from data the card already holds. Returns null
 * whenever the chip shouldn't show: a decided (or dead) game, or a score
 * below the threshold. The reason is the strongest matched signal, as one
 * short phrase ("Tight late" / "Upset watch" / "Toss-up").
 */
export function watchability(
  game: Game,
  odds: GameOdds | null | undefined,
): WhyWatch | null {
  // Callers gate the chip by phase already, but the scorer stays honest on
  // its own: nothing left to watch for in a final / postponed / canceled.
  if (game.phase !== "scheduled" && game.phase !== "in_progress") {
    return null;
  }

  let score = 0;
  // Signals are tallied in descending weight, so the first match names the
  // chip and the rest only add score.
  let reason: string | null = null;

  if (isTightLate(game)) {
    score += TIGHT_LATE_SCORE;
    reason = "Tight late";
  }
  if (odds != null && detectSignal(game, odds) !== null) {
    score += UPSET_SCORE;
    reason ??= "Upset watch";
  }
  if (
    odds != null &&
    odds.home_win_pct !== null &&
    odds.away_win_pct !== null &&
    Math.abs(odds.home_win_pct - 50) <= TOSSUP_MAX_GAP
  ) {
    score += TOSSUP_SCORE;
    reason ??= "Toss-up";
  }
  if (game.followed_team_ids.length >= 2) {
    score += BOTH_FOLLOWED_BONUS;
  }

  if (reason === null || score < WATCHABILITY_THRESHOLD) {
    return null;
  }
  return { score, reason };
}
