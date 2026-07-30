/**
 * Typed fetch wrapper for the SportsDash REST API.
 *
 * Base URL comes from VITE_API_BASE (e.g. "http://localhost:8000/api"),
 * defaulting to "/api" so the Vite dev proxy / Bun runtime proxy handle
 * routing in both dev and production.
 */

import type {
  Bracket,
  CatalogLeaguesResponse,
  CatalogTeamsResponse,
  FollowRequest,
  Game,
  GameDetail,
  GameOdds,
  MapResponse,
  Matchup,
  Meta,
  NewsItem,
  NewsScope,
  NotificationPrefsResponse,
  NotificationPrefUpdate,
  Nation,
  NewsRefreshResult,
  OnThisDay,
  PlayerFollow,
  PlayerFollowIn,
  Roster,
  Scorers,
  SetupStatus,
  SportEvent,
  Standings,
  StatLeaders,
  TeamsResponse,
  TodayResponse,
  Weather,
} from "./types";

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, body: string) {
    super(body || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function get<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  let url = API_BASE + path;
  if (params) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        search.set(key, String(value));
      }
    }
    const query = search.toString();
    if (query) {
      url += `?${query}`;
    }
  }
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body);
  }
  return (await res.json()) as T;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method: "POST", headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
  return (await res.json()) as T;
}

async function put<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method: "PUT", headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
  return (await res.json()) as T;
}

/** DELETE returning no body (the backend answers 204 No Content). */
async function del(path: string): Promise<void> {
  const res = await fetch(API_BASE + path, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
}

export const api = {
  teams: () => get<TeamsResponse>("/teams"),

  /** App version, poll cadence, and the backend's configured timezone. */
  meta: () => get<Meta>("/meta"),

  today: () => get<TodayResponse>("/today"),

  map: (days?: number) => get<MapResponse>("/map", { days }),

  schedule: (p: { start: string; end: string; teamId?: string }) =>
    p.teamId !== undefined
      ? get<Game[]>(`/schedule/${encodeURIComponent(p.teamId)}`, {
          start: p.start,
          end: p.end,
        })
      : get<Game[]>("/schedule", { start: p.start, end: p.end }),

  // Leaderboard competitions (golf tournaments — golf is the only sport
  // modeled as an Event) OVERLAPPING the local-day range: unlike /schedule
  // these span days, so a tournament that started before `start` is returned
  // while it is still running.
  events: (p: { start: string; end: string }) =>
    get<SportEvent[]>("/events", { start: p.start, end: p.end }),

  gameDetail: (id: string) =>
    get<GameDetail>(`/games/${encodeURIComponent(id)}`),

  matchup: (id: string) =>
    get<Matchup>(`/matchup/${encodeURIComponent(id)}`),

  // Venue forecasts for the given games (outdoor scheduled games only); the
  // response maps a game id to its forecast, omitting games without weather.
  scheduleWeather: (ids: string[]) =>
    get<Record<string, Weather>>("/schedule/weather", { ids: ids.join(",") }),

  // Betting lines + win-probability for the given games (scheduled / live,
  // non-individual only); maps a game id to its odds, omitting games the
  // provider has no line for.
  odds: (ids: string[]) =>
    get<Record<string, GameOdds>>("/odds", { ids: ids.join(",") }),

  standings: (leagueId: string, season?: number) =>
    get<Standings>(`/standings/${encodeURIComponent(leagueId)}`, { season }),

  // A followed team's FINAL games from one past season (fetched from the
  // provider on demand; espn team sports only).
  historyResults: (teamId: string, season: number) =>
    get<Game[]>(`/history/results/${encodeURIComponent(teamId)}`, { season }),

  /** Followed teams' past games on today's month-day (empty = no anniversaries). */
  onThisDay: () => get<OnThisDay>("/history/on-this-day"),

  leaders: (leagueId: string) =>
    get<StatLeaders>(`/leaders/${encodeURIComponent(leagueId)}`),

  scorers: (leagueId: string) =>
    get<Scorers>(`/scorers/${encodeURIComponent(leagueId)}`),

  bracket: (leagueId: string) =>
    get<Bracket>(`/bracket/${encodeURIComponent(leagueId)}`),

  nation: (leagueId: string, name: string) =>
    get<Nation>(
      `/nation/${encodeURIComponent(leagueId)}/${encodeURIComponent(name)}`,
    ),

  roster: (teamId: string) =>
    get<Roster>(`/roster/${encodeURIComponent(teamId)}`),

  results: (teamId: string, limit?: number) =>
    get<Game[]>(`/results/${encodeURIComponent(teamId)}`, { limit }),

  // Scoped to a followed team (teamId) or a whole-competition follow
  // (leagueId); with neither, every followed team's + competition's news.
  news: (scope?: NewsScope, limit?: number) =>
    get<NewsItem[]>("/news", {
      team_id: scope?.teamId,
      league_id: scope?.leagueId,
      limit,
    }),

  // Pull fresh articles on demand (server fetches provider + RSS + Google
  // News). Scoped like `news` above.
  refreshNews: (scope?: NewsScope) => {
    const params = new URLSearchParams();
    if (scope?.teamId) params.set("team_id", scope.teamId);
    else if (scope?.leagueId) params.set("league_id", scope.leagueId);
    const qs = params.toString();
    return post<NewsRefreshResult>(`/news/refresh${qs ? `?${qs}` : ""}`);
  },

  setupStatus: () => get<SetupStatus>("/setup/status"),

  setupLeagues: () => get<CatalogLeaguesResponse>("/setup/leagues"),

  setupTeams: (leagueId: string) =>
    get<CatalogTeamsResponse>(`/setup/teams/${encodeURIComponent(leagueId)}`),

  setupFollow: (body: FollowRequest) =>
    post<TeamsResponse>("/setup/follow", body),

  notificationPrefs: () =>
    get<NotificationPrefsResponse>("/notifications/prefs"),

  updateNotificationPref: (body: NotificationPrefUpdate) =>
    put<NotificationPrefsResponse>("/notifications/prefs", body),

  /** Every individually-followed player (team-sport athletes, keyed by bare ESPN id). */
  playerFollows: () => get<PlayerFollow[]>("/players/follows"),

  /**
   * Follow (or re-follow) one player. The body carries the roster row's
   * context (name/team/league/position/photo); the backend validates the
   * v1 constraint that the athlete is on that followed team's roster.
   */
  followPlayer: (athleteId: string, body: PlayerFollowIn) =>
    put<PlayerFollow>(
      `/players/follows/${encodeURIComponent(athleteId)}`,
      body,
    ),

  /** Stop following a player (404s if the follow doesn't exist). */
  unfollowPlayer: (athleteId: string) =>
    del(`/players/follows/${encodeURIComponent(athleteId)}`),

  calendarIcsUrl: API_BASE + "/calendar.ics",
};
