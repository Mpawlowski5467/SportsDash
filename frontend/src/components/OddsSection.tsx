import type { Game, GameOdds } from "../types";
import { sideLabel } from "../lib/labels";
import { formatMoneyline, formatSpread } from "../lib/odds";

/**
 * Win-probability + betting line for a pre-game / live matchup — shared by
 * the game detail modal and the Matchup preview (which used to carry a
 * byte-identical copy). The win-probability split bar reads away (amber,
 * left) / home (sky, right); the moneyline / spread / total rows show only
 * what the provider priced, attributed to the sportsbook. Soccer usually
 * has no line, so this whole section is simply absent there.
 */
export default function OddsSection({
  odds,
  game,
}: {
  odds: GameOdds;
  game: Game;
}) {
  const awayLabel = sideLabel(game.away);
  const homeLabel = sideLabel(game.home);
  const awayPct = odds.away_win_pct;
  const homePct = odds.home_win_pct;
  const hasProb = awayPct !== null && homePct !== null;
  const hasLine =
    odds.home_moneyline !== null ||
    odds.away_moneyline !== null ||
    odds.spread !== null ||
    odds.over_under !== null;

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Win probability &amp; odds
      </h3>
      <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-3">
        {hasProb && (
          <div className="space-y-1">
            <div className="flex items-baseline justify-between text-sm tabular-nums">
              <span className="font-semibold text-amber-400">
                {awayLabel} {Math.round(awayPct!)}%
              </span>
              <span className="text-[11px] uppercase tracking-wide text-zinc-500">
                Win probability
              </span>
              <span className="font-semibold text-sky-400">
                {Math.round(homePct!)}% {homeLabel}
              </span>
            </div>
            <div className="flex h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="bg-amber-500/70"
                style={{ width: `${awayPct}%` }}
              />
              <div className="flex-1 bg-sky-500/60" />
            </div>
          </div>
        )}

        {hasLine && (
          <dl className="space-y-1.5 text-sm">
            {(odds.away_moneyline !== null || odds.home_moneyline !== null) && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Moneyline
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  {awayLabel} {formatMoneyline(odds.away_moneyline)}
                  <span className="px-1.5 text-zinc-600">·</span>
                  {homeLabel} {formatMoneyline(odds.home_moneyline)}
                </dd>
              </div>
            )}
            {odds.spread !== null && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Spread
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  {homeLabel} {formatSpread(odds.spread)}
                </dd>
              </div>
            )}
            {odds.over_under !== null && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Total
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  O/U {odds.over_under}
                </dd>
              </div>
            )}
          </dl>
        )}

        {odds.provider !== null && (
          <p className="text-[11px] text-zinc-500">via {odds.provider}</p>
        )}
      </div>
    </div>
  );
}
