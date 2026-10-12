import { describe, expect, it } from "vitest";

import type {
  Game,
  GameSide,
  Sport,
  StandingRow,
  Standings,
  Team,
} from "../../types";
import { sportWords } from "../../lib/labels";
import {
  FORM_GAMES,
  SEASON_GAP_DAYS,
  colorsByName,
  currentSeason,
  deckSeason,
  detailLabel,
  figureEms,
  figureSize,
  followedOpponent,
  formRecord,
  gamesPlayed,
  hasUnwatchedLive,
  holdsNewestResult,
  isSeasonInPlay,
  lastGames,
  mergeSeason,
  monthGroups,
  nextUp,
  nextUpDay,
  nextUpNote,
  perspective,
  recordWords,
  seasonKey,
  seasonRow,
  standingFigure,
  streakFigure,
  streakWords,
  summarize,
  teamRow,
  teamSwitcherKind,
  unlistedFinals,
  venueWord,
} from "./model";

// The suite runs in America/New_York (see the `test` script).

const ATH: Team = {
  id: "ath",
  league_id: "mlb",
  name: "Athletics",
  abbreviation: "ATH",
  logo_url: null,
  color: "#EFB21E",
};
const ARI: Team = {
  id: "ari",
  league_id: "mlb",
  name: "Arizona Diamondbacks",
  abbreviation: "ARI",
  logo_url: null,
  color: "#A71930",
};

function side(
  name: string,
  score: number | null = null,
  teamId: string | null = null,
): GameSide {
  return { team_id: teamId, name, abbreviation: null, logo_url: null, color: null, score };
}

function game(over: Partial<Game> = {}): Game {
  return {
    id: "g",
    league_id: "mlb",
    sport: "baseball",
    home: side("Athletics", null, "ath"),
    away: side("Boston Red Sox"),
    start_time: "2026-07-30T02:05:00Z", // 10:05 PM on Wed Jul 29 in New York
    venue: null,
    series: null,
    fight_result: null,
    broadcasts: [],
    phase: "final",
    period: 0,
    period_label: "",
    clock: null,
    is_intermission: false,
    followed_team_ids: ["ath"],
    ...over,
  };
}

/** A final for the Athletics: their score first, at home unless told otherwise. */
function final(
  id: string,
  mine: number | null,
  theirs: number | null,
  over: Partial<Game> & { road?: boolean } = {},
): Game {
  const { road = false, ...rest } = over;
  const me = side("Athletics", mine, "ath");
  const them = side("Boston Red Sox", theirs);
  return game({
    id,
    home: road ? them : me,
    away: road ? me : them,
    ...rest,
  });
}

function row(over: Partial<StandingRow> = {}): StandingRow {
  return {
    rank: 4,
    team_name: "Athletics",
    team_id: "ath",
    logo_url: null,
    abbreviation: null,
    color: null,
    wins: 45,
    losses: 63,
    draws: null,
    points: null,
    goal_diff: null,
    win_pct: 0.417,
    games_back: 10,
    ot_losses: null,
    group: "American League",
    subgroup: "AL West",
    ...over,
  };
}

function table(sport: Sport, rows: StandingRow[], leagueName = "MLB"): Standings {
  return {
    league_id: "l",
    league_name: leagueName,
    sport,
    season: "2026",
    fetched_at: null,
    rows,
    is_stale: false,
  };
}

describe("teamSwitcherKind", () => {
  it("keeps toggle buttons up to six teams and a dropdown past that", () => {
    expect(teamSwitcherKind(1)).toBe("buttons");
    expect(teamSwitcherKind(6)).toBe("buttons");
    expect(teamSwitcherKind(7)).toBe("select");
  });
});

describe("perspective", () => {
  it("finds the team's side by id, home or away", () => {
    const home = perspective(final("a", 2, 4), ATH);
    expect(home?.home).toBe(true);
    expect(home?.mine.score).toBe(2);
    expect(home?.theirs.name).toBe("Boston Red Sox");
    const away = perspective(final("b", 2, 4, { road: true }), ATH);
    expect(away?.home).toBe(false);
    expect(away?.mine.score).toBe(2);
  });

  it("falls back to the name when a row has lost its tag", () => {
    const untagged = game({ home: side("Athletics", 3), away: side("Boston Red Sox", 1) });
    expect(perspective(untagged, ATH)?.home).toBe(true);
  });

  it("is null when the team is on neither side", () => {
    const other = game({ home: side("Detroit Tigers"), away: side("Boston Red Sox") });
    expect(perspective(other, ATH)).toBeNull();
  });
});

