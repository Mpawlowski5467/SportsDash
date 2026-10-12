import { describe, expect, it } from "vitest";

import type { Game } from "../types";
import {
  boxScoreLabel,
  isIndividualSport,
  shortMatchup,
  shortSide,
  showsScore,
} from "./labels";
import { ordinal, periodLabel } from "./periodLabel";

const base = {
  team_id: null,
  logo_url: null,
  color: null,
  score: null,
};
const game = (sport: Game["sport"], period_label: string): Game =>
  ({
    sport,
    period_label,
    home: { ...base, name: "Carlos Alcaraz", abbreviation: "C. Alcar" },
    away: { ...base, name: "Jannik Sinner", abbreviation: "J. Sinne" },
  }) as Game;

describe("ordinal", () => {
  it("handles the teens and the units", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal)).toEqual([
      "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th",
    ]);
  });
});

describe("periodLabel", () => {
  it("writes a baseball half out, short or long", () => {
    expect(periodLabel(game("baseball", "Bot 5"), "short")).toBe("Bot 5th");
    expect(periodLabel(game("baseball", "Bot 5"), "long")).toBe("Bottom 5th");
    expect(periodLabel(game("baseball", "Top 1"), "long")).toBe("Top 1st");
    expect(periodLabel(game("baseball", "Mid 3"), "long")).toBe("Middle 3rd");
    expect(periodLabel(game("baseball", "End 12"), "short")).toBe("End 12th");
  });

  it("returns anything it does not recognise exactly as sent", () => {
    expect(periodLabel(game("baseball", "Delayed"), "long")).toBe("Delayed");
    expect(periodLabel(game("baseball", ""), "long")).toBe("");
    expect(periodLabel(game("basketball", "Q4"), "long")).toBe("Q4");
    expect(periodLabel(game("soccer", "2nd Half"), "short")).toBe("2nd Half");
  });
});

describe("short side labels", () => {
  it("uses a team's code and a person's surname", () => {
    expect(isIndividualSport("tennis")).toBe(true);
    expect(isIndividualSport("soccer")).toBe(false);
    const tennis = game("tennis", "");
    expect(shortSide(tennis, tennis.away)).toBe("Sinner");
    expect(shortMatchup(tennis)).toBe("Sinner vs Alcaraz");
    const soccer = game("soccer", "");
    expect(shortSide(soccer, soccer.away)).toBe("J. Sinne");
    expect(shortMatchup(soccer)).toBe("J. Sinne at C. Alcar");
  });
});

describe("a break in play", () => {
  const atBreak = (sport: Game["sport"], period: number, period_label: string): Game =>
    ({ ...game(sport, period_label), period, is_intermission: true }) as Game;

  it("is half time in soccer, and after the second quarter", () => {
    expect(periodLabel(atBreak("soccer", 1, "1st Half"), "long")).toBe("Half time");
    expect(periodLabel(atBreak("soccer", 1, "1st Half"), "short")).toBe("HT");
    expect(periodLabel(atBreak("basketball", 2, "Q2"), "long")).toBe("Half time");
    expect(periodLabel(atBreak("football", 2, "Q2"), "short")).toBe("HT");
  });

  it("is the end of the period otherwise", () => {
    expect(periodLabel(atBreak("basketball", 1, "Q1"), "long")).toBe("End of Q1");
    expect(periodLabel(atBreak("hockey", 2, "P2"), "short")).toBe("End P2");
    expect(periodLabel(atBreak("hockey", 1, ""), "long")).toBe("Half time");
  });

  it("leaves baseball alone: its labels already say it", () => {
    expect(periodLabel(atBreak("baseball", 5, "Mid 5"), "long")).toBe("Middle 5th");
  });
});

describe("boxScoreLabel", () => {
  const scored = (over: Partial<Game>): Game =>
    ({
      ...game("baseball", "Bot 5"),
      phase: "in_progress",
      period: 5,
      clock: null,
      is_intermission: false,
      fight_result: null,
      start_time: "2026-07-30T23:05:00Z",
      home: { ...base, name: "Athletics", abbreviation: "ATH", score: 4 },
      away: { ...base, name: "Boston Red Sox", abbreviation: "BOS", score: 3 },
      ...over,
    }) as Game;

  it("carries the score and the state, because it replaces the row for a screen reader", () => {
    expect(boxScoreLabel(scored({}))).toBe(
      "Box score: Boston Red Sox 3, Athletics 4, live Bottom 5th",
    );
    expect(boxScoreLabel(scored({ phase: "final" }))).toBe(
      "Box score: Boston Red Sox 3, Athletics 4, final",
    );
  });

  it("says when a game is still to come, or off", () => {
    const pending = { home: { ...base, name: "Athletics", abbreviation: "ATH" }, away: { ...base, name: "Boston Red Sox", abbreviation: "BOS" } };
    expect(boxScoreLabel(scored({ ...pending, phase: "scheduled" }))).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM",
    );
    expect(boxScoreLabel(scored({ ...pending, phase: "postponed" }))).toBe(
      "Box score: Boston Red Sox at Athletics, postponed",
    );
  });

  it("names a fight's winner and how it ended, and never shows its round count", () => {
    const bout = scored({
      sport: "mma",
      phase: "final",
      home: { ...base, name: "Arman Tsarukyan", abbreviation: null, score: 0 },
      away: { ...base, name: "Islam Makhachev", abbreviation: null, score: 1 },
      fight_result: { method: "submission", detail: "Rear-Naked Choke", round: 2, clock: "4:21" },
    });
    expect(showsScore(bout)).toBe(false);
    expect(boxScoreLabel(bout)).toBe(
      "Box score: Islam Makhachev beat Arman Tsarukyan, Submission · Rear-Naked Choke · R2 4:21",
    );
  });
});
