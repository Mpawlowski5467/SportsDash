/**
 * The freshness keeper: one timer that keeps every mounted query no older
 * than its row in the policy table below.
 *
 * SportsDash is left running for days — a wall display, a pinned browser
 * tab, the desktop app — and its views stay mounted once visited. Most hooks
 * fetch once per key and never again, so without this a display shows the
 * standings, schedule and previews it loaded on the day it was opened. The
 * keeper walks the query cache every `TICK_MS` and refetches what has
 * outlived its policy. A view sitting behind another tab is still mounted,
 * so it is kept current too and comes back into view fresh.
 *
 * It lives outside the hooks on purpose. Interval defaults on the
 * QueryClient cannot do the job: any hook that passes `refetchInterval`,
 * even as `undefined`, overrides them. The keeper instead reads each query's
 * AGE — time since its last attempt, whoever made it — so a hook that polls
 * faster simply never comes due, and one that does not poll at all is still
 * kept current.
 *
 * What it will not do:
 * - fetch while the browser tab is hidden or the network is down (the
 *   backlog is caught up when either returns);
 * - fetch a parked (`enabled: false`) query, or one nobody is observing;
 * - restart a fetch that is already in flight;
 * - start more than `UPSTREAM_PER_TICK` provider-backed requests per tick,
 *   so several expensive rows that come due together — after a long hidden
 *   spell, say — drain one tick apart instead of landing on ESPN as a burst.
 *
 * Every query root in hooks.ts needs a row here; freshness.test.ts fails
 * when one is missing.
 */
import {
  focusManager,
  keepPreviousData,
  onlineManager,
  type QueryClient,
  type QueryKey,
  type QueryState,
} from "@tanstack/react-query";

import { ApiError } from "../api";
import type { Game } from "../types";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

/** How often the keeper looks at the cache. */
export const TICK_MS = 30_000;

/**
 * Max age for a query root with no policy row, so that a hook added without
 * one still cannot go stale forever.
 */
export const DEFAULT_MAX_AGE_MS = 15 * MINUTE;

/** The longest a query waits to be asked again after the backend refused it. */
export const ERROR_RETRY_MS = 5 * MINUTE;

/** The same, when the backend could not be reached at all. */
export const UNREACHABLE_RETRY_MS = MINUTE;

/** Provider-backed refetches the keeper may start in one tick. */
export const UPSTREAM_PER_TICK = 1;

/**
 * Age, in ms since the last attempt, at which a query is due. null = never
 * refreshed once it has loaded; a first load that could not reach the
 * backend is still retried (see `overdueMs`).
 */
export type MaxAge = number | null;

export type FreshnessRule =
  | MaxAge
  | ((key: QueryKey, data: unknown, nowMs: number) => MaxAge);

/**
 * What one request costs the backend. "db" endpoints read only the database;
 * "upstream" ones can call ESPN or Open-Meteo, and are rationed per tick.
 */
export type CostClass = "db" | "upstream";

export interface FreshnessRow {
  maxAge: FreshnessRule;
  cost: CostClass;
}

/**
 * Live, or scheduled with kickoff between an hour ahead and six hours
 * overdue. A game still "scheduled" past its start time is one the backend
 * has not caught up with yet, so it counts; after six hours it is a
 * postponement nobody recorded, not a game in play.
 */
export function isHot(game: Game | undefined, nowMs: number): boolean {
  if (game === undefined) return false;
  if (game.phase === "in_progress") return true;
  if (game.phase !== "scheduled") return false;
  const kickoff = Date.parse(game.start_time);
  return kickoff - nowMs <= HOUR && nowMs - kickoff <= 6 * HOUR;
}

/** The game inside a `/matchup` or `/games/{id}` payload, if it has loaded. */
function payloadGame(data: unknown): Game | undefined {
  return (data as { game?: Game } | undefined)?.game;
}

/**
 * Max age per query root (`queryKey[0]`), counted from the query's last
 * attempt. The ages follow what a request costs and how fast the data behind
 * it actually moves. Where a hook polls on its own, its row is no shorter
 * than that hook's slowest interval, so the keeper only steps in once the
 * hook has stopped asking.
 */
