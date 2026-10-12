import { afterEach, describe, expect, it, vi } from "vitest";

import type { League, NewsItem, Sport, Team } from "../../types";
import {
  ALL_KEY,
  FEED_REQUEST,
  FEED_SHOWN,
  QUIET_ROWS_MAX,
  ageText,
  countSpoken,
  countText,
  dateLine,
  dateParts,
  deckOf,
  feedOf,
  feedOverdue,
  filterEntries,
  filterKind,
  headlineOf,
  headlineParts,
  heroCodeSize,
  heroHeadlineRem,
  inCapitals,
  leagueCode,
  markFor,
  markLabel,
  moreNote,
  quietCaveat,
  quietEntries,
  quietHeadline,
  quietLine,
  quietRows,
  refreshFailedLine,
  refreshLineSpent,
  refreshedLine,
  rowLabel,
  scopeCount,
  sourceLabel,
  storyKey,
  topNote,
  type Feed,
  type FilterEntry,
  type ScopeCount,
} from "./model";

// The suite runs in America/New_York (see the `test` script).

function league(
  id: string,
  sport: Sport,
  name: string,
  followAll = false,
): League {
  return { id, sport, name, provider: "espn", follow_all: followAll };
}

function team(
  id: string,
  leagueId: string,
  name: string,
  abbreviation: string,
  color: string | null,
): Team {
  return { id, league_id: leagueId, name, abbreviation, logo_url: null, color };
}

let serial = 0;
function story(over: Partial<NewsItem> = {}): NewsItem {
  serial += 1;
  return {
    id: `n${serial}`,
    team_id: "ath",
    league_id: null,
    title: "Red Sox take game three in West Sacramento, 4-2",
    url: `https://example.test/${serial}`,
    source: "ESPN",
    published_at: "2026-07-30T02:41:00Z",
    summary: null,
    image_url: null,
    ...over,
  };
}

/** `count` stories for one team, or for a competition ("league:<id>"). */
function stories(count: number, scope = "ath"): NewsItem[] {
  return Array.from({ length: count }, () =>
    scope.startsWith("league:")
      ? story({ team_id: null, league_id: scope.slice(7) })
      : story({ team_id: scope }),
  );
}

const feed = (items: NewsItem[]): Feed => feedOf(items) as Feed;

const MLB = league("mlb", "baseball", "MLB");
const EPL = league("epl", "soccer", "Premier League");
const ATP = league("atp", "tennis", "ATP");
const PGA = league("pga", "golf", "PGA Tour");
const UFC = league("ufc", "mma", "UFC");
const F1 = league("f1", "racing", "Formula 1");
const WC = league("wc", "soccer", "FIFA World Cup", true);
const LALIGA = league("laliga", "soccer", "LaLiga", true);

const ATH = team("ath", "mlb", "Athletics", "ATH", "#EFB21E");
const ARI = team("ari", "mlb", "Arizona Diamondbacks", "ARI", "#A71930");
const BOU = team("bou", "epl", "AFC Bournemouth", "BOU", "#DA291C");
const WOL = team("wol", "epl", "Wolverhampton Wanderers", "WOL", null);
const ALCARAZ = team("alc", "atp", "Carlos Alcaraz", "C. Alcar", null);
const SINNER = team("sin", "atp", "Jannik Sinner", "J. Sinne", null);
const SCHEFFLER = team("sch", "pga", "Scottie Scheffler", "S. Schef", null);

/** What each filter lists; null for one that has not answered. */
const countsOf = (
  pairs: [string, number | null][],
): Map<string, ScopeCount | null> =>
  new Map(
    pairs.map(([key, count]) => [
      key,
      count === null ? null : { count, more: false },
    ]),
  );

const MOCK_ENTRIES = filterEntries([ATH, ARI, BOU], [MLB, EPL]);
const entryOf = (entries: readonly FilterEntry[], key: string): FilterEntry => {
  const found = entries.find((entry) => entry.key === key);
  if (found === undefined) throw new Error(`no filter ${key}`);
  return found;
};

describe("feedOf", () => {
  it("is null until the feed has answered", () => {
    expect(feedOf(undefined)).toBeNull();
  });

  it("asks for one story more than it lists", () => {
    expect(FEED_REQUEST).toBe(FEED_SHOWN + 1);
  });

  it("lists everything from a feed that fits, and says there is no more", () => {
    const items = stories(FEED_SHOWN);
    expect(feed(items)).toEqual({ stories: items, more: false });
    expect(feed([])).toEqual({ stories: [], more: false });
  });

  it("holds the extra story back and takes it as proof of more", () => {
    const items = stories(FEED_REQUEST);
    const result = feed(items);
    expect(result.stories).toHaveLength(FEED_SHOWN);
    expect(result.stories).toEqual(items.slice(0, FEED_SHOWN));
    expect(result.more).toBe(true);
  });
});

describe("leagueCode", () => {
  it("takes the initials of several words", () => {
    expect(leagueCode("FIFA World Cup")).toBe("FWC");
    expect(leagueCode("Premier League")).toBe("PL");
    expect(leagueCode("PGA Tour")).toBe("PT");
    expect(leagueCode("Formula 1")).toBe("F1");
  });

  it("stops at four initials", () => {
    expect(leagueCode("UEFA Europa Conference League Qualifying")).toBe("UECL");
  });

  it("keeps a short single word and cuts a long one to three letters", () => {
    expect(leagueCode("MLB")).toBe("MLB");
    expect(leagueCode("ATP")).toBe("ATP");
    expect(leagueCode("LaLiga")).toBe("LAL");
    expect(leagueCode("PlusLiga")).toBe("PLU");
  });

  it("skips words that start with punctuation, and survives an empty name", () => {
    expect(leagueCode("Serie A - Brasil")).toBe("SAB");
    expect(leagueCode("   ")).toBe("");
    expect(leagueCode("")).toBe("");
  });
});