describe("venueWord", () => {
  it("is vs at home and at on the road, and always vs for people", () => {
    expect(venueWord("baseball", true)).toBe("vs");
    expect(venueWord("baseball", false)).toBe("at");
    expect(venueWord("tennis", false)).toBe("vs");
    expect(venueWord("mma", false)).toBe("vs");
  });
});

describe("lastGames", () => {
  it("names a stretch of games, matches or bouts", () => {
    expect(lastGames(8, sportWords("baseball"))).toBe("last 8 games");
    expect(lastGames(1, sportWords("mma"))).toBe("last bout");
    expect(lastGames(3, sportWords("tennis"))).toBe("last 3 matches");
  });
});

describe("summarize", () => {
  // The mockup's eight July rows, newest first: L W L L L W L L.
  const july = [
    final("1", 2, 4),
    final("2", 4, 3),
    final("3", 2, 4),
    final("4", 8, 11, { road: true }),
    final("5", 0, 2, { road: true }),
    final("6", 2, 0, { road: true }),
    final("7", 5, 15, { road: true }),
    final("8", 5, 6, { road: true }),
  ];

  it("reproduces the mockup's figures from its own rows", () => {
    const summary = summarize(july, ATH, "baseball");
    expect(summary.form).toEqual(["L", "W", "L", "L", "L", "W", "L", "L"]);
    expect(formRecord(summary, "baseball")).toBe("2–6");
    expect(summary.streak).toEqual({ outcome: "L", count: 1 });
    expect(summary.scored).toBe(28);
    expect(summary.allowed).toBe(45);
  });

  it("covers eight games by default and no more", () => {
    expect(FORM_GAMES).toBe(8);
    const summary = summarize([...july, final("9", 9, 0), final("10", 9, 0)], ATH, "baseball");
    expect(summary.form).toHaveLength(8);
    expect(summary.scored).toBe(28);
  });

  it("runs the streak past the window", () => {
    const wins = Array.from({ length: 11 }, (_, index) => final(`w${index}`, 3, 1));
    const summary = summarize([...wins, final("l", 0, 1)], ATH, "baseball");
    expect(summary.streak).toEqual({ outcome: "W", count: 11 });
    expect(summary.form).toHaveLength(8);
  });

  it("skips a game with no result: it neither breaks the streak nor fills the window", () => {
    const summary = summarize(
      [final("a", 3, 1), final("b", null, null), final("c", 2, 0), final("d", 0, 1)],
      ATH,
      "baseball",
    );
    expect(summary.form).toEqual(["W", "W", "L"]);
    expect(summary.streak).toEqual({ outcome: "W", count: 2 });
    expect(summary.scored).toBe(5);
    expect(summary.allowed).toBe(2);
  });

  it("says how many games it covers when there are fewer than the window", () => {
    const summary = summarize([final("a", 4, 3), final("b", 3, 2)], ATH, "soccer");
    expect(summary.form).toEqual(["W", "W"]);
    expect(summary.scored).toBe(7);
    expect(summary.allowed).toBe(5);
  });

  it("has nothing to say with no decided game", () => {
    const summary = summarize([], ATH, "baseball");
    expect(summary.form).toEqual([]);
    expect(summary.streak).toBeNull();
    expect(summary.scored).toBeNull();
    expect(summary.allowed).toBeNull();
  });

  it("adds up no totals for a fight", () => {
    const bout = final("f", 1, 0, {
      sport: "mma",
      fight_result: { method: "ko", detail: null, round: 1, clock: "2:10" },
    });
    const summary = summarize([bout], ATH, "mma");
    expect(summary.form).toEqual(["W"]);
    expect(summary.scored).toBeNull();
    expect(summary.allowed).toBeNull();
  });

  it("counts a draw", () => {
    const summary = summarize(
      [final("a", 1, 1), final("b", 1, 1), final("c", 2, 0)],
      ATH,
      "soccer",
    );
    expect(summary.streak).toEqual({ outcome: "D", count: 2 });
    expect([summary.wins, summary.draws, summary.losses]).toEqual([1, 2, 0]);
  });
});

describe("formRecord", () => {
  const record = (wins: number, losses: number, draws: number) => ({
    form: [],
    wins,
    losses,
    draws,
    streak: null,
    scored: null,
    allowed: null,
  });

  it("writes soccer as W–D–L, always with the draws", () => {
    expect(formRecord(record(5, 1, 2), "soccer")).toBe("5–2–1");
    expect(formRecord(record(5, 3, 0), "soccer")).toBe("5–0–3");
  });

  it("writes every other sport as W–L, with a tie only when there was one", () => {
    expect(formRecord(record(2, 6, 0), "baseball")).toBe("2–6");
    expect(formRecord(record(4, 3, 1), "football")).toBe("4–3–1");
  });
});

