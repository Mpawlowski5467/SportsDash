/**
 * The why-watch scorer behind the card's fire chip, plus the upset signal
 * it shares with UpsetRadar.
 *
 * Two properties matter. `detectSignal` only fires on complete odds where
 * the market and the model actually disagree — nulls anywhere, or an
 * underdog the model doesn't favor, must stay quiet. And `watchability` is
 * deliberately RARE: the both-sides-followed bonus alone never crosses the
 * threshold, and a decided game scores nothing no matter how close it was.
 */
import { describe, expect, it } from "vitest";

import { detectSignal, watchability } from "./whyWatch";
import type { Game, GameOdds } from "../types";

function makeGame(overrides: Partial<Game> = {}): Game {
  return {
    id: "test:g1",
    league_id: "test-league",
    sport: "basketball",
    home: {
      team_id: "h",
      name: "Harbor Herons",
      abbreviation: "HH",
      logo_url: null,
      color: null,
      score: null,
    },
    away: {
      team_id: "a",
      name: "Alpine Auks",
      abbreviation: "AA",
      logo_url: null,
      color: null,
      score: null,
    },
    start_time: "2026-07-12T23:00:00Z",
    venue: null,
    series: null,
    fight_result: null,
    broadcasts: [],
    phase: "scheduled",
    period: 0,
    period_label: "",
    clock: null,
    is_intermission: false,
    followed_team_ids: [],
    ...overrides,
  };
}

function makeOdds(overrides: Partial<GameOdds> = {}): GameOdds {
  return {
    provider: "TestBook",
    details: null,
    home_moneyline: null,
    away_moneyline: null,
    spread: null,
    over_under: null,
    home_win_pct: null,
    away_win_pct: null,
    ...overrides,
  };
}

/** Live one-score basketball game in the 4th quarter. */
function tightLateGame(overrides: Partial<Game> = {}): Game {
  return makeGame({
    phase: "in_progress",
    period: 4,
    home: { ...makeGame().home, score: 98 },
    away: { ...makeGame().away, score: 96 },
    ...overrides,
  });
}

/** Market fades the home side (+160) but the model likes it (52%). */
const UPSET_ODDS: Partial<GameOdds> = {
  home_moneyline: 160,
  away_moneyline: -180,
  home_win_pct: 52,
  away_win_pct: 48,
};

describe("detectSignal", () => {
  it("needs all four odds fields — any null stays quiet", () => {
    const game = makeGame();
    for (const missing of [
      "home_win_pct",
      "away_win_pct",
      "home_moneyline",
      "away_moneyline",
    ] as const) {
      const odds = makeOdds({ ...UPSET_ODDS, [missing]: null });
      expect(detectSignal(game, odds)).toBeNull();
    }
  });

  it("flags the market underdog the model favors", () => {
    const signal = detectSignal(makeGame(), makeOdds(UPSET_ODDS));
    expect(signal).toEqual({
      gameId: "test:g1",
      underdogAbbr: "HH",
      winPct: 52,
      moneyline: 160,
    });
  });

  it("flags an away underdog too, rounding its win %", () => {
    const signal = detectSignal(
      makeGame(),
      makeOdds({
        home_moneyline: -175,
        away_moneyline: 144,
        home_win_pct: 44.4,
        away_win_pct: 55.6,
      }),
    );
    expect(signal?.underdogAbbr).toBe("AA");
    expect(signal?.winPct).toBe(56);
    expect(signal?.moneyline).toBe(144);
  });

  it("stays quiet when the market and the model agree", () => {
    // Underdog at exactly 50% is agreement, not a signal.
    expect(
      detectSignal(
        makeGame(),
        makeOdds({ ...UPSET_ODDS, home_win_pct: 50, away_win_pct: 50 }),
      ),
    ).toBeNull();
    expect(
      detectSignal(
        makeGame(),
        makeOdds({ ...UPSET_ODDS, home_win_pct: 38, away_win_pct: 62 }),
      ),
    ).toBeNull();
  });

  it("derives the underdog's code from its name when it has none", () => {
    const game = makeGame({
      home: { ...makeGame().home, abbreviation: null },
    });
    expect(detectSignal(game, makeOdds(UPSET_ODDS))?.underdogAbbr).toBe("HAR");
  });
});

