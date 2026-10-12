/**
 * When a score figure may pulse. Pure, so the rule can be unit-tested; the
 * component that applies it is `components/bs/ScoreFigure`.
 */

/**
 * How long the pulse runs. `sd-score-flash` in index.css is the same
 * length; the class comes off a moment after it ends.
 */
export const SCORE_FLASH_MS = 700;

/** A figure as it stands on the page: whose it is, and what it reads. */
export interface DrawnScore {
  /** What the figure counts for: the game's id. */
  of: string;
  /** The figure as drawn; null draws nothing. */
  value: number | string | null;
}

/**
 * Whether the figure went from one score to another for the SAME game.
 * False when nothing changed, when the place on the page was handed
 * another game (its score is not a change of this one's), and when a
 * figure appears or goes away (a game starting has not "changed" its
 * score from nothing).
 *
 * This is half of the rule. The other half needs the page: the figure
 * must be on screen at that moment, which `ScoreFigure` checks itself.
 */
export function scoreMoved(before: DrawnScore, after: DrawnScore): boolean {
  return (
    before.of === after.of &&
    before.value !== null &&
    after.value !== null &&
    before.value !== after.value
  );
}
