import type { Game } from "../types";
import { formatTime } from "../lib/time";

/**
 * Phase-appropriate status indicator for a game.
 *
 * - scheduled            -> local start time, plain zinc text
 * - in_progress + break  -> amber pill with the period label (fallback "HT")
 * - in_progress          -> red pill with pulsing live dot, period label, clock
 * - final                -> zinc pill "FINAL"
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
    return (
      <span className="sd-status inline-flex items-center rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-300">
        FINAL
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
