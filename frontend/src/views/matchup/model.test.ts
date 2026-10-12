import { describe, expect, it } from "vitest";

import { sportWords } from "../../lib/labels";
import type {
  FightResult,
  Game,
  GameOdds,
  GameSide,
  GameSummary,
  HeadToHeadRecord,
  League,
  LineupSlot,
  Player,
  Sport,
  StandingRow,
  Standings,
  TeamLineup,
  Weather,
} from "../../types";
import { summarize } from "../results/model";
import {
  ENDED_STAYS_MS,
  LAST_GAMES,
  PICKER_BUTTONS,
  SERIES_GAMES,
  barFills,
  barLabel,
  bettingLines,
  bothFollowed,
  catalogLeagues,
  chooseGame,
  colorDistance,
  favoredLine,
  figureColumnRem,
  focusSide,
  forecastFact,
  formCaption,
  formLetters,
  freshest,
  gameStatus,
  hasCatalog,
  joiner,
  lastGames,
  lastTitle,
  lastTotals,
  leadSentence,
  leadVerb,
  linesNote,
  lineupGroups,
  liveUpset,
  liveUpsetSentence,
  marketSentence,
  matchupNames,
  meetingDate,
  meetingDetail,
  meetingWinner,
  mergeMeetings,
  moreGamesLabel,
  optionLabel,
  percentPair,
  pickerLabel,
  pickerSplit,
  pickerWhen,
  possessive,
  pregameShares,
  previewGames,
  probabilityNote,
  probabilityRows,
  radarFootnote,
  radarNote,
  radarSignals,
  recordLine,
  resultDate,
  scoreOrderNote,
  sectionNote,
  sectionTitle,
  seriesHeadline,
  seriesNote,
  seriesTally,
  shortPairing,
  sideCaption,
  sideCode,
  sideFigures,
  sideNoun,
  sideTone,
  slotOrder,
  splitSeries,
  standingsNote,
  standingsPanels,
  statusLabel,
  venueSentence,
  viewerDay,
} from "./model";

// The suite runs in America/New_York (see the `test` script): every clock
// time below is written with its offset and read back in Eastern time.

const ZONE = "America/New_York";
const TODAY = "2026-07-30";
const NOW = new Date("2026-07-30T21:58:00-04:00");

function side(
  name: string,
  score: number | null = null,
  teamId: string | null = null,
  over: Partial<GameSide> = {},
): GameSide {
  return {
    team_id: teamId,
    name,
    abbreviation: null,
    logo_url: null,
    color: null,
    score,
    ...over,
  };
}

