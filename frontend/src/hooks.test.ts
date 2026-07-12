/**
 * The adaptive polling policy: 30s while a game is live, 60s on a game
 * day, 5 minutes otherwise. TZ pinned to America/New_York by the test
 * script.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { todayRefetchInterval } from "./hooks";
import type { Game, TodayResponse } from "./types";

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