export const FRESHNESS_POLICY: Record<string, FreshnessRow> = {
  // Straight from the database, which the backend's own jobs keep moving
  // (the live tick, the events tick, a standings refresh on every FINAL).
  schedule: { maxAge: 5 * MINUTE, cost: "db" },
  events: { maxAge: 5 * MINUTE, cost: "db" },
  results: { maxAge: 5 * MINUTE, cost: "db" },
  // These two poll themselves, at least this often. Their rows matter after
  // a hidden or offline spell, when the keeper catches them up at once
  // instead of on the hook's next beat.
  today: { maxAge: 5 * MINUTE, cost: "db" },
  news: { maxAge: 5 * MINUTE, cost: "db" },
  // A numeric season is an archive and never changes. Everything else
  // refreshes — the "current" sentinel, and any key shape this rule has not
  // seen: guessing wrong must cost a request, not freeze the table.
  standings: {
    maxAge: (key) => (typeof key[2] === "number" ? null : 5 * MINUTE),
    cost: "db",
  },
  // Follows can be edited from another device.
  teams: { maxAge: 10 * MINUTE, cost: "db" },
  // The pre-game roster re-sync is a 15-minute job.
  roster: { maxAge: 15 * MINUTE, cost: "db" },
  nation: { maxAge: 15 * MINUTE, cost: "db" },
  "player-follows": { maxAge: 15 * MINUTE, cost: "db" },

  // A preview is assembled from a dozen or so provider calls. Its lines and
  // lineups move in the run-up and during play; a final never changes.
  matchup: {
    maxAge: (_key, data, nowMs) => {
      const game = payloadGame(data);
      if (game?.phase === "final") return null;
      return isHot(game, nowMs) ? 5 * MINUTE : 30 * MINUTE;
    },
    cost: "upstream",
  },
  // The box score polls itself while live; this covers the hours before
  // kickoff, and the kickoff itself, which that poll cannot see coming.
  "game-detail": {
    maxAge: (_key, data) =>
      payloadGame(data)?.phase === "final" ? null : 5 * MINUTE,
    cost: "upstream",
  },
  // Two provider calls per game id, for up to 60 ids.
  "game-odds": { maxAge: 30 * MINUTE, cost: "upstream" },
  leaders: { maxAge: 30 * MINUTE, cost: "upstream" },
  bracket: { maxAge: 30 * MINUTE, cost: "upstream" },
  // The widest fan-outs: a summary per final game, a forecast per outdoor
  // game on the calendar.
  scorers: { maxAge: HOUR, cost: "upstream" },
  "schedule-weather": { maxAge: HOUR, cost: "upstream" },
  // The map polls itself while it has a competition pin or an upcoming
  // game; with neither it would otherwise never ask again.
  map: { maxAge: 15 * MINUTE, cost: "upstream" },

  // Keyed by local day and on its own 6-hour timer.
  "on-this-day": { maxAge: null, cost: "upstream" },
  // A past season is immutable.
  "history-results": { maxAge: null, cost: "upstream" },
  // Static for the session, or open only inside the setup wizard. The status
  // must stay unpolled: App refetches EVERYTHING when its `refreshing` flag
  // clears, and polling it would let the nightly refresh trigger that.
  meta: { maxAge: null, cost: "db" },
  "setup-status": { maxAge: null, cost: "db" },
  "setup-selection": { maxAge: null, cost: "db" },
  "setup-leagues": { maxAge: null, cost: "upstream" },
  "setup-teams": { maxAge: null, cost: "upstream" },
  "notification-prefs": { maxAge: null, cost: "db" },
};

// A root this table has never heard of is refreshed, but rationed: nothing
// here can tell what its endpoint costs.
const UNLISTED_ROW: FreshnessRow = {
  maxAge: DEFAULT_MAX_AGE_MS,
  cost: "upstream",
};

function rowFor(key: QueryKey): FreshnessRow {
  const root = key[0];
  // hasOwn, not `in`: a root named "constructor" is not a policy row.
  return typeof root === "string" && Object.hasOwn(FRESHNESS_POLICY, root)
    ? FRESHNESS_POLICY[root]
    : UNLISTED_ROW;
}

