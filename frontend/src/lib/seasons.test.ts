import { describe, expect, it } from "vitest";

import { seasonChoices, seasonKeys, seasonTitle, seasonToken } from "./seasons";

describe("season labels", () => {
  it("keeps the leading season of a provider's sentence", () => {
    expect(seasonToken("2026-27 English Premier League")).toBe("2026-27");
    expect(seasonToken("2026")).toBe("2026");
    expect(seasonToken("Regular Season")).toBeNull();
    expect(seasonToken("")).toBeNull();
    expect(seasonToken(undefined)).toBeNull();
  });

  it("names the year keys a labelled season may be filed under, likeliest first", () => {
    // A soccer season is keyed by the year it starts in (checked against
    // the real API: the 2026-27 Premier League season is season=2026).
    expect(seasonKeys("2026-27 English Premier League", "soccer", 2026)).toEqual([2026, 2027]);
    // Basketball and hockey by the year it ends in.
    expect(seasonKeys("2026-27", "basketball", 2026)).toEqual([2027, 2026]);
    expect(seasonKeys("2026/2027", "hockey", 2026)).toEqual([2027, 2026]);
    expect(seasonKeys("1999-00 NBA", "basketball", 2026)).toEqual([2000, 1999]);
    // One year in the label is one key, whatever the sport.
    expect(seasonKeys("2026", "baseball", 2026)).toEqual([2026]);
    expect(seasonKeys("2025 NFL", "football", 2026)).toEqual([2025]);
  });

  it("falls back to the calendar year when the label says nothing", () => {
    expect(seasonKeys("", "baseball", 2026)).toEqual([2026]);
    expect(seasonKeys(undefined, "soccer", 2026)).toEqual([2026]);
    expect(seasonKeys("Regular Season", undefined, 2026)).toEqual([2026]);
  });
});

describe("seasonTitle", () => {
  it("keeps the leading year or year range of a provider's sentence", () => {
    expect(seasonTitle("2026")).toBe("2026 season");
    expect(seasonTitle("2026-27 English Premier League")).toBe("2026-27 season");
    expect(seasonTitle("2025/26 LaLiga")).toBe("2025/26 season");
  });

  it("returns a string with no leading year as it is, and nothing as null", () => {
    expect(seasonTitle("ATP Rankings")).toBe("ATP Rankings");
    expect(seasonTitle("  ")).toBeNull();
    expect(seasonTitle(undefined)).toBeNull();
  });
});

describe("seasonChoices", () => {
  const base = [
    { id: "current", label: "Current season" },
    { id: "2026", label: "2026" },
    { id: "2025", label: "2025" },
  ];

  it("names the live option by its season once the live table has said it", () => {
    expect(seasonChoices(base, "current", "current", "2026-27 season", null)).toEqual([
      { id: "current", label: "2026-27 season" },
      { id: "2026", label: "2026 season" },
      { id: "2025", label: "2025 season" },
    ]);
  });

  it("keeps the generic label until the live table loads", () => {
    expect(seasonChoices(base, "current", "current", null, null)[0].label).toBe(
      "Current season",
    );
  });

  it("does not list the season in progress twice under one name", () => {
    // A calendar-year league: the newest archive key is the live season.
    expect(seasonChoices(base, "current", "current", "2026 season", null)).toEqual([
      { id: "current", label: "2026 season" },
      { id: "2025", label: "2025 season" },
    ]);
    // Unless it is the one picked: the trigger must still find its option.
    expect(
      seasonChoices(base, "current", "2026", "2026 season", "2026 season").map((c) => c.id),
    ).toEqual(["current", "2026", "2025"]);
  });

  it("names only the picked past season by what its own table says", () => {
    const choices = seasonChoices(base, "current", "2026", "2026-27 season", "2025-26 season");
    expect(choices[1].label).toBe("2025-26 season");
    expect(choices[2].label).toBe("2025 season");
  });
});
