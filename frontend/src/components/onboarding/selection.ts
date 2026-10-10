/**
 * The wizard's selection — which leagues, each in which mode, with which
 * picks — and every way it can change.
 *
 * The state REMEMBERS more than it follows. Deselecting a league keeps its
 * picks and its whole-league flag, and switching a league to whole-league
 * keeps its picks, so that each toggle is undone by toggling again. That
 * used to be a nicety and is now a safeguard: in manage mode the wizard
 * opens on the followed set, and a save deletes the stored data of whatever
 * is missing from it — a toggle that forgot would turn a mis-click and its
 * undo into an unfollowed league.
 *
 * So remembered is not followed, and nothing may read the raw fields as if
 * it were. The effective view below (`followedLeagues` and its neighbours)
 * is the only reading of the state: a league is whole-league only if it is
 * selected AND flagged, and its picks count only if it is selected and NOT
 * whole-league. (`rememberedLeagueIds` reads what is remembered on purpose,
 * and only to decide which cards the league grid keeps on screen.)
 *
 * Kept pure (no React) so the rules are unit-testable, matching
 * `views/calendar/gameSideFollows.ts` — the repo has no component-test
 * harness, and these rules decide what a save unfollows.
 */

import type {
  CatalogLeague,
  CatalogTeam,
  CurrentSelectionsResponse,
  FollowRequest,
  FollowSelection,
} from "../../types";

export interface SelectionState {
  // Leagues being followed, in the order the Teams and Review steps list
  // them and the save submits them.
  selectedLeagueIds: string[];
  // Every league selected at some point in this run, first-selected first —
  // always a superset of `selectedLeagueIds`. A deselected league keeps its
  // place here, so selecting it again puts it back where it was rather than
  // at the end.
  leagueOrder: string[];
  // Leagues flagged whole-competition (follow_all:true, no team picking).
  // The flag is remembered while its league is deselected.
  followAllLeagueIds: string[];
  // Team picks per league, remembered while the league is deselected or in
  // whole-league mode.
  teamsByLeague: Record<string, CatalogTeam[]>;
}

/** What a first run starts from. */
export const EMPTY_SELECTION: SelectionState = {
  selectedLeagueIds: [],
  leagueOrder: [],
  followAllLeagueIds: [],
  teamsByLeague: {},
};

/** What the wizard opens on: a selection plus where to open it. */
export interface WizardSeed extends SelectionState {
  step: "countries" | "leagues";
  selectedCountryCodes: string[];
}

/** One league as the state follows it right now. */
export interface LeagueFollow {
  leagueId: string;
  followAll: boolean;
  // The picks; empty for a whole-league follow.
  teams: CatalogTeam[];
}

/** One seeded league that a save would unfollow, in whole or in part. */
export interface RemovedFollow {
  leagueId: string;
  // The league leaves the followed set altogether, taking what is stored
  // for the league itself (standings, league news) with it.
  leagueRemoved: boolean;
  // It was followed in whole and no longer is: dropped, or narrowed to
  // picked teams.
  followAllRemoved: boolean;
  // Seeded picks no longer followed as teams: unpicked, or their league was
  // dropped or switched to whole-league (which follows the games, not the
  // teams).
  teams: CatalogTeam[];
}

// --- The effective view ----------------------------------------------------

/** True when the league is followed in whole: selected AND flagged. */
export function isFollowAll(state: SelectionState, leagueId: string): boolean {
  return (
    state.selectedLeagueIds.includes(leagueId) &&
    state.followAllLeagueIds.includes(leagueId)
  );
}

/** The leagues followed in whole — not a deselected one's remembered flag. */
export function followAllIds(state: SelectionState): string[] {
  return state.selectedLeagueIds.filter((id) => isFollowAll(state, id));
}

/** Leagues that still need a team-picking grid (selected, not follow-all). */
export function pickTeamLeagueIds(state: SelectionState): string[] {
  return state.selectedLeagueIds.filter((id) => !isFollowAll(state, id));
}

/** Picks that count: those of the pick-teams leagues, and no others. */
export function totalSelected(state: SelectionState): number {
  return pickTeamLeagueIds(state).reduce(
    (sum, id) => sum + (state.teamsByLeague[id] ?? []).length,
    0,
  );
}

/**
 * What the state follows: each selected league, in whole or by its picks.
 * A pick-teams league with no picks follows nothing and is left out — the
 * backend rejects a selection that is neither.
 */
