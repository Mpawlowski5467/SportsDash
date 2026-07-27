import type { Game } from "../types";
import { formatTime } from "../lib/time";
import {
  fightMethodLabel,
  fightResultOf,
  fightRoundLabel,
  fightResultSummary,
} from "../lib/fight";

// The fight helpers live in lib/fight.ts (pure, unit-tested there, like
// map/travel.ts and calendar/eventSpans.ts); they are re-exported here
// because this badge is where the rest of the app already imports them from.
export { fightMethodLabel, fightResultOf, fightRoundLabel, fightResultSummary };

/**
 * Phase-appropriate status indicator for a game.
 *
 * - scheduled            -> local start time, plain zinc text
 * - in_progress + break  -> amber pill with the period label (fallback "HT")
 * - in_progress          -> red pill with pulsing live dot, period label, clock
 * - final                -> zinc pill "FINAL" (plus the method, for a fight)
 * - postponed/canceled   -> muted zinc pill "PPD"/"CANC"
 */
export default function StatusBadge({ game }: { game: Game }) {
  if (game.phase === "scheduled") {
    return (
      <span className="sd-status text-xs font-medium text-zinc-300 tabular-nums">
        {formatTime(game.start_time)}
      </span>
    );
  }

  if (game.phase === "in_progress" && game.is_intermission) {
    return (
      <span className="sd-status inline-flex items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-400">
        {game.period_label || "HT"}
      </span>
    );
  }

  if (game.phase === "in_progress") {
    return (
      <span className="sd-status inline-flex items-center gap-1.5 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">
        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-red-500" />
        </span>
        <span className="uppercase tracking-wide">{game.period_label || "LIVE"}</span>
        {game.clock !== null && <span className="tabular-nums">{game.clock}</span>}
      </span>
    );
  }

  if (game.phase === "final") {
    // A finished fight's score is a round count, so the badge carries how it
    // actually ended; every other sport keeps the plain "FINAL" — and so does
    // a bout whose method has no wording at all (an unusable value upstream).
    const fight = fightResultOf(game);
    const method = fight !== null ? fightMethodLabel(fight.method).toUpperCase() : "";
    return (
      <span
        className="sd-status inline-flex items-center rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-300"
        title={fight !== null ? fightResultSummary(fight) : undefined}
      >
        {method !== "" ? `FINAL · ${method}` : "FINAL"}
      </span>
    );
  }

  // postponed | canceled — deliberately muted, the game isn't happening.
  return (
    <span className="sd-status inline-flex items-center rounded-full bg-zinc-800/50 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-600">
      {game.phase === "postponed" ? "PPD" : "CANC"}
    </span>
  );
}