describe("filterEntries", () => {
  it("starts with everything, then the teams as listed, then whole competitions", () => {
    const entries = filterEntries([ATH, BOU, ALCARAZ], [MLB, EPL, ATP, WC, LALIGA]);
    expect(entries.map((entry) => entry.key)).toEqual([
      ALL_KEY,
      "team:ath",
      "team:bou",
      "team:alc",
      "league:wc",
      "league:laliga",
    ]);
  });

  it("asks /news for the right scope per filter", () => {
    const entries = filterEntries([ATH], [MLB, WC]);
    expect(entries.map((entry) => entry.scope)).toEqual([
      undefined,
      { teamId: "ath" },
      { leagueId: "wc" },
    ]);
  });

  it("carries a team's own name, code and colour", () => {
    expect(entryOf(MOCK_ENTRIES, "team:ath")).toEqual({
      key: "team:ath",
      scope: { teamId: "ath" },
      kind: "team",
      name: "Athletics",
      abbreviation: "ATH",
      color: "#EFB21E",
      leagueId: "mlb",
    });
  });

  it("gives a competition a code from its name and no colour", () => {
    const entries = filterEntries([], [WC]);
    expect(entryOf(entries, "league:wc")).toMatchObject({
      kind: "competition",
      name: "FIFA World Cup",
      abbreviation: "FWC",
      color: null,
      leagueId: "wc",
    });
  });

  it("does not list a league that is only the home of a followed team", () => {
    expect(filterEntries([ATH], [MLB]).map((entry) => entry.key)).toEqual([
      ALL_KEY,
      "team:ath",
    ]);
  });

  it("knows people from teams by the sport of their league", () => {
    const fighter = team("fig", "ufc", "Islam Makhachev", "I. Makha", null);
    const driver = team("ver", "f1", "Max Verstappen", "VER", "#3671C6");
    const entries = filterEntries(
      [ATH, BOU, ALCARAZ, SCHEFFLER, fighter, driver],
      [MLB, EPL, ATP, PGA, UFC, F1],
    );
    expect(entries.map((entry) => entry.kind)).toEqual([
      "all",
      "team",
      "team",
      "athlete",
      "athlete",
      "athlete",
      "athlete",
    ]);
  });

  it("treats a follow whose league is missing as a team", () => {
    const orphan = team("x", "gone", "Somebody", "", null);
    expect(entryOf(filterEntries([orphan], []), "team:x").kind).toBe("team");
  });

  it("is only the everything filter when nothing is followed", () => {
    expect(filterEntries([], [MLB]).map((entry) => entry.key)).toEqual([ALL_KEY]);
  });
});

describe("filterKind", () => {
  const follows = (count: number): FilterEntry[] =>
    filterEntries(
      Array.from({ length: count }, (_, index) =>
        team(`t${index}`, "epl", `Team ${index}`, "", null),
      ),
      [EPL],
    );

  it("is a row of segments for up to six follows", () => {
    expect(filterKind(follows(1))).toBe("segments");
    expect(filterKind(follows(6))).toBe("segments");
  });

  it("is the dropdown past six, as the Results team switcher is", () => {
    expect(filterKind(follows(7))).toBe("select");
    expect(filterKind(follows(20))).toBe("select");
  });

  it("counts whole competitions among the follows", () => {
    const entries = filterEntries(
      [ATH, ARI, BOU, WOL, ALCARAZ],
      [MLB, EPL, ATP, WC, LALIGA],
    );
    expect(filterKind(entries)).toBe("select");
  });
});

describe("storyKey", () => {
  it("is the story's team, else its competition, else nothing", () => {
    expect(storyKey({ team_id: "ath", league_id: null })).toBe("team:ath");
    expect(storyKey({ team_id: null, league_id: "wc" })).toBe("league:wc");
    expect(storyKey({ team_id: "ath", league_id: "mlb" })).toBe("team:ath");
    expect(storyKey({ team_id: null, league_id: null })).toBeNull();
  });
});

describe("scopeCount", () => {
  const all = entryOf(MOCK_ENTRIES, ALL_KEY);
  const ath = entryOf(MOCK_ENTRIES, "team:ath");
  const bou = entryOf(MOCK_ENTRIES, "team:bou");

  it("says nothing until the filter's own feed has answered", () => {
    expect(scopeCount(ath, null, feed(stories(4)))).toBeNull();
    expect(scopeCount(all, null, null)).toBeNull();
  });

  it("is the number of stories the filter lists", () => {
    const mixed = [...stories(4, "ath"), ...stories(2, "ari")];
    expect(scopeCount(all, feed(mixed), feed(mixed))).toEqual({
      count: 6,
      more: false,
    });
    expect(scopeCount(ath, feed(stories(4, "ath")), feed(mixed))).toEqual({
      count: 4,
      more: false,
    });
    expect(scopeCount(bou, feed([]), feed(mixed))).toEqual({
      count: 0,
      more: false,
    });
  });

  it("is a floor when the filter holds more than the page lists", () => {
    const busy = stories(FEED_REQUEST, "ath");
    expect(scopeCount(ath, feed(busy), feed(busy))).toEqual({
      count: FEED_SHOWN,
      more: true,
    });
    expect(scopeCount(all, feed(busy), feed(busy))).toEqual({
      count: FEED_SHOWN,
      more: true,
    });
  });

  it("does not take a team's share of the unfiltered feed for its count", () => {
    // The newest fifty of everything are all one busy team's; the quiet
    // team's own feed still holds seven.
    const crowded = feed(stories(FEED_REQUEST, "ath"));
    expect(scopeCount(bou, feed(stories(7, "bou")), crowded)).toEqual({
      count: 7,
      more: false,
    });
  });

  it("never says fewer than the unfiltered feed already shows", () => {
    // The two answers are a moment apart: a story has arrived in between.
    const newer = feed([...stories(1, "bou"), ...stories(3, "ath")]);
    expect(scopeCount(bou, feed([]), newer)).toEqual({ count: 1, more: false });
  });

  it("counts a competition by its league id", () => {
    const entries = filterEntries([ATH], [MLB, WC]);
    const wc = entryOf(entries, "league:wc");
    const own = feed(stories(3, "league:wc"));
    expect(scopeCount(wc, own, feed(stories(9, "ath")))).toEqual({
      count: 3,
      more: false,
    });
  });
});