export function followedLeagues(state: SelectionState): LeagueFollow[] {
  return state.selectedLeagueIds
    .map((leagueId): LeagueFollow => {
      const followAll = isFollowAll(state, leagueId);
      return {
        leagueId,
        followAll,
        teams: followAll ? [] : (state.teamsByLeague[leagueId] ?? []),
      };
    })
    .filter((follow) => follow.followAll || follow.teams.length > 0);
}

/** The body of `POST /setup/follow` for the state as it stands. */
export function toFollowRequest(state: SelectionState): FollowRequest {
  return {
    selections: followedLeagues(state).map(
      (follow): FollowSelection =>
        follow.followAll
          ? {
              league_id: follow.leagueId,
              team_provider_keys: [],
              follow_all: true,
            }
          : {
              league_id: follow.leagueId,
              team_provider_keys: follow.teams.map((team) => team.provider_key),
            },
    ),
  };
}

// --- The league grid -------------------------------------------------------

/**
 * Every league this run has had selected, whether or not it still is.
 * Remembered, not followed: only `offeredLeagues` may read it, to decide
 * which cards stay on screen.
 */
export function rememberedLeagueIds(state: SelectionState): string[] {
  // `leagueOrder` is meant to hold them all; the selected ids are folded in
  // for the reason `withLeagueSelected` gives.
  return [...new Set([...state.leagueOrder, ...state.selectedLeagueIds])];
}

/**
 * The leagues the grid offers once the country step has narrowed it: those
 * of the chosen countries, those with no country of their own, and every
 * remembered one wherever it is from. No chosen country means no narrowing.
 *
 * Remembered rather than merely selected, because the card is the only undo
 * a deselect has. In manage mode the editor opens with no country chosen,
 * so every followed league outside the countries picked later is on the
 * grid only by this rule; were it "selected", one stray click would take the
 * card away with it — and the league, its teams and their stored data with
 * the save — leaving nothing to click to put it back.
 */
export function offeredLeagues(
  leagues: CatalogLeague[],
  countryCodes: string[],
  rememberedIds: string[],
): CatalogLeague[] {
  if (countryCodes.length === 0) return leagues;
  const chosen = new Set(countryCodes);
  const remembered = new Set(rememberedIds);
  return leagues.filter(
    (league) =>
      league.country_code === null ||
      chosen.has(league.country_code) ||
      remembered.has(league.id),
  );
}

// --- Transitions -----------------------------------------------------------

function withLeagueSelected(
  state: SelectionState,
  leagueId: string,
  selected: boolean,
): SelectionState {
  // Every league with a place to keep, new ones last. The selected ids are
  // folded in so that one `leagueOrder` has lost track of is carried along
  // instead of being filtered out below — the failure this module exists to
  // rule out is a league dropped by something other than the user.
  const leagueOrder = [
    ...new Set([...state.leagueOrder, ...state.selectedLeagueIds, leagueId]),
  ];
  return {
    ...state,
    leagueOrder,
    selectedLeagueIds: leagueOrder.filter((id) =>
      id === leagueId ? selected : state.selectedLeagueIds.includes(id),
    ),
  };
}

/**
 * Select or deselect a league. Deselecting forgets nothing — the league's
 * picks, its whole-league flag and its place in the order all wait for it —
 * so toggling it back on restores exactly the follow it had.
 */
export function toggleLeague(
  state: SelectionState,
  leagueId: string,
): SelectionState {
  return withLeagueSelected(
    state,
    leagueId,
    !state.selectedLeagueIds.includes(leagueId),
  );
}

/**
 * Whole-competition toggle on a chip. Marking follow-all selects the league
 * if needed and sets its picks aside; unmarking leaves it selected in
 * pick-teams mode with those picks back in force.
 *
 * Unmarking deliberately does not deselect, so one sequence is not a clean
 * round trip: marking then unmarking a DESELECTED league that still
 * remembers picks leaves it selected with them. That errs towards following
 * and never towards deleting; closing it would mean either forgetting the
 * picks or deselecting on unmark, which the chip has never done.
 */
export function toggleFollowAll(
  state: SelectionState,
  leagueId: string,
): SelectionState {
  if (isFollowAll(state, leagueId)) {
    return {
      ...state,
      followAllLeagueIds: state.followAllLeagueIds.filter(
        (id) => id !== leagueId,
      ),
    };
  }
  const selected = withLeagueSelected(state, leagueId, true);
  return {
    ...selected,
    // A deselected league may still carry the flag from before.
    followAllLeagueIds: selected.followAllLeagueIds.includes(leagueId)
      ? selected.followAllLeagueIds
      : [...selected.followAllLeagueIds, leagueId],
  };
}

