/**
 * Calendar spans for leaderboard events (golf tournaments).
 *
 * A `SportEvent` runs over DAYS, so the calendar draws it as an all-day bar
 * across its start..end dates rather than a point in time. FullCalendar's
 * `end` for an all-day event is EXCLUSIVE — the same off-by-one iCalendar's
 * `DTEND` has — so the value handed to it is the day AFTER the tournament's
 * last day. A null `end_time` is a single-day span.
 *
 * Kept pure (no React, no FullCalendar) so the date arithmetic — the part
 * that is actually easy to get wrong — is unit-testable; the repo has no
 * component-test harness, matching `views/map/travel.ts`.
 */

import {
  localDateKey,
  localKeyFromDate,
  parseLocalDateKey,
} from "../../lib/time";
import type { Sport, SportEvent } from "../../types";

/**
 * Prefix that keeps a span's calendar id from colliding with a game id —
 * both are provider-scoped strings and the grid holds them side by side.
 */
export const SPAN_ID_PREFIX = "event:";

/**
 * Leading glyph so a span reads as a tournament at a glance.
 *
 * Golf is the backend's only `LEADERBOARD_SPORTS` member, so it is the only
 * key that can resolve today — a tennis match is a two-sided `Game` (with a
 * `series` label), never an `Event`, so no tennis entry could ever be hit.
 * The map keeps the per-sport shape for whichever leaderboard sport lands
 * next; until then the `undefined` fallback in `eventSpan` covers the rest.
 */
const SPORT_GLYPH: Partial<Record<Sport, string>> = {
  golf: "⛳",
};

export interface EventSpan {
  /** Calendar-scoped id (prefixed); pass to `eventIdFromSpanId` to reverse. */
  id: string;
  eventId: string;
  title: string;
  sport: Sport;
  /** Local "YYYY-MM-DD", inclusive. */
  start: string;
  /** Local "YYYY-MM-DD", EXCLUSIVE — the day after the last day. */
  end: string;
}

/** The local day after `key` ("2026-12-31" -> "2027-01-01"). */
function nextDayKey(key: string): string {
  const day = parseLocalDateKey(key);
  day.setDate(day.getDate() + 1);
  return localKeyFromDate(day);
}

/** All-day span covering the days a leaderboard event runs over. */
export function eventSpan(event: SportEvent): EventSpan {
  const start = localDateKey(event.start_time);
  const last =
    event.end_time !== null ? localDateKey(event.end_time) : start;
  const glyph = SPORT_GLYPH[event.sport];
  return {
    id: SPAN_ID_PREFIX + event.id,
    eventId: event.id,
    title: glyph !== undefined ? `${glyph} ${event.name}` : event.name,
    sport: event.sport,
    start,
    // Zero-padded date keys compare lexicographically, so this also
    // absorbs an end_time that predates the start (bad provider data).
    end: nextDayKey(last > start ? last : start),
  };
}

/** The event id behind a span id, or null when the id is a game's. */
export function eventIdFromSpanId(spanId: string): string | null {
  return spanId.startsWith(SPAN_ID_PREFIX)
    ? spanId.slice(SPAN_ID_PREFIX.length)
    : null;
}