describe("countText and countSpoken", () => {
  it("writes a count, a zero, and a floor", () => {
    expect(countText({ count: 4, more: false })).toBe("4");
    expect(countText({ count: 0, more: false })).toBe("0");
    expect(countText({ count: 50, more: true })).toBe("50+");
  });

  it("writes nothing for a filter that has not answered", () => {
    expect(countText(null)).toBeNull();
    expect(countSpoken("Athletics", null)).toBe("Athletics");
  });

  it("says the count in words a screen reader can use", () => {
    expect(countSpoken("Athletics", { count: 4, more: false })).toBe(
      "Athletics, 4 stories",
    );
    expect(countSpoken("Chelsea", { count: 1, more: false })).toBe(
      "Chelsea, 1 story",
    );
    expect(countSpoken("AFC Bournemouth", { count: 0, more: false })).toBe(
      "AFC Bournemouth, no stories",
    );
    expect(countSpoken("All", { count: 50, more: true })).toBe(
      "All, more than 50 stories",
    );
  });
});

describe("quietEntries", () => {
  const feedsOf = (
    pairs: [string, Feed | null][],
  ): Map<string, Feed | null> => new Map(pairs);

  it("lists the follows whose own feed came back empty", () => {
    const mixed = [...stories(4, "ath"), ...stories(2, "ari")];
    const quiet = quietEntries(
      MOCK_ENTRIES,
      feedsOf([
        [ALL_KEY, feed(mixed)],
        ["team:ath", feed(stories(4, "ath"))],
        ["team:ari", feed(stories(2, "ari"))],
        ["team:bou", feed([])],
      ]),
    );
    expect(quiet.map((entry) => entry.key)).toEqual(["team:bou"]);
  });

  it("does not call a follow quiet for being absent from the newest fifty", () => {
    const quiet = quietEntries(
      MOCK_ENTRIES,
      feedsOf([
        [ALL_KEY, feed(stories(FEED_REQUEST, "ath"))],
        ["team:ath", feed(stories(FEED_REQUEST, "ath"))],
        ["team:ari", feed(stories(3, "ari"))],
        ["team:bou", feed(stories(1, "bou"))],
      ]),
    );
    expect(quiet).toEqual([]);
  });

  it("does not call a follow quiet before its feed has answered", () => {
    const quiet = quietEntries(
      MOCK_ENTRIES,
      feedsOf([
        [ALL_KEY, feed(stories(2, "ath"))],
        ["team:ath", feed(stories(2, "ath"))],
        ["team:ari", null],
      ]),
    );
    expect(quiet).toEqual([]);
  });

  it("does not call a follow quiet while the unfiltered feed shows its story", () => {
    const quiet = quietEntries(
      MOCK_ENTRIES,
      feedsOf([
        [ALL_KEY, feed(stories(1, "bou"))],
        ["team:ath", feed(stories(1, "ath"))],
        ["team:ari", feed(stories(1, "ari"))],
        ["team:bou", feed([])],
      ]),
    );
    expect(quiet).toEqual([]);
  });

  it("never lists the everything filter", () => {
    const quiet = quietEntries(
      MOCK_ENTRIES,
      feedsOf([
        [ALL_KEY, feed([])],
        ["team:ath", feed([])],
        ["team:ari", feed([])],
        ["team:bou", feed([])],
      ]),
    );
    expect(quiet.map((entry) => entry.key)).toEqual([
      "team:ath",
      "team:ari",
      "team:bou",
    ]);
    expect(QUIET_ROWS_MAX).toBeGreaterThanOrEqual(1);
  });
});

