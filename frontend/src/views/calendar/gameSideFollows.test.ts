/**
 * appearsOnGameSide: the one rule behind both calendar controls that list
 * followed teams — the Subscribe menu's per-team `.ics` rows and the team
 * filter's options. A leaderboard-sport follow (golf, racing) is dropped
 * (its tournaments are `Event`s, which have no team side); every other
 * follow stays, including the other athlete-as-team sports, whose matches
 * ARE games.
 */
import { describe, expect, it } from "vitest";

import { appearsOnGameSide, LEADERBOARD_SPORTS } from "./gameSideFollows";
import type { League, Sport, Team } from "../../types";

function makeLeague(overrides: Partial<League>): League {
  return {
    id: "test-league",
    sport: "soccer",
    name: "Test League",
    provider: "espn",
    follow_all: false,
    ...overrides,
  };
}

function makeTeam(overrides: Partial<Team>): Team {
  return {
    id: "test:t1",
    league_id: "test-league",
    name: "Harbor Herons",
    abbreviation: "HH",
    logo_url: null,
    color: null,
    ...overrides,
  };
}

/** `leaguesById` holding one league of `sport`, keyed as the team expects. */
function leaguesFor(sport: Sport): Record<string, League> {
  return { "test-league": makeLeague({ sport }) };
}

describe("appearsOnGameSide", () => {
  it("drops a leaderboard-sport follow", () => {
    // A golfer is an athlete-as-team row that never plays a Game.
    const golfer = makeTeam({ id: "test:gl-vance", name: "Marlowe Vance" });

    expect(appearsOnGameSide(golfer, leaguesFor("golf"))).toBe(false);
  });

  it("drops a racing driver the same way", () => {
    // Racing is the second leaderboard sport: a driver's races are Events.
    const driver = makeTeam({ id: "test:rc-ashby", name: "Corin Ashby" });

    expect(appearsOnGameSide(driver, leaguesFor("racing"))).toBe(false);
  });

  it("keeps athlete-as-team follows whose matches are games", () => {
    // Tennis and MMA are individual sports too, but their bouts are
    // two-sided Games, so both controls can serve them.
    for (const sport of ["tennis", "mma"] as const) {
      expect(appearsOnGameSide(makeTeam({}), leaguesFor(sport))).toBe(true);
    }
  });

  it("keeps an ordinary club", () => {
    expect(appearsOnGameSide(makeTeam({}), leaguesFor("soccer"))).toBe(true);
  });

  it("keeps a team whose league has not loaded yet", () => {
    // Sport unknown while /teams is in flight — better a row that works
    // than one flickering out from under the user mid-choice.
    expect(appearsOnGameSide(makeTeam({}), {})).toBe(true);
  });

  it("mirrors the backend's LEADERBOARD_SPORTS: golf and racing", () => {
    expect([...LEADERBOARD_SPORTS].sort()).toEqual(["golf", "racing"]);
  });
});
