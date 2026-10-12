import { describe, expect, it } from "vitest";

import type {
  Game,
  GameSide,
  League,
  LeaderRow,
  SportEvent,
  Team,
  Weather,
} from "../../types";
import {
  COMING_UP_ROWS,
  FULL_CARDS_MAX,
  WEATHER_BATCH,
  WEEK_STARTS_ON,
  DAY_LIST_BESIDE_MAX,
  MONTH_CELL_DOTS,
  anchorForUnit,
  busiestDayRows,
  captionTeam,
  cardLabel,
  cardOutcome,
  cardSideName,
  cardStatus,
  cellDots,
  comingUp,
  comingUpEvents,
  comingUpFoot,
  comingUpTitle,
  comingUpWindow,
  competitionName,
  contextLine,
  dayCellLabel,
  dayKeys,
  dayOfMonth,
  daySpan,
  daySpoken,
  daysBetween,
  defaultDay,
  eventDayOf,
  eventDays,
  eventKind,
  eventLabel,
  eventRow,
  eventState,
  eventsOn,
  fetchWindow,
  fightLine,
  followedNoun,
  followedRows,
  gameDay,
  gamesByDay,
  gamesInRange,
  hasFollowedSide,
  hasLiveGame,
  hasStandings,
  holdsDay,
  isBusyDay,
  layoutBars,
  legendRows,
  liveShort,
  monthRange,
  monthShort,
  monthWeeks,
  offNote,
  overlayLatest,
  overlayLatestEvents,
  pairFigure,
  pickGames,
  plainCaption,
  playsIn,
  pointOfView,
  rangeCaption,
  rangeFor,
  rangeLabel,
  rangePhrase,
  rangeSpoken,
  rangeTitle,
  resolveRange,
  rowSides,
  settleAnchor,
  shiftRange,
  shortDay,
  sideValue,
  stepAnchor,
  tally,
  teamCaption,
  weatherGameIds,
  weatherLine,
  weekRange,
  weekdayLong,
  weekdayOf,
  wordsFor,
  weekFromMonth,
  wordsForRange,
  type DayRange,
} from "./model";

// The suite runs in America/New_York (see the `test` script): a game is
// placed on the New York day it starts on, and every start time below is
// written with its offset (-04:00 in summer, -05:00 in winter).

// ── Fixtures ────────────────────────────────────────────────────────────

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
const CHE: Team = {
  id: "che",
  league_id: "epl",
  name: "Chelsea",
  abbreviation: "CHE",
  logo_url: null,
  color: "#144992",
};
const SIN: Team = {
  id: "sin",
  league_id: "atp",
  name: "Jannik Sinner",
  abbreviation: "J. Sinne",
  logo_url: null,
  color: null,
};
const MOR: Team = {
  id: "mor",
  league_id: "pga",
  name: "Collin Morikawa",
  abbreviation: "C. Morik",
  logo_url: null,
  color: null,
};
const NOR: Team = {
  id: "nor",
  league_id: "f1",
  name: "Lando Norris",
  abbreviation: "NOR",
  logo_url: null,
  color: "#FF8000",
};

function league(id: string, sport: League["sport"], name: string, followAll = false): League {
  return { id, sport, name, provider: "espn", follow_all: followAll };
}
const MLB = league("mlb", "baseball", "MLB");
const EPL = league("epl", "soccer", "Premier League");
const LALIGA = league("laliga", "soccer", "LaLiga", true);
const ATP = league("atp", "tennis", "ATP");
const PGA = league("pga", "golf", "PGA Tour", true);
const F1 = league("f1", "racing", "Formula 1");
const LEAGUES = [MLB, EPL, LALIGA, ATP, PGA, F1];

function side(
  name: string,
  score: number | null = null,
  teamId: string | null = null,
  abbreviation: string | null = null,
): GameSide {
  return { team_id: teamId, name, abbreviation, logo_url: null, color: null, score };
}

