import type { GameOdds } from "../types";

/** American odds with an explicit sign (+144 / -175); "—" when unpriced. */
export function formatMoneyline(value: number | null): string {
  if (value === null) return "—";
  return value > 0 ? `+${value}` : `${value}`;
}

/** Home point spread with an explicit sign (-1.5 / +3); "PK" at zero. */
export function formatSpread(value: number): string {
  if (value === 0) return "PK";
  return value > 0 ? `+${value}` : `${value}`;
}

/** Whether the provider priced anything worth rendering. */
export function oddsHasContent(odds: GameOdds): boolean {
  return (
    odds.home_win_pct !== null ||
    odds.away_win_pct !== null ||
    odds.home_moneyline !== null ||
    odds.away_moneyline !== null ||
    odds.spread !== null ||
    odds.over_under !== null
  );
}
