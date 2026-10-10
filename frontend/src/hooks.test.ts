/**
 * The polling policies.
 *
 * Today: 30s while a game is live, 60s on a game day, 5 minutes otherwise.
 * TZ pinned to America/New_York by the test script.
 *
 * Setup status: every 5s while the backend reports a refresh in flight,
 * otherwise never — it is how the app learns that the refresh a save kicked
 * has finished.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { statusRefetchInterval, todayRefetchInterval } from "./hooks";
import type { Game, SetupStatus, TodayResponse } from "./types";

function makeGame(overrides: Partial<Game>): Game {
  return {
    id: "test:g1",
    league_id: "test-league",
    sport: "basketball",
    home: { team_id: "h", name: "Harbor Herons", abbreviation: "HH", logo_url: null, color: null },
    away: { team_id: "a", name: "Alpine Auks", abbreviation: "AA", logo_url: null, color: null },
    start_time: "2026-07-12T23:00:00Z",
    venue: null,
    phase: "scheduled",
    state: null,
    followed_team_ids: [],
    series: null,
    ...overrides,
  } as Game;
}

function response(games: Game[]): TodayResponse {
  return { date: "2026-07-12", timezone: "America/New_York", games, events: [] };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("todayRefetchInterval", () => {
  it("polls every 5 minutes with no data", () => {
    expect(todayRefetchInterval(undefined)).toBe(300_000);
  });

  it("polls every 30s while any game is in progress", () => {
    const data = response([makeGame({ phase: "in_progress" })]);
    expect(todayRefetchInterval(data)).toBe(30_000);
  });

  it("polls every 60s when a game is scheduled for the local today", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-12T16:00:00Z"));
    const data = response([
      makeGame({ phase: "scheduled", start_time: "2026-07-12T23:00:00Z" }),
    ]);
    expect(todayRefetchInterval(data)).toBe(60_000);
  });

  it("polls every 5 minutes when the only scheduled game is another local day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-12T16:00:00Z"));
    // 01:00 UTC July 14 = 21:00 ET July 13 — not today.
    const data = response([
      makeGame({ phase: "scheduled", start_time: "2026-07-14T01:00:00Z" }),
    ]);
    expect(todayRefetchInterval(data)).toBe(300_000);
  });

  it("treats finals as idle (5 minutes)", () => {
    const data = response([makeGame({ phase: "final" })]);
    expect(todayRefetchInterval(data)).toBe(300_000);
  });
});

describe("statusRefetchInterval", () => {
  function status(refreshing: boolean): SetupStatus {
    return { onboarded: true, followed_team_count: 3, refreshing };
  }

  it("polls every 5s while a refresh is running", () => {
    expect(statusRefetchInterval(status(true))).toBe(5_000);
  });

  it("stops polling once the refresh has finished", () => {
    // `false`, not a long interval: TanStack Query reads it as "no timer",
    // and an idle app has no reason to ask.
    expect(statusRefetchInterval(status(false))).toBe(false);
  });

  it("does not poll before the status has loaded", () => {
    expect(statusRefetchInterval(undefined)).toBe(false);
  });
});
