import { formatMoneyline } from "../lib/odds";
import { abbrev } from "../lib/labels";
import { useMemo } from "react";

import { useGameOdds } from "../hooks";
import type { Game, GameOdds } from "../types";

/**
 * A flagged "upset signal": a game where the betting market's underdog (the
 * side with the higher / more-positive moneyline) is also the model's favorite
 * (its win probability is above 50%). The two views of the same game disagree,
 * which is exactly the contrarian spot a previews browser wants surfaced.
 */
interface UpsetSignal {
  gameId: string;
  /** The market underdog the model likes — its short code, win %, and line. */
  underdogAbbr: string;
  winPct: number;
  moneyline: number;
}

/** Up-to-three-char abbreviation fallback, mirroring the modal helper. */
/** American odds with an explicit sign (+144 / -175). */
/**
 * Decide whether a game is an upset signal. Needs BOTH win probabilities AND
 * both moneylines: the market underdog is the side with the higher moneyline
 * (in American odds, more positive = longer odds = underdog), and it's flagged
 * only when that same side is the model favorite (its win % > 50). Returns null
 * when the data is incomplete or the two views agree.
 */
function detectSignal(game: Game, odds: GameOdds): UpsetSignal | null {
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
      <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
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
