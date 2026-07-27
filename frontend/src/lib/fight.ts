/**
 * Method-of-victory helpers for combat sports (MMA).
 *
 * A finished bout's score is a round count ("1-0"), which says almost
 * nothing — HOW it ended is the result. These are the pure functions
 * behind the final badge, the card's round chip and the detail modal's
 * Result block, kept out of the components so they are unit-testable.
 */
import type { FightMethod, FightResult, Game } from "../types";

/** Display wording for each normalized method of victory. */
const FIGHT_METHOD_LABELS: Record<FightMethod, string> = {
  ko: "KO/TKO",
  submission: "Submission",
  decision: "Decision",
  disqualification: "DQ",
  no_contest: "No Contest",
  draw: "Draw",
};

/**
 * Title-case a method the map doesn't know: "technical_draw" -> "Technical
 * Draw". Anything blank (or not a string at all — this is parsed JSON, the
 * types are a promise the network doesn't keep) yields "", which every
 * caller renders as no wording rather than an empty label.
 */
function humanizeMethod(method: unknown): string {
  if (typeof method !== "string") return "";
  return method
    .split(/[\s_-]+/)
    .filter((word) => word !== "")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Display wording for a method of victory.
 *
 * The exhaustive `Record<FightMethod, string>` keeps the mapping honest at
 * compile time, but `FightMethod` is an assumption about a LIVE upstream
 * feed, not a guarantee: ESPN can classify a bout as something the union
 * has never heard of, and the backend passes its own new enum values
 * straight through. An index miss must therefore degrade to a readable
 * label instead of `undefined` — the previous code called `.toUpperCase()`
 * on the result, so a single unknown method took the whole card down.
 */
export function fightMethodLabel(method: FightMethod): string {
  const known: string | undefined = FIGHT_METHOD_LABELS[method];
  return known ?? humanizeMethod(method);
}

/**
 * A finished fight's method of victory, or null when there isn't one.
 *
 * Only MMA bouts carry it, and only once they're over — a round count is a
 * meaningless "score", so this is the actual result. Written to tolerate a
 * payload that predates the field.
 */
export function fightResultOf(game: Game): FightResult | null {
  return game.phase === "final" ? (game.fight_result ?? null) : null;
}

/** `"R2 4:21"` / `"R3"` — when the bout ended, or null if unreported. */
export function fightRoundLabel(result: FightResult): string | null {
  const round = result.round !== null ? `R${result.round}` : null;
  if (round !== null && result.clock !== null) return `${round} ${result.clock}`;
  return round ?? result.clock;
}

/** The whole result on one line: `"KO/TKO · Punches · R2 4:21"`. */
export function fightResultSummary(result: FightResult): string {
  return [fightMethodLabel(result.method), result.detail, fightRoundLabel(result)]
    .filter((part): part is string => part !== null && part !== "")
    .join(" · ");
}
