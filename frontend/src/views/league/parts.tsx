import { createContext, useContext, useMemo, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useSetupTeams, useStandings, useTeams } from "../../hooks";
import type { League } from "../../types";
import { hasCatalog, teamLookup, type TeamLookup } from "./model";

// ── The controls row ────────────────────────────────────────────────────

/**
 * The element beside the sub-view switch, on the same row. The switch
 * belongs to the page and must not remount when the sub-view changes (the
 * arrow keys move focus along it), but what sits beside it belongs to the
 * sub-view: the conference toggle and the legend are Standings' own. So
 * the page owns the row and each sub-view renders into it.
 */
export const ControlsSlotContext = createContext<HTMLElement | null>(null);

/** Renders its children beside the sub-view switch. */
export function Controls({ children }: { children: ReactNode }) {
  const slot = useContext(ControlsSlotContext);
  return slot === null ? null : createPortal(children, slot);
}

// ── Team identity ───────────────────────────────────────────────────────

/**
 * Every team in a league by name or code, for the colour blocks. A
 * standings row for a team that is not followed has no colour, and a
 * leaders row for one has no code, so three payloads are joined: the
 * league's standings, the followed teams, and the setup catalog (the list
 * the follow wizard is built from, which carries every team's colour).
 *
 * The lookup is rebuilt whenever one of the three queries brings new data.
 */
export function useTeamLookup(league: League | undefined): TeamLookup {
  const teams = useTeams().data?.teams;
  const standings = useStandings(league?.id).data;
  const catalog = useSetupTeams(
    league !== undefined && hasCatalog(league) ? league.id : undefined,
  ).data;
  return useMemo(
    () =>
      teamLookup([
        (standings?.rows ?? []).map((row) => ({
          name: row.team_name,
          abbreviation: row.abbreviation,
          color: row.color,
        })),
        (teams ?? []).filter((team) => team.league_id === league?.id),
        catalog?.teams ?? [],
      ]),
    [standings, teams, catalog, league?.id],
  );
}
