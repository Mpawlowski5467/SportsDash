/**
 * The wizard's selection rules. In manage mode the wizard opens on the
 * followed set and a save deletes the stored data of whatever is missing
 * from it, so these are the rules that decide what gets unfollowed:
 * - the seed must post back exactly the set it was built from;
 * - a toggle and its undo must leave that set alone;
 * - what is only remembered (a deselected league's picks, a whole-league
 *   league's picks) must never be read as followed;
 * - a deselected league must stay on the grid, where its undo is;
 * - and what a save would unfollow must be reportable before it happens.
 */
import { describe, expect, it } from "vitest";

import {
  EMPTY_SELECTION,
  clearLeagueTeams,
  followAllIds,
  followedLeagues,
  isFollowAll,
  mergeSelectedTeams,
  offeredLeagues,
  pickTeamLeagueIds,
  rememberedLeagueIds,
  removedFromSeed,
  seedFromSelection,
  toFollowRequest,
  toggleFollowAll,
  toggleLeague,
  toggleTeam,
  totalSelected,
  type SelectionState,
} from "./selection";
import type {
  CatalogLeague,
  CatalogTeam,
  CurrentSelectionsResponse,
} from "../../types";

function makeTeam(key: string, name: string): CatalogTeam {
  return {
    provider_key: key,
    name,
    abbreviation: name.slice(0, 3).toUpperCase(),
    logo_url: null,
    color: null,
  };
}

const HERONS = makeTeam("11", "Harbor Herons");
const AUKS = makeTeam("12", "Alpine Auks");
const OTTERS = makeTeam("13", "Orchard Otters");
const VANCE = makeTeam("901", "Marlowe Vance");

/**
 * A followed set with one league of each kind, and the pick-teams one FIRST
 * so that a league re-selected at the end of the list would show up as a
 * reordered request.
 */
const FOLLOWED: CurrentSelectionsResponse = {
  selections: [
    { league_id: "test-club", follow_all: false, teams: [HERONS, AUKS] },
    { league_id: "test-cup", follow_all: true, teams: [] },
    { league_id: "test-tour", follow_all: false, teams: [VANCE] },
  ],
};

/** The request that follows exactly `FOLLOWED`. */
const FOLLOWED_REQUEST = {
  selections: [
    { league_id: "test-club", team_provider_keys: ["11", "12"] },
    { league_id: "test-cup", team_provider_keys: [], follow_all: true },
    { league_id: "test-tour", team_provider_keys: ["901"] },
  ],
};

describe("seedFromSelection", () => {
  it("opens on the leagues step with every followed league selected", () => {
    const seed = seedFromSelection(FOLLOWED);

    expect(seed.step).toBe("leagues");
    expect(seed.selectedLeagueIds).toEqual([
      "test-club",
      "test-cup",
      "test-tour",
    ]);
    expect(seed.followAllLeagueIds).toEqual(["test-cup"]);
    expect(seed.teamsByLeague).toEqual({
      "test-club": [HERONS, AUKS],
      "test-tour": [VANCE],
    });
  });

  it("applies no country filter", () => {
    // A filter derived from the follows would open the editor with every
    // other country's leagues hidden; empty hides nothing.
    expect(seedFromSelection(FOLLOWED).selectedCountryCodes).toEqual([]);
  });

  it("starts where a first run does when nothing is followed", () => {
    const seed = seedFromSelection({ selections: [] });

    expect(seed.step).toBe("countries");
    expect(toFollowRequest(seed)).toEqual({ selections: [] });
  });

  it("posts back exactly the set it was built from", () => {
    // The property the editor rests on: open it, touch nothing, save, and
    // the followed set is unchanged.
    expect(toFollowRequest(seedFromSelection(FOLLOWED))).toEqual(
      FOLLOWED_REQUEST,
    );
  });
});