describe("streak", () => {
  it("is written the short way and in words", () => {
    const games = sportWords("baseball");
    expect(streakFigure({ outcome: "L", count: 1 })).toBe("L1");
    expect(streakFigure({ outcome: "W", count: 12 })).toBe("W12");
    expect(streakWords({ outcome: "L", count: 1 }, games)).toBe("Lost last game");
    expect(streakWords({ outcome: "W", count: 3 }, games)).toBe("Won last 3 games");
    expect(streakWords({ outcome: "D", count: 2 }, sportWords("soccer"))).toBe(
      "Drew last 2 matches",
    );
  });
});

describe("seasonKey", () => {
  it("is the one year the provider files the labelled season under", () => {
    // Soccer by the year a season starts in, basketball and hockey by the
    // year it ends in (checked against the provider for all three).
    expect(seasonKey("2026-27 English Premier League", "soccer", 2026)).toBe(2026);
    expect(seasonKey("2026-27", "basketball", 2026)).toBe(2027);
    expect(seasonKey("2026/2027", "hockey", 2026)).toBe(2027);
    expect(seasonKey("2026", "baseball", 2026)).toBe(2026);
    expect(seasonKey("2025 NFL", "football", 2026)).toBe(2025);
  });

  it("never offers the label's other year: it is last season, or next", () => {
    // In November 2026 the calendar year is the 2025-26 season's key.
    expect(seasonKey("2026-27", "hockey", 2026)).not.toBe(2026);
    expect(seasonKey("2026-27 English Premier League", "soccer", 2027)).not.toBe(2027);
  });

  it("guesses the calendar year without a label, where that cannot be last season", () => {
    expect(seasonKey(undefined, "soccer", 2026)).toBe(2026);
    expect(seasonKey("", "baseball", 2026)).toBe(2026);
    expect(seasonKey("Regular Season", "football", 2026)).toBe(2026);
  });

  it("has no key without a label for a season filed under its ending year", () => {
    expect(seasonKey(undefined, "hockey", 2026)).toBeUndefined();
    expect(seasonKey("", "basketball", 2026)).toBeUndefined();
  });
});

describe("isSeasonInPlay", () => {
  it("is always so for Current season, and for the year that is the live key", () => {
    expect(isSeasonInPlay(undefined, 2026)).toBe(true);
    expect(isSeasonInPlay(undefined, undefined)).toBe(true);
    expect(isSeasonInPlay(2026, 2026)).toBe(true);
  });

  it("is not so for any other year, or while the live key is unknown", () => {
    // Autumn 2026 in the NBA: the picker's 2026 is last season (the key in
    // play is 2027).
    expect(isSeasonInPlay(2026, 2027)).toBe(false);
    expect(isSeasonInPlay(2025, 2026)).toBe(false);
    expect(isSeasonInPlay(2026, undefined)).toBe(false);
  });
});

describe("deckSeason", () => {
  it("is the picked year", () => {
    expect(deckSeason(2025, "2026-27 English Premier League", true, false)).toBe("2025");
  });

  it("is the live table's season when that season's list is on screen", () => {
    expect(deckSeason(undefined, "2026-27 English Premier League", true, true)).toBe("2026-27");
    expect(deckSeason(undefined, "2026", true, true)).toBe("2026");
  });

  it("is left out when the label cannot be checked against a list", () => {
    // October 2026: the provider's baseball table already reads "2027"
    // over the 2026 records, and there is no 2027 list.
    expect(deckSeason(undefined, "2027", true, false)).toBeNull();
    expect(deckSeason(undefined, undefined, true, true)).toBeNull();
  });

  it("keeps the label for a league that has no lists to check it against", () => {
    expect(deckSeason(undefined, "2026", false, false)).toBe("2026");
    expect(deckSeason(undefined, "", false, false)).toBeNull();
  });
});