/** A game; by default Boston at the (followed) Athletics, still to start. */
function game(over: Partial<Game> = {}): Game {
  return {
    id: "g",
    league_id: "mlb",
    sport: "baseball",
    home: side("Athletics", null, "ath", "ATH"),
    away: side("Boston Red Sox", null, null, "BOS"),
    start_time: "2026-07-30T19:05:00-04:00",
    venue: "Sutter Health Park",
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

/** A finished game, away score first. */
function final(id: string, start: string, away: GameSide, home: GameSide, followed: string[]): Game {
  return game({ id, start_time: start, away, home, phase: "final", followed_team_ids: followed });
}

/** A LaLiga match nobody followed is in (a whole-competition row). */
function laliga(id: string, start: string, over: Partial<Game> = {}): Game {
  return game({
    id,
    league_id: "laliga",
    sport: "soccer",
    away: side("Real Betis", null, null, "BET"),
    home: side("Sevilla", null, null, "SEV"),
    start_time: start,
    venue: "Ramón Sánchez-Pizjuán",
    followed_team_ids: [],
    ...over,
  });
}

function leaderRow(over: Partial<LeaderRow> = {}): LeaderRow {
  return {
    position: 1,
    position_label: "1",
    name: "Scottie Scheffler",
    score: "-7",
    detail: null,
    player_id: null,
    ...over,
  };
}

function event(over: Partial<SportEvent> = {}): SportEvent {
  return {
    id: "open",
    league_id: "pga",
    sport: "golf",
    name: "3M Open",
    start_time: "2026-07-30T08:00:00-04:00",
    end_time: "2026-08-02T18:00:00-04:00",
    phase: "scheduled",
    round_label: "",
    venue: "TPC Twin Cities",
    followed_player_ids: [],
    leaderboard: [],
    ...over,
  };
}

const TODAY = "2026-07-30"; // a Thursday
const THIS_WEEK: DayRange = { first: "2026-07-27", last: "2026-08-02" };
const JULY: DayRange = { first: "2026-07-01", last: "2026-07-31" };

const ath = (score: number | null = null) => side("Athletics", score, "ath", "ATH");
const ari = (score: number | null = null) => side("Arizona Diamondbacks", score, "ari", "ARI");
const bos = (score: number | null = null) => side("Boston Red Sox", score, null, "BOS");
const det = (score: number | null = null) => side("Detroit Tigers", score, null, "DET");
const lad = (score: number | null = null) => side("Los Angeles Dodgers", score, null, "LAD");
const cin = (score: number | null = null) => side("Cincinnati Reds", score, null, "CIN");
const tex = (score: number | null = null) => side("Texas Rangers", score, null, "TEX");

/**
 * The mockup's week as the API would send it for the padded request: the
 * Sunday before and the Monday after come along and belong to neither the
 * grid nor any count.
 */
const MOCK_FETCH: Game[] = [
  final("sun-before", "2026-07-26T16:05:00-04:00", ath(1), side("Seattle Mariners", 7), ["ath"]),
  final("mon", "2026-07-27T19:05:00-04:00", bos(4), ath(2), ["ath"]),
  final("tue", "2026-07-28T19:05:00-04:00", bos(3), ath(4), ["ath"]),
  final("wed", "2026-07-29T19:05:00-04:00", bos(4), ath(2), ["ath"]),
  final("wed-ari", "2026-07-29T19:10:00-04:00", ari(6), lad(3), ["ari"]),
  game({
    id: "thu",
    away: bos(3),
    home: ath(4),
    phase: "in_progress",
    period: 5,
    period_label: "Bot 5",
  }),
  game({
    id: "thu-ari",
    start_time: "2026-07-30T22:10:00-04:00",
    away: ari(),
    home: lad(),
    venue: "Dodger Stadium",
    followed_team_ids: ["ari"],
  }),
  game({ id: "fri", start_time: "2026-07-31T21:05:00-04:00", away: det(), home: ath() }),
  game({ id: "sat", start_time: "2026-08-01T21:05:00-04:00", away: det(), home: ath() }),
  game({ id: "sun", start_time: "2026-08-02T15:05:00-04:00", away: det(), home: ath() }),
  game({ id: "mon-after", start_time: "2026-08-03T19:10:00-04:00", away: ath(), home: cin() }),
];
const MOCK_WEEK = gamesInRange(MOCK_FETCH, THIS_WEEK);

// ── Ranges ──────────────────────────────────────────────────────────────

describe("weekRange", () => {
  it("starts the week on Monday, as the design is drawn", () => {
    expect(WEEK_STARTS_ON).toBe(1);
    expect(weekRange("2026-07-30")).toEqual(THIS_WEEK);
  });

  it("puts a Monday first and the Sunday after it last", () => {
    expect(weekRange("2026-07-27")).toEqual(THIS_WEEK);
    expect(weekRange("2026-08-02")).toEqual(THIS_WEEK);
    expect(weekRange("2026-07-26").last).toBe("2026-07-26");
    expect(weekRange("2026-08-03").first).toBe("2026-08-03");
  });

  it("runs across the end of a month and of a year", () => {
    expect(weekRange("2026-08-01")).toEqual(THIS_WEEK);
    expect(weekRange("2027-01-01")).toEqual({ first: "2026-12-28", last: "2027-01-03" });
  });

  it("is seven whole days in the weeks the clocks change", () => {
    // In New York the clocks go forward on Sunday 8 March 2026 (a 23-hour
    // day) and back on Sunday 1 November (25 hours).
    const spring = weekRange("2026-03-08");
    expect(spring).toEqual({ first: "2026-03-02", last: "2026-03-08" });
    expect(dayKeys(spring)).toEqual([
      "2026-03-02",
      "2026-03-03",
      "2026-03-04",
      "2026-03-05",
      "2026-03-06",
      "2026-03-07",
      "2026-03-08",
    ]);
    expect(weekRange("2026-03-09")).toEqual({ first: "2026-03-09", last: "2026-03-15" });

    const autumn = weekRange("2026-11-01");
    expect(autumn).toEqual({ first: "2026-10-26", last: "2026-11-01" });
    expect(dayKeys(autumn)).toHaveLength(7);
    expect(dayKeys(autumn)[6]).toBe("2026-11-01");
    expect(weekRange("2026-11-02")).toEqual({ first: "2026-11-02", last: "2026-11-08" });
  });
});

describe("monthRange", () => {
  it("is the calendar month a day is in", () => {
    expect(monthRange("2026-07-30")).toEqual(JULY);
    expect(monthRange("2026-12-31")).toEqual({ first: "2026-12-01", last: "2026-12-31" });
  });

  it("knows February, leap year or not", () => {
    expect(monthRange("2026-02-10").last).toBe("2026-02-28");
    expect(monthRange("2028-02-10").last).toBe("2028-02-29");
  });

  it("rangeFor picks by unit", () => {
    expect(rangeFor("week", TODAY)).toEqual(THIS_WEEK);
    expect(rangeFor("month", TODAY)).toEqual(JULY);
  });
});

describe("daysBetween and dayKeys", () => {
  it("counts whole days, backwards as negatives", () => {
    expect(daysBetween("2026-07-27", "2026-08-02")).toBe(6);
    expect(daysBetween("2026-08-02", "2026-07-27")).toBe(-6);
    expect(daysBetween(TODAY, TODAY)).toBe(0);
  });

  it("is not thrown by a 23-hour or a 25-hour day", () => {
    expect(daysBetween("2026-03-07", "2026-03-09")).toBe(2);
    expect(daysBetween("2026-10-31", "2026-11-02")).toBe(2);
  });

  it("lists every day of a month, both ends included", () => {
    const days = dayKeys(JULY);
    expect(days).toHaveLength(31);
    expect(days[0]).toBe("2026-07-01");
    expect(days[30]).toBe("2026-07-31");
    // November 2026 holds the 25-hour day: still thirty keys, none twice.
    const november = dayKeys(monthRange("2026-11-15"));
    expect(november).toHaveLength(30);
    expect(new Set(november).size).toBe(30);
  });

  it("holdsDay includes both ends", () => {
    expect(holdsDay(THIS_WEEK, "2026-07-27")).toBe(true);
    expect(holdsDay(THIS_WEEK, "2026-08-02")).toBe(true);
    expect(holdsDay(THIS_WEEK, "2026-07-26")).toBe(false);
    expect(holdsDay(THIS_WEEK, "2026-08-03")).toBe(false);
  });
});

describe("shiftRange", () => {
  it("moves by whole weeks", () => {
    expect(shiftRange("week", THIS_WEEK, 1)).toEqual({ first: "2026-08-03", last: "2026-08-09" });
    expect(shiftRange("week", THIS_WEEK, -1)).toEqual({ first: "2026-07-20", last: "2026-07-26" });
    expect(shiftRange("week", THIS_WEEK, 0)).toEqual(THIS_WEEK);
  });

  it("lands on a Monday on the far side of a clock change", () => {
    expect(shiftRange("week", weekRange("2026-03-02"), 1).first).toBe("2026-03-09");
    expect(shiftRange("week", weekRange("2026-03-09"), -1).first).toBe("2026-03-02");
    expect(shiftRange("week", weekRange("2026-10-26"), 1).first).toBe("2026-11-02");
    expect(shiftRange("week", weekRange("2026-11-02"), -1).first).toBe("2026-10-26");
  });

  it("moves by whole months, short ones included", () => {
    expect(shiftRange("month", JULY, 1)).toEqual({ first: "2026-08-01", last: "2026-08-31" });
    expect(shiftRange("month", monthRange("2026-01-31"), 1)).toEqual({
      first: "2026-02-01",
      last: "2026-02-28",
    });
    expect(shiftRange("month", monthRange("2026-12-15"), 1)).toEqual({
      first: "2027-01-01",
      last: "2027-01-31",
    });
    expect(shiftRange("month", monthRange("2026-01-15"), -1)).toEqual({
      first: "2025-12-01",
      last: "2025-12-31",
    });
  });
});

describe("the anchor: following today, or staying where the user went", () => {
  it("with no anchor the range is today's own", () => {
    expect(resolveRange("week", null, TODAY)).toEqual(THIS_WEEK);
    expect(resolveRange("month", null, TODAY)).toEqual(JULY);
  });

  it("so a view left on this week moves to the new week by itself", () => {
    // Sunday night on a kiosk, then Monday morning: nothing was pressed.
    expect(resolveRange("week", null, "2026-08-02")).toEqual(THIS_WEEK);
    expect(resolveRange("week", null, "2026-08-03")).toEqual({
      first: "2026-08-03",
      last: "2026-08-09",
    });
    expect(resolveRange("month", null, "2026-08-01").first).toBe("2026-08-01");
  });

  it("and a week the user went to stays put whatever the day", () => {
    const pinned = { first: "2026-08-17", last: "2026-08-23" };
    expect(resolveRange("week", "2026-08-17", TODAY)).toEqual(pinned);
    expect(resolveRange("week", "2026-08-17", "2026-08-04")).toEqual(pinned);
    expect(settleAnchor("week", "2026-08-17", "2026-08-04")).toBe("2026-08-17");
  });

  it("an anchor whose range holds today is dropped: that IS this week", () => {
    expect(settleAnchor("week", null, TODAY)).toBeNull();
    expect(settleAnchor("week", "2026-07-27", TODAY)).toBeNull();
    expect(settleAnchor("week", "2026-08-01", TODAY)).toBeNull();
    expect(settleAnchor("month", "2026-07-04", TODAY)).toBeNull();
    // Time reaches a week that was navigated to: from then on it follows.
    expect(settleAnchor("week", "2026-08-17", "2026-08-17")).toBeNull();
  });

  it("any other anchor is stored as the first day of its range", () => {
    expect(settleAnchor("week", "2026-08-12", TODAY)).toBe("2026-08-10");
    expect(settleAnchor("month", "2026-08-15", TODAY)).toBe("2026-08-01");
  });

  it("previous and next step from wherever the view is", () => {
    expect(stepAnchor("week", null, TODAY, 1)).toBe("2026-08-03");
    expect(stepAnchor("week", null, TODAY, -1)).toBe("2026-07-20");
    expect(stepAnchor("week", "2026-08-03", TODAY, 1)).toBe("2026-08-10");
    // Stepping back onto this week is following today again.
    expect(stepAnchor("week", "2026-08-03", TODAY, -1)).toBeNull();
    expect(stepAnchor("month", null, TODAY, 1)).toBe("2026-08-01");
    expect(stepAnchor("month", "2026-08-01", TODAY, -1)).toBeNull();
  });

  it("next month from the 31st is the next month, not the one after", () => {
    expect(stepAnchor("month", null, "2026-01-31", 1)).toBe("2026-02-01");
    expect(stepAnchor("month", null, "2026-03-31", -1)).toBe("2026-02-01");
  });

  it("changing the unit keeps following today when it was", () => {
    expect(anchorForUnit("month", "week", null, TODAY)).toBeNull();
    expect(anchorForUnit("week", "month", null, TODAY)).toBeNull();
  });

  it("a week becomes the month most of it lies in", () => {
    // 29 June to 5 July: four of its days are July's.
    expect(anchorForUnit("month", "week", "2026-06-29", "2026-05-15")).toBe("2026-07-01");
    // 27 July to 2 August is mostly July, which holds today.
    expect(anchorForUnit("month", "week", "2026-07-27", "2026-06-10")).toBe("2026-07-01");
    expect(anchorForUnit("month", "week", "2026-08-03", TODAY)).toBe("2026-08-01");
  });

  it("a month becomes the week its first day is in", () => {
    expect(anchorForUnit("week", "month", "2026-08-01", "2026-07-15")).toBe("2026-07-27");
    // That week holds today: it is this week.
    expect(anchorForUnit("week", "month", "2026-08-01", TODAY)).toBeNull();
    expect(anchorForUnit("week", "week", "2026-08-10", TODAY)).toBe("2026-08-10");
  });

  it("Week, Month and back returns to the week it left", () => {
    const august: DayRange = { first: "2026-08-01", last: "2026-08-31" };
    // Next week (3 to 9 August) opened August; back is 3 to 9 August, not
    // the week 1 August falls in (which is this one).
    expect(anchorForUnit("month", "week", "2026-08-03", TODAY)).toBe("2026-08-01");
    expect(weekFromMonth(august, null, "2026-08-03", TODAY)).toBe("2026-08-03");
    // This week opened July and comes back as "this week" (following today).
    expect(weekFromMonth(JULY, null, "2026-07-27", TODAY)).toBeNull();
    // A week across two months belongs to both.
    expect(weekFromMonth(august, null, "2026-07-27", "2026-06-10")).toBe("2026-07-27");
  });

  it("opens the week of the day chosen in the month, before anything else", () => {
    const august: DayRange = { first: "2026-08-01", last: "2026-08-31" };
    expect(weekFromMonth(august, "2026-08-20", "2026-08-03", TODAY)).toBe("2026-08-17");
    // Today chosen: this week, which follows today.
    expect(weekFromMonth(JULY, TODAY, "2026-07-06", TODAY)).toBeNull();
    // A day chosen in another month is not this month's choice.
    expect(weekFromMonth(august, "2026-07-14", "2026-08-03", TODAY)).toBe("2026-08-03");
  });

  it("falls back to today's week, then the month's first, once the month was stepped away", () => {
    const september: DayRange = { first: "2026-09-01", last: "2026-09-30" };
    // Entered from an August week, then stepped to September.
    expect(weekFromMonth(september, null, "2026-08-03", TODAY)).toBe("2026-08-31");
    // Stepped back to the month that holds today.
    expect(weekFromMonth(JULY, null, "2026-08-10", TODAY)).toBeNull();
    // Never came from a week at all (nothing remembered).
    expect(weekFromMonth(september, null, null, TODAY)).toBe("2026-08-31");
  });
});

describe("fetchWindow", () => {
  it("asks for a day more on each side", () => {
    expect(fetchWindow(THIS_WEEK)).toEqual({ start: "2026-07-26", end: "2026-08-03" });
    expect(fetchWindow(monthRange("2026-03-10"))).toEqual({
      start: "2026-02-28",
      end: "2026-04-01",
    });
  });
});

describe("monthWeeks", () => {
  it("covers the month in rows of seven that start on Monday", () => {
    // 1 July 2026 is a Wednesday and the 31st a Friday.
    const weeks = monthWeeks(JULY);
    expect(weeks).toHaveLength(5);
    expect(weeks.every((week) => week.length === 7)).toBe(true);
    expect(weeks[0][0]).toBe("2026-06-29");
    expect(weeks[0][2]).toBe("2026-07-01");
    expect(weeks[4][6]).toBe("2026-08-02");
  });

  it("takes six rows when it must and four when it can", () => {
    // August 2026 starts on a Saturday and ends on a Monday.
    const august = monthWeeks(monthRange("2026-08-10"));
    expect(august).toHaveLength(6);
    expect(august[0][0]).toBe("2026-07-27");
    expect(august[5][6]).toBe("2026-09-06");
    // February 2027 is exactly four Monday-to-Sunday weeks.
    const february = monthWeeks(monthRange("2027-02-10"));
    expect(february).toHaveLength(4);
    expect(february[0][0]).toBe("2027-02-01");
    expect(february[3][6]).toBe("2027-02-28");
  });

  it("keeps seven days a row across both clock changes", () => {
    for (const month of ["2026-03-15", "2026-11-15"]) {
      const weeks = monthWeeks(monthRange(month));
      expect(weeks.every((week) => week.length === 7)).toBe(true);
      expect(new Set(weeks.flat()).size).toBe(weeks.length * 7);
    }
  });
});

// ── Dates in words ──────────────────────────────────────────────────────

describe("dates in words", () => {
  it("names a day in its short and long forms", () => {
    expect(shortDay("2026-07-27")).toBe("Jul 27");
    expect(monthShort("2026-08-02")).toBe("Aug");
    expect(weekdayOf(TODAY)).toBe("Thu");
    expect(weekdayLong(TODAY)).toBe("Thursday");
    expect(dayOfMonth("2026-08-02")).toBe(2);
  });

  it("says when a day is today", () => {
    expect(daySpoken(TODAY, TODAY)).toBe("Thursday, July 30, today");
    expect(daySpoken("2026-07-27", TODAY)).toBe("Monday, July 27");
  });

  it("writes a run of days without repeating the month", () => {
    expect(daySpan(THIS_WEEK)).toBe("Jul 27 – Aug 2");
    expect(daySpan({ first: "2026-08-03", last: "2026-08-09" })).toBe("Aug 3 – 9");
    expect(daySpan({ first: "2026-08-03", last: "2026-08-03" })).toBe("Aug 3");
    expect(daySpan({ first: "2026-12-28", last: "2027-01-03" })).toBe("Dec 28 – Jan 3");
  });

  it("labels the range between the buttons, the year once", () => {
    expect(rangeLabel("week", THIS_WEEK)).toBe("Jul 27 – Aug 2, 2026");
    expect(rangeLabel("week", { first: "2026-08-03", last: "2026-08-09" })).toBe(
      "Aug 3 – 9, 2026",
    );
    expect(rangeLabel("week", { first: "2026-12-28", last: "2027-01-03" })).toBe(
      "Dec 28, 2026 – Jan 3, 2027",
    );
    expect(rangeLabel("month", JULY)).toBe("July 2026");
  });

  it("titles the range by how far it is from today", () => {
    expect(rangeTitle("week", THIS_WEEK, TODAY)).toBe("This week");
    expect(rangeTitle("week", weekRange("2026-08-05"), TODAY)).toBe("Next week");
    expect(rangeTitle("week", weekRange("2026-07-22"), TODAY)).toBe("Last week");
    expect(rangeTitle("week", weekRange("2026-08-12"), TODAY)).toBe("Week of Aug 10");
    expect(rangeTitle("week", weekRange("2026-07-14"), TODAY)).toBe("Week of Jul 13");
    expect(rangeTitle("month", JULY, TODAY)).toBe("This month");
    expect(rangeTitle("month", monthRange("2026-08-15"), TODAY)).toBe("Next month");
    expect(rangeTitle("month", monthRange("2026-06-15"), TODAY)).toBe("Last month");
    expect(rangeTitle("month", monthRange("2026-10-15"), TODAY)).toBe("October");
    expect(rangeTitle("month", monthRange("2027-02-15"), TODAY)).toBe("February 2027");
  });

  it("still calls it next week across a clock change and a new year", () => {
    expect(rangeTitle("week", weekRange("2026-03-09"), "2026-03-05")).toBe("Next week");
    expect(rangeTitle("week", weekRange("2026-10-26"), "2026-11-03")).toBe("Last week");
    expect(rangeTitle("week", weekRange("2027-01-05"), "2026-12-30")).toBe("Next week");
    expect(rangeTitle("month", monthRange("2027-01-15"), "2026-12-30")).toBe("Next month");
  });

  it("puts the range in a sentence", () => {
    expect(rangePhrase("week", THIS_WEEK, TODAY)).toBe("this week");
    expect(rangePhrase("week", weekRange("2026-07-22"), TODAY)).toBe("last week");
    expect(rangePhrase("month", monthRange("2026-08-15"), TODAY)).toBe("next month");
    expect(rangePhrase("week", weekRange("2026-08-12"), TODAY)).toBe("that week");
  });

  it("names the section for a screen reader", () => {
    expect(rangeSpoken("week", THIS_WEEK)).toBe("Week of July 27 to August 2");
    expect(rangeSpoken("month", JULY)).toBe("July 2026");
  });
});

// ── Games on days ───────────────────────────────────────────────────────

describe("gameDay", () => {
  it("is the viewer's local day the game starts on", () => {
    expect(gameDay(game({ start_time: "2026-07-30T19:05:00-04:00" }))).toBe("2026-07-30");
    // A 9:40 PM start on the Pacific coast is 12:40 AM in New York.
    expect(gameDay(game({ start_time: "2026-07-30T21:40:00-07:00" }))).toBe("2026-07-31");
    expect(gameDay(game({ start_time: "2026-07-31T03:59:00Z" }))).toBe("2026-07-30");
    expect(gameDay(game({ start_time: "2026-07-31T04:00:00Z" }))).toBe("2026-07-31");
  });

  it("keeps a game that runs past midnight on the day it began", () => {
    // 11:30 PM Thursday, still going at half past midnight on Friday.
    const late = game({
      id: "late",
      start_time: "2026-07-30T23:30:00-04:00",
      phase: "in_progress",
      period: 7,
      period_label: "Top 7",
      away: bos(2),
      home: ath(2),
    });
    expect(gameDay(late)).toBe("2026-07-30");
    expect(gamesByDay([late]).get("2026-07-30")).toEqual([late]);
    expect(gamesByDay([late]).has("2026-07-31")).toBe(false);
    expect(hasLiveGame([late])).toBe(true);
    // Sunday's late game is this week's, though it ends on next week's Monday.
    const sunday = { ...late, start_time: "2026-08-02T23:30:00-04:00" };
    expect(gamesInRange([sunday], THIS_WEEK)).toEqual([sunday]);
    expect(gamesInRange([sunday], { first: "2026-08-03", last: "2026-08-09" })).toEqual([]);
  });
});

describe("overlayLatest", () => {
  it("lays the polled copy over the schedule's, by id", () => {
    const stale = game({ id: "thu" });
    const other = game({ id: "fri", start_time: "2026-07-31T21:05:00-04:00" });
    const live = game({
      id: "thu",
      phase: "in_progress",
      period_label: "Bot 5",
      away: bos(3),
      home: ath(4),
    });
    expect(overlayLatest([stale, other], [live])).toEqual([live, other]);
  });

  it("adds nothing the schedule does not hold, and copies when there is nothing newer", () => {
    const scheduled = game({ id: "fri" });
    const elsewhere = game({ id: "not-in-range" });
    expect(overlayLatest([scheduled], [elsewhere])).toEqual([scheduled]);
    const schedule = [scheduled];
    const same = overlayLatest(schedule, []);
    expect(same).toEqual(schedule);
    expect(same).not.toBe(schedule);
  });

  it("does the same for a tournament's leaderboard", () => {
    const stored = event({ id: "open" });
    const fresh = event({
      id: "open",
      phase: "in_progress",
      round_label: "Round 1",
      leaderboard: [leaderRow()],
    });
    expect(overlayLatestEvents([stored], [fresh])).toEqual([fresh]);
    expect(overlayLatestEvents([stored], [event({ id: "other" })])).toEqual([stored]);
    expect(overlayLatestEvents([stored], [])).toEqual([stored]);
  });
});

describe("gamesInRange", () => {
  it("drops what the padded fetch brought in from outside the range", () => {
    expect(MOCK_FETCH).toHaveLength(11);
    expect(MOCK_WEEK.map((entry) => entry.id)).toEqual([
      "mon",
      "tue",
      "wed",
      "wed-ari",
      "thu",
      "thu-ari",
      "fri",
      "sat",
      "sun",
    ]);
  });

  it("cuts at local midnight on both edges", () => {
    const at = (id: string, start: string) => game({ id, start_time: start });
    const games = [
      at("before", "2026-07-26T23:59:00-04:00"),
      at("first", "2026-07-27T00:00:00-04:00"),
      at("last", "2026-08-02T23:59:00-04:00"),
      at("after", "2026-08-03T00:00:00-04:00"),
    ];
    expect(gamesInRange(games, THIS_WEEK).map((entry) => entry.id)).toEqual(["first", "last"]);
  });

  it("holds its edges in the week the clocks go forward", () => {
    const week = weekRange("2026-03-08");
    const at = (id: string, start: string) => game({ id, start_time: start });
    const games = [
      at("sat-late", "2026-03-07T23:30:00-05:00"),
      at("before-the-jump", "2026-03-08T01:30:00-05:00"),
      at("after-the-jump", "2026-03-08T03:30:00-04:00"),
      at("sun-late", "2026-03-08T23:30:00-04:00"),
      at("mon-early", "2026-03-09T00:30:00-04:00"),
    ];
    expect(gamesInRange(games, week).map((entry) => entry.id)).toEqual([
      "sat-late",
      "before-the-jump",
      "after-the-jump",
      "sun-late",
    ]);
    expect(gamesByDay(games).get("2026-03-08")?.map((entry) => entry.id)).toEqual([
      "before-the-jump",
      "after-the-jump",
      "sun-late",
    ]);
  });

  it("and in the week they go back, where 1:30 AM comes twice", () => {
    const week = weekRange("2026-11-01");
    const at = (id: string, start: string) => game({ id, start_time: start });
    const games = [
      at("first-0130", "2026-11-01T01:30:00-04:00"),
      at("second-0130", "2026-11-01T01:30:00-05:00"),
      at("sun-late", "2026-11-01T23:30:00-05:00"),
      at("mon-early", "2026-11-02T00:15:00-05:00"),
    ];
    expect(gamesInRange(games, week).map((entry) => entry.id)).toEqual([
      "first-0130",
      "second-0130",
      "sun-late",
    ]);
    expect(gamesInRange(games, weekRange("2026-11-02")).map((entry) => entry.id)).toEqual([
      "mon-early",
    ]);
  });

  it("cuts a month at its first and last day", () => {
    const at = (id: string, start: string) => game({ id, start_time: start });
    const games = [
      at("june", "2026-06-30T23:30:00-04:00"),
      at("first", "2026-07-01T00:05:00-04:00"),
      at("last", "2026-07-31T23:55:00-04:00"),
      at("august", "2026-08-01T00:05:00-04:00"),
    ];
    expect(gamesInRange(games, JULY).map((entry) => entry.id)).toEqual(["first", "last"]);
  });

  it("keeps one team's games when filtered, in start order", () => {
    expect(playsIn(MOCK_FETCH[4], "ari")).toBe(true);
    expect(playsIn(MOCK_FETCH[4], "ath")).toBe(false);
    expect(gamesInRange(MOCK_FETCH, THIS_WEEK, "ari").map((entry) => entry.id)).toEqual([
      "wed-ari",
      "thu-ari",
    ]);
    const shuffled = [MOCK_FETCH[9], MOCK_FETCH[1], MOCK_FETCH[5]];
    expect(gamesInRange(shuffled, THIS_WEEK).map((entry) => entry.id)).toEqual([
      "mon",
      "thu",
      "sun",
    ]);
  });
});

describe("gamesByDay", () => {
  it("buckets by day in start order and leaves empty days out", () => {
    const days = gamesByDay(MOCK_WEEK);
    expect(days.get("2026-07-29")?.map((entry) => entry.id)).toEqual(["wed", "wed-ari"]);
    expect(days.get("2026-07-30")?.map((entry) => entry.id)).toEqual(["thu", "thu-ari"]);
    expect(days.size).toBe(7);
    expect(gamesByDay([MOCK_WEEK[0]]).has("2026-07-28")).toBe(false);
  });
});

describe("tally", () => {
  it("counts the week as the mockup does: 4 final, 1 live, 4 upcoming", () => {
    expect(tally(MOCK_WEEK)).toEqual({
      final: 4,
      live: 1,
      upcoming: 4,
      postponed: 0,
      canceled: 0,
    });
  });

  it("would be wrong by two without the clipping", () => {
    expect(tally(MOCK_FETCH).final).toBe(5);
    expect(tally(MOCK_FETCH).upcoming).toBe(5);
  });

  it("keeps postponed and canceled games out of the three", () => {
    const counts = tally([
      ...MOCK_WEEK,
      game({ id: "ppd", phase: "postponed" }),
      game({ id: "canc", phase: "canceled" }),
      game({ id: "canc2", phase: "canceled" }),
    ]);
    expect(counts).toEqual({ final: 4, live: 1, upcoming: 4, postponed: 1, canceled: 2 });
    expect(offNote(counts)).toBe("1 postponed · 2 canceled");
    expect(offNote(tally(MOCK_WEEK))).toBeNull();
    expect(offNote({ ...counts, postponed: 0 })).toBe("2 canceled");
  });

  it("counts one team's games when the list was filtered", () => {
    expect(tally(gamesInRange(MOCK_FETCH, THIS_WEEK, "ari"))).toMatchObject({
      final: 1,
      live: 0,
      upcoming: 1,
    });
  });
});

describe("wordsFor", () => {
  it("uses the sport's own word when every game shares it", () => {
    expect(wordsFor([laliga("a", "2026-08-01T12:00:00-04:00")]).games).toBe("matches");
    expect(wordsFor([game({ sport: "mma" }), game({ sport: "mma" })]).game).toBe("bout");
    // Tennis and soccer both say "match".
    expect(wordsFor([laliga("a", "2026-08-01T12:00:00-04:00"), game({ sport: "tennis" })]).game).toBe(
      "match",
    );
  });

  it("falls back to games for a mixture, or nothing", () => {
    expect(wordsFor([game(), laliga("a", "2026-08-01T12:00:00-04:00")]).games).toBe("games");
    expect(wordsFor([]).games).toBe("games");
  });
});

describe("wordsForRange", () => {
  it("is the word for the games on screen when there are any", () => {
    expect(wordsForRange(MOCK_WEEK, [EPL]).games).toBe("games");
    expect(wordsForRange([laliga("a", "2026-08-01T12:00:00-04:00")], [MLB]).games).toBe("matches");
  });

  it("in an empty range, is what the followed competitions play when they agree", () => {
    // Somebody who follows only soccer reads "No matches", as the Legend says.
    expect(wordsForRange([], [LALIGA, EPL]).games).toBe("matches");
    expect(wordsForRange([], [MLB]).games).toBe("games");
    // Tennis and soccer are both matches; baseball beside them is not.
    expect(wordsForRange([], [EPL, ATP]).games).toBe("matches");
    expect(wordsForRange([], [EPL, MLB]).games).toBe("games");
    // A tour has no games: it does not vote.
    expect(wordsForRange([], [LALIGA, PGA, F1]).games).toBe("matches");
    expect(wordsForRange([], [PGA]).games).toBe("games");
    expect(wordsForRange([], []).games).toBe("games");
  });
});

describe("pickGames", () => {
  const matchday = Array.from({ length: 10 }, (_, index) =>
    laliga(`ll${index}`, `2026-08-01T${String(8 + index).padStart(2, "0")}:00:00-04:00`),
  );
  const followed = game({ id: "che", start_time: "2026-08-01T20:00:00-04:00" });

  it("returns everything, in start order, when it fits", () => {
    const { shown, more } = pickGames([matchday[2], matchday[0], matchday[1]], 4);
    expect(shown.map((entry) => entry.id)).toEqual(["ll0", "ll1", "ll2"]);
    expect(more).toBe(0);
  });

  it("never cuts a followed team's game, and says how many it left out", () => {
    const { shown, more } = pickGames([...matchday, followed], 4);
    // The followed game is the latest of the eleven and still makes the cut.
    expect(shown.map((entry) => entry.id)).toEqual(["ll0", "ll1", "ll2", "che"]);
    expect(more).toBe(7);
    expect(shown.length + more).toBe(11);
  });

  it("a day switches to compact cards past three", () => {
    expect(FULL_CARDS_MAX).toBe(3);
    expect(isBusyDay(3)).toBe(false);
    expect(isBusyDay(4)).toBe(true);
    expect(isBusyDay(10)).toBe(true);
  });
});

// ── A card ──────────────────────────────────────────────────────────────

describe("cardStatus", () => {
  it("is the start time before a game", () => {
    // With the instant itself, for the <time> element's dateTime.
    expect(cardStatus(game())).toEqual({
      kind: "time",
      text: "7:05 PM",
      iso: "2026-07-30T19:05:00-04:00",
    });
    // Shown in the viewer's zone: 10:10 PM Pacific is 1:10 AM here.
    expect(cardStatus(game({ start_time: "2026-07-30T22:10:00-07:00" }))).toEqual({
      kind: "time",
      text: "1:10 AM",
      iso: "2026-07-30T22:10:00-07:00",
    });
  });

  it("writes a baseball half-inning as a scorecard does", () => {
    expect(cardStatus(MOCK_WEEK[4])).toEqual({
      kind: "live",
      text: "Bot 5th",
      title: "Bottom 5th",
    });
  });

  it("carries the clock for the sports that have one", () => {
    const nba = game({
      sport: "basketball",
      phase: "in_progress",
      period: 4,
      period_label: "Q4",
      clock: "3:12",
    });
    expect(cardStatus(nba)).toEqual({ kind: "live", text: "Q4 3:12", title: "Q4 3:12" });
    expect(liveShort(nba)).toBe("Q4 3:12");
  });

  it("tells soccer by its minute alone, and the half in the long form", () => {
    const soccer = laliga("live", "2026-07-30T15:00:00-04:00", {
      phase: "in_progress",
      period: 2,
      period_label: "2nd Half",
      clock: "71'",
    });
    expect(cardStatus(soccer)).toEqual({ kind: "live", text: "71'", title: "2nd Half 71'" });
    expect(liveShort({ ...soccer, clock: null })).toBe("2nd Half");
  });

  it("shows a break as a break, with no clock", () => {
    const halfTime = laliga("ht", "2026-07-30T15:00:00-04:00", {
      phase: "in_progress",
      period: 1,
      period_label: "1st Half",
      clock: "45'",
      is_intermission: true,
    });
    expect(cardStatus(halfTime)).toEqual({ kind: "live", text: "HT", title: "Half time" });
    const hockey = game({
      sport: "hockey",
      phase: "in_progress",
      period: 2,
      period_label: "P2",
      clock: "0:00",
      is_intermission: true,
    });
    expect(cardStatus(hockey)).toEqual({ kind: "live", text: "End P2", title: "End of P2" });
  });

  it("names the states the mockup does not draw", () => {
    expect(cardStatus(game({ phase: "final" }))).toEqual({ kind: "final" });
    expect(cardStatus(game({ phase: "postponed" }))).toEqual({
      kind: "off",
      text: "Postponed",
      short: "PPD",
    });
    expect(cardStatus(game({ phase: "canceled" }))).toEqual({
      kind: "off",
      text: "Canceled",
      short: "Canc",
    });
  });
});

describe("sideValue", () => {
  it("is the score once there is one, the side in front set forward", () => {
    const lost = MOCK_WEEK[0]; // Boston 4, Athletics 2
    expect(sideValue(lost, "away")).toEqual({ kind: "score", value: 4, lead: true });
    expect(sideValue(lost, "home")).toEqual({ kind: "score", value: 2, lead: false });
    const live = MOCK_WEEK[4]; // Boston 3, Athletics 4, in progress
    expect(sideValue(live, "home")).toEqual({ kind: "score", value: 4, lead: true });
  });

  it("sets both forward when level", () => {
    const draw = final("d", "2026-07-27T12:00:00-04:00", side("Brighton", 1), side("Chelsea", 1, "che"), [
      "che",
    ]);
    expect(sideValue({ ...draw, sport: "soccer" }, "away")).toMatchObject({ lead: true });
    expect(sideValue({ ...draw, sport: "soccer" }, "home")).toMatchObject({ lead: true });
  });

  it("says Away and Home for a team game still to start", () => {
    expect(sideValue(game(), "away")).toEqual({ kind: "tag", text: "Away" });
    expect(sideValue(game(), "home")).toEqual({ kind: "tag", text: "Home" });
  });

  it("says nothing of the kind for people, who have no home", () => {
    const tennis = game({ sport: "tennis" });
    expect(sideValue(tennis, "away")).toBeNull();
    expect(sideValue(tennis, "home")).toBeNull();
  });

  it("never shows a fight's round count; the winner is marked instead", () => {
    const bout = game({
      sport: "mma",
      phase: "final",
      away: side("Islam Makhachev", 1, "mak"),
      home: side("Arman Tsarukyan", 0),
      fight_result: { method: "submission", detail: "Rear-Naked Choke", round: 2, clock: "4:21" },
      followed_team_ids: ["mak"],
    });
    expect(sideValue(bout, "away")).toEqual({ kind: "tag", text: "Won" });
    expect(sideValue(bout, "home")).toBeNull();
    const noContest = { ...bout, fight_result: { ...bout.fight_result!, method: "no_contest" as const } };
    expect(sideValue(noContest, "away")).toBeNull();
    const liveBout = { ...bout, phase: "in_progress" as const, fight_result: null };
    expect(sideValue(liveBout, "away")).toBeNull();
    expect(sideValue(liveBout, "home")).toBeNull();
  });

  it("is empty for a game that is off", () => {
    expect(sideValue(game({ phase: "postponed" }), "home")).toBeNull();
    expect(sideValue(game({ phase: "canceled" }), "away")).toBeNull();
  });
});

describe("pointOfView and cardOutcome", () => {
  const both = final("both", "2026-07-28T19:05:00-04:00", ari(3), ath(5), ["ath", "ari"]);
  const neither = laliga("ll", "2026-07-30T15:00:00-04:00", {
    phase: "final",
    away: side("Real Madrid", 2),
    home: side("Athletic Club", 2),
  });

  it("is the one followed side's", () => {
    expect(pointOfView(MOCK_WEEK[0], null)).toBe("home");
    expect(pointOfView(MOCK_WEEK[3], null)).toBe("away");
  });

  it("is nobody's when neither side is followed", () => {
    expect(pointOfView(neither, null)).toBeNull();
    expect(cardOutcome(neither, null)).toBeNull();
  });

  it("is nobody's when both are, until the filter chooses", () => {
    expect(pointOfView(both, null)).toBeNull();
    expect(cardOutcome(both, null)).toBeNull();
    expect(pointOfView(both, "ath")).toBe("home");
    expect(cardOutcome(both, "ath")).toEqual({ outcome: "W", spoken: "Athletics won" });
    expect(pointOfView(both, "ari")).toBe("away");
    expect(cardOutcome(both, "ari")).toEqual({
      outcome: "L",
      spoken: "Arizona Diamondbacks lost",
    });
  });

  it("falls back to the followed side when the filter names neither", () => {
    expect(pointOfView(MOCK_WEEK[0], "che")).toBe("home");
  });

  it("gives W and L from the followed side, home or away", () => {
    expect(cardOutcome(MOCK_WEEK[0], null)).toEqual({ outcome: "L", spoken: "Athletics lost" });
    expect(cardOutcome(MOCK_WEEK[1], null)).toEqual({ outcome: "W", spoken: "Athletics won" });
    expect(cardOutcome(MOCK_WEEK[3], null)).toEqual({
      outcome: "W",
      spoken: "Arizona Diamondbacks won",
    });
  });

  it("gives D for a draw", () => {
    const draw: Game = {
      ...final("d", "2026-07-27T12:00:00-04:00", side("Brighton", 1), side("Chelsea", 1, "che"), ["che"]),
      sport: "soccer",
      league_id: "epl",
    };
    expect(cardOutcome(draw, null)).toEqual({ outcome: "D", spoken: "Chelsea drew" });
  });

  it("has nothing to say until the game is over, or when it is off", () => {
    expect(cardOutcome(MOCK_WEEK[4], null)).toBeNull(); // live, 3-4
    expect(cardOutcome(game(), null)).toBeNull();
    expect(cardOutcome(game({ phase: "postponed" }), null)).toBeNull();
  });

  it("reads a fight by how it ended, not by its round count", () => {
    const bout = game({
      sport: "mma",
      phase: "final",
      away: side("Islam Makhachev", 1, "mak"),
      home: side("Arman Tsarukyan", 0),
      fight_result: { method: "submission", detail: null, round: 2, clock: "4:21" },
      followed_team_ids: ["mak"],
    });
    expect(cardOutcome(bout, null)).toEqual({ outcome: "W", spoken: "Islam Makhachev won" });
    const level = { ...bout, away: side("Islam Makhachev", 1, "mak"), home: side("Arman Tsarukyan", 1) };
    // Level round counts are not a draw...
    expect(cardOutcome({ ...level, fight_result: { ...bout.fight_result!, method: "decision" } }, null)).toBeNull();
    // ...unless the method says so; a no-contest is no result at all.
    expect(
      cardOutcome({ ...level, fight_result: { ...bout.fight_result!, method: "draw" } }, null),
    ).toEqual({ outcome: "D", spoken: "Islam Makhachev drew" });
    expect(
      cardOutcome({ ...bout, fight_result: { ...bout.fight_result!, method: "no_contest" } }, null),
    ).toBeNull();
  });

  it("puts the result in the button's name, which replaces the card", () => {
    expect(cardLabel(MOCK_WEEK[0], null)).toBe(
      "Box score: Boston Red Sox 4, Athletics 2, final, Athletics lost, Sutter Health Park",
    );
    expect(cardLabel({ ...both, venue: null }, null)).toBe(
      "Box score: Arizona Diamondbacks 3, Athletics 5, final",
    );
    expect(cardLabel({ ...both, venue: null }, "ari")).toBe(
      "Box score: Arizona Diamondbacks 3, Athletics 5, final, Arizona Diamondbacks lost",
    );
    expect(cardLabel(game({ venue: null }), null)).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM",
    );
  });

  it("and where it is played and what it belongs to, as the card or row shows them", () => {
    // The venue is a column of "Coming up": a name without it hides it.
    expect(cardLabel(game(), null)).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM, Sutter Health Park",
    );
    // A whole-competition game: the competition, then the round.
    expect(
      cardLabel(laliga("x", "2026-08-01T12:00:00-04:00", { series: "Matchday 3" }), null, null, LALIGA),
    ).toBe("Box score: Real Betis at Sevilla, 12:00 PM, Ramón Sánchez-Pizjuán, LaLiga · Matchday 3");
    // A followed team's game is not explained by its competition; its round still is.
    expect(cardLabel(game({ series: "ALDS Game 2" }), null, null, MLB)).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM, Sutter Health Park, ALDS Game 2",
    );
    expect(cardLabel(game({ venue: "" }), null, null, MLB)).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM",
    );
  });

  it("and the forecast, when the card shows one", () => {
    expect(
      cardLabel(game(), null, {
        text: "High 78° · 30% precip",
        spoken: "Forecast: high 78°F, 30% chance of precipitation",
      }),
    ).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM, Sutter Health Park, Forecast: high 78°F, 30% chance of precipitation",
    );
    expect(cardLabel(game(), null, null)).toBe(
      "Box score: Boston Red Sox at Athletics, 7:05 PM, Sutter Health Park",
    );
  });
});