/** The max age of one query right now; `data` is its current payload. */
export function maxAgeFor(
  key: QueryKey,
  data: unknown,
  nowMs: number,
): MaxAge {
  const rule = rowFor(key).maxAge;
  return typeof rule === "function" ? rule(key, data, nowMs) : rule;
}

/** Whether a refetch of this query can reach a provider. */
export function costFor(key: QueryKey): CostClass {
  return rowFor(key).cost;
}

/**
 * Whether a failed attempt never reached the backend: no HTTP status (the
 * request got no answer at all) or the proxy's 502 (it could not connect).
 *
 * The backend sends a 502 of its own — `/setup/teams`, when the team catalog
 * cannot be fetched — and that one is an answer: read as "unreachable" it
 * had the wizard's Teams step re-asking a failing provider every minute and
 * a half. The two are told apart by the body `ApiError` keeps as its
 * message: FastAPI's is a JSON object, a proxy's is plain text, HTML or
 * nothing.
 *
 * One answer cannot be told from silence. In the desktop app a backend crash
 * is a 500 with no CORS header, which the webview rejects with no status, so
 * it lands here as unreachable (docs/CONTRACTS.md, "When a query is due").
 */
function isUnreachable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 502 && !error.message.trimStart().startsWith("{");
}

/**
 * How long to wait before asking again after a failed attempt.
 *
 * - Unreachable: nothing is listening, so the retry is free and a quick one
 *   shortens the outage on screen.
 * - Anything else, 503 and 504 included: the backend is there but did not
 *   help. A few minutes, never slower than the row's own cadence.
 *
 * A 504 — the proxy gave up waiting on a slow fan-out — deliberately gets no
 * longer wait of its own, tempting as "do not start a second fan-out" is:
 * - the app's `retry: 1` sends that second request a second later whatever
 *   is decided here;
 * - the backend does not stop when the proxy hangs up. It finishes the
 *   fan-out and caches it, and every upstream cache lives at least ten
 *   minutes, so asking again after five is answered from that cache. A wait
 *   of the row's full age is as long as its cache lives or longer (scorers:
 *   60 minutes against 30), so it meets a cold fan-out, and a timeout,
 *   again;
 * - the Matchup preview shows an error panel in place of its content when a
 *   refetch fails, and would sit on that panel for half an hour.
 */
function retryAfter(error: unknown, maxAge: number): number {
  return Math.min(
    maxAge,
    isUnreachable(error) ? UNREACHABLE_RETRY_MS : ERROR_RETRY_MS,
  );
}

/** How far past due a query is, or null when it is not due. */
function overdueMs(
  key: QueryKey,
  state: QueryState,
  nowMs: number,
): number | null {
  // Fetching, or paused offline: an attempt is already under way.
  if (state.fetchStatus !== "idle") return null;
  // Never attempted: the first load belongs to the hook that mounts it.
  if (Math.max(state.dataUpdatedAt, state.errorUpdatedAt) === 0) return null;
  // A stamp ahead of the clock was written before the clock was set back
  // (an NTP correction, a manual change). It says nothing about the query's
  // age, and measured against the new clock it would hold the row back for
  // the size of the step, so it is left out.
  const dataAt = state.dataUpdatedAt <= nowMs ? state.dataUpdatedAt : 0;
  const errorAt = state.errorUpdatedAt <= nowMs ? state.errorUpdatedAt : 0;
  const maxAge = maxAgeFor(key, state.data, nowMs);
  let limit: number;
  if (maxAge === null) {
    // "Never refreshed" still means "loaded once". A row that has no data
    // because the backend could not be reached is asked again until it has
    // some: a display that boots before the API would otherwise sit on the
    // "couldn't reach" splash until somebody pressed Retry. An error the
    // backend ANSWERED with (an archive's 404, the team catalog's own 502)
    // stays final, as before. The
    // status test matters: a query with no error at all has `error: null`,
    // which carries no HTTP status either.
    if (
      state.data !== undefined ||
      state.status !== "error" ||
      !isUnreachable(state.error)
    ) {
      return null;
    }
    limit = UNREACHABLE_RETRY_MS;
  } else {
    limit = errorAt > dataAt ? retryAfter(state.error, maxAge) : maxAge;
  }
  const last = Math.max(dataAt, errorAt);
  // Every stamp is ahead of the clock: the age is unknown, so ask now. The
  // attempt stamps the query afresh, and from then on it is timed as usual.
  if (last === 0) return 0;
  const overdue = nowMs - last - limit;
  return overdue >= 0 ? overdue : null;
}