describe("toFollowRequest", () => {
  it("drops a selected league that ended up with no picks", () => {
    // Selected but empty follows nothing, and the backend rejects a
    // selection with neither teams nor follow_all.
    const picked = toggleTeam(
      toggleLeague(EMPTY_SELECTION, "test-club"),
      "test-club",
      HERONS,
    );
    const state = toggleLeague(picked, "test-tour");

    expect(toFollowRequest(state)).toEqual({
      selections: [{ league_id: "test-club", team_provider_keys: ["11"] }],
    });
  });

  it("ignores the picks a deselected league remembers", () => {
    const state = toggleLeague(seedFromSelection(FOLLOWED), "test-club");

    // Still remembered...
    expect(state.teamsByLeague["test-club"]).toEqual([HERONS, AUKS]);
    // ...but not followed.
    expect(toFollowRequest(state)).toEqual({
      selections: [
        { league_id: "test-cup", team_provider_keys: [], follow_all: true },
        { league_id: "test-tour", team_provider_keys: ["901"] },
      ],
    });
    expect(totalSelected(state)).toBe(1);
  });

  it("ignores the picks a whole-league league remembers", () => {
    const state = toggleFollowAll(seedFromSelection(FOLLOWED), "test-club");

    expect(state.teamsByLeague["test-club"]).toEqual([HERONS, AUKS]);
    expect(toFollowRequest(state).selections[0]).toEqual({
      league_id: "test-club",
      team_provider_keys: [],
      follow_all: true,
    });
    expect(totalSelected(state)).toBe(1);
  });
});

describe("toggleLeague", () => {
  it("restores a pick-teams league exactly when toggled off and on", () => {
    const seed = seedFromSelection(FOLLOWED);
    const off = toggleLeague(seed, "test-club");
    const on = toggleLeague(off, "test-club");

    expect(followedLeagues(off).map((follow) => follow.leagueId)).toEqual([
      "test-cup",
      "test-tour",
    ]);
    // Same picks, and back in its own place rather than at the end.
    expect(toFollowRequest(on)).toEqual(FOLLOWED_REQUEST);
  });

  it("restores a whole-league follow exactly when toggled off and on", () => {
    const seed = seedFromSelection(FOLLOWED);
    const off = toggleLeague(seed, "test-cup");
    const on = toggleLeague(off, "test-cup");

    expect(followedLeagues(off).map((follow) => follow.leagueId)).toEqual([
      "test-club",
      "test-tour",
    ]);
    expect(toFollowRequest(on)).toEqual(FOLLOWED_REQUEST);
  });

  it("does not show a deselected league's remembered flag as active", () => {
    // The chip reads its follow-all pill from this list: a league that is
    // off must not render as a live whole-league follow.
    const off = toggleLeague(seedFromSelection(FOLLOWED), "test-cup");

    expect(off.followAllLeagueIds).toContain("test-cup");
    expect(isFollowAll(off, "test-cup")).toBe(false);
    expect(followAllIds(off)).toEqual([]);
    expect(pickTeamLeagueIds(off)).toEqual(["test-club", "test-tour"]);
  });

  it("never drops a selected league the order has lost track of", () => {
    // `leagueOrder` is meant to cover every selected league. If a state ever
    // reaches a toggle without that, the toggle must still leave the other
    // leagues selected: filtering by an incomplete order would unfollow
    // them without a click.
    const state = toggleLeague(
      { ...seedFromSelection(FOLLOWED), leagueOrder: [] },
      "test-new",
    );

    expect(state.selectedLeagueIds).toEqual([
      "test-club",
      "test-cup",
      "test-tour",
      "test-new",
    ]);
  });

  it("appends a league selected for the first time", () => {
    const state = toggleLeague(seedFromSelection(FOLLOWED), "test-new");

    expect(state.selectedLeagueIds).toEqual([
      "test-club",
      "test-cup",
      "test-tour",
      "test-new",
    ]);
  });
});