describe("the season being played", () => {
  const at = (id: string, start: string, over: Partial<Game> = {}) =>
    final(id, 1, 0, { start_time: start, ...over });
  // What the provider lists for the season, newest first...
  const season = [
    at("oct10", "2026-10-10T14:00:00Z"),
    at("sep18", "2026-09-18T19:00:00Z"),
    at("sep12", "2026-09-12T14:00:00Z"),
    at("sep06", "2026-09-06T15:30:00Z"),
    at("aug30", "2026-08-30T13:00:00Z"),
    at("aug24", "2026-08-24T19:00:00Z"),
  ];
  // ...and what the app stored: it was off for September.
  const stored = [season[0], season[4], season[5]];
  const lastSeason = [at("may24", "2026-05-24T15:00:00Z"), at("may19", "2026-05-19T19:00:00Z")];

  it("is proven by holding the team's newest stored result in its league", () => {
    expect(holdsNewestResult(season, stored, "mlb")).toBe(true);
    expect(holdsNewestResult(lastSeason, stored, "mlb")).toBe(false);
  });

  it("ignores a stored result from another competition when looking for the newest", () => {
    const friendly = at("friendly", "2026-10-12T18:00:00Z", { league_id: "friendlies" });
    expect(holdsNewestResult(season, [friendly, ...stored], "mlb")).toBe(true);
  });

  it("is unproven with nothing stored, or no list", () => {
    expect(holdsNewestResult(season, [], "mlb")).toBe(false);
    expect(holdsNewestResult(season, undefined, "mlb")).toBe(false);
    expect(holdsNewestResult(undefined, stored, "mlb")).toBe(false);
  });

  it("is the list asked for, when it has games in it", () => {
    expect(currentSeason(season)).toBe(season);
  });

  it("is undefined when the list did not come back, or came back empty", () => {
    expect(currentSeason(undefined)).toBeUndefined();
    expect(currentSeason([])).toBeUndefined();
  });

  it("fills the hole in the stored results from the season's list", () => {
    const merged = mergeSeason(season, stored, "mlb");
    expect(merged.map((entry) => entry.id)).toEqual([
      "oct10",
      "sep18",
      "sep12",
      "sep06",
      "aug30",
      "aug24",
    ]);
    // A streak read from the stored results alone would run across September.
    const lost = (entry: Game) => ({ ...entry, home: side("Athletics", 0, "ath"), away: side("Boston Red Sox", 2) });
    const truth = mergeSeason([season[0], lost(season[1]), ...season.slice(2)], stored, "mlb");
    expect(summarize(stored, ATH, "baseball").streak).toEqual({ outcome: "W", count: 3 });
    expect(summarize(truth, ATH, "baseball").streak).toEqual({ outcome: "W", count: 1 });
  });

  it("keeps the stored copy of a game both have: it is the one a box score opens for", () => {
    const listed = { ...season[0], venue: "from the list" };
    const kept = { ...season[0], venue: "from the store" };
    expect(mergeSeason([listed], [kept], "mlb")[0].venue).toBe("from the store");
  });

  it("adds a stored result the list does not have yet, and drops earlier seasons", () => {
    const fresh = at("oct17", "2026-10-17T14:00:00Z");
    const merged = mergeSeason(season, [fresh, ...stored, ...lastSeason], "mlb");
    expect(merged[0].id).toBe("oct17");
    expect(merged.map((entry) => entry.id)).not.toContain("may24");
    expect(merged).toHaveLength(7);
  });

  it("orders by the moment, not by how the time is written", () => {
    const offset = at("later", "2026-10-10T08:00:00-07:00"); // 15:00Z
    expect(mergeSeason([season[0]], [offset], "mlb").map((entry) => entry.id)).toEqual([
      "later",
      "oct10",
    ]);
  });

  it("is the stored results alone when the season's list is empty", () => {
    expect(mergeSeason([], stored, "mlb")).toEqual(stored);
  });

  it("keeps stored games from other competitions: the list does not speak for them", () => {
    // A national team followed under one competition, with a friendly and a
    // tournament stored from before that competition's first game.
    const league = [at("nl2", "2026-10-09T18:45:00Z"), at("nl1", "2026-09-25T18:45:00Z")];
    const others = [
      at("friendly", "2026-09-05T18:45:00Z", { league_id: "friendly" }),
      at("wc2", "2026-07-04T19:00:00Z", { league_id: "worldcup" }),
      at("wc1", "2026-06-28T19:00:00Z", { league_id: "worldcup" }),
    ];
    const merged = mergeSeason(league, [...league, ...others], "mlb");
    expect(merged.map((entry) => entry.id)).toEqual(["nl2", "nl1", "friendly", "wc2", "wc1"]);
  });

  it("follows the stored games back from a list that starts mid-season", () => {
    // Once a postseason begins the provider lists only that; the regular
    // season before it is the same season, days earlier.
    const postseason = [
      at("p2", "2026-10-06T00:00:00Z"),
      at("p1", "2026-10-04T00:00:00Z"),
      at("p0", "2026-10-03T20:00:00Z"),
    ];
    const kept = [postseason[0], postseason[1], at("r2", "2026-09-19T02:00:00Z"), at("r1", "2026-09-18T02:00:00Z")];
    expect(mergeSeason(postseason, [...kept, ...lastSeason], "mlb").map((entry) => entry.id)).toEqual([
      "p2",
      "p1",
      "p0",
      "r2",
      "r1",
    ]);
  });

  it("stops following at a gap longer than a season has: that is an earlier season", () => {
    const day = 24 * 60 * 60 * 1000;
    const first = new Date("2026-08-24T19:00:00Z").getTime();
    const before = (id: string, days: number) => at(id, new Date(first - days * day).toISOString());
    const within = before("within", SEASON_GAP_DAYS);
    const chained = before("chained", SEASON_GAP_DAYS * 2);
    const cold = before("cold", SEASON_GAP_DAYS * 3 + 1);
    const older = before("older", SEASON_GAP_DAYS * 3 + 2);
    // Given out of order on purpose: the walk is by time, not by position.
    const merged = mergeSeason(season, [older, chained, within, cold], "mlb");
    expect(merged.slice(-2).map((entry) => entry.id)).toEqual(["within", "chained"]);
    expect(merged.map((entry) => entry.id)).not.toContain("cold");
    // A game past the gap is not picked up again by one just behind it.
    expect(merged.map((entry) => entry.id)).not.toContain("older");
    // A whole off-season away, as before.
    expect(mergeSeason(season, [before("spring", 60)], "mlb")).toHaveLength(season.length);
  });
});

