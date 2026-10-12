import { describe, expect, it } from "vitest";

import type {
  BracketSeries,
  Game,
  GameSide,
  Scorers,
  StandingRow,
  Standings,
  StatLeader,
  StatLeaders,
} from "../../types";
import {
  boardNote,
  bracketKind,
  echoesLeagueName,
  expectsOneTable,
  fixtureDay,
  formLabel,
  formatGap,
  formatStat,
  gapBehind,
  halve,
  hasTeamTable,
  heroStats,
  inTable,
  isRankingTable,
  layoutStandings,
  leadNote,
  leadersBoard,
  leagueColumns,
  liveSeasonKey,
  outcomeSummary,
  raceUnit,
  recordRem,
  roundOf,
  scoreFor,
  shareOfLeader,
  shortSlot,
  splitRound,
  statColumnRem,
  teamLookup,
  teamOutcomes,
  topOfBoard,
  upcomingFixtures,
  venueSide,
  venueSummary,
  withoutLiveSeason,
} from "./model";

function row(
  rank: number,
  name: string,
  over: Partial<StandingRow> = {},
): StandingRow {
  return {
    rank,
    team_name: name,
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

function standings(rows: StandingRow[], over: Partial<Standings> = {}): Standings {
  return {
    league_id: "mlb",
    league_name: "MLB",
    sport: "baseball",
    season: "2026",
    fetched_at: null,
    rows,
    is_stale: false,
    ...over,
  };
}

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

const followed = (r: StandingRow) => r.team_id !== null;
const nobody = () => false;

describe("words", () => {
  it("formats a gap without a trailing zero", () => {
    expect(formatGap(10)).toBe("10");
    expect(formatGap(2.5)).toBe("2.5");
  });
});

describe("the season picker", () => {
  it("files the season in progress under the key its sport uses", () => {
    // Soccer by the year the season starts in, in either half of it.
    expect(liveSeasonKey("2026-27 English Premier League", "soccer", 2026)).toBe("2026");
    expect(liveSeasonKey("2026-27 English Premier League", "soccer", 2027)).toBe("2026");
    expect(liveSeasonKey("2026/2027 Turkish Super Lig", "soccer", 2026)).toBe("2026");
    // Basketball and hockey by the year it ends in.
    expect(liveSeasonKey("2026-27", "basketball", 2026)).toBe("2027");
    expect(liveSeasonKey("2026-27", "hockey", 2027)).toBe("2027");
    // One year is one key.
    expect(liveSeasonKey("2026", "baseball", 2026)).toBe("2026");
  });

  it("names no key until the live table has said which season it is", () => {
    // Not the calendar year: that would hide a real past season.
    expect(liveSeasonKey(undefined, "soccer", 2026)).toBeNull();
    expect(liveSeasonKey(null, "basketball", 2026)).toBeNull();
    expect(liveSeasonKey("", "baseball", 2026)).toBeNull();
    expect(liveSeasonKey("Regular Season", "hockey", 2026)).toBeNull();
  });

  const choices = (...ids: string[]) => ids.map((id) => ({ id, label: id }));
  const ids = (list: { id: string }[]) => list.map((choice) => choice.id);

  it("does not list the season in progress a second time", () => {
    // Premier League in October 2026: key 2026 is the live 2026-27 season.
    const autumn = choices("current", "2026", "2025", "2024");
    const epl = liveSeasonKey("2026-27 English Premier League", "soccer", 2026);
    expect(ids(withoutLiveSeason(autumn, epl, "current"))).toEqual(["current", "2025", "2024"]);
    // The NBA at the same moment: key 2026 is last season (2025-26) and stays.
    const nba = liveSeasonKey("2026-27", "basketball", 2026);
    expect(ids(withoutLiveSeason(autumn, nba, "current"))).toEqual(["current", "2026", "2025", "2024"]);

    // March 2027, the same seasons still in progress.
    const spring = choices("current", "2027", "2026", "2025");
    expect(
      ids(withoutLiveSeason(spring, liveSeasonKey("2026-27 English Premier League", "soccer", 2027), "current")),
    ).toEqual(["current", "2027", "2025"]);
    expect(
      ids(withoutLiveSeason(spring, liveSeasonKey("2026-27", "basketball", 2027), "current")),
    ).toEqual(["current", "2026", "2025"]);
  });

  it("keeps every key while the live season is not known", () => {
    const all = choices("current", "2026", "2025");
    expect(ids(withoutLiveSeason(all, null, "current"))).toEqual(["current", "2026", "2025"]);
  });

  it("keeps the live season's key while it is the one picked", () => {
    const all = choices("current", "2026", "2025");
    expect(ids(withoutLiveSeason(all, "2026", "2026"))).toEqual(["current", "2026", "2025"]);
    expect(ids(withoutLiveSeason(all, "2026", "2025"))).toEqual(["current", "2025"]);
  });
});

describe("echoesLeagueName", () => {
  it("spots a group label that only restates the league", () => {
    expect(echoesLeagueName("2026-27 English Premier League", "Premier League")).toBe(true);
    expect(echoesLeagueName("Eastern Conference", "NBA")).toBe(false);
    expect(echoesLeagueName("AL West", "MLB")).toBe(false);
  });

  it("never matches on a league name with no letters in it", () => {
    expect(echoesLeagueName("Group A", "")).toBe(false);
  });
});

describe("layoutStandings", () => {
  const mlb = standings([
    row(1, "Texas Rangers", { group: "American League", subgroup: "AL West" }),
    row(2, "Athletics", { group: "American League", subgroup: "AL West", team_id: "ath" }),
    row(1, "Tampa Bay Rays", { group: "American League", subgroup: "AL East" }),
    row(1, "Los Angeles Dodgers", { group: "National League", subgroup: "NL West" }),
    row(2, "Arizona Diamondbacks", { group: "National League", subgroup: "NL West", team_id: "ari" }),
  ]);

  it("shows one conference at a time when there are divisions", () => {
    const layout = layoutStandings(mlb, "National League", followed);
    expect(layout.groups).toEqual(["American League", "National League"]);
    expect(layout.active).toBe("National League");
    expect(layout.tables.map((table) => table.title)).toEqual(["NL West"]);
    expect(layout.leaderWord).toBe("Division leader");
  });

  it("opens on the conference that holds a followed team", () => {
    const nlOnly = standings(
      mlb.rows.map((r) => (r.team_id === "ath" ? { ...r, team_id: null } : r)),
    );
    expect(layoutStandings(nlOnly, null, followed).active).toBe("National League");
    expect(layoutStandings(mlb, null, followed).active).toBe("American League");
    expect(layoutStandings(mlb, null, nobody).active).toBe("American League");
  });

  it("ignores a picked conference that belongs to another league", () => {
    const layout = layoutStandings(mlb, "Eastern Conference", followed);
    expect(layout.active).toBe("American League");
    expect(layout.tables.map((table) => table.title)).toEqual(["AL West", "AL East"]);
  });

  it("orders each table by rank whatever order the rows came in", () => {
    const shuffled = standings([
      row(2, "B", { group: "X", subgroup: "X1" }),
      row(1, "A", { group: "X", subgroup: "X1" }),
      row(1, "C", { group: "Y", subgroup: "Y1" }),
    ]);
    const [table] = layoutStandings(shuffled, "X", nobody).tables;
    expect(table.rows.map((r) => r.team_name)).toEqual(["A", "B"]);
  });

  it("has no toggle for one conference with divisions", () => {
    const one = standings([
      row(1, "A", { group: "Only", subgroup: "North" }),
      row(1, "B", { group: "Only", subgroup: "South" }),
    ]);
    const layout = layoutStandings(one, null, nobody);
    expect(layout.groups).toEqual([]);
    expect(layout.active).toBeNull();
    expect(layout.tables.map((table) => table.title)).toEqual(["North", "South"]);
  });

  it("shows every flat group at once, and names its leaders by what the groups are", () => {
    const cup = standings(
      [
        row(1, "Mexico", { group: "Group A" }),
        row(2, "South Korea", { group: "Group A" }),
        row(1, "Canada", { group: "Group B" }),
      ],
      { league_name: "FIFA World Cup", sport: "soccer" },
    );
    const layout = layoutStandings(cup, "Group B", nobody);
    expect(layout.groups).toEqual([]);
    expect(layout.tables.map((table) => table.title)).toEqual(["Group A", "Group B"]);
    expect(layout.leaderWord).toBe("Group leader");

    const conferences = standings([
      row(1, "Boston Celtics", { group: "Eastern Conference" }),
      row(1, "Oklahoma City Thunder", { group: "Western Conference" }),
    ]);
    expect(layoutStandings(conferences, null, nobody).leaderWord).toBe("Conference leader");

    const other = standings([row(1, "A", { group: "Apertura" }), row(1, "B", { group: "Clausura" })]);
    expect(layoutStandings(other, null, nobody).leaderWord).toBe("Table leader");
  });

  it("titles a single table with the league, not the provider's echo of it", () => {
    const epl = standings(
      [
        row(1, "Manchester City", { group: "2026-27 English Premier League" }),
        row(2, "Arsenal", { group: "2026-27 English Premier League" }),
      ],
      { league_name: "Premier League", sport: "soccer" },
    );
    const layout = layoutStandings(epl, null, nobody);
    expect(layout.tables).toHaveLength(1);
    expect(layout.tables[0].title).toBe("Premier League");
    expect(layout.leaderWord).toBe("League leader");
    expect(layout.groups).toEqual([]);

    const flat = standings([row(1, "A"), row(2, "B")]);
    expect(layoutStandings(flat, null, nobody).tables[0].title).toBe("MLB");
  });

  it("titles a single table with the league when the provider's label does not echo it", () => {
    // Labels as the provider sends them for leagues the catalog names
    // otherwise: the season's sentence, never a table's name.
    const cases: [league: string, group: string][] = [
      ["Brasileirão Série A", "2026"],
      ["EFL Championship", "2026-27 English League Championship"],
      ["Liga Portugal", "2026-2027 Portuguese Liga"],
      ["Süper Lig", "2026/2027 Turkish Super Lig"],
    ];
    for (const [league, group] of cases) {
      const single = standings([row(1, "A", { group }), row(2, "B", { group })], {
        league_name: league,
        sport: "soccer",
      });
      const layout = layoutStandings(single, null, nobody);
      expect(layout.tables.map((table) => table.title)).toEqual([league]);
      expect(layout.tables[0].rows.map((r) => r.team_name)).toEqual(["A", "B"]);
      expect(layout.leaderWord).toBe("League leader");
    }
  });

  it("still names several flat tables by their own labels", () => {
    const split = standings(
      [row(1, "A", { group: "2026 Apertura" }), row(1, "B", { group: "2026 Clausura" })],
      { league_name: "Liga MX", sport: "soccer" },
    );
    expect(layoutStandings(split, null, nobody).tables.map((table) => table.title)).toEqual([
      "2026 Apertura",
      "2026 Clausura",
    ]);
  });
});

describe("expectsOneTable", () => {
  it("guesses one table for everything but the sports with divisions", () => {
    expect(expectsOneTable("soccer")).toBe(true);
    expect(expectsOneTable("volleyball")).toBe(true);
    expect(expectsOneTable("tennis")).toBe(true);
    expect(expectsOneTable("baseball")).toBe(false);
    expect(expectsOneTable("basketball")).toBe(false);
    expect(expectsOneTable("hockey")).toBe(false);
    expect(expectsOneTable("football")).toBe(false);
  });
});

describe("leagueColumns", () => {
  const labels = (sport: Parameters<typeof leagueColumns>[0]) =>
    leagueColumns(sport).map((column) => column.label);

  it("keeps each sport's own full set", () => {
    expect(labels("baseball")).toEqual(["W", "L", "PCT", "GB"]);
    expect(labels("basketball")).toEqual(["W", "L", "PCT", "GB"]);
    expect(labels("soccer")).toEqual(["W", "D", "L", "GD", "Pts"]);
    expect(labels("hockey")).toEqual(["W", "L", "OTL", "Pts"]);
    expect(labels("football")).toEqual(["W", "L", "T", "PCT"]);
    expect(labels("volleyball")).toEqual(["W", "L", "Pts"]);
    expect(labels("tennis")).toEqual(["Pts"]);
  });

  it("falls back to wins, losses, percentage and games back", () => {
    expect(labels("golf")).toEqual(["W", "L", "PCT", "GB"]);
    expect(labels("mma")).toEqual(["W", "L", "PCT", "GB"]);
  });

  it("marks exactly one column as the one the table is ranked by", () => {
    for (const sport of ["baseball", "soccer", "hockey", "football", "volleyball", "tennis"] as const) {
      expect(leagueColumns(sport).filter((column) => column.ranked)).toHaveLength(1);
    }
    expect(leagueColumns("soccer").find((column) => column.ranked)?.label).toBe("Pts");
    expect(leagueColumns("football").find((column) => column.ranked)?.label).toBe("PCT");
  });

  it("signs a goal difference and dashes a missing one", () => {
    const gd = leagueColumns("soccer").find((column) => column.key === "gd");
    expect(gd?.value(row(1, "A", { goal_diff: 13 }))).toBe("+13");
    expect(gd?.value(row(1, "A", { goal_diff: -4 }))).toBe("-4");
    expect(gd?.value(row(1, "A", { goal_diff: 0 }))).toBe("0");
    expect(gd?.value(row(1, "A"))).toBe("—");
  });

  it("gives every column a width, and a ranking's points room for thousands", () => {
    for (const column of leagueColumns("soccer")) expect(column.width).toBeGreaterThan(0);
    const [points] = leagueColumns("tennis");
    expect(points.width).toBeGreaterThan(leagueColumns("soccer").at(-1)?.width ?? 0);
    expect(points.value(row(1, "Jannik Sinner", { points: 11830 }))).toBe("11830");
  });

  it("knows which sports are rankings and which have a team table", () => {
    expect(isRankingTable("tennis")).toBe(true);
    expect(isRankingTable("mma")).toBe(true);
    expect(isRankingTable("soccer")).toBe(false);
    expect(hasTeamTable("baseball")).toBe(true);
    expect(hasTeamTable("volleyball")).toBe(true);
    expect(hasTeamTable("tennis")).toBe(false);
    expect(hasTeamTable("golf")).toBe(false);
  });
});

describe("gapBehind", () => {
  it("counts each sport's race in its own unit", () => {
    expect(raceUnit("baseball")).toBe("games");
    expect(raceUnit("football")).toBe("games");
    expect(raceUnit("soccer")).toBe("points");
    expect(raceUnit("hockey")).toBe("points");
    expect(raceUnit("tennis")).toBeNull();
  });

  it("uses the provider's games back when it is measured from this leader", () => {
    const leader = row(1, "A", { wins: 62, losses: 46, games_back: 0 });
    expect(gapBehind(row(2, "B", { wins: 60, losses: 49, games_back: 2.5 }), leader, "baseball")).toBe(2.5);
  });

  it("works games back out from the records when the figure is missing", () => {
    const leader = row(1, "A", { wins: 62, losses: 46 });
    expect(gapBehind(row(2, "B", { wins: 60, losses: 49 }), leader, "baseball")).toBe(2.5);
    // Football has no games-back figure at all.
    expect(
      gapBehind(row(2, "B", { wins: 9, losses: 4, draws: 1 }), row(1, "A", { wins: 11, losses: 3 }), "football"),
    ).toBe(1.5);
  });

  it("ignores a provider figure measured from some other leader", () => {
    // Both figures are against a conference leader, not this division's.
    const leader = row(1, "A", { wins: 55, losses: 53, games_back: 7 });
    const second = row(2, "B", { wins: 54, losses: 54, games_back: 8 });
    expect(gapBehind(second, leader, "baseball")).toBe(1);
  });

  it("is a plain points difference for the sports ranked on points", () => {
    const leader = row(1, "A", { points: 15 });
    expect(gapBehind(row(5, "E", { points: 10 }), leader, "soccer")).toBe(5);
    expect(gapBehind(row(5, "E"), leader, "soccer")).toBeNull();
  });

  it("has nothing to say for a ranking", () => {
    expect(gapBehind(row(2, "B", { points: 9000 }), row(1, "A", { points: 11000 }), "tennis")).toBeNull();
  });
});

describe("leadNote", () => {
  const west = [
    row(1, "Texas Rangers", { wins: 55, losses: 53, games_back: 0 }),
    row(2, "Houston Astros", { wins: 54, losses: 54, games_back: 1 }),
    row(3, "Seattle Mariners", { wins: 53, losses: 56, games_back: 2.5 }),
    row(4, "Athletics", { wins: 45, losses: 63, games_back: 10, team_id: "ath" }),
  ];

  it("says who is ahead and by how much, and where the followed team is", () => {
    expect(leadNote(west, "baseball", followed)).toBe(
      "Texas Rangers ahead by 1 game · Athletics 10 back",
    );
    expect(leadNote(west.slice(0, 3), "baseball", followed)).toBe(
      "Texas Rangers ahead by 1 game",
    );
  });

  it("uses the plural for anything but exactly one", () => {
    const east = [
      row(1, "Tampa Bay Rays", { wins: 62, losses: 46 }),
      row(2, "New York Yankees", { wins: 60, losses: 49 }),
    ];
    expect(leadNote(east, "baseball", nobody)).toBe("Tampa Bay Rays ahead by 2.5 games");
  });

  it("does not repeat a followed leader", () => {
    const rows = [
      row(1, "Athletics", { wins: 60, losses: 40, team_id: "ath" }),
      row(2, "Texas Rangers", { wins: 55, losses: 45 }),
    ];
    expect(leadNote(rows, "baseball", followed)).toBe("Athletics ahead by 5 games");
  });

  it("is a points gap for the sports ranked on points", () => {
    const rows = [
      row(1, "Arsenal", { points: 17 }),
      row(2, "Liverpool", { points: 15 }),
      row(5, "Chelsea", { points: 10, team_id: "che" }),
    ];
    expect(leadNote(rows, "soccer", followed)).toBe(
      "Arsenal ahead by 2 points · Chelsea 7 back",
    );
    expect(leadNote([row(1, "A", { points: 3 }), row(2, "B", { points: 2 })], "hockey", nobody)).toBe(
      "A ahead by 1 point",
    );
  });

  it("calls a tie a tie", () => {
    const rows = [
      row(1, "Manchester City", { points: 15 }),
      row(2, "Arsenal", { points: 15 }),
      row(5, "Chelsea", { points: 10, team_id: "che" }),
    ];
    expect(leadNote(rows, "soccer", followed)).toBe(
      "Manchester City and Arsenal level on 15 points · Chelsea 5 back",
    );
    const level = [
      row(1, "A", { wins: 10, losses: 5 }),
      row(2, "B", { wins: 10, losses: 5, team_id: "b" }),
      row(3, "C", { wins: 10, losses: 5, team_id: "c" }),
    ];
    expect(leadNote(level, "baseball", followed)).toBe("A and B level · C level");
  });

  it("leaves out what the payload cannot support", () => {
    // Ranked first on percentage with fewer games played: not "ahead by".
    const odd = [
      row(1, "A", { wins: 3, losses: 0 }),
      row(2, "B", { wins: 5, losses: 1 }),
    ];
    expect(leadNote(odd, "basketball", nobody)).toBeNull();
    // No points sent.
    expect(leadNote([row(1, "A"), row(2, "B")], "soccer", nobody)).toBeNull();
    // A ranking is not a race, and one row is not one either.
    expect(leadNote([row(1, "A", { points: 9 }), row(2, "B", { points: 8 })], "tennis", nobody)).toBeNull();
    expect(leadNote([row(1, "A", { points: 9 })], "soccer", nobody)).toBeNull();
    expect(leadNote([], "soccer", nobody)).toBeNull();
  });
});

describe("teamLookup", () => {
  const lookup = teamLookup([
    [
      { name: "Houston Astros", abbreviation: "HOU", color: null },
      { name: "Athletics", abbreviation: "ATH", color: null },
      { name: "No Code FC" },
    ],
    [{ name: "Athletics", abbreviation: "ATH", color: "#EFB21E" }],
    [
      { name: "Houston Astros", abbreviation: "HOU", color: "#002D62" },
      { name: "No Code FC", abbreviation: "NCF", color: "" },
      { name: "Catalog Only", abbreviation: "CAT", color: "#123456" },
    ],
  ]);

  it("merges what each payload knows about a team", () => {
    expect(lookup("Houston Astros")).toEqual({
      name: "Houston Astros",
      abbreviation: "HOU",
      color: "#002D62",
    });
    expect(lookup("Athletics")?.color).toBe("#EFB21E");
    expect(lookup("No Code FC")).toEqual({ name: "No Code FC", abbreviation: "NCF", color: null });
  });

  it("finds a team by name whatever the case or spacing", () => {
    expect(lookup("  houston   astros ")?.abbreviation).toBe("HOU");
  });

  it("finds a team by its code, given beside the name or as the name", () => {
    expect(lookup("Astros", "hou")?.name).toBe("Houston Astros");
    expect(lookup("HOU")?.color).toBe("#002D62");
    expect(lookup("CAT")?.name).toBe("Catalog Only");
  });

  it("returns null for a team no payload describes", () => {
    expect(lookup("Somewhere Athletic")).toBeNull();
    expect(lookup("Somewhere Athletic", "SAT")).toBeNull();
    expect(teamLookup([])("Anything")).toBeNull();
  });

  it("lets an earlier source keep what it already said", () => {
    const first = teamLookup([
      [{ name: "A", abbreviation: "AAA", color: "#111111" }],
      [{ name: "A", abbreviation: "ZZZ", color: "#999999" }],
    ]);
    expect(first("A")).toEqual({ name: "A", abbreviation: "AAA", color: "#111111" });
  });
});

function leader(over: Partial<StatLeader> = {}): StatLeader {
  return {
    rank: 1,
    player_id: "p1",
    name: "Yordan Alvarez",
    position: "DH",
    photo_url: null,
    team_id: "",
    team_name: "HOU",
    team_logo_url: null,
    team_color: null,
    value: 35,
    stat_label: "HR",
    detail: "",
    highlighted: false,
    followed: false,
    ...over,
  };
}

function leaders(rows: StatLeader[], over: Partial<StatLeaders> = {}): StatLeaders {
  return { league_id: "mlb", league_name: "MLB", sport: "baseball", stat_label: "HR", rows, ...over };
}

const noScorers: Scorers = { league_id: "mlb", league_name: "MLB", games_counted: 0, rows: [] };

describe("leadersBoard", () => {
  it("is null until there is a row from either source", () => {
    expect(leadersBoard(undefined, undefined)).toBeNull();
    expect(leadersBoard(noScorers, leaders([]))).toBeNull();
  });

  it("names a league-wide board after its stat and its league", () => {
    const board = leadersBoard(noScorers, leaders([leader(), leader({ rank: 2, player_id: "p2", value: 33 })]));
    expect(board?.kind).toBe("stat");
    expect(board?.title).toBe("Home run leaders");
    expect(board?.unit).toBe("HR");
    expect(board?.leagueWide).toBe(true);
    expect(board?.source).toBe("MLB");
    expect(board?.photos).toBe(true);
    expect(board?.rows[0]).toMatchObject({ name: "Yordan Alvarez", team: "HOU", teamId: null, detail: null });
  });

  it("does not call a roster-built board the league's", () => {
    const board = leadersBoard(
      noScorers,
      leaders(
        [leader({ team_id: "che", team_name: "Chelsea", stat_label: "G", value: 3, detail: "3 G · 3 A" })],
        { sport: "soccer", stat_label: "G", league_name: "Premier League" },
      ),
    );
    expect(board?.leagueWide).toBe(false);
    expect(board?.source).toBe("Your teams' rosters");
    expect(board?.title).toBe("G leaders");
    expect(board?.rows[0]).toMatchObject({ teamId: "che", detail: "3 G · 3 A" });
  });

  it("falls back to a plain title when the unit is unknown or empty", () => {
    expect(leadersBoard(noScorers, leaders([leader()], { stat_label: "" }))?.title).toBe("Leaders");
    expect(leadersBoard(noScorers, leaders([leader()], { sport: "basketball", stat_label: "PPG" }))?.title).toBe(
      "Scoring leaders",
    );
    expect(leadersBoard(noScorers, leaders([leader()], { sport: "hockey", stat_label: "pts" }))?.title).toBe(
      "Points leaders",
    );
  });

  it("prefers the Golden Boot when finished matches have goals", () => {
    const scorers: Scorers = {
      league_id: "epl",
      league_name: "Premier League",
      games_counted: 1,
      rows: [{ rank: 1, player: "João Pedro", team: "Chelsea", team_logo_url: null, goals: 4, highlighted: true }],
    };
    const board = leadersBoard(scorers, leaders([leader()]));
    expect(board?.kind).toBe("scorers");
    expect(board?.title).toBe("Golden Boot");
    expect(board?.unit).toBe("Goals");
    expect(board?.source).toBe("1 match counted");
    expect(board?.photos).toBe(false);
    expect(board?.rows[0]).toMatchObject({ name: "João Pedro", team: "Chelsea", value: 4, highlighted: true });
    expect(leadersBoard({ ...scorers, games_counted: 36 }, undefined)?.source).toBe("36 matches counted");
  });
});

describe("boardNote", () => {
  const ten = Array.from({ length: 12 }, (_, index) =>
    leader({ rank: index + 1, player_id: `p${index}`, value: 35 - index }),
  );

  it("is the mocked sentence for a league-wide board", () => {
    const board = leadersBoard(noScorers, leaders(ten));
    expect(board && boardNote(board, 10)).toBe(
      "MLB top ten · each bar is measured against the leader's 35",
    );
  });

  it("counts the rows actually shown and names a narrower source as one", () => {
    const board = leadersBoard(
      noScorers,
      leaders([leader({ team_id: "che", value: 2.5 }), leader({ team_id: "che", player_id: "p2", value: 2 })]),
    );
    expect(board && boardNote(board, 2)).toBe(
      "Your teams' rosters · top two · each bar is measured against the leader's 2.5",
    );
  });
});

describe("topOfBoard", () => {
  it("keeps the provider's ranks when every row counts the same thing", () => {
    const board = leadersBoard(
      noScorers,
      leaders([leader(), leader({ rank: 2, player_id: "p2", value: 33 }), leader({ rank: 3, player_id: "p3", value: 31 })]),
    );
    expect(board && topOfBoard(board, 2).map((r) => [r.rank, r.value])).toEqual([
      [1, 35],
      [2, 33],
    ]);
  });

  it("keeps only the rows that can be measured against the leader", () => {
    const mixed = leadersBoard(
      noScorers,
      leaders(
        [
          leader({ rank: 1, player_id: "qb", team_id: "mia", value: 3412, stat_label: "YDS" }),
          leader({ rank: 2, player_id: "k", team_id: "mia", value: 1200, stat_label: "PTS" }),
          leader({ rank: 3, player_id: "wr", team_id: "mia", value: 1104, stat_label: "YDS" }),
        ],
        { sport: "football", stat_label: "YDS", league_name: "NFL" },
      ),
    );
    expect(mixed?.rows.map((r) => r.unit)).toEqual(["YDS", "PTS", "YDS"]);
    expect(mixed && topOfBoard(mixed, 10).map((r) => [r.rank, r.key, r.value])).toEqual([
      [1, "mia-qb", 3412],
      [2, "mia-wr", 1104],
    ]);
    expect(mixed && boardNote(mixed, 2)).toBe(
      "Your teams' rosters · top two · each bar is measured against the leader's 3412",
    );
  });

  it("gives each Golden Boot row its own unit word", () => {
    const scorers: Scorers = {
      league_id: "epl",
      league_name: "Premier League",
      games_counted: 3,
      rows: [
        { rank: 1, player: "A", team: "X", team_logo_url: null, goals: 4, highlighted: false },
        { rank: 2, player: "B", team: "Y", team_logo_url: null, goals: 1, highlighted: false },
      ],
    };
    const board = leadersBoard(scorers, undefined);
    expect(board && topOfBoard(board, 10).map((r) => r.unit)).toEqual(["goals", "goal"]);
  });
});

describe("leaders figures", () => {
  it("drops a trailing .0 only", () => {
    expect(formatStat(35)).toBe("35");
    expect(formatStat(32.7)).toBe("32.7");
    expect(formatStat(27.44)).toBe("27.4");
  });

  it("measures a value against the leader to one decimal", () => {
    expect(shareOfLeader(35, 35)).toBe(100);
    expect(shareOfLeader(33, 35)).toBe(94.3);
    expect(shareOfLeader(31, 35)).toBe(88.6);
    expect(shareOfLeader(24, 35)).toBe(68.6);
  });

  it("never divides by a leader with nothing, and stays within the bar", () => {
    expect(shareOfLeader(3, 0)).toBe(0);
    expect(shareOfLeader(3, -1)).toBe(0);
    expect(shareOfLeader(Number.NaN, 35)).toBe(0);
    expect(shareOfLeader(40, 35)).toBe(100);
    expect(shareOfLeader(-2, 35)).toBe(0);
  });

  it("sizes the value column to the longest value, never under two digits", () => {
    expect(statColumnRem(["35", "33"])).toBe(3.5);
    expect(statColumnRem(["4"])).toBe(3.5);
    expect(statColumnRem(["32.7", "26"])).toBeGreaterThan(3.5);
    expect(statColumnRem(["3412"])).toBeGreaterThan(statColumnRem(["32.7"]));
    expect(statColumnRem([])).toBe(3.5);
  });
});

describe("the followed-team panel", () => {
  // The test zone is America/New_York: this is 9:58 PM on July 30.
  const now = new Date("2026-07-31T01:58:00Z");

  it("names a fixture's day against the clock", () => {
    expect(fixtureDay("2026-07-30T23:05:00Z", now)).toBe("Tonight"); // 7:05 PM
    expect(fixtureDay("2026-07-30T17:05:00Z", now)).toBe("Today"); // 1:05 PM
    expect(fixtureDay("2026-07-31T23:05:00Z", now)).toBe("Jul 31");
    expect(fixtureDay("2026-07-29T23:05:00Z", now)).toBe("Jul 29");
  });

  it("knows which side the team is on", () => {
    expect(venueSide(game(), "ath")).toBe("home");
    expect(venueSide(game({ home: side("Reds"), away: side("Athletics", null, "ath") }), "ath")).toBe("away");
    expect(venueSide(game(), "other")).toBeNull();
  });

  it("lists what is still to come, soonest first, and no more than asked", () => {
    const schedule = [
      game({ id: "c", start_time: "2026-08-02T20:05:00Z" }),
      game({ id: "final", start_time: "2026-07-29T23:05:00Z", phase: "final" }),
      game({ id: "a", start_time: "2026-07-30T23:05:00Z", phase: "in_progress" }),
      game({ id: "ppd", start_time: "2026-07-31T23:05:00Z", phase: "postponed" }),
      game({ id: "b", start_time: "2026-08-01T23:05:00Z" }),
      game({ id: "other", start_time: "2026-07-31T23:05:00Z", home: side("X"), away: side("Y") }),
    ];
    expect(upcomingFixtures(schedule, "ath", 6).map((g) => g.id)).toEqual(["a", "b", "c"]);
    expect(upcomingFixtures(schedule, "ath", 2).map((g) => g.id)).toEqual(["a", "b"]);
  });

  it("counts home and away among the fixtures listed", () => {
    const home = game();
    const away = game({ home: side("Reds"), away: side("Athletics", null, "ath") });
    expect(venueSummary([home, home, home, home, away, away], "ath")).toBe("4 home · 2 away");
    expect(venueSummary([away], "ath")).toBe("1 away");
    expect(venueSummary([], "ath")).toBe("");
  });

  const win = game({ phase: "final", home: side("Athletics", 4, "ath"), away: side("Boston Red Sox", 3) });
  const loss = game({ phase: "final", home: side("Twins", 11), away: side("Athletics", 8, "ath") });
  const draw = game({ phase: "final", sport: "soccer", home: side("Athletics", 1, "ath"), away: side("X", 1) });
  const unscored = game({ phase: "final" });

  it("reads results from the team's side, skipping ones with no score", () => {
    expect(teamOutcomes([loss, win, draw, unscored], "ath")).toEqual(["L", "W", "D"]);
    expect(teamOutcomes([win], "nobody")).toEqual([]);
  });

  it("sums a run of results in words", () => {
    expect(outcomeSummary(["L", "W", "L", "L", "L"])).toBe("1 win · 4 losses");
    expect(outcomeSummary(["W", "D", "L"])).toBe("1 win · 1 draw · 1 loss");
    expect(outcomeSummary(["W", "W", "D", "D"])).toBe("2 wins · 2 draws");
    expect(outcomeSummary([])).toBe("");
  });

  it("labels the form strip by how many results there are", () => {
    expect(formLabel(5)).toBe("Last five, most recent first");
    expect(formLabel(2)).toBe("Last two, most recent first");
    expect(formLabel(1)).toBe("Last game");
  });

  it("writes a score with the team's own figure first", () => {
    expect(scoreFor(win, "ath")).toBe("4–3");
    expect(scoreFor(loss, "ath")).toBe("8–11");
    expect(scoreFor(unscored, "ath")).toBeNull();
    expect(scoreFor(win, "nobody")).toBeNull();
  });
});

describe("heroStats", () => {
  const west = [
    row(1, "Texas Rangers", { wins: 55, losses: 53, games_back: 0 }),
    row(2, "Houston Astros", { wins: 54, losses: 54, games_back: 1 }),
    row(4, "Athletics", { wins: 45, losses: 63, games_back: 10, team_id: "ath" }),
  ];

  it("gives place and games back, as mocked", () => {
    expect(heroStats(west[2], west, "baseball", "AL West")).toEqual([
      { figure: "4th", label: "in AL West" },
      { figure: "10", label: "games back" },
    ]);
    expect(heroStats(west[1], west, "baseball", "AL West")[1]).toEqual({
      figure: "1",
      label: "game back",
    });
  });

  it("gives the leader its lead instead", () => {
    expect(heroStats(west[0], west, "baseball", "AL West")).toEqual([
      { figure: "1st", label: "in AL West" },
      { figure: "1", label: "game ahead" },
    ]);
    // Level with second place: only the place is true.
    const tied = [west[0], row(2, "B", { wins: 55, losses: 53, games_back: 0 })];
    expect(heroStats(tied[0], tied, "baseball", "AL West")).toHaveLength(1);
  });

  it("gives points for the sports ranked on them", () => {
    const chelsea = row(5, "Chelsea", { wins: 3, draws: 1, losses: 2, points: 10 });
    expect(heroStats(chelsea, [chelsea], "soccer", "Premier League")).toEqual([
      { figure: "5th", label: "in Premier League" },
      { figure: "10", label: "points" },
    ]);
    expect(heroStats({ ...chelsea, points: 1 }, [chelsea], "hockey", "X")[1].label).toBe("point");
    expect(heroStats({ ...chelsea, points: null }, [chelsea], "soccer", "X")).toHaveLength(1);
  });

  it("gives football its win percentage, which is all it has", () => {
    const miami = row(2, "Miami Dolphins", { wins: 9, losses: 4, draws: 1, win_pct: 0.679 });
    expect(heroStats(miami, [miami], "football", "AFC East")).toEqual([
      { figure: "2nd", label: "in AFC East" },
      { figure: ".679", label: "win pct" },
    ]);
    expect(heroStats({ ...miami, win_pct: 1 }, [miami], "football", "AFC East")[1].figure).toBe("1.000");
  });

  it("gives a ranking its place alone", () => {
    const sinner = row(1, "Jannik Sinner", { points: 11830 });
    expect(heroStats(sinner, [sinner], "tennis", "ATP")).toEqual([{ figure: "1st", label: "in ATP" }]);
  });
});

describe("panel wording and size", () => {
  it("steps a long record down so it keeps its place on the line", () => {
    expect(recordRem("45–63")).toBe(4.5);
    expect(recordRem("102–60")).toBe(4.5);
    expect(recordRem("38–4–12")).toBeLessThan(4.5);
    expect(recordRem("52–18–12")).toBeLessThan(recordRem("38–4–12"));
  });

  it("adds the article only where a table's name takes one", () => {
    expect(inTable("AL West")).toBe("in the AL West");
    expect(inTable("Premier League")).toBe("in the Premier League");
    expect(inTable("Eastern Conference")).toBe("in the Eastern Conference");
    expect(inTable("Atlantic Division")).toBe("in the Atlantic Division");
    // The NBA's divisions, as the provider names them: all six read alike.
    for (const division of ["Atlantic", "Central", "Southeast", "Northwest", "Pacific", "Southwest"]) {
      expect(inTable(division)).toBe(`in the ${division}`);
    }
    expect(inTable("Northeast")).toBe("in the Northeast");
    expect(inTable("Serie A")).toBe("in Serie A");
    expect(inTable("LaLiga")).toBe("in LaLiga");
    expect(inTable("Group A")).toBe("in Group A");
    expect(inTable("MLB")).toBe("in MLB");
  });
});

describe("bracket", () => {
  const slot = (away: string, home: string) => game({ home: side(home), away: side(away) });

  it("reads a knockout round from the draw slots", () => {
    expect(roundOf(slot("Group A Winner", "Group B 2nd Place"))).toBe("r32");
    expect(roundOf(slot("Round of 32 1 Winner", "Round of 32 2 Winner"))).toBe("r16");
    expect(roundOf(slot("Round of 16 1 Winner", "Round of 16 2 Winner"))).toBe("qf");
    expect(roundOf(slot("Quarterfinal 1 Winner", "Quarterfinal 2 Winner"))).toBe("sf");
    expect(roundOf(slot("Semifinal 1 Winner", "Semifinal 2 Winner"))).toBe("final");
    expect(roundOf(slot("Semifinal 1 Loser", "Semifinal 2 Loser"))).toBe("third");
    expect(roundOf(slot("Chelsea", "Arsenal"))).toBeNull();
  });

  it("shortens slot names", () => {
    expect(shortSlot("Round of 32 1 Winner")).toBe("R32 #1");
    expect(shortSlot("Round of 16 12 Winner")).toBe("R16 #12");
    expect(shortSlot("Quarterfinal 3 Winner")).toBe("QF #3");
    expect(shortSlot("Semifinal 1 Winner")).toBe("SF1 W");
    expect(shortSlot("Semifinal 2 Loser")).toBe("SF2 L");
    expect(shortSlot("Brazil")).toBe("Brazil");
  });

  it("halves a draw, the odd one to the left", () => {
    expect(halve([1, 2, 3, 4])).toEqual({ left: [1, 2], right: [3, 4] });
    expect(halve([1, 2, 3])).toEqual({ left: [1, 2], right: [3] });
    expect(halve([])).toEqual({ left: [], right: [] });
  });

  const series = (conference: string | null, name = "A"): BracketSeries => ({
    team1: { name, abbreviation: null, logo_url: null },
    team2: { name: "B", abbreviation: null, logo_url: null },
    summary: "",
    conference,
  });

  it("puts the East left, the West right and the unlabeled final in the center", () => {
    const split = splitRound([series("East"), series("West"), series(null)]);
    expect(split.left).toHaveLength(1);
    expect(split.right).toHaveLength(1);
    expect(split.center).toHaveLength(1);
  });

  it("centers a lone unlabeled series and halves a round with no labels", () => {
    expect(splitRound([series(null)]).center).toHaveLength(1);
    const halves = splitRound([series(null, "1"), series(null, "2"), series(null, "3"), series(null, "4")]);
    expect(halves.left.map((s) => s.team1.name)).toEqual(["1", "2"]);
    expect(halves.right.map((s) => s.team1.name)).toEqual(["3", "4"]);
    expect(halves.center).toEqual([]);
    expect(splitRound([])).toEqual({ left: [], right: [], center: [] });
  });

  it("knows what kind of bracket a league can have", () => {
    expect(bracketKind("soccer", true)).toBe("cup");
    expect(bracketKind("soccer", false)).toBeNull();
    expect(bracketKind("basketball", false)).toBe("playoff");
    expect(bracketKind("baseball", false)).toBe("playoff");
    expect(bracketKind("hockey", false)).toBe("playoff");
    expect(bracketKind("football", false)).toBeNull();
    expect(bracketKind("tennis", false)).toBeNull();
  });
});