function game(over: Partial<Game> = {}): Game {
  return {
    id: "g",
    league_id: "mlb",
    sport: "baseball",
    home: side("Athletics", null, "ath", { abbreviation: "ATH" }),
    away: side("Boston Red Sox", null, null, { abbreviation: "BOS" }),
    start_time: "2026-07-30T19:05:00-04:00",
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

/** A game in progress with a score: away first, as a scoreboard reads. */
function live(away: number, home: number, over: Partial<Game> = {}): Game {
  const base = game(over);
  return {
    ...base,
    phase: over.phase ?? "in_progress",
    away: { ...base.away, score: away },
    home: { ...base.home, score: home },
  };
}

/** A finished meeting of the two sides, either way round. */
function meeting(
  id: string,
  date: string,
  away: [string, number | null],
  home: [string, number | null],
  over: Partial<Game> = {},
): Game {
  return game({
    id,
    start_time: `${date}T19:05:00-04:00`,
    phase: "final",
    away: side(away[0], away[1], away[0] === "Athletics" ? "ath" : null),
    home: side(home[0], home[1], home[0] === "Athletics" ? "ath" : null),
    ...over,
  });
}

function odds(over: Partial<GameOdds> = {}): GameOdds {
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

function summary(series: number[]): GameSummary {
  return {
    game_id: "g",
    periods: [],
    performers: [],
    team_stats: [],
    goals: [],
    home_total: null,
    away_total: null,
    win_probability: series,
    plays: [],
    clips: [],
  };
}

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

function table(sport: Sport, rows: StandingRow[], name = "MLB"): Standings {
  return {
    league_id: "mlb",
    league_name: name,
    sport,
    season: "2026",
    fetched_at: null,
    rows,
    is_stale: false,
  };
}

const league = (over: Partial<League> = {}): League => ({
  id: "mlb",
  sport: "baseball",
  name: "MLB",
  provider: "espn",
  follow_all: false,
  ...over,
});

const fight = (over: Partial<FightResult> = {}): FightResult => ({
  method: "decision",
  detail: "Unanimous",
  round: 3,
  clock: "5:00",
  ...over,
});

// ── The games on offer ──────────────────────────────────────────────────

describe("previewGames", () => {
  const early = game({ id: "early", start_time: "2026-07-30T13:05:00-04:00" });
  const late = game({ id: "late", start_time: "2026-07-30T22:10:00-04:00" });
  const tomorrow = game({ id: "tomorrow", start_time: "2026-07-31T19:05:00-04:00" });
  const going = live(3, 4, { id: "going", start_time: "2026-07-30T19:05:00-04:00" });

  it("puts games in progress first, then those to start, soonest first", () => {
    expect(
      previewGames([tomorrow, late, going, early], [], TODAY, ZONE).map((g) => g.id),
    ).toEqual(["going", "early", "late", "tomorrow"]);
  });

  it("orders two live games by when they started, then by id", () => {
    const first = live(0, 0, { id: "b", start_time: "2026-07-30T18:00:00-04:00" });
    const same = live(1, 1, { id: "a", start_time: "2026-07-30T18:00:00-04:00" });
    expect(previewGames([going, first, same], [], TODAY, ZONE).map((g) => g.id)).toEqual([
      "a",
      "b",
      "going",
    ]);
  });

  it("leaves out games that are over or called off", () => {
    const list = previewGames(
      [
        live(4, 2, { id: "final", phase: "final" }),
        game({ id: "ppd", phase: "postponed" }),
        game({ id: "off", phase: "canceled" }),
        late,
      ],
      [],
      TODAY,
      ZONE,
    );
    expect(list.map((g) => g.id)).toEqual(["late"]);
  });

  it("lays the polled Today copy over the schedule's", () => {
    const stale = game({ id: "going", start_time: going.start_time });
    const list = previewGames([stale, late], [going], TODAY, ZONE);
    expect(list[0]).toBe(going);
    // And a game Today now calls final has left the list.
    const over = { ...going, phase: "final" as const };
    expect(previewGames([stale, late], [over], TODAY, ZONE).map((g) => g.id)).toEqual([
      "late",
    ]);
  });

  it("stands on Today alone while the schedule has not loaded", () => {
    expect(previewGames([], [late, going], TODAY, ZONE).map((g) => g.id)).toEqual([
      "going",
      "late",
    ]);
  });

  it("drops a game left marked scheduled on a day that has passed", () => {
    const stuck = game({ id: "stuck", start_time: "2026-07-29T19:05:00-04:00" });
    expect(previewGames([stuck, late], [], TODAY, ZONE).map((g) => g.id)).toEqual(["late"]);
  });

  it("keeps a game still in progress after midnight", () => {
    const overnight = live(5, 5, { id: "night", start_time: "2026-07-29T22:10:00-04:00" });
    expect(previewGames([overnight], [], TODAY, ZONE).map((g) => g.id)).toEqual(["night"]);
  });

  it("places a game on the server's day, not the viewer's", () => {
    // 01:30 UTC on the 31st is still the 30th in New York and already the 31st in London.
    const evening = game({ id: "eve", start_time: "2026-07-31T01:30:00Z" });
    expect(previewGames([evening], [], "2026-07-31", "Europe/London")).toHaveLength(1);
    expect(previewGames([evening], [], "2026-07-31", ZONE)).toHaveLength(0);
  });
});

describe("chooseGame", () => {
  const first = live(3, 4, { id: "first" });
  const second = game({ id: "second", start_time: "2026-07-30T22:10:00-04:00" });
  const list = [first, second];

  it("is the first on offer when the reader has chosen nothing", () => {
    expect(chooseGame(list, list, [], undefined, NOW)).toBe(first);
    expect(chooseGame([], [], [], undefined, NOW)).toBeNull();
  });

  it("is the reader's choice while it is on offer", () => {
    expect(chooseGame(list, list, [], "second", NOW)).toBe(second);
  });

  it("keeps a chosen game on screen once it has ended", () => {
    const ended = { ...first, phase: "final" as const };
    expect(chooseGame([second], [ended, second], [], "first", NOW)).toBe(ended);
    // The polled copy is the one shown.
    const polled = { ...ended, home: { ...ended.home, score: 6 } };
    expect(chooseGame([second], [ended, second], [polled], "first", NOW)).toBe(polled);
  });

  it("keeps a chosen game that was postponed or canceled", () => {
    const off = game({ id: "off", phase: "postponed" });
    expect(chooseGame([second], [off, second], [], "off", NOW)).toBe(off);
  });

  it("does not hold a game that was called off days before its start", () => {
    // Chosen on Thursday for the Saturday after next, then postponed: the
    // page goes back to what is coming up instead of sitting on
    // "Postponed" until that Saturday has passed.
    const off = game({
      id: "off",
      phase: "postponed",
      start_time: "2026-08-08T19:05:00-04:00",
    });
    expect(chooseGame([second], [off, second], [], "off", NOW)).toBe(second);
    // Called off on the day, it stays for the evening like an ended game.
    const sameDay = { ...off, start_time: "2026-07-31T07:05:00-04:00" };
    expect(chooseGame([second], [sameDay, second], [], "off", NOW)).toBe(sameDay);
  });

  it("lets an ended game go the morning after, back to the first on offer", () => {
    const ended = { ...first, phase: "final" as const };
    const later = new Date(new Date(ended.start_time).getTime() + ENDED_STAYS_MS);
    expect(chooseGame([second], [ended, second], [], "first", later)).toBe(second);
    const justBefore = new Date(later.getTime() - 60_000);
    expect(chooseGame([second], [ended, second], [], "first", justBefore)).toBe(ended);
  });

  it("falls back to the first on offer when the schedule no longer knows the choice", () => {
    expect(chooseGame(list, list, [], "gone", NOW)).toBe(first);
  });

  it("does not hold on to a game left scheduled on a past day", () => {
    const stuck = game({ id: "stuck", start_time: "2026-07-29T19:05:00-04:00" });
    expect(chooseGame(list, [stuck, ...list], [], "stuck", NOW)).toBe(first);
  });
});

describe("pickerSplit", () => {
  const games = (count: number) =>
    Array.from({ length: count }, (_, index) => game({ id: `g${index}` }));

  it("gives every game a button while there are only a few", () => {
    const four = games(PICKER_BUTTONS + 1);
    expect(pickerSplit(four, four[0])).toEqual({ buttons: four, rest: [] });
  });

  it("moves the rest into the dropdown past that", () => {
    const many = games(14);
    const split = pickerSplit(many, many[0]);
    expect(split.buttons.map((g) => g.id)).toEqual(["g0", "g1", "g2"]);
    expect(split.rest).toHaveLength(11);
    expect(split.rest[0].id).toBe("g3");
  });

  it("always shows the game on screen as a button, in the last place", () => {
    const many = games(14);
    const split = pickerSplit(many, many[9]);
    expect(split.buttons.map((g) => g.id)).toEqual(["g0", "g1", "g9"]);
    expect(split.rest.map((g) => g.id)).not.toContain("g9");
    expect(split.rest).toHaveLength(11);
  });

  it("gives a chosen game that has left the list a button of its own", () => {
    const ended = live(4, 2, { id: "ended", phase: "final" });
    const few = games(2);
    expect(pickerSplit(few, ended).buttons.map((g) => g.id)).toEqual(["g0", "g1", "ended"]);
    const many = games(14);
    const split = pickerSplit(many, ended);
    expect(split.buttons.map((g) => g.id)).toEqual(["g0", "g1", "ended"]);
    expect(split.rest).toHaveLength(12);
  });

  it("has no buttons with nothing on offer", () => {
    expect(pickerSplit([], null)).toEqual({ buttons: [], rest: [] });
  });
});

describe("pickerWhen and the picker's words", () => {
  it("says Live now for a game in progress", () => {
    expect(pickerWhen(live(3, 4), TODAY)).toEqual({ live: true, text: "Live now" });
  });

  it("gives the time for a game today and the weekday and date otherwise", () => {
    expect(
      pickerWhen(game({ start_time: "2026-07-30T22:10:00-04:00" }), TODAY),
    ).toEqual({ live: false, text: "10:10 PM" });
    expect(
      pickerWhen(game({ start_time: "2026-07-31T19:05:00-04:00" }), TODAY),
    ).toEqual({ live: false, text: "Fri Jul 31" });
  });

  it("goes by the viewer's day, the one the time is printed in", () => {
    // 11:30 PM for the viewer. A server an hour ahead already calls
    // tomorrow "today"; a bare "7:05 PM" would read as four hours ago.
    const late = new Date("2026-07-30T23:30:00-04:00");
    expect(viewerDay(late)).toBe("2026-07-30");
    expect(
      pickerWhen(game({ start_time: "2026-07-31T19:05:00-04:00" }), viewerDay(late)),
    ).toEqual({ live: false, text: "Fri Jul 31" });
    // Half an hour on it is the viewer's day too, and the time alone will do.
    const next = new Date("2026-07-31T00:00:00-04:00");
    expect(
      pickerWhen(game({ start_time: "2026-07-31T19:05:00-04:00" }), viewerDay(next)),
    ).toEqual({ live: false, text: "7:05 PM" });
  });

  it("names the state of a game that is over or off", () => {
    expect(pickerWhen(game({ phase: "final" }), TODAY).text).toBe("Final");
    expect(pickerWhen(game({ phase: "postponed" }), TODAY).text).toBe("Postponed");
    expect(pickerWhen(game({ phase: "canceled" }), TODAY).text).toBe("Canceled");
  });

  it("joins teams with at and people with vs", () => {
    expect(joiner("baseball")).toBe("at");
    expect(joiner("tennis")).toBe("vs");
    expect(joiner("mma")).toBe("vs");
    expect(matchupNames(game())).toBe("Boston Red Sox at Athletics");
  });

  it("names a button by the whole pairing and when it is", () => {
    const tonight = game({ start_time: "2026-07-30T22:10:00-04:00" });
    expect(pickerLabel(tonight, pickerWhen(tonight, TODAY))).toBe(
      "Boston Red Sox at Athletics, 10:10 PM",
    );
    const going = live(3, 4);
    expect(pickerLabel(going, pickerWhen(going, TODAY))).toBe(
      "Boston Red Sox at Athletics, live now",
    );
  });

  it("writes a dropdown option in codes, and in surnames for people", () => {
    const tonight = game({ start_time: "2026-07-31T19:05:00-04:00" });
    expect(optionLabel(tonight, pickerWhen(tonight, TODAY))).toBe(
      "BOS at ATH · Fri Jul 31",
    );
    const tennis = game({
      sport: "tennis",
      away: side("Jannik Sinner", null, "sin", { abbreviation: "J. Sinne" }),
      home: side("Carlos Alcaraz", null, null, { abbreviation: "C. Alcar" }),
    });
    expect(shortPairing(tennis)).toBe("Sinner vs Alcaraz");
  });

  it("makes a code for a side that has none, or an empty one", () => {
    expect(sideCode(side("Wolverhampton Wanderers"))).toBe("WOL");
    expect(sideCode(side("ZAKSA Kędzierzyn-Koźle", null, null, { abbreviation: "" }))).toBe(
      "ZAK",
    );
    expect(sideCode(side("Athletics", null, null, { abbreviation: "ATH" }))).toBe("ATH");
  });

  it("counts the games in the dropdown", () => {
    expect(moreGamesLabel(1)).toBe("1 more game");
    expect(moreGamesLabel(11)).toBe("11 more games");
  });
});

describe("freshest", () => {
  it("takes the copy whose query answered last", () => {
    expect(
      freshest([
        { value: "schedule", at: 100 },
        { value: "today", at: 300 },
        { value: "preview", at: 200 },
      ]),
    ).toBe("today");
  });

  it("skips copies that are missing and keeps the first of a tie", () => {
    expect(
      freshest<string>([
        { value: undefined, at: 900 },
        { value: null, at: 800 },
        { value: "listed", at: 100 },
        { value: "detail", at: 100 },
      ]),
    ).toBe("listed");
    expect(freshest<string>([{ value: undefined, at: 1 }])).toBeUndefined();
  });
});

// ── The game section ────────────────────────────────────────────────────

describe("sectionTitle and sectionNote", () => {
  it("names the state of the game on screen", () => {
    expect(sectionTitle(live(3, 4), TODAY)).toBe("Live now");
    expect(sectionTitle(game({ phase: "final" }), TODAY)).toBe("Final");
    expect(sectionTitle(game({ phase: "postponed" }), TODAY)).toBe("Postponed");
    expect(sectionTitle(game({ phase: "canceled" }), TODAY)).toBe("Canceled");
  });

  it("splits a game still to start into today, tonight and upcoming", () => {
    expect(
      sectionTitle(game({ start_time: "2026-07-30T13:05:00-04:00" }), TODAY),
    ).toBe("Today");
    expect(
      sectionTitle(game({ start_time: "2026-07-30T19:05:00-04:00" }), TODAY),
    ).toBe("Tonight");
    expect(
      sectionTitle(game({ start_time: "2026-07-31T19:05:00-04:00" }), TODAY),
    ).toBe("Upcoming");
  });

  it("does not call tomorrow evening's game Tonight in the hour before midnight", () => {
    const late = new Date("2026-07-30T23:30:00-04:00");
    const tomorrow = game({ start_time: "2026-07-31T19:05:00-04:00" });
    expect(sectionTitle(tomorrow, viewerDay(late))).toBe("Upcoming");
    expect(sectionTitle(tomorrow, viewerDay(new Date("2026-07-31T09:00:00-04:00")))).toBe(
      "Tonight",
    );
  });

  it("writes the league, the day and the pairing for teams", () => {
    expect(sectionNote(game(), "MLB")).toBe(
      "MLB · Thursday, July 30 · Boston Red Sox at Athletics",
    );
    expect(sectionNote(game(), undefined)).toBe(
      "Thursday, July 30 · Boston Red Sox at Athletics",
    );
  });

  it("puts a tournament round in place of the pairing for people", () => {
    const tennis = game({
      sport: "tennis",
      series: "Wimbledon · QF",
      away: side("Jannik Sinner"),
      home: side("Carlos Alcaraz"),
    });
    expect(sectionNote(tennis, "ATP")).toBe("ATP · Thursday, July 30 · Wimbledon · QF");
    // With no round to name, the pairing is all there is.
    expect(sectionNote({ ...tennis, series: null }, "ATP")).toBe(
      "ATP · Thursday, July 30 · Jannik Sinner vs Carlos Alcaraz",
    );
  });

  it("keeps both the round and the pairing for a team game that has a round", () => {
    const cup = game({ sport: "soccer", series: "Quarterfinal" });
    expect(sectionNote(cup, "FIFA World Cup")).toBe(
      "FIFA World Cup · Thursday, July 30 · Quarterfinal · Boston Red Sox at Athletics",
    );
  });
});

describe("leadSentence", () => {
  it("counts runs in baseball", () => {
    expect(leadSentence(live(3, 4))).toBe("Athletics lead by one run");
    expect(leadSentence(live(9, 4))).toBe("Boston Red Sox lead by five runs");
    expect(leadSentence(live(2, 2))).toBe("Tied at 2");
    expect(leadSentence(live(0, 0))).toBe("Tied at 0");
  });

  it("counts points in basketball and football, in digits past ten", () => {
    expect(leadSentence(live(104, 98, { sport: "basketball" }))).toBe(
      "Boston Red Sox lead by six points",
    );
    expect(leadSentence(live(60, 98, { sport: "basketball" }))).toBe(
      "Athletics lead by 38 points",
    );
    expect(leadSentence(live(14, 14, { sport: "football" }))).toBe("Tied at 14");
  });

  it("counts goals in soccer and hockey, and writes a level score in full", () => {
    expect(leadSentence(live(0, 1, { sport: "soccer" }))).toBe("Athletics lead by one goal");
    expect(leadSentence(live(1, 1, { sport: "soccer" }))).toBe("Level at 1–1");
    expect(leadSentence(live(2, 2, { sport: "hockey" }))).toBe("Level at 2–2");
    expect(leadSentence(live(3, 1, { sport: "hockey" }))).toBe(
      "Boston Red Sox lead by two goals",
    );
  });

  it("counts sets in tennis and volleyball, with a singular verb for a person", () => {
    const tennis = { sport: "tennis" as const, away: side("Jannik Sinner"), home: side("Carlos Alcaraz") };
    expect(leadSentence(live(1, 0, tennis))).toBe("Jannik Sinner leads by one set");
    expect(leadSentence(live(1, 1, tennis))).toBe("Level at one set each");
    // Nothing to say before a set has been won.
    expect(leadSentence(live(0, 0, tennis))).toBeNull();
    expect(leadSentence(live(2, 1, { sport: "volleyball" }))).toBe(
      "Boston Red Sox lead by one set",
    );
    expect(leadVerb("tennis")).toBe("leads");
    expect(leadVerb("soccer")).toBe("lead");
  });

  it("says who won, and how a level game ended", () => {
    expect(leadSentence(live(4, 2, { phase: "final" }))).toBe("Boston Red Sox won by two runs");
    expect(leadSentence(live(1, 1, { sport: "soccer", phase: "final" }))).toBe("Drawn 1–1");
    expect(leadSentence(live(20, 20, { sport: "football", phase: "final" }))).toBe(
      "Tied 20–20",
    );
  });

  it("says nothing where there is no score to compare", () => {
    expect(leadSentence(game())).toBeNull();
    expect(leadSentence(game({ phase: "postponed" }))).toBeNull();
    expect(leadSentence(game({ phase: "in_progress" }))).toBeNull();
  });

  it("never reads a fight's round count as a lead", () => {
    const bout = {
      sport: "mma" as const,
      away: side("Islam Makhachev"),
      home: side("Arman Tsarukyan"),
    };
    expect(leadSentence(live(2, 1, bout))).toBeNull();
    // A finished fight gives its method, which is its result.
    const won = live(1, 0, { ...bout, phase: "final", fight_result: fight({ method: "submission", detail: "Rear-Naked Choke", round: 2, clock: "4:21" }) });
    expect(leadSentence(won)).toMatch(/^Islam Makhachev won · Submission/);
    const drawn = live(1, 1, { ...bout, phase: "final", fight_result: fight({ method: "draw", detail: "Majority" }) });
    expect(leadSentence(drawn)).not.toMatch(/won/);
    const none = live(0, 0, { ...bout, phase: "final", fight_result: fight({ method: "no_contest", detail: null }) });
    expect(leadSentence(none)).toMatch(/No Contest/);
    // A finished fight with no method on record says nothing.
    expect(leadSentence(live(1, 0, { ...bout, phase: "final" }))).toBeNull();
  });
});

describe("gameStatus", () => {
  it("writes the inning out for a live baseball game", () => {
    const status = gameStatus(
      live(3, 4, { period: 5, period_label: "Bot 5" }),
      NOW,
    );
    expect(status).toEqual({
      live: true,
      eyebrow: null,
      headline: "Bottom 5th",
      detail: null,
      sentence: "Athletics lead by one run",
    });
  });

  it("carries the clock beside a period that has one", () => {
    const status = gameStatus(
      live(104, 98, { sport: "basketball", period: 4, period_label: "Q4", clock: "3:12" }),
      NOW,
    );
    expect(status.headline).toBe("Q4");
    expect(status.detail).toBe("3:12");
  });

  it("says half time at the break, with no clock", () => {
    const status = gameStatus(
      live(1, 1, {
        sport: "soccer",
        period: 1,
        period_label: "1st Half",
        clock: "45'",
        is_intermission: true,
      }),
      NOW,
    );
    expect(status.live).toBe(true);
    expect(status.headline).toBe("Half time");
    expect(status.detail).toBeNull();
    expect(status.sentence).toBe("Level at 1–1");
  });

  it("gives the day and the start time of a game still to come", () => {
    const tonight = gameStatus(
      game({ start_time: "2026-07-30T23:40:00-04:00" }),
      NOW,
    );
    expect(tonight).toEqual({
      live: false,
      eyebrow: "Tonight",
      headline: "11:40 PM",
      detail: null,
      sentence: null,
    });
    expect(
      gameStatus(game({ start_time: "2026-07-31T13:05:00-04:00" }), NOW).eyebrow,
    ).toBe("Tomorrow");
    expect(
      gameStatus(game({ start_time: "2026-08-02T13:05:00-04:00" }), NOW).eyebrow,
    ).toBe("Sunday, August 2");
    const afternoon = new Date("2026-07-30T09:00:00-04:00");
    expect(
      gameStatus(game({ start_time: "2026-07-30T13:05:00-04:00" }), afternoon)
        .eyebrow,
    ).toBe("Today");
  });

  it("takes Tonight and Tomorrow from the viewer's own day", () => {
    // 11:30 PM Thursday on the viewer's clock, whatever day the server is on.
    const late = new Date("2026-07-30T23:30:00-04:00");
    expect(
      gameStatus(game({ start_time: "2026-07-31T19:05:00-04:00" }), late).eyebrow,
    ).toBe("Tomorrow");
    expect(
      gameStatus(game({ start_time: "2026-08-01T19:05:00-04:00" }), late).eyebrow,
    ).toBe("Saturday, August 1");
    expect(
      gameStatus(game({ start_time: "2026-07-30T23:40:00-04:00" }), late).eyebrow,
    ).toBe("Tonight");
  });

  it("counts down inside the last hour, in the sport's own word", () => {
    const soon = gameStatus(
      game({ start_time: "2026-07-30T22:10:00-04:00" }),
      NOW,
    );
    expect(soon.sentence).toBe("First pitch in 12 minutes");
    const kickoff = gameStatus(
      game({ sport: "soccer", start_time: "2026-07-30T22:10:00-04:00" }),
      NOW,
    );
    expect(kickoff.sentence).toBe("Kickoff in 12 minutes");
  });

  it("says a game is over, postponed or canceled, under its day", () => {
    const final = gameStatus(live(4, 2, { phase: "final" }), NOW);
    expect(final).toEqual({
      live: false,
      eyebrow: "Thursday, July 30",
      headline: "Final",
      detail: null,
      sentence: "Boston Red Sox won by two runs",
    });
    const postponed = gameStatus(game({ phase: "postponed" }), NOW);
    expect(postponed.headline).toBe("Postponed");
    expect(postponed.sentence).toBeNull();
    expect(gameStatus(game({ phase: "canceled" }), NOW).headline).toBe(
      "Canceled",
    );
  });
});

describe("sideTone and sideCaption", () => {
  it("sets the side in front apart and leaves a level game even", () => {
    const ahead = live(3, 4);
    expect(sideTone(ahead, "home")).toBe("lead");
    expect(sideTone(ahead, "away")).toBe("trail");
    expect(sideTone(live(2, 2), "home")).toBe("even");
    expect(sideTone(game(), "home")).toBe("even");
  });

  it("does not read a live fight's rounds as a lead, but knows a finished one's winner", () => {
    const bout = { sport: "mma" as const };
    expect(sideTone(live(2, 1, bout), "away")).toBe("even");
    const won = live(1, 0, { ...bout, phase: "final", fight_result: fight() });
    expect(sideTone(won, "away")).toBe("lead");
    expect(sideTone(won, "home")).toBe("trail");
  });

  it("says home or away, and which side is followed", () => {
    expect(sideCaption(game(), "home")).toBe("Home · Followed team");
    expect(sideCaption(game(), "away")).toBe("Away");
  });

  it("gives people no home, and calls them players", () => {
    const tennis = game({
      sport: "tennis",
      away: side("Jannik Sinner", null, "sin"),
      home: side("Carlos Alcaraz"),
      followed_team_ids: ["sin"],
    });
    expect(sideCaption(tennis, "away")).toBe("Followed player");
    expect(sideCaption(tennis, "home")).toBe("");
  });
});

describe("sideFigures and their column", () => {
  const standings = table("baseball", [
    row(3, "Boston Red Sox", { wins: 58, losses: 51, group: "American League", subgroup: "AL East" }),
    row(4, "Athletics", { team_id: "ath", wins: 45, losses: 63, group: "American League", subgroup: "AL West" }),
  ]);

  it("gives a side its record and its place in its division", () => {
    expect(sideFigures(game().home, standings)).toEqual([
      { label: "Record", value: "45–63" },
      { label: "AL West", value: "4th" },
    ]);
    expect(sideFigures(game().away, standings)).toEqual([
      { label: "Record", value: "58–51" },
      { label: "AL East", value: "3rd" },
    ]);
  });

  it("writes a soccer record with its draws and names a single table by its league", () => {
    const epl = table(
      "soccer",
      [row(5, "Chelsea", { wins: 3, draws: 1, losses: 2, points: 10, group: "2026-27 English Premier League" })],
      "Premier League",
    );
    expect(sideFigures(side("Chelsea"), epl)).toEqual([
      { label: "Record", value: "3–1–2" },
      { label: "Premier League", value: "5th" },
    ]);
  });

  it("writes a hockey record with its overtime losses", () => {
    const nhl = table("hockey", [
      row(2, "Los Angeles Kings", { wins: 48, losses: 25, ot_losses: 9, points: 105, group: "Western Conference", subgroup: "Pacific Division" }),
    ]);
    expect(sideFigures(side("Los Angeles Kings"), nhl)[0]).toEqual({
      label: "Record",
      value: "48–25–9",
    });
  });

  it("gives a ranked player a place and no record", () => {
    const atp = table("tennis", [row(1, "Jannik Sinner", { points: 11830 })], "ATP");
    expect(sideFigures(side("Jannik Sinner"), atp)).toEqual([{ label: "ATP", value: "1st" }]);
  });

  it("is empty for a side with no row, and before the table has loaded", () => {
    expect(sideFigures(side("Clube de Regatas do Flamengo"), standings)).toEqual([]);
    expect(sideFigures(game().home, undefined)).toEqual([]);
  });

  it("keeps the mockup's column for a short record and widens for a long one", () => {
    expect(figureColumnRem(["58–51", "45–63"])).toBe(8);
    expect(figureColumnRem(["3rd"])).toBe(8);
    expect(figureColumnRem([])).toBe(8);
    expect(figureColumnRem(["10–0–2", "9–1–3"])).toBeGreaterThan(8);
    expect(figureColumnRem(["52–22–10"])).toBeGreaterThan(figureColumnRem(["10–0–2"]));
  });
});

describe("forecastFact", () => {
  const weather: Weather = {
    temperature: 51.6,
    condition: "Light rain",
    code: 61,
    wind_speed: 14.2,
    units: "imperial",
    high: 88.4,
    low: 63.2,
    precip_chance: 5,
  };

  it("prints the day's forecast figures and nothing of the weather right now", () => {
    const fact = forecastFact(weather);
    expect(fact).toBe("High 88°F · Low 63°F · 5% chance of precipitation");
    // The venue's temperature, condition and wind at the moment of asking.
    expect(fact).not.toContain("52");
    expect(fact?.toLowerCase()).not.toContain("rain");
    expect(fact?.toLowerCase()).not.toContain("wind");
  });

  it("uses metric units and leaves out what the day lacks", () => {
    expect(forecastFact({ ...weather, units: "metric", high: 17.4, low: null })).toBe(
      "High 17°C · 5% chance of precipitation",
    );
    expect(forecastFact({ ...weather, high: null, low: null, precip_chance: 0 })).toBe(
      "0% chance of precipitation",
    );
  });

  it("is nothing when the day has no figure, however much is known of now", () => {
    expect(
      forecastFact({ ...weather, high: null, low: null, precip_chance: null }),
    ).toBeNull();
  });
});

// ── Win probability ─────────────────────────────────────────────────────

describe("pregameShares and percentPair", () => {
  it("takes both sides' figures as sent", () => {
    expect(pregameShares(odds({ home_win_pct: 38, away_win_pct: 62 }), "baseball")).toEqual({
      away: 62,
      home: 38,
    });
  });

  it("derives a missing side only where a game cannot end level", () => {
    expect(pregameShares(odds({ away_win_pct: 85.4 }), "basketball")).toEqual({
      away: 85.4,
      home: 100 - 85.4,
    });
    expect(pregameShares(odds({ home_win_pct: 52 }), "baseball")).toEqual({ away: 48, home: 52 });
    expect(pregameShares(odds({ away_win_pct: 60 }), "football")).toBeNull();
    expect(pregameShares(odds({ home_win_pct: 45 }), "soccer")).toBeNull();
    expect(pregameShares(odds({ home_win_pct: 45 }), "hockey")).toBeNull();
  });

  it("is null with no projection, and for figures that are not percentages", () => {
    expect(pregameShares(null, "baseball")).toBeNull();
    expect(pregameShares(undefined, "baseball")).toBeNull();
    expect(pregameShares(odds({ home_moneyline: -150, away_moneyline: 130 }), "baseball")).toBeNull();
    expect(pregameShares(odds({ home_win_pct: 140, away_win_pct: -40 }), "baseball")).toBeNull();
    expect(pregameShares(odds({ home_win_pct: Number.NaN, away_win_pct: 50 }), "football")).toBeNull();
  });

  it("prints two shares that make a whole as percentages that add to 100", () => {
    expect(percentPair({ away: 50.5, home: 49.5 })).toEqual({ away: 51, home: 49 });
    expect(percentPair({ away: 49.5, home: 50.5 })).toEqual({ away: 49, home: 51 });
    expect(percentPair({ away: 62, home: 38 })).toEqual({ away: 62, home: 38 });
    expect(percentPair({ away: 50, home: 50 })).toEqual({ away: 50, home: 50 });
  });

  it("rounds football's two on their own, since a tie share sits between them", () => {
    expect(percentPair({ away: 13.8, home: 86.1 })).toEqual({ away: 14, home: 86 });
    expect(percentPair({ away: 26.2, home: 73.6 })).toEqual({ away: 26, home: 74 });
    expect(percentPair({ away: 28.3, home: 46.1 })).toEqual({ away: 28, home: 46 });
  });
});

describe("probabilityRows", () => {
  const line = odds({ home_win_pct: 38, away_win_pct: 62 });

  it("shows the projection alone before the start", () => {
    const rows = probabilityRows(game(), line, null);
    expect(rows.map((r) => r.key)).toEqual(["before"]);
    expect(rows[0]).toMatchObject({
      title: "Before the start",
      live: false,
      percent: { away: 62, home: 38 },
      favored: "away",
    });
    expect(probabilityNote(rows, "baseball")).toBe(
      "Each team’s chance to win, as projected before the start",
    );
    // Two people are not teams.
    expect(probabilityNote(rows, "tennis")).toBe(
      "Each player’s chance to win, as projected before the start",
    );
    expect(probabilityNote(rows, "mma")).toBe(
      "Each fighter’s chance to win, as projected before the start",
    );
  });

  it("adds Right now from the last point of the in-game series while live", () => {
    const rows = probabilityRows(live(3, 4), line, summary([38, 44, 61, 64]));
    expect(rows.map((r) => r.key)).toEqual(["before", "now"]);
    expect(rows[1]).toMatchObject({
      title: "Right now",
      live: true,
      percent: { away: 36, home: 64 },
      favored: "home",
    });
    expect(probabilityNote(rows, "baseball")).toBe(
      "Each team’s chance to win, before the start and now",
    );
  });

  it("never takes the projection for the live figure when there is no series", () => {
    const rows = probabilityRows(live(3, 4), line, summary([]));
    expect(rows.map((r) => r.key)).toEqual(["before"]);
    expect(probabilityRows(live(3, 4), line, null).map((r) => r.key)).toEqual(["before"]);
  });

  it("shows Right now alone where there is a series and no projection", () => {
    const rows = probabilityRows(live(49, 51, { sport: "basketball" }), null, summary([49, 55]));
    expect(rows.map((r) => r.key)).toEqual(["now"]);
    expect(probabilityNote(rows, "basketball")).toBe("Each team’s chance to win right now");
  });

  it("ignores a series for a game that has not started or is over", () => {
    expect(probabilityRows(game(), line, summary([50, 60])).map((r) => r.key)).toEqual([
      "before",
    ]);
    // The series of a finished game ends at 100 or 0, which the score says.
    expect(
      probabilityRows(live(4, 2, { phase: "final" }), line, summary([38, 0])).map((r) => r.key),
    ).toEqual(["before"]);
  });

  it("has no rows with no data, or for a game that is off", () => {
    expect(probabilityRows(game(), null, null)).toEqual([]);
    expect(probabilityRows(live(1, 1, { sport: "soccer" }), odds({ home_moneyline: -120 }), summary([]))).toEqual([]);
    expect(probabilityRows(game({ phase: "postponed" }), line, null)).toEqual([]);
    expect(probabilityRows(game({ phase: "canceled" }), line, null)).toEqual([]);
  });

  it("calls an exact split even", () => {
    const [row] = probabilityRows(game(), odds({ home_win_pct: 50, away_win_pct: 50 }), null);
    expect(row.favored).toBeNull();
    expect(favoredLine(game(), row)).toBe("Even");
  });

  it("names the favourite and reads the bar aloud", () => {
    const [row] = probabilityRows(game(), line, null);
    expect(favoredLine(game(), row)).toBe("Boston Red Sox favored");
    expect(barLabel(game(), row)).toBe(
      "Before the start: Boston Red Sox 62 percent, Athletics 38 percent",
    );
  });
});

describe("barFills", () => {
  const WHITE = "#FFFFFF";
  const DARK = "#171C22";

  it("uses the two teams' colours when they can be told apart", () => {
    expect(barFills("#BD3039", "#EFB21E", WHITE)).toEqual({ away: "#bd3039", home: "#efb21e" });
  });

  it("gives up when either colour is missing or not a colour", () => {
    expect(barFills(null, "#EFB21E", WHITE)).toBeNull();
    expect(barFills("#BD3039", undefined, WHITE)).toBeNull();
    expect(barFills("red", "#EFB21E", WHITE)).toBeNull();
  });

  it("gives up when a colour would dissolve into the sheet it is drawn on", () => {
    expect(barFills("#FFFFFF", "#BD3039", WHITE)).toBeNull();
    expect(barFills("#FFFFFF", "#BD3039", DARK)).not.toBeNull();
    // Navy on the dark sheet is the same problem the other way round.
    expect(barFills("#0C2340", "#EFB21E", DARK)).toBeNull();
    expect(barFills("#0C2340", "#EFB21E", WHITE)).not.toBeNull();
  });

  it("gives up when the two are the same colour, or near enough", () => {
    expect(barFills("#0000FF", "#0000FF", WHITE)).toBeNull();
    expect(barFills("#006BB6", "#0067B1", WHITE)).toBeNull();
    expect(barFills("#0C2340", "#002B5C", WHITE)).toBeNull();
  });

  it("measures distance as zero for one colour and large for opposites", () => {
    expect(colorDistance("#123456", "#123456")).toBe(0);
    expect(colorDistance("#000000", "#ffffff")).toBeGreaterThan(700);
    expect(colorDistance("#bd3039", "#efb21e")).toBeGreaterThan(100);
  });
});

describe("linesNote and sideNoun", () => {
  it("says the lines were priced before the start once the game is on or over", () => {
    expect(linesNote(game())).toBe("What the sportsbook has priced for this game");
    expect(linesNote(live(0, 1, { sport: "soccer" }))).toBe(
      "What the sportsbook priced before the start",
    );
    expect(linesNote(live(0, 1, { sport: "soccer", phase: "final" }))).toBe(
      "What the sportsbook priced before the start",
    );
  });

  it("calls a side what its sport calls it", () => {
    expect(sideNoun("baseball")).toBe("team");
    expect(sideNoun("soccer")).toBe("team");
    expect(sideNoun("tennis")).toBe("player");
    expect(sideNoun("mma")).toBe("fighter");
  });
});

describe("bettingLines", () => {
  it("lists both moneylines, the home spread, the total and whose lines they are", () => {
    expect(
      bettingLines(
        game(),
        odds({ provider: "DraftKings", home_moneyline: 135, away_moneyline: -160, spread: 1.5, over_under: 8.5 }),
      ),
    ).toEqual([
      { label: "Moneyline", value: "BOS -160 · ATH +135" },
      { label: "Spread", value: "ATH +1.5" },
      { label: "Total", value: "O/U 8.5" },
      { label: "Lines from", value: "DraftKings" },
    ]);
  });

  it("lists only what was priced", () => {
    expect(bettingLines(game(), odds({ over_under: 2.5 }))).toEqual([
      { label: "Total", value: "O/U 2.5" },
    ]);
  });

  it("is empty with nothing priced: a sportsbook's name alone is not a line", () => {
    expect(bettingLines(game(), odds({ provider: "DraftKings", home_win_pct: 38, away_win_pct: 62 }))).toEqual([]);
    expect(bettingLines(game(), null)).toEqual([]);
    expect(bettingLines(game(), undefined)).toEqual([]);
  });

  it("has no lines for a game that is off", () => {
    const line = odds({ home_moneyline: -150, away_moneyline: 130 });
    expect(bettingLines(game({ phase: "postponed" }), line)).toEqual([]);
    expect(bettingLines(game({ phase: "canceled" }), line)).toEqual([]);
    expect(bettingLines(live(4, 2, { phase: "final" }), line)).toHaveLength(1);
  });

  it("makes a code for a side that has none", () => {
    const [moneyline] = bettingLines(
      game({ away: side("Wolverhampton Wanderers"), home: side("Chelsea", null, null, { abbreviation: "" }) }),
      odds({ home_moneyline: -115, away_moneyline: 310 }),
    );
    expect(moneyline.value).toBe("WOL +310 · CHE -115");
  });
});

// ── Upset radar ─────────────────────────────────────────────────────────

describe("liveUpset and the radar", () => {
  const line = odds({ home_win_pct: 38, away_win_pct: 62 });

  it("flags a live game whose leader was the pre-game underdog", () => {
    const upset = liveUpset(live(3, 4), line);
    expect(upset).toEqual({ leader: "home", pct: 38 });
    expect(liveUpsetSentence(live(3, 4), upset!)).toBe(
      "Athletics lead after starting as a 38% underdog.",
    );
  });

  it("does not flag the favourite in front, a level game, or one not in progress", () => {
    expect(liveUpset(live(5, 4), line)).toBeNull();
    expect(liveUpset(live(4, 4), line)).toBeNull();
    expect(liveUpset(game(), line)).toBeNull();
    expect(liveUpset(live(3, 4, { phase: "final" }), line)).toBeNull();
  });

  it("needs the projection: no figure, no signal", () => {
    expect(liveUpset(live(3, 4), null)).toBeNull();
    expect(liveUpset(live(0, 1, { sport: "soccer" }), odds({ home_moneyline: 320 }))).toBeNull();
    // Football with one side missing is not guessed at.
    expect(liveUpset(live(3, 7, { sport: "football" }), odds({ away_win_pct: 70 }))).toBeNull();
  });

  it("works from one side's figure where the other follows from it", () => {
    const nba = live(104, 98, { sport: "basketball" });
    expect(liveUpset(nba, odds({ away_win_pct: 41.4 }))).toEqual({ leader: "away", pct: 41 });
  });

  it("never reads a fight's rounds as a lead", () => {
    expect(liveUpset(live(2, 1, { sport: "mma" }), line)).toBeNull();
  });

  it("does not call a side with an even chance an underdog", () => {
    expect(liveUpset(live(3, 4), odds({ home_win_pct: 50, away_win_pct: 50 }))).toBeNull();
    // Football: under 50 but the larger share of the two.
    expect(
      liveUpset(live(3, 7, { sport: "football" }), odds({ home_win_pct: 49.9, away_win_pct: 49.8 })),
    ).toBeNull();
  });

  it("prints the percentage the Before the start row prints", () => {
    // 62.5 and 37.5 are printed 63% and 37% in the row; the card agrees.
    const line625 = odds({ home_win_pct: 37.5, away_win_pct: 62.5 });
    const row = percentPair(pregameShares(line625, "baseball")!);
    expect(row).toEqual({ away: 63, home: 37 });
    expect(liveUpset(live(3, 4), line625)).toEqual({ leader: "home", pct: row.home });
  });

  it("never calls a side that rounds to 50% an underdog", () => {
    expect(
      liveUpset(live(3, 4), odds({ home_win_pct: 49.6, away_win_pct: 50.4 })),
    ).toBeNull();
    expect(
      liveUpset(live(3, 4), odds({ home_win_pct: 49.4, away_win_pct: 50.6 })),
    ).toEqual({ leader: "home", pct: 49 });
  });

  it("uses a singular verb for a person", () => {
    const tennis = live(1, 0, { sport: "tennis", away: side("Jannik Sinner"), home: side("Carlos Alcaraz") });
    expect(liveUpsetSentence(tennis, { leader: "away", pct: 31 })).toBe(
      "Jannik Sinner leads after starting as a 31% underdog.",
    );
  });

  it("collects both kinds of signal, in the order of the games", () => {
    const going = live(3, 4, { id: "going" });
    const quiet = game({ id: "quiet" });
    const market = game({ id: "market" });
    const signals = radarSignals([going, quiet, market, game({ id: "unpriced" })], {
      going: line,
      quiet: odds({ home_moneyline: -150, away_moneyline: 130, home_win_pct: 58, away_win_pct: 42 }),
      // The model's favourite (74%) is the betting underdog (+120).
      market: odds({ home_moneyline: 120, away_moneyline: -142, home_win_pct: 73.6, away_win_pct: 26.2 }),
    });
    expect(signals.map((s) => s.game.id)).toEqual(["going", "market"]);
    expect(signals[0].live).toEqual({ leader: "home", pct: 38 });
    expect(signals[0].market).toBeNull();
    expect(signals[1].live).toBeNull();
    expect(marketSentence(signals[1].market!)).toBe(
      "Model likes ATH (74%) — line has them at +120",
    );
  });

  it("has no signals before the odds have answered", () => {
    expect(radarSignals([live(3, 4)], undefined)).toEqual([]);
    expect(radarSignals([live(3, 4)], {})).toEqual([]);
  });

  it("counts the signals and says what a signal is", () => {
    expect(radarNote(1)).toBe("1 signal across your games");
    expect(radarNote(3)).toBe("3 signals across your games");
    expect(radarFootnote(false, 2)).toMatch(/^A game is flagged when/);
    expect(radarFootnote(true, 1)).toMatch(/^You are viewing this game\. A game is flagged/);
    expect(radarFootnote(true, 3)).toMatch(/^You are viewing the highlighted game\./);
    // Both kinds are named, because both are flagged.
    expect(radarFootnote(false, 1)).toMatch(/betting underdog/);
    expect(radarFootnote(false, 1)).toMatch(/underdog before the start/);
  });
});

// ── Meetings ────────────────────────────────────────────────────────────

describe("meetings", () => {
  const m27 = meeting("m27", "2026-07-27", ["Boston Red Sox", 4], ["Athletics", 2], { venue: "Sutter Health Park" });
  const m28 = meeting("m28", "2026-07-28", ["Boston Red Sox", 3], ["Athletics", 4], { venue: "Sutter Health Park" });
  const m29 = meeting("m29", "2026-07-29", ["Boston Red Sox", 4], ["Athletics", 2], { venue: "Sutter Health Park" });
  const old1 = meeting("old1", "2025-09-10", ["Athletics", 5], ["Boston Red Sox", 3], { venue: "Fenway Park" });
  const old2 = meeting("old2", "2025-09-09", ["Athletics", 1], ["Boston Red Sox", 6], { venue: "Fenway Park" });
  const today = game();

  const record = (meetings: Game[]): HeadToHeadRecord => ({
    team_id: "ath",
    team_name: "Athletics",
    opponent_name: "Boston Red Sox",
    seasons: 5,
    wins: 11,
    losses: 14,
    draws: 0,
    meetings,
  });

  it("merges the stored meetings with the archive's, newest first, without repeats", () => {
    const stored = { ...m29, venue: "stored copy" };
    const merged = mergeMeetings([stored, m28], record([m29, m28, m27, old1, old2]));
    expect(merged.map((g) => g.id)).toEqual(["m29", "m28", "m27", "old1", "old2"]);
    // The stored copy replaces the listed one.
    expect(merged[0].venue).toBe("stored copy");
    expect(mergeMeetings([m27, m29], null).map((g) => g.id)).toEqual(["m29", "m27"]);
    expect(mergeMeetings([], null)).toEqual([]);
  });

  it("draws the newest few in full, oldest first, and lists the rest behind them", () => {
    const series = splitSeries([m29, m28, m27, old1, old2]);
    expect(SERIES_GAMES).toBe(3);
    expect(series.shown.map((g) => g.id)).toEqual(["m27", "m28", "m29"]);
    expect(series.earlier.map((g) => g.id)).toEqual(["old1", "old2"]);
    expect(splitSeries([m29]).shown.map((g) => g.id)).toEqual(["m29"]);
    expect(splitSeries([])).toEqual({ shown: [], earlier: [] });
  });

  it("knows who won, a draw, and a meeting with no result", () => {
    expect(meetingWinner(m27)).toBe("away");
    expect(meetingWinner(m28)).toBe("home");
    expect(meetingWinner(meeting("d", "2026-02-14", ["Chelsea", 1], ["Arsenal", 1], { sport: "soccer" }))).toBe("draw");
    expect(meetingWinner(meeting("n", "2026-02-14", ["Chelsea", null], ["Arsenal", 1]))).toBeNull();
  });

  it("reads a fight's result from its method, not its rounds", () => {
    const bout = (result: FightResult | null, away = 1, home = 0) =>
      meeting("f", "2025-03-01", ["A", away], ["B", home], { sport: "mma", fight_result: result });
    expect(meetingWinner(bout(fight()))).toBe("away");
    expect(meetingWinner(bout(fight({ method: "draw" }), 1, 1))).toBe("draw");
    expect(meetingWinner(bout(fight({ method: "no_contest" }), 0, 0))).toBeNull();
  });

  it("tallies wins for today's two sides, whichever was at home", () => {
    const tally = seriesTally(today, [m27, m28, m29]);
    expect(tally).toEqual({ leader: "away", ahead: 2, behind: 1, draws: 0, played: 3 });
    expect(seriesNote(today, tally)).toBe("Boston Red Sox lead 2–1");
    expect(seriesHeadline(today, tally)).toEqual({
      headline: "Boston Red Sox lead",
      sub: "over the three meetings below",
    });
    // The Athletics won at Fenway: counted for today's home side.
    expect(seriesTally(today, [old1, old2])).toMatchObject({ leader: null, ahead: 1, behind: 1 });
  });

  it("counts draws beside the wins", () => {
    const che = game({
      sport: "soccer",
      home: side("Chelsea", null, "che"),
      away: side("Brighton & Hove Albion"),
      followed_team_ids: ["che"],
    });
    const meetings = [
      meeting("a", "2026-02-14", ["Chelsea", 1], ["Brighton & Hove Albion", 1], { sport: "soccer" }),
      meeting("b", "2025-11-29", ["Brighton & Hove Albion", 0], ["Chelsea", 0], { sport: "soccer" }),
      meeting("c", "2025-04-12", ["Chelsea", 3], ["Brighton & Hove Albion", 0], { sport: "soccer" }),
    ];
    const tally = seriesTally(che, meetings);
    expect(tally).toEqual({ leader: "home", ahead: 1, behind: 0, draws: 2, played: 3 });
    expect(seriesNote(che, tally)).toBe("Chelsea lead 1–0, two drawn");
    expect(seriesHeadline(che, tally)).toEqual({
      headline: "Chelsea lead",
      sub: "over the three meetings below, two drawn",
    });
  });

  it("says level, and all drawn, where nobody is ahead", () => {
    const level = seriesTally(today, [m27, m28]);
    expect(seriesNote(today, level)).toBe("Level at 1–1");
    expect(seriesHeadline(today, level)).toEqual({
      headline: "Level",
      sub: "over the two meetings below",
    });
    const drawn = seriesTally(today, [
      meeting("d", "2026-06-20", ["Boston Red Sox", 0], ["Athletics", 0], { sport: "soccer" }),
    ]);
    expect(drawn).toMatchObject({ leader: null, ahead: 0, behind: 0, draws: 1, played: 1 });
    expect(seriesNote(today, drawn)).toBe("One drawn");
    expect(seriesHeadline(today, drawn)).toEqual({
      headline: "Level",
      sub: "over the meeting below, one drawn",
    });
  });

  it("gives a single meeting a winner, not a leader", () => {
    const one = seriesTally(today, [m29]);
    expect(seriesNote(today, one)).toBe("Boston Red Sox won the last one");
    expect(seriesHeadline(today, one)).toEqual({
      headline: "Boston Red Sox won",
      sub: "the meeting below",
    });
  });

  it("skips meetings with no result or between other sides", () => {
    const other = meeting("o", "2026-07-01", ["Texas Rangers", 5], ["Seattle Mariners", 1]);
    const unscored = meeting("u", "2026-07-02", ["Boston Red Sox", null], ["Athletics", null]);
    expect(seriesTally(today, [other, unscored])).toMatchObject({ played: 0, leader: null });
    expect(seriesNote(today, seriesTally(today, []))).toBeNull();
  });

  it("says how many of the meetings drawn it counted, when one has no result", () => {
    // Three rows are listed; the middle one has no score, so two count.
    const unscored = meeting("u", "2026-07-28", ["Boston Red Sox", null], ["Athletics", null]);
    const shown = [m27, unscored, m29];
    const tally = seriesTally(today, shown);
    expect(tally.played).toBe(2);
    expect(seriesHeadline(today, tally, shown.length)).toEqual({
      headline: "Boston Red Sox lead",
      sub: "over two of the three meetings below",
    });
    const one = seriesTally(today, [m27, unscored]);
    expect(seriesHeadline(today, one, 2)).toEqual({
      headline: "Boston Red Sox won",
      sub: "one of the two meetings below",
    });
    // With every meeting counted the line is as it was.
    expect(seriesHeadline(today, seriesTally(today, [m27, m28, m29]), 3).sub).toBe(
      "over the three meetings below",
    );
  });

  it("uses a singular verb for a person in front", () => {
    const tennis = game({ sport: "tennis", away: side("Jannik Sinner"), home: side("Carlos Alcaraz") });
    const tally = seriesTally(tennis, [
      meeting("x", "2026-07-12", ["Jannik Sinner", 3], ["Carlos Alcaraz", 1], { sport: "tennis" }),
      meeting("y", "2026-06-07", ["Carlos Alcaraz", 2], ["Jannik Sinner", 3], { sport: "tennis" }),
    ]);
    expect(seriesNote(tennis, tally)).toBe("Jannik Sinner leads 2–0");
  });

  it("names the venue only when every meeting was there", () => {
    const words = sportWords("baseball");
    expect(venueSentence([m27, m28, m29], words)).toBe("All three games at Sutter Health Park");
    expect(venueSentence([m27, m28], words)).toBe("Both games at Sutter Health Park");
    expect(venueSentence([m27, old1], words)).toBeNull();
    expect(venueSentence([m27], words)).toBeNull();
    expect(venueSentence([m27, { ...m28, venue: null }], words)).toBeNull();
    expect(venueSentence([{ ...m27, venue: " " }, { ...m28, venue: " " }], words)).toBeNull();
    expect(venueSentence([m27, m28], sportWords("soccer"))).toBe(
      "Both matches at Sutter Health Park",
    );
  });

  it("dates a meeting by its weekday this year and by its year before that", () => {
    expect(meetingDate("2026-07-27T19:05:00-04:00", NOW)).toEqual({ top: "Mon", date: "Jul 27" });
    expect(meetingDate("2025-09-10T19:05:00-04:00", NOW)).toEqual({ top: "2025", date: "Sep 10" });
  });

  it("adds the tournament and the method under a meeting of two people only", () => {
    expect(meetingDetail(m27)).toBeNull();
    expect(
      meetingDetail(meeting("t", "2026-07-12", ["A", 1], ["B", 3], { sport: "tennis", series: "Wimbledon · Final" })),
    ).toBe("Wimbledon · Final");
    const bout = meeting("f", "2025-03-01", ["A", 1], ["B", 0], { sport: "mma", series: "UFC 300", fight_result: fight({ detail: "Split" }) });
    expect(meetingDetail(bout)).toMatch(/^UFC 300 · Decision/);
    expect(meetingDetail(meeting("n", "2025-03-01", ["A", 1], ["B", 0], { sport: "tennis" }))).toBeNull();
  });

  it("writes the cross-season record from the followed side's view", () => {
    expect(recordLine(record([]), "baseball")).toEqual({
      label: "Last five seasons",
      value: "Athletics: 11 wins, 14 losses",
    });
    expect(
      recordLine({ ...record([]), team_name: "Chelsea", wins: 1, draws: 0, losses: 1 }, "soccer"),
    ).toEqual({ label: "Last five seasons", value: "Chelsea: 1 win, 0 draws, 1 loss" });
    expect(recordLine({ ...record([]), draws: 2 }, "football").value).toBe(
      "Athletics: 11 wins, 2 draws, 14 losses",
    );
  });
});

// ── "{Team}, last N" ────────────────────────────────────────────────────

describe("focusSide", () => {
  const both = game({
    home: side("Arizona Diamondbacks", null, "ari"),
    away: side("Athletics", null, "ath"),
    followed_team_ids: ["ath", "ari"],
  });

  it("is the followed side when only one is", () => {
    expect(focusSide(game(), null)).toBe("home");
    expect(
      focusSide(game({ home: side("Cincinnati Reds"), away: side("Athletics", null, "ath") }), null),
    ).toBe("away");
    expect(bothFollowed(game())).toBe(false);
  });

  it("is the favourite team with both followed, else the home side", () => {
    expect(bothFollowed(both)).toBe(true);
    expect(focusSide(both, "ath")).toBe("away");
    expect(focusSide(both, "ari")).toBe("home");
    expect(focusSide(both, null)).toBe("home");
    expect(focusSide(both, "someone-else")).toBe("home");
  });

  it("follows the reader's switch only when both are followed", () => {
    expect(focusSide(both, "ari", "away")).toBe("away");
    expect(focusSide(game(), null, "away")).toBe("home");
  });

  it("is nobody when neither side is followed", () => {
    const neither = game({ home: side("Getafe"), away: side("Deportivo Alavés"), followed_team_ids: [] });
    expect(focusSide(neither, "ath")).toBeNull();
    expect(focusSide(neither, null, "home")).toBeNull();
  });
});

describe("lastGames and its totals", () => {
  const ATH = { id: "ath", name: "Athletics" };
  const results = [
    meeting("r1", "2026-07-29", ["Boston Red Sox", 4], ["Athletics", 2]),
    meeting("r2", "2026-07-28", ["Boston Red Sox", 3], ["Athletics", 4]),
    meeting("r3", "2026-07-26", ["Athletics", 8], ["Minnesota Twins", 11]),
    meeting("r4", "2026-07-25", ["Athletics", null], ["Minnesota Twins", null]),
    meeting("r5", "2026-07-24", ["Athletics", 2], ["Minnesota Twins", 0]),
  ];

  it("lists the team's results newest first, from its own side", () => {
    const rows = lastGames(results, ATH);
    expect(rows.map((r) => [r.game.id, r.outcome, r.home, r.opponent.name, r.mine, r.theirs])).toEqual([
      ["r1", "L", true, "Boston Red Sox", 2, 4],
      ["r2", "W", true, "Boston Red Sox", 4, 3],
      ["r3", "L", false, "Minnesota Twins", 8, 11],
      ["r5", "W", false, "Minnesota Twins", 2, 0],
    ]);
  });

  it("stops at the limit, which a game with no result does not use up", () => {
    expect(lastGames(results, ATH, 3).map((r) => r.game.id)).toEqual(["r1", "r2", "r3"]);
    expect(LAST_GAMES).toBe(8);
  });

  it("skips a game the team is not in", () => {
    const stray = meeting("s", "2026-07-30", ["Texas Rangers", 5], ["Seattle Mariners", 1]);
    expect(lastGames([stray, ...results], ATH, 1).map((r) => r.game.id)).toEqual(["r1"]);
  });

  it("records a draw", () => {
    const che = { id: "che", name: "Chelsea" };
    const drawn = meeting("d", "2026-07-25", ["Arsenal", 1], ["Chelsea", 1], {
      sport: "soccer",
      home: side("Chelsea", 1, "che"),
    });
    expect(lastGames([drawn], che)[0]).toMatchObject({ outcome: "D", mine: 1, theirs: 1 });
  });

  it("titles the table by how many games it really holds", () => {
    expect(lastTitle("Athletics", 8, sportWords("baseball"))).toBe("Athletics, last 8");
    expect(lastTitle("Chelsea", 2, sportWords("soccer"))).toBe("Chelsea, last 2");
    expect(lastTitle("Chelsea", 1, sportWords("soccer"))).toBe("Chelsea, last match");
    expect(lastTitle("Islam Makhachev", 1, sportWords("mma"))).toBe("Islam Makhachev, last bout");
  });

  it("says which way round the score reads, in the sport's own noun", () => {
    expect(possessive("Athletics")).toBe("Athletics’");
    expect(possessive("Chelsea")).toBe("Chelsea’s");
    expect(scoreOrderNote("Athletics", sportWords("baseball"))).toBe("Athletics’ runs first");
    expect(scoreOrderNote("Chelsea", sportWords("soccer"))).toBe("Chelsea’s goals first");
    expect(scoreOrderNote("Jannik Sinner", sportWords("tennis"))).toBe("Jannik Sinner’s sets first");
    expect(scoreOrderNote("Islam Makhachev", sportWords("mma"))).toBeNull();
  });

  it("sums the games listed, in the sport's own words", () => {
    const rows = lastGames(results, ATH);
    const baseball = sportWords("baseball");
    expect(
      lastTotals(summarize(rows.map((r) => r.game), ATH, "baseball", LAST_GAMES), "baseball", baseball),
    ).toEqual([
      { label: "In the last 4", value: "2–2" },
      { label: "Runs scored", value: "16" },
      { label: "Runs allowed", value: "18" },
    ]);
  });

  it("writes a soccer record with draws and conceded goals", () => {
    const che = { id: "che", name: "Chelsea" };
    const games = [
      meeting("a", "2026-07-25", ["Arsenal", 1], ["Chelsea", 1], { sport: "soccer", home: side("Chelsea", 1, "che") }),
      meeting("b", "2026-07-18", ["Chelsea", 3], ["Fulham", 0], { sport: "soccer", away: side("Chelsea", 3, "che") }),
    ];
    const soccer = sportWords("soccer");
    expect(lastTotals(summarize(games, che, "soccer", LAST_GAMES), "soccer", soccer)).toEqual([
      { label: "In the last 2", value: "1–1–0" },
      { label: "Goals scored", value: "4" },
      { label: "Goals conceded", value: "1" },
    ]);
  });

  it("gives a fighter a record and no tallies, and nothing with no games", () => {
    const mak = { id: "mak", name: "Islam Makhachev" };
    const bout = meeting("f", "2026-06-01", ["Islam Makhachev", 1], ["Dustin Poirier", 0], {
      sport: "mma",
      away: side("Islam Makhachev", 1, "mak"),
      fight_result: fight(),
    });
    const mma = sportWords("mma");
    expect(lastTotals(summarize([bout], mak, "mma", LAST_GAMES), "mma", mma)).toEqual([
      { label: "In the last bout", value: "1–0" },
    ]);
    expect(lastTotals(summarize([], mak, "mma", LAST_GAMES), "mma", mma)).toEqual([]);
  });
});

describe("form letters and what they are", () => {
  it("keeps only wins, losses and draws", () => {
    expect(formLetters(["W", "l", "D", "?", "", "T", "w"])).toEqual(["W", "L", "D", "W"]);
    expect(formLetters([])).toEqual([]);
  });

  it("calls a followed side's letters its results on record", () => {
    expect(formCaption(6, true, league())).toBe("Last six results on record, newest first");
    expect(formCaption(1, true, league())).toBe("Last result on record, newest first");
  });

  it("calls every side's letters its results in a league followed whole", () => {
    expect(formCaption(6, false, league({ follow_all: true }))).toBe(
      "Last six results on record, newest first",
    );
  });

  it("says an unfollowed side's letters are only its games against followed sides", () => {
    expect(formCaption(3, false, league())).toBe(
      "Last three results against teams you follow, newest first",
    );
    expect(formCaption(1, false, undefined)).toBe(
      "Last result against teams you follow, newest first",
    );
    expect(formCaption(2, false, league(), "tennis")).toBe(
      "Last two results against players you follow, newest first",
    );
    expect(formCaption(1, false, league(), "mma")).toBe(
      "Last result against fighters you follow, newest first",
    );
  });

  it("adds the year to a result that is not this year's", () => {
    expect(resultDate("2026-07-29T19:05:00-04:00", NOW)).toBe("Jul 29");
    expect(resultDate("2025-03-01T19:00:00-05:00", NOW)).toBe("Mar 1, 2025");
  });
});

// ── Standings context ───────────────────────────────────────────────────

describe("standingsPanels", () => {
  const al = [
    row(1, "Texas Rangers", { wins: 55, losses: 53, games_back: 0, group: "American League", subgroup: "AL West" }),
    row(2, "Houston Astros", { wins: 54, losses: 54, games_back: 1, group: "American League", subgroup: "AL West" }),
    row(4, "Athletics", { team_id: "ath", wins: 45, losses: 63, games_back: 10, group: "American League", subgroup: "AL West" }),
    row(1, "Tampa Bay Rays", { wins: 62, losses: 46, games_back: 0, group: "American League", subgroup: "AL East" }),
    row(3, "Boston Red Sox", { wins: 58, losses: 51, games_back: 4.5, group: "American League", subgroup: "AL East" }),
  ];

  it("puts the followed side first, with games back in baseball", () => {
    const panels = standingsPanels(game(), table("baseball", al));
    expect(panels).toEqual([
      {
        side: "home",
        followed: true,
        place: { value: "4th", label: "in AL West" },
        second: { value: "10", label: "games back" },
        record: "45–63",
        group: "American League",
      },
      {
        side: "away",
        followed: false,
        place: { value: "3rd", label: "in AL East" },
        second: { value: "4.5", label: "games back" },
        record: "58–51",
        group: "American League",
      },
    ]);
  });

  it("gives a division leader its lead over second place", () => {
    const leader = game({ home: side("Texas Rangers"), away: side("Athletics", null, "ath") });
    const [, texas] = standingsPanels(leader, table("baseball", al));
    expect(texas.second).toEqual({ value: "1", label: "game ahead" });
  });

  it("shows points for the sports ranked on them", () => {
    const epl = table(
      "soccer",
      [
        row(3, "Brighton & Hove Albion", { wins: 4, draws: 1, losses: 1, points: 13, group: "Premier League" }),
        row(5, "Chelsea", { team_id: "che", wins: 3, draws: 1, losses: 2, points: 10, group: "Premier League" }),
      ],
      "Premier League",
    );
    const che = game({
      sport: "soccer",
      home: side("Chelsea", null, "che"),
      away: side("Brighton & Hove Albion"),
      followed_team_ids: ["che"],
    });
    const [chelsea, brighton] = standingsPanels(che, epl);
    expect(chelsea).toMatchObject({
      place: { value: "5th", label: "in Premier League" },
      second: { value: "10", label: "points" },
      record: "3–1–2",
    });
    expect(brighton.second).toEqual({ value: "13", label: "points" });
  });

  it("shows win percentage in football and ranking points in tennis", () => {
    const nfl = table("football", [
      row(2, "Miami Dolphins", { wins: 9, losses: 4, draws: 1, win_pct: 0.679, group: "AFC", subgroup: "AFC East" }),
    ]);
    const [miami] = standingsPanels(
      game({ sport: "football", home: side("Buffalo Bills"), away: side("Miami Dolphins") }),
      nfl,
    );
    expect(miami.second).toEqual({ value: ".679", label: "win pct" });
    expect(miami.record).toBe("9–4–1");

    const atp = table("tennis", [row(1, "Jannik Sinner", { points: 11830 })], "ATP");
    const [sinner] = standingsPanels(
      game({ sport: "tennis", home: side("Carlos Alcaraz"), away: side("Jannik Sinner") }),
      atp,
    );
    expect(sinner).toMatchObject({
      place: { value: "1st", label: "in ATP" },
      second: { value: "11,830", label: "ranking points" },
      record: null,
    });
  });

  it("gives a side with no row no panel, and nothing before the table loads", () => {
    const cup = game({ home: side("Athletics", null, "ath"), away: side("Yomiuri Giants") });
    expect(standingsPanels(cup, table("baseball", al)).map((p) => p.side)).toEqual(["home"]);
    expect(standingsPanels(game({ home: side("A"), away: side("B") }), table("baseball", al))).toEqual([]);
    expect(standingsPanels(game(), undefined)).toEqual([]);
  });

  it("keeps away before home when neither or both are followed", () => {
    const neither = game({ home: side("Boston Red Sox"), away: side("Texas Rangers"), followed_team_ids: [] });
    expect(standingsPanels(neither, table("baseball", al)).map((p) => p.side)).toEqual(["away", "home"]);
  });

  it("names the second figure beside the tab", () => {
    expect(standingsNote("baseball")).toBe("Games back of first place");
    expect(standingsNote("basketball")).toBe("Games back of first place");
    expect(standingsNote("soccer")).toBe("Place and points in the table");
    expect(standingsNote("hockey")).toBe("Place and points in the table");
    expect(standingsNote("football")).toBe("Place and win percentage");
    expect(standingsNote("tennis")).toBe("Place and points in the rankings");
    expect(standingsNote("mma")).toBeUndefined();
  });
});

// ── Squads ──────────────────────────────────────────────────────────────

describe("lineups and injuries", () => {
  const player = (id: string, name: string): Player => ({
    id,
    name,
    position: null,
    jersey_number: null,
    status: "active",
    status_detail: null,
    stat_line: null,
    career_stat_line: null,
    photo_url: null,
  });
  const slot = (id: string, role: string, unit: string, order: number | null = null): LineupSlot => ({
    player: player(id, `Player ${id}`),
    role,
    unit,
    order,
  });
  const lineup = (slots: LineupSlot[]): TeamLineup => ({ formation: null, slots, bench: [] });

  it("keeps a batting order as one group, in the order sent", () => {
    const groups = lineupGroups("baseball", lineup([slot("a", "RF", "BAT", 1), slot("b", "DH", "BAT", 2)]));
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Batting order");
    expect(groups[0].slots.map((s) => s.player.id)).toEqual(["a", "b"]);
  });

  it("groups a soccer side from the back, whatever order the slots come in", () => {
    const groups = lineupGroups(
      "soccer",
      lineup([slot("st", "ST", "FWD"), slot("gk", "GK", "GK"), slot("cm", "CM", "MID"), slot("cb", "CB", "DEF"), slot("lb", "LB", "DEF")]),
    );
    expect(groups.map((g) => [g.label, g.slots.map((s) => s.player.id)])).toEqual([
      ["Goalkeeper", ["gk"]],
      ["Defence", ["cb", "lb"]],
      ["Midfield", ["cm"]],
      ["Attack", ["st"]],
    ]);
  });

  it("names the units of the other sports", () => {
    expect(lineupGroups("basketball", lineup([slot("c", "C", "C"), slot("g", "PG", "G")])).map((g) => g.label)).toEqual(["Guards", "Center"]);
    expect(lineupGroups("hockey", lineup([slot("f", "LW", "F"), slot("g", "G", "G")])).map((g) => g.label)).toEqual(["Goalie", "Forwards"]);
    expect(lineupGroups("football", lineup([slot("k", "K", "ST"), slot("q", "QB", "OFF")])).map((g) => g.label)).toEqual(["Offense", "Special Teams"]);
    expect(lineupGroups("volleyball", lineup([slot("o", "OH", "OH"), slot("s", "S", "S")])).map((g) => g.label)).toEqual(["Setter", "Outside"]);
  });

  it("keeps a slot whose unit the sport does not name, rather than drop it", () => {
    const groups = lineupGroups("soccer", lineup([slot("gk", "GK", "GK"), slot("x", "WB", "WING"), slot("y", "WB", "WING"), slot("z", "?", "")]));
    expect(groups.map((g) => [g.label, g.slots.length])).toEqual([
      ["Goalkeeper", 1],
      ["WING", 2],
      ["Lineup", 1],
    ]);
    expect(lineupGroups("tennis", lineup([slot("a", "P", "SINGLES")])).map((g) => g.label)).toEqual(["SINGLES"]);
  });

  it("has no groups for an empty lineup", () => {
    expect(lineupGroups("soccer", lineup([]))).toEqual([]);
  });

  it("numbers a batting order, and nothing else", () => {
    expect(slotOrder(slot("a", "RF", "BAT", 3), 0, "baseball")).toBe("3");
    expect(slotOrder(slot("a", "RF", "BAT", null), 4, "baseball")).toBe("5");
    expect(slotOrder(slot("a", "GK", "GK", 1), 0, "soccer")).toBe("");
  });

  it("puts a player's availability in words", () => {
    expect(statusLabel("day_to_day")).toBe("Day-to-day");
    expect(statusLabel("injured")).toBe("Injured");
    expect(statusLabel("out")).toBe("Out");
    expect(statusLabel("active")).toBe("Active");
  });
});

// ── Team identity ───────────────────────────────────────────────────────

describe("catalogLeagues", () => {
  const leagues = [
    league(),
    league({ id: "epl", sport: "soccer", name: "Premier League" }),
    league({ id: "atp", sport: "tennis", name: "ATP" }),
    league({ id: "ufc", sport: "mma", name: "UFC" }),
    league({ id: "vol", sport: "volleyball", name: "PlusLiga", provider: "thesportsdb" }),
  ];

  it("has a catalog for an ESPN team sport only", () => {
    expect(hasCatalog(leagues[0])).toBe(true);
    expect(hasCatalog(leagues[1])).toBe(true);
    expect(hasCatalog(leagues[2])).toBe(false);
    expect(hasCatalog(leagues[3])).toBe(false);
    expect(hasCatalog(leagues[4])).toBe(false);
  });

  it("lists the leagues of the games on offer once each, in the games' order", () => {
    const games = [
      game({ league_id: "epl" }),
      game({ league_id: "atp" }),
      game({ league_id: "mlb" }),
      game({ league_id: "epl" }),
      game({ league_id: "vol" }),
      game({ league_id: "unknown" }),
    ];
    expect(catalogLeagues(games, leagues)).toEqual(["epl", "mlb"]);
  });

  it("asks for a handful at most", () => {
    const many = Array.from({ length: 9 }, (_, index) => league({ id: `l${index}` }));
    const games = many.map((entry) => game({ league_id: entry.id }));
    expect(catalogLeagues(games, many, 3)).toEqual(["l0", "l1", "l2"]);
    expect(catalogLeagues(games, many)).toHaveLength(6);
    expect(catalogLeagues([], many)).toEqual([]);
  });
});
