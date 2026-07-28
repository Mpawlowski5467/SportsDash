/**
 * Which followed "teams" can ever turn up on a side of a game.
 *
 * Individual sports are followed as athlete-as-team rows, and one kind of
 * them — a leaderboard-sport follow (a golfer) — competes in TOURNAMENTS,
 * which the backend models as `Event`s belonging to no team. Two calendar
 * controls sitting next to each other both have to know that:
 * - the Subscribe menu, because a `?team_id=` .ics feed is that team's GAMES,
 *   so a golfer's would come back empty forever; and
 * - the team filter, because a golfer matches neither side of any game (and
 *   spans are hidden while filtering), so the grid would sit empty forever.
 * They ask this one predicate rather than each spelling out the rule, which
 * is how they came to disagree about whether a golfer is selectable.
 *
 * Kept pure (no React) so the rule is unit-testable, matching
 * `calendar/eventSpans.ts` and `views/map/travel.ts` — the repo has no
 * component-test harness.
 */

import type { League, Sport, Team } from "../../types";

/**
 * Sports modeled as leaderboard `Event`s rather than two-sided `Game`s —
 * mirrors the backend's `LEADERBOARD_SPORTS`, which is
 * `frozenset({Sport.GOLF})`. Tennis/MMA athletes are athlete-as-team too but
 * their matches ARE games, so they are deliberately NOT listed here.
 */
export const LEADERBOARD_SPORTS: ReadonlySet<Sport> = new Set<Sport>(["golf"]);

/**
 * True when `team` can appear on a side of a game — everything except a
 * leaderboard-sport follow.
 *
 * A team whose league isn't in `leaguesById` yet counts as game-side: the
 * sport is unknown while `/teams` is in flight, and both callers are lists a
 * user reads and picks from, so a row that works beats one flickering out
 * from under them mid-choice.
 */
export function appearsOnGameSide(
  team: Team,
  leaguesById: Record<string, League>,
): boolean {
  const sport = leaguesById[team.league_id]?.sport;
  return sport === undefined || !LEADERBOARD_SPORTS.has(sport);
}
