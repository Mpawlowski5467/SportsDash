import { formatMoneyline } from "../lib/odds";
import { detectSignal, type UpsetSignal } from "../lib/whyWatch";
import { useMemo } from "react";

import { useGameOdds } from "../hooks";
import type { Game } from "../types";

/**
 * A compact strip across the top of the matchup previews that flags upcoming
 * games where the model and the market disagree — i.e. the betting underdog is
 * the model's favorite. Odds are fetched in one batch for all the visible
 * games; games without both a win-probability and a moneyline (soccer often
 * has neither) simply never flag, so the strip is frequently empty and shows a
 * quiet placeholder rather than erroring.
 */
export default function UpsetRadar({ games }: { games: Game[] }) {
  const ids = useMemo(() => games.map((g) => g.id), [games]);
  const oddsQuery = useGameOdds(ids);
  const oddsById = oddsQuery.data;

  const signals = useMemo(() => {
    if (oddsById === undefined) return [];
    const out: UpsetSignal[] = [];
    for (const game of games) {
      const odds = oddsById[game.id];
      if (odds === undefined) continue;
      const signal = detectSignal(game, odds);
      if (signal !== null) out.push(signal);
    }
    return out;
  }, [games, oddsById]);

  return (
    <section>
      <h2 className="sd-eyebrow sd-rule mb-3 flex items-center gap-2 pb-2.5 text-zinc-500">
        <svg
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-3.5 w-3.5 text-amber-400"
          aria-hidden="true"
        >
          <circle cx="10" cy="10" r="7.5" />
          <circle cx="10" cy="10" r="3.5" />
          <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2" />
        </svg>
        Upset radar
      </h2>

      {signals.length === 0 ? (
        <p className="rounded-lg border border-dashed border-zinc-800 px-3 py-2.5 text-xs text-zinc-500">
          {oddsQuery.isLoading
            ? "Scanning the slate for upset signals…"
            : "No upset signals right now."}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {signals.map((signal) => (
            <li
              key={signal.gameId}
              className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm"
            >
              <span className="text-amber-400" aria-hidden="true">
                ▲
              </span>
              <span className="text-zinc-300">
                Model likes{" "}
                <span className="font-semibold text-zinc-100">
                  {signal.underdogAbbr}
                </span>{" "}
                <span className="tabular-nums text-amber-400">
                  ({signal.winPct}%)
                </span>{" "}
                — line has them at{" "}
                <span className="font-semibold tabular-nums text-zinc-100">
                  {formatMoneyline(signal.moneyline)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