describe("toggleFollowAll", () => {
  it("restores a league's picks when marked and unmarked", () => {
    const seed = seedFromSelection(FOLLOWED);
    const marked = toggleFollowAll(seed, "test-club");
    const unmarked = toggleFollowAll(marked, "test-club");

    expect(followAllIds(marked)).toEqual(["test-club", "test-cup"]);
    expect(pickTeamLeagueIds(marked)).toEqual(["test-tour"]);
    expect(toFollowRequest(unmarked)).toEqual(FOLLOWED_REQUEST);
  });

  it("restores a whole-league follow when unmarked and marked", () => {
    const seed = seedFromSelection(FOLLOWED);
    const unmarked = toggleFollowAll(seed, "test-cup");
    const marked = toggleFollowAll(unmarked, "test-cup");

    // Unmarked, it is a pick-teams league with nothing picked: not followed.
    expect(pickTeamLeagueIds(unmarked)).toContain("test-cup");
    expect(
      followedLeagues(unmarked).map((follow) => follow.leagueId),
    ).toEqual(["test-club", "test-tour"]);
    expect(toFollowRequest(marked)).toEqual(FOLLOWED_REQUEST);
  });

  it("selects a league that was not selected", () => {
    const state = toggleFollowAll(EMPTY_SELECTION, "test-cup");

    expect(toFollowRequest(state)).toEqual({
      selections: [
        { league_id: "test-cup", team_provider_keys: [], follow_all: true },
      ],
    });
  });

  it("follows nothing more after marking and unmarking a fresh league", () => {
    // Unmarking leaves the league selected in pick-teams mode, as the chip
    // always has; with no picks it adds nothing to the request.
    const seed = seedFromSelection(FOLLOWED);
    const marked = toggleFollowAll(seed, "test-new");
    const state = toggleFollowAll(marked, "test-new");

    expect(state.selectedLeagueIds).toContain("test-new");
    expect(toFollowRequest(state)).toEqual(FOLLOWED_REQUEST);
  });

  it("brings a deselected league back with its picks, never without", () => {
    // The one sequence that is not a clean round trip: the league was off,
    // and marking then unmarking leaves it on, as unmarking always has. Its
    // picks come back with it — the direction that deletes nothing.
    const off = toggleLeague(seedFromSelection(FOLLOWED), "test-club");
    const marked = toggleFollowAll(off, "test-club");
    const state = toggleFollowAll(marked, "test-club");

    expect(toFollowRequest(state)).toEqual(FOLLOWED_REQUEST);
  });

  it("re-marks a deselected league without duplicating its flag", () => {
    const off = toggleLeague(seedFromSelection(FOLLOWED), "test-cup");
    const marked = toggleFollowAll(off, "test-cup");

    expect(marked.followAllLeagueIds).toEqual(["test-cup"]);
    expect(toFollowRequest(marked)).toEqual(FOLLOWED_REQUEST);
  });
});

describe("toggleTeam", () => {
  it("adds a team that is not picked and removes one that is", () => {
    const seed = seedFromSelection(FOLLOWED);
    const added = toggleTeam(seed, "test-club", OTTERS);
    const removed = toggleTeam(added, "test-club", HERONS);

    expect(added.teamsByLeague["test-club"]).toEqual([HERONS, AUKS, OTTERS]);
    expect(removed.teamsByLeague["test-club"]).toEqual([AUKS, OTTERS]);
    expect(totalSelected(removed)).toBe(3);
  });

  it("matches on the provider key, not on the object", () => {
    // The seed's teams come from the database and the grid's from the live
    // catalog: the same team, as two objects whose display fields can
    // differ.
    const renamed = { ...HERONS, name: "Harbour Herons FC" };
    const state = toggleTeam(seedFromSelection(FOLLOWED), "test-club", renamed);

    expect(state.teamsByLeague["test-club"]).toEqual([AUKS]);
  });
});

describe("clearLeagueTeams", () => {
  it("empties one league's picks and leaves the others", () => {
    const state = clearLeagueTeams(seedFromSelection(FOLLOWED), "test-club");

    expect(state.teamsByLeague["test-club"]).toEqual([]);
    expect(toFollowRequest(state)).toEqual({
      selections: [
        { league_id: "test-cup", team_provider_keys: [], follow_all: true },
        { league_id: "test-tour", team_provider_keys: ["901"] },
      ],
    });
  });
});