describe("what an empty filter says", () => {
  const leagues = [MLB, EPL, ATP, PGA];
  const entries = filterEntries([BOU, ALCARAZ, SINNER, SCHEFFLER], leagues);
  const bou = entryOf(entries, "team:bou");
  const alcaraz = entryOf(entries, "team:alc");
  const sinner = entryOf(entries, "team:sin");
  const scheffler = entryOf(entries, "team:sch");
  // Alcaraz holds the tour's stories; the others hold none.
  const held = countsOf([
    ["team:bou", 0],
    ["team:alc", 4],
    ["team:sin", 0],
    ["team:sch", 0],
  ]);

  it("is plain for a team: no stories, and nothing called recent", () => {
    expect(quietCaveat(bou, entries, leagues, held)).toBeNull();
    expect(quietHeadline(bou, entries, leagues, held)).toBe(
      "No stories for AFC Bournemouth",
    );
    expect(quietLine(bou, entries, leagues, held)).toBe(
      "No stories for AFC Bournemouth.",
    );
  });

  it("explains itself for a person whose league-mate holds the stories", () => {
    // ESPN keeps the tour's feed under ONE followed player's id.
    expect(quietCaveat(sinner, entries, leagues, held)).toBe(
      "ATP’s own coverage is kept under one of the people you follow there.",
    );
    expect(quietHeadline(sinner, entries, leagues, held)).toBe(
      "No stories filed under Jannik Sinner",
    );
    expect(quietLine(sinner, entries, leagues, held)).toBe(
      "No stories filed under Jannik Sinner. ATP’s own coverage is kept under one of the people you follow there.",
    );
  });

  it("does not point at another follow when nobody in the league holds a story", () => {
    // Both players just followed, or the collection failed: the tour's
    // coverage is under neither, so neither row may say it is.
    const none = countsOf([
      ["team:bou", 3],
      ["team:alc", 0],
      ["team:sin", 0],
      ["team:sch", 0],
    ]);
    for (const player of [alcaraz, sinner]) {
      expect(quietCaveat(player, entries, leagues, none)).toBeNull();
      expect(quietLine(player, entries, leagues, none)).toBe(
        `No stories for ${player.name}.`,
      );
    }
  });

  it("does not point at a league-mate whose filter has not answered", () => {
    const unknown = countsOf([
      ["team:alc", null],
      ["team:sin", 0],
    ]);
    expect(quietCaveat(sinner, entries, leagues, unknown)).toBeNull();
    expect(quietCaveat(sinner, entries, leagues, new Map())).toBeNull();
  });

  it("is plain for the only person followed in a league", () => {
    expect(quietCaveat(scheffler, entries, leagues, held)).toBeNull();
    expect(quietLine(scheffler, entries, leagues, held)).toBe(
      "No stories for Scottie Scheffler.",
    );
  });

  it("still explains itself when the league's name is not to hand", () => {
    expect(quietCaveat(sinner, entries, [], held)).toBe(
      "The league’s own coverage is kept under one of the people you follow there.",
    );
  });

  it("is plain for a competition", () => {
    const withCup = filterEntries([], [WC]);
    expect(
      quietLine(entryOf(withCup, "league:wc"), withCup, [WC], new Map()),
    ).toBe("No stories for FIFA World Cup.");
  });
});

describe("quietRows", () => {
  const leagues = [MLB, EPL, ATP];
  const entries = filterEntries([BOU, WOL, ATH, ALCARAZ, SINNER], leagues);
  const keys = (list: readonly FilterEntry[]) => list.map((entry) => entry.key);
  const held = countsOf([
    ["team:bou", 0],
    ["team:wol", 0],
    ["team:ath", 0],
    ["team:alc", 1],
    ["team:sin", 0],
  ]);

  it("gives each of a few quiet follows a row of its own", () => {
    const quiet = [entryOf(entries, "team:bou"), entryOf(entries, "team:sin")];
    const rows = quietRows(quiet, entries, leagues, held);
    expect(rows.together).toEqual([]);
    expect(rows.alone.map((row) => row.line)).toEqual([
      "No stories for AFC Bournemouth.",
      "No stories filed under Jannik Sinner. ATP’s own coverage is kept under one of the people you follow there.",
    ]);
  });

  it("past the limit, shares a row among the plain ones only", () => {
    // Three teams and Sinner are quiet; Alcaraz holds the ATP's stories.
    const quiet = ["team:bou", "team:wol", "team:ath", "team:sin"].map((key) =>
      entryOf(entries, key),
    );
    expect(quiet.length).toBeGreaterThan(QUIET_ROWS_MAX);
    const rows = quietRows(quiet, entries, leagues, held);
    expect(keys(rows.together)).toEqual(["team:bou", "team:wol", "team:ath"]);
    // Sinner is not listed as plainly having no stories: he keeps the row
    // that says where the tour's stories are.
    expect(rows.alone).toHaveLength(1);
    expect(rows.alone[0].entry.key).toBe("team:sin");
    expect(rows.alone[0].line).toContain("No stories filed under Jannik Sinner.");
  });

  it("shares the row among them all when nobody holds the league's stories", () => {
    const none = countsOf([["team:alc", 0]]);
    const quiet = entries.filter((entry) => entry.kind !== "all");
    const rows = quietRows(quiet, entries, leagues, none);
    expect(keys(rows.together)).toEqual(keys(quiet));
    expect(rows.alone).toEqual([]);
  });

  it("does not make a shared row of one", () => {
    const tour = filterEntries(
      [BOU, ALCARAZ, SINNER, team("djo", "atp", "Novak Djokovic", "N. Djoko", null), team("zve", "atp", "Alexander Zverev", "A. Zvere", null)],
      leagues,
    );
    const counts = countsOf([["team:alc", 2]]);
    const quiet = tour.filter(
      (entry) => entry.kind !== "all" && entry.key !== "team:alc",
    );
    const rows = quietRows(quiet, tour, leagues, counts);
    expect(rows.together).toEqual([]);
    expect(keys(rows.alone.map((row) => row.entry))).toEqual(keys(quiet));
    expect(rows.alone[0].line).toBe("No stories for AFC Bournemouth.");
  });
});

