/**
 * TanStack Query hooks — thin, typed wrappers over the API client with
 * stable query keys. Views consume these exclusively; no component
 * touches fetch/api directly.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { api } from "./api";
import { localDateKey } from "./lib/time";
import type {
  Bracket,
  CatalogLeaguesResponse,
  CatalogTeamsResponse,
  Game,
  GameDetail,
  GameOdds,
  MapResponse,
  Matchup,
  Meta,
  NewsItem,
  NewsRefreshResult,
  NewsScope,
  NotificationPrefsResponse,
  Roster,
  Nation,
  Scorers,
  SetupStatus,
  SportEvent,
  Standings,
  StatLeaders,
  TeamsResponse,
  TodayResponse,
  Weather,
} from "./types";

const FIVE_MINUTES = 300_000;

/**
 * Adaptive polling for the Today view:
 * - any game in progress         -> 30s
 * - any game scheduled for the
 *   local "today"                -> 60s
 * - otherwise                    -> 5min
 */
function todayRefetchInterval(data: TodayResponse | undefined): number {
  if (data === undefined) {
    return FIVE_MINUTES;
  }
  if (data.games.some((game) => game.phase === "in_progress")) {
    return 30_000;
  }
  const todayKey = localDateKey(new Date().toISOString());
  const hasScheduledToday = data.games.some(
    (game) =>
      game.phase === "scheduled" && localDateKey(game.start_time) === todayKey,
  );
  if (hasScheduledToday) {
    return 60_000;
  }
  return FIVE_MINUTES;
}

export function useMeta(): UseQueryResult<Meta> {
  return useQuery({
    queryKey: ["meta"],
    queryFn: () => api.meta(),
    staleTime: Infinity,
  });
}

export function useTeams(): UseQueryResult<TeamsResponse> {
  return useQuery({
    queryKey: ["teams"],
    queryFn: () => api.teams(),
    staleTime: Infinity,
  });
}

export function useToday(): UseQueryResult<TodayResponse> {
  return useQuery({
    queryKey: ["today"],
    queryFn: () => api.today(),
    refetchInterval: (query) => todayRefetchInterval(query.state.data),
  });
}

/**
 * Stadium locations for the Map view. Followed teams' coordinates are
 * resolved by the daily backend job and change rarely. Active-competition
 * teams (`source === "competition"`), however, resolve progressively: each
 * `/api/map` call geocodes only a bounded slice and caches it, so unresolved
 * nations fill in on later calls (and the background job warms the cache).
 * When a competition is present we therefore poll on a short interval so the
 * full field (e.g. 48 World Cup nations) populates without a manual reload;
 * with only followed teams the payload is effectively static (no polling).
 *
 * `days` is the upcoming-games window (1–30, the map's slider) and is part of
 * the query key so changing it refetches. Polling also kicks in while any
 * upcoming game is present, since a game at an unlocated venue resolves its
 * coordinates on a later poll (the backend geocodes it in the background).
 */
export function useMap(days?: number): UseQueryResult<MapResponse> {
  return useQuery({
    queryKey: ["map", days ?? null],
    queryFn: () => api.map(days),
    staleTime: 30_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasCompetition = data?.teams.some(
        (team) => team.source === "competition",
      );
      const hasGames = (data?.games.length ?? 0) > 0;
      return hasCompetition || hasGames ? 30_000 : false;
    },
  });
}

export function useSchedule(
  start: string,
  end: string,
  teamId?: string,
): UseQueryResult<Game[]> {
  return useQuery({
    queryKey: ["schedule", start, end, teamId ?? null],
    queryFn: () => api.schedule({ start, end, teamId }),
  });
}

/**
 * Venue forecasts for a set of games (the calendar's outdoor scheduled
 * games), as a { gameId -> Weather } map. Disabled when there are no ids.
 * The backend reuses the same per-game weather logic as the detail modal
 * and the upstream service caches ~45min, so this is cheap to refetch; the
 * sorted-id key keeps the cache stable as the visible range shifts.
 */
