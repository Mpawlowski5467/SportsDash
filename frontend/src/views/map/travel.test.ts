/**
 * findFollowedTeamForSide: the id/name join from a game side to a FOLLOWED
 * plotted team, which gates the venue-panel "click any game" travel visuals.
 */
import { describe, expect, it } from "vitest";

import { findFollowedTeamForSide } from "./travel";
import type { GameSide, MapTeam } from "../../types";

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
