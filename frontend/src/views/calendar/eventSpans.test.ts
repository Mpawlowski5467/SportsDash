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
    // Golf and racing are the leaderboard sports, so theirs are the only
    // glyphs that can resolve from real data; every other sport takes the
    // bare-name branch.
    expect(eventSpan(makeEvent({ sport: "golf" })).title).toBe(
      "⛳ The Gilded Open",
    );
    expect(eventSpan(makeEvent({ sport: "racing" })).title).toBe(
      "🏁 The Gilded Open",
    );
    expect(eventSpan(makeEvent({ sport: "soccer" })).title).toBe(
      "The Gilded Open",
    );
  });
});

describe("eventSpan in the server's timezone", () => {
  // The suite runs with TZ=America/New_York (see package.json), so a
  // Chicago-configured server is genuinely a different calendar day here —
  // which is the whole bug: the .ics feed dates a tournament in the SERVER's
  // zone, and the grid used to date it in the viewer's.
  it("dates a span in the given zone, not the viewer's", () => {
    // 04:00Z on the 23rd is Jul 23 in New York but still Jul 22 in Chicago.
    const event = makeEvent({
      start_time: "2026-07-23T04:00:00Z",
      end_time: "2026-07-27T02:00:00Z",
    });

    expect(eventSpan(event, "America/Chicago").start).toBe("2026-07-22");
    expect(eventSpan(event, "America/New_York").start).toBe("2026-07-23");
  });

  it("matches the .ics feed for a real tournament", () => {
    // The 3M Open, exactly as ESPN served it: Thu 23 - Sun 26 July 2026.
    // The backend's feed emits DTSTART 20260723 / DTEND 20260727 for this,
    // and the grid must agree rather than drawing it a day early.
    const span = eventSpan(
      makeEvent({
        start_time: "2026-07-23T04:00:00Z",
        end_time: "2026-07-26T23:00:00Z",
      }),
      "America/New_York",
    );

    expect(span.start).toBe("2026-07-23");
    expect(span.end).toBe("2026-07-27"); // exclusive, per DTEND
  });

  it("falls back to the viewer's zone when the server's is unknown", () => {
    // /api/meta not loaded yet, or an unusable zone string: render something
    // rather than nothing.
    const event = makeEvent({ start_time: "2026-07-16T14:00:00Z" });

    expect(eventSpan(event, undefined).start).toBe("2026-07-16");
    expect(eventSpan(event, "Not/AZone").start).toBe("2026-07-16");
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