describe("seasonRow", () => {
  const live = table("baseball", [row()]);

  it("is the team's row in the live table for the season being played", () => {
    expect(seasonRow(live, ATH, true, true)?.rank).toBe(4);
  });

  it("is nothing for a season the live table is not about", () => {
    expect(seasonRow(live, ATH, false, true)).toBeUndefined();
    expect(seasonRow(undefined, ATH, true, true)).toBeUndefined();
  });

  it("drops a row with no games played while there are results on screen", () => {
    const rolled = table("baseball", [row({ wins: 0, losses: 0 })]);
    expect(gamesPlayed(rolled.rows[0])).toBe(0);
    expect(seasonRow(rolled, ATH, true, true)).toBeUndefined();
    // Before a first game there is nothing on screen to contradict 0–0.
    expect(seasonRow(rolled, ATH, true, false)?.wins).toBe(0);
  });

  it("keeps a ranking, which never has games played", () => {
    const ranking = table("tennis", [row({ wins: 0, losses: 0, points: 9730 })]);
    expect(seasonRow(ranking, ATH, true, true)?.points).toBe(9730);
  });

  it("counts draws and overtime losses as games played", () => {
    expect(gamesPlayed(row({ wins: 3, losses: 2, draws: 1, ot_losses: null }))).toBe(6);
    expect(gamesPlayed(row({ wins: 40, losses: 30, draws: null, ot_losses: 12 }))).toBe(82);
  });
});

describe("teamRow", () => {
  it("finds the row by id, then by name (an archived table has no ids)", () => {
    const live = table("baseball", [row({ team_name: "A's", team_id: "ath" })]);
    expect(teamRow(live, ATH)?.team_name).toBe("A's");
    const archived = table("baseball", [row({ team_id: null })]);
    expect(teamRow(archived, ATH)?.rank).toBe(4);
    expect(teamRow(table("baseball", []), ATH)).toBeUndefined();
    expect(teamRow(undefined, ATH)).toBeUndefined();
  });
});

describe("recordWords", () => {
  it("spells the record out as each sport counts it", () => {
    expect(recordWords(row(), "baseball")).toBe("45 wins, 63 losses");
    expect(recordWords(row({ wins: 1, losses: 1 }), "baseball")).toBe("1 win, 1 loss");
    expect(recordWords(row({ wins: 18, draws: 9, losses: 11 }), "soccer")).toBe(
      "18 wins, 9 draws, 11 losses",
    );
    expect(recordWords(row({ wins: 40, losses: 30, ot_losses: 1 }), "hockey")).toBe(
      "40 wins, 30 losses, 1 overtime loss",
    );
    expect(recordWords(row({ wins: 9, losses: 7, draws: 1 }), "football")).toBe(
      "9 wins, 7 losses, 1 tie",
    );
    expect(recordWords(row({ wins: 9, losses: 8, draws: 0 }), "football")).toBe(
      "9 wins, 8 losses",
    );
  });
});

