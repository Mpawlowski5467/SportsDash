/**
 * findFollowedTeamForSide: the id/name join from a game side to a FOLLOWED
 * plotted team, which gates the venue-panel "click any game" travel visuals.
 *
 * buildTravelInfo: the travel facts behind the team panel and the pin's plane
 * badge — in particular `inTransit`, which is time-based and must flip on the
 * clock alone.
 */
import { describe, expect, it } from "vitest";

import { buildTravelInfo, findFollowedTeamForSide, type NextGame } from "./travel";
import type { GameSide, MapGame, MapTeam } from "../../types";

function makeTeam(overrides: Partial<MapTeam>): MapTeam {
  return {
    team_id: "test:t1",
    name: "Harbor Herons",
    abbreviation: "HH",
    league_id: "test-league",
    league_name: "Test League",
    sport: "soccer",
    color: "#123456",
    logo_url: null,
    venue: "Heron Park",
    lat: 51.5,
    lon: -0.12,
    capacity: null,
    opened: null,
    image_url: null,
    location: null,
    surface: null,
    description: null,
    description_source: null,
    venue_description: null,
    founded_year: null,
    next_opponent: null,
    next_match_time: null,
    group: null,
    source: "followed",
    weather: null,
    ...overrides,
  };
}

function makeSide(overrides: Partial<GameSide>): GameSide {
  return {
    team_id: null,
    name: "Harbor Herons",
    abbreviation: "HH",
    logo_url: null,
    color: null,
    score: null,
    ...overrides,
  };
}

describe("findFollowedTeamForSide", () => {
  it("joins a side to a followed team by team_id", () => {
    const teams = [makeTeam({ team_id: "test:t1" })];
    const side = makeSide({ team_id: "test:t1", name: "Unrelated Name" });
    expect(findFollowedTeamForSide(teams, side)?.team_id).toBe("test:t1");
  });

  it("joins by normalized name when the side carries no team_id", () => {
    const teams = [makeTeam({ name: "Harbor Herons" })];
    const side = makeSide({ team_id: null, name: "  harbor herons " });
    expect(findFollowedTeamForSide(teams, side)?.name).toBe("Harbor Herons");
  });

  it("ignores competition teams even when the id matches", () => {
    const teams = [makeTeam({ source: "competition" })];
    const side = makeSide({ team_id: "test:t1" });
    expect(findFollowedTeamForSide(teams, side)).toBeUndefined();
  });

  it("prefers the id join over a colliding name", () => {
    const teams = [
      makeTeam({ team_id: "test:t1", name: "Harbor Herons" }),
      makeTeam({ team_id: "test:t2", name: "Alpine Auks" }),
    ];
    const side = makeSide({ team_id: "test:t2", name: "Harbor Herons" });
    expect(findFollowedTeamForSide(teams, side)?.team_id).toBe("test:t2");
  });

  it("returns undefined when no plotted team matches", () => {
    const teams = [makeTeam({})];
    const side = makeSide({ team_id: "test:t9", name: "Desert Dingoes" });
    expect(findFollowedTeamForSide(teams, side)).toBeUndefined();
  });
});

/** The 48h window `inTransit` reports on, in ms. */
const IN_TRANSIT_MS = 48 * 3600 * 1000;

function makeAwayGame(startTime: string): MapGame {
  return {
    game_id: "test:g1",
    league_id: "test-league",
    league_name: "Test League",
    sport: "soccer",
    venue: "Auk Arena",
    // Far enough from the team's Heron Park to be a real trip.
    lat: 48.85,
    lon: 2.35,
    home: makeSide({ team_id: "test:t2", name: "Alpine Auks", abbreviation: "AA" }),
    away: makeSide({ team_id: "test:t1" }),
    start_time: startTime,
    phase: "scheduled",
    period_label: "",
    group: null,
    followed: true,
    source: "followed",
  };
}

function inTransitAt(nowMs: number, startMs: number): boolean {
  const teams = [makeTeam({})];
  const next = new Map<string, NextGame>([
    ["test:t1", { game: makeAwayGame(new Date(startMs).toISOString()), isHome: false }],
  ]);
  return buildTravelInfo(teams, next, nowMs).get("test:t1")?.inTransit ?? false;
}

describe("buildTravelInfo inTransit", () => {
  // Fixed instant: the flag depends on the clock, so the clock is the input.
  const now = Date.parse("2026-07-27T12:00:00Z");

  it("flips false -> true as the clock crosses the 48h boundary", () => {
    // Kick-off is a minute outside the window: not in transit yet...
    const kickoff = now + IN_TRANSIT_MS + 60_000;
    expect(inTransitAt(now, kickoff)).toBe(false);
    // ...and two minutes later, with nothing else changed, it is. This is the
    // transition a render-derived clock can never deliver on an idle map.
    expect(inTransitAt(now + 120_000, kickoff)).toBe(true);
  });

  it("treats exactly 48h out as in transit (the window is inclusive)", () => {
    expect(inTransitAt(now, now + IN_TRANSIT_MS)).toBe(true);
  });

  it("stops reporting transit once kick-off has passed", () => {
    expect(inTransitAt(now, now - 1000)).toBe(false);
  });

  it("ignores an unparseable kick-off rather than claiming a trip", () => {
    const teams = [makeTeam({})];
    const next = new Map<string, NextGame>([
      ["test:t1", { game: makeAwayGame("not-a-date"), isHome: false }],
    ]);
    expect(buildTravelInfo(teams, next, now).get("test:t1")?.inTransit).toBe(
      false,
    );
  });
});