/** Pick or unpick one team, matched by provider key. */
export function toggleTeam(
  state: SelectionState,
  leagueId: string,
  team: CatalogTeam,
): SelectionState {
  const current = state.teamsByLeague[leagueId] ?? [];
  const exists = current.some(
    (chosen) => chosen.provider_key === team.provider_key,
  );
  return {
    ...state,
    teamsByLeague: {
      ...state.teamsByLeague,
      [leagueId]: exists
        ? current.filter((chosen) => chosen.provider_key !== team.provider_key)
        : [...current, team],
    },
  };
}

/**
 * "Clear" on a league's grid: the one transition that does forget, because
 * forgetting is what was asked for.
 */
export function clearLeagueTeams(
  state: SelectionState,
  leagueId: string,
): SelectionState {
  return {
    ...state,
    teamsByLeague: { ...state.teamsByLeague, [leagueId]: [] },
  };
}

// --- Manage mode -----------------------------------------------------------

/**
 * The followed set (`GET /setup/selection`) as the state the wizard opens
 * on, such that saving it untouched posts the same set back.
 */
export function seedFromSelection(
  response: CurrentSelectionsResponse,
): WizardSeed {
  const selectedLeagueIds = response.selections.map(
    (selection) => selection.league_id,
  );
  const teamsByLeague: Record<string, CatalogTeam[]> = {};
  for (const selection of response.selections) {
    if (!selection.follow_all) {
      teamsByLeague[selection.league_id] = selection.teams;
    }
  }
  return {
    // Open on the follows when there are any to edit; with none there is
    // nothing to show yet, so start where a first run does.
    step: selectedLeagueIds.length > 0 ? "leagues" : "countries",
    // No country narrowing, rather than one derived from the follows: empty
    // shows every league, where a derived filter would open the editor with
    // every other country's leagues hidden.
    selectedCountryCodes: [],
    selectedLeagueIds,
    leagueOrder: selectedLeagueIds,
    followAllLeagueIds: response.selections
      .filter((selection) => selection.follow_all)
      .map((selection) => selection.league_id),
    teamsByLeague,
  };
}

/**
 * What saving `state` would unfollow, compared with the set the wizard
 * opened on — so a destructive save can say so before it is confirmed.
 * Empty when everything seeded is still followed the way it was.
 */
export function removedFromSeed(
  seed: SelectionState,
  state: SelectionState,
): RemovedFollow[] {
  const now = new Map(
    followedLeagues(state).map((follow) => [follow.leagueId, follow]),
  );
  const removed: RemovedFollow[] = [];
  for (const before of followedLeagues(seed)) {
    const after = now.get(before.leagueId);
    const followAllRemoved = before.followAll && after?.followAll !== true;
    // A whole-league follow has no teams, so a league switched to one keeps
    // none of its picks — the same as a league that is gone.
    const kept = new Set(
      (after?.teams ?? []).map((team) => team.provider_key),
    );
    const teams = before.teams.filter((team) => !kept.has(team.provider_key));
    if (after === undefined || followAllRemoved || teams.length > 0) {
      removed.push({
        leagueId: before.leagueId,
        leagueRemoved: after === undefined,
        followAllRemoved,
        teams,
      });
    }
  }
  return removed;
}

/**
 * The cards a league's grid shows: the live catalog in its own order, then
 * any seeded or selected team the catalog no longer lists.
 *
 * The catalog is not a fixed roster — the golf one is the current
 * tournament's field, and the tennis and MMA ones are capped rankings a
 * followed athlete slides out of — so a pick missing from it is routine. A
 * pick with no card could be counted but neither seen nor unpicked.
 * `seeded` keeps the stored picks on screen even once they are unpicked or
 * cleared, which is the only way back to a follow the catalog has dropped.
 */
export function mergeSelectedTeams(
  catalog: CatalogTeam[],
  seeded: CatalogTeam[],
  selected: CatalogTeam[],
): CatalogTeam[] {
  const merged = [...catalog];
  const listed = new Set(catalog.map((team) => team.provider_key));
  for (const team of [...seeded, ...selected]) {
    if (!listed.has(team.provider_key)) {
      listed.add(team.provider_key);
      merged.push(team);
    }
  }
  return merged;
}
