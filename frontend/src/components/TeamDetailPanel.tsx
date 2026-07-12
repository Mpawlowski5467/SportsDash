import { createContext, useCallback, useContext, useMemo, useState } from "react";

import {
  useMap,
  useNation,
  useNews,
  useResults,
  useRoster,
  useSchedule,
  useTeams,
} from "../hooks";
import { localDayOffset } from "../lib/time";
import type { Game } from "../types";
import TeamProfileView, {
  type Outcome,
  type TeamProfile,
} from "./TeamProfileView";

/**
 * App-wide "open this team / nation" handles so any view (standings rows, the
 * map panel, …) can launch the unified team profile without threading state
 * down. `TeamDetailProvider` owns the selection and mounts the matching
 * loader (a followed team by id, or a competition nation by name); both build
 * a normalized `TeamProfile` and render the same `TeamProfileView`.
 */
const OpenTeamContext = createContext<(teamId: string) => void>(() => {});
const OpenNationContext = createContext<
  (leagueId: string, name: string) => void
>(() => {});
const CloseTeamDetailContext = createContext<() => void>(() => {});

export function useOpenTeam(): (teamId: string) => void {
  return useContext(OpenTeamContext);
}

export function useOpenNation(): (leagueId: string, name: string) => void {
  return useContext(OpenNationContext);
}

/** Programmatically dismiss whatever team/nation page is open (used by the
 *  map's auto-tour to close one team's page before flying to the next). */
export function useCloseTeamDetail(): () => void {
  return useContext(CloseTeamDetailContext);
}

export function TeamDetailProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [teamId, setTeamId] = useState<string | null>(null);
  const [nation, setNation] = useState<{
    leagueId: string;
    name: string;
  } | null>(null);
  // Stable so consumers (the map tour) can hold it across renders / in effects.
  const openNationCb = useCallback(
    (leagueId: string, name: string) => setNation({ leagueId, name }),
    [],
  );
  const close = useCallback(() => {
    setTeamId(null);
    setNation(null);
  }, []);
  return (
    <OpenTeamContext.Provider value={setTeamId}>
      <OpenNationContext.Provider value={openNationCb}>
        <CloseTeamDetailContext.Provider value={close}>
          {children}
          {teamId !== null && (
            <FollowedTeamLoader teamId={teamId} onClose={close} />
          )}
          {nation !== null && (
            <NationLoader
              leagueId={nation.leagueId}
              name={nation.name}
              onClose={close}
            />
          )}
        </CloseTeamDetailContext.Provider>
      </OpenNationContext.Provider>
    </OpenTeamContext.Provider>
  );
}

/** Win/loss/draw for `self` in a game; null when undecided. */
function outcomeOf(game: Game, self: string): Outcome | null {
  const homeMine = game.home.name === self;
  const mine = homeMine ? game.home : game.away;
  const theirs = homeMine ? game.away : game.home;
  if (mine.score === null || theirs.score === null) return null;
  if (mine.score > theirs.score) return "W";
  if (mine.score < theirs.score) return "L";
  return "D";
}

/** Recent form (newest-first, up to 5) + a W-D-L record from results. */
function formAndRecord(
  results: Game[],
  self: string,
): { form: Outcome[]; record: string | null } {
  const outcomes = results
    .map((g) => outcomeOf(g, self))
    .filter((o): o is Outcome => o !== null);
  if (outcomes.length === 0) return { form: [], record: null };
  const w = outcomes.filter((o) => o === "W").length;
  const d = outcomes.filter((o) => o === "D").length;
  const l = outcomes.filter((o) => o === "L").length;
  return { form: outcomes.slice(0, 5), record: `${w}-${d}-${l}` };
}

// --- Followed team: full dashboard from the per-team endpoints --------------