describe("standingFigure", () => {
  it("is a division place with games back in baseball", () => {
    const mine = row();
    expect(standingFigure(mine, table("baseball", [mine, row({ subgroup: "AL East" })]))).toEqual({
      label: "Division",
      rank: 4,
      table: "AL West",
      second: { value: "10", label: "games back" },
    });
  });

  it("keeps a half game and drops the figure for a leader", () => {
    const half = row({ games_back: 2.5 });
    expect(standingFigure(half, table("baseball", [half]))?.second?.value).toBe("2.5");
    const one = row({ games_back: 1 });
    expect(standingFigure(one, table("baseball", [one]))?.second?.label).toBe("game back");
    const leader = row({ rank: 1, games_back: 0 });
    expect(standingFigure(leader, table("baseball", [leader]))?.second).toBeNull();
  });

  it("is a place in the league with points in soccer, where there is no division", () => {
    const chelsea = row({
      rank: 4,
      team_name: "Chelsea",
      points: 6,
      games_back: null,
      group: "2026-27 English Premier League",
      subgroup: null,
    });
    expect(standingFigure(chelsea, table("soccer", [chelsea], "Premier League"))).toEqual({
      label: "Standing",
      rank: 4,
      table: "Premier League",
      second: { value: "6", label: "points" },
    });
  });

  it("is a win percentage in football", () => {
    const team = row({ win_pct: 0.667, games_back: null });
    expect(standingFigure(team, table("football", [team]))?.second).toEqual({
      value: ".667",
      label: "win pct",
    });
  });

  it("is a ranking in tennis and nothing for a sport with no table", () => {
    const player = row({ rank: 2, points: 9730, games_back: null, group: null, subgroup: null });
    expect(standingFigure(player, table("tennis", [player], "ATP"))).toEqual({
      label: "Ranking",
      rank: 2,
      table: "ATP",
      second: { value: "9730", label: "points" },
    });
    expect(standingFigure(player, table("mma", [player]))).toBeNull();
  });
});

describe("monthGroups", () => {
  it("groups consecutive games by local month, newest first", () => {
    const groups = monthGroups([
      game({ id: "a", start_time: "2026-08-01T02:05:00Z" }), // Jul 31 in New York
      game({ id: "b", start_time: "2026-07-02T23:05:00Z" }),
      game({ id: "c", start_time: "2026-06-30T23:05:00Z" }),
    ]);
    expect(groups.map((group) => group.key)).toEqual(["2026-07", "2026-06"]);
    // In English, like every other date on the page, whatever the browser's
    // language is.
    expect(groups.map((group) => group.label)).toEqual(["July 2026", "June 2026"]);
    expect(groups[0].games.map((entry) => entry.id)).toEqual(["a", "b"]);
  });

  it("is empty for no games", () => {
    expect(monthGroups([])).toEqual([]);
  });
});

describe("detailLabel", () => {
  it("names the game and how it went", () => {
    expect(detailLabel(final("a", 2, 4), ATH)).toBe(
      "Box score, July 29 vs Boston Red Sox, lost 2–4",
    );
    expect(detailLabel(final("b", 8, 11, { road: true }), ATH)).toBe(
      "Box score, July 29 at Boston Red Sox, lost 8–11",
    );
    expect(detailLabel(final("c", 1, 1, { sport: "soccer" }), ATH)).toBe(
      "Box score, July 29 vs Boston Red Sox, drew 1–1",
    );
  });

  it("leaves a fight's round count out", () => {
    const bout = final("f", 1, 0, {
      road: true,
      sport: "mma",
      fight_result: { method: "ko", detail: null, round: 1, clock: null },
    });
    expect(detailLabel(bout, ATH)).toBe("Box score, July 29 vs Boston Red Sox, won");
  });

  it("still says which game it is with no result", () => {
    expect(detailLabel(final("a", null, null), ATH)).toBe(
      "Box score, July 29 vs Boston Red Sox",
    );
  });
});

describe("followedOpponent", () => {
  const teams = [ATH, ARI, { ...ARI, id: "other", league_id: "nba" }];

  it("knows a stored opponent by its id", () => {
    expect(followedOpponent(side("Arizona", 1, "ari"), teams, ATH)?.id).toBe("ari");
  });

  it("matches an archived opponent by name, within the same league", () => {
    expect(followedOpponent(side("Arizona Diamondbacks"), teams, ATH)?.id).toBe("ari");
    expect(followedOpponent(side("Boston Red Sox"), teams, ATH)).toBeUndefined();
    const elsewhere = { ...ATH, league_id: "epl" };
    expect(followedOpponent(side("Arizona Diamondbacks"), teams, elsewhere)).toBeUndefined();
  });

  it("never marks the team itself", () => {
    expect(followedOpponent(side("Athletics", 1, "ath"), teams, ATH)).toBeUndefined();
    expect(followedOpponent(side("Athletics"), teams, ATH)).toBeUndefined();
  });
});

describe("colorsByName", () => {
  it("collects the colours the loaded games carry, first seen first", () => {
    const boston = { ...side("Boston Red Sox", 4), color: "#BD3039" };
    const colors = colorsByName([
      game({ away: side("Boston Red Sox", 1) }),
      game({ away: boston }),
      game({ away: { ...boston, color: "#000000" } }),
    ]);
    expect(colors).toEqual({ "Boston Red Sox": "#BD3039" });
  });

  it("is empty when no side has a colour", () => {
    expect(colorsByName([game()])).toEqual({});
  });
});