describe("followedNoun", () => {
  const tennis = game({
    sport: "tennis",
    away: side("Jannik Sinner", null, "sin"),
    home: side("Carlos Alcaraz"),
    followed_team_ids: ["sin"],
  });

  it("is a team, as the mockup's key says, for team games and by default", () => {
    expect(followedNoun(MOCK_WEEK)).toBe("team");
    expect(followedNoun([])).toBe("team");
    // A match nobody followed is in says nothing about who is followed.
    expect(followedNoun([laliga("ll", "2026-08-01T12:00:00-04:00")])).toBe("team");
    expect(followedNoun([{ ...tennis, followed_team_ids: [] }])).toBe("team");
  });

  it("is an athlete when the followed sides on screen are people", () => {
    expect(followedNoun([tennis])).toBe("athlete");
    expect(followedNoun([tennis, laliga("ll", "2026-08-01T12:00:00-04:00")])).toBe("athlete");
    expect(followedNoun([tennis, ...MOCK_WEEK])).toBe("team or athlete");
  });
});

describe("the lines under a card's sides", () => {
  it("names the competition only for a game no followed team is in", () => {
    const match = laliga("ll", "2026-08-01T12:00:00-04:00");
    expect(competitionName(match, LALIGA)).toBe("LaLiga");
    expect(contextLine(match, LALIGA)).toBe("LaLiga");
    expect(competitionName(game(), MLB)).toBeNull();
    expect(contextLine(game(), MLB)).toBeNull();
    // The league may not have loaded: then there is no name to give.
    expect(competitionName(match, undefined)).toBeNull();
    expect(contextLine(match, undefined)).toBeNull();
  });

  it("adds the round or the card", () => {
    expect(contextLine(laliga("ll", "2026-08-01T12:00:00-04:00", { series: "Matchday 9" }), LALIGA)).toBe(
      "LaLiga · Matchday 9",
    );
    expect(
      contextLine(game({ sport: "tennis", series: "Canadian Open · Round of 16" }), ATP),
    ).toBe("Canadian Open · Round of 16");
    expect(contextLine(game({ series: "" }), MLB)).toBeNull();
  });

  it("gives a finished fight's result a line of its own", () => {
    const bout = game({
      sport: "mma",
      phase: "final",
      series: "UFC 320 · Main card",
      fight_result: { method: "submission", detail: "Rear-Naked Choke", round: 2, clock: "4:21" },
    });
    expect(fightLine(bout)).toBe("Submission · Rear-Naked Choke · R2 4:21");
    expect(contextLine(bout, undefined)).toBe("UFC 320 · Main card");
    expect(fightLine({ ...bout, phase: "scheduled" })).toBeNull();
    expect(fightLine({ ...bout, fight_result: null })).toBeNull();
    expect(fightLine(MOCK_WEEK[0])).toBeNull();
  });

  it("names a person by surname where a team is told by its block", () => {
    const tennis = game({ sport: "tennis", away: side("Jannik Sinner", null, "sin", "J. Sinne") });
    expect(cardSideName(tennis, tennis.away)).toBe("Sinner");
    expect(cardSideName(game(), game().away)).toBe("Boston Red Sox");
  });
});

