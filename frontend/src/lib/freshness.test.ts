/**
 * The freshness keeper: what makes a display left running for days keep
 * showing current data.
 *
 * Three layers, cheapest first:
 * - the pure policy (`maxAgeFor`, `isDue`, `selectDue`) with an injected
 *   clock;
 * - source guards: main.tsx must start the keeper and hooks.ts must keep the
 *   key shapes the policy reads. Every test below builds its own
 *   QueryClient, so without these a merge that dropped the one wiring line
 *   would switch the whole fix off with the suite still green;
 * - the keeper itself against a real QueryClient and QueryObserver under
 *   fake timers — the same objects `useQuery` drives, minus the DOM.
 */
import {
  QueryClient,
  QueryObserver,
  environmentManager,
  focusManager,
  isServer,
  onlineManager,
  type QueryObserverOptions,
  type QueryState,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import hooksSource from "../hooks.ts?raw";
import mainSource from "../main.tsx?raw";
import type { Game } from "../types";
import {
  DEFAULT_MAX_AGE_MS,
  FRESHNESS_POLICY,
  costFor,
  isDue,
  isHot,
  maxAgeFor,
  selectDue,
  startFreshnessKeeper,
} from "./freshness";

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const NOW = Date.parse("2026-10-05T16:00:00Z");

function makeGame(overrides: Partial<Game>): Game {
  return {
    id: "espn:401",
    league_id: "test-league",
    sport: "basketball",
    start_time: new Date(NOW).toISOString(),
    phase: "scheduled",
    ...overrides,
  } as Game;
}

/** A game whose kickoff is `offsetMs` from NOW (negative = already past). */
function gameAt(offsetMs: number, phase: Game["phase"] = "scheduled"): Game {
  const start_time = new Date(NOW + offsetMs).toISOString();
  return makeGame({ phase, start_time });
}

function state(overrides: Partial<QueryState>): QueryState {
  return {
    data: undefined,
    dataUpdateCount: 0,
    dataUpdatedAt: 0,
    error: null,
    errorUpdateCount: 0,
    errorUpdatedAt: 0,
    fetchFailureCount: 0,
    fetchFailureReason: null,
    fetchMeta: null,
    isInvalidated: false,
    status: "pending",
    fetchStatus: "idle",
    ...overrides,
  };
}

/** A query whose last attempt succeeded `agoMs` before NOW. */
function loaded(agoMs: number, data: unknown = {}): QueryState {
  return state({
    status: "success",
    data,
    dataUpdatedAt: NOW - agoMs,
    dataUpdateCount: 1,
  });
}

/** A query that loaded, and whose refresh an hour later failed `agoMs` ago. */
function failed(agoMs: number, error: Error): QueryState {
  return state({
    status: "error",
    data: {},
    dataUpdatedAt: NOW - agoMs - HOUR,
    dataUpdateCount: 1,
    error,
    errorUpdatedAt: NOW - agoMs,
    errorUpdateCount: 1,
  });
}

/** A query whose first load failed `agoMs` ago and has never succeeded. */
function neverLoaded(agoMs: number, error: Error): QueryState {
  return state({
    status: "error",
    error,
    errorUpdatedAt: NOW - agoMs,
    errorUpdateCount: 1,
  });
}

describe("maxAgeFor", () => {
  it("never refreshes an archive or static data", () => {
    const finalGame = { game: gameAt(-3 * HOUR, "final") };
    expect(maxAgeFor(["standings", "test-league", 2024], {}, NOW)).toBeNull();
    expect(maxAgeFor(["history-results", "test:t2", 2023], [], NOW)).toBeNull();
    expect(maxAgeFor(["meta"], {}, NOW)).toBeNull();
    expect(maxAgeFor(["game-detail", "espn:401"], finalGame, NOW)).toBeNull();
  });

  it("refreshes the current standings every 5 minutes", () => {
    const key = ["standings", "test-league", "current"];
    expect(maxAgeFor(key, {}, NOW)).toBe(300_000);
  });

  it("refreshes standings it does not recognise instead of freezing them", () => {
    // Only a numeric season is an archive. A renamed sentinel, a missing
    // slot or a season sent as text must all keep refreshing.
    const age = (...rest: unknown[]) =>
      maxAgeFor(["standings", "test-league", ...rest], {}, NOW);
    expect(age("live")).toBe(300_000);
    expect(age()).toBe(300_000);
    expect(age(null)).toBe(300_000);
    expect(age("2024")).toBe(300_000);
  });

  it("gives an unlisted root the default age, rationed as upstream", () => {
    expect(maxAgeFor(["brand-new-hook", 1], {}, NOW)).toBe(900_000);
    expect(DEFAULT_MAX_AGE_MS).toBe(900_000);
    expect(costFor(["brand-new-hook", 1])).toBe("upstream");
    // An inherited Object.prototype member is not a policy row.
    expect(maxAgeFor(["constructor"], {}, NOW)).toBe(900_000);
    expect(maxAgeFor([{ scope: "x" }], {}, NOW)).toBe(900_000);
  });

  it("paces a matchup by its game", () => {
    const key = ["matchup", "espn:401"];
    const age = (game: Game) => maxAgeFor(key, { game }, NOW);
    expect(age(gameAt(-20 * MINUTE, "in_progress"))).toBe(300_000);
    expect(age(gameAt(30 * MINUTE))).toBe(300_000);
    expect(age(gameAt(-5 * HOUR))).toBe(300_000);
    expect(age(gameAt(24 * HOUR))).toBe(1_800_000);
    expect(age(gameAt(-3 * HOUR, "final"))).toBeNull();
    // A postponed game can still be rescheduled; before the payload loads
    // there is no game to read.
    expect(age(gameAt(0, "postponed"))).toBe(1_800_000);
    expect(maxAgeFor(key, undefined, NOW)).toBe(1_800_000);
  });

  it("refreshes a box score until its game is final", () => {
    const key = ["game-detail", "espn:401"];
    const live = { game: gameAt(-HOUR, "in_progress") };
    expect(maxAgeFor(key, { game: gameAt(2 * HOUR) }, NOW)).toBe(300_000);
    expect(maxAgeFor(key, live, NOW)).toBe(300_000);
    expect(maxAgeFor(key, undefined, NOW)).toBe(300_000);
  });
});

describe("isHot", () => {
  it("is hot from an hour before kickoff until six hours after", () => {
    expect(isHot(gameAt(HOUR + MINUTE), NOW)).toBe(false);
    expect(isHot(gameAt(HOUR), NOW)).toBe(true);
    expect(isHot(gameAt(-6 * HOUR), NOW)).toBe(true);
    expect(isHot(gameAt(-6 * HOUR - MINUTE), NOW)).toBe(false);
  });

  it("is hot while the game is in progress, whatever its start time", () => {
    expect(isHot(gameAt(-9 * HOUR, "in_progress"), NOW)).toBe(true);
  });

  it("is never hot once the game is over, off, or unknown", () => {
    expect(isHot(gameAt(-HOUR, "final"), NOW)).toBe(false);
    expect(isHot(gameAt(0, "postponed"), NOW)).toBe(false);
    expect(isHot(gameAt(0, "canceled"), NOW)).toBe(false);
    expect(isHot(undefined, NOW)).toBe(false);
    expect(isHot(makeGame({ start_time: "not a date" }), NOW)).toBe(false);
  });
});

describe("isDue", () => {
  const results = ["results", "test:t2", null]; // a 5-minute row
  const scorers = ["scorers", "test-cup"]; // a 60-minute row

  it("is due once the last attempt is as old as the row allows", () => {
    expect(isDue(results, loaded(5 * MINUTE - 1), NOW)).toBe(false);
    expect(isDue(results, loaded(5 * MINUTE), NOW)).toBe(true);
    expect(isDue(scorers, loaded(59 * MINUTE), NOW)).toBe(false);
    expect(isDue(scorers, loaded(61 * MINUTE), NOW)).toBe(true);
  });

  it("is not due while an attempt is under way", () => {
    const fetching = { ...loaded(3 * HOUR), fetchStatus: "fetching" as const };
    const paused = { ...loaded(3 * HOUR), fetchStatus: "paused" as const };
    expect(isDue(results, fetching, NOW)).toBe(false);
    expect(isDue(results, paused, NOW)).toBe(false);
  });

  it("is not due before the first attempt: that load is the hook's", () => {
    expect(isDue(results, state({}), NOW)).toBe(false);
  });

  it("is never due on a row that never refreshes", () => {
    const monthAgo = 30 * 24 * HOUR;
    expect(isDue(["meta"], loaded(monthAgo), NOW)).toBe(false);
    const archive = ["standings", "test-league", 2024];
    expect(isDue(archive, loaded(monthAgo), NOW)).toBe(false);
    // An archive that 404'd is not re-asked either.
    const missing = failed(monthAgo, new ApiError(404, "No archive"));
    const pastSeason = ["history-results", "test:t2", 1999];
    expect(isDue(pastSeason, missing, NOW)).toBe(false);
  });

  it("still loads a never-refreshed row the backend was unreachable for", () => {
    // "Never refreshed" is not "never loaded": a display that boots before
    // the API must not sit on the "couldn't reach" splash until a key press.
    const status = ["setup-status"];
    const offline = new TypeError("Failed to fetch");
    const badGateway = new ApiError(502, "Bad Gateway");
    expect(isDue(status, neverLoaded(59 * SECOND, offline), NOW)).toBe(false);
    expect(isDue(status, neverLoaded(60 * SECOND, offline), NOW)).toBe(true);
    expect(isDue(status, neverLoaded(60 * SECOND, badGateway), NOW)).toBe(true);
  });

  it("leaves a never-refreshed row alone once the backend has answered", () => {
    // An answer is final, whatever it said: an archive that does not exist
    // (404) is not asked for every minute, nor is a row the backend 500'd.
    // (Where the 500 arrives with its status, that is. The desktop app's
    // webview delivers a backend crash as a rejection with no status, which
    // is the test above: the crash's 500 has no CORS header.)
    const pastSeason = ["history-results", "test:t2", 1999];
    const noArchive = new ApiError(404, "No archive");
    const broken = new ApiError(500, "Internal Server Error");
    expect(isDue(pastSeason, neverLoaded(HOUR, noArchive), NOW)).toBe(false);
    expect(isDue(["setup-status"], neverLoaded(HOUR, broken), NOW)).toBe(false);
    // The backend's own 502 is an answer too. It is how `/setup/teams` says
    // the team catalog could not be fetched, and every retry of it is a
    // fresh call to the provider that just failed.
    const catalogDown = new ApiError(
      502,
      '{"detail":"Failed to fetch teams from ESPN"}',
    );
    const teams = ["setup-teams", "test-league"];
    expect(isDue(teams, neverLoaded(HOUR, catalogDown), NOW)).toBe(false);
    // And a row that HAS loaded is never due, even when a later refetch
    // (App's refresh-everything, say) could not reach the backend.
    const offline = new TypeError("Failed to fetch");
    expect(isDue(["setup-status"], failed(HOUR, offline), NOW)).toBe(false);
    // Only a recorded failure is retried. A query with no error has
    // `error: null`, which has no HTTP status either and must not read as
    // "unreachable": loading it is its hook's business, as on a first load.
    const noFailure = { ...neverLoaded(HOUR, offline), error: null };
    expect(
      isDue(["setup-status"], { ...noFailure, status: "pending" }, NOW),
    ).toBe(false);
  });

  it("re-asks within a minute when the backend could not be reached", () => {
    // fetch() rejects with no HTTP status; the proxy answers 502, in plain
    // text, as an HTML page or with no body at all. A brace further in (an
    // error page's inline CSS) does not make the body the backend's JSON:
    // only a body that STARTS with "{" is an answer.
    const unreachable = [
      new TypeError("Failed to fetch"),
      new ApiError(502, "Bad Gateway"),
      new ApiError(502, "<html><body>502 Bad Gateway</body></html>"),
      new ApiError(
        502,
        "<html><head><style>body{margin:0}</style></head><body>502</body></html>",
      ),
      new ApiError(502, ""),
    ];
    for (const error of unreachable) {
      expect(isDue(scorers, failed(59 * SECOND, error), NOW)).toBe(false);
      expect(isDue(scorers, failed(60 * SECOND, error), NOW)).toBe(true);
    }
  });

  it("re-asks after 5 minutes on any other error, 503 and 504 included", () => {
    // A 504 is the proxy giving up on a slow fan-out. It gets no longer
    // wait: the backend finishes and caches the answer, and five minutes
    // later that cache is still warm (see `retryAfter`).
    // So does a 502 the backend sent itself (a JSON body): only the proxy's
    // means nothing was listening. Whitespace ahead of the "{" is skipped.
    const refusals = [
      new ApiError(503, "Upstream provider unavailable"),
      new ApiError(500, "Internal Server Error"),
      new ApiError(504, "Gateway Timeout"),
      new ApiError(502, '{"detail":"Failed to fetch teams from ESPN"}'),
      new ApiError(502, '\n{"detail":"Failed to fetch teams from ESPN"}'),
    ];
    for (const error of refusals) {
      expect(isDue(scorers, failed(4 * MINUTE, error), NOW)).toBe(false);
      expect(isDue(scorers, failed(5 * MINUTE, error), NOW)).toBe(true);
    }
  });

  it("never re-asks a failed row more slowly than a healthy one", () => {
    // A 5-minute row is already at the error cap.
    const timeout = new ApiError(504, "Gateway Timeout");
    expect(isDue(results, failed(5 * MINUTE, timeout), NOW)).toBe(true);
  });

  it("times a failed first load from the failure", () => {
    // There is no successful load to measure from: dataUpdatedAt is 0.
    const refused = new ApiError(503, "Upstream provider unavailable");
    expect(isDue(scorers, neverLoaded(4 * MINUTE, refused), NOW)).toBe(false);
    expect(isDue(scorers, neverLoaded(5 * MINUTE, refused), NOW)).toBe(true);
  });

  it("ignores a timestamp that is ahead of the clock", () => {
    // The clock was set back two hours after these loaded. Their stamps say
    // nothing about age any more; read literally they would hold every row
    // back for the two hours plus its max age.
    const ahead = loaded(-2 * HOUR);
    const schedule = ["schedule", "2026-10-01", "2026-10-31", null];
    expect(isDue(schedule, ahead, NOW)).toBe(true);
    expect(isDue(scorers, ahead, NOW)).toBe(true);
    // A row that never refreshes still never does.
    expect(isDue(["standings", "test-league", 2024], ahead, NOW)).toBe(false);

    // The refetch that follows failed: it is the one stamp that can be
    // trusted, so the row is paced by its error rule, not asked every tick.
    const failedSince = (agoMs: number): QueryState => ({
      ...ahead,
      status: "error",
      error: new ApiError(503, "Upstream provider unavailable"),
      errorUpdatedAt: NOW - agoMs,
      errorUpdateCount: 1,
    });
    expect(isDue(scorers, failedSince(4 * MINUTE), NOW)).toBe(false);
    expect(isDue(scorers, failedSince(5 * MINUTE), NOW)).toBe(true);

    // And a failure stamped ahead of the clock is left out the same way:
    // what remains is a load an hour and a half old.
    const failedAhead: QueryState = {
      ...failed(-2 * HOUR, new ApiError(503, "Upstream provider unavailable")),
      dataUpdatedAt: NOW - 90 * MINUTE,
    };
    expect(isDue(scorers, failedAhead, NOW)).toBe(true);
    expect(isDue(scorers, { ...failedAhead, dataUpdatedAt: NOW - MINUTE }, NOW))
      .toBe(false);

    // The two rules together: a row that never loaded and whose one stamp is
    // ahead of the clock has an unknown age, so it is retried at once.
    // "Never due" here would leave the splash up for good.
    const offline = new TypeError("Failed to fetch");
    expect(isDue(["setup-status"], neverLoaded(-2 * HOUR, offline), NOW)).toBe(
      true,
    );
    // A `null` row that HAS data stays never due, future stamp or not.
    const loadedAhead: QueryState = {
      ...failed(HOUR, offline),
      dataUpdatedAt: NOW + 2 * HOUR,
    };
    expect(isDue(["setup-status"], loadedAhead, NOW)).toBe(false);
  });
});

describe("selectDue", () => {
  const query = (queryKey: unknown[], queryState: QueryState) => ({
    queryKey,
    state: queryState,
  });
  const roots = (queries: { queryKey: readonly unknown[] }[]) =>
    queries.map((q) => q.queryKey[0]);

  it("starts every due db row but only the most overdue upstream row", () => {
    // All loaded 3 hours ago: the 30-minute rows are 2.5 h overdue, the
    // 60-minute rows 2 h. `leaders` loaded a minute earlier than the rest.
    const tomorrow = { game: gameAt(24 * HOUR) };
    const upstream = [
      query(["scorers", "test-cup"], loaded(3 * HOUR)),
      query(["schedule-weather", "a,b"], loaded(3 * HOUR)),
      query(["game-odds", "a,b"], loaded(3 * HOUR)),
      query(["leaders", "test-league"], loaded(3 * HOUR + MINUTE)),
      query(["bracket", "test-league"], loaded(3 * HOUR)),
      query(["matchup", "espn:401"], loaded(3 * HOUR, tomorrow)),
    ];
    const db = [
      query(["schedule", "2026-10-01", "2026-10-31", null], loaded(3 * HOUR)),
      query(["standings", "test-league", "current"], loaded(3 * HOUR)),
      query(["teams"], loaded(3 * HOUR)),
    ];

    const picked = selectDue([...upstream, ...db], NOW, 1);

    expect(roots(picked)).toEqual([
      "schedule",
      "standings",
      "teams",
      "leaders",
    ]);
  });

  it("works through a backlog in order of how overdue each row is", () => {
    let backlog = [
      query(["scorers", "test-cup"], loaded(3 * HOUR)), // 2 h overdue
      query(["game-odds", "a,b"], loaded(3 * HOUR)), // 2.5 h
      query(["leaders", "test-league"], loaded(4 * HOUR)), // 3.5 h
      query(["schedule-weather", "a,b"], loaded(5 * HOUR)), // 4 h
    ];
    const order: unknown[] = [];
    // Each tick the picked row is refetched, which makes it fresh.
    for (let tick = 0; tick < 4; tick += 1) {
      const [next, ...rest] = selectDue(backlog, NOW, 1);
      expect(rest).toEqual([]);
      order.push(next.queryKey[0]);
      backlog = backlog.filter((q) => q !== next);
    }
    expect(order).toEqual([
      "schedule-weather",
      "leaders",
      "game-odds",
      "scorers",
    ]);
  });

  it("ranks a failed row by its retry time, not by its age", () => {
    // Its refresh failed 6 minutes ago with a 503: due at 5, so 1 minute
    // overdue — behind a healthy row that is 10 minutes past its age.
    const refused = new ApiError(503, "Upstream provider unavailable");
    const picked = selectDue(
      [
        query(["scorers", "test-cup"], failed(6 * MINUTE, refused)),
        query(["leaders", "test-league"], loaded(40 * MINUTE)),
      ],
      NOW,
      1,
    );
    expect(roots(picked)).toEqual(["leaders"]);
  });

  it("ranks a row of unknown age behind one that is measurably overdue", () => {
    // `scorers` is stamped ahead of the clock: due, but as zero overdue, so
    // it does not jump the queue past a row known to be a minute late.
    const picked = selectDue(
      [
        query(["scorers", "test-cup"], loaded(-2 * HOUR)),
        query(["leaders", "test-league"], loaded(31 * MINUTE)),
      ],
      NOW,
      1,
    );
    expect(roots(picked)).toEqual(["leaders"]);
  });

  it("returns nothing that is not due, and no upstream row unbudgeted", () => {
    const rows = [
      query(["scorers", "test-cup"], loaded(3 * HOUR)),
      query(["teams"], loaded(3 * HOUR)),
      query(["results", "test:t2", null], loaded(MINUTE)),
      query(["meta"], loaded(3 * HOUR)),
    ];
    expect(roots(selectDue(rows, NOW, 0))).toEqual(["teams"]);
    expect(roots(selectDue(rows, NOW, 5))).toEqual(["teams", "scorers"]);
  });
});

describe("source guards", () => {
  const hookRoots = new Set(
    [...hooksSource.matchAll(/queryKey:\s*\[\s*"([^"]+)"/g)].map((m) => m[1]),
  );

  /** The `useQuery({ ... })` options block that declares `root`'s key. */
  function hookOptions(root: string): string {
    const start = hooksSource.indexOf(`queryKey: ["${root}"`);
    const end = hooksSource.indexOf("\n  });", start);
    expect(start, `hooks.ts has no query keyed ["${root}", …]`).not.toBe(-1);
    expect(end, `no end found for the ["${root}"] useQuery`).not.toBe(-1);
    return hooksSource.slice(start, end);
  }

  it("main.tsx starts the keeper on the app's QueryClient", () => {
    expect(mainSource).toMatch(
      /^import\s*\{[^}]*\bstartFreshnessKeeper\b[^}]*\}\s*from\s*"\.\/lib\/freshness";?$/m,
    );
    // Anchored to the line start so a commented-out call does not count.
    expect(mainSource).toMatch(/^startFreshnessKeeper\(queryClient\);?$/m);
  });

  it("every query root in hooks.ts has a policy row", () => {
    expect(hookRoots.size).toBeGreaterThan(20);
    const missing = [...hookRoots].filter(
      (root) => !Object.hasOwn(FRESHNESS_POLICY, root),
    );
    expect(missing, "add a FRESHNESS_POLICY row for each").toEqual([]);
  });

  it("every policy row still names a query root in hooks.ts", () => {
    // A row left behind by a renamed key does nothing, and the renamed
    // query silently falls back to the default age.
    const dead = Object.keys(FRESHNESS_POLICY).filter(
      (root) => !hookRoots.has(root),
    );
    expect(dead, "remove or rename these FRESHNESS_POLICY rows").toEqual([]);
  });

  it("hooks.ts still keys standings the way the policy reads them", () => {
    // key[2] is the season: a number for an archive, "current" otherwise.
    expect(hooksSource).toMatch(
      /queryKey:\s*\[\s*"standings",\s*leagueId \?\? null,\s*season \?\? "current",?\s*\]/,
    );
    expect(hooksSource).toMatch(
      /export function useStandings\(\s*leagueId: string \| undefined,\s*season\?: number,/,
    );
  });

  it("the id-list hooks pass no placeholderData over the keeper's", () => {
    // An explicit option on the hook, even `undefined`, beats a key default.
    // If a hook starts setting its own, drop the keeper's default for it.
    for (const root of ["game-odds", "schedule-weather"]) {
      const options = hookOptions(root);
      expect(options).toContain("queryFn");
      expect(options).not.toContain("placeholderData");
    }
  });
});

describe("startFreshnessKeeper", () => {
  type WatchOptions = Omit<QueryObserverOptions, "queryFn">;

  interface Watched {
    observer: QueryObserver;
    options: QueryObserverOptions;
    /** When each request started (epoch ms); its length is the count. */
    starts: number[];
    unmount: () => void;
  }

  let cleanups: (() => void)[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    // Node has no `window`, which TanStack reads as server rendering and
    // answers by installing no timers at all.
    environmentManager.setIsServer(() => false);
  });

  afterEach(() => {
    for (const cleanup of cleanups) cleanup();
    cleanups = [];
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
    environmentManager.setIsServer(() => isServer);
    vi.useRealTimers();
  });

  /** main.tsx's defaults, mounted the way QueryClientProvider mounts it. */
  function makeClient(): QueryClient {
    const client = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
      },
    });
    client.mount();
    cleanups.push(() => client.unmount());
    return client;
  }

  function startKeeper(client: QueryClient): () => void {
    const stop = startFreshnessKeeper(client);
    cleanups.push(stop);
    return stop;
  }

  /** Mount an observer the way a component's `useQuery` does. */
  function watch(
    client: QueryClient,
    watchOptions: WatchOptions,
    respond: () => Promise<unknown> = async () => ({}),
  ): Watched {
    const starts: number[] = [];
    const options: QueryObserverOptions = {
      ...watchOptions,
      queryFn: () => {
        starts.push(Date.now());
        return respond();
      },
    };
    const observer = new QueryObserver(client, options);
    const unmount = observer.subscribe(() => undefined);
    cleanups.push(unmount);
    return { observer, options, starts, unmount };
  }

  const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);
  const sleep = (ms: number) =>
    new Promise<void>((resolve) => setTimeout(resolve, ms));

  const RESULTS = ["results", "test:t2", null]; // a 5-minute db row
  const SCORERS = ["scorers", "test-cup"]; // a 60-minute upstream row
  const SCHEDULE = ["schedule", "2026-10-01", "2026-10-31", null];

  // What useStandings passes for the current season and for an archive.
  const currentStandings: WatchOptions = {
    queryKey: ["standings", "test-league", "current"],
    staleTime: undefined,
    retry: undefined,
  };
  const archivedStandings: WatchOptions = {
    queryKey: ["standings", "test-league", 2024],
    staleTime: Infinity,
    retry: false,
  };

  it("without the keeper, standings on screen for four days load once", async () => {
    // The bug this module fixes, kept as a control for the test below.
    const standings = watch(makeClient(), currentStandings);

    await advance(96 * HOUR);

    expect(standings.starts).toHaveLength(1);
  });

  it("keeps current standings fresh for days and leaves archives alone", async () => {
    const client = makeClient();
    startKeeper(client);
    const standings = watch(client, currentStandings);
    const archive = watch(client, archivedStandings);
    const pastSeason = watch(client, {
      queryKey: ["history-results", "test:t2", 2023],
      staleTime: Infinity,
      retry: false,
    });
    const meta = watch(client, { queryKey: ["meta"], staleTime: Infinity });

    await advance(96 * HOUR);

    // The first load, then one refresh every 5 minutes.
    expect(standings.starts).toHaveLength(1 + 96 * 12);
    expect(archive.starts).toHaveLength(1);
    expect(pastSeason.starts).toHaveLength(1);
    expect(meta.starts).toHaveLength(1);
  });

  it("asks nothing while the browser tab is hidden, then once on return", async () => {
    const client = makeClient();
    startKeeper(client);
    const results = watch(client, { queryKey: RESULTS });

    focusManager.setFocused(false);
    // Ten seconds off the tick grid, so the fetch on return can only come
    // from the visibility listener.
    await advance(8 * HOUR + 10 * SECOND);
    expect(results.starts).toHaveLength(1);

    focusManager.setFocused(true);
    expect(results.starts).toHaveLength(2);
    await advance(4 * MINUTE);
    expect(results.starts).toHaveLength(2);
  });

  it("asks nothing while offline, then once when the network returns", async () => {
    const client = makeClient();
    startKeeper(client);
    // TanStack refetches a STALE query on reconnect by itself; `teams` is
    // never stale by its own lights (useTeams: staleTime Infinity), so its
    // refresh can only be the keeper's. `results` is refetched by both and
    // must still be asked once.
    const teams = watch(client, { queryKey: ["teams"], staleTime: Infinity });
    const results = watch(client, { queryKey: RESULTS });

    onlineManager.setOnline(false);
    await advance(8 * HOUR + 10 * SECOND);
    expect(teams.starts).toHaveLength(1);
    expect(results.starts).toHaveLength(1);

    onlineManager.setOnline(true);
    expect(teams.starts).toHaveLength(2);
    await advance(4 * MINUTE);
    expect(teams.starts).toHaveLength(2);
    expect(results.starts).toHaveLength(2);
  });

  it.each<[string, WatchOptions["refetchInterval"], number]>([
    ["every 60 s", 60_000, 60],
    ["every 10 min", 600_000, 12],
    ["never (an explicit undefined)", undefined, 12],
  ])(
    "does not stack with a caller that polls %s",
    async (_label, refetchInterval, perHour) => {
      const client = makeClient();
      startKeeper(client);
      const results = watch(client, { queryKey: RESULTS, refetchInterval });

      await advance(HOUR);

      // The faster of the two sets the pace; they never add up.
      expect(results.starts).toHaveLength(1 + perHour);
    },
  );

  it("never fetches a parked query until something live watches its key", async () => {
    const client = makeClient();
    startKeeper(client);
    const parked = watch(client, { queryKey: RESULTS });
    parked.observer.setOptions({ ...parked.options, enabled: false });

    await advance(24 * HOUR);
    expect(parked.starts).toHaveLength(1);

    // A second, enabled observer of the key: stale data refetches on mount,
    // then the keeper holds it at the row's age again.
    const live = watch(client, { queryKey: RESULTS });
    expect(live.starts).toHaveLength(1);
    await advance(HOUR);
    expect(live.starts).toHaveLength(1 + 12);
    expect(parked.starts).toHaveLength(1);
  });

  it("does not let a parked query hold the upstream slot", async () => {
    const client = makeClient();
    startKeeper(client);
    // Parked first, so it is always the more overdue of the two.
    const parked = watch(client, { queryKey: ["leaders", "test-league"] });
    parked.observer.setOptions({ ...parked.options, enabled: false });
    await advance(SECOND);
    const live = watch(client, { queryKey: ["leaders", "test-cup"] });

    await advance(31 * MINUTE);

    expect(parked.starts).toHaveLength(1);
    expect(live.starts).toHaveLength(2);
  });

  it("does not let a static query hold the upstream slot", async () => {
    const client = makeClient();
    startKeeper(client);
    const fixed = watch(client, {
      queryKey: ["leaders", "test-league"],
      staleTime: "static",
    });
    await advance(SECOND);
    const live = watch(client, { queryKey: ["leaders", "test-cup"] });

    await advance(31 * MINUTE);

    expect(fixed.starts).toHaveLength(1);
    expect(live.starts).toHaveLength(2);
  });

  it("leaves a query alone once nothing is watching it", async () => {
    const client = makeClient();
    startKeeper(client);
    const schedule = watch(client, { queryKey: SCHEDULE });

    // The view closes at 4 minutes; the query stays cached for 5 more and
    // passes its 5-minute age in that time.
    await advance(4 * MINUTE);
    schedule.unmount();
    await advance(4 * MINUTE);

    expect(client.getQueryState(SCHEDULE)).toBeDefined();
    expect(schedule.starts).toHaveLength(1);
  });

  it("does not restart a refresh that is still in flight", async () => {
    const client = makeClient();
    startKeeper(client);
    let delay = 0;
    const scorers = watch(client, { queryKey: SCORERS }, async () => {
      await sleep(delay);
      return { rows: [] };
    });

    // The hourly refresh is a cold fan-out that takes 4 minutes: 8 ticks.
    delay = 4 * MINUTE;
    await advance(HOUR + 3 * MINUTE);
    expect(scorers.starts).toHaveLength(2);
    expect(client.getQueryState(SCORERS)?.fetchStatus).toBe("fetching");

    await advance(MINUTE);
    expect(client.getQueryState(SCORERS)?.fetchStatus).toBe("idle");
    expect(scorers.starts).toHaveLength(2);
  });

  it.each<[string, Error, number]>([
    ["the request got no answer", new TypeError("Failed to fetch"), 60],
    ["the proxy answered 502", new ApiError(502, "Bad Gateway"), 60],
    ["the backend answered 503", new ApiError(503, "Unavailable"), 12],
    ["the proxy answered 504", new ApiError(504, "Gateway Timeout"), 12],
  ])(
    "re-asks a failed hourly row at the pace its error calls for: %s",
    async (_label, error, perHour) => {
      const client = makeClient();
      startKeeper(client);
      let outage = false;
      // retry: false so one attempt is one request; the app's single retry
      // would only add a second request a second later.
      const scorers = watch(
        client,
        { queryKey: SCORERS, retry: false },
        async () => {
          if (outage) throw error;
          return { rows: ["Wren Halloway"] };
        },
      );

      outage = true;
      await advance(HOUR);
      const failure = client.getQueryState(SCORERS);
      expect(failure?.status).toBe("error");
      // The table on screen is still the one that loaded.
      expect(failure?.data).toEqual({ rows: ["Wren Halloway"] });
      expect(scorers.starts).toHaveLength(2);

      await advance(HOUR);
      expect(scorers.starts).toHaveLength(2 + perHour);
    },
  );

  it("recovers a failed hourly row within minutes of the backend's return", async () => {
    const client = makeClient();
    startKeeper(client);
    let outage = false;
    watch(client, { queryKey: SCORERS, retry: false }, async () => {
      if (outage) throw new ApiError(503, "Upstream provider unavailable");
      return { rows: ["Wren Halloway"] };
    });

    outage = true;
    await advance(HOUR + 2 * MINUTE);
    expect(client.getQueryState(SCORERS)?.status).toBe("error");

    outage = false;
    await advance(5 * MINUTE + 30 * SECOND);
    expect(client.getQueryState(SCORERS)?.status).toBe("success");
  });

  it("brings a preview back within minutes of a refresh the proxy timed out", async () => {
    // MatchupView shows an error panel in place of its content when a
    // refetch fails, so how long a failed refresh is left standing is how
    // long the preview is off the screen.
    const client = makeClient();
    startKeeper(client);
    const preview = { game: gameAt(26 * HOUR) }; // not hot: a 30-minute row
    let attempts = 0;
    const matchup = watch(
      client,
      { queryKey: ["matchup", "test:g1"], staleTime: 5 * MINUTE },
      async () => {
        attempts += 1;
        // The half-hour refresh and the app's one-second retry of it.
        if (attempts === 2 || attempts === 3) {
          throw new ApiError(504, "Gateway Timeout");
        }
        return preview;
      },
    );

    await advance(31 * MINUTE);
    expect(matchup.observer.getCurrentResult().isError).toBe(true);

    await advance(5 * MINUTE + 30 * SECOND);
    expect(matchup.observer.getCurrentResult().isError).toBe(false);
    expect(matchup.starts).toHaveLength(4);
  });

  it("re-asks a timed-out row while the backend's answer is still cached", async () => {
    const client = makeClient();
    startKeeper(client);
    // The server behind a 20-second proxy: a cold walk takes 25 seconds, so
    // the proxy answers 504 — but the walk runs on and is cached for 30
    // minutes, and any request inside that time is answered from the cache.
    let cachedUntil = 0;
    let walks = 0;
    watch(client, { queryKey: SCORERS }, async () => {
      if (Date.now() < cachedUntil) return { rows: ["Wren Halloway"] };
      walks += 1;
      setTimeout(() => {
        cachedUntil = Date.now() + 30 * MINUTE;
      }, 25 * SECOND);
      await sleep(20 * SECOND);
      throw new ApiError(504, "Gateway Timeout");
    });

    await advance(MINUTE);
    expect(client.getQueryState(SCORERS)?.status).toBe("error");
    // The load and the app's one-second retry of it, both cold.
    expect(walks).toBe(2);

    await advance(6 * MINUTE);
    expect(client.getQueryState(SCORERS)?.status).toBe("success");
    expect(walks).toBe(2);
  });

  it("gets a display that booted before the API off its error splash", async () => {
    const client = makeClient();
    startKeeper(client);
    let apiUp = false;
    // What useSetupStatus passes: it polls only while a refresh is running.
    const status = watch(
      client,
      {
        queryKey: ["setup-status"],
        staleTime: 0,
        refetchInterval: (query) =>
          (query.state.data as { refreshing?: boolean } | undefined)?.refreshing
            ? 5 * SECOND
            : false,
      },
      async () => {
        if (!apiUp) throw new TypeError("Failed to fetch");
        return { onboarded: true, refreshing: false };
      },
    );

    // Ten seconds off the tick grid: during an attempt the query is
    // "pending" again, not "error".
    await advance(3 * MINUTE + 10 * SECOND);
    expect(status.observer.getCurrentResult().status).toBe("error");
    // The load and its retry, then the same pair each minute and a half
    // (due after 60 s, seen on the next 30 s tick).
    expect(status.starts).toHaveLength(6);

    apiUp = true;
    await advance(90 * SECOND);
    expect(status.observer.getCurrentResult().status).toBe("success");
    const loadedAfter = status.starts.length;

    // Loaded, it is a never-refreshed row again: App refetches EVERYTHING
    // when this query's `refreshing` flag clears, so it must stay unpolled.
    await advance(24 * HOUR);
    expect(status.starts).toHaveLength(loadedAfter);
  });

  it("does not re-ask a team catalog the backend said it could not fetch", async () => {
    // The wizard's Teams step, open on a league whose catalog is down. The
    // backend answers 502 itself, and each request is a fresh call to the
    // provider that just failed: read as "unreachable", this row was asked
    // again every minute and a half for as long as the step stayed open.
    const client = makeClient();
    startKeeper(client);
    // What useSetupTeams passes; the retry is the app's default.
    const teams = watch(
      client,
      { queryKey: ["setup-teams", "test-league"], staleTime: 5 * MINUTE },
      async () => {
        throw new ApiError(502, '{"detail":"Failed to fetch teams from ESPN"}');
      },
    );

    await advance(10 * MINUTE);
    expect(teams.observer.getCurrentResult().status).toBe("error");
    // The load and the app's one-second retry of it; the section's own
    // Retry button is the way back.
    expect(teams.starts).toHaveLength(2);
  });

  it("keeps refreshing after the clock is set back", async () => {
    const client = makeClient();
    startKeeper(client);
    const schedule = watch(client, { queryKey: SCHEDULE });
    await advance(20 * MINUTE);
    expect(schedule.starts).toHaveLength(5);

    // An NTP correction, or someone fixing the clock by hand: every stamp
    // in the cache is now two hours in the future.
    vi.setSystemTime(Date.now() - 2 * HOUR);
    await advance(HOUR);

    // Asked on the first tick after the step, then every 5 minutes.
    expect(schedule.starts).toHaveLength(5 + 12);
  });

  it("paces a failing row by its error after the clock is set back", async () => {
    const client = makeClient();
    startKeeper(client);
    let outage = false;
    const scorers = watch(
      client,
      { queryKey: SCORERS, retry: false },
      async () => {
        if (outage) throw new ApiError(503, "Upstream provider unavailable");
        return { rows: ["Wren Halloway"] };
      },
    );
    await advance(MINUTE);

    vi.setSystemTime(Date.now() - 2 * HOUR);
    outage = true;
    await advance(HOUR);

    // The load's stamp stays ahead of the clock for as long as the refetch
    // keeps failing. It must not outrank the failure's: once on the first
    // tick, then every 5 minutes — not on every tick.
    expect(scorers.starts).toHaveLength(1 + 12);
    expect(client.getQueryState(SCORERS)?.data).toEqual({
      rows: ["Wren Halloway"],
    });
  });

  it("starts upstream rows that come due together one tick apart", async () => {
    const client = makeClient();
    startKeeper(client);
    // Three 30-minute rows loaded in the same instant.
    const rows = [
      watch(client, { queryKey: ["leaders", "test-league"] }),
      watch(client, { queryKey: ["bracket", "test-league"] }),
      watch(client, { queryKey: ["game-odds", "a,b"], staleTime: 5 * MINUTE }),
    ];

    await advance(35 * MINUTE);

    const refreshedAt = rows
      .map((row) => row.starts[1] - NOW)
      .sort((a, b) => a - b);
    expect(refreshedAt).toEqual([
      30 * MINUTE,
      30 * MINUTE + 30 * SECOND,
      31 * MINUTE,
    ]);
  });

  it("drains a backlog one upstream row per tick when the tab returns", async () => {
    const client = makeClient();
    startKeeper(client);
    const order: string[] = [];
    const mount = (queryKey: unknown[], extra: Partial<WatchOptions> = {}) =>
      watch(client, { queryKey, ...extra }, async () => {
        order.push(String(queryKey[0]));
        return {};
      });

    // Six provider-backed rows, mounted a second apart and NOT in order of
    // how overdue they will be: the 30-minute rows fall due half an hour
    // before the 60-minute ones, and within each group the earlier mount is
    // the more overdue.
    const upstream = [
      SCORERS,
      ["game-odds", "a,b"],
      ["schedule-weather", "a,b"],
      ["leaders", "test-league"],
      ["bracket", "test-league"],
      ["matchup", "espn:401"],
    ];
    for (const queryKey of upstream) {
      mount(queryKey);
      await advance(SECOND);
    }
    mount(SCHEDULE);
    mount(["standings", "test-league", "current"]);
    mount(["teams"], { staleTime: Infinity });

    focusManager.setFocused(false);
    await advance(8 * HOUR);
    order.length = 0;

    // Back on screen, everything is hours past its age: every db row goes
    // at once, with the single most overdue upstream row.
    focusManager.setFocused(true);
    expect([...order].sort()).toEqual([
      "game-odds",
      "schedule",
      "standings",
      "teams",
    ]);

    const drained: string[][] = [];
    for (let tick = 0; tick < 6; tick += 1) {
      order.length = 0;
      await advance(30 * SECOND);
      drained.push([...order]);
    }
    expect(drained).toEqual([
      ["leaders"],
      ["bracket"],
      ["matchup"],
      ["scorers"],
      ["schedule-weather"],
      [],
    ]);
  });

  it("looks at the cache only as often as it is told to", async () => {
    const client = makeClient();
    cleanups.push(startFreshnessKeeper(client, 7 * MINUTE));
    const results = watch(client, { queryKey: RESULTS });

    await advance(15 * MINUTE);

    // Due at 5 minutes, but only seen on the 7- and 14-minute ticks.
    expect(results.starts.map((at) => at - NOW)).toEqual([
      0,
      7 * MINUTE,
      14 * MINUTE,
    ]);
  });

  it("stops for good when its stop function is called", async () => {
    const client = makeClient();
    const stop = startKeeper(client);
    const teams = watch(client, { queryKey: ["teams"], staleTime: Infinity });
    await advance(20 * MINUTE);
    expect(teams.starts).toHaveLength(3);

    stop();
    await advance(24 * HOUR);
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await advance(24 * HOUR);

    expect(teams.starts).toHaveLength(3);
  });

  describe("id-list queries", () => {
    const ID_LIST_ROOTS = ["game-odds", "schedule-weather"];

    // What useGameOdds / useScheduleWeather build from a list of game ids.
    // `isDown` makes the batch answer 503, as `/odds` does in an ESPN outage.
    function idListOptions(
      root: string,
      ids: string[],
      isDown: () => boolean = () => false,
    ): QueryObserverOptions {
      return {
        queryKey: [root, [...ids].sort().join(",")],
        queryFn: async () => {
          await sleep(2 * SECOND);
          if (isDown()) {
            throw new ApiError(503, "Upstream provider unavailable");
          }
          return Object.fromEntries(ids.map((id) => [id, `${root} of ${id}`]));
        },
        enabled: ids.length > 0,
        staleTime: 5 * MINUTE,
      };
    }

    function mountIdList(client: QueryClient, root: string): QueryObserver {
      const options = idListOptions(root, ["g1", "g2"]);
      const observer = new QueryObserver(client, options);
      cleanups.push(observer.subscribe(() => undefined));
      return observer;
    }

    it.each(ID_LIST_ROOTS)(
      "%s: without the keeper, a changed id list blanks the data",
      async (root) => {
        const observer = mountIdList(makeClient(), root);
        await advance(2 * SECOND);
        expect(observer.getCurrentResult().data).toBeDefined();

        // g1 kicks off (or goes final) and drops out of the list.
        observer.setOptions(idListOptions(root, ["g2"]));

        expect(observer.getCurrentResult().data).toBeUndefined();
        expect(observer.getCurrentResult().isLoading).toBe(true);
      },
    );

    it.each(ID_LIST_ROOTS)(
      "%s: keeps the previous list's data while a changed list loads",
      async (root) => {
        const client = makeClient();
        startKeeper(client);
        const observer = mountIdList(client, root);
        await advance(2 * SECOND);
        const before = observer.getCurrentResult().data;
        expect(before).toEqual({ g1: `${root} of g1`, g2: `${root} of g2` });

        observer.setOptions(idListOptions(root, ["g2"]));

        const during = observer.getCurrentResult();
        expect(during.data).toEqual(before);
        expect(during.isPlaceholderData).toBe(true);
        expect(during.isLoading).toBe(false);
        expect(during.isFetching).toBe(true);

        await advance(2 * SECOND);
        const after = observer.getCurrentResult();
        expect(after.data).toEqual({ g2: `${root} of g2` });
        expect(after.isPlaceholderData).toBe(false);
      },
    );

    it.each(ID_LIST_ROOTS)(
      "%s: a changed list that fails has nothing to keep (a known limit)",
      async (root) => {
        // Placeholder data is shown only while a query is PENDING. A new
        // id list is a new query with no data of its own, so once its first
        // request fails the previous list's data goes with it — unlike a
        // failed REFETCH of a list already loaded, which keeps its data.
        // Pinned so the docs cannot promise more than this.
        const client = makeClient();
        startKeeper(client);
        let outage = false;
        const options = (ids: string[]) =>
          idListOptions(root, ids, () => outage);
        const observer = new QueryObserver(client, options(["g1", "g2"]));
        cleanups.push(observer.subscribe(() => undefined));
        await advance(2 * SECOND);
        const before = observer.getCurrentResult().data;
        expect(before).toBeDefined();

        // The upstream goes down, and then g1 kicks off.
        outage = true;
        observer.setOptions(options(["g2"]));
        const during = observer.getCurrentResult();
        expect(during.data).toEqual(before);
        expect(during.isPlaceholderData).toBe(true);

        // The request and the app's one retry of it: 2 s, a 1 s wait, 2 s.
        await advance(5 * SECOND);
        const failure = observer.getCurrentResult();
        expect(failure.isError).toBe(true);
        expect(failure.data).toBeUndefined();

        // Still nothing an hour into the outage; back on the keeper's first
        // 5-minute retry after it ends.
        await advance(HOUR);
        expect(observer.getCurrentResult().data).toBeUndefined();
        outage = false;
        await advance(5 * MINUTE + 30 * SECOND);
        const recovered = observer.getCurrentResult();
        expect(recovered.data).toEqual({ g2: `${root} of g2` });
        expect(recovered.isPlaceholderData).toBe(false);
      },
    );

    it("holds previous data for those two roots only", async () => {
      // Anywhere else the previous key is a different subject: one league's
      // leaders must never sit under another league's heading.
      const client = makeClient();
      startKeeper(client);
      const leaders = (league: string): QueryObserverOptions => ({
        queryKey: ["leaders", league],
        queryFn: async () => {
          await sleep(2 * SECOND);
          return { league };
        },
      });
      const observer = new QueryObserver(client, leaders("test-league"));
      cleanups.push(observer.subscribe(() => undefined));
      await advance(2 * SECOND);
      expect(observer.getCurrentResult().data).toEqual({
        league: "test-league",
      });

      observer.setOptions(leaders("test-cup"));

      expect(observer.getCurrentResult().data).toBeUndefined();
    });
  });
});
