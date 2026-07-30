/**
 * Row logic for the Today view's "On this day" section: which side of a
 * historical final to bold, and the anniversary year for the row's chip.
 *
 * Kept pure (no React) so the score comparison and the year derivation are
 * unit-testable, matching `views/calendar/eventSpans.ts` and
 * `views/calendar/gameSideFollows.ts` — the repo has no component-test
 * harness.
 */

import type { Game } from "../../types";

/** The winning side of a final, or null for a draw / missing scores. */
export type WinningSide = "home" | "away" | null;

/**
 * Which side won, derived from the scores alone — history rows carry no
 * richer result data. A draw or an absent score (defensive: the backend
 * only ships FINAL games here, but `score` is nullable) bolds nobody.
 */
export function winningSide(game: Game): WinningSide {
  const { home, away } = game;
  if (home.score === null || away.score === null) {
    return null;
  }
  if (home.score === away.score) {
    return null;
  }
  return home.score > away.score ? "home" : "away";
}

/**
 * The year for the row's chip, from the game's start in the viewer's local
 * zone (matching how every other timestamp in the app localizes at render
 * time).
 */
export function anniversaryYear(game: Game): number {
  return new Date(game.start_time).getFullYear();
}

/** "3 – 1" (an absent score renders as an em dash). */
export function scoreline(game: Game): string {
  const part = (score: number | null): string =>
    score === null ? "—" : String(score);
  return `${part(game.away.score)} – ${part(game.home.score)}`;
}

/**
 * Render guard for the whole section: does a fetched payload still belong
 * to the day on screen? The query is keyed by the viewer's local day, but
 * the previous day's cached payload survives the midnight rollover until
 * the fresh fetch lands, so the section compares the payload's own `date`
 * stamp against today's local key and hides on a definite mismatch.
 * Either side missing (an older backend omitting `date`) skips the
 * comparison and shows the section — the guard exists to catch a stale
 * day, not to punish a payload that can't be dated.
 */
export function isOnThisDayCurrent(
  payloadDate: string | undefined,
  todayKey: string | undefined,
): boolean {
  if (!payloadDate || !todayKey) {
    return true;
  }
  return payloadDate === todayKey;
}