describe("watchability", () => {
  it("names a coin flip a toss-up", () => {
    const result = watchability(
      makeGame(),
      makeOdds({ home_win_pct: 51, away_win_pct: 49 }),
    );
    expect(result).toEqual({ score: 35, reason: "Toss-up" });
  });

  it("draws the toss-up line at 5 points from 50/50", () => {
    expect(
      watchability(makeGame(), makeOdds({ home_win_pct: 55, away_win_pct: 45 })),
    ).not.toBeNull();
    expect(
      watchability(
        makeGame(),
        makeOdds({ home_win_pct: 55.5, away_win_pct: 44.5 }),
      ),
    ).toBeNull();
  });

  it("needs both win probabilities for the toss-up read", () => {
    expect(
      watchability(makeGame(), makeOdds({ home_win_pct: 50 })),
    ).toBeNull();
  });

  it("prefers the upset story over the toss-up one", () => {
    // 52/48 is both an upset signal and a toss-up: 45 + 35.
    const result = watchability(makeGame(), makeOdds(UPSET_ODDS));
    expect(result).toEqual({ score: 80, reason: "Upset watch" });
  });

  it("flags a live one-score game in the closing period", () => {
    expect(watchability(tightLateGame(), null)).toEqual({
      score: 60,
      reason: "Tight late",
    });
  });

  it("lets the live read outrank the pre-game ones", () => {
    const result = watchability(tightLateGame(), makeOdds(UPSET_ODDS));
    expect(result?.reason).toBe("Tight late");
    expect(result?.score).toBe(60 + 45 + 35);
  });

  it("does not call the 3rd quarter late, nor 4 points close", () => {
    expect(watchability(tightLateGame({ period: 3 }), null)).toBeNull();
    expect(
      watchability(
        tightLateGame({ home: { ...makeGame().home, score: 100 } }),
        null,
      ),
    ).toBeNull();
  });

  it("uses one-goal margins and shorter games for hockey and soccer", () => {
    const hockey = tightLateGame({
      sport: "hockey",
      period: 3,
      home: { ...makeGame().home, score: 2 },
      away: { ...makeGame().away, score: 1 },
    });
    expect(watchability(hockey, null)?.reason).toBe("Tight late");
    // Two goals is not one score in hockey.
    expect(
      watchability(
        { ...hockey, home: { ...hockey.home, score: 3 } },
        null,
      ),
    ).toBeNull();

    const soccer = tightLateGame({
      sport: "soccer",
      period: 2,
      home: { ...makeGame().home, score: 1 },
      away: { ...makeGame().away, score: 0 },
    });
    expect(watchability(soccer, null)?.reason).toBe("Tight late");
  });

  it("falls back to margin ≤ 2 and the 8th inning for baseball", () => {
    const baseball = tightLateGame({
      sport: "baseball",
      period: 8,
      home: { ...makeGame().home, score: 5 },
      away: { ...makeGame().away, score: 3 },
    });
    expect(watchability(baseball, null)?.reason).toBe("Tight late");
    expect(watchability({ ...baseball, period: 7 }, null)).toBeNull();
  });

  it("never flags a live game with unreported scores", () => {
    expect(
      watchability(
        tightLateGame({ home: { ...makeGame().home, score: null } }),
        null,
      ),
    ).toBeNull();
  });

  it("treats following both sides as a bonus, never a reason", () => {
    const followed = makeGame({ followed_team_ids: ["h", "a"] });
    expect(watchability(followed, null)).toBeNull();
    expect(
      watchability(followed, makeOdds({ home_win_pct: 50, away_win_pct: 50 })),
    ).toEqual({ score: 50, reason: "Toss-up" });
  });

  it("scores nothing without odds before the game starts", () => {
    expect(watchability(makeGame(), null)).toBeNull();
    expect(watchability(makeGame(), undefined)).toBeNull();
  });

  it("goes quiet once the game is decided or dead", () => {
    for (const phase of ["final", "postponed", "canceled"] as const) {
      expect(
        watchability(tightLateGame({ phase }), makeOdds(UPSET_ODDS)),
      ).toBeNull();
    }
  });
});
