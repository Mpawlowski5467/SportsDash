/**
 * winningSide / anniversaryYear / scoreline: the logic behind the Today
 * view's "On this day" rows — who gets bolded, what year the chip shows,
 * and the away-first score string.
 *
 * The suite runs under TZ=America/New_York (see package.json), which is
 * what makes the local-year boundary case below meaningful.
 */
import { describe, expect, it } from "vitest";

import {
  anniversaryYear,
  isOnThisDayCurrent,
  scoreline,
  winningSide,
} from "./onThisDay";
import type { Game } from "../../types";

function makeGame(overrides: Partial<Game>): Game {
  return {
    id: "mock:hh-gc-2019",
    league_id: "test-league",
    sport: "soccer",
    home: {
      team_id: "test:hh",
      name: "Harbor Herons",
      abbreviation: "HH",
      logo_url: null,
      color: null,
      score: 2,
    },
    away: {
      team_id: null,
      name: "Granite Cliffs",
      abbreviation: "GC",
      logo_url: null,
      color: null,
      score: 1,
    },
    start_time: "2019-07-28T23:00:00Z",
    venue: null,
    series: null,
    fight_result: null,
    broadcasts: [],
    phase: "final",
    period: 0,
    period_label: "",
    clock: null,
    is_intermission: false,
    followed_team_ids: ["test:hh"],
    ...overrides,
  };
}

/** The given away/home scores on the base fixture. */
function withScores(away: number | null, home: number | null): Game {
  const base = makeGame({});
  return {
    ...base,
    away: { ...base.away, score: away },
    home: { ...base.home, score: home },
  };
}

describe("winningSide", () => {
  it("picks the home side when it outscored the away side", () => {
    expect(winningSide(withScores(1, 2))).toBe("home");
  });

  it("picks the away side when it outscored the home side", () => {
    expect(winningSide(withScores(3, 0))).toBe("away");
  });

  it("bolds nobody on a draw", () => {
    expect(winningSide(withScores(2, 2))).toBeNull();
  });

  it("bolds nobody when a score is missing", () => {
    // Defensive: the backend only ships FINAL games, but score is nullable.
    expect(winningSide(withScores(null, 2))).toBeNull();
    expect(winningSide(withScores(1, null))).toBeNull();
  });
});

describe("anniversaryYear", () => {
  it("reads the year off the start time", () => {
    expect(anniversaryYear(makeGame({ start_time: "2019-07-28T23:00:00Z" }))).toBe(
      2019,
    );
  });

  it("uses the LOCAL year, not the UTC one", () => {
    // 02:00Z on Jan 1 is still New Year's Eve in New York.
    expect(anniversaryYear(makeGame({ start_time: "2020-01-01T02:00:00Z" }))).toBe(
      2019,
    );
  });
});

describe("scoreline", () => {
  it("renders away-first, matching the row's AwayName ... HomeName order", () => {
    expect(scoreline(withScores(1, 2))).toBe("1 – 2");
  });

  it("dashes a missing score", () => {
    expect(scoreline(withScores(null, 2))).toBe("— – 2");
  });
});

describe("isOnThisDayCurrent", () => {
  it("accepts a payload stamped with today's local day", () => {
    expect(isOnThisDayCurrent("2026-07-28", "2026-07-28")).toBe(true);
  });

  it("rejects yesterday's payload after the midnight rollover", () => {
    expect(isOnThisDayCurrent("2026-07-27", "2026-07-28")).toBe(false);
  });

  it("skips the comparison when either side is missing", () => {
    // An older backend may omit `date`; a stamp we can't compare must not
    // hide an otherwise good section.
    expect(isOnThisDayCurrent(undefined, "2026-07-28")).toBe(true);
    expect(isOnThisDayCurrent("", "2026-07-28")).toBe(true);
    expect(isOnThisDayCurrent("2026-07-28", undefined)).toBe(true);
    expect(isOnThisDayCurrent("2026-07-28", "")).toBe(true);
  });
});