describe("transitions", () => {
  it("never modify the state they are given", () => {
    // React state: a transition that edited in place would change what the
    // wizard had already rendered, and the seed it compares against.
    const seed = seedFromSelection(FOLLOWED);
    const snapshot = structuredClone(seed);

    toggleLeague(seed, "test-club");
    toggleLeague(seed, "test-new");
    toggleFollowAll(seed, "test-club");
    toggleFollowAll(seed, "test-cup");
    toggleTeam(seed, "test-club", HERONS);
    toggleTeam(seed, "test-club", OTTERS);
    clearLeagueTeams(seed, "test-club");

    expect(seed).toEqual(snapshot);
  });
});

describe("offeredLeagues", () => {
  function makeLeague(id: string, countryCode: string | null): CatalogLeague {
    return {
      id,
      name: id,
      sport: "soccer",
      provider: "espn",
      national: false,
      supports_follow_all: true,
      entity_noun: "team",
      logo_url: null,
      country_code: countryCode,
    };
  }

  // Three countries' leagues and a confederation cup that belongs to none.
  const CATALOG = [
    makeLeague("test-albion", "alb"),
    makeLeague("test-iberia", "ibe"),
    makeLeague("test-rhine", "rhi"),
    makeLeague("test-continental", null),
  ];
  // What manage mode opens on: a league followed by team, and the cup in
  // whole — and no country chosen.
  const seed = seedFromSelection({
    selections: [
      { league_id: "test-albion", follow_all: false, teams: [HERONS] },
      { league_id: "test-continental", follow_all: true, teams: [] },
    ],
  });
  const offered = (countryCodes: string[], state: SelectionState) =>
    offeredLeagues(CATALOG, countryCodes, rememberedLeagueIds(state)).map(
      (league) => league.id,
    );

  it("is the whole catalog when no country is chosen", () => {
    expect(offeredLeagues(CATALOG, [], [])).toEqual(CATALOG);
    expect(offered([], seed)).toEqual(CATALOG.map((league) => league.id));
  });

  it("narrows to the chosen countries, keeping leagues that have none", () => {
    expect(offeredLeagues(CATALOG, ["ibe"], []).map((l) => l.id)).toEqual([
      "test-iberia",
      "test-continental",
    ]);
  });

  it("keeps a followed league from a country that was not chosen", () => {
    // The editor's user went back and picked Iberia to add a league there:
    // the Albion league they already follow must not leave the grid.
    expect(offered(["ibe"], seed)).toEqual([
      "test-albion",
      "test-iberia",
      "test-continental",
    ]);
  });

  it("keeps a league on the grid after it is deselected there", () => {
    // The stray click. With the card gone there was nothing to click to
    // undo it, and the save would have unfollowed the league.
    const deselected = toggleLeague(seed, "test-albion");

    expect(deselected.selectedLeagueIds).not.toContain("test-albion");
    expect(offered(["ibe"], deselected)).toEqual([
      "test-albion",
      "test-iberia",
      "test-continental",
    ]);
    // ...and the second click, on the card that is still there, undoes it.
    expect(toFollowRequest(toggleLeague(deselected, "test-albion"))).toEqual(
      toFollowRequest(seed),
    );
  });

  it("keeps a first-run league that was selected and then deselected", () => {
    const state = toggleLeague(
      toggleLeague(EMPTY_SELECTION, "test-albion"),
      "test-albion",
    );

    expect(offered(["ibe"], state)).toEqual([
      "test-albion",
      "test-iberia",
      "test-continental",
    ]);
  });

  it("still hides a league that was never selected", () => {
    expect(offered(["ibe"], seed)).not.toContain("test-rhine");
  });
});