describe("rowSides", () => {
  it("leads with the followed team and says vs at home, at on the road", () => {
    const home = rowSides(game(), null);
    expect(home.first.name).toBe("Athletics");
    expect(home.second.name).toBe("Boston Red Sox");
    expect(home).toMatchObject({
      firstKey: "home",
      secondKey: "away",
      word: "vs",
      place: "Home",
      viewed: true,
    });
    const away = rowSides(game({ away: ath(), home: cin() }), null);
    expect(away.first.name).toBe("Athletics");
    expect(away).toMatchObject({ firstKey: "away", word: "at", place: "Away", viewed: true });
  });

  it("is away at home, with no place, when there is no point of view", () => {
    const row = rowSides(laliga("ll", "2026-08-01T12:00:00-04:00"), null);
    expect(row.first.name).toBe("Real Betis");
    expect(row.second.name).toBe("Sevilla");
    expect(row).toMatchObject({ word: "at", place: null, viewed: false });
    const both = game({ away: ari(), home: ath(), followed_team_ids: ["ath", "ari"] });
    expect(rowSides(both, null)).toMatchObject({ firstKey: "away", viewed: false });
    expect(rowSides(both, "ari")).toMatchObject({ firstKey: "away", word: "at", place: "Away" });
    expect(rowSides(both, "ath")).toMatchObject({ firstKey: "home", word: "vs", place: "Home" });
  });

  it("never says at, Home or Away for people", () => {
    const tennis = game({
      sport: "tennis",
      away: side("Jannik Sinner", null, "sin"),
      home: side("Carlos Alcaraz"),
      followed_team_ids: ["sin"],
    });
    expect(rowSides(tennis, null)).toMatchObject({ word: "vs", place: null, viewed: true });
    expect(rowSides({ ...tennis, followed_team_ids: [] }, null)).toMatchObject({
      word: "vs",
      place: null,
      viewed: false,
    });
  });
});

