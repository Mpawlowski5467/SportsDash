import { describe, expect, it } from "vitest";

import type {
  Game,
  GameOdds,
  GameSide,
  GameSummary,
  SportEvent,
  StandingRow,
  Standings,
} from "../../types";
import {
  dayKeyOffset,
  favoriteOf,
  followedOutcome,
  followedTables,
  leadingSide,
  lineScoreOf,
  liveStatus,
  liveWinProbability,
  nextStartNote,
  outcomeForTeam,
  pickHero,
  recordText,
  resultFor,
  showsScore,
  soleFollowedSide,
  standingColumns,
  standingRowFor,
  tableName,
  upcomingTitle,
  weekLine,
  weekStrip,
  yesterdayTitle,
} from "./model";

function side(
  name: string,
  abbreviation: string | null,
  score: number | null = null,
  teamId: string | null = null,
): GameSide {
  return { team_id: teamId, name, abbreviation, logo_url: null, color: null, score };
}

function game(over: Partial<Game> = {}): Game {
  return {
    id: "g",
    league_id: "mlb",
    sport: "baseball",
    home: side("Athletics", "ATH", null, "ath"),
    away: side("Boston Red Sox", "BOS"),
    start_time: "2026-07-30T23:05:00Z", // 7:05 PM in New York (the test zone)
    venue: null,
    series: null,
    fight_result: null,
    broadcasts: [],
    phase: "scheduled",
    period: 0,
    period_label: "",
    clock: null,
    is_intermission: false,
    followed_team_ids: ["ath"],
    ...over,
  };
}

const final = (away: number, home: number, over: Partial<Game> = {}): Game =>
  game({
    phase: "final",
    home: side("Athletics", "ATH", home, "ath"),
    away: side("Boston Red Sox", "BOS", away),
    ...over,
  });

describe("sides and results", () => {
  it("finds the one followed side, and none when both or neither are", () => {
    expect(soleFollowedSide(game())).toBe("home");
    expect(soleFollowedSide(game({ followed_team_ids: [] }))).toBeNull();
    expect(
      soleFollowedSide(
        game({ away: side("Arizona", "ARI", null, "ari"), followed_team_ids: ["ath", "ari"] }),
      ),
    ).toBeNull();
  });

  it("reads the result from the followed team's side", () => {
    expect(followedOutcome(final(2, 4))).toBe("W");
    expect(followedOutcome(final(4, 2))).toBe("L");
    expect(followedOutcome(final(1, 1))).toBe("D");
    expect(followedOutcome(final(4, 2, { followed_team_ids: [] }))).toBeNull();
    expect(outcomeForTeam(final(4, 2), "ath")).toBe("L");
    expect(outcomeForTeam(final(4, 2), "nobody")).toBeNull();
  });

  it("does not call level round counts a drawn fight", () => {
    const bout = (method: "decision" | "draw" | "no_contest", away: number, home: number) =>
      final(away, home, {
        sport: "mma",
        fight_result: { method, detail: null, round: 3, clock: null },
      });
    expect(followedOutcome(bout("decision", 0, 1))).toBe("W");
    expect(followedOutcome(bout("decision", 1, 1))).toBeNull();
    expect(followedOutcome(bout("draw", 1, 1))).toBe("D");
    expect(followedOutcome(bout("no_contest", 0, 1))).toBeNull();
  });

  it("tells a team's win, loss or draw from its own side", () => {
    expect(resultFor(final(3, 4), "ath")).toBe("W");
    expect(resultFor(final(4, 2), "ath")).toBe("L");
    expect(resultFor(final(1, 1, { sport: "soccer" }), "ath")).toBe("D");
    const onTheRoad = final(0, 0, {
      away: side("Athletics", "ATH", 4, "ath"),
      home: side("Boston Red Sox", "BOS", 3),
    });
    expect(resultFor(onTheRoad, "ath")).toBe("W");
  });

  it("has no result without both scores, or for a team not in the game", () => {
    const unscored = final(3, 0, { home: side("Athletics", "ATH", null, "ath") });
    expect(resultFor(unscored, "ath")).toBeNull();
    expect(resultFor(final(3, 4), "someone-else")).toBeNull();
  });

  it("applies the fight rule to a team's result too", () => {
    const fight = (
      method: "decision" | "draw" | "no_contest" | "ko" | null,
      mine: number,
      theirs: number,
    ) =>
      final(theirs, mine, {
        sport: "mma",
        fight_result:
          method === null ? null : { method, detail: null, round: 3, clock: null },
      });
    expect(resultFor(fight("decision", 1, 1), "ath")).toBeNull();
    expect(resultFor(fight("draw", 1, 1), "ath")).toBe("D");
    expect(resultFor(fight("no_contest", 1, 0), "ath")).toBeNull();
    expect(resultFor(fight("ko", 1, 0), "ath")).toBe("W");
    expect(resultFor(fight(null, 0, 1), "ath")).toBe("L");
    expect(resultFor(fight("ko", 1, 0), "someone-else")).toBeNull();
  });

  it("knows who is in front, and when a score means nothing", () => {
    expect(leadingSide(final(3, 4))).toBe("home");
    expect(leadingSide(final(3, 3))).toBeNull();
    expect(leadingSide(game())).toBeNull();
    expect(showsScore(final(3, 4))).toBe(true);
    expect(showsScore(game())).toBe(false);
    expect(showsScore(final(1, 0, { sport: "mma" }))).toBe(false);
  });

  it("gives the hero to a followed team's game before any other", () => {
    const neutral = game({ id: "n", phase: "in_progress", followed_team_ids: [] });
    const mine = game({ id: "m", phase: "in_progress" });
    expect(pickHero([neutral, mine])?.id).toBe("m");
    expect(pickHero([neutral])?.id).toBe("n");
    expect(pickHero([])).toBeNull();
  });
});