export function useScheduleWeather(
  ids: string[],
): UseQueryResult<Record<string, Weather>> {
  return useQuery({
    queryKey: ["schedule-weather", [...ids].sort().join(",")],
    queryFn: () => api.scheduleWeather(ids),
    enabled: ids.length > 0,
    staleTime: 30 * 60_000,
  });
}

/**
 * Betting lines + win-probability for a set of games, keyed by game id.
 * Mirrors `useScheduleWeather`: the caller passes the visible (scheduled /
 * live) game ids and threads the resulting map into the cards. Lines drift
 * slowly and the backend caches them ~10 min, so a 5-minute stale window
 * keeps cards quiet without going stale.
 */
export function useGameOdds(
  ids: string[],
): UseQueryResult<Record<string, GameOdds>> {
  return useQuery({
    queryKey: ["game-odds", [...ids].sort().join(",")],
    queryFn: () => api.odds(ids),
    enabled: ids.length > 0,
    staleTime: 5 * 60_000,
  });
}

/**
 * Local calendar day, `daysOffset` from today, as "YYYY-MM-DD". Used to
 * build the default events window (today-7d .. today+45d) the way the
 * backend's /events route expects local days.
 */
export function localDayOffset(daysOffset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysOffset);
  const year = String(d.getFullYear()).padStart(4, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Leaderboard events (golf, …). Defaults to the same window as the backend
 * (today-7d .. today+45d) when start/end are omitted, so the Golf tab can
 * just call `useEvents()`. Polls faster while a tournament is in progress.
 */
export function useEvents(
  start?: string,
  end?: string,
): UseQueryResult<SportEvent[]> {
  const startKey = start ?? localDayOffset(-7);
  const endKey = end ?? localDayOffset(45);
  return useQuery({
    queryKey: ["events", startKey, endKey],
    queryFn: () => api.events({ start: startKey, end: endKey }),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.some((event) => event.phase === "in_progress")) {
        return 60_000;
      }
      return FIVE_MINUTES;
    },
  });
}

/**
 * On-demand box-score drill-down for a single game. Disabled until an id is
 * provided (the modal opens with a game id), so closing the modal stops the
 * polling. While the game is in progress the line score / performers keep
 * refreshing every 30s; once it is final the summary never changes, so we
 * treat it as permanently fresh and stop refetching.
 */
export function useGameDetail(
  gameId: string | undefined,
): UseQueryResult<GameDetail> {
  return useQuery({
    queryKey: ["game-detail", gameId ?? null],
    queryFn: () => {
      if (gameId === undefined) {
        throw new Error("useGameDetail: gameId is required");
      }
      return api.gameDetail(gameId);
    },
    enabled: !!gameId,
    refetchInterval: (query) =>
      query.state.data?.game.phase === "in_progress" ? 30_000 : false,
    staleTime: (query) =>
      query.state.data?.game.phase === "final" ? Infinity : 30_000,
  });
}

/**
 * Assembled pre-game preview (odds/win-prob, form, head-to-head, lineups,
 * weather, injuries) for one game. Disabled until an id is given. Refreshes
 * while the game is scheduled (lines/lineups move); a final game is static.
 */
export function useMatchup(
  gameId: string | undefined,
): UseQueryResult<Matchup> {
  return useQuery({
    queryKey: ["matchup", gameId ?? null],
    queryFn: () => {
      if (gameId === undefined) {
        throw new Error("useMatchup: gameId is required");
      }
      return api.matchup(gameId);
    },
    enabled: !!gameId,
    staleTime: 5 * 60_000,
  });
}

export function useStandings(
  leagueId: string | undefined,
): UseQueryResult<Standings> {
  return useQuery({
    queryKey: ["standings", leagueId ?? null],
    queryFn: () => {
      if (leagueId === undefined) {
        throw new Error("useStandings: leagueId is required");
      }
      return api.standings(leagueId);
    },
    enabled: !!leagueId,
  });
}

export function useLeaders(
  leagueId: string | undefined,
): UseQueryResult<StatLeaders> {
  return useQuery({
    queryKey: ["leaders", leagueId ?? null],
    queryFn: () => {
      if (leagueId === undefined) {
        throw new Error("useLeaders: leagueId is required");
      }
      return api.leaders(leagueId);
    },
    enabled: !!leagueId,
  });
}