// ── Weather ─────────────────────────────────────────────────────────────

describe("weather", () => {
  function forecast(over: Partial<Weather> = {}): Weather {
    return {
      temperature: 71,
      condition: "Partly cloudy",
      code: 2,
      wind_speed: 8,
      units: "imperial",
      high: 78.4,
      low: 61,
      precip_chance: 30,
      ...over,
    };
  }

  it("asks only for outdoor games still to start, the soonest first", () => {
    const ids = weatherGameIds([
      game({ id: "late", start_time: "2026-08-01T21:05:00-04:00" }),
      game({ id: "soon", start_time: "2026-07-31T21:05:00-04:00" }),
      game({ id: "live", phase: "in_progress" }),
      game({ id: "final", phase: "final" }),
      game({ id: "ppd", phase: "postponed" }),
      game({ id: "indoors", sport: "basketball" }),
      game({ id: "ice", sport: "hockey" }),
      game({ id: "cage", sport: "mma" }),
      game({ id: "court", sport: "tennis", start_time: "2026-07-30T23:00:00-04:00" }),
      laliga("pitch", "2026-08-01T12:00:00-04:00"),
      game({ id: "gridiron", sport: "football", start_time: "2026-08-02T13:00:00-04:00" }),
    ]);
    expect(ids).toEqual(["court", "soon", "pitch", "late", "gridiron"]);
  });

  it("sends no more ids than the backend reads", () => {
    const many = Array.from({ length: WEATHER_BATCH + 5 }, (_, index) =>
      game({ id: `g${index}`, start_time: `2026-08-01T12:${String(index % 60).padStart(2, "0")}:00-04:00` }),
    );
    expect(weatherGameIds(many)).toHaveLength(WEATHER_BATCH);
  });

  it("puts the day's figures in words and says which is which", () => {
    // "Forecast", not "Game-day forecast": the backend's day is the UTC
    // date of the start, which is not always the game's day at the venue.
    expect(weatherLine(forecast())).toEqual({
      text: "High 78° · 30% precip",
      spoken: "Forecast: high 78°F, low 61°F, 30% chance of precipitation",
    });
    expect(weatherLine(forecast())?.spoken).not.toMatch(/game.day/i);
    expect(weatherLine(forecast({ units: "metric", high: 19, low: null, precip_chance: null }))).toEqual({
      text: "High 19°",
      spoken: "Forecast: high 19°C",
    });
    expect(weatherLine(forecast({ high: null, low: null, precip_chance: 80 }))?.text).toBe(
      "80% precip",
    );
  });

  it("never passes off the venue's weather right now as the game's", () => {
    // `temperature` and `condition` are current; with no forecast for the
    // day there is nothing to say.
    expect(weatherLine(forecast({ high: null, low: null, precip_chance: null }))).toBeNull();
    expect(weatherLine(forecast({ high: null, low: 55, precip_chance: null }))).toBeNull();
    expect(weatherLine(undefined)).toBeNull();
    expect(weatherLine(forecast())?.text).not.toContain("71");
    expect(weatherLine(forecast())?.spoken).not.toContain("cloudy");
  });
});

// ── Tournaments ─────────────────────────────────────────────────────────