describe("markFor and markLabel", () => {
  const leagues = [MLB, EPL, ATP, WC];
  const entries = filterEntries([ATH, WOL, ALCARAZ], leagues);

  it("marks a story with its followed team", () => {
    const mark = markFor(story({ team_id: "ath" }), entries, leagues);
    expect(mark).toEqual({
      key: "team:ath",
      name: "Athletics",
      abbreviation: "ATH",
      color: "#EFB21E",
      context: null,
    });
    expect(markLabel(mark)).toBe("Athletics");
  });

  it("keeps a team with no colour colourless", () => {
    const mark = markFor(story({ team_id: "wol" }), entries, leagues);
    expect(mark.color).toBeNull();
    expect(mark.abbreviation).toBe("WOL");
  });

  it("names the league beside a person, whose story may be the tour's", () => {
    const mark = markFor(story({ team_id: "alc" }), entries, leagues);
    expect(mark).toMatchObject({
      name: "Carlos Alcaraz",
      abbreviation: "C. Alcar",
      color: null,
      context: "ATP",
    });
    expect(markLabel(mark)).toBe("Carlos Alcaraz · ATP");
  });

  it("marks a competition's story with the competition, colourless", () => {
    const mark = markFor(
      story({ team_id: null, league_id: "wc" }),
      entries,
      leagues,
    );
    expect(mark).toEqual({
      key: "league:wc",
      name: "FIFA World Cup",
      abbreviation: "FWC",
      color: null,
      context: null,
    });
  });

  it("names a known competition that is not one of the filters", () => {
    const mark = markFor(
      story({ team_id: null, league_id: "epl" }),
      entries,
      leagues,
    );
    expect(mark).toEqual({
      key: null,
      name: "Premier League",
      abbreviation: "PL",
      color: null,
      context: null,
    });
  });

  it("gives a story whose team is no longer followed no label, not a raw id", () => {
    const mark = markFor(
      story({ team_id: "gone", league_id: null }),
      entries,
      leagues,
    );
    expect(mark).toEqual({
      key: null,
      name: "",
      abbreviation: null,
      color: null,
      context: null,
    });
    expect(markLabel(mark)).toBe("");
  });

  it("gives a story with neither id no label", () => {
    const mark = markFor(
      story({ team_id: null, league_id: null }),
      entries,
      leagues,
    );
    expect(markLabel(mark)).toBe("");
  });
});

describe("headlineOf", () => {
  it("takes the outlet's own name off the end of a search-feed title", () => {
    expect(
      headlineOf({
        title: "Athletics edge Boston 4-3 to level the series - The Sacramento Bee",
        source: "The Sacramento Bee",
      }),
    ).toBe("Athletics edge Boston 4-3 to level the series");
  });

  it("matches the outlet whatever its capitals, and after other separators", () => {
    expect(headlineOf({ title: "Chelsea win – BBC SPORT", source: "BBC Sport" })).toBe(
      "Chelsea win",
    );
    expect(headlineOf({ title: "Chelsea win | Goal.com", source: "Goal.com" })).toBe(
      "Chelsea win",
    );
    expect(headlineOf({ title: "Chelsea win — Marca", source: "Marca" })).toBe(
      "Chelsea win",
    );
  });

  it("takes off only the last part, and only once", () => {
    expect(
      headlineOf({
        title:
          "Chelsea vs. Bournemouth: Match Highlights (10/10) - Scoreline Stream of Soccer - CBS Sports",
        source: "CBS Sports",
      }),
    ).toBe("Chelsea vs. Bournemouth: Match Highlights (10/10) - Scoreline Stream of Soccer");
  });

  it("works for an outlet named in another script", () => {
    expect(
      headlineOf({
        title: "Arsenal and Chelsea return to winning ways in Premier League - سانا",
        source: "سانا",
      }),
    ).toBe("Arsenal and Chelsea return to winning ways in Premier League");
  });

  it("leaves a dash that is the headline's own", () => {
    expect(
      headlineOf({ title: "Fulham - Chelsea 2-3", source: "Flashscore.com" }),
    ).toBe("Fulham - Chelsea 2-3");
    expect(
      headlineOf({ title: "Twins outslug Athletics 11-8", source: "ESPN" }),
    ).toBe("Twins outslug Athletics 11-8");
  });

  it("leaves a title that would be nothing without the outlet", () => {
    expect(headlineOf({ title: " - ESPN", source: "ESPN" })).toBe("- ESPN");
    expect(headlineOf({ title: "ESPN", source: "ESPN" })).toBe("ESPN");
  });

  it("trims, and copes with no source", () => {
    expect(headlineOf({ title: "  A headline  ", source: "" })).toBe("A headline");
    expect(headlineOf({ title: "A headline - ", source: "  " })).toBe("A headline -");
  });
});

describe("headlineParts", () => {
  const kept = (headline: string): string[] =>
    headlineParts(headline)
      .filter((part) => part.keep)
      .map((part) => part.text);

  it("keeps a score in one piece", () => {
    expect(headlineParts("Twins outslug Athletics 11-8 in series finale")).toEqual([
      { text: "Twins outslug Athletics ", keep: false },
      { text: "11-8", keep: true },
      { text: " in series finale", keep: false },
    ]);
  });

  it("keeps a score at either end, and several in one headline", () => {
    expect(headlineParts("4-2: Red Sox take game three")).toEqual([
      { text: "4-2", keep: true },
      { text: ": Red Sox take game three", keep: false },
    ]);
    expect(kept("Fifteen-run night carries Arizona past the Athletics, 15-5")).toEqual([
      "15-5",
    ]);
    expect(kept("Fulham - Chelsea 2-3 after 0-2 at the break")).toEqual(["2-3", "0-2"]);
  });

  it("knows the other ways figures are joined", () => {
    expect(kept("ZAKSA pokonała Jastrzębski Węgiel 3:1")).toEqual(["3:1"]);
    expect(kept("Chelsea 2–2 Bournemouth")).toEqual(["2–2"]);
    expect(kept("The 2026-27 fixtures, and a 4-3-3 for the opener")).toEqual([
      "2026-27",
      "4-3-3",
    ]);
  });

  it("leaves a hyphen between a figure and a word alone", () => {
    expect(kept("36-year-old scores for 10-man Chelsea (10/10)")).toEqual([]);
  });

  it("puts a headline back together unchanged", () => {
    for (const headline of [
      "",
      "No figures here",
      "11-8",
      "Fulham - Chelsea 2-3 after 0-2 at the break",
    ]) {
      expect(
        headlineParts(headline)
          .map((part) => part.text)
          .join(""),
      ).toBe(headline);
    }
    expect(headlineParts("")).toEqual([]);
  });
});