export function useScorers(
  leagueId: string | undefined,
): UseQueryResult<Scorers> {
  return useQuery({
    queryKey: ["scorers", leagueId ?? null],
    queryFn: () => {
      if (leagueId === undefined) {
        throw new Error("useScorers: leagueId is required");
      }
      return api.scorers(leagueId);
    },
    enabled: !!leagueId,
  });
}

export function useBracket(
  leagueId: string | undefined,
): UseQueryResult<Bracket> {
  return useQuery({
    queryKey: ["bracket", leagueId ?? null],
    queryFn: () => {
      if (leagueId === undefined) {
        throw new Error("useBracket: leagueId is required");
      }
      return api.bracket(leagueId);
    },
    enabled: !!leagueId,
  });
}

export function useNation(
  leagueId: string | null,
  name: string | null,
): UseQueryResult<Nation> {
  return useQuery({
    queryKey: ["nation", leagueId ?? null, name ?? null],
    queryFn: () => {
      if (leagueId === null || name === null) {
        throw new Error("useNation: leagueId and name are required");
      }
      return api.nation(leagueId, name);
    },
    enabled: leagueId !== null && name !== null,
  });
}

export function useRoster(teamId: string | undefined): UseQueryResult<Roster> {
  return useQuery({
    queryKey: ["roster", teamId ?? null],
    queryFn: () => {
      if (teamId === undefined) {
        throw new Error("useRoster: teamId is required");
      }
      return api.roster(teamId);
    },
    enabled: !!teamId,
  });
}

export function useResults(
  teamId: string | undefined,
  limit?: number,
): UseQueryResult<Game[]> {
  return useQuery({
    queryKey: ["results", teamId ?? null, limit ?? null],
    queryFn: () => {
      if (teamId === undefined) {
        throw new Error("useResults: teamId is required");
      }
      return api.results(teamId, limit);
    },
    enabled: !!teamId,
  });
}

export function useNews(scope?: NewsScope): UseQueryResult<NewsItem[]> {
  return useQuery({
    queryKey: ["news", scope?.teamId ?? null, scope?.leagueId ?? null],
    queryFn: () => api.news(scope),
    refetchInterval: FIVE_MINUTES,
  });
}

/**
 * Manual on-demand news refresh (the "Refresh" button). Triggers a
 * server-side fetch of the latest articles for the given team or competition
 * (or all followed teams + competitions when omitted), then invalidates every
 * cached news query so the list reloads with whatever was just stored.
 */
export function useRefreshNews(
  scope?: NewsScope,
): UseMutationResult<NewsRefreshResult, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.refreshNews(scope),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["news"] });
    },
  });
}

/**
 * Onboarding gate. staleTime 0 so re-checks after setup mutations always
 * hit the backend instead of trusting a cached "not onboarded".
 */
export function useSetupStatus(): UseQueryResult<SetupStatus> {
  return useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
    staleTime: 0,
  });
}

export function useSetupLeagues(): UseQueryResult<CatalogLeaguesResponse> {
  return useQuery({
    queryKey: ["setup-leagues"],
    queryFn: () => api.setupLeagues(),
    staleTime: Infinity, // static catalog — never changes within a session
  });
}

export function useSetupTeams(
  leagueId: string | undefined,
): UseQueryResult<CatalogTeamsResponse> {
  return useQuery({
    queryKey: ["setup-teams", leagueId ?? null],
    queryFn: () => {
      if (leagueId === undefined) {
        throw new Error("useSetupTeams: leagueId is required");
      }
      return api.setupTeams(leagueId);
    },
    enabled: !!leagueId,
    staleTime: FIVE_MINUTES, // backend caches the ESPN catalog for 1h
  });
}

export function useNotificationPrefs(): UseQueryResult<NotificationPrefsResponse> {
  return useQuery({
    queryKey: ["notification-prefs"],
    queryFn: () => api.notificationPrefs(),
    staleTime: FIVE_MINUTES,
  });
}