describe("nextUp", () => {
  const TODAY = "2026-07-30";
  const ZONE = "America/Los_Angeles";
  const at = (id: string, start: string, phase: Game["phase"] = "scheduled") =>
    game({ id, start_time: start, phase });

  it("keeps what is live or still to start, in start order, four at most", () => {
    const schedule = [
      at("f5", "2026-08-04T23:10:00Z"),
      at("y", "2026-07-30T02:05:00Z", "final"),
      at("f1", "2026-07-31T02:05:00Z"),
      at("f2", "2026-08-01T02:05:00Z"),
      at("f3", "2026-08-02T01:05:00Z"),
      at("f4", "2026-08-02T20:05:00Z"),
    ];
    expect(nextUp(schedule, [], TODAY, ZONE).map((entry) => entry.id)).toEqual([
      "f1",
      "f2",
      "f3",
      "f4",
    ]);
  });

  it("leaves out postponed and canceled games, and a ghost left scheduled on a past day", () => {
    const schedule = [
      at("ghost", "2026-07-30T02:05:00Z"), // Jul 29 in Los Angeles
      at("ppd", "2026-07-31T02:05:00Z", "postponed"),
      at("off", "2026-08-01T02:05:00Z", "canceled"),
      at("ok", "2026-08-02T02:05:00Z"),
    ];
    expect(nextUp(schedule, [], TODAY, ZONE).map((entry) => entry.id)).toEqual(["ok"]);
  });

  it("keeps a game still in progress from the day before", () => {
    const late = at("late", "2026-07-30T05:40:00Z", "in_progress"); // Jul 29, 10:40 PM
    expect(nextUp([late], [], TODAY, ZONE)).toHaveLength(1);
  });

  it("takes Today's copy of a game: it turns live, then leaves when it is final", () => {
    const schedule = [at("g1", "2026-07-31T02:05:00Z"), at("g2", "2026-08-01T02:05:00Z")];
    const live = nextUp(schedule, [at("g1", "2026-07-31T02:05:00Z", "in_progress")], TODAY, ZONE);
    expect(live[0].phase).toBe("in_progress");
    const over = nextUp(schedule, [at("g1", "2026-07-31T02:05:00Z", "final")], TODAY, ZONE);
    expect(over.map((entry) => entry.id)).toEqual(["g2"]);
  });
});

describe("a game the Today payload does not carry", () => {
  const at = (id: string, phase: Game["phase"], over: Partial<Game> = {}) =>
    game({ id, phase, ...over });

  it("is unwatched while it is in progress in the schedule alone", () => {
    const late = at("late", "in_progress");
    expect(hasUnwatchedLive([late, at("next", "scheduled")], [])).toBe(true);
    // Today polls its own copy: nothing more to do.
    expect(hasUnwatchedLive([late], [at("late", "in_progress")])).toBe(false);
    expect(hasUnwatchedLive([late], [at("late", "final")])).toBe(false);
    expect(hasUnwatchedLive([at("next", "scheduled"), at("done", "final")], [])).toBe(false);
    expect(hasUnwatchedLive([], [])).toBe(false);
  });

  it("is an unlisted final once it ends, wherever that shows", () => {
    const listed = new Set(["old"]);
    // In the team's own schedule: last night's game, which Today has let go.
    expect(
      unlistedFinals([at("late", "final"), at("old", "final"), at("next", "scheduled")], [], "ath", listed),
    ).toEqual(["late"]);
    // In Today: the team's own game only, not another followed team's.
    const others = at("other", "final", { home: side("Detroit Tigers", 1, "det"), away: side("Boston Red Sox", 0) });
    expect(unlistedFinals([], [at("tonight", "final"), others], "ath", listed)).toEqual(["tonight"]);
  });

  it("names each game once, and nothing that is listed or still going", () => {
    const listed = new Set(["a"]);
    const both = at("b", "final");
    expect(unlistedFinals([both, at("a", "final")], [both, at("c", "in_progress")], "ath", listed)).toEqual(["b"]);
    expect(unlistedFinals([at("a", "final")], [at("a", "final")], "ath", listed)).toEqual([]);
  });

  it("names every unlisted final, so one that never arrives cannot hide the next", () => {
    expect(unlistedFinals([at("stuck", "final")], [at("new", "final")], "ath", new Set())).toEqual([
      "new",
      "stuck",
    ]);
  });
});

