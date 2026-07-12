/**
 * The day-bucketing helpers are the highest-risk pure logic in the app: a
 * regression silently puts games on the wrong calendar day in Today and
 * Calendar. TZ is pinned to America/New_York by the `test` script so the
 * local-vs-UTC assertions are deterministic.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  localDateKey,
  localDayOffset,
  localKeyFromDate,
  parseLocalDateKey,
  relativeTime,
} from "./time";

afterEach(() => {
  vi.useRealTimers();
});

describe("localDateKey", () => {
  it("keeps an ET evening game on its local day (no UTC day-shift)", () => {
    // 01:30 UTC Jan 2 is 20:30 ET Jan 1 — the game belongs to Jan 1.
    expect(localDateKey("2026-01-02T01:30:00Z")).toBe("2026-01-01");
  });

  it("keeps an afternoon game on the same local day", () => {
    expect(localDateKey("2026-01-02T18:00:00Z")).toBe("2026-01-02");
  });

  it("handles the DST spring-forward day", () => {
    // 06:59 UTC on 2026-03-08 is 01:59 EST; 08:00 UTC is 04:00 EDT — both
    // are still March 8 locally.
    expect(localDateKey("2026-03-08T06:59:00Z")).toBe("2026-03-08");
    expect(localDateKey("2026-03-08T08:00:00Z")).toBe("2026-03-08");
  });
});

describe("parseLocalDateKey", () => {
  it("parses at LOCAL midnight, not UTC midnight", () => {
    const d = parseLocalDateKey("2026-06-15");
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(5);
    expect(d.getDate()).toBe(15);
    expect(d.getHours()).toBe(0);
  });

  it("round-trips with localKeyFromDate", () => {
    expect(localKeyFromDate(parseLocalDateKey("2026-02-01"))).toBe(
      "2026-02-01",
    );
  });
});

describe("localDayOffset", () => {
  it("offsets across a month boundary in local time", () => {
    // 03:00 UTC July 1 = 23:00 ET June 30, so "today" is June 30 locally.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-01T03:00:00Z"));
    expect(localDayOffset(0)).toBe("2026-06-30");
    expect(localDayOffset(1)).toBe("2026-07-01");
    expect(localDayOffset(-30)).toBe("2026-05-31");
  });
});

describe("relativeTime", () => {
  it("buckets by age and falls back to a short date", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-12T12:00:00Z"));
    expect(relativeTime("2026-07-12T11:59:40Z")).toBe("just now");
    expect(relativeTime("2026-07-12T11:48:00Z")).toBe("12m ago");
    expect(relativeTime("2026-07-12T09:00:00Z")).toBe("3h ago");
    expect(relativeTime("2026-07-10T12:00:00Z")).toBe("2d ago");
    expect(relativeTime("2026-06-21T12:00:00Z")).toBe("3w ago");
  });

  it("returns 'recently' for an unparseable timestamp", () => {
    expect(relativeTime("not-a-date")).toBe("recently");
  });
});