describe("inCapitals", () => {
  it("sets an English headline in capitals", () => {
    expect(inCapitals("Red Sox take game three in West Sacramento, 4-2")).toBe(true);
  });

  it("sets other Latin-script languages in capitals too", () => {
    expect(inCapitals("Altmeister Henderson mit Traumfreistoß!")).toBe(true);
    expect(inCapitals("ZAKSA Kędzierzyn-Koźle pokonała Jastrzębski Węgiel")).toBe(true);
    expect(inCapitals("Vinícius Júnior, expulsado en el Bernabéu")).toBe(true);
  });

  it("leaves a script without capitals to the browser, which changes nothing", () => {
    expect(inCapitals("أرسنال وتشيلسي يعودان إلى سكة الانتصارات")).toBe(true);
  });

  it("leaves a Turkish headline as written: capitals would respell it", () => {
    expect(inCapitals("İstanbul’da kritik maç: Chelsea deplasmanda")).toBe(false);
    expect(inCapitals("Chelsea, Fenerbahçe deplasmanında ışıl ışıl")).toBe(false);
    expect(inCapitals("Galatasaray’dan sürpriz transfer: Yeni golcü geldiği gibi")).toBe(
      false,
    );
  });
});

describe("deckOf", () => {
  it("is nothing for a story without a summary", () => {
    expect(deckOf(story({ summary: null }))).toBeNull();
    expect(deckOf(story({ summary: "   " }))).toBeNull();
    expect(deckOf(story({ summary: "…" }))).toBeNull();
  });

  it("is nothing when the summary is the headline again", () => {
    expect(
      deckOf(
        story({
          title: "Burley: Vinícius Jr. must learn actions have consequences",
          summary: "Burley: Vinícius Jr. must learn actions have consequences",
        }),
      ),
    ).toBeNull();
  });

  it("is nothing when it is the headline with the outlet moved to the end", () => {
    expect(
      deckOf(
        story({
          title: "Premier League: Arsenal, Chelsea win; Spurs hold Manchester Utd - Rediff",
          source: "Rediff",
          summary: "Premier League: Arsenal, Chelsea win; Spurs hold Manchester Utd  Rediff",
        }),
      ),
    ).toBeNull();
    expect(
      deckOf(
        story({
          title: "Fulham - Chelsea 2-3 - Flashscore.com",
          source: "Flashscore.com",
          summary: "Fulham - Chelsea 2-3 Flashscore.com",
        }),
      ),
    ).toBeNull();
  });

  it("is nothing when only the punctuation and capitals differ", () => {
    expect(
      deckOf(
        story({
          title: "Chelsea 2-2 Bournemouth: late equaliser denies the Blues",
          summary: "CHELSEA 2–2 BOURNEMOUTH — late equaliser denies the Blues.",
        }),
      ),
    ).toBeNull();
  });

  it("is nothing for a run of other outlets' headlines after its own", () => {
    // A search-feed "cluster": the headline, the outlet, then related
    // coverage from elsewhere, with nothing between them.
    expect(
      deckOf(
        story({
          title:
            "Chelsea vs Bournemouth: Premier League preview, team news, stats & head-to-head - BBC",
          source: "BBC",
          summary:
            "Chelsea vs Bournemouth: Premier League preview, team news, stats & head-to-head  BBC Chelsea 5-1 AFC Bournemouth, Premier League: Post-match reaction, ratings  We Ain't Got No History Chelsea - Bournemouth LIVE. Premier League latest updates  MARCA",
        }),
      ),
    ).toBeNull();
  });

  it("keeps a real summary that opens with the headline's words", () => {
    const summary =
      "Red Sox take game three in West Sacramento, 4-2, scoring twice in the eighth.";
    expect(
      deckOf(
        story({
          title: "Red Sox take game three in West Sacramento, 4-2",
          source: "ESPN",
          summary,
        }),
      ),
    ).toBe(summary);
    // With no outlet named there is nothing to tell a cluster by.
    expect(
      deckOf(story({ title: "A headline", source: "", summary: "A headline and more." })),
    ).toBe("A headline and more.");
  });

  it("is the summary when it says more than the headline", () => {
    const summary =
      "Real Madrid held on for a 1-0 win over Villarreal in a stormy encounter.";
    expect(
      deckOf(
        story({
          title: "Yan Diomande's first goal gives 10-man Real Madrid narrow win",
          summary: `  ${summary}  `,
        }),
      ),
    ).toBe(summary);
  });
});