describe("nextUpNote", () => {
  const home = (id: string) => game({ id });
  const road = (id: string) =>
    game({ id, home: side("Cincinnati Reds"), away: side("Athletics", null, "ath") });

  it("says how many and where", () => {
    expect(nextUpNote([home("a"), home("b"), home("c"), home("d")], ATH, "baseball")).toBe(
      "Next four games, all at home",
    );
    expect(nextUpNote([road("a"), road("b")], ATH, "baseball")).toBe(
      "Next two games, all on the road",
    );
    expect(nextUpNote([home("a"), road("b"), road("c")], ATH, "baseball")).toBe(
      "Next three games, one at home and two on the road",
    );
    expect(nextUpNote([home("a")], ATH, "baseball")).toBe("Next game, at home");
    expect(nextUpNote([road("a")], ATH, "soccer")).toBe("Next match, on the road");
  });

  it("has no home or road for people", () => {
    expect(nextUpNote([home("a"), road("b")], ATH, "tennis")).toBe("Next two matches");
    expect(nextUpNote([home("a")], ATH, "mma")).toBe("Next bout");
  });
});

describe("nextUpDay", () => {
  const ZONE = "America/New_York";

  it("is Tonight for an evening game today, Today for an afternoon one", () => {
    const evening = game({ start_time: "2026-07-30T23:05:00Z" }); // 7:05 PM
    expect(nextUpDay(evening, "2026-07-30", ZONE)).toEqual({
      isToday: true,
      label: "Tonight",
      day: 30,
    });
    const afternoon = game({ start_time: "2026-07-30T17:05:00Z" }); // 1:05 PM
    expect(nextUpDay(afternoon, "2026-07-30", ZONE).label).toBe("Today");
  });

  it("is the weekday and month for any other day", () => {
    const friday = game({ start_time: "2026-07-31T23:05:00Z" });
    expect(nextUpDay(friday, "2026-07-30", ZONE)).toEqual({
      isToday: false,
      label: "Fri · Jul",
      day: 31,
    });
    const august = game({ start_time: "2026-08-01T23:05:00Z" });
    expect(nextUpDay(august, "2026-07-30", ZONE)).toEqual({
      isToday: false,
      label: "Sat · Aug",
      day: 1,
    });
  });

  it("says which game is today by the server's day", () => {
    // 10:05 PM Thursday in Los Angeles is already Friday in New York.
    const late = game({ start_time: "2026-07-31T05:05:00Z" });
    expect(nextUpDay(late, "2026-07-30", "America/Los_Angeles").isToday).toBe(true);
    expect(nextUpDay(late, "2026-07-30", ZONE).isToday).toBe(false);
  });

  it("dates the cell by the viewer's day, as its spoken text and the results rows are", () => {
    // 7:05 PM Friday here (the suite runs in New York) is Saturday morning
    // for a server in Tokyo. The viewer is told Friday the 31st.
    const evening = game({ start_time: "2026-07-31T23:05:00Z" });
    expect(nextUpDay(evening, "2026-07-30", "Asia/Tokyo")).toEqual({
      isToday: false,
      label: "Fri · Jul",
      day: 31,
    });
    // On the server's today it is still the viewer's date under the word.
    const late = game({ start_time: "2026-07-31T05:05:00Z" }); // 1:05 AM Friday here
    expect(nextUpDay(late, "2026-07-30", "America/Los_Angeles")).toEqual({
      isToday: true,
      label: "Today",
      day: 31,
    });
  });
});

describe("figure sizing", () => {
  it("measures a figure in ems of Barlow Condensed Bold, digit by digit", () => {
    expect(figureEms("45–63")).toBeCloseTo(2.19, 2);
    expect(figureEms("18–9–11")).toBeCloseTo(2.51, 2);
    expect(figureEms("W12")).toBeCloseTo(1.42, 2);
    expect(figureEms(".667")).toBeCloseTo(1.52, 2);
  });

  it("is never under the width the browser sets the figure at, and not far over", () => {
    // Measured in the app with the real font, proportional figures.
    const measured: Record<string, number> = {
      "18–9–11": 2.479,
      "45–63": 2.179,
      "40–30–12": 3.312,
      "11–11–11": 2.44,
      "111–111": 2.052,
      "2–6": 1.258,
      W12: 1.415,
      ".667": 1.51,
      "10": 0.737,
    };
    for (const [text, width] of Object.entries(measured)) {
      expect(figureEms(text)).toBeGreaterThanOrEqual(width);
      expect(figureEms(text)).toBeLessThan(width * 1.06);
    }
  });

  it("caps at the design's 80px and shrinks a wider figure with its tile", () => {
    expect(figureSize(2.38)).toBe("min(5rem, 40.3cqi)");
    expect(figureSize(3.26)).toBe("min(5rem, 29.4cqi)");
    expect(figureSize(0)).toBe("5rem");
  });
});
