/**
 * Calendar spans for leaderboard events (golf tournaments, race weekends).
 *
 * A `SportEvent` runs over DAYS, so the calendar draws it as a bar across
 * its start..end dates rather than as a card at a point in time. The `end`
 * here is EXCLUSIVE, the day AFTER the tournament's last day: the same
 * off-by-one iCalendar's `DTEND` has, so the grid and the `.ics` feed derive
 * a tournament's days from one rule. A null `end_time` is a single-day span.
 *
 * Kept pure (no React) so the date arithmetic, the part that is actually
 * easy to get wrong, is unit-testable; `model.ts` turns a span into the
 * inclusive days and the lanes the grid draws.
 */

import {
  dateKeyInZone,
  localKeyFromDate,
  parseLocalDateKey,
} from "../../lib/time";
import type { Sport, SportEvent } from "../../types";

export interface EventSpan {
  eventId: string;
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

/**
 * All-day span covering the days a leaderboard event runs over.
 *
 * `timeZone` should be the backend's configured zone (`MetaOut.timezone`),
 * NOT the browser's. A tournament's days belong to the tournament, and the
 * `.ics` feed already names them in that zone — deriving them from the
 * viewer's zone instead puts the grid a day off from the subscribed
 * calendar for anyone whose machine disagrees with the server. Omitted
 * falls back to browser-local.
 */
export function eventSpan(event: SportEvent, timeZone?: string): EventSpan {
  const start = dateKeyInZone(event.start_time, timeZone);
  const last =
    event.end_time !== null ? dateKeyInZone(event.end_time, timeZone) : start;
  return {
    eventId: event.id,
    sport: event.sport,
    start,
    // Zero-padded date keys compare lexicographically, so this also
    // absorbs an end_time that predates the start (bad provider data).
    end: nextDayKey(last > start ? last : start),
  };
}