describe("removedFromSeed", () => {
  it("reports nothing for an untouched seed", () => {
    const seed = seedFromSelection(FOLLOWED);

    expect(removedFromSeed(seed, seed)).toEqual([]);
  });

  it("reports nothing when the edit only adds", () => {
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleFollowAll(
      toggleTeam(seed, "test-club", OTTERS),
      "test-new",
    );

    expect(removedFromSeed(seed, state)).toEqual([]);
  });

  it("reports a team that was unpicked", () => {
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleTeam(seed, "test-club", AUKS);

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-club",
        leagueRemoved: false,
        followAllRemoved: false,
        teams: [AUKS],
      },
    ]);
  });

  it("reports a deselected league with every team it had", () => {
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleLeague(seed, "test-club");

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-club",
        leagueRemoved: true,
        followAllRemoved: false,
        teams: [HERONS, AUKS],
      },
    ]);
  });

  it("reports a league whose picks were all cleared as removed", () => {
    // Still selected, but with nothing picked it is not followed — the
    // request leaves it out, so the league goes like a deselected one.
    const seed = seedFromSelection(FOLLOWED);
    const state = clearLeagueTeams(seed, "test-tour");

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-tour",
        leagueRemoved: true,
        followAllRemoved: false,
        teams: [VANCE],
      },
    ]);
  });

  it("reports a whole-league follow that was dropped", () => {
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleLeague(seed, "test-cup");

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-cup",
        leagueRemoved: true,
        followAllRemoved: true,
        teams: [],
      },
    ]);
  });

  it("reports a whole-league follow narrowed to picked teams", () => {
    // The league stays; following all of it does not.
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleTeam(
      toggleFollowAll(seed, "test-cup"),
      "test-cup",
      OTTERS,
    );

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-cup",
        leagueRemoved: false,
        followAllRemoved: true,
        teams: [],
      },
    ]);
  });

  it("reports the picks of a league switched to whole-league", () => {
    // A whole-league follow keeps the league's games but no team: the
    // picks' rosters and alert settings go with them.
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleFollowAll(seed, "test-club");

    expect(removedFromSeed(seed, state)).toEqual([
      {
        leagueId: "test-club",
        leagueRemoved: false,
        followAllRemoved: false,
        teams: [HERONS, AUKS],
      },
    ]);
  });

  it("reports nothing once a removal is undone", () => {
    const seed = seedFromSelection(FOLLOWED);
    const state = toggleLeague(toggleLeague(seed, "test-club"), "test-club");

    expect(removedFromSeed(seed, state)).toEqual([]);
  });
});

describe("mergeSelectedTeams", () => {
  it("is the catalog, in catalog order, when it lists every pick", () => {
    const catalog = [OTTERS, HERONS, AUKS];

    expect(mergeSelectedTeams(catalog, [HERONS, AUKS], [AUKS])).toEqual(
      catalog,
    );
  });

  it("appends a selected team the catalog no longer lists", () => {
    // The golf catalog is this week's field: a followed golfer sitting the
    // tournament out is absent from it, and still has to have a card.
    expect(mergeSelectedTeams([HERONS, AUKS], [], [VANCE])).toEqual([
      HERONS,
      AUKS,
      VANCE,
    ]);
  });

  it("keeps a seeded team on the grid after it is unpicked", () => {
    // Otherwise unpicking it — or pressing Clear — would remove its card,
    // and with it the only way to pick it again.
    expect(mergeSelectedTeams([HERONS], [VANCE], [])).toEqual([
      HERONS,
      VANCE,
    ]);
  });

  it("lists a team once however many sources carry it", () => {
    const listed = { ...VANCE, name: "M. Vance" };

    expect(mergeSelectedTeams([listed], [VANCE], [VANCE])).toEqual([listed]);
    expect(mergeSelectedTeams([], [VANCE], [VANCE, OTTERS])).toEqual([
      VANCE,
      OTTERS,
    ]);
  });

  it("is the picks alone when the catalog could not be loaded", () => {
    expect(mergeSelectedTeams([], [HERONS, AUKS], [AUKS, OTTERS])).toEqual([
      HERONS,
      AUKS,
      OTTERS,
    ]);
  });
});
