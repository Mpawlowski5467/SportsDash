/**
 * The method-of-victory helpers behind the final badge, the card's round
 * chip and the modal's Result block.
 *
 * Two things are worth pinning down. `fightRoundLabel` reports a partially
 * known finish (the round without the time, the time without the round),
 * and its nulls come straight off the wire. And `fightMethodLabel` indexes
 * a `Record<FightMethod, string>` whose key type is a compile-time
 * assumption about a LIVE feed: a method the union has never heard of must
 * render *something*, because the badge upper-cases whatever comes back.
 */
import { describe, expect, it } from "vitest";

import {
  fightMethodLabel,
  fightResultOf,
  fightResultSummary,
  fightRoundLabel,
} from "./fight";
import type { FightMethod, FightResult, Game } from "../types";

function makeResult(overrides: Partial<FightResult> = {}): FightResult {
  return {
    method: "ko",
    detail: "Punches",
    round: 2,
    clock: "4:21",
    ...overrides,
  };
}

function makeGame(overrides: Partial<Game> = {}): Game {
  return {
    id: "espn:bout-1",
    league_id: "ironclad-circuit",
    sport: "mma",
    home: {
      team_id: "rowan-alder",
      name: "Rowan Alder",
      abbreviation: "ALD",
      logo_url: null,
      color: null,
      score: 1,
    },
    away: {
      team_id: null,
      name: "Kestrel Vane",
      abbreviation: "VAN",
      logo_url: null,
      color: null,
      score: 0,
    },
    start_time: "2026-06-28T03:00:00Z",
    venue: null,
    series: "Ironclad 41",
    fight_result: makeResult(),
    phase: "final",
    period: 2,
    period_label: "R2",
    clock: null,
    is_intermission: false,
    followed_team_ids: ["rowan-alder"],
    ...overrides,
  };
}

describe("fightMethodLabel", () => {
  it("names every method the union knows", () => {
    expect(fightMethodLabel("ko")).toBe("KO/TKO");
    expect(fightMethodLabel("submission")).toBe("Submission");
    expect(fightMethodLabel("decision")).toBe("Decision");
    expect(fightMethodLabel("disqualification")).toBe("DQ");
    expect(fightMethodLabel("no_contest")).toBe("No Contest");
    expect(fightMethodLabel("draw")).toBe("Draw");
  });

  it("falls back to readable wording for a method the union lacks", () => {
    // The backend can classify a bout as something this build has never
    // heard of; the badge calls .toUpperCase() on the result, so undefined
    // here used to throw and take the whole card out via the error boundary.
    const unknown = "technical_draw" as FightMethod;
    expect(fightMethodLabel(unknown)).toBe("Technical Draw");
    expect(fightMethodLabel(unknown).toUpperCase()).toBe("TECHNICAL DRAW");
  });

  it("yields no wording at all for an unusable method", () => {
    // "" tells the badge to stay on the plain "FINAL" rather than render
    // "FINAL · " — and a non-string (parsed JSON promises nothing) is the
    // same non-answer, not a crash.
    expect(fightMethodLabel("" as FightMethod)).toBe("");
    expect(fightMethodLabel(undefined as unknown as FightMethod)).toBe("");
  });
});

describe("fightRoundLabel", () => {
  it("joins the round and the stoppage time", () => {
    expect(fightRoundLabel(makeResult())).toBe("R2 4:21");
  });

  it("reports the round alone when the time is unknown", () => {
    expect(fightRoundLabel(makeResult({ clock: null }))).toBe("R2");
  });

  it("reports the time alone when the round is unknown", () => {
    expect(fightRoundLabel(makeResult({ round: null }))).toBe("4:21");
  });

  it("is null when neither was reported", () => {
    expect(fightRoundLabel(makeResult({ round: null, clock: null }))).toBeNull();
  });
});

describe("fightResultSummary", () => {
  it("puts method, flavour and round on one line", () => {
    expect(fightResultSummary(makeResult())).toBe("KO/TKO · Punches · R2 4:21");
  });

  it("drops the parts the provider never reported", () => {
    expect(fightResultSummary(makeResult({ detail: null }))).toBe("KO/TKO · R2 4:21");
    expect(
      fightResultSummary(makeResult({ detail: null, round: null, clock: null })),
    ).toBe("KO/TKO");
    // An empty flavour string is as absent as a null one.
    expect(fightResultSummary(makeResult({ detail: "", round: null, clock: null }))).toBe(
      "KO/TKO",
    );
  });

  it("still summarizes a method the union lacks", () => {
    const summary = fightResultSummary(
      makeResult({ method: "technical_draw" as FightMethod, detail: null }),
    );
    expect(summary).toBe("Technical Draw · R2 4:21");
  });
});

describe("fightResultOf", () => {
  it("returns the result of a finished bout", () => {
    expect(fightResultOf(makeGame())?.method).toBe("ko");
  });

  it("withholds a result until the bout is final", () => {
    expect(fightResultOf(makeGame({ phase: "in_progress" }))).toBeNull();
    expect(fightResultOf(makeGame({ phase: "scheduled" }))).toBeNull();
    expect(fightResultOf(makeGame({ phase: "canceled" }))).toBeNull();
  });

  it("is null for every sport that has no method of victory", () => {
    expect(fightResultOf(makeGame({ fight_result: null }))).toBeNull();
    // A payload that predates the field entirely.
    const legacy = makeGame();
    delete (legacy as Partial<Game>).fight_result;
    expect(fightResultOf(legacy)).toBeNull();
  });
});
