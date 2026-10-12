import type { League, Sport } from "../types";
import type { SelectOption } from "../components/Select";

/**
 * Season-archive support: espn team sports only. Tennis "standings" are
 * a rolling tour ranking, MMA has no table, golf and racing are
 * leaderboard sports, and TheSportsDB (volleyball) has no season archive
 * endpoints.
 */
const NO_ARCHIVE_SPORTS = new Set(["tennis", "mma", "golf", "racing"]);

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

// ── What a provider's season string says ────────────────────────────────

/** "2026-27" from "2026-27 English Premier League"; null without a leading year. */
export function seasonToken(label: string | undefined): string | null {
  if (label === undefined) return null;
  const match = /^\d{4}(?:[-/]\d{2,4})?/.exec(label.trim());
  return match === null ? null : match[0];
}

/**
 * The year keys the season a table is labelled with may be filed under,
 * most likely first. The provider keys a season by one year, and which one
 * depends on the sport: a soccer season by the year it starts in ("2026-27"
 * is 2026), a basketball or hockey season by the year it ends in. A label
 * with one year has one key. With no readable label the calendar year is
 * the only guess there is.
 */
export function seasonKeys(
  label: string | undefined,
  sport: Sport | undefined,
  thisYear: number,
): number[] {
  const token = seasonToken(label);
  if (token === null) return [thisYear];
  const [first, second] = token.split(/[-/]/);
  const start = Number(first);
  if (second === undefined) return [start];
  let end = Number(second);
  if (second.length < 4) {
    end = Math.floor(start / 100) * 100 + end;
    if (end < start) end += 100;
  }
  if (end === start) return [start];
  return sport === "soccer" ? [start, end] : [end, start];
}

/**
 * "2026 season" from a provider's season string. Providers ship the season
 * as a sentence ("2026-27 English Premier League") that restates the league
 * it sits beside; only its leading year or year range is the season. A
 * string with no leading year is returned as it is, and nothing gives null.
 */
export function seasonTitle(season: string | null | undefined): string | null {
  const text = (season ?? "").trim();
  if (text === "") return null;
  const match = /^\d{4}(?:[-/–]\d{2,4})?/.exec(text);
  return match === null ? text : `${match[0]} season`;
}

/**
 * The season picker's options, relabelled with what is known. The live
 * option reads as its season ("2026 season") once the live table has said
 * which one that is; a past year reads as its key until its own table has
 * loaded and can say more ("2025-26 season").
 *
 * For a league whose season is one calendar year, the newest archive key
 * is the season in progress again. It would be listed under the same name
 * as the live option, so it is left out (unless it is what is picked).
 */
export function seasonChoices(
  base: readonly SelectOption[],
  currentId: string,
  selected: string,
  liveTitle: string | null,
  shownTitle: string | null,
): SelectOption[] {
  const choices: SelectOption[] = [];
  for (const option of base) {
    if (option.id === currentId) {
      choices.push({ id: option.id, label: liveTitle ?? option.label });
      continue;
    }
    const resolved = option.id === selected ? shownTitle : null;
    const label = resolved ?? `${option.label} season`;
    if (label === liveTitle && option.id !== selected) continue;
    choices.push({ id: option.id, label });
  }
  return choices;
}
