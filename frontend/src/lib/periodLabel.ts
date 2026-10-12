import type { Game } from "../types";

const HALF: Record<string, { short: string; long: string }> = {
  top: { short: "Top", long: "Top" },
  bot: { short: "Bot", long: "Bottom" },
  mid: { short: "Mid", long: "Middle" },
  end: { short: "End", long: "End" },
};

/** 1st, 2nd, 3rd, 4th, 11th, 21st. */
export function ordinal(n: number): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  const suffix = ["th", "st", "nd", "rd"][n % 10] ?? "th";
  return `${n}${suffix}`;
}

/**
 * A live game's period as a scorecard writes it. The backend sends baseball
 * as "Top 5" / "Bot 5" / "Mid 5" / "End 5"; that becomes "Bot 5th" in a
 * tight spot and "Bottom 5th" where there is room. Every other sport's
 * label ("Q4", "2nd Half", "Set 3") is already how it is said and is
 * returned as sent.
 */
export function periodLabel(game: Game, form: "short" | "long"): string {
  const label = game.period_label;
  if (game.sport !== "baseball") {
    // During a break the backend still sends the period just played ("Q2",
    // "1st Half"), which reads as play going on. Baseball needs nothing:
    // "Mid 5" and "End 5" already say it.
    if (!game.is_intermission) return label;
    const half =
      game.sport === "soccer" ||
      ((game.sport === "basketball" || game.sport === "football") &&
        game.period === 2);
    if (half || label === "") return form === "short" ? "HT" : "Half time";
    return form === "short" ? `End ${label}` : `End of ${label}`;
  }
  const match = /^(top|bot|mid|end)\w*\s+(\d+)$/i.exec(label.trim());
  if (match === null) return label;
  const half = HALF[match[1].toLowerCase()];
  return `${half[form]} ${ordinal(Number(match[2]))}`;
}