/** Whether a query has outlived its policy and may be refetched now. */
export function isDue(
  key: QueryKey,
  state: QueryState,
  nowMs: number,
): boolean {
  return overdueMs(key, state, nowMs) !== null;
}

/** What `selectDue` reads off a TanStack `Query`. */
export interface AgedQuery {
  queryKey: QueryKey;
  state: QueryState;
}

/**
 * The queries to refetch this tick: every due "db" one, but at most
 * `upstreamBudget` due "upstream" ones — the most overdue first, so a row
 * that has waited through several ticks is never passed over by one that
 * came due later.
 */
export function selectDue<T extends AgedQuery>(
  queries: readonly T[],
  nowMs: number,
  upstreamBudget: number,
): T[] {
  const db: T[] = [];
  const upstream: { query: T; overdue: number }[] = [];
  for (const query of queries) {
    const overdue = overdueMs(query.queryKey, query.state, nowMs);
    if (overdue === null) continue;
    if (costFor(query.queryKey) === "db") {
      db.push(query);
    } else {
      upstream.push({ query, overdue });
    }
  }
  upstream.sort((a, b) => b.overdue - a.overdue);
  const rationed = upstream.slice(0, Math.max(0, upstreamBudget));
  return [...db, ...rationed.map((entry) => entry.query)];
}

// Query roots keyed on a list of game ids that is derived from another
// payload (hooks.ts: useGameOdds, useScheduleWeather).
const ID_LIST_ROOTS = ["game-odds", "schedule-weather"] as const;

/**
 * Start the keeper on `client`; returns a function that stops it.
 *
 * Call once, before the first query mounts (main.tsx does). Stopping ends
 * the refetching only: the key defaults set below stay on the client.
 */
export function startFreshnessKeeper(
  client: QueryClient,
  tickMs: number = TICK_MS,
): () => void {
  // The id-list queries change KEY whenever the schedule they derive from
  // does — a game kicks off or goes final — and now that the schedule
  // refreshes every few minutes that happens all day. A new key is a new,
  // empty query: the upset radar would drop to its loading line and every
  // calendar forecast glyph would vanish until the batch returned. Holding
  // the previous list's data across the switch keeps both painted. Set as a
  // key default because the hooks pass no `placeholderData` of their own.
  for (const root of ID_LIST_ROOTS) {
    client.setQueryDefaults([root], { placeholderData: keepPreviousData });
  }

  const tick = (): void => {
    // A hidden tab has no reader and an offline one no backend; both are
    // caught up by the listeners below the moment they return.
    if (!focusManager.isFocused() || !onlineManager.isOnline()) return;
    // "active" = watched by an enabled observer, which leaves out parked
    // queries and ones kept in the cache after their view closed. Static
    // queries go too, because `refetchQueries` below refuses them: one
    // selected here but skipped there would hold the upstream slot on every
    // tick and starve the rows behind it.
    const candidates = client
      .getQueryCache()
      .findAll({ type: "active" })
      .filter((query) => !query.isStatic());
    const due = new Set(selectDue(candidates, Date.now(), UPSTREAM_PER_TICK));
    if (due.size === 0) return;
    // cancelRefetch: false joins a fetch already in flight rather than
    // aborting and restarting it.
    void client.refetchQueries(
      { predicate: (query) => due.has(query) },
      { cancelRefetch: false },
    );
  };

  const timer = setInterval(tick, tickMs);
  const offFocus = focusManager.subscribe((focused) => {
    if (focused) tick();
  });
  const offOnline = onlineManager.subscribe((online) => {
    if (online) tick();
  });

  return () => {
    clearInterval(timer);
    offFocus();
    offOnline();
  };
}