describe("dateParts", () => {
  it("is nothing for a story with no date, or one that cannot be read", () => {
    expect(dateParts(null)).toBeNull();
    expect(dateParts("not a date")).toBeNull();
  });

  const NOW = new Date("2026-07-30T21:58:00-04:00");

  it("cuts the date for the cell, in the viewer's zone", () => {
    // 02:41 UTC on the 30th is 10:41 PM on the 29th in New York.
    const parts = dateParts("2026-07-30T02:41:00Z", NOW);
    expect(parts).toMatchObject({
      month: "Jul",
      day: "29",
      key: "2026-07-29",
      time: "10:41 PM",
    });
    expect(parts?.full).toMatch(/^Wednesday, July 29, 2026(,| at) 10:41 PM$/);
  });

  it("does not pad the day, and reads an offset as written", () => {
    const parts = dateParts("2026-10-03T09:05:00-04:00", NOW);
    expect(parts).toMatchObject({
      month: "Oct",
      day: "3",
      key: "2026-10-03",
      time: "9:05 AM",
    });
  });

  it("puts a story from just after midnight UTC on the evening before", () => {
    expect(dateParts("2026-01-01T00:30:00Z", NOW)).toMatchObject({
      month: "Dec",
      day: "31",
      year: "2025",
      key: "2025-12-31",
      time: "7:30 PM",
    });
  });

  it("carries the year only when it is not the viewer's current one", () => {
    expect(dateParts("2026-07-30T20:00:00-04:00", NOW)?.year).toBeNull();
    expect(dateParts("2026-01-02T12:00:00-05:00", NOW)?.year).toBeNull();
    expect(dateParts("2025-07-30T20:00:00-04:00", NOW)?.year).toBe("2025");
    expect(dateParts("2024-10-09T12:00:00-04:00", NOW)?.year).toBe("2024");
    // The year is the viewer's: 03:00 UTC on New Year's Day is still
    // the old year in New York.
    expect(
      dateParts("2027-01-01T03:00:00Z", new Date("2027-01-01T03:30:00Z"))?.year,
    ).toBeNull();
  });

  it("tells a story from today from one a year ago to the minute", () => {
    const today = dateParts("2026-07-30T20:00:00-04:00", NOW);
    const lastYear = dateParts("2025-07-30T20:00:00-04:00", NOW);
    if (today === null || lastYear === null) throw new Error("no date");
    expect(dateLine(today)).toBe("Jul 30, 8:00 PM");
    expect(dateLine(lastYear)).toBe("Jul 30, 2025, 8:00 PM");
  });

  it("uses plain spaces, so the tooltip and the spoken label match", () => {
    expect(dateParts("2026-07-30T02:41:00Z")?.full).not.toMatch(/[  ]/);
  });
});

describe("ageText", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const at = (now: string) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(now));
    return (iso: string) => {
      const date = dateParts(iso);
      if (date === null) throw new Error("no date");
      return ageText(iso, date);
    };
  };

  it("is an age while the story is recent", () => {
    const age = at("2026-07-30T21:58:00-04:00");
    expect(age("2026-07-30T21:21:00-04:00")).toBe("37m ago");
    expect(age("2026-07-10T12:00:00-04:00")).toBe("2w ago");
  });

  it("is the date past five weeks, with the year when it is not this one", () => {
    const age = at("2026-07-30T21:58:00-04:00");
    expect(age("2026-05-01T12:00:00-04:00")).toBe("May 1");
    expect(age("2025-12-01T12:00:00-05:00")).toBe("Dec 1, 2025");
    expect(age("2024-10-09T12:00:00-04:00")).toBe("Oct 9, 2024");
  });

  it("needs no year for last week's story just after New Year", () => {
    const age = at("2027-01-02T12:00:00-05:00");
    expect(age("2026-12-30T12:00:00-05:00")).toBe("3d ago");
  });
});

describe("heroHeadlineRem", () => {
  it("is the mockup's 52px for a headline of the mockup's length", () => {
    expect(
      heroHeadlineRem(
        "Kurtz exits against Red Sox with bruised left forearm after hit-by-pitch; MRI planned",
      ),
    ).toBe(3.25);
    expect(heroHeadlineRem("x".repeat(90))).toBe(3.25);
  });

  it("steps down for the longer headlines real feeds send", () => {
    expect(heroHeadlineRem("x".repeat(91))).toBe(2.75);
    expect(heroHeadlineRem("x".repeat(130))).toBe(2.75);
    expect(heroHeadlineRem("x".repeat(131))).toBe(2.25);
    expect(heroHeadlineRem("x".repeat(400))).toBe(2.25);
  });

  it("measures the headline, not the space around it", () => {
    expect(heroHeadlineRem(`  ${"x".repeat(90)}  `)).toBe(3.25);
  });
});

describe("heroCodeSize", () => {
  it("is the mockup's 168px, capped by the slot's width per letter", () => {
    expect(heroCodeSize("ATH")).toBe("min(10.5rem, 47cqi)");
    expect(heroCodeSize("FWC")).toBe("min(10.5rem, 47cqi)");
  });

  it("gives each letter of a longer code less of the slot", () => {
    expect(heroCodeSize("UECL")).toBe("min(10.5rem, 35cqi)");
    expect(heroCodeSize("CA")).toBe("min(10.5rem, 70cqi)");
  });

  it("copes with an empty code (a story with no team)", () => {
    expect(heroCodeSize("")).toBe("min(10.5rem, 140cqi)");
  });

  it("keeps any code within the slot: at most 0.6em a letter", () => {
    for (const code of ["A", "CA", "ATH", "UECL"]) {
      const cqi = Number(/(\d+)cqi/.exec(heroCodeSize(code))?.[1]);
      expect(cqi * 0.6 * code.length).toBeLessThanOrEqual(85);
    }
  });
});

