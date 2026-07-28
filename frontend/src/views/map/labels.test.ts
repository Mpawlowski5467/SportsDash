/**
 * The marker accessible names.
 *
 * The map used to hand MapLibre bare marker elements, so every pin fell back
 * to its built-in "Map marker" and the whole map read as one repeated button.
 * The property that matters here is DISTINGUISHABILITY: two different pins
 * must not produce the same name, and the three kinds must not read alike — a
 * cluster is a count you can open, not a place.
 */
import { describe, expect, it } from "vitest";

import { clusterMarkerLabel, teamMarkerLabel, venueMarkerLabel } from "./labels";
import type { MapGame, MapTeam } from "../../types";
import type { MapVenueGroup } from "../../components/MapVenueGamesPanel";

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

function makeGame(gameId: string): MapGame {
  return {
    game_id: gameId,
    league_id: "test-league",
    league_name: "Test League",
    sport: "soccer",
    venue: "Heron Park",
    lat: 51.5,
    lon: -0.12,
    home: {
      team_id: "test:t1",
      name: "Harbor Herons",
      abbreviation: "HH",
      logo_url: null,
      color: null,
      score: null,
    },
    away: {
      team_id: "test:t2",
      name: "Alpine Auks",
      abbreviation: "AA",
      logo_url: null,
      color: null,
      score: null,
    },
    start_time: "2026-07-27T18:00:00Z",
    phase: "scheduled",
    period_label: "",
    group: null,
    followed: true,
    source: "followed",
  };
}

function makeGroup(overrides: Partial<MapVenueGroup>): MapVenueGroup {
  return {
    key: "51.5000,-0.1200",
    venue: "Heron Park",
    lat: 51.5,
    lon: -0.12,
    games: [makeGame("test:g1")],
    source: "followed",
    ...overrides,
  };
}

describe("teamMarkerLabel", () => {
  it("names the club and the ground it stands on", () => {
    expect(teamMarkerLabel(makeTeam({}), false)).toBe("Harbor Herons, Heron Park");
  });

  it("tells two clubs apart", () => {
    // The defect in one assertion: these two pins used to speak identically.
    expect(teamMarkerLabel(makeTeam({}), false)).not.toBe(
      teamMarkerLabel(makeTeam({ name: "Alpine Auks", venue: "Auk Arena" }), false),
    );
  });

  it("falls back to the club alone when the venue is unknown", () => {
    expect(teamMarkerLabel(makeTeam({ venue: null }), false)).toBe("Harbor Herons");
  });

  it("speaks the today pulse and the in-transit plane badge", () => {
    expect(teamMarkerLabel(makeTeam({}), true)).toBe(
      "Harbor Herons, Heron Park, playing today",
    );
    expect(teamMarkerLabel(makeTeam({}), false, true)).toBe(
      "Harbor Herons, Heron Park, traveling to an away game",
    );
  });
});

describe("venueMarkerLabel", () => {
  it("supplies the place and what the badge's number counts", () => {
    const group = makeGroup({ games: [makeGame("test:g1"), makeGame("test:g2")] });
    expect(venueMarkerLabel(group, false)).toBe("Heron Park, 2 upcoming games");
  });

  it("says game, not games, for a lone fixture", () => {
    expect(venueMarkerLabel(makeGroup({}), false)).toBe("Heron Park, 1 upcoming game");
  });

  it("keeps naming an unnamed venue", () => {
    expect(venueMarkerLabel(makeGroup({ venue: null }), true)).toBe(
      "Venue, 1 upcoming game, playing today",
    );
  });
});

describe("clusterMarkerLabel", () => {
  it("reads as a count and an action, never as a place", () => {
    expect(clusterMarkerLabel(4, false, "games")).toBe("4 venues, zoom in");
    expect(clusterMarkerLabel(4, false, "stadiums")).toBe("4 stadiums, zoom in");
  });

  it("singularizes a one-pin cluster", () => {
    expect(clusterMarkerLabel(1, false, "games")).toBe("1 venue, zoom in");
  });

  it("says what the amber ring says", () => {
    expect(clusterMarkerLabel(9, true, "stadiums")).toBe(
      "9 stadiums, including teams you follow, zoom in",
    );
  });

  it("abbreviates the count exactly as the badge prints it", () => {
    // Keeps the visible label inside the accessible name (WCAG "Label in Name").
    expect(clusterMarkerLabel(1298, false, "stadiums")).toBe("1.3k stadiums, zoom in");
  });

  it("never reads like a single venue's pin", () => {
    const group = makeGroup({ games: [makeGame("test:g1"), makeGame("test:g2")] });
    expect(clusterMarkerLabel(2, false, "games")).not.toBe(
      venueMarkerLabel(group, false),
    );
  });
});