describe("days and titles", () => {
  it("moves a day key across a month and a year", () => {
    expect(dayKeyOffset("2026-07-30", -1)).toBe("2026-07-29");
    expect(dayKeyOffset("2026-07-30", 6)).toBe("2026-08-05");
    expect(dayKeyOffset("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("only says tonight when the whole slate is in the evening", () => {
    const evening = game({ start_time: "2026-07-30T23:05:00Z" }); // 7:05 PM
    const afternoon = game({ start_time: "2026-07-30T17:05:00Z" }); // 1:05 PM
    expect(upcomingTitle([evening])).toBe("Tonight");
    expect(upcomingTitle([afternoon, evening])).toBe("Later today");
    expect(upcomingTitle([])).toBe("Later today");
    expect(yesterdayTitle([evening])).toBe("Last night");
    expect(yesterdayTitle([afternoon])).toBe("Yesterday");
  });

  it("counts down to the next start in the sport's own words", () => {
    const start = "2026-07-30T23:05:00Z";
    const at = (iso: string) => new Date(iso);
    expect(nextStartNote([game({ start_time: start })], at("2026-07-30T22:53:00Z"))).toBe(
      "First pitch in 12 minutes",
    );
    expect(nextStartNote([game({ start_time: start })], at("2026-07-30T23:04:00Z"))).toBe(
      "First pitch in 1 minute",
    );
    expect(nextStartNote([game({ start_time: start })], at("2026-07-30T23:06:00Z"))).toBe(
      "First pitch any moment",
    );
    expect(
      nextStartNote([game({ start_time: start, sport: "soccer" })], at("2026-07-30T20:00:00Z")),
    ).toBe("Kickoff at 7:05 PM");
    expect(nextStartNote([game({ phase: "postponed" })], at(start))).toBeNull();
  });
});

function row(over: Partial<StandingRow>): StandingRow {
  return {
    rank: 1,
    team_name: "Team",
    team_id: null,
    logo_url: null,
    abbreviation: null,
    color: null,
    wins: 0,
    losses: 0,
    draws: null,
    points: null,
    goal_diff: null,
    win_pct: null,
    games_back: null,
    ot_losses: null,
    group: null,
    subgroup: null,
    ...over,
  };
}

function table(sport: Standings["sport"], rows: StandingRow[]): Standings {
  return {
    league_id: "x",
    league_name: "Premier League",
    sport,
    season: "2026",
    fetched_at: null,
    rows,
    is_stale: false,
  };
}

describe("standings in words", () => {
  it("writes a record the way each sport does", () => {
    expect(recordText(row({ wins: 45, losses: 63 }), "baseball")).toBe("45–63");
    expect(recordText(row({ wins: 3, losses: 2, draws: 1 }), "soccer")).toBe("3–1–2");
    expect(recordText(row({ wins: 30, losses: 20, ot_losses: 5 }), "hockey")).toBe("30–20–5");
    expect(recordText(row({ wins: 9, losses: 7, draws: 1 }), "football")).toBe("9–7–1");
    expect(recordText(row({ wins: 9, losses: 8, draws: 0 }), "football")).toBe("9–8");
  });

  it("names a division where there is one, else the league", () => {
    const mlb = table("baseball", [
      row({ group: "American League", subgroup: "AL West" }),
      row({ group: "National League", subgroup: "NL West" }),
    ]);
    expect(tableName(mlb.rows[0], mlb)).toBe("AL West");
    const nba = table("basketball", [
      row({ group: "Eastern Conference" }),
      row({ group: "Western Conference" }),
    ]);
    expect(tableName(nba.rows[1], nba)).toBe("Western Conference");
    // A single table's group only repeats the season's name.
    const epl = table("soccer", [row({ group: "2026-27 English Premier League" })]);
    expect(tableName(epl.rows[0], epl)).toBe("Premier League");
  });

  it("finds a side's row by id when followed, else by name", () => {
    const standings = table("baseball", [
      row({ team_name: "Athletics", team_id: "ath", rank: 4 }),
      row({ team_name: "Boston Red Sox", rank: 3 }),
    ]);
    expect(standingRowFor(standings, side("A's", "ATH", null, "ath"))?.rank).toBe(4);
    expect(standingRowFor(standings, side("Boston Red Sox", "BOS"))?.rank).toBe(3);
    expect(standingRowFor(standings, side("Nobody", null))).toBeUndefined();
    expect(standingRowFor(undefined, side("Athletics", "ATH"))).toBeUndefined();
  });

  it("offers four columns at most, and none for sports without a team table", () => {
    expect(standingColumns("baseball")?.map((c) => c.label)).toEqual(["W", "L", "PCT", "GB"]);
    expect(standingColumns("soccer")?.map((c) => c.label)).toEqual(["W", "D", "L", "Pts"]);
    expect(standingColumns("hockey")?.map((c) => c.label)).toEqual(["W", "L", "OTL", "Pts"]);
    expect(standingColumns("tennis")).toBeNull();
    expect(standingColumns("mma")).toBeNull();
    const [, , pct, gb] = standingColumns("baseball") ?? [];
    expect(pct.value(row({ win_pct: 0.417 }))).toBe(".417");
    expect(pct.value(row({ win_pct: 1 }))).toBe("1.000");
    expect(gb.value(row({ games_back: 0 }))).toBe("—");
    expect(gb.value(row({ games_back: 2.5 }))).toBe("2.5");
    expect(gb.value(row({ games_back: 10 }))).toBe("10");
  });

  it("shows a whole division, but only a window of a long single table", () => {
    const division = table(
      "baseball",
      [1, 2, 3, 4, 5].map((rank) =>
        row({
          rank,
          team_name: `W${rank}`,
          team_id: rank === 4 ? "ath" : null,
          group: "AL",
          subgroup: "AL West",
        }),
      ).concat([row({ rank: 1, team_name: "E1", group: "AL", subgroup: "AL East" })]),
    );
    const [west, ...rest] = followedTables(division);
    expect(rest).toEqual([]); // AL East holds no followed team
    expect(west.title).toBe("AL West");
    expect(west.rows.map((r) => r.rank)).toEqual([1, 2, 3, 4, 5]);
    expect([west.from, west.to, west.total]).toEqual([1, 5, 5]);

    const long = (followedRank: number) =>
      followedTables(
        table(
          "soccer",
          Array.from({ length: 20 }, (_, i) =>
            row({ rank: i + 1, team_name: `T${i + 1}`, team_id: i + 1 === followedRank ? "che" : null }),
          ),
        ),
      )[0];
    expect(long(5).rows.map((r) => r.rank)).toEqual([2, 3, 4, 5, 6, 7, 8]);
    expect(long(1).rows.map((r) => r.rank)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(long(20).rows.map((r) => r.rank)).toEqual([14, 15, 16, 17, 18, 19, 20]);
    expect([long(5).from, long(5).to, long(5).total]).toEqual([2, 8, 20]);
  });

  it("keeps a second followed team that falls outside the window", () => {
    const [slice] = followedTables(
      table(
        "soccer",
        Array.from({ length: 20 }, (_, i) =>
          row({
            rank: i + 1,
            team_name: `T${i + 1}`,
            team_id: i + 1 === 3 ? "a" : i + 1 === 18 ? "b" : null,
          }),
        ),
      ),
    );
    // The window sits round the first followed team; the other is added.
    expect(slice.rows.map((r) => r.rank)).toEqual([1, 2, 3, 4, 5, 6, 7, 18]);
    expect(slice.total).toBe(20);
  });

  it("has no table for a league none of your teams are in", () => {
    expect(followedTables(table("soccer", [row({ rank: 1 }), row({ rank: 2 })]))).toEqual([]);
  });
});

function odds(over: Partial<GameOdds>): GameOdds {
  return {
    provider: null,
    details: null,
    home_moneyline: null,
    away_moneyline: null,
    spread: null,
    over_under: null,
    home_win_pct: null,
    away_win_pct: null,
    ...over,
  };
}

function summary(over: Partial<GameSummary>): GameSummary {
  return {
    game_id: "g",
    periods: [],
    performers: [],
    team_stats: [],
    goals: [],
    home_total: null,
    away_total: null,
    win_probability: [],
    plays: [],
    clips: [],
    ...over,
  };
}

describe("odds and win probability", () => {
  it("names the favoured side with its share, and gives the bar both halves", () => {
    const favorite = favoriteOf(game(), odds({ home_win_pct: 58, away_win_pct: 42 }));
    expect(favorite?.label).toBe("ATH 58%");
    expect(favorite?.split).toEqual({ away: 42, home: 58 });
  });

  it("falls back to the favoured moneyline, with no bar to draw", () => {
    const favorite = favoriteOf(game(), odds({ home_moneyline: 130, away_moneyline: -150 }));
    expect(favorite?.label).toBe("BOS -150");
    expect(favorite?.split).toBeNull();
    expect(favoriteOf(game(), odds({ home_moneyline: 120 }))?.label).toBe("ATH +120");
  });

  it("has nothing to say without a line, or once the game is over", () => {
    expect(favoriteOf(game(), odds({}))).toBeNull();
    expect(favoriteOf(game(), undefined)).toBeNull();
    expect(favoriteOf(final(1, 2), odds({ home_win_pct: 58, away_win_pct: 42 }))).toBeNull();
  });

  it("takes the live chance from the end of the series, and never invents one", () => {
    expect(liveWinProbability(summary({ win_probability: [50, 44, 64] }))).toEqual({
      away: 36,
      home: 64,
    });
    expect(liveWinProbability(summary({ win_probability: [] }))).toBeNull();
    expect(liveWinProbability(null)).toBeNull();
    expect(liveWinProbability(summary({ win_probability: [140] }))).toEqual({ away: 0, home: 100 });
  });
});

describe("line score", () => {
  const five = [
    { label: "1", away: 0, home: 1 },
    { label: "2", away: 1, home: 0 },
    { label: "3", away: 0, home: 0 },
    { label: "4", away: 2, home: 3 },
    { label: "5", away: 0, home: 0 },
  ];
  const liveGame = (label: string) =>
    game({
      phase: "in_progress",
      period: 5,
      period_label: label,
      home: side("Athletics", "ATH", 4, "ath"),
      away: side("Boston Red Sox", "BOS", 3),
    });

  it("pads baseball to nine innings and marks the one being played", () => {
    const line = lineScoreOf(liveGame("Bot 5"), summary({ periods: five, home_total: 4, away_total: 3 }));
    expect(line?.columns.map((c) => c.label)).toEqual(["1", "2", "3", "4", "5", "6", "7", "8", "9"]);
    expect(line?.columns.filter((c) => c.current).map((c) => c.label)).toEqual(["5"]);
    expect(line?.columns[5]).toEqual({ label: "6", away: null, home: null, current: false });
    expect([line?.totalLabel, line?.awayTotal, line?.homeTotal]).toEqual(["R", 3, 4]);
  });

  it("blanks the home half that has not been batted, instead of showing 0", () => {
    const top = lineScoreOf(liveGame("Top 5"), summary({ periods: five }));
    expect(top?.columns[4]).toMatchObject({ away: 0, home: null });
    const bottom = lineScoreOf(liveGame("Bot 5"), summary({ periods: five }));
    expect(bottom?.columns[4]).toMatchObject({ away: 0, home: 0 });
    // A finished game shows what was scored, zeros included.
    const done = lineScoreOf(final(3, 4, { period_label: "Top 5" }), summary({ periods: five }));
    expect(done?.columns[4]).toMatchObject({ home: 0, current: false });
  });

  it("uses the periods as sent for other sports, with a T total", () => {
    const line = lineScoreOf(
      game({ sport: "basketball", phase: "in_progress", period: 2 }),
      summary({ periods: [{ label: "Q1", away: 28, home: 31 }, { label: "Q2", away: 12, home: 9 }] }),
    );
    expect(line?.columns.map((c) => c.label)).toEqual(["Q1", "Q2"]);
    expect(line?.totalLabel).toBe("T");
    expect(line?.columns[1].current).toBe(true);
  });

  it("is absent when the provider sent no periods", () => {
    expect(lineScoreOf(game(), null)).toBeNull();
    expect(lineScoreOf(game(), summary({ periods: [] }))).toBeNull();
  });

  it("writes the live status out, with the clock beside it", () => {
    expect(liveStatus(liveGame("Bot 5"))).toEqual({ headline: "Bottom 5th", detail: null });
    expect(
      liveStatus(game({ sport: "soccer", period_label: "2nd Half", clock: "71'" })),
    ).toEqual({ headline: "2nd Half", detail: "71'" });
    expect(liveStatus(game({ sport: "soccer", is_intermission: true })).headline).toBe("Half time");
    // At a break the clock is stopped and is not shown beside the label.
    expect(
      liveStatus(
        game({ sport: "soccer", period: 1, period_label: "1st Half", clock: "45'", is_intermission: true }),
      ),
    ).toEqual({ headline: "Half time", detail: null });
    expect(liveStatus(game({ sport: "soccer" })).headline).toBe("Live");
  });
});

describe("the week strip", () => {
  it("writes a line from the followed side's point of view", () => {
    expect(weekLine(game())).toBe("ATH vs BOS");
    expect(
      weekLine(
        game({
          home: side("Cincinnati Reds", "CIN"),
          away: side("Athletics", "ATH", null, "ath"),
        }),
      ),
    ).toBe("ATH at CIN");
    expect(weekLine(game({ followed_team_ids: [] }))).toBe("BOS at ATH");
    expect(
      weekLine(
        game({
          sport: "tennis",
          home: side("Carlos Alcaraz", "C. Alcar"),
          away: side("Jannik Sinner", "J. Sinne", null, "sin"),
          followed_team_ids: ["sin"],
        }),
      ),
    ).toBe("Sinner vs Alcaraz");
  });

  const at = (day: string, id: string, over: Partial<Game> = {}) =>
    game({ id, start_time: `${day}T23:05:00Z`, ...over });

  it("lays out seven days from today, each with its games in start order", () => {
    const days = weekStrip(
      "2026-07-30",
      [at("2026-08-01", "sat"), at("2026-07-30", "thu"), at("2026-08-06", "next-week")],
      [],
      "America/New_York",
    );
    expect(days.map((d) => d.key)).toEqual([
      "2026-07-30",
      "2026-07-31",
      "2026-08-01",
      "2026-08-02",
      "2026-08-03",
      "2026-08-04",
      "2026-08-05",
    ]);
    expect(days[0].isToday).toBe(true);
    expect(days.map((d) => d.lines.length)).toEqual([1, 0, 1, 0, 0, 0, 0]);
  });

  it("puts a game on the server's day, not the browser's", () => {
    // 03:05 UTC on the 31st is still the 30th in Los Angeles.
    const late = game({ start_time: "2026-07-31T03:05:00Z" });
    expect(weekStrip("2026-07-30", [late], [], "America/Los_Angeles")[0].lines).toHaveLength(1);
    expect(weekStrip("2026-07-30", [late], [], "Europe/London")[0].lines).toHaveLength(0);
  });

  it("caps a busy day and counts the rest", () => {
    const busy = Array.from({ length: 7 }, (_, i) => at("2026-07-30", `g${i}`));
    const [today] = weekStrip("2026-07-30", busy, [], "America/New_York");
    expect(today.lines).toHaveLength(4);
    expect(today.more).toBe(3);
  });

  it("leaves out games that are off, and runs an event across its days", () => {
    const event: SportEvent = {
      id: "e",
      league_id: "pga",
      sport: "golf",
      name: "3M Open",
      start_time: "2026-07-30T13:00:00Z",
      end_time: "2026-08-02T22:00:00Z",
      phase: "in_progress",
      round_label: "Round 1",
      venue: null,
      followed_player_ids: [],
      leaderboard: [],
    };
    const days = weekStrip(
      "2026-07-30",
      [at("2026-07-31", "ppd", { phase: "postponed" })],
      [event],
      "America/New_York",
    );
    expect(days.map((d) => d.lines)).toEqual([
      ["3M Open"],
      ["3M Open"],
      ["3M Open"],
      ["3M Open"],
      [],
      [],
      [],
    ]);
  });
});