describe("the notes beside the section tabs", () => {
  it("says what the top story is the newest of", () => {
    expect(topNote(entryOf(MOCK_ENTRIES, ALL_KEY), MOCK_ENTRIES)).toBe(
      "Newest headline across your teams",
    );
    expect(topNote(entryOf(MOCK_ENTRIES, "team:ari"), MOCK_ENTRIES)).toBe(
      "Newest headline for Arizona Diamondbacks",
    );
  });

  it("does not say teams when people or competitions are followed too", () => {
    const mixed = filterEntries([ATH, ALCARAZ], [MLB, ATP]);
    expect(topNote(entryOf(mixed, ALL_KEY), mixed)).toBe(
      "Newest headline across everything you follow",
    );
    const cup = filterEntries([ATH], [MLB, WC]);
    expect(topNote(entryOf(cup, ALL_KEY), cup)).toBe(
      "Newest headline across everything you follow",
    );
  });

  it("counts the stories under the top one", () => {
    expect(moreNote(feed(stories(6)))).toBe("5 stories · newest first");
    expect(moreNote(feed(stories(2)))).toBe("1 story · newest first");
  });

  it("says so when the top story is the only one", () => {
    expect(moreNote(feed(stories(1)))).toBe("No other stories");
    expect(moreNote(feed([]))).toBe("No other stories");
  });

  it("says the list is a window when the filter holds more", () => {
    expect(moreNote(feed(stories(FEED_REQUEST)))).toBe(
      `${FEED_SHOWN - 1} stories · newest first · only the newest ${FEED_SHOWN} are listed`,
    );
  });
});

describe("rowLabel and sourceLabel", () => {
  const entries = filterEntries([ATH, ALCARAZ], [MLB, ATP]);

  it("names a row by its headline, then who, where and when", () => {
    const item = story({ team_id: "ath" });
    const label = rowLabel(
      headlineOf(item),
      markFor(item, entries, [MLB, ATP]),
      item.source,
      dateParts(item.published_at),
    );
    expect(label).toMatch(
      /^Red Sox take game three in West Sacramento, 4-2\. Athletics, ESPN, Wednesday, July 29, 2026(,| at) 10:41 PM$/,
    );
  });

  it("leaves out what a story does not carry", () => {
    const item = story({ team_id: "gone", published_at: null });
    expect(
      rowLabel("A headline", markFor(item, entries, [MLB, ATP]), "ESPN", null),
    ).toBe("A headline. ESPN");
    expect(
      rowLabel("A headline", markFor(item, entries, [MLB, ATP]), "", null),
    ).toBe("A headline");
  });

  it("names the league with a person", () => {
    const item = story({ team_id: "alc", published_at: null });
    expect(
      rowLabel("A headline", markFor(item, entries, [MLB, ATP]), "ESPN", null),
    ).toBe("A headline. Carlos Alcaraz · ATP, ESPN");
  });

  it("names the link out by the story and the outlet", () => {
    expect(sourceLabel("A headline", "ESPN")).toBe(
      "Read “A headline” at ESPN, in a new tab",
    );
    expect(sourceLabel("A headline", "")).toBe(
      "Read “A headline” at its source, in a new tab",
    );
  });
});


describe("the refresh line", () => {
  const all = { kind: "all", name: "All" } as const;
  const athletics = { kind: "team", name: "Athletics" } as const;

  it("counts what a refresh of everything found, on its own page", () => {
    expect(refreshedLine("9:58 PM", 3, all, true)).toBe(
      "Refreshed at 9:58 PM · 3 new stories",
    );
    expect(refreshedLine("9:58 PM", 1, all, true)).toBe(
      "Refreshed at 9:58 PM · 1 new story",
    );
    expect(refreshedLine("9:58 PM", 0, all, true)).toBe(
      "Refreshed at 9:58 PM · nothing new",
    );
  });

  it("says whose they are when the result lands on another filter's page", () => {
    // Refresh pressed on All, the filter changed before it answered: the
    // count must not read as the Athletics'.
    expect(refreshedLine("9:58 PM", 3, all, false)).toBe(
      "Refreshed at 9:58 PM · 3 new stories across everything you follow",
    );
    expect(refreshedLine("9:58 PM", 0, all, false)).toBe(
      "Refreshed at 9:58 PM · nothing new across everything you follow",
    );
  });

  it("always names a single follow", () => {
    for (const onScreen of [true, false]) {
      expect(refreshedLine("9:58 PM", 3, athletics, onScreen)).toBe(
        "Refreshed at 9:58 PM · 3 new stories for Athletics",
      );
    }
  });

  it("points at the stories below only when there are some", () => {
    expect(refreshFailedLine(" — provider timed out", true)).toBe(
      "Refresh failed — provider timed out. The stories below are the last ones loaded.",
    );
    expect(refreshFailedLine(" — provider timed out", false)).toBe(
      "Refresh failed — provider timed out.",
    );
    expect(refreshFailedLine("", false)).toBe("Refresh failed.");
  });

  it("is spent once the viewer's day has changed", () => {
    expect(refreshLineSpent("2026-07-30", "2026-07-30")).toBe(false);
    expect(refreshLineSpent("2026-07-30", "2026-07-31")).toBe(true);
    expect(refreshLineSpent("2026-12-31", "2027-01-01")).toBe(true);
    // A result from just after midnight, read against a clock that has
    // not ticked over yet, is not spent.
    expect(refreshLineSpent("2026-07-31", "2026-07-30")).toBe(false);
  });
});

describe("feedOverdue", () => {
  const FIVE = 5 * 60_000;
  const now = Date.parse("2026-07-30T21:58:00-04:00");

  it("is true once a whole beat has gone by since the last read", () => {
    expect(feedOverdue(now - FIVE, now, FIVE)).toBe(true);
    expect(feedOverdue(now - 3 * FIVE, now, FIVE)).toBe(true);
    // Never read at all.
    expect(feedOverdue(0, now, FIVE)).toBe(true);
  });

  it("is false for a feed read within the beat", () => {
    expect(feedOverdue(now - FIVE + 1, now, FIVE)).toBe(false);
    expect(feedOverdue(now, now, FIVE)).toBe(false);
  });
});
