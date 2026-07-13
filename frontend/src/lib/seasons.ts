import type { League } from "../types";
import type { SelectOption } from "../components/Select";

/**
 * Season-archive support: espn team sports only. Tennis "standings" are
 * a rolling tour ranking, MMA has no table, golf is a leaderboard sport,
 * and TheSportsDB (volleyball) has no season archive endpoints.
 */
const NO_ARCHIVE_SPORTS = new Set(["tennis", "mma", "golf"]);

export function supportsArchives(league: League | undefined): boolean {
  return (
    league !== undefined &&
    league.provider === "espn" &&
    !NO_ARCHIVE_SPORTS.has(league.sport)
  );
}

export const CURRENT_SEASON = "current";

/**
 * Season picker options: the live snapshot first, then recent season
 * keys (ESPN keys cross-year seasons by their ENDING year, so picking
 * "2020" on an NBA league returns the 2019-20 table).
 */
export function seasonOptions(count = 15): SelectOption[] {
  const now = new Date().getFullYear();
  const options: SelectOption[] = [
    { id: CURRENT_SEASON, label: "Current season" },
  ];
  for (let year = now; year > now - count; year -= 1) {
    options.push({ id: String(year), label: String(year) });
  }
  return options;
}
