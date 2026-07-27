/**
 * eventSpan: the start..end day arithmetic behind the calendar's multi-day
 * tournament bars. The `end` it produces is EXCLUSIVE (FullCalendar's rule
 * for all-day events, and iCalendar's for DTEND), which is the off-by-one
 * this suite pins down — along with the local-vs-UTC day boundary.
 *
 * The suite runs under TZ=America/New_York (see package.json), so a UTC
 * instant before 05:00 belongs to the previous local day.
 */
import { describe, expect, it } from "vitest";

import { eventIdFromSpanId, eventSpan, SPAN_ID_PREFIX } from "./eventSpans";
import type { SportEvent } from "../../types";

function makeEvent(overrides: Partial<SportEvent>): SportEvent {
  return {
    id: "mock:gl-open",
    league_id: "gilded-links",
    sport: "golf",
    name: "The Gilded Open",
    start_time: "2026-07-16T14:00:00Z",
    end_time: "2026-07-19T23:00:00Z",
    phase: "scheduled",
    round_label: "",
    venue: null,
    followed_player_ids: [],
    leaderboard: [],
    ...overrides,
  };
}

describe("eventSpan", () => {
  it("spans start..end with an EXCLUSIVE end day", () => {
    // A Thursday-Sunday major: the bar covers Jul 16-19, so `end` is Jul 20.
    const span = eventSpan(makeEvent({}));

    expect(span.start).toBe("2026-07-16");
    expect(span.end).toBe("2026-07-20");
  });

  it("treats a null end_time as a single day", () => {
    const span = eventSpan(makeEvent({ end_time: null }));

    expect(span.start).toBe("2026-07-16");
    expect(span.end).toBe("2026-07-17");
  });

  it("collapses a single-day event whose end shares the start day", () => {
    const span = eventSpan(
      makeEvent({
        start_time: "2026-07-16T14:00:00Z",
        end_time: "2026-07-16T22:00:00Z",
      }),
    );

    expect(span.start).toBe("2026-07-16");
    expect(span.end).toBe("2026-07-17");
  });

  it("uses local days, not UTC days", () => {
    // 02:00Z is still the previous evening in the viewer's timezone.
    const span = eventSpan(
      makeEvent({
        start_time: "2026-07-16T02:00:00Z",
        end_time: "2026-07-19T02:00:00Z",
      }),
    );

    expect(span.start).toBe("2026-07-15");
    expect(span.end).toBe("2026-07-19");
  });

  it("never runs backwards when end_time predates start_time", () => {
    const span = eventSpan(
      makeEvent({
        start_time: "2026-07-16T14:00:00Z",
        end_time: "2026-07-14T14:00:00Z",
      }),
    );

    expect(span.start).toBe("2026-07-16");
    expect(span.end).toBe("2026-07-17");
  });

  it("rolls the exclusive end over a month and year boundary", () => {
    const span = eventSpan(
      makeEvent({
        start_time: "2026-12-30T17:00:00Z",
        end_time: "2026-12-31T17:00:00Z",
      }),
    );

    expect(span.start).toBe("2026-12-30");
    expect(span.end).toBe("2027-01-01");
  });

  it("prefixes the id so it cannot collide with a game id", () => {
    const span = eventSpan(makeEvent({ id: "mock:gl-open" }));

    expect(span.id).toBe(`${SPAN_ID_PREFIX}mock:gl-open`);
    expect(span.eventId).toBe("mock:gl-open");
  });

  it("marks the title with a sport glyph when there is one", () => {
    // Golf is the only leaderboard sport, so it is the only glyph that can
    // resolve from real data; every other sport takes the bare-name branch.
    expect(eventSpan(makeEvent({ sport: "golf" })).title).toBe(
      "⛳ The Gilded Open",
    );
    expect(eventSpan(makeEvent({ sport: "soccer" })).title).toBe(
      "The Gilded Open",
    );
  });
});

describe("eventIdFromSpanId", () => {
  it("reverses a span id", () => {
    expect(eventIdFromSpanId(eventSpan(makeEvent({})).id)).toBe("mock:gl-open");
  });

  it("returns null for a game id", () => {
    expect(eventIdFromSpanId("mock:vc-live")).toBeNull();
  });
});