describe("tournament days and lanes", () => {
  const brickyard = event({
    id: "brickyard",
    sport: "racing",
    league_id: "nascar",
    name: "Brickyard 400",
    start_time: "2026-07-24T12:00:00-04:00",
    end_time: "2026-07-27T16:00:00-04:00",
    phase: "final",
  });
  const open = event({ phase: "in_progress", round_label: "Round 1" });
  const grandPrix = event({
    id: "gp",
    sport: "racing",
    league_id: "f1",
    name: "Hungarian Grand Prix",
    start_time: "2026-07-31T07:30:00-04:00",
    end_time: "2026-08-02T11:00:00-04:00",
  });
  const overTheEdge = event({
    id: "edge",
    name: "A Five-Day Classic",
    start_time: "2026-08-01T08:00:00-04:00",
    end_time: "2026-08-05T18:00:00-04:00",
  });
  const nextWeek = event({
    id: "wyndham",
    name: "Wyndham Championship",
    start_time: "2026-08-06T08:00:00-04:00",
    end_time: "2026-08-09T18:00:00-04:00",
  });
  const oneDay = event({
    id: "sprint",
    name: "A One-Day Sprint",
    start_time: "2026-07-28T14:00:00-04:00",
    end_time: null,
  });
  const placed = eventDays([nextWeek, overTheEdge, grandPrix, open, brickyard, oneDay], undefined);

  it("gives each its days, both ends included, earliest first", () => {
    expect(placed.map((entry) => [entry.event.id, entry.first, entry.last])).toEqual([
      ["brickyard", "2026-07-24", "2026-07-27"],
      ["sprint", "2026-07-28", "2026-07-28"],
      ["open", "2026-07-30", "2026-08-02"],
      ["gp", "2026-07-31", "2026-08-02"],
      ["edge", "2026-08-01", "2026-08-05"],
      ["wyndham", "2026-08-06", "2026-08-09"],
    ]);
  });

  it("orders two that start together by the longer, then by name", () => {
    const short = event({ id: "b", name: "B", end_time: "2026-07-31T18:00:00-04:00" });
    const long = event({ id: "a", name: "Z", end_time: "2026-08-02T18:00:00-04:00" });
    const twin = event({ id: "c", name: "A", end_time: "2026-07-31T18:00:00-04:00" });
    expect(eventDays([short, long, twin], undefined).map((entry) => entry.event.id)).toEqual([
      "a",
      "c",
      "b",
    ]);
  });

  it("dates them in the server's zone, not the viewer's", () => {
    // 1 AM Thursday in New York is still Wednesday evening in Los Angeles.
    const early = event({ start_time: "2026-07-30T05:00:00Z", end_time: "2026-08-02T23:00:00Z" });
    expect(eventDays([early], "America/Los_Angeles")[0]).toMatchObject({
      first: "2026-07-29",
      last: "2026-08-02",
    });
    expect(eventDays([early], undefined)[0].first).toBe("2026-07-30");
  });

  it("finds the ones running on a day, and which day of theirs it is", () => {
    expect(eventsOn(placed, "2026-08-01").map((entry) => entry.event.id)).toEqual([
      "open",
      "gp",
      "edge",
    ]);
    expect(eventsOn(placed, "2026-07-29")).toEqual([]);
    const entry = placed[2];
    expect(eventDayOf(entry, "2026-07-30")).toBe("Day 1 of 4");
    expect(eventDayOf(entry, "2026-08-02")).toBe("Day 4 of 4");
    expect(eventDayOf(placed[1], "2026-07-28")).toBeNull();
  });

  it("lays a week's bars out in lanes, cut to the week", () => {
    const { lanes, bars } = layoutBars(placed, THIS_WEEK);
    expect(lanes).toBe(3);
    expect(
      bars.map((bar) => [
        bar.entry.event.id,
        bar.lane,
        bar.column,
        bar.span,
        bar.startsBefore,
        bar.endsAfter,
      ]),
    ).toEqual([
      // Began last Friday: only its last day is in this week.
      ["brickyard", 0, 0, 1, true, false],
      // Lane 0 is free again from Tuesday.
      ["sprint", 0, 1, 1, false, false],
      ["open", 0, 3, 4, false, false],
      // Overlaps the Open: its own lane.
      ["gp", 1, 4, 3, false, false],
      // Overlaps both, and runs on into next week.
      ["edge", 2, 5, 2, false, true],
    ]);
  });

  it("has no lanes when nothing touches the range", () => {
    expect(layoutBars(placed, { first: "2026-08-10", last: "2026-08-16" })).toEqual({
      lanes: 0,
      bars: [],
    });
    expect(layoutBars([], THIS_WEEK)).toEqual({ lanes: 0, bars: [] });
  });

  it("cuts a bar to the days of a week that are in the month", () => {
    // The last week of July as the month grid draws it: Monday to Friday 31.
    const { bars } = layoutBars(placed, { first: "2026-07-27", last: "2026-07-31" });
    const openBar = bars.find((bar) => bar.entry.event.id === "open");
    expect(openBar).toMatchObject({ column: 3, span: 2, startsBefore: false, endsAfter: true });
    expect(bars.some((bar) => bar.entry.event.id === "edge")).toBe(false);
  });

  it("keeps a tournament in the same lane from one week to the next", () => {
    const next = layoutBars(placed, { first: "2026-08-03", last: "2026-08-09" });
    expect(next.bars.map((bar) => [bar.entry.event.id, bar.lane, bar.column, bar.span])).toEqual([
      ["edge", 0, 0, 3],
      ["wyndham", 0, 3, 4],
    ]);
  });

  it("says what a bar is and where it stands, in words", () => {
    expect(eventKind("golf")).toBe("Golf");
    expect(eventKind("racing")).toBe("Racing");
    expect(eventState(open)).toBe("Live");
    expect(eventState(brickyard)).toBe("Final");
    expect(eventState(nextWeek)).toBeNull();
    expect(eventState(event({ phase: "postponed" }))).toBe("Postponed");
    expect(eventState(event({ phase: "canceled" }))).toBe("Canceled");
    expect(eventLabel(placed[2])).toBe("Leaderboard: 3M Open, golf, Jul 30 – Aug 2, live, Round 1");
    expect(eventLabel(placed[5])).toBe("Leaderboard: Wyndham Championship, golf, Aug 6 – 9");
    expect(eventLabel(placed[0])).toBe("Leaderboard: Brickyard 400, racing, Jul 24 – 27, final");
    expect(eventLabel(placed[1])).toBe("Leaderboard: A One-Day Sprint, golf, Jul 28");
  });

  it("picks the followed athletes off a board", () => {
    const board = event({
      leaderboard: [
        leaderRow(),
        leaderRow({ position: 14, position_label: "T14", name: "Collin Morikawa", player_id: "mor" }),
      ],
    });
    expect(followedRows(board).map((row) => row.name)).toEqual(["Collin Morikawa"]);
    expect(followedRows(event())).toEqual([]);
  });
});

describe("a tournament as a row of a list", () => {
  const field = [
    leaderRow({ score: "-9" }),
    leaderRow({ position: 14, position_label: "T14", name: "Collin Morikawa", score: "-2", player_id: "mor" }),
  ];
  // Thursday 30 July to Sunday 2 August; today is the Thursday.
  const [live] = eventDays(
    [event({ phase: "in_progress", round_label: "Round 1", leaderboard: field })],
    "America/New_York",
  );

  it("is live, with the round and the followed places, only on the day being played", () => {
    expect(eventRow(live, TODAY, TODAY)).toEqual({
      live: true,
      state: null,
      dayOf: "Day 1 of 4",
      followed: [{ name: "Collin Morikawa", place: "T14", score: "-2" }],
      label:
        "Leaderboard: 3M Open, golf, Jul 30 – Aug 2, day 1 of 4, live, Round 1, TPC Twin Cities, T14 Collin Morikawa, -2",
    });
  });

  it("says which day it is on its other days, and nothing about a round not yet played", () => {
    const sunday = eventRow(live, "2026-08-02", TODAY);
    expect(sunday).toEqual({
      live: false,
      state: null,
      dayOf: "Day 4 of 4",
      followed: [],
      label: "Leaderboard: 3M Open, golf, Jul 30 – Aug 2, day 4 of 4, TPC Twin Cities",
    });
    // On Saturday the live row is Saturday's, and Thursday's is a day gone by.
    expect(eventRow(live, "2026-08-01", "2026-08-01").live).toBe(true);
    expect(eventRow(live, TODAY, "2026-08-01").live).toBe(false);
  });

  it("gives each day's row of one tournament a name of its own", () => {
    const names = ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02"].map(
      (day) => eventRow(live, day, TODAY).label,
    );
    expect(new Set(names).size).toBe(4);
  });

  it("holds the live day to the tournament's own days when the viewer's today is outside them", () => {
    // The days are the server's; a viewer further east is already on Monday.
    expect(eventRow(live, "2026-08-02", "2026-08-03").live).toBe(true);
    expect(eventRow(live, "2026-07-30", "2026-07-29").live).toBe(true);
  });

  it("gives nobody a place before the first round: the board is the entry list", () => {
    // The backend numbers the field in the order it came, every score "E".
    const [soon] = eventDays(
      [
        event({
          leaderboard: [
            leaderRow({ position_label: "T1", score: "E" }),
            leaderRow({ position: 37, position_label: "T37", name: "Collin Morikawa", score: "E", player_id: "mor" }),
          ],
        }),
      ],
      "America/New_York",
    );
    expect(hasStandings(soon.event)).toBe(false);
    const row = eventRow(soon, "2026-07-30", "2026-07-27");
    expect(row.followed).toEqual([{ name: "Collin Morikawa", place: null, score: null }]);
    expect(row.label).toBe(
      "Leaderboard: 3M Open, golf, Jul 30 – Aug 2, day 1 of 4, TPC Twin Cities, Collin Morikawa in the field",
    );
    expect(row.label).not.toContain("T37");
    expect(row).toMatchObject({ live: false, state: null });
  });

  it("keeps the result on every day of one that is over", () => {
    const [done] = eventDays([event({ phase: "final", leaderboard: field })], "America/New_York");
    expect(hasStandings(done.event)).toBe(true);
    expect(eventRow(done, "2026-08-01", "2026-08-05")).toMatchObject({
      live: false,
      state: "Final",
      dayOf: "Day 3 of 4",
      followed: [{ name: "Collin Morikawa", place: "T14", score: "-2" }],
    });
    expect(eventRow(done, "2026-08-01", "2026-08-05").label).toBe(
      "Leaderboard: 3M Open, golf, Jul 30 – Aug 2, day 3 of 4, final, TPC Twin Cities, T14 Collin Morikawa, -2",
    );
  });

  it("has no day count for a single day, and no score where the board has none", () => {
    const [race] = eventDays(
      [
        event({
          sport: "racing",
          name: "A One-Day Sprint",
          start_time: "2026-07-28T13:00:00-04:00",
          end_time: null,
          venue: null,
          phase: "final",
          leaderboard: [leaderRow({ name: "Lando Norris", score: " ", player_id: "nor" })],
        }),
      ],
      "America/New_York",
    );
    expect(eventRow(race, "2026-07-28", TODAY)).toEqual({
      live: false,
      state: "Final",
      dayOf: null,
      followed: [{ name: "Lando Norris", place: "1", score: null }],
      label: "Leaderboard: A One-Day Sprint, racing, Jul 28, final, 1 Lando Norris",
    });
  });
});

// ── Legend ──────────────────────────────────────────────────────────────