function FollowedTeamLoader({
  teamId,
  onClose,
}: {
  teamId: string;
  onClose: () => void;
}) {
  const teamsQuery = useTeams();
  const mapQuery = useMap();
  const scheduleQuery = useSchedule(localDayOffset(-1), localDayOffset(45), teamId);
  const resultsQuery = useResults(teamId, 12);
  const rosterQuery = useRoster(teamId);
  const newsQuery = useNews({ teamId });

  const team = teamsQuery.data?.teams.find((t) => t.id === teamId);
  const leagueName =
    teamsQuery.data?.leagues.find((l) => l.id === team?.league_id)?.name ?? "";
  const stadiumRow = mapQuery.data?.teams.find((t) => t.team_id === teamId);

  const profile: TeamProfile | null = useMemo(() => {
    if (!team) return null;
    const fixtures = (scheduleQuery.data ?? [])
      .filter((g) => g.phase === "scheduled" || g.phase === "in_progress")
      .slice(0, 6);
    const results = (resultsQuery.data ?? []).slice(0, 8);
    const { form, record } = formAndRecord(results, team.name);
    return {
      name: team.name,
      abbreviation: team.abbreviation,
      logoUrl: team.logo_url,
      color: team.color,
      subtitle: leagueName,
      rank: null,
      record,
      points: null,
      form,
      nextMatch: fixtures[0] ?? null,
      fixtures,
      results,
      roster: rosterQuery.data?.players ?? [],
      news: (newsQuery.data ?? []).slice(0, 6),
      stadium: stadiumRow
        ? {
            venue: stadiumRow.venue,
            location: stadiumRow.location,
            capacity: stadiumRow.capacity,
            imageUrl: stadiumRow.image_url,
          }
        : null,
      description: stadiumRow?.description ?? null,
      founded: stadiumRow?.founded_year ?? null,
    };
  }, [team, leagueName, scheduleQuery.data, resultsQuery.data, rosterQuery.data, newsQuery.data, stadiumRow]);

  return (
    <TeamProfileView
      profile={profile}
      isLoading={!team && teamsQuery.isLoading}
      isError={teamsQuery.isError && !team}
      onClose={onClose}
    />
  );
}

// --- Competition nation: by-name profile from the /nation endpoint ----------

function NationLoader({
  leagueId,
  name,
  onClose,
}: {
  leagueId: string;
  name: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError } = useNation(leagueId, name);
  const mapQuery = useMap();

  // This nation is plotted on the map, so the stadium cache already knows its
  // home ground — match it by league + name (case-insensitive) and fold the
  // venue/photo/capacity into the profile. That guarantees a competition team
  // always shows SOMETHING (its crest + home stadium) even out of season when
  // it has no synced games, and works whether the page was opened from the
  // map or a standings row.
  const target = name.trim().toLowerCase();
  const mapRow = mapQuery.data?.teams.find(
    (t) =>
      t.source === "competition" &&
      t.league_id === leagueId &&
      t.name.trim().toLowerCase() === target,
  );
  // The pin is the team's HOME ground only when it isn't a next-match host
  // placement: during an active tournament a nation is plotted at the match
  // venue (e.g. a World Cup team at Levi's Stadium), which isn't its stadium —
  // those teams have fixtures/results to fill the page instead.
  const homeGround = mapRow && !mapRow.next_opponent ? mapRow : null;

  const profile: TeamProfile | null = useMemo(() => {
    // Prefer the live /nation dashboard; fall back to the map row so a plotted
    // team still gets a page even if /nation has nothing for it.
    if (!data && !mapRow) return null;
    const standing = data?.standing ?? null;
    const record =
      standing !== null
        ? `${standing.wins}-${standing.draws ?? 0}-${standing.losses}`
        : null;
    const { form } = data ? formAndRecord(data.results, data.name) : { form: [] };
    const fixtures = data?.fixtures ?? [];
    const results = data?.results ?? [];
    return {
      name: data?.name ?? mapRow?.name ?? name,
      abbreviation: data?.abbreviation ?? mapRow?.abbreviation ?? null,
      logoUrl: data?.logo_url ?? mapRow?.logo_url ?? null,
      color: data?.color ?? mapRow?.color ?? null,
      subtitle:
        data?.group ?? data?.league_name ?? mapRow?.group ?? mapRow?.league_name ?? "",
      rank: standing?.rank ?? null,
      record,
      points: standing?.points ?? null,
      form,
      nextMatch: fixtures[0] ?? null,
      fixtures: fixtures.slice(0, 6),
      results: results.slice(0, 8),
      roster: [],
      news: [],
      stadium: homeGround
        ? {
            venue: homeGround.venue,
            location: homeGround.location,
            capacity: homeGround.capacity,
            imageUrl: homeGround.image_url,
          }
        : null,
      description: homeGround?.description ?? null,
      founded: homeGround?.founded_year ?? null,
    };
  }, [data, mapRow, homeGround, name]);

  return (
    <TeamProfileView
      profile={profile}
      // Only block on /nation while we have nothing at all to show; a known
      // map row lets the page render immediately and enrich when data lands.
      isLoading={isLoading && !mapRow && !data}
      isError={isError && !mapRow}
      onClose={onClose}
    />
  );
}