describe("legendRows", () => {
  const board = event({
    phase: "in_progress",
    leaderboard: [
      leaderRow(),
      leaderRow({ position: 14, position_label: "T14", name: "Collin Morikawa", player_id: "mor" }),
    ],
  });

  it("counts each team's games in the range and lists its results, oldest first", () => {
    const rows = legendRows([ATH, ARI, CHE], LEAGUES, MOCK_WEEK, [], "this week");
    expect(rows.slice(0, 3)).toEqual([
      {
        key: "ath",
        name: "Athletics",
        abbreviation: "ATH",
        color: "#EFB21E",
        kind: "team",
        marked: true,
        line: "7 games this week",
        results: ["L", "W", "L"],
        quiet: false,
      },
      {
        key: "ari",
        name: "Arizona Diamondbacks",
        abbreviation: "ARI",
        color: "#A71930",
        kind: "team",
        marked: true,
        line: "2 games this week",
        results: ["W"],
        quiet: false,
      },
      {
        key: "che",
        name: "Chelsea",
        abbreviation: "CHE",
        color: "#144992",
        kind: "team",
        marked: true,
        line: "No matches this week",
        results: [],
        quiet: true,
      },
    ]);
  });

  it("counts only what is inside the range: the padded days would add two", () => {
    const unclipped = legendRows([ATH], LEAGUES, MOCK_FETCH, [], "this week")[0];
    expect(unclipped.line).toBe("9 games this week");
    expect(unclipped.results).toEqual(["L", "L", "W", "L"]);
    const clipped = legendRows([ATH], LEAGUES, MOCK_WEEK, [], "this week")[0];
    expect(clipped.line).toBe("7 games this week");
    expect(clipped.results).toEqual(["L", "W", "L"]);
  });

  it("leaves a game that is off out of the count, and knows a draw", () => {
    const games = [
      {
        ...final("d", "2026-07-27T12:00:00-04:00", side("Brighton", 1), side("Chelsea", 1, "che"), ["che"]),
        sport: "soccer" as const,
        league_id: "epl",
      },
      game({
        id: "off",
        sport: "soccer",
        league_id: "epl",
        phase: "canceled",
        away: side("Chelsea", null, "che"),
        home: side("Wolves"),
        followed_team_ids: ["che"],
      }),
    ];
    const row = legendRows([CHE], LEAGUES, games, [], "this week")[0];
    expect(row.line).toBe("1 match this week");
    expect(row.results).toEqual(["D"]);
  });

  it("uses the sport's word even with no game to read it from", () => {
    expect(legendRows([SIN], LEAGUES, [], [], "next month")[0].line).toBe("No matches next month");
    expect(legendRows([ATH], LEAGUES, [], [], "that week")[0].line).toBe("No games that week");
  });

  it("gives a golfer or a driver their place on a board, not a count of games", () => {
    const rows = legendRows([MOR, NOR], LEAGUES, MOCK_WEEK, [board], "this week");
    expect(rows[0]).toMatchObject({
      key: "mor",
      kind: "athlete",
      line: "T14 at the 3M Open",
      results: [],
      quiet: false,
      marked: true,
    });
    expect(rows[1]).toMatchObject({
      key: "nor",
      kind: "athlete",
      line: "Not on a leaderboard this week",
      quiet: true,
    });
  });

  it("does not double the article of a tournament that carries its own", () => {
    for (const name of ["The Genesis Invitational", "THE PLAYERS Championship", "the Memorial Tournament"]) {
      const line = legendRows([MOR], LEAGUES, [], [{ ...board, name }], "this week")[0].line;
      expect(line).toBe(`T14 at ${name}`);
    }
    // "Theater Classic" starts with the letters, not the word.
    expect(
      legendRows([MOR], LEAGUES, [], [{ ...board, name: "Theater Classic" }], "this week")[0].line,
    ).toBe("T14 at the Theater Classic");
  });

  it("claims no place in a tournament that has not started", () => {
    const entryList = event({
      phase: "scheduled",
      leaderboard: [
        leaderRow({ position_label: "T1", score: "E" }),
        leaderRow({ position: 37, position_label: "T37", name: "Collin Morikawa", score: "E", player_id: "mor" }),
      ],
    });
    const row = legendRows([MOR], LEAGUES, [], [entryList], "next week")[0];
    expect(row.line).toBe("In the field at the 3M Open");
    expect(row.quiet).toBe(false);
    // Called off: there is no field to be in.
    expect(
      legendRows([MOR], LEAGUES, [], [{ ...entryList, phase: "canceled" }], "next week")[0],
    ).toMatchObject({ line: "Not on a leaderboard next week", quiet: true });
    // Over: the place is the result.
    expect(
      legendRows([MOR], LEAGUES, [], [{ ...board, phase: "final" }], "last week")[0].line,
    ).toBe("T14 at the 3M Open");
  });

  it("does not say 'not on a leaderboard' while the tournaments are unknown", () => {
    const loading = legendRows([MOR, ATH], LEAGUES, MOCK_WEEK, "loading", "this week");
    expect(loading[0]).toMatchObject({ line: "Loading tournaments…", quiet: true });
    const failed = legendRows([MOR, ATH], LEAGUES, MOCK_WEEK, "failed", "this week");
    expect(failed[0]).toMatchObject({ line: "Couldn’t load tournaments", quiet: true });
    // A team's row does not depend on the tournaments at all.
    expect(failed[1].line).toBe(legendRows([ATH], LEAGUES, MOCK_WEEK, [], "this week")[0].line);
  });

  it("knows a tennis player or a fighter is a person, not a team", () => {
    expect(legendRows([SIN], LEAGUES, [], [], "this week")[0].kind).toBe("athlete");
    expect(legendRows([ATH], LEAGUES, [], [], "this week")[0].kind).toBe("team");
    // A team whose league has not loaded is taken for a team.
    expect(legendRows([SIN], [], [], [], "this week")[0].kind).toBe("team");
  });

  it("adds a row for each whole competition, unmarked, and none for a tour", () => {
    const games = [
      ...MOCK_WEEK,
      laliga("a", "2026-08-01T12:00:00-04:00"),
      laliga("b", "2026-08-01T14:00:00-04:00"),
      laliga("c", "2026-08-01T16:00:00-04:00", { phase: "postponed" }),
    ];
    const rows = legendRows([ATH], LEAGUES, games, [board], "this week");
    expect(rows.map((row) => row.key)).toEqual(["ath", "league:laliga"]);
    expect(rows[1]).toEqual({
      key: "league:laliga",
      name: "LaLiga",
      abbreviation: null,
      color: null,
      kind: "competition",
      marked: false,
      line: "2 matches this week",
      results: [],
      quiet: false,
    });
    const quiet = legendRows([], LEAGUES, MOCK_WEEK, [], "this week");
    expect(quiet).toHaveLength(1);
    expect(quiet[0]).toMatchObject({ line: "No matches this week", quiet: true });
  });
});

// ── The sentences beside the tabs ───────────────────────────────────────

describe("captions", () => {
  const nextWeek = [
    game({ id: "n1", start_time: "2026-08-04T18:10:00-04:00", away: ath(), home: cin() }),
    game({ id: "n2", start_time: "2026-08-05T12:40:00-04:00", away: ath(), home: cin() }),
    game({ id: "n3", start_time: "2026-08-06T12:40:00-04:00", away: ath(), home: cin() }),
    game({ id: "n4", start_time: "2026-08-07T20:05:00-04:00", away: ath(), home: tex() }),
    game({ id: "n5", start_time: "2026-08-08T19:05:00-04:00", away: ath(), home: tex() }),
  ];

  it("picks the team a caption is about", () => {
    const teams = [ATH, ARI, CHE];
    // Two followed teams play this week: the favourite stands for it.
    expect(captionTeam(teams, MOCK_WEEK, null, "ath")).toBe(ATH);
    expect(captionTeam(teams, MOCK_WEEK, null, "ari")).toBe(ARI);
    // No favourite, or one that is not playing: no single team does.
    expect(captionTeam(teams, MOCK_WEEK, null, null)).toBeNull();
    expect(captionTeam(teams, MOCK_WEEK, null, "che")).toBeNull();
    // Only one followed team is playing: it does, favourite or not.
    expect(captionTeam(teams, nextWeek, null, null)).toBe(ATH);
    // The filter decides, when its team has a game.
    expect(captionTeam(teams, MOCK_WEEK, "ari", "ath")).toBe(ARI);
    expect(captionTeam(teams, MOCK_WEEK, "che", "ath")).toBeNull();
    expect(captionTeam(teams, [], null, "ath")).toBeNull();
  });

  it("writes the mockup's week from the games alone", () => {
    // The mockup says "Boston" and "Detroit"; the API has only full names.
    expect(teamCaption(ATH, MOCK_WEEK, "baseball", true)).toBe(
      "Athletics: 7 games, all at home · Boston Red Sox through Thursday, Detroit Tigers from Friday",
    );
    expect(teamCaption(ATH, nextWeek, "baseball", true)).toBe(
      "Athletics: 5 games, all on the road · Cincinnati Reds through Thursday, Texas Rangers from Friday",
    );
  });

  it("says a single game is ON its day, not through it", () => {
    const two = [
      game({ id: "a", start_time: "2026-07-28T19:05:00-04:00", away: ari(), home: ath() }),
      game({ id: "b", start_time: "2026-07-31T19:05:00-04:00", away: ath(), home: det() }),
    ];
    expect(teamCaption(ATH, two, "baseball", true)).toBe(
      "Athletics: 2 games, one at home and one on the road · Arizona Diamondbacks on Tuesday, Detroit Tigers on Friday",
    );
    const mixed = [...two, game({ id: "c", start_time: "2026-08-01T19:05:00-04:00", away: ath(), home: det() })];
    expect(teamCaption(ATH, mixed, "baseball", true)).toBe(
      "Athletics: 3 games, one at home and two on the road · Arizona Diamondbacks on Tuesday, Detroit Tigers from Friday",
    );
  });

  it("names the one game there is, home or away", () => {
    const home = game({
      sport: "soccer",
      league_id: "epl",
      start_time: "2026-10-10T10:00:00-04:00",
      away: side("AFC Bournemouth"),
      home: side("Chelsea", null, "che"),
      followed_team_ids: ["che"],
    });
    expect(teamCaption(CHE, [home], "soccer", true)).toBe("Chelsea: vs AFC Bournemouth on Saturday");
    const away = { ...home, away: side("Chelsea", null, "che"), home: side("Everton") };
    expect(teamCaption(CHE, [away], "soccer", true)).toBe("Chelsea: at Everton on Saturday");
    // In a month a weekday does not say which: the date does.
    expect(teamCaption(CHE, [away], "soccer", false)).toBe("Chelsea: at Everton on October 10");
  });

  it("names the opponent when there is only one, and no weekdays in a month", () => {
    const series = MOCK_WEEK.filter((entry) => entry.away.name === "Boston Red Sox");
    expect(teamCaption(ATH, series, "baseball", true)).toBe(
      "Athletics: 4 games, all at home against Boston Red Sox",
    );
    expect(teamCaption(ATH, MOCK_WEEK, "baseball", false)).toBe("Athletics: 7 games, all at home");
  });

  it("splits home and road, as words or as figures but not both", () => {
    const at = (index: number, homeGame: boolean) =>
      game({
        id: `m${index}`,
        start_time: `2026-07-${String(index + 1).padStart(2, "0")}T19:05:00-04:00`,
        away: homeGame ? side(`Visitor ${index}`) : ath(),
        home: homeGame ? ath() : side(`Host ${index}`),
      });
    const month = Array.from({ length: 20 }, (_, index) => at(index, index < 12));
    expect(teamCaption(ATH, month, "baseball", false)).toBe(
      "Athletics: 20 games, 12 at home and 8 on the road",
    );
    expect(teamCaption(ATH, month.slice(9, 14), "baseball", false)).toBe(
      "Athletics: 5 games, three at home and two on the road",
    );
  });

  it("leaves out home and road for people, and games that are off for everyone", () => {
    const tennis = [
      game({
        id: "t1",
        sport: "tennis",
        start_time: "2026-07-28T11:00:00-04:00",
        away: side("Jannik Sinner", 2, "sin"),
        home: side("Alexander Zverev", 1),
        phase: "final",
        followed_team_ids: ["sin"],
      }),
      game({
        id: "t2",
        sport: "tennis",
        start_time: "2026-07-30T20:00:00-04:00",
        away: side("Carlos Alcaraz"),
        home: side("Jannik Sinner", null, "sin"),
        followed_team_ids: ["sin"],
      }),
    ];
    expect(teamCaption(SIN, tennis, "tennis", true)).toBe(
      "Jannik Sinner: 2 matches · Alexander Zverev on Tuesday, Carlos Alcaraz on Thursday",
    );
    expect(teamCaption(SIN, [tennis[1]], "tennis", true)).toBe(
      "Jannik Sinner: vs Carlos Alcaraz on Thursday",
    );
    expect(teamCaption(ATH, [game({ phase: "postponed" })], "baseball", true)).toBeNull();
    expect(teamCaption(CHE, MOCK_WEEK, "soccer", true)).toBeNull();
  });

  it("counts plainly when no one team stands for the range", () => {
    const saturday = Array.from({ length: 5 }, (_, index) =>
      laliga(`s${index}`, `2026-08-01T${10 + index}:00:00-04:00`),
    );
    expect(plainCaption(saturday)).toBe("5 matches, all on Saturday");
    expect(plainCaption([saturday[0]])).toBe("1 match, on Saturday");
    expect(plainCaption(MOCK_WEEK)).toBe("9 games across seven days");
    expect(plainCaption([...saturday, MOCK_WEEK[0]])).toBe("6 games across two days");
    expect(plainCaption([game({ phase: "canceled" })])).toBeNull();
    expect(plainCaption([])).toBeNull();
    // A month has four or five Saturdays: there the day is its date.
    expect(plainCaption(saturday, false)).toBe("5 matches, all on August 1");
    expect(plainCaption([saturday[0]], false)).toBe("1 match, on August 1");
    expect(plainCaption(MOCK_WEEK, false)).toBe("9 games across seven days");
    expect(rangeCaption([], LEAGUES, saturday, null, null, "month")).toBe(
      "5 matches, all on August 1",
    );
    expect(rangeCaption([], LEAGUES, saturday, null, null, "week")).toBe(
      "5 matches, all on Saturday",
    );
  });

  it("chooses between the two", () => {
    const teams = [ATH, ARI, CHE];
    expect(rangeCaption(teams, LEAGUES, MOCK_WEEK, null, "ath", "week")).toBe(
      "Athletics: 7 games, all at home · Boston Red Sox through Thursday, Detroit Tigers from Friday",
    );
    expect(rangeCaption(teams, LEAGUES, MOCK_WEEK, null, null, "week")).toBe(
      "9 games across seven days",
    );
    expect(rangeCaption(teams, LEAGUES, MOCK_WEEK, null, "ath", "month")).toBe(
      "Athletics: 7 games, all at home",
    );
    expect(rangeCaption(teams, LEAGUES, [], null, "ath", "week")).toBeNull();
  });
});

// ── Coming up ───────────────────────────────────────────────────────────

describe("Coming up", () => {
  const NEXT: DayRange = { first: "2026-08-03", last: "2026-08-09" };

  it("covers the week after the range on screen", () => {
    expect(comingUpWindow(THIS_WEEK, TODAY)).toEqual(NEXT);
    // On the range's last day too.
    expect(comingUpWindow(THIS_WEEK, "2026-08-02")).toEqual(NEXT);
    expect(comingUpWindow(NEXT, TODAY)).toEqual({ first: "2026-08-10", last: "2026-08-16" });
    expect(comingUpWindow(JULY, TODAY)).toEqual({ first: "2026-08-01", last: "2026-08-07" });
  });

  it("or the week from today, when the range is in the past", () => {
    expect(comingUpWindow(weekRange("2026-07-15"), TODAY)).toEqual({
      first: "2026-07-30",
      last: "2026-08-05",
    });
    expect(comingUpWindow(monthRange("2026-05-15"), TODAY).first).toBe(TODAY);
  });

  it("is titled Next week only when it is exactly that", () => {
    expect(comingUpTitle(NEXT, TODAY)).toBe("Next week");
    expect(comingUpTitle({ first: "2026-08-10", last: "2026-08-16" }, TODAY)).toBe("Aug 10 – 16");
    expect(comingUpTitle({ first: "2026-07-30", last: "2026-08-05" }, TODAY)).toBe(
      "Jul 30 – Aug 5",
    );
  });

  const followed = game({ id: "ath", start_time: "2026-08-08T21:05:00-04:00", away: ath(), home: tex() });
  const matchday = Array.from({ length: 8 }, (_, index) =>
    laliga(`ll${index}`, `2026-08-08T${10 + index}:00:00-04:00`),
  );
  const fetched = [
    game({ id: "sun-before", start_time: "2026-08-02T15:05:00-04:00" }),
    game({ id: "done", start_time: "2026-08-03T13:05:00-04:00", phase: "final" }),
    game({ id: "off", start_time: "2026-08-04T19:05:00-04:00", phase: "postponed" }),
    ...matchday,
    followed,
    game({ id: "mon-after", start_time: "2026-08-10T19:05:00-04:00" }),
  ];

  it("lists what is still to start inside the window, a followed game first in line", () => {
    const up = comingUp(fetched, NEXT, null);
    expect(up.all.map((entry) => entry.id)).toEqual([...matchday.map((entry) => entry.id), "ath"]);
    expect(COMING_UP_ROWS).toBe(5);
    expect(up.shown.map((entry) => entry.id)).toEqual(["ll0", "ll1", "ll2", "ll3", "ath"]);
    expect(up.more).toBe(4);
    // The earliest game with no row.
    expect(up.moreFrom).toBe("2026-08-08");
  });

  it("is one team's when filtered", () => {
    const up = comingUp(fetched, NEXT, "ath");
    expect(up.shown.map((entry) => entry.id)).toEqual(["ath"]);
    expect(up.more).toBe(0);
    expect(up.moreFrom).toBeNull();
    expect(comingUp(fetched, NEXT, "che").shown).toEqual([]);
  });

  it("says how many more there are and which week the button opens", () => {
    const up = comingUp(fetched, NEXT, null);
    expect(comingUpFoot(up, NEXT, TODAY)).toEqual({
      line: "4 more games through Aug 9",
      action: "Show next week",
      week: NEXT,
    });
    expect(comingUpFoot(comingUp(fetched, NEXT, "ath"), NEXT, TODAY)).toBeNull();
  });

  it("names the week it opens when the window is not one", () => {
    // After July the window is 1 to 7 August, which straddles two weeks.
    const window = { first: "2026-08-01", last: "2026-08-07" };
    const saturday = Array.from({ length: 7 }, (_, index) =>
      laliga(`s${index}`, `2026-08-01T${10 + index}:00:00-04:00`),
    );
    const early = comingUpFoot(comingUp(saturday, window, null), window, TODAY);
    expect(early).toEqual({
      line: "2 more matches through Aug 7",
      action: "Show this week",
      week: THIS_WEEK,
    });
    // The first game without a row is three weeks off.
    const far = { first: "2026-08-17", last: "2026-08-23" };
    const later = Array.from({ length: 6 }, (_, index) =>
      laliga(`f${index}`, `2026-08-22T${10 + index}:00:00-04:00`),
    );
    expect(comingUpFoot(comingUp(later, far, null), far, TODAY)).toEqual({
      line: "1 more match through Aug 23",
      action: "Show week of Aug 17",
      week: far,
    });
  });

  it("lists a tournament that starts in the window, not one already under way", () => {
    const placed = eventDays(
      [
        event({ id: "running", start_time: "2026-07-30T08:00:00-04:00", end_time: "2026-08-04T18:00:00-04:00" }),
        event({ id: "starts", start_time: "2026-08-06T08:00:00-04:00", end_time: "2026-08-09T18:00:00-04:00" }),
        event({
          id: "called-off",
          start_time: "2026-08-07T08:00:00-04:00",
          end_time: "2026-08-09T18:00:00-04:00",
          phase: "canceled",
        }),
        event({ id: "later", start_time: "2026-08-13T08:00:00-04:00", end_time: "2026-08-16T18:00:00-04:00" }),
      ],
      undefined,
    );
    expect(comingUpEvents(placed, NEXT).map((entry) => entry.event.id)).toEqual(["starts"]);
  });
});

// ── A day of a month ────────────────────────────────────────────────────

describe("a month's day", () => {
  it("shows a pair's score once there is one, away first, else its time", () => {
    expect(pairFigure(MOCK_WEEK[0])).toEqual({ text: "4–2", live: false });
    expect(pairFigure(MOCK_WEEK[4])).toEqual({ text: "3–4", live: true });
    expect(pairFigure(game())).toEqual({ text: "7:05 PM", live: false });
    expect(pairFigure(game({ phase: "postponed" }))).toEqual({ text: "PPD", live: false });
    expect(pairFigure(game({ phase: "canceled" }))).toEqual({ text: "Canc", live: false });
  });

  it("never shows a fight's round count", () => {
    const bout = game({ sport: "mma", phase: "final", away: side("A", 1), home: side("B", 0) });
    expect(pairFigure(bout)).toEqual({ text: "Final", live: false });
    expect(pairFigure({ ...bout, phase: "in_progress" })).toEqual({ text: "Live", live: true });
  });

  it("says what a day holds, for the cell that stands for it", () => {
    const wednesday = MOCK_WEEK.filter((entry) => gameDay(entry) === "2026-07-29");
    expect(dayCellLabel("2026-07-29", TODAY, wednesday, [])).toBe(
      "Wednesday, July 29, 2 games: 2 final",
    );
    const today = MOCK_WEEK.filter((entry) => gameDay(entry) === TODAY);
    const open = eventDays([event()], undefined);
    expect(dayCellLabel(TODAY, TODAY, today, open)).toBe(
      "Thursday, July 30, today, 2 games: 1 live, 1 upcoming, 3M Open",
    );
    expect(dayCellLabel("2026-07-03", TODAY, [], [])).toBe("Friday, July 3, nothing on");
    expect(dayCellLabel("2026-08-01", TODAY, [], open)).toBe("Saturday, August 1, 3M Open");
    expect(
      dayCellLabel(
        "2026-08-01",
        TODAY,
        [
          laliga("a", "2026-08-01T12:00:00-04:00"),
          laliga("b", "2026-08-01T14:00:00-04:00", { phase: "postponed" }),
        ],
        [],
      ),
    ).toBe("Saturday, August 1, 2 matches: 1 upcoming, 1 postponed");
  });

  it("opens a month on today, else on its first day with anything on", () => {
    expect(defaultDay(JULY, TODAY, MOCK_WEEK, [])).toBe(TODAY);
    const august = monthRange("2026-08-15");
    expect(defaultDay(august, TODAY, MOCK_FETCH, [])).toBe("2026-08-01");
    const wyndham = eventDays(
      [event({ start_time: "2026-08-06T08:00:00-04:00", end_time: "2026-08-09T18:00:00-04:00" })],
      undefined,
    );
    expect(defaultDay(august, TODAY, [game({ start_time: "2026-08-11T19:05:00-04:00" })], wyndham)).toBe(
      "2026-08-06",
    );
    expect(defaultDay(august, TODAY, [], [])).toBe("2026-08-01");
  });
});

// ── Freshness ───────────────────────────────────────────────────────────

describe("a narrow month cell's squares", () => {
  const day = (count: number, followed: number[] = []) =>
    Array.from({ length: count }, (_, index) =>
      laliga(`d${index}`, `2026-08-01T${String(10 + index).padStart(2, "0")}:00:00-04:00`, {
        followed_team_ids: followed.includes(index) ? ["che"] : [],
      }),
    );

  it("marks up to three games with a square each and no plus", () => {
    expect(MONTH_CELL_DOTS).toBe(3);
    expect(cellDots([])).toEqual({ shown: [], more: 0 });
    expect(cellDots(day(1)).shown).toHaveLength(1);
    expect(cellDots(day(3))).toMatchObject({ more: 0 });
    expect(cellDots(day(3)).shown).toHaveLength(3);
  });

  it("shows two and a plus on a busier day, so the plus stays on the line", () => {
    const four = cellDots(day(4));
    expect(four.shown).toHaveLength(2);
    expect(four.more).toBe(2);
    expect(cellDots(day(10))).toMatchObject({ more: 8 });
  });

  it("keeps a followed team's game among the two", () => {
    const busy = cellDots(day(10, [7, 9]));
    expect(busy.shown.map((game) => game.id)).toEqual(["d7", "d9"]);
  });
});

describe("where a month's day list goes", () => {
  const AUGUST: DayRange = { first: "2026-08-01", last: "2026-08-31" };
  const matchday = (count: number, date: string) =>
    Array.from({ length: count }, (_, index) =>
      laliga(`${date}-${index}`, `${date}T${String(10 + index).padStart(2, "0")}:00:00-04:00`),
    );

  it("counts the rows of the month's busiest day, tournaments included", () => {
    expect(busiestDayRows(AUGUST, [], [])).toBe(0);
    expect(busiestDayRows(AUGUST, [...matchday(2, "2026-08-08"), ...matchday(4, "2026-08-15")], [])).toBe(4);
    const [open] = eventDays([event()], "America/New_York"); // 30 July to 2 August
    expect(busiestDayRows(AUGUST, matchday(4, "2026-08-02"), [open])).toBe(5);
    // A day outside the range (the padded fetch) does not count.
    expect(busiestDayRows(JULY, matchday(10, "2026-08-01"), [])).toBe(0);
  });

  it("is the same whichever day is chosen: a one-team month stays beside the grid", () => {
    expect(busiestDayRows(JULY, MOCK_WEEK, [])).toBeLessThanOrEqual(DAY_LIST_BESIDE_MAX);
    expect(busiestDayRows(AUGUST, matchday(10, "2026-08-01"), [])).toBeGreaterThan(DAY_LIST_BESIDE_MAX);
  });
});

describe("freshness", () => {
  it("polls only while a game on screen is in progress", () => {
    expect(hasLiveGame(MOCK_WEEK)).toBe(true);
    expect(hasLiveGame(MOCK_WEEK.filter((entry) => entry.id !== "thu"))).toBe(false);
    expect(hasLiveGame([])).toBe(false);
  });

  it("knows whether a game has a followed side at all", () => {
    expect(hasFollowedSide(MOCK_WEEK[0])).toBe(true);
    expect(hasFollowedSide(laliga("ll", "2026-08-01T12:00:00-04:00"))).toBe(false);
    // An id on a side is not a follow unless the game says so.
    expect(hasFollowedSide(game({ followed_team_ids: [] }))).toBe(false);
  });
});
