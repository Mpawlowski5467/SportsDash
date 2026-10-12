# SportsDash — Module Contracts

> **Update (mock provider removed):** SportsDash is now live-data only. The
> mock provider (`backend/app/providers/mock.py`), the demo install
> (`POST /api/setup/demo`, `seed.DEMO_CONFIG` / `install_demo`), and the
> onboarding "Try the demo" path have been removed — all data comes from the
> ESPN and TheSportsDB providers. The "Mock provider" and "demo mode" sections
> below are retained as historical design context and no longer reflect the
> shipping code.

> **Update (phased plan retired):** every `## Phase N` section below was
> written against `docs/ROADMAP.md`, a phased build plan that **no longer
> exists in this repo** — don't go looking for it. Inside those sections,
> "per ROADMAP.md", "ROADMAP Phase N" and bare "ROADMAP.md" all refer to
> that deleted file, *not* to the root [ROADMAP.md](../ROADMAP.md), which
> is a forward-looking list with no phases. The phases shipped; the threads
> still open from them live under **Loose ends** in that root ROADMAP.md.

This file is the source of truth for every cross-module boundary. The
foundation files (already written, **do not modify**) are:

- `backend/app/models/domain.py` — normalized domain dataclasses
- `backend/app/models/orm.py` — SQLAlchemy tables
- `backend/app/schemas.py` — API response models
- `backend/app/providers/base.py` — `SportsProvider` Protocol
- `backend/app/config.py` — settings (`SPORTSDASH_*` env vars)
- `backend/app/db.py` — engine/session (`get_session` dependency, `session_scope()` for jobs)
- `backend/app/timeutil.py` — UTC helpers (`utcnow`, `ensure_utc`, `local_day_bounds`, …)
- `frontend/src/types.ts` — TS mirror of `schemas.py`

## Conventions (apply everywhere)

- **UTC internally.** All stored/compared datetimes are tz-aware UTC.
  Anything read from the DB goes through `timeutil.ensure_utc` (SQLite
  returns naive datetimes). Localization only at the response boundary
  (`/today`'s day computation) or in the frontend.
- **Fictional names only** in test fixtures and recorded provider
  payloads — never real teams, leagues, or players. Real names are fine in
  the ESPN catalog and in whatever the user follows: that is live app
  data, not sample data.
- Internal game ids are `f"{provider}:{provider_game_key}"`.
- Python 3.12, SQLAlchemy 2.0 style (`select()`, `Mapped`), Pydantic v2,
  full type hints, `logging.getLogger(__name__)` per module. No TODOs or
  placeholder stubs — production-quality code.
- External HTTP: `httpx.AsyncClient` with explicit timeouts; parse
  defensively (missing keys must not crash a whole refresh); log and
  skip bad records.

## Backend module contracts

### `app/providers/registry.py` (owner: providers agent)

```python
def register_provider(provider: SportsProvider) -> None
def get_provider(provider_id: str) -> SportsProvider          # KeyError if unknown
def provider_ids() -> list[str]                                # sorted ids, read-only view
async def close_all() -> None                                  # close every registered provider
```
Registers `espn` and `thesportsdb` at import time; both are wrapped in a
circuit-breaker guard (`_GUARDED_PROVIDERS`) so a down or rate-limited
source fails fast.

### `app/providers/espn/` (owner: providers agent)

A package since 2026-07-12 (split from the original 3,174-line
`espn.py`): `common` (constants, coercion, status/period normalization),
`games`, `individual` (tennis / MMA), `golf`, `racing` (race weekends,
added 2026-07-28 — see *Motorsport* below), `summary` (schedule
chunking, box score, plays, win probability, odds), `standings`,
`roster`, `news_location`, and `provider` (the class itself). Every
module but `provider` is pure parsers over fetched JSON; `__init__`
re-exports the provider and the parser entry points, so
`from app.providers.espn import …` is unchanged.

`class EspnProvider` implementing `SportsProvider`, `provider_id = "espn"`.
`League.provider_key` is the ESPN sport/league URL fragment (e.g.
`"basketball/nba"`); `Team.provider_key` is the ESPN team id. Endpoints
(site.api.espn.com, `/apis/site/v2/sports/{league.provider_key}/...`):
`/scoreboard?dates=YYYYMMDD` (the date is computed in **US/Eastern** —
ESPN buckets scoreboard days by ET, and a UTC date misses evening games),
`/teams/{id}/schedule`, `/teams/{id}/roster`, and
`/apis/v2/sports/{league.provider_key}/standings` — all parsed
defensively. The provider remembers ESPN-id → internal-id mappings from
schedule/roster calls and tags followed teams in standings rows. Status mapping: `status.type.state` `"pre"|"in"|"post"` →
`scheduled|in_progress|final` (+ `status.type.name` for postponed/canceled).
Period normalization: basketball → `"Q{n}"` / `"OT"`; soccer → period 1/2 →
`"1st Half"/"2nd Half"`, halftime → `is_intermission=True`; baseball →
`period` = inning, label `"Top {n}"/"Bot {n}"`, `clock=None`.

### `app/providers/thesportsdb.py` (owner: providers agent)

`class TheSportsDbProvider` implementing `SportsProvider`,
`provider_id = "thesportsdb"` — the second live source, backing
volleyball. `League.provider_key` is the TheSportsDB league id;
`Team.provider_key` the TheSportsDB team id. Its full endpoint/parsing
contract is in the *Phase 6: volleyball* section below.

> There is no mock provider. `app/providers/mock.py` was deleted when
> SportsDash went live-data only (see the banner at the top of this file);
> the mock sections further down are historical design context.

### `app/services/repository.py` (owner: persistence agent)

All functions take `session: AsyncSession` first; **no commits inside** —
callers commit (`session_scope()` commits on success; route handlers that
write must commit explicitly). Exactly **three** route modules write:
`routes/setup.py` (`POST /setup/follow`), `routes/notifications.py`
(`PUT /notifications/prefs`) and — since 2026-07-28 — `routes/players.py`
(`PUT`/`DELETE /players/follows/{athlete_id}`); every other route is
read-only and never commits (see *Player follows* below).

```python
async def list_leagues(session) -> list[LeagueORM]
async def list_teams(session, league_id: str | None = None) -> list[TeamORM]
async def get_team(session, team_id: str) -> TeamORM | None
async def get_league(session, league_id: str) -> LeagueORM | None
async def upsert_league(session, league: domain.League) -> None
async def upsert_team(session, team: domain.Team) -> None
async def upsert_games(session, games: Sequence[domain.Game]) -> int
    # insert-or-update by id; updates schedule fields (start_time, venue,
    # names); merges team ids (incoming None never erases a stored id).
    # Live-state columns belong to live polling, with three recovery
    # exceptions: a stored 'scheduled' row accepts any incoming .state;
    # an incoming FINAL state is authoritative from any stored phase
    # (games that ended while the app was down heal on daily refresh);
    # postponed/canceled rows may be reinstated by an incoming
    # scheduled/in_progress state. A game's fight_* columns (MMA method
    # of victory) merge PER FIELD, and an incoming None never wipes a
    # stored result: fight_method is overwritten outright, while
    # fight_detail/fight_round/fight_clock are written only when the
    # incoming value is not None (they come from a separate call that
    # can fail on its own, so a thinner incoming result must not erase a
    # richer stored one). This is NOT the crest/color rule — those merge
    # on truthiness from a single source. See the fight_result section
    # below. All provider strings are clipped to
    # their column widths on write. Returns number of rows touched.
async def prune_stale_games(session, now_utc: datetime) -> int
    # delete ghost rows: 'scheduled' games with start older than now-3d,
    # 'in_progress' games older than now-2d (daily refresh would have
    # healed them if any provider still knew the fixture); also deletes
    # 'in_progress' EventORM rows whose coalesce(end_time, start_time)
    # is older than now-2d (events_tick would otherwise poll a stuck
    # event forever). Returns games + events deleted. Run by daily_refresh.
async def apply_game_state(session, state: domain.GameState) -> GameORM | None
    # update state columns + state_updated_at; None if game id unknown
def state_from_row(row: GameORM) -> domain.GameState
    # build the previous GameState snapshot from a row (for diffing)
async def get_game(session, game_id: str) -> GameORM | None
async def games_between(session, start_utc, end_utc, team_id: str | None = None) -> list[GameORM]
    # start_time in [start_utc, end_utc), ordered ascending; team_id
    # filters to games where home_team_id or away_team_id matches
async def results_for_team(session, team_id: str, limit: int = 25) -> list[GameORM]
    # phase == 'final', newest first
async def games_needing_live_poll(session, now_utc, lead: timedelta, max_age: timedelta = timedelta(hours=8)) -> list[GameORM]
    # in_progress games (start_time newer than now-max_age), plus
    # scheduled games with start_time in [now - max_age, now + lead]
async def team_ids_with_games_starting_within(session, now_utc, lookahead: timedelta) -> set[str]
    # home/away ids of phase == 'scheduled' games with start_time in
    # [now, now + lookahead); the null side is dropped. Returns ids, NOT
    # rows: the caller fetches providers between DB scopes and a detached
    # GameORM would lazy-load (MissingGreenlet). Nothing already started is
    # included, unlike games_needing_live_poll. Scope query for
    # refresh_pregame_rosters (2026-07-29).
async def save_standings(session, standings: domain.Standings) -> None
async def get_standings(session, league_id: str) -> StandingsORM | None
async def replace_roster(session, roster: domain.Roster) -> None
    # delete + reinsert players for the team; set teams.roster_updated_at.
    # THE single writer of that timestamp — since 2026-07-29 it is also the
    # per-team cooldown clock for refresh_pregame_rosters, so a sync cools
    # itself down. The delete+reinsert writes EVERY player column, so a
    # caller holding a partial payload must carry the stored enrichment
    # forward first (see _carry_forward_enrichment in scheduler/refresh.py).
async def get_roster(session, team_id: str) -> list[PlayerORM]
async def upsert_news(session, items: Sequence[domain.NewsItem]) -> int   # newly inserted count
async def list_news(session, team_id: str | None = None, limit: int = 50) -> list[NewsORM]
    # newest first by published_at (nulls last), then fetched_at
async def was_notified(session, dedupe_key: str) -> bool
async def mark_notified(session, dedupe_key: str) -> None
```

`StandingsORM.rows` JSON dicts are shaped exactly like `StandingRowOut`.

### `app/services/events.py` (owner: events agent)

Sport-agnostic transition detector. Pure functions, no I/O, no sport
specifics (providers already normalized period semantics).

```python
def diff_states(prev: GameState | None, new: GameState, *, home_name: str, away_name: str) -> list[GameEvent]
def starting_soon_event(game_row_start_utc: datetime, game_id: str, *, home_name: str, away_name: str, minutes_out: int) -> GameEvent
```

Rules for `diff_states` (in this order; multiple events can fire in one diff):
- phase `scheduled→in_progress` (or `prev is None` and new is live) → `GAME_START`, dedupe `"{game_id}:start"`.
- while `in_progress`, `new.period > prev.period` and `new.period > 1` → `PERIOD_START`, message uses `new.period_label`, dedupe `"{game_id}:period:{new.period}"`. (Period 1 is covered by GAME_START.)
- while `in_progress`, `is_intermission` False→True → `INTERMISSION` ("End of {prev.period_label}…" with score), dedupe `"{game_id}:intermission:{prev.period}"`.
- phase →`final` (from anything non-final) → `FINAL` with final score, dedupe `"{game_id}:final"`.
- `starting_soon_event` dedupe `"{game_id}:soon"`; its message shows the
  start time localized to `settings.tzinfo` (a push notification is a
  response boundary — never UTC there).

Titles like `"{away_name} @ {home_name}"`; messages human-readable with
score where relevant. Unit tests must cover quarter, half, and inning
flavors plus intermission and final.

### `app/services/notify.py` (owner: events agent)

```python
async def send_event(event: GameEvent) -> bool
async def send(title: str, message: str, *, tags: str | None = None, priority: str | None = None) -> bool
```
POST `{settings.ntfy_url}/` — the server root, never the topic URL: this
is ntfy's publish-as-JSON form (ntfy ≥ 1.18.0). JSON body: `topic` =
`settings.ntfy_topic`, `title`, `message`; `tags` as a list of strings
(the comma-separated argument split, stripped, empties dropped; omitted
when none are left); `priority` as an integer (`min` 1, `low` 2,
`default` 3, `high` 4, `max`/`urgent` 5; omitted for `None` or any other
name). The only header set is `Authorization: Bearer` when `ntfy_token`
set. Nothing provider-derived may travel in a header: httpx encodes
header values as ASCII, so a `Title` header carrying "Getafe @ Atlético
Madrid" failed before the request was sent and the notification was
lost. Returns False (never raises) on failure, a non-2xx response
included. When `notifications_enabled` is False, skip sending and return
True (treat as handled).

### `app/services/news.py` (owner: persistence agent)

```python
async def fetch_team_news(team: TeamORM) -> list[domain.NewsItem]   # parse all team.rss_feeds via feedparser in asyncio.to_thread, errors → log + skip feed
async def refresh_all_news() -> int                                  # own session_scope; returns new-item count
```
NewsItem id = sha1 hex of url (first 16 chars), source = feed title or hostname.

### `app/services/cache.py` (owner: persistence agent)

Thin optional Redis JSON cache; no function ever raises. A configured
Redis that is down, or a call that fails, is a silent no-op (or None).
With **no** `redis_url` the same holds for every key except an allowlist
of upstream read caches, which are kept in a bounded in-process store
instead (amended 2026-10-11 — see *`services/cache.py` without Redis*
below):
```python
async def cache_get_json(key: str) -> Any | None
async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None
async def close_cache() -> None          # also empties the in-process store
def clear_local_cache() -> None          # the in-process store only (tests)
def cache_counters() -> tuple[int, int]  # (hits, misses), for /api/metrics
```

### `app/scheduler/jobs.py` (owner: scheduler agent)

```python
def setup_scheduler() -> AsyncIOScheduler   # configured, NOT started
async def refresh_schedules() -> None       # window: -7d … +45d per team
async def refresh_standings() -> None
async def refresh_rosters() -> None
async def refresh_pregame_rosters() -> None # status-only roster re-sync for imminent games; NOT part of daily_refresh (2026-07-29)
async def refresh_news() -> None            # delegates to services.news.refresh_all_news
async def live_tick() -> None
async def daily_refresh() -> None           # schedules + standings + rosters (+ news)
```
**Where these actually live.** `jobs.py` kept the names above — it imports
them to register them, so `jobs.refresh_schedules` still resolves — but only
`setup_scheduler` is defined there. The package split into
`scheduler/refresh.py` (`refresh_schedules`, `refresh_standings`,
`refresh_rosters`, `refresh_pregame_rosters`, `refresh_news`,
`refresh_locations`, `daily_refresh` and
their `_resolve_*` helpers), `scheduler/live.py` (`live_tick`,
`events_tick`), `scheduler/common.py` (shared constants and the
followed-athlete-id tagging for leaderboard sports —
`_tag_followed_athletes`, renamed from `_tag_followed_golfers` when
racing joined golf in `LEADERBOARD_SPORTS`; see *Motorsport* below) and
`scheduler/stadium_cache.py` (competition stadium resolution).
Sections written before the split — Phase 10, Phase 11, Phase 27 — still say
`jobs.<name>`; read that as the name, not the file.

Jobs use `session_scope()`, resolve providers via
`registry.get_provider(league.provider)`, and must catch/log per-league
and per-team errors so one bad source never kills a whole job. Schedule:
`daily_refresh` daily at `settings.daily_refresh_hour` (in
`settings.tzinfo`); news every `news_refresh_minutes`; `live_tick` every
`live_poll_seconds` (`max_instances=1`, `coalesce=True`);
`refresh_pregame_rosters` every `pregame_roster_refresh_minutes`
(`max_instances=1`, `misfire_grace_time=300`, `coalesce=True` — see
*Same-day pre-game roster re-sync* below).

`live_tick` cheap-gate logic:
1. `games_needing_live_poll(now, lead=live_lead_minutes)`; empty → return
   (this is the "only poll fast while a followed team plays" gate).
2. Starting-soon: for `scheduled` rows with `0 <= start_time - now <= starting_soon_minutes`,
   build `starting_soon_event`; if not `was_notified`, `send_event` →
   on success `mark_notified`.
3. Group remaining rows by league; one `get_live_games()` call per league;
   match returned games to rows by id; fall back to `get_game_state()` for
   in-progress rows the scoreboard didn't cover.
4. For each match: `prev = state_from_row(row)`, then
   `events = diff_states(prev, new_state, home_name=…, away_name=…)`,
   then `apply_game_state`. For each event not `was_notified`:
   `send_event` → on success `mark_notified`. An `apply_game_state` that
   returns `None` — the row was deleted since step 1, i.e. a follow save
   landed mid-tick — skips the game: nothing is sent or ledgered for it.

### `app/seed.py` (owner: scheduler agent)

```python
async def seed_from_config(path: str | None = None) -> None   # parse YAML, upsert leagues+teams via repository
```
Seeds **only while the teams table is empty** — once onboarded the DB is
the source of truth for follows, so a stale config can never clobber the
user's picks. When a config does seed at least one team it also sets the
`onboarded` meta flag (the user authored their own follows; the wizard
would only get in the way). A missing, unreadable, or malformed file is
logged and skipped, never fatal; individual bad league/team entries are
logged and skipped too.

The shipped `backend/config/teams.yaml` is **comments-only** — a template,
not sample data — so a fresh install seeds nothing and lands on the setup
wizard. The documented shape (every league names a live provider; there is
no `mock`):
```yaml
leagues:
  - id: my-league                    # internal slug, unique
    sport: basketball                # any domain.Sport value
    name: My League
    provider: espn                   # espn | thesportsdb
    provider_key: basketball/nba     # ESPN sport/league URL fragment
teams:
  - id: my-team
    league_id: my-league
    name: My Team
    abbreviation: MYT
    provider_key: "18"               # numeric ESPN team id, quoted
    color: "#22c55e"                 # optional
    rss_feeds: []                    # optional news feed URLs
```
Because a hand-authored config names real leagues and teams, the
fictional-names rule does not apply to it — it is user data, like the
wizard's picks. Test fixtures stay fictional.

### `app/main.py` (owner: scheduler agent)

FastAPI app, CORS from `settings.cors_origins`. Lifespan: `init_db()` →
`seed_from_config()` → start scheduler → spawn `daily_refresh()` as a
background task (don't block startup) → yield → shutdown scheduler,
`registry.close_all()`, `cache.close_cache()`, `dispose_engine()`.
Includes `app.routes` router under prefix `/api`.

### `app/routes/` + serializers (owner: routes agent)

`app/routes/__init__.py` exposes `router = APIRouter()` aggregating all
endpoint modules. `app/services/serialize.py` provides
`game_to_out(row: GameORM, league: LeagueORM) -> GameOut` (scores are
`None` while `phase == "scheduled"`; `followed_team_ids` = non-null
home/away team ids). `app/services/ics.py` provides
`games_to_ics(rows, leagues_by_id, *, events=()) -> str` (hand-rolled
VCALENDAR, UTC `DTSTART`, escaped text; the keyword-only `events` adds
all-day leaderboard spans — see *MMA method of victory + leaderboard
events on the calendar* below).

Route table (all under `/api`, response models from `schemas.py`):

| Route | Response | Notes |
|---|---|---|
| `GET /health` | `{status, database, providers, provider_health}` | deep check; `status` is `"ok"`/`"degraded"`, `providers` is the count, `provider_health` the per-provider circuit state. Always 200 — a failed DB probe degrades `status` rather than 500-ing (see *Ops: backups + health deep-check* below) |
| `GET /metrics` | `text/plain; version=0.0.4; charset=utf-8` | Prometheus text-format exposition, hand-rolled (no prometheus_client). Reads the SAME sources as `/health` (live `SELECT 1` probe, circuit-breaker registry) so the two can never disagree. Always 200 — a failed DB probe becomes `sportsdash_database_up 0` (see *Prometheus-style `/api/metrics`* below) |
| `GET /meta` | `MetaOut` | `version` = `meta.APP_VERSION`, the shipped app version. Deliberately **not** repeated here as a literal: the string already ships in five files (only two of which fail the build when they disagree — see the release checklist in [CONTRIBUTING.md](../CONTRIBUTING.md#release-checklist)), and a copy in prose is the one nothing would catch |
| `GET /teams` | `TeamsOut` | |
| `GET /today` | `TodayOut` | local day via `local_day_bounds(local_today(tz), tz)`; sorted by start_time |
| `GET /schedule?start=YYYY-MM-DD&end=YYYY-MM-DD&team_id=` | `list[GameOut]` | dates are local days, inclusive; defaults: start = today−7d, end = today+45d |
| `GET /schedule/{team_id}?start=&end=` | `list[GameOut]` | same semantics, path team |
| `GET /standings/{league_id}` | `StandingsOut` | 404 unknown league; empty rows + `fetched_at: null` if not yet fetched |
| `GET /roster/{team_id}` | `RosterOut` | 404 unknown team |
| `GET /results/{team_id}?limit=25` | `list[GameOut]` | finals, newest first |
| `GET /news?team_id=&limit=50` | `list[NewsItemOut]` | |
| `GET /calendar.ics?team_id=` | `text/calendar` | all games −30d…+60d **plus every leaderboard event overlapping that window** (all-day spans); `Content-Disposition: attachment; filename=sportsdash.ics`. `?team_id=` narrows to that team's games, omits events, and renames the file `sportsdash-{team_id}.ics`; 404 unknown team |
| `GET /history/on-this-day` | `OnThisDayOut` | every followed team's past games on today's **local** month-day, scanned over the last 5 season keys; always 200 — empty `teams` is the normal no-anniversary state; unsupported providers/sports skipped silently (see *"On this day"* below) |
| `GET /players/follows` | `list[PlayerFollowOut]` | followed players, ordered by name |
| `PUT /players/follows/{athlete_id}` | `PlayerFollowOut` | body `PlayerFollowIn`; 404 unless the athlete is on a followed team's stored roster (see *Player follows* below) |
| `DELETE /players/follows/{athlete_id}` | 204 | 404 when not followed |

404s raise `HTTPException(404, "...")`. Unknown query params ignored.

## Frontend contracts

Stack: React 19 + TypeScript ~5.8, Vite ^6, Tailwind **v4** via
`@tailwindcss/vite` (CSS-first config — `@import "tailwindcss";` in
`src/index.css`, no tailwind.config), TanStack Query ^5, FullCalendar ^6.1.
(FullCalendar was removed on 2026-10-11: see "Box Score: the Calendar view".)
Dark-mode-first kiosk styling: near-black background (`bg-zinc-950`),
zinc-100 text, dense layout, team colors as accents. No router — tab
switching via `useState` in `App.tsx`.

### File ownership

- **scaffold agent**: `package.json`, `tsconfig.json`, `vite.config.ts`
  (react + tailwindcss plugins; dev server proxies `/api` →
  `http://localhost:8000`), `index.html` (dark `<html class="dark">`, title
  SportsDash), `src/main.tsx` (QueryClientProvider; QueryClient defaults:
  `staleTime: 30_000`, `refetchOnWindowFocus: false`, `retry: 1`; and,
  straight after the client is built, `startFreshnessKeeper(queryClient)`
  from `src/lib/freshness.ts` — the one line that keeps a long-running
  display current, see *Data freshness* below),
  `src/App.tsx`, `src/components/Layout.tsx`.
- **api/hooks agent**: `src/api.ts`, `src/hooks.ts`, `src/lib/time.ts`.
- **today/calendar agent**: `src/views/TodayView.tsx`,
  `src/views/CalendarView.tsx`, `src/components/GameCard.tsx`,
  `src/components/StatusBadge.tsx`, `src/calendar.css`.
- **tables/news agent**: `src/views/StandingsView.tsx`,
  `src/views/RosterView.tsx`, `src/views/ResultsView.tsx`,
  `src/views/NewsView.tsx`.

### `App.tsx` (scaffold agent)

```tsx
import TodayView from "./views/TodayView";
import CalendarView from "./views/CalendarView";
import StandingsView from "./views/StandingsView";
import RosterView from "./views/RosterView";
import ResultsView from "./views/ResultsView";
import NewsView from "./views/NewsView";
```
All views are default exports taking **no props**; each fetches its own
data via hooks (views needing a team/league selector render their own,
backed by `useTeams()`). Tabs: Today, Calendar, Standings, Rosters,
Results, News.

### `src/api.ts`

```ts
export class ApiError extends Error { status: number }
async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T>
export const api = {
  meta: () => get<Meta>("/meta"),
  teams: () => get<TeamsResponse>("/teams"),
  today: () => get<TodayResponse>("/today"),
  schedule: (p: { start: string; end: string; teamId?: string }) => Promise<Game[]>,
  standings: (leagueId: string) => Promise<Standings>,
  roster: (teamId: string) => Promise<Roster>,
  results: (teamId: string, limit?: number) => Promise<Game[]>,
  news: (teamId?: string, limit?: number) => Promise<NewsItem[]>,
  calendarIcsUrl: "/api/calendar.ics",
};
```
Base URL: `import.meta.env.VITE_API_BASE ?? "/api"`.

### `src/hooks.ts`

```ts
export function useMeta(): UseQueryResult<Meta>
export function useTeams(): UseQueryResult<TeamsResponse>
export function useToday(): UseQueryResult<TodayResponse>
  // refetchInterval: any game in_progress → 30s; any scheduled today → 60s; else 5min
export function useSchedule(start: string, end: string, teamId?: string): UseQueryResult<Game[]>
export function useStandings(leagueId: string | undefined): UseQueryResult<Standings>   // enabled only when defined
export function useRoster(teamId: string | undefined): UseQueryResult<Roster>
export function useResults(teamId: string | undefined, limit?: number): UseQueryResult<Game[]>
export function useNews(teamId?: string): UseQueryResult<NewsItem[]>
```

**Every query root needs a freshness row** (added 2026-10-11). A hook's
own `refetchInterval` — most have none — is no longer what decides how old
its data may get on a display left running: `src/lib/freshness.ts` holds a
max age and a cost class per `queryKey[0]`, and a keeper started from
`main.tsx` refetches whatever has outlived its row. A hook added here
under a new root must add its row to `FRESHNESS_POLICY` in the same
change, and a root renamed or removed here must take its row with it —
`freshness.test.ts` fails on either. The table and the rules are under
*Data freshness* below.

### `src/lib/time.ts`

```ts
export function formatTime(iso: string): string        // "7:30 PM" local
export function formatShortDate(iso: string): string   // "Jun 11"
export function formatDateTime(iso: string): string    // "Jun 11, 7:30 PM"
export function localDateKey(iso: string): string      // "YYYY-MM-DD" local
```

### `src/components/GameCard.tsx` (today/calendar agent; also used by Results)

```tsx
export default function GameCard({ game, teamColors }: {
  game: Game;
  teamColors?: Record<string, string>;  // team_id -> hex
})
```
Dense card: away @ home, scores when started, `StatusBadge` showing
phase-appropriate context (start time / `period_label` + clock + LIVE
pulse / FINAL), followed-team sides accented with their color.

### Calendar view

> Superseded on 2026-10-11 by "Box Score: the Calendar view" below; kept as history.

FullCalendar `dayGridMonth` (toggle to `timeGridWeek`), dark-themed via
`src/calendar.css` overriding `--fc-*` CSS vars. Events from
`useSchedule` over the visible range (track via `datesSet`), colored by
followed team's `color`. An "Export .ics" button links to
`api.calendarIcsUrl`.

## Phase 0+1: hotfixes + news (added per ROADMAP.md)

Foundation already landed (do not re-do): `StandingRow.group` /
`StandingRowOut.group` / `types.ts group`, `NewsItem.image_url` end to
end, `Settings.news_locale` ("lang-COUNTRY", default "en-US"),
`SportsProvider.get_news(league, team) -> list[NewsItem]` in the
protocol, `app/migrations.py` additive-column migrations (append-only;
`news_items.image_url` registered), repository serializes both new
fields.

### ESPN adapter (`espn/` package owner)

- `get_schedule` soccer fix: for `Sport.SOCCER` leagues make TWO calls —
  bare (completed results) and `?fixture=true` (upcoming) — and merge by
  game id. Other sports keep one call.
- All scoreboard fetches pass `limit=400` (ESPN silently caps at 100).
- `_parse_standings`: carry the conference/division/table name from each
  `children[].name` (or `.abbreviation`) into `StandingRow.group`; the
  top-level un-nested `standings` block gets `group=None`. Rank-sort
  WITHIN groups, preserving group order.
- `get_news(league, team)`: GET `{site}/{provider_key}/news?team={key}&limit=20`
  → NewsItem: id = sha1(links.web.href)[:16], title=headline,
  url=links.web.href, source "ESPN", published from `published`,
  summary=description, image_url=images[0].url when present. Skip
  `premium: true` articles. Defensive parsing throughout.

### News service (news.py owner)

`refresh_all_news()` merges three sources per team, still one
transaction per team: (1) provider news via
`registry.get_provider(league.provider).get_news(league, team)` —
resolve the team's league once per run; (2) the existing
`team.rss_feeds`; (3) an auto-generated Google News query
`https://news.google.com/rss/search?q="{team name}"&hl={lang-CC}&gl={CC}&ceid={CC}:{lang}`
from `settings.news_locale` (verified feedparser-compatible). RSS
entries may carry `media_content`/`media_thumbnail` → image_url.
Dedupe stays id/url-based across all sources. Per-source failures log
and skip. Google links are redirect URLs — keep `source` from the
entry's `source.title` when present.

### Mock provider (mock.py owner)

- `get_news`: deterministic fictional articles (5-8 per team, fictional
  outlets like "Harborline Sports Desk", example.com URLs,
  image_url=None, published spread over recent days) so demo mode demos
  the news pipeline.
- Standings: basketball + baseball pools split into two fictional
  conferences ("Eastreach" / "Westmark", 4 teams each, group field set,
  rank within group); soccer stays a single table (group=None).

### Frontend (views owner)

- StandingsView: group rows by `row.group` preserving API order — one
  sub-table per group with a small group heading; `group=null` renders
  exactly as today (single table).
- NewsView: left-aligned thumbnail (h-16 w-24 rounded object-cover) when
  `image_url` present, with `onError` hiding the img; layout unchanged
  when absent. (Superseded on 2026-10-11 by "Box Score: the News view".)

## Phase 2: NHL + NFL (per ROADMAP.md)

Foundation already landed (do not redo): `Sport.HOCKEY`/`Sport.FOOTBALL`,
`StandingRow.ot_losses` through domain/schemas/types/repository,
`types.ts` Sport union. All payload facts below were verified against
real ESPN traffic (see ROADMAP Phase 2).

### ESPN adapter (`espn/` package + espn_catalog.py owner)

- Catalog: add `nhl` ("NHL", hockey, espn, "hockey/nhl") and `nfl`
  ("NFL", football, espn, "football/nfl").
- `_normalize_period` MUST gain explicit HOCKEY and FOOTBALL branches —
  baseball is currently the fall-through and would regex hockey details
  into innings.
  - Hockey: periods 1–3 → "P{n}"; period 4 → "OT" (playoffs: period>4 →
    "{n-3}OT"); shootout → "SO" (regular season period 5; detect via
    `type.detail`/`altDetail` containing "SO" in preference to raw
    period). clock from displayClock while live. Intermission:
    state "in" + `END_PERIOD`-style status name (or detail "End of
    {n}th") → is_intermission=True.
    **Still provisional.** That intermission mapping was written
    defensively from ESPN's documented status names and has never been
    checked against a live NHL feed; it cannot be, because verifying it
    needs a game actually sitting between periods. Next window: the
    2026-27 NHL season, from October 2026. Tracked as *NHL intermission,
    verified live* under **Loose ends** in
    [ROADMAP.md](../ROADMAP.md).
  - Football: quarters like basketball ("Q1".."Q4", period≥5 → "OT");
    `STATUS_HALFTIME` → intermission.
- Schedules: for hockey AND football leagues fetch `?seasontype=2` and
  `?seasontype=3` and merge by game id (NHL defaults to the current
  phase only — playoffs-only during playoffs; verified). Keep the
  soccer two-call merge as-is; reuse the same merge helper.
- Schedule competitor scores arrive as OBJECTS here ({value,
  displayValue}) vs strings on the scoreboard — `_coerce_int` already
  handles both; don't regress.
- Standings stats: hockey → wins, losses, ot_losses ("otLosses" or
  "overtimeLosses"), points; rank via playoffSeed; group from
  children[].name (conferences). Football → wins, losses, draws (stat
  "ties"), win_pct ("winPercent"), group from children. Beware
  duplicate stat names and null values on summary rows.
- Roster: NHL injuries[] carries status but `status.type` stays
  "active" — derive PlayerStatus from injuries[0].status first
  (Out→out, Day-To-Day/Questionable→day_to_day), fall back to
  status.type. Applies generically (guarded) to all sports.

### Mock provider + demo (mock.py, seed.py, services/ics.py owner)

- mock.py: `_VENUE_SUFFIX` ("Coliseum" hockey, "Field" football) and
  `_POSITIONS` (C/LW/RW/D/G; QB/RB/WR/TE/OL/DL/LB/CB/S/K) entries —
  these dicts KeyError on unknown sports today. New deterministic state
  engines: hockey 3×20min periods with 3min intermissions, 5min OT then
  SO (goal-probability scoring, clock counts down); football 4×15min
  quarters, 2min breaks, 12min halftime, scores in 3/7-point steps.
  Standings: the two-conference split now applies to basketball,
  baseball, hockey AND football (hockey rows carry ot_losses+points and
  losses+otL reconcile with games played; football rows carry draws=0–2
  ties and win_pct); soccer stays single-table.
- seed.py DEMO_CONFIG: add a fictional hockey league ("Glacierline
  Hockey Loop", team "Northhollow Ironwolves", NHI, #38d9a9) and
  football league ("Harvest Football Alliance", team "Gildmere
  Stormwrights", GST, #f97316) — demo mode must demo every sport.
- services/ics.py: sport-duration map gains hockey ≈3h, football ≈3.5h.

### Frontend (views owner)

- StandingsView: hockey columns # / Team / W / L / OTL / PTS; football
  columns # / Team / W / L / T / PCT. Branch on standings.sport like
  the existing soccer branch; group rendering unchanged.
- Onboarding wizard league step: verify new sports group correctly
  under proper headings ("Hockey", "Football") — fix the sport-label
  mapping if it hardcodes the original three.

## Phase 3: national teams, more soccer, whole-competition, notifications

Foundation already landed (do NOT redo): `League.follow_all`,
`TeamCompetitionORM`, `NotificationPrefORM`, migration for
`leagues.follow_all`, `CatalogLeagueOut.national`/`supports_follow_all`,
`FollowSelection.follow_all`, notification-pref schemas + TS types,
`repository.replace_followed(competitions=…)` / `list_team_competitions`
/ `list_follow_all_leagues`, `SportsProvider.get_competition_schedule`,
`upsert_league` persists follow_all. All ESPN facts below were verified
live (ROADMAP Phase 3 research).

### Phase 3a — catalog, national teams, whole-competition follow

**espn_catalog.py (catalog owner):**
- Add `national: bool = False` and `supports_follow_all: bool = False`
  to `CatalogLeague`; `setup_leagues` route maps them through (route
  already returns the model — just populate the new fields).
- Add the 17 verified soccer codes. National-team comps
  (`national=True, supports_follow_all=True`): `worldcup` (fifa.world),
  `womens-worldcup` (fifa.wwc), `euros` (uefa.euro), `nations-league`
  (uefa.nations), `copa-america` (conmebol.america). Club comps
  (`supports_follow_all=True`): `europa` (uefa.europa), `conference`
  (uefa.europa.conf), `club-world-cup` (fifa.cwc); also set
  `supports_follow_all=True` on the existing `ucl`. Domestic
  (plain): `championship` (eng.2), `scottish-prem` (sco.1), `eredivisie`
  (ned.1), `liga-portugal` (por.1), `super-lig` (tur.1), `liga-mx`
  (mex.1), `bundesliga-2` (ger.2), `laliga-2` (esp.2), `ligue-2`
  (fra.2). Keep ids stable/kebab and unique.
- `NATIONAL_TEAM_COMPETITIONS: dict[str, tuple[str,...]]` mapping each
  national catalog league id → the sibling catalog league ids a nation
  followed there should ALSO be scheduled from (e.g. `worldcup` →
  `("nations-league",)` is wrong for non-UEFA; keep it simple and
  CORRECT: map by the league itself — `euros`/`nations-league` →
  each other; `worldcup`/`womens-worldcup`/`copa-america` → just
  themselves). Expose `national_competition_siblings(league_id) ->
  list[CatalogLeague]`. Because ESPN national-team ids are GLOBAL, the
  same `provider_key` works in every sibling context.

**ESPN adapter (`espn/` package owner):**
- Implement `get_competition_schedule(league, start, end)`: ranged
  scoreboard `?dates=YYYYMMDD-YYYYMMDD&limit=400` (ET-bucketed dates),
  parse via the existing `_parse_scoreboard`, filter to [start,end].
  Chunk ranges >45 days by month to bound payload size. Soccer/tournament
  leagues only need this; other sports may also use it.

**routes/setup.py (route owner):**
- `setup_follow`: a selection with `follow_all=True` needs no teams →
  create the `domain.League(follow_all=True)` and skip team validation.
  A selection with teams works as today (follow_all defaults False).
  For teams in a `national` catalog league, also build `competitions`
  triples from `national_competition_siblings` (each sibling league must
  be upserted too, and the team's global provider_key reused). Pass
  `competitions` to `replace_followed`. A single follow request may mix
  team-follows and whole-competition follows.
- Validation: reject a selection that has neither teams nor follow_all
  (400). Unknown league still 400.

**scheduler/jobs.py (scheduler owner):**
- `refresh_schedules`: after the per-team loop, also fetch each team's
  `list_team_competitions` sibling leagues (via `get_schedule` with the
  stored provider_key) and merge. THEN loop `list_follow_all_leagues`
  and call `get_competition_schedule(league, start, end)` →
  `upsert_games` (window today-7d..today+45d as today). Per-league/team
  errors stay isolated. Whole-competition games have null team ids
  unless a side matches a followed team — that's fine.

**mock.py (mock owner):**
- Implement `get_competition_schedule` returning all of a league's games
  in the window (reuse the existing `_league_games`). Demo: add ONE
  fictional national-team-style league to DEMO_CONFIG marked... actually
  DEMO_CONFIG leagues are plain; instead the demo for follow_all is
  exercised by tests, not the demo install. Keep DEMO_CONFIG as-is.

**Frontend (wizard owner):**
- LeagueStep: render a distinct "National Teams" group (leagues with
  `national`) and keep domestic leagues under "Soccer". Leagues with
  `supports_follow_all` show a small "Follow whole competition" toggle on
  the league chip; when toggled on, that league is added to the follow
  request with `follow_all:true` and contributes NO team-picking step.
- TeamsStep: skip leagues that were follow_all-toggled.
- ReviewStep/submit: build `selections` mixing team picks and
  follow_all leagues. SyncingStep unchanged.
- Today/Calendar: games whose `followed_team_ids` is empty (whole-comp
  games) still render; color them by league (fallback color) and label
  with the league name. No per-team color needed.

### Phase 3b — notification preferences

Notifiable event types (ordered): `starting_soon`, `game_start`,
`period_start`, `intermission`, `final` (from `EventType`). 

**repository.py:** `get_notification_prefs(session) -> list[NotificationPrefORM]`,
`upsert_notification_pref(session, scope, muted=None, events=None)`
(merge — only overwrite provided fields), `resolve_should_notify(session,
event_type, *, team_ids, league_id) -> bool` (most-specific wins:
any `team:{id}` scope for the game's followed teams, else `league:{id}`,
else `global`; a muted scope blocks; an explicit event flag wins; default
True). Keep it a single cheap query set (load all prefs once per tick).

**services/notify_prefs.py (new):** pure helper
`decide(prefs_by_scope, event_type, team_ids, league_id) -> bool`
implementing the resolution, unit-tested exhaustively.

**scheduler/jobs.py:** in `live_tick`, before `_notify_once` for each
event, consult the prefs (load once per tick) using the game row's
team ids + league id; skip muted/disabled. starting_soon too.

**routes/notifications.py (new, under /api):**
- `GET /notifications/prefs` → `NotificationPrefsOut` (event_types +
  one pref per scope: global + every followed team + every followed
  league/competition, filling defaults for scopes with no stored row,
  labels from team/league names).
- `PUT /notifications/prefs` body `NotificationPrefUpdate` → upsert one
  scope, commit, return the updated `NotificationPrefsOut`.

**Follow defaults:** when `setup_follow`/`install_demo` writes the
followed set, seed prefs: followed teams → all events on (no row needed,
default is on); whole-competition leagues → store a `league:{id}` pref
with only `game_start`+`final` enabled (avoid 104-game spam). Do this in
the route/seed after `replace_followed`. **Amended 2026-10-10**: the seed
is written ONCE — when a league first becomes a whole-competition follow
and has no `league:{id}` row — not on every save, and it is removed again
when the league stops being one, if the row is still an untouched seed
(see *Editing the followed set* below).

**Frontend:** `api.ts`/`hooks.ts` add `notificationPrefs()` +
`updateNotificationPref()`. A new Settings view/panel (reached from the
Layout gear, as a second option alongside "Manage teams"): list scopes
with a mute toggle + per-event checkboxes; optimistic update via the PUT.
Keep dark kiosk styling.

## Phase 4: tennis + UFC (athlete following)

KEY DESIGN: **an athlete is a single-member "team."** Tennis matches and
UFC bouts reuse the existing Game/Standings/Today/Calendar/Results/News/
notification machinery unchanged — only provider adapters, catalog, mock,
and minor frontend labels are new. A followed player/fighter is a
`TeamORM` row whose `provider_key` is the ESPN athlete id.

Foundation already landed (do NOT redo): `Sport.TENNIS`/`Sport.MMA`,
`INDIVIDUAL_SPORTS`, `Game.series` + `GameORM.series` (+ migration) +
`GameOut.series` + types, `CatalogLeagueOut.entity_noun` (route derives
"player"/"fighter"/"team" from sport) + TS, `upsert_games`/`serialize`
persist series. All ESPN facts below verified live (ROADMAP Phase 4).

### espn_catalog.py (catalog owner)
- Add leagues: `atp` ("ATP Tour", tennis, "tennis/atp"), `wta`
  ("WTA Tour", tennis, "tennis/wta"), `ufc` ("UFC", mma, "mma/ufc").
  national=False, supports_follow_all=False.
- `get_league_teams` must special-case these (the normal `/teams`
  endpoint is empty for individual sports). Tennis: fetch the rankings
  endpoint and return the ranked players as `CatalogTeam`
  (provider_key=athlete id, name, abbreviation from a short name,
  logo_url=headshot if available). UFC: fetch a fighters/athletes list
  (verify the working endpoint live — likely the athletes index or
  rankings) and return fighters likewise. Cap to a sane number
  (e.g. top ~120) so the picker is usable. Defensive parsing; cache as
  today.

### ESPN adapter (`espn/` package owner)
- `_normalize_period` gains TENNIS (period→"Set {n}", no clock, no
  intermission) and MMA (period→"R{n}", clock from displayClock while
  live) branches. Keep the scheduled/postponed→(0,"",None,False) guard.
- The provider methods must work when the followed "team" is an athlete.
  `get_schedule(league, team, start, end)` for tennis/mma scans the
  scoreboard (tennis: events→groupings→competitions, filter
  competition.date to the window; mma: month-range card fetch) for
  competitions whose competitor ids include `team.provider_key`, and
  returns them as Games. Set the athlete's internal id on the matching
  side, opponent on the other. Populate `Game.series` (tennis: tournament
  name + round, e.g. "Wimbledon · QF"; mma: card name, e.g. "UFC 320").
  Game id `espn:{competition id}`. `get_live_games(league)` returns
  today's in-progress/near matches across the tour (used by live_tick;
  scanning the whole scoreboard is fine). `get_game_state` via the
  match/competition. `get_standings`: tennis → rankings as StandingRow
  (rank, team_name=player, points; group=tour); mma → empty Standings.
  `get_roster`: return an empty Roster for individual sports.
  `get_news`: per-athlete/league news (best-effort; empty ok).
- UFC fights have NO homeAway and NO per-fight start time: assign
  order 1→home, 2→away; use the card start time for every bout (document
  it). Tennis competitors DO carry homeAway.

### mock.py (mock owner)
- Add deterministic tennis + mma engines: tennis best-of-3 sets
  (period→"Set n", scores = sets won 0–2, no clock, final at 2 sets);
  mma up to 3 rounds (period→"R n", clock counts down, final by
  decision/finish via stable seed). `_VENUE_SUFFIX` ("Center" tennis,
  "Arena" mma — bouts are at a venue), `_POSITIONS` may stay empty for
  these (rosters are empty for individual sports — `get_roster` returns
  an empty Roster; do NOT KeyError). Populate `Game.series` with a
  fictional tournament/card name. `get_standings`: tennis → an 8-player
  ranking (rank/points, single group); mma → empty.
- DEMO_CONFIG: add a tennis league ("Coastline Tennis Series", a
  fictional player e.g. "Rowan Ashgrove", entity is a player) and an mma
  league ("Summit Fighting Championship", a fictional fighter e.g.
  "Cassius Dunmore"). After this, demo demos all 7 sports — RESTORE the
  tightened assertions in test_providers_mock.py
  (`test_demo_config_demos_every_sport`: 7 leagues / 8 teams, sport set
  == every Sport value) and add tennis/mma engine tests.

### Frontend (views owner)
- StandingsView: tennis branch → columns # / Player / Pts (from
  rank/team_name/points, single group ok); mma → standings has no rows,
  show a friendly "Rankings not available for this league" empty state.
- RosterView: individual-sport teams have empty rosters — show a
  graceful "No roster for individual sports" message instead of an empty
  table (detect via the team's league sport from useTeams()).
- GameCard / StatusBadge: when `game.series` is set, show it as a small
  context line (e.g. above the matchup): "Wimbledon · QF" / "UFC 320".
  Tennis/mma "scores" are set/round counts — the existing score display
  is fine. Period labels "Set 2"/"R3" already flow through.
- Onboarding picker noun: use `league.entity_noun` to label the teams
  step ("Pick your players"/"Pick your fighters"/"Pick your teams") and
  the chips. Sport headings: map "tennis"→"Tennis", "mma"→"MMA" (don't
  show "Mma").

## Phase 5: golf + the Event/leaderboard model

KEY DESIGN: golf is NOT two-sided — a tournament is ONE `Event` with a
field on a `leaderboard`. A followed golfer is a single-member `TeamORM`
(sport=golf, provider_key=athlete id, entity_noun "golfer"); the
scheduler fetches `Event`s for the golfer's tour and the leaderboard
marks followed golfers via `player_id`.

Foundation already landed (do NOT redo): `Sport.GOLF`,
`LEADERBOARD_SPORTS`, `domain.Event`/`LeaderRow`, `EventORM` (+ created
by create_all, no migration), `EventOut`/`LeaderRowOut`,
`TodayOut.events`, TS `SportEvent`/`LeaderRow`/`TodayResponse.events`,
`SportsProvider.get_events`/`get_event_state`, repository
`upsert_events`/`get_event`/`events_between`/`active_events` (+ EventORM
among the tables `replace_followed` clears — since 2026-10-10 only the
events of a league that left the followed set; see *Editing the followed
set* below), `serialize.event_to_out`/`events_to_out`,
`/today` returns active events, route derives entity_noun "golfer" for
golf. (Add "golfer" to the setup.py entity_noun derivation if not
already — currently only player/fighter/team; ADD golf→"golfer".)

### espn_catalog.py (catalog owner)
- Add `pga` league ("PGA Tour", golf, "golf/pga"). entity_noun derives
  to "golfer". `get_league_teams` for golf: fetch the field/rankings
  (verify live — the scoreboard's current-event competitors, or a
  golfers/rankings index) and return golfers as CatalogTeam
  (provider_key=athlete id, name, short abbr, headshot if present).
  Cap ~120.

### ESPN adapter (`espn/` package owner)
- Implement `get_events(league, start, end)` for golf: scoreboard /
  tournament schedule → `Event` per tournament with a populated
  `leaderboard` (LeaderRow: position from `order`, position_label "T3"
  with ties shared, name from athlete, score = score-to-par string,
  detail = "thru N"/"F"/round). phase from event status; round_label
  from `status.type.detail` ("Round 2"). Set `player_id` on a leader
  row ONLY when that athlete id matches a followed golfer — but the
  provider can't know followers; so leave player_id None in the provider
  and let the SCHEDULER tag followed golfers after fetch (it knows the
  followed set). Provider just returns the board with names+ids; carry
  the ESPN athlete id in a parseable way (use LeaderRow.player_id to
  carry the ESPN id transiently, scheduler rewrites it to internal id or
  None). DOCUMENT this. `get_event_state(league, key)` for a single
  tournament. Non-golf leagues: get_events returns []. Score is a string
  ("-10","E","+3") — keep as-is.
- Verify live against the real PGA feed (a tournament is active now).

### scheduler/jobs.py (scheduler owner)
- `refresh_schedules`: for each followed GOLF team, and for golf
  follow_all leagues, call `get_events(league, start, end)`; before
  upsert, map each LeaderRow.player_id (carrying the ESPN athlete id)
  to the internal followed-team id when it matches a followed golfer,
  else None. `upsert_events`.
- A new `events_tick` job (interval ~3 min, max_instances=1, coalesce):
  for everything `active_events` returns — in-progress events PLUS
  SCHEDULED events starting within the 30-min lookahead, so a
  single-day race's flips (including straight to FINAL) are observed
  live — call `get_event_state`, re-tag followed golfers,
  `upsert_events`. Notifications (modest, via notify + prefs):
  tournament FINAL with the followed golfer's finishing position. Use a
  dedupe key `"{event_id}:final"`; `active_events` never returns FINAL
  rows, so a fresh FINAL state is by definition a transition.
  Round-by-round is out of scope this phase. Register events_tick in
  setup_scheduler.

### mock.py (mock owner)
- Implement `get_events`/`get_event_state`: a deterministic fictional
  golf tournament (Thu–Sun span) with an ~8-golfer leaderboard that
  evolves with elapsed time (round_label Round 1→Final, scores to-par,
  positions sorted, "thru" detail), final after Sunday. Followed golfer
  appears in the field with their id on the row. DEMO_CONFIG: add a golf
  league ("Fairway Masters Tour", a fictional golfer e.g. "Soren
  Larkspur") so demo demos all 8 sports — RESTORE the tightened
  `test_demo_config_demos_every_sport` (8 leagues/9 teams, full Sport
  set). Add golf engine tests.

### routes (route owner)
- `routes/events.py` under /api: `GET /events?start=&end=` →
  `list[EventOut]` (events_between over a default window today-7d..+45d,
  local days like /schedule); `GET /events/{event_id}` → `EventOut`
  (404 unknown). Include the router.

### Frontend (views owner)
- New `EventsView.tsx` (a "Golf"/"Events" tab — add to Layout TABS):
  list active + upcoming events; each expands to its leaderboard table
  (Pos / Player / Score / Detail), followed golfers' rows highlighted by
  their team color. Use a new `useEvents()` hook + `api.events()`.
- TodayView: render `today.events` above or alongside games — a compact
  event card (name, round_label, top-3 + followed golfer's line).
- CalendarView: optionally show event spans (start..end) as multi-day
  background events — nice-to-have; at minimum don't break. **Landed
  2026-07-27**, as solid all-day spans rather than FullCalendar
  *background* events (a background fill is unreadable under the light /
  light themes and can't be clicked open) — see "MMA method of
  victory + leaderboard events on the calendar" for the shipped contract.
- Onboarding: golf league picker uses entity_noun "golfer" ("Pick your
  golfers"); sport heading "golf"→"Golf".
- Add `api.events(start,end)` + `api.event(id)` and `useEvents`,
  `useToday` already returns events (extend the type usage).

## Phase 6: volleyball (second provider — TheSportsDB)

HONEST DATA LIMITS (verified): TheSportsDB free tier (key "3") has only
~5 European volleyball competitions (CEV Men's/Women's European
Championship, European Volleyball League, CEV Challenge Cup) — NO
PlusLiga, NO VNL, NO SuperLega — with sparse, mostly finals-only
fixtures and national-team rosters that may be empty. We ship what
genuinely exists; mock demos volleyball richly; Google News (Phase 1,
`news_locale`) covers PlusLiga clubs for those who follow them by name.

Volleyball is two-sided and SET-scored: `home_score`/`away_score` = sets
won (e.g. 3–1); period = current set; period_label "Set {n}"; no clock.
It reuses the Game model (no new domain object).

Foundation already landed: `Sport.VOLLEYBALL` + TS union. The registry
exposes `register_provider`; build providers there.

### New provider `app/providers/thesportsdb.py` (provider owner)
`class TheSportsDbProvider`, `provider_id = "thesportsdb"`, implementing
the full SportsProvider protocol against `https://www.thesportsdb.com/api/v1/json/3/`.
Pure parser functions over fetched JSON (like the ESPN adapter), lazy httpx
client, `close()`. `League.provider_key` is the TheSportsDB league id;
`Team.provider_key` the TheSportsDB team id.
- `get_schedule(league, team, start, end)`: `eventspastleague.php?id={key}`
  + `eventsnextleague.php?id={key}`, filter to the team and window. Parse
  `dateEvent`+`strTime` as the start (TheSportsDB times are UTC-ish —
  treat as UTC; document the assumption), `intHomeScore`/`intAwayScore`
  as sets won, `strStatus`/`strProgress` → phase (FT/Match Finished →
  final; NS → scheduled; otherwise in_progress). Game id
  `thesportsdb:{idEvent}`.
- `get_live_games(league)`: today's events for the league (free tier is
  largely finals-after-the-fact — that's acceptable; return what's there
  with state).
- `get_game_state(league, key)`: `lookupevent.php?id={key}`.
- `get_standings(league)`: `lookuptable.php?l={key}&s={season}` →
  StandingRow (wins/losses/points; group=None).
- `get_roster(league, team)`: `lookup_all_players.php?id={teamKey}` →
  Players (may be empty — return empty Roster, never crash).
- `get_events`/`get_event_state`: return `[]`/`None` (not a leaderboard
  sport). `get_news`: `[]` (Google News covers it).
- Defensive throughout: a *sparse* or *not-found* response (empty body,
  404, missing keys) must degrade to an empty/`None` result, never crash.
  A *sustained transient outage* (timeout / connection error / 429 / 5xx
  surviving every retry), however, surfaces as
  `http_util.TransientProviderError` — it propagates through the provider up
  to the registry's circuit-breaker guard so a down/rate-limited source can
  fail fast instead of being hammered. Every route/scheduler caller already
  wraps guarded-provider calls in `except Exception` and degrades, so this
  never reaches the client. (ESPN already behaves this way; this aligns
  TheSportsDB with it.)
Register it in `registry.py`.

### Catalog dispatch (catalog owner)
`espn_catalog.get_league_teams` must route by provider: keep ESPN logic
for `provider == "espn"`; for `provider == "thesportsdb"` fetch teams via
TheSportsDB (`lookup_all_teams.php?id={key}` or `search_all_teams.php`).
Cleanest: add a `tsdb_catalog.py` with `get_tsdb_league_teams(league)`
and have the setup route / `get_league_teams` dispatch on
`league.provider`. Add ~3 volleyball CatalogLeagues (provider
"thesportsdb", verified ids: CEV Men's European Championship, CEV
Women's European Championship, European Volleyball League) with the real
TheSportsDB league ids you confirm live. entity_noun "team".

### Mock (mock owner)
- Volleyball engine: best-of-5 sets (first to 3 sets; per-set races
  loosely to 25; score = sets won; period→"Set n"; final at 3 sets).
  `_VENUE_SUFFIX` "Hall". DEMO_CONFIG: add a volleyball league ("Tidewater
  Volleyball League", team "Saltmarsh Spikers") so demo demos all 9
  sports — RESTORE the tightened `test_demo_config_demos_every_sport`
  (9 leagues/10 teams, full Sport set). Add a volleyball engine test.

### Frontend (views owner)
- StandingsView: volleyball columns # / Team / W / L / Pts (reuse the
  points-style branch). StatusBadge/GameCard: "Set n" labels flow through;
  score = sets won displays fine. Sport heading "volleyball"→"Volleyball".
  Onboarding picker noun stays "team".

### Verification notes
TheSportsDB free tier is rate-limited and sparse — live smoke should
tolerate empty schedules/rosters (assert NO CRASH + correct shapes, not
specific match counts). Demo mode is the rich volleyball showcase. Mock
provider + Google News mean volleyball teams are always followable even
where TheSportsDB has no data.

## Phase 7a: kiosk polish, PWA, .ics subscribe, results chips, backups

All additive; no domain-model changes. Keep dark kiosk styling, full
type hints, and the never-raise discipline.

### Per-team .ics subscribe (calendar owner)
- `app/routes/calendar.py`: accept optional `?team_id=` on `/calendar.ics`
  — when set, filter `games_between` to that team (use the existing
  `team_id` arg) and set the download filename to
  `sportsdash-{team_id}.ics`; 404 unknown team. All-games behavior
  unchanged when absent.
- (Layout superseded on 2026-10-11 by "Box Score: the Calendar view": the
  export is now the last item of the Subscribe menu.)
- Frontend CalendarView: alongside "Export .ics", a "Subscribe"
  control offering a `webcal://{host}/api/calendar.ics` URL (all) and a
  per-followed-team `webcal://.../api/calendar.ics?team_id={id}` — a small
  dropdown/list with copy-to-clipboard. `webcal://` uses the current
  `window.location.host`. (Subscribing keeps the calendar live in Apple/
  Google Calendar.)

### Kiosk auto-rotation + idle screen (scaffold owner)
- Layout: a small "kiosk" toggle (monitor icon) next to the gear. When
  on, the active tab auto-advances through TABS every ~20s (pausable on
  any user interaction for ~60s, then resumes). Persist the on/off in
  localStorage (`sportsdash.kiosk`).
- An idle full-screen clock overlay: after ~90s of no interaction, show a
  large local time + date + a compact "N live / N upcoming today" line
  (from `useToday`); dismiss on any interaction. Only when kiosk mode is
  on. New `src/components/KioskClock.tsx` + a small `useIdle(ms)` hook in
  `src/lib/`.

### PWA installability (scaffold owner)
- `frontend/public/manifest.webmanifest` (name "SportsDash", short_name,
  dark theme_color `#09090b`, background `#09090b`, display "standalone",
  start_url "/", icons — generate simple SVG-based icons or a single
  maskable icon in `public/`). Link it + apple-touch + theme-color meta
  in `index.html`. A minimal service worker (`public/sw.js`) registered
  from `main.tsx` that does an app-shell/offline fallback cache (keep it
  simple and safe — network-first, never serve stale `/api`). Don't break
  dev (`bun run dev`) or the build.

### Results streak / last-10 chips (tables/news owner)
- ResultsView: above the list, compute from the selected team's results
  (newest-first) a current W/L streak chip (e.g. "W3") and a last-10
  record ("7-3", with draws as "7-2-1" for draw sports). Win/loss is from
  the selected team's perspective (reuse the existing per-row W/L logic).
  Pure client-side over the data already fetched.

### Ops: backups + health deep-check (infra owner)
- `app/routes/health.py`: extend `GET /health` to a deep check —
  `{"status": "ok"|"degraded", "database": bool, "providers": int}` by
  doing a trivial DB `SELECT 1` (degraded+200 if it fails, never 500) and
  counting `registry` providers. Keep `status:"ok"` shape backward
  compatible (status key still present).
- docker-compose: add a `backup` service (postgres:16-alpine, same
  network) running a small loop that `pg_dump`s the db to a mounted
  `./backups` volume daily and prunes dumps older than 14 days; document
  it. Must not block the stack if it fails (restart unless-stopped).
- README: short "Backups" + "Kiosk mode" + "Subscribe to your calendar"
  sections.

## Phase 7b: game detail / box-score drill-down

- `domain.GameSummary` + `schemas.GameSummaryOut`/TS: per-period line
  scores (both sides) + optional top performers/scoring summary; built
  from provider summary endpoints, fetched ON DEMAND (not stored).
- `SportsProvider.get_game_summary(league, provider_game_key) ->
  GameSummary | None` (ESPN: parse the summary endpoint's boxscore/
  linescores per sport; mock: deterministic from the game's state;
  TheSportsDB: None ok). `GET /api/games/{game_id}` → GameOut +
  on-demand summary (or a separate `/api/games/{id}/summary`).
- Frontend: clicking a GameCard opens a detail modal with the line-score
  grid + any performers; loading/empty states; ESC/backdrop close.

## Phase 8: theme system + kiosk fix

### Theming approach (KEY — do NOT refactor all 23 components)
Tailwind v4 compiles `bg-zinc-900`/`text-zinc-400`/`text-amber-400`/etc. to
`var(--color-zinc-900)` (the palette lives in CSS vars). So themes are
implemented by REMAPPING those palette vars under a `[data-theme]`
attribute on `<html>` — components keep their existing utility classes
and re-theme automatically. Only targeted files change.

- `src/index.css`: define theme blocks. Default (`:root` / no attribute /
  `[data-theme="dark"]`) keeps today's look (Tailwind zinc defaults — can
  leave unset). Add `[data-theme="light"]`, `[data-theme="custom"]`,
  `[data-theme="contrast"]` each overriding the ramp the app actually uses:
  `--color-zinc-950/900/800/700/600/500/400/300/200/100` (background→text
  scale) plus accent `--color-amber-300/400/500` and status `--color-red-*`,
  `--color-emerald-*`. Set `color-scheme` per theme (light vs dark) so
  scrollbars/form controls follow. Pick tasteful, WCAG-AA-contrast ramps:
  - light: near-white bgs (zinc-950→#f8fafc … zinc-900→#f1f5f9), dark text
    (zinc-100→#18181b … zinc-400→#52525b), amber accent kept readable.
  - custom: dark base (keep dark ramp) but accent driven by a
    `--sd-accent` var — remap `--color-amber-400`(and 300/500) to
    `var(--sd-accent, <default>)`. A small effect sets `--sd-accent` from
    the team being viewed (see below); default to a vibrant amber. The
    team colour is passed through `accentOnDark` first, which lifts a dark
    club colour (navy, maroon) toward a readable tone — without it a navy
    club renders at ~1.3:1 on the base.
  - contrast: an accessibility option, not a look — true black backdrop,
    white headings, every text token at WCAG AAA (7:1) against both the
    backdrop and a card surface. Borders are deliberately lighter than the
    other dark themes (#3a3a3a, not #27272a): at a true-black base a
    hairline at the usual value is invisible.
  Retired 2026-08-19: `newsprint`, `floodlight`, `ember`. A stored id for
  any of them must MIGRATE rather than fail validation (see
  `LEGACY_THEME_IDS` in `src/lib/theme.ts`, mirrored in the `index.html`
  bootstrap), or a returning user is silently reset to the default.
- `src/calendar.css`: change the hardcoded `--fc-*` hex values to reference
  the palette vars (e.g. `--fc-page-bg-color: var(--color-zinc-950)`), so
  FullCalendar re-themes with the rest. (`src/calendar.css` was deleted on
  2026-10-11 with FullCalendar: see "Box Score: the Calendar view".)
### Setup: the country drill-down

`GET /api/setup/leagues` carries two things the wizard's first step needs:
each league's `country_code`, and a `countries[]` list of `{code, name,
league_count}`.

- `code` is **ESPN's league-code prefix, not an ISO code**. ESPN's soccer
  keys are `soccer/eng.1`, `soccer/sco.1`, `soccer/ned.1` — "eng", "sco" and
  "ned" are not ISO-3166, which is exactly why the mapping is a written
  table in `espn_catalog.COUNTRIES` rather than a derivation. Guessing would
  be wrong for the countries a soccer fan cares most about.
- A code absent from that table (`uefa`, `fifa`, `conmebol`, `concacaf`) is
  a confederation, so its leagues carry `country_code: null` and are offered
  regardless of what the map has selected. So does every sport not organised
  by country.
- `countries[]` lists ONLY countries with at least one league — one that
  opens an empty list is worse than none — and `league_count` must equal the
  number of leagues actually carrying that code.
- The step renders as a GRID, not a map. It was an OpenStreetMap pin map
  briefly and the pins did not work: nine of the twenty-one countries sit
  within a few degrees of each other, so labels overlapped and had to hide
  below a zoom threshold, leaving anonymous dots. Pin coordinates were
  dropped from this payload with the map (2026-08-20); re-adding a map means
  re-adding them AND solving the collision, which is why the Map view
  clusters.
- The wizard shows the step only when some sport spans MORE than one country
  (`countryStepApplies`), so the step turns itself on when a catalog gains a
  second country for a sport and off again when it does not — today soccer
  alone qualifies. Selecting nothing is valid and means "no narrowing".

- Theme switcher: persisted in localStorage `sportsdash.theme` (values
  `dark|light|custom|contrast`, default `dark`). Apply `data-theme` to
  `document.documentElement` as early as possible (an inline script in
  `index.html` head, or first thing in `main.tsx`) to avoid a flash.
  Expose a switcher control in SettingsView (the gear → Settings panel
  gets an "Appearance" section) AND a quick theme button in the header
  next to the gear is optional. Keep `types`/api untouched (pure client
  state).
- Stadium dynamic accent: a tiny `ThemeContext`/effect that, only when
  theme==="stadium", sets `--sd-accent` on `document.documentElement` to
  the color of the team currently in focus (e.g. the selected team in
  Roster/Results/Standings, or the first followed team on Today). Keep it
  simple and safe; default accent when none.

### Box Score theming (2026-10-10 — SUPERSEDES the theming rules above)

The "B · Box Score" redesign replaces the palette remap as the way the app
is coloured. Where this section and the Phase 8 text above disagree, this
section wins; the Phase 8 text is kept as history.

- **Tokens, not palettes.** `src/index.css` defines semantic tokens
  (`--bs-ground`, `--bs-sheet`, `--bs-tint`, `--bs-rule`, `--bs-hair`,
  `--bs-ink`, `--bs-ink-2`, `--bs-muted`, `--bs-spot`, `--bs-on-spot`,
  `--bs-spot-text`, `--bs-block`, `--bs-on-block`, `--bs-tab`, `--bs-band`,
  `--bs-alert`, `--bs-draw` and their companions) and exposes them as
  Tailwind utilities (`bg-sheet`, `text-ink`, `border-rule`, …) through
  `@theme inline`. New code uses those utilities and the `bs-*` component
  classes; a theme is a token swap, never a second set of components.
- **The legacy ramp is a bridge.** `--color-zinc-*`, `--color-amber-*`,
  `--color-red-*`, `--color-emerald-*` (and the few sky/rose/orange stops in
  use) are pointed at the tokens so un-migrated components follow every
  theme. Amber resolves to ink: Box Score has no accent text. A family is
  deleted from that block when nothing uses it.
- **Two persisted choices.** `localStorage["sportsdash.theme"]` is the BASE:
  `light | dark | contrast`, default `light`. `localStorage["sportsdash.palette"]`
  is the COLOURS: `brand | team`, default `brand`. They land on `<html>` as
  `data-theme` and `data-palette`. Team applies on the light and dark bases
  only; High contrast keeps its own spot so its AAA guarantee holds.
- **Every id ever stored still resolves** (`STORED_THEME_IDS` in
  `src/lib/theme.ts`, mirrored by the `STORED` table in the `index.html`
  bootstrap; `theme.test.ts` fails if the two drift): `light`, `dark`,
  `contrast` keep their base; `custom` and `stadium` become `dark` + `team`;
  `floodlight` and `ember` become `dark`; `newsprint` becomes `light`. An
  explicit stored palette wins over the one an old id implies. Nothing
  stored means `light` + `brand`.
- **Favourite team.** `localStorage["sportsdash.favoriteTeam"]` holds a
  followed team's id. Unset, or pointing at a team no longer followed, it
  falls back to the first followed team with a colour (`pickFavoriteTeam`).
- **Team palette.** The API carries ONE colour per team, so only the spot
  is recoloured; section tabs, the current nav item and the score line stay
  ink. `teamAccentVars` measures the colour against the base and sets
  `--sd-accent`, `--sd-on-accent`, `--sd-accent-text`, `--sd-accent-edge`
  and `--sd-accent-edge-block` on `<html>`. Text on the fill is ink or white
  by measured contrast. A colour too dark to see on the dark base has its
  lightness raised (hue and saturation kept) to 3:1 against the sheet; a
  pale colour on the light base is left as it is and keylined. The result is
  cached in `localStorage["sportsdash.teamAccent"]` so the bootstrap can
  paint it on the first frame.
- **Contrast floors, enforced by tests.** Every text token clears 4.5:1 on
  every surface it is used on in Light and Dark, and 7:1 in High contrast;
  the Team palette clears 4.5:1 for every colour in a 4,096-colour sweep.
- **Radius is zero.** The Tailwind radius scale is flattened in `@theme`.
- **Fonts.** Barlow (400/500/600/700) is `--font-sans`; Barlow Condensed
  (500/600/700) is `--font-condensed`. Both are self-hosted woff2 in
  `public/fonts` (latin + latin-ext), because the Tauri build has no network
  font loader.
- **Kiosk scale.** Kiosk mode sets `data-kiosk` on `<html>`, which grows the
  root font size with the viewport (`clamp(100%, 100vw / 90, 175%)`), so the
  1440px design fills a wall display. Sizes are therefore written in rem;
  rule weights stay in px.
- **PWA colours** follow the light default: `theme_color` `#ffffff`,
  `background_color` `#f1f2f4`. The `theme-color` and `color-scheme` metas
  are rewritten per base by the bootstrap and by `applyAppearance`.

### Box Score: the Today view (2026-10-10 — SUPERSEDES the Today layout notes above)

Today is laid out by phase instead of as one chronological list. Nothing
about the API changed; the view only asks for more of what already exists.

- **Sections.** Day header with live / upcoming / final counts. Left
  column: `Live now` (one hero, the first live game a followed team is in,
  else the first live game; other live games as `Also live` rows under it),
  the games still to come (`Tonight` when every one starts at 5 PM or later,
  else `Later today`; postponed and canceled games stay here with the status
  in place of the time), `Final today`, yesterday's finals (`Last night` /
  `Yesterday` by the same rule), then `Leaderboards` (golf, racing). Right
  column: a standings box per followed league, then `On this day`. Full
  width: `Next 7 days`.
- **Every game row is one button** that opens `GameDetailModal`, as before.
  Its accessible name carries the state and score, because the name replaces
  the row's content for a screen reader.
- **Requests, beyond `/today`, `/teams`, `/history/on-this-day` and the
  batched `/odds`:** one `/schedule` for yesterday through day +6 (last
  night and the week strip), one `/events` for the same week, `/standings`
  per followed league that has a team table (at most three),
  `/results/{team}?limit=5` per followed team shown in a box, and
  `/games/{id}` for the hero only. Today never unmounts, so the schedule,
  standings and results refresh every five minutes (opt-in
  `refetchInterval` on `useSchedule` / `useStandings` / `useResults`; other
  callers are unchanged). The hero's box score is polled while the game is
  live AND Today is the tab on screen (`useGameDetail(id, { enabled })`,
  `useIsActiveTab`): each poll costs the server three uncached provider
  calls.
- **Going to another tab from a view** uses `NavigateContext`:
  `navigate(tab, { leagueId?, teamId? })`. The destination reads
  `useNavigationRequest(tab)` and applies it once per `nonce`. The jump
  scrolls to the top and moves focus to `<main id="sd-main">`.
- **A break in play is said as one** (`lib/periodLabel.ts`): "Half time" in
  soccer and after the second quarter, "End of Q1" elsewhere, with no clock
  beside it. Baseball's own labels are written out ("Bottom 5th").
- **Layout inside a column is chosen by the column's width** (container
  queries), not the window's: the game lists switch to their columned form
  at 56rem of their own width and restack below it.
- **Days are the server's.** Day 0 is `/today`'s `date`; games are placed on
  days with `dateKeyInZone(start_time, today.timezone)`.
- **Left out because the API does not carry them** (never faked): hits and
  errors in the line score, runners on base, outs and the count, the marker
  for a half-inning in progress, regular-season series text, weather for a
  live game, short city-style team names, a second team colour. "Play-by-play"
  has no destination of its own, so the hero offers only "Box score".
- **The live win probability** is the last point of
  `summary.win_probability` only. The pre-game projection from `/odds` is
  shown for games still to start and is never relabelled as a live number.
- **A side with no colour** gets the neutral block. A followed side falls
  back to its `/teams` colour, because games reached through a followed
  team's schedule carry no colour on either side.
- **Results are told by fill:** W solid, L outlined, D a mid-tone fill, from
  the followed side's point of view; no square when both sides or neither
  are followed. A fight's level round count is not a draw unless its method
  says so.
- **The standings box** shows each division (or single table) that holds a
  followed team; a table longer than eight rows is cut to a seven-row window
  around that team. Team sports only.
- Pure logic lives in `views/today/model.ts` and is unit-tested there.

### Box Score: the Results view (2026-10-10 — SUPERSEDES the Results layout notes elsewhere)

One followed team's season, as mocked: the page head with the team and
season controls, a strip of key figures, one results table per month, and
a `Next up` strip. Nothing about the API changed.

- **Team control.** A toggle button per followed team, each with its block;
  past six followed teams the shared `Select` takes over. Another view can
  ask for a team: `navigate("results", { teamId })`.
- **Key figures.** Season record (written the sport's way: W-L, W-D-L for
  soccer, W-L-OTL for hockey), the last 8 results with their form strip,
  the streak, scored and allowed over the last 8 in the sport's own word
  (runs, goals, points), and the place in the table with a second figure
  (games back where the sport ranks by it, otherwise points or win
  percentage). A tile whose data the sport does not have is not drawn (no
  scored/allowed or record for MMA, no record for tennis).
- **"Current season" is the whole season in play:** the provider's season
  list (`/history/results/{team}?season=K`) with the stored results laid
  over it. Stored results alone have holes whenever the app was off, and a
  hole silently corrupts the streak and the last-8 figures. There is ONE
  season key, read from the live standings label (`seasonKey` in the
  model); the other year of a two-year label is never tried, because the
  backend answers "no such season" and a provider failure alike and the
  wrong year would file last season's games under this one. With no label
  and no safe guess there is no key, and the stored results stand alone.
  - The stored results wait at most 2 s for that list (so a streak the
    list is about to correct is not flashed first); after that they go up
    alone and the list is merged in when it lands.
  - A 404 for the list is an answer and is not retried; any other failure
    of the season in play is retried once, then asked for again every
    10 minutes while the tab is visible. A past season is asked for once.
  - The Season control names the season in play by its label ("2026-27"),
    not "Current season".
- **Standings are never requested by season** from this view: that request
  stores the table for good and the browser cannot always tell which year
  is still in play. A past season therefore shows three tiles, not five.
- **Rows are written from the selected team's side:** vs / at, the
  opponent's block and full name, the result square, own score first. A
  "Box score" button appears only on games the API has stored
  (`/games/{id}` is a 404 for the rest). A fight shows its method of
  victory instead of round counts.
- **Requests:** `/results/{team}`, the season list above, `/standings/{league}`
  (live only), `/schedule/{team}` from yesterday to +45 days, and the shared
  `/today` query. When Today reports one of the team's games final and the
  results do not have it yet, they are refetched once. While the schedule
  holds a live game that Today no longer carries (one running past
  midnight), the schedule is re-read every 60 s.
- **Left out because the API does not carry them:** per-game pitchers,
  attendance and duration; short team names; a second team colour; a
  neutral-site marker.
- Pure logic lives in `views/results/model.ts` and is unit-tested there.

### Box Score: the League view (2026-10-10 — SUPERSEDES the League layout notes elsewhere)

One page with one league picker and one season picker for its three
sub-views (they used to keep their own), and a tablist: Standings, Leaders,
Bracket. Nothing about the API changed.

- **Standings is the mocked page:** the conference toggle and legend, the
  tables with team blocks, the `Leader` tag and a note beside each tab
  ("X ahead by N games", or points for sports ranked on them), the top ten
  of the league's stat board with a bar measured against the leader, and a
  panel for a followed team (record, place, recent form, next up, recent
  results) with a switcher when several followed teams are in the league.
  A league with conferences AND divisions shows one conference at a time,
  opening on the one that holds a followed team; flat groups are all shown.
  A single table (every soccer league) puts the leaders block and the
  panel beside it rather than under it.
- **Leaders and Bracket are restyled, not re-laid-out.** Bracket shows the
  page's league; a league with no bracket says so. On a past season those
  two sub-views say "current season only": `/leaders`, `/scorers` and
  `/bracket` take no season.
- **Another view can ask for a league:** `navigate("league", { leagueId,
  group? })` selects it once and opens Standings; `group` names the
  conference to open on (Today's "Full standings" passes the one its box
  shows), and without it the view picks its own.
- **Team colours for rows that carry none** come from the setup catalog
  (`/setup/teams/{league}`, ESPN team sports only), joined by name or code
  with the standings and `/teams`. Standings rows carry no colour of their
  own.
- **Requests:** `/standings/{league}` on every sub-view (it names the page),
  `/leaders` and `/scorers` on Standings and Leaders, `/bracket` on Bracket,
  `/setup/teams/{league}`, and for the panel `/results/{team}?limit=5` and
  `/schedule/{team}`, both re-read every 60 s while the panel is up.
- The tablist keeps `role="tab"` buttons named Standings / Leaders /
  Bracket and the league picker stays `button[aria-label="Choose league"]`
  with `role="option"` items: the screenshot script finds them that way.
- **Left out because the API does not carry them:** short city names in
  the notes, a readable stat name beyond the three boards the app knows,
  an "updated" time on Leaders and Bracket, player pages behind leader rows.
- Pure logic lives in `views/league/model.ts` and is unit-tested there.

### Box Score: the Calendar view (2026-10-11 — SUPERSEDES "Calendar view" above and every FullCalendar note elsewhere)

Built by hand, as mocked: the range and its controls beside the title, the
week as seven day columns of score cards, then `Coming up` and a `Legend`.
FullCalendar, its hour-by-hour week, `src/calendar.css` and the four
`@fullcalendar/*` packages are gone. Nothing about the API changed.

- **Three views behind one tablist** (`role="tab"` buttons Week / Month /
  Agenda; Week is the default and the mocked page). Month is a grid of days
  in the same language: a pair of blocks and a time or score per game, four
  at most, then "+N more". A day there is a button that CHOOSES the day, and
  that day's games are listed in full beside the grid, or under it: under
  it, at full width, where there is no room beside, and also whenever some
  day of the month holds more than four rows (`busiestDayRows`; the
  month's busiest day, not the chosen one, so the grid does not change
  width as days are chosen). Agenda is the range on screen as a plain list.
  On a narrow sheet (container queries, not the window) the week is a list
  of days and a month's cell holds a square per game (three at most; a
  busier day shows two and a "+", which fit on one line in a 390px
  phone's cell). Going from Month back to
  Week opens the week of the day chosen in the month, else the week the
  month was opened from (`weekFromMonth`).
- **The week starts on Monday:** `WEEK_STARTS_ON` in
  `views/calendar/model.ts` is the one place that says so.
- **Days.** A game sits on the VIEWER's local day it starts on, and "today"
  is the viewer's clock, read every 30 s, so a card's day and its start time
  are in one zone. (Today and Results take the server's day.) A tournament's
  days are the SERVER's (`/meta` timezone), as in the `.ics` feed. Until the
  user steps to another range the view follows today, so a kiosk left on
  "this week" moves to the new week by itself at midnight.
- **Every card and list row is one button** that opens `GameDetailModal`; a
  tournament's bar or row opens `EventLeaderboardModal`. The button's name
  replaces its content, so it carries all of it: the state, the score, the
  result, the venue, the competition or round, and the forecast. A
  tournament's list row adds which day of it the row is, the venue and the
  followed athletes' places, so its rows on different days have different
  names. Dates and start times are `<time dateTime>` elements.
- **A card's rules beyond the mockup.** Postponed / Canceled stand in place
  of the time, with no score and no square, and are in none of the three
  counters (a line under them says how many). A draw is a `D` square. A game
  between two followed teams has no square until the team filter picks a
  side. A game no followed team is in carries its competition's name.
  Tennis and MMA sides have no Away / Home, and a finished fight shows "Won"
  and how it ended, never a round count. A day with more than three games
  draws compact cards; no game is ever left out of a week.
- **Tournaments (golf, racing)** are a band of bars under the day headers,
  one lane per overlap, bars across a month's weeks, and rows in the lists.
  A team filter hides them, and golfers and drivers are not offered by the
  filter or as per-team feeds; a note says so. A list shows a tournament on
  each day it runs, and the row is about THAT day (`eventRow`): the Live
  chip, the round and the followed athletes' places stand only on the day
  being played; its other days say "Day N of M". A place or a score is
  read from a board only once the tournament is in progress or final:
  before the first round the backend's board is the entry list (every score
  level, numbered in the order it came), so a row shows the followed name
  alone and the Legend says "In the field at ...", never "T37 at ...".
  While `/events` has not arrived or has failed, the Legend's athlete rows
  say so ("Loading tournaments…" / "Couldn't load tournaments") instead of
  "Not on a leaderboard", and an otherwise empty Coming up takes its
  loading or failed state (with Retry) from that request.
- **Controls:** on the first line Previous / the range / Next / Today and
  the three counters, as mocked; on the second the view tabs, the team
  filter (the shared `Select`, `button[aria-label="Filter by team"]`),
  Refresh (a 44px icon button that re-reads what is stored; its icon turns
  only where motion is allowed) and Subscribe, a menu of `webcal://` feeds
  (all follows, then one per team) each with Copy, plus "Download .ics
  file", which used to be a button of its own. In the menu Enter or Space
  act on an item and Escape closes it wherever focus is. Where the
  controls stand must not depend on how long the range's name is: from
  40rem up the name has a slot as wide as the longest week of a year, and
  below that its line is the full width with the name centred between the
  two arrows, and the counters are a line of their own. The counters stay
  drawn while a range is on its way, with dashes for their figures, so
  nothing under them moves. One word names the games everywhere on the
  page ("games" / "matches" / "bouts", `wordsForRange`): what the range
  holds, or in an empty range what the followed competitions play.
- **Requests:** `/schedule?start&end` and `/events?start&end` for the range
  padded by a day on each side (the two zones can differ), the same pair for
  the seven days of Coming up, `/schedule/weather` for scheduled outdoor
  games, and the shared `/teams`, `/meta` and `/today`. Every count on the
  page is taken AFTER the padding is cut away. The polled `/today` payload
  is laid over the schedule by game id, which is what turns a card live. The
  schedule itself is re-read every 60 s only while a game on screen is live
  and Calendar is the tab showing, once when a live game ends, at local
  midnight, and on coming back to the tab after five minutes. A batch of
  forecasts is read again once it is 30 minutes old, checked on the 30 s
  clock while Calendar is the tab showing (so a kiosk does not show
  Tuesday's forecast on Friday).
- **For scripts:** the grid on screen carries `data-calendar-grid="week" |
  "month" | "agenda"` (the screenshot script waits for the week), and a
  month's days are `button[data-day="YYYY-MM-DD"]`.
- **Left out because the API does not carry them:** a team's city on its
  own and short team names (the notes and the Legend use full names), a
  second team colour, the long form of a period for sports other than
  baseball, a neutral-site flag (Away / Home is by position), and the
  weather at game time (only the day's high and chance of precipitation
  are shown). That day is the UTC date of the start
  (`services/game_detail.py`), which for an evening game in the Americas is
  mostly the day after at the venue; the view cannot tell which case it is
  in, so the line is named "Forecast", not "Game-day forecast". The venue's
  own date would be backend work.
- Pure logic lives in `views/calendar/model.ts` and is unit-tested there;
  `eventSpans.ts` and `gameSideFollows.ts` beside it keep their own suites.

### Box Score: the Matchup view (2026-10-11 — SUPERSEDES the Matchup layout notes elsewhere)

One game at a time, previewed or followed live, as mocked: the two sides
with their records and the score, each side's chance to win, the upset
radar, the two sides' meetings, the followed side's last results, and where
both stand in the table. Kept from the old view and restyled: the betting
lines, projected lineups and injuries. A section with no data is not drawn.
Nothing about the API changed.

- **Choosing a game.** On offer are the games in progress, then those still
  to start over the next 14 days, soonest first. Three get a button each
  (four when there are only four) and the rest go in the shared `Select`
  (`button[aria-label="Choose another game"]`); the game on screen always
  has a button. With no choice made the page shows the first on offer and
  moves on when that game ends, which is what a kiosk shows. A game the
  reader chose stays on screen when it ends or is called off, while it is
  within 12 hours of its start either way: a game called off days ahead
  does not hold the page until its day comes.
- **Whose day.** Which games are on offer goes by the server's day
  (`/today`'s `date` and `timezone`). The day words go by the viewer's own
  day, the one the clock times are printed in: "Tonight", "Today" and
  "Tomorrow" in the tab and the status block, and a bare start time in a
  picker button, a dropdown option or a radar card (any other day shows its
  date). The two days differ for an hour or more every night when the
  server and the viewer are in different zones, and a game at 7:00 PM
  tomorrow must never read "Tonight 7:00 PM".
- **The forecast.** `Weather.temperature`, `condition` and `wind_speed` are
  the venue's weather at the moment the server asked; only `high`, `low`
  and `precip_chance` are a day's forecast, and that day is the UTC date
  the game starts in (`services/game_detail.py`), which for an evening game
  in the Americas is mostly the day after at the venue. The fact cell is
  therefore "Forecast", the Calendar's word for the same figures, never
  "Game-day forecast" or "Weather". It prints those three alone ("High 88°F
  · Low 63°F · 5% chance of precipitation") and only for a game still
  scheduled: a copy fetched before the start is not shown once the game is
  live or over.
- **Win probability.** "Before the start" is `GameOdds.home_win_pct /
  away_win_pct`, the provider's pre-game projection. It was checked against
  the live feeds on 2026-10-11 and does not move during or after a game, so
  it keeps that label in every phase. "Right now" is only ever the last
  point of `GameSummary.win_probability`, and only while the game is in
  progress. When the feed sends one side's figure the other is its
  complement in baseball and basketball only. The betting lines are a strip
  at the foot of the sheet, and stand alone as "Betting lines" when there is
  no probability row. They are the sportsbook's pre-game lines and do not
  move with the score, so once the game is live or over that section's note
  reads "What the sportsbook priced before the start". The notes say
  "player" or "fighter" where the sides are people. The bar is in the two
  teams' colours unless one is
  missing, would dissolve into the sheet, or the two are alike; then the
  favoured share is solid and the other outlined.
- **Upset radar** (`components/UpsetRadar`) flags two things and says so
  under the cards: the model's favourite is the betting underdog (the signal
  it always had), and the side in front of a live game was given under 50%
  before the start. A card is a button that puts its game on screen. The
  underdog's percentage is the one the "Before the start" row prints, and a
  side that rounds to 50% is not flagged. The odds batch is read again
  every 10 minutes while Matchup is the tab on screen, so a projection
  published after the page opened still reaches the radar. The batch is
  keyed by the list of games; when the list changes (a game ends, the day
  turns) the last list's odds stand in until the new ones arrive (the
  freshness keeper's key default for `game-odds`; the hook itself must set
  no `placeholderData`), so the cards do not give way to "Scanning the
  slate" and come back.
- **Meetings are not called a season series:** the API cannot scope them to
  a season. The newest three are drawn in full and tallied, older ones
  follow in a compact list, and the cross-season record closes the sheet.
  A meeting with no result is listed but not counted, and the tally then
  says so ("over two of the three meetings below").
  "Box score" is offered only for the meetings in `head_to_head` (stored
  games); those that came only with `head_to_head_record` would answer 404.
- **"{Team}, last N"** is the followed side's finished games, built as
  Results builds them (stored results laid over the season list), with the
  other side's form at the foot. With both sides followed it opens on the
  favourite team if it is playing, else the home side, with a switch. With
  neither followed there is no table, only both sides' form squares.
- **Standings context:** a panel per side that has a row. "Full standings"
  is `navigate("league", { leagueId, group })`.
- **Sheets that share a row** (meetings, last N, standings) are stretched
  to one height so their edges line up, but their content is not: it keeps
  the height it is drawn at, and the room left over in a shorter sheet is
  tint under a hairline (`SpareRoom` in `views/matchup/parts`), as in the
  Calendar's Legend. Nothing is spread out or centred to fill a stretched
  sheet.
- **Projected lineups and injuries** are two sections below the mocked
  ones, drawn as Box Score tables with the old disclaimer and "lineup
  unavailable" line. `components/LineupView` is unchanged and still used by
  the game detail modal, which "Box score" opens once a game has started.
- **Requests:** the shared `/today` and `/teams`; `/schedule` from yesterday
  to +14 days (every 5 minutes, 60 s while it holds a live game Today has
  dropped); `/matchup/{id}` once per game and again every 10 minutes while
  the tab is on screen; `/games/{id}` every 30 s, only while the game is
  live AND Matchup is the tab on screen; `/standings/{league}` every 5
  minutes; the batched `/odds` for the radar, once per list of games and
  again every 10 minutes while the tab is on screen;
  `/results/{team}?limit=50`
  and the season list for the last-N table; and `/setup/teams/{league}` for
  team colours and codes, for at most six leagues of the games on offer.
  The game on screen is the newest copy among those payloads. When the
  server's day changes the schedule's range does too; the last answer
  stands in until the new range has answered.
- **Left out because the API does not carry them:** the venue's city,
  weather during a live game (a forecast exists only for an outdoor game
  still to start), short city-style team names, a second team colour, a
  play-by-play page to open, a season to scope the series to, and a
  pre-game figure for soccer, hockey, tennis and MMA (no row is drawn).
- Pure logic lives in `views/matchup/model.ts` and is unit-tested there.

### Box Score: the News view (2026-10-11 — SUPERSEDES the News layout notes elsewhere)

As mocked: the newest story large (photo slot, who it is for, headline,
summary, source and age, "Read at source"), the rest in a list (date, team
block, who and where, headline), and a filter to one follow. The in-app
reader is kept, restyled as a Box Score sheet. Nothing about the API
changed.

- **Every filter has its own feed.** The view asks `/news` once for
  everything and once per followed team, athlete and whole competition
  (`useNewsFeeds`), each with `limit=51`: 50 are listed, and the 51st only
  proves there are more. A filter's count is the stories it lists when
  chosen, written "50+" when it holds more. The unscoped feed is the newest
  50 of everything, in which one busy team crowds the rest out, so a team's
  share of it is never used as its count.
- **Nothing is stated that the loaded data cannot prove.** A follow is
  called without stories only when its own feed came back empty, a filter
  that has not answered shows no number, and nothing is called "recent":
  the API has no age filter. A date from another year than the current one
  carries its year (in the date cell, the phone's date line, the top
  story's date and the reader), since the feed is never pruned by age.
- **The filter** is a row of segments (block, name, count) for up to six
  follows. Below 1024px it is one strip that scrolls inside itself and
  keeps the chosen segment, and the one with keyboard focus, in view; from
  1024px a row that does not fit wraps onto a second. Past six follows it
  is the shared `Select` (`button[aria-label="Show stories for"]`) with the
  counts in the options. A whole competition is one segment with a neutral
  block.
- **Refresh** collects for the filter on screen (`POST /news/refresh`) and
  says what it found, or that it failed, in a line under the head. It stays
  focusable while busy, and its icon turns only where motion is allowed.
  The line stays while the next refresh runs (so the page under it does not
  move), is cleared by a change of filter, and is dropped when the day
  changes, since it gives only a time of day. A refresh of everything whose
  result lands on another filter's page says "across everything you
  follow"; a failure points at "the stories below" only when there are
  some.
- **A row opens the reader; the arrow box on the row, and "Read at source"
  on the top story and in the reader, open the story where it was
  published** (new tab, `rel="noreferrer"`). `components/NewsDetailPanel`
  is a modal dialog named by its headline: focus moves in and is held,
  Escape and a click on the dimmed page close it, focus returns to the row,
  the page behind does not scroll, and it is `inert` while closed. Where
  the story is taller than the window its text is a named keyboard stop
  ("Story text, scrolls") so the arrow keys can scroll it. As before, it
  stays open if the tab changes under it (kiosk rotation).
- **Headlines** drop the outlet's name from the end ("Headline - Outlet":
  the outlet is `source`, printed beside it) and are set in capitals by
  CSS, except a Turkish headline, which capitals without a language would
  respell. A summary is shown only when it says more than the headline:
  search-feed summaries repeat it, and some go on into other outlets'
  headlines (the headline, the story's own outlet, then the rest), which is
  not a summary either. The photo slot shows `image_url`, and
  the team's colour block and code when there is none or it fails to load.
- **People** (tennis, MMA, golf, racing follows) are labelled with their
  league ("Carlos Alcaraz · ATP"): the backend keeps a tour's feed under
  one of the followed players, so a story under a player may be the tour's.
  A followed person without stories is told so ("No stories filed under X.
  ATP's own coverage is kept under one of the people you follow there.")
  only when another followed person of that league does list a story;
  otherwise the line is the plain "No stories for X". Such a follow keeps a
  row of its own when the other quiet follows share one.
- **Requests:** `/teams`, and `/news?limit=51` once unscoped plus once per
  follow, re-read every 5 minutes while News is the tab on screen. The view
  stays mounted behind the other tabs and reads nothing there; on coming
  back, a feed last read 5 minutes ago or more is read at once (so a kiosk
  rotation lands on fresh stories). Refresh and a manage-teams save still
  invalidate every news query. They are plain database reads; the server
  collects from its sources hourly, or on Refresh.
- **Left out because the API does not carry them:** the category chips
  ("Injury", "Recap"), a team's short name in the filter, a second team
  colour for the block's text (the text colour is measured instead), a
  story's language, and a feed of one athlete's own stories.
- Pure logic lives in `views/news/model.ts` and is unit-tested there.

### Box Score: shared controls (2026-10-10)

- `components/Select.tsx` is the Box Score listbox everywhere it is used: a
  `.bs-btn` trigger (44px, `size="lg"` 52px for page-level pickers), a
  square list with a 2px ink frame that hangs from whichever edge keeps it
  on screen, 44px options, the chosen option an ink block. Its props,
  keyboard, focus and aria contract are unchanged from the notes below:
  the trigger's `aria-label` stays exactly the label it was given (tests
  and the screenshot script find it that way) and the chosen value is
  attached with `aria-describedby`.
- `components/bs/Segmented` with `kind="tabs"` moves with the arrow keys
  from the tab that has focus, not from the selected one.
- `components/bs/` also holds `Notice` (a headline and a line in a sheet,
  plain or `role="alert"` with an action), `Loading` (page-shaped
  placeholders with a hidden `role="status"` sentence), `ScrollRegion` (a
  strip that is a named group and a Tab stop only while it scrolls
  sideways) and `MarkedName` (a follow marker that stays with the last word
  of the name it marks).
- Added on 2026-10-11 with Calendar, Matchup and News, all additive:
  `Select` takes an optional `placeholder`, what the trigger says while
  `value` is none of the options (the Matchup picker's "10 more games");
  without it nothing changes. `components/bs` exports `RefreshIcon`, the
  one icon of a Refresh button (`spinning` turns it only where motion is
  allowed). `hooks.ts` has `useNewsFeeds(scopes, limit)` and
  `useSetupTeamsFor(leagueIds)`, which read the same cache entries as
  `useSetupTeams`. `lib/time.ts` has `monthShort`. `hasCatalog(league)` in
  `views/league/model.ts` is the one statement of which leagues have a
  setup catalog (ESPN team sports).
- **The score flash** (added 2026-10-11, the step 5 gate). A score pulses
  (`sd-score-flash`, 700 ms, size only) when its value changes in front of
  the viewer, and at no other time. Every score in the app is drawn by
  `components/bs/ScoreFigure` (`value`, and `of` = the game's id): the
  score line, Today's hero and rows, the Calendar's cards and rows, and
  Matchup's game sheet. It puts `.sd-score--flash` on for one pulse and
  takes it off again; `.sd-score` alone is only the figure and never
  animates. The pulse does NOT play:
  - on a figure's first paint, or when a figure appears where there was
    none (a game starting, a row moving to another list, Calendar switching
    Week / Month / Agenda, the score line returning after the kiosk clock);
  - when a view is shown again. Views stay mounted behind `hidden`
    (`display: none`), and a CSS animation left on an element starts over
    whenever the element is displayed again, so a class that stayed on
    flashed every score on every tab switch and every kiosk rotation;
  - when the score changed while its view was hidden (the figure is simply
    the new one when the view comes back), or when the same place on the
    page is given another game (`of` changes: Matchup's picker).
  Reduced motion is the stylesheet's affair as everywhere else: the class
  does nothing outside `prefers-reduced-motion: no-preference`. Do not put
  an animation on `.sd-score` itself, and do not key a score element on its
  value to restart one: use `ScoreFigure`. The part of the rule that needs
  no page (`scoreMoved`, and `SCORE_FLASH_MS`, which must equal the
  stylesheet's 700ms) is in `lib/scoreFlash.ts`; its test also reads
  `index.css` and the six files that draw a score, and fails if the
  animation goes back on `.sd-score`, leaves the reduced-motion gate, or a
  view draws a score without `ScoreFigure`.
- Also added by the step 5 gate, additive: `useNewsFeeds(scopes, limit,
  poll = true)` stops its own five-minute reads when `poll` is false (News
  passes whether it is the tab on screen). Since the merge of the freshness
  keeper (2026-10-11) the keeper still re-reads a mounted `news` query past
  its five-minute row while the browser tab is visible, whichever app tab
  is showing; that is the keeper's rule and stands. The gate's
  `keepPrevious` option on `useGameOdds` was removed in that merge: the
  keeper's key default does the same for every caller, and
  `freshness.test.ts` fails if the hook sets its own `placeholderData`.

### Kiosk fix (Layout.tsx)
Make kiosk mode PERCEPTIBLE (current bug: pause-on-interaction + long
timers make it invisible while testing). When kiosk is on: show a small
persistent indicator in the header ("Kiosk" pill) with a live "next in
{n}s" countdown to the next tab rotation; shorten `ROTATE_MS` to ~12s;
make the post-interaction pause shorter (~15s) OR only pause while the
pointer is actively moving. Keep the idle clock. The countdown must
visibly tick so the user sees the feature is active.

## Phase 9: golf-tab consolidation, more leagues, division standings, player stats

Foundation landed: `StandingRow.subgroup` (+ StandingRowOut + TS + row-dict),
`Player.stat_line` (+ PlayerORM column + migration `players.stat_line` +
PlayerOut + TS + repository insert + roster route).

### Golf tab consolidation (frontend owner)
Remove the dedicated "golf" tab from `Layout.tsx` TABS and the `App.tsx`
view switch / TabId. Golf stays fully usable WITHOUT a tab: Today already
renders `today.events` — make those event cards CLICKABLE to open a
leaderboard modal (reuse the GameDetailModal pattern / the existing
EventsView leaderboard table as the modal body). Delete `EventsView.tsx`
(or repurpose its table into the modal). Calendar may show event spans but
that's optional. Net: no Golf tab; golf leaderboards reachable from Today.
Don't break `useEvents`/`api.events` (still used to power Today's events or
the modal).

### More leagues — curated majors + lower English tiers (catalog owner)
Add to `espn_catalog.CATALOG` (all ESPN, sport soccer, verify each code
live before adding — skip any that 404):
- Lower English: `eng.3` League One, `eng.4` League Two.
- South America: `bra.1` (Brazil Série A), `arg.1` (Argentina Liga
  Profesional), `conmebol.libertadores` (Copa Libertadores),
  `conmebol.sudamericana`.
- North America: `usa.open` (US Open Cup) and `concacaf.league`/
  `concacaf.champions` if valid (MLS + Liga MX already exist).
- More Europe: `por.1` (already?), `bel.1`, `gre.1`, `aut.1`, `sui.1`,
  `rus.1`, `ukr.1`, `den.1`, `nor.1`, `swe.1` — add the ones that resolve.
Clean kebab ids, entity_noun "team". Update the `test_setup_api.py`
static-shape league-id list to match exactly what you add. Report the
final added set.

### Division-nested standings (espn + mock + frontend owners)
- espn `_parse_standings`: ESPN's standings support division depth. Fetch
  with the deeper level (e.g. `?level=3`) or read nested `children` of
  `children` so each entry gets BOTH `group` (conference/league) and
  `subgroup` (division: Atlantic/Central/Southeast; AL/NL East/Central/
  West; etc.). When only one level exists, leave subgroup None.
- mock: split each conference pool into 2 fictional divisions, setting
  `subgroup` (e.g. "Eastreach North"/"Eastreach South"); ranks restart
  per the FINEST group. Keep soccer single-table (group+subgroup None).
- StandingsView: render nested — group heading, then per-subgroup
  sub-heading + mini-table, ranks within the subgroup. When subgroup is
  null everywhere in a group, render that group as one table (today's
  behavior). When group is null everywhere, single table.

### Player stats (espn + mock + frontend owners)
- espn `get_roster` / a stats fetch: populate `Player.stat_line` with a
  compact, sport-appropriate season line from ESPN
  (basketball "24.1 PPG · 7.8 REB · 5.2 AST", baseball ".291 AVG · 22 HR ·
  74 RBI" or pitcher "3.12 ERA · 178 K", hockey "31 G · 44 A · 75 PTS",
  football QB "28 TD · 9 INT · 4015 YDS" etc., soccer "12 G · 5 A").
  Use the most efficient available source (roster payload statistics, a
  team leaders/statistics endpoint, or per-athlete overview — pick what
  keeps the daily refresh reasonable; it's fine if some players have no
  line → None). Never raise.
- mock: deterministic fictional stat_line per player, sport-appropriate.
- RosterView: show `stat_line` under/next to each player (muted, tabular);
  graceful when null. Individual sports (tennis/mma/golf) have empty
  rosters already — unaffected.

## Phase 10: stadium Map view (MapLibre + OSM, free, stadiums-only)

Decisions: MapLibre + OpenStreetMap (free, NO API key/token), 3D building
extrusions, stadiums only (no training grounds/history). Each followed
team appears at its home stadium.

Foundation landed: `domain.TeamLocation(venue, lat, lon)`,
`TeamORM.home_venue/venue_lat/venue_lon` (+ migration),
`SportsProvider.get_team_location`, `schemas.MapTeamOut`/`MapOut`, TS
`MapTeam`/`MapResponse`.

### Providers (provider owner)
Implement `get_team_location(league, team) -> TeamLocation | None`:
- ESPN: GET `/teams/{provider_key}` → venue `fullName` (+ address city for
  geocode context); coords usually absent → return venue name, lat/lon
  None (geocoder fills). Never raise.
- mock: return deterministic FICTIONAL coordinates per team (spread plausibly
  across a region so the demo map is populated) + the fictional venue name.
- thesportsdb: `lookupteam.php`/`lookup_all_teams` often has
  `strStadiumLocation` and sometimes lat/lon — return what's there.

### Geocode service (backend owner) `app/services/geocode.py`
`async def geocode(query: str) -> tuple[float, float] | None` via
Nominatim (`https://nominatim.openstreetmap.org/search?q=&format=json&limit=1`),
REQUIRED descriptive User-Agent ("SportsDash/1.0 (self-hosted)"), ≤1 req/s
(serialize calls), httpx timeout, never-raise. Results cached in the team
row (don't re-geocode a team that already has venue_lat/lon).

### Resolve + store (scheduler owner)
A `refresh_locations()` job (and folded into daily_refresh, after
schedules so venues exist): for each followed team without coords, call
`get_team_location`; if it returns coords use them, else geocode
`"{venue}"` (fall back to the team's most common home-game venue from
GameORM when the provider gives no venue); store home_venue/venue_lat/
venue_lon via a `repository.set_team_location(team_id, venue, lat, lon)`.
Per-team errors isolated; job never raises. Run it once on startup too
(so the map populates without waiting for the daily cron).

### Route (route owner) `app/routes/map_view.py`
`GET /api/map` → `MapOut`: followed teams that HAVE venue_lat/lon, mapped
to MapTeamOut (include sport + color + venue). Teams without resolved
coords are omitted. Include the router.

### Frontend (frontend owner)
- Add `maplibre-gl` (^4) to package.json deps; import its CSS.
- New "Map" tab in Layout TABS + App view + `src/views/MapView.tsx`.
- `api.map()` + `useMap()` hook (staleTime long; coords change rarely).
- MapView: a MapLibre map using a FREE keyless style — use OpenFreeMap
  (`https://tiles.openfreemap.org/styles/liberty`) which needs no token and
  includes building footprints; enable a 3D `fill-extrusion` buildings
  layer and a tilted default pitch so stadium-area buildings render in 3D.
  One marker per followed team at [lon, lat], colored by team color, with
  a popup (team name, venue, league). Fit bounds to all followed teams on
  load; clicking a team's marker flies to it. Graceful empty state when
  `/api/map` returns no teams (e.g. coords not resolved yet). Keep dark
  kiosk styling for the surrounding chrome; the map tiles themselves are
  the provider's style.
- Verify `bun install` adds maplibre-gl and `bun run build` bundles it.

## Phase 11: stadium enrichment + facts, logos everywhere, live standings, animations

Foundation landed: `TeamLocation` facts (capacity/opened/image_url/location/
surface), `TeamORM.venue_capacity/venue_opened/venue_image_url/venue_location/
venue_surface` (+ migration), `MapTeamOut` logo_url + facts, `CatalogLeague(Out)`
+ TS logo_url, setup route passes league logo_url through.

### Stadium enrichment + Chelsea/soccer fix (backend owner)
ESPN gives soccer clubs NO venue, and off-season teams have no fixtures to
borrow one from — so Chelsea never resolves. Add a stadium-enrichment
source via TheSportsDB (already integrated): `app/services/stadiums.py`
`async def lookup_stadium(team_name: str, *, sport: str | None=None) ->
TeamLocation | None` — `searchteams.php?t={name}` → strStadium,
strStadiumLocation, intStadiumCapacity, strStadiumThumb (image),
intFormedYear/strStadium opened where available, and lat/lon if present;
defensive, cached, never-raise. Update `jobs._resolve_team_location`
strategy order: (1) provider coords; (2) TheSportsDB stadium enrichment by
team name (gives venue + facts + often coords); (3) geocode the venue name
(provider's or TheSportsDB's or the stored-game venue); (4) stored-game
venue. Persist facts via an extended `repository.set_team_location(...,
capacity, opened, image_url, location, surface)`. `/api/map` (route) maps
the new TeamORM facts + `logo_url` (from the team row) into MapTeamOut.
ON-DEMAND: when `GET /api/map` finds followed teams without coords, kick
`refresh_locations` (or resolve inline) so a just-followed team isn't
silently missing — never block the response longer than a short budget;
return what's resolved and let the rest fill in.

**Narrowed 2026-07-27 — step (2) returns a venue name ONLY when the
upstream record names a stadium.** (`_resolve_team_location` now lives in
`app/scheduler/refresh.py`.) TheSportsDB's `strLocation` is free text and is
usually an administrative area — "Dorset, England", "Kyiv, Ukraine" — so
`stadiums._compose_venue` returns `None` rather than pass it off as a venue.
The area is still returned as `TeamLocation.location` (a true fact, shown in
the panel as *Location*); it is never promoted to `TeamLocation.venue`,
because geocoding an area yields its **centroid**, which step (3) would then
cache on the team row and `/api/map` would plot as a precise home-ground fix
— a marker tens of km from the real ground, drawn and flown to exactly as
confidently as a correct one, and never revisited (`refresh_locations` only
processes teams *without* coordinates). Refusing there un-shadows step (4),
the stored-game venue, which is a name worth geocoding; a team with neither
is left **unplotted** rather than pinned somewhere plausible. Rows cached
under the old rule are recognised by `stadiums.is_area_only_venue(venue,
location)` — the pre-fix fallback's fingerprint is `venue == location` — and
re-resolved by `refresh_locations` (teams) and
`refresh_competition_stadiums` (competition/stadium cache), both of which
skip their "already located / missed recently" shortcuts for such a row.
The Redis venue index self-heals: `build_index` only indexes teams that have
both a venue name and coordinates, so a repaired row stops feeding the
county centroid into game-venue resolution.
`services/venue_coords.py` and `services/geocode.py` are unchanged — the
defect was the composition, not the geocoder.
See *Loose ends* in [ROADMAP.md](../ROADMAP.md) for the two consequences
that are still open: the repair has not been run against a live database,
and a whole-competition team has no stored-fixture fallback to land on
(`repository.most_common_home_venue()` is keyed by `team_id`, which those
teams do not have), so it drops off the map instead of re-resolving.

### League logos in the picker (catalog owner)
Populate `CatalogLeague.logo_url` for catalog leagues from ESPN (league
logo — verify the endpoint, e.g. the league's `logos[].href` from a
leagues/scoreboard call, or the core API; fall back to None). The setup
route already passes it through. Frontend picker shows it.

### Live standings (scheduler owner)
When `live_tick`/`events_tick` observes a followed league's game transition
to FINAL, trigger that league's standings refresh (so group/division
positions move in near-real-time — "a country moves places and it
updates"). Debounce/coalesce so one tick doesn't refresh a league's
standings repeatedly; isolate failures; never raise. Daily refresh stays.

### Frontend (two owners — map/logos, and app-logos/animations)
- Map markers = TEAM LOGOS: render each team's `logo_url` as the marker
  (small rounded badge with a team-color ring), falling back to a
  colored pin when no logo. Clicking a marker opens a stadium panel/popup
  with the photo (image_url), capacity, year opened, location, surface —
  graceful when a fact is missing.
- League logos in onboarding picker (LeagueStep): show `league.logo_url`
  beside each league name (fallback to none).
- Team logos AROUND THE APP: use the stored team logo (TeamsResponse teams
  carry logo_url) in GameCard sides, StandingsView rows, RosterView header,
  Today cards — small logo next to/instead of the abbreviation chip,
  fallback to the abbreviation chip when no logo (onError too).
- ANIMATIONS (subtle & polished): smooth tab/view transitions, card hover
  lift, score-change flash, list fade/stagger-in, map fly-to easing (map
  already eases). Use CSS transitions / small keyframes (and the existing
  Tailwind) — NO heavy animation library unless a tiny one is justified;
  respect `prefers-reduced-motion`. Keep it tasteful, never distracting,
  and theme-aware (works in all 4 themes). (When the score-change flash
  may play is fixed as of 2026-10-11: see "The score flash" under "Box
  Score: shared controls".)

## Phase 12: national-team follow model + competition map plotting

Foundation landed: `StadiumORM` (key `"{provider}:{scope}:{provider_key}"`
from `repository.stadium_key`,
venue+coords+facts+resolved flag — caches stadium resolution decoupled
from TeamORM so competition teams without a team row can be plotted);
`MapTeamOut.league_name` + `source` ("followed"|"competition") + TS.

**Key amended 2026-10-11.** The key was `"{provider}:{provider_key}"`, which
is not an identity: ESPN numbers each US league's teams from 1, so the NFL
and NHL sides numbered 1 shared one row. `scope` is the id space the
provider numbers the team in. The rule, the two repository functions that
own it and what happens to rows cached under the old key are in *Stadium
cache keys carry the id space* below; the two mentions of the key in 12b
are corrected in place.

### 12a — National-team individual follow (catalog + follow owner)
- Expand `espn_catalog.NATIONAL_TEAM_COMPETITIONS` so following a nation
  (from any national-team picker league) attaches its FULL international
  slate via TeamCompetition: FIFA World Cup + WC qualifiers, the relevant
  continental cup + its qualifiers, UEFA Nations League, and friendlies
  (`fifa.friendly`). LIVE-VERIFY the ESPN codes that resolve (WC qualifying
  is split by confederation, e.g. `fifa.worldq.uefa`, `fifa.worldq.conmebol`,
  `fifa.worldq.concacaf`, `fifa.worldq.afc`, `fifa.worldq.caf`,
  `fifa.worldq.ofc`; Euro qualifying `uefa.euroq`; add only codes that
  exist). Attach a broad international set; per-team schedule filtering
  (get_schedule already filters to the team's own games) means attaching a
  confederation a nation doesn't play in just yields nothing — harmless.
  Each attached sibling league must be upserted (as in Phase 3a). Net: a
  followed nation aggregates games across all its international comps and
  PERSISTS after any single tournament. Its map pin resolves to its
  national stadium via the Phase 11 enrichment (TheSportsDB searchteams
  "{nation}" → e.g. Wembley) — verify England→Wembley.

### 12b — Whole-competition map plotting (map backend owner)
- `repository`: stadium-cache helpers — `stadium_key(league,
  team_provider_key)` (the only place a key is built),
  `find_team_stadium(session, league, team_provider_key, team_name, *,
  adopt=False)` (a team's row, wherever an older build left it),
  `get_stadium(session, key)` (exact key),
  `upsert_stadium(session, key, team_name, location_fields...)`.
- A competition is ACTIVE if it has at least one game in the DB within a
  near window (e.g. now-2d .. now+21d) — reuse `games_between` on the
  league. (When the World Cup ends, no near games → inactive → its teams
  drop off the map automatically.)
- `GET /api/map` assembles TWO sources:
  1. followed teams with resolved coords (as today) → `source="followed"`.
  2. for each ACTIVE `follow_all` league: its catalog teams
     (`espn_catalog.get_league_teams`), each resolved via the stadium
     cache (StadiumORM by `repository.stadium_key`, read through
     `repository.find_team_stadium`; on miss, resolve
     via the Phase 11 stadium-enrichment/geocode pipeline and cache it —
     bounded so the request doesn't hang; unresolved teams are simply
     omitted and fill in on later calls) → `source="competition"`,
     carrying league_name + the team's logo/color from the catalog.
  Dedupe: a team that is both followed and in an active followed
  competition appears once as "followed". "The same team" is the same
  `repository.stadium_key`, not the same provider id (2026-10-11).
- A background job (fold into `refresh_locations` / a new
  `refresh_competition_stadiums`) pre-resolves active-competition teams'
  stadiums into StadiumORM so the map is populated without on-request
  geocoding latency; rate-limited + never-raise.

### Frontend (map owner)
- MapView consumes `source`: "followed" markers as today (logo + color
  ring); "competition" markers styled subtly differently (e.g. smaller /
  slightly muted / a thin ring) so the user's own teams stand out among a
  competition's full field. A small legend ("Your teams" vs
  "{competition} teams"). Popups unchanged (facts when present). Empty
  state unchanged. The 48-nation case must stay performant (markers are
  fine; avoid per-marker heavy work).

## Setup & onboarding (added after v1)

Followed teams live in the **DB**, not the YAML: `seed_from_config` seeds
ONLY when the teams table is empty, and when it does seed from an
explicit user config it also sets the onboarded flag. The shipped
`backend/config/teams.yaml` is comments-only (a template), so a fresh
install is never pre-seeded. First-run UX: frontend checks setup status
and shows a full-screen wizard until onboarded.

Real league/team names ARE allowed here: the catalog and whatever the
user picks are live app data, not sample data. Test fixtures stay
fictional.

### `app/providers/espn_catalog.py`

```python
@dataclass(frozen=True)
class CatalogLeague: id: str; name: str; sport: Sport; provider: str; provider_key: str
CATALOG: tuple[CatalogLeague, ...]   # nba, wnba, mlb, epl, laliga, bundesliga, seriea, ligue1, mls, ucl — all provider "espn"
def get_catalog_league(league_id: str) -> CatalogLeague | None
async def get_league_teams(league: CatalogLeague) -> list[CatalogTeam]
    # GET {site}/{provider_key}/teams?limit=1000, parsed defensively;
    # CatalogTeam(provider_key, name, abbreviation, logo_url|None, color|None — "#"-prefixed)
    # in-process cache per league, TTL 1h; raises EspnCatalogError on HTTP failure
```

### ORM / repository additions

`AppMetaORM` (`app_meta`: key PK String(64), value Text). Repository:
```python
async def get_meta(session, key: str) -> str | None
async def set_meta(session, key: str, value: str) -> None
async def replace_followed(session, leagues: list[domain.League], teams: list[domain.Team],
                           competitions: list[tuple[str, str, str]] | None = None) -> None
    # reconciles, never wipes: afterwards leagues / teams /
    # team_competitions equal the arguments, and any other row survives
    # only if the new set still backs it. notifications_sent is never
    # deleted. (Until 2026-10-10 this wiped every table and left the
    # refresh to rebuild it — see *Editing the followed set* below.)
async def prune_unbacked(session) -> None
    # the same rules against the set as STORED (tested in SQL, per
    # statement — never against ids read beforehand); a no-op on a
    # consistent database. Run first by every daily_refresh pass.
```
Onboarded flag: meta key `"onboarded"` = `"1"`.

### Setup routes (`app/routes/setup.py`, under /api)

| Route | Response | Notes |
|---|---|---|
| `GET /setup/status` | `SetupStatusOut{onboarded, followed_team_count, refreshing}` | `refreshing` (2026-10-10): a `daily_refresh` is in flight |
| `GET /setup/selection` | `CurrentSelectionsOut{selections: [{league_id, follow_all, teams: [CatalogTeamOut]}]}` | added 2026-10-10: the followed set as wizard selections; database only, no network, never commits |
| `GET /setup/leagues` | `CatalogLeaguesOut{leagues: [{id,name,sport,provider}]}` | static, no network |
| `GET /setup/teams/{league_id}` | `CatalogTeamsOut{league_id, teams: [CatalogTeamOut]}` | live ESPN fetch; 404 unknown league; 502 on upstream failure |
| `POST /setup/follow` | `TeamsOut` | body `FollowRequest{selections: [{league_id, team_provider_keys}]}`; 400 on empty/unknown keys; `replace_followed` + set onboarded + commit + `kick_daily_refresh()`. The body is the WHOLE new set and the save reconciles — whatever it still follows keeps its rows |

Internal ids from a follow: league id = catalog id; team id =
`f"{league_id}-{slugify(team name)}"` for a team not followed yet — one
that already is (same `league_id` and `provider_key`) keeps its stored id,
whatever the provider calls it now. `scheduler/jobs.py` gains
`def kick_daily_refresh() -> None` (marks the followed set changed, then
create_task + held reference; routes must not import `app.main` —
circular). The reconcile rules, the per-key team resolution, the selection
route and what the kick does to a refresh already running are all under
*Editing the followed set* below.

### Frontend onboarding

`types.ts`/`api.ts`/`hooks.ts` mirror the five routes
(`useSetupStatus()` etc.; setup mutations via plain `api.*` calls +
`queryClient.invalidateQueries()`). `App.tsx`: splash while status
loads; full-screen `<OnboardingWizard mode="first-run" .../>` when not
onboarded; the gear button in `Layout` opens `<ManageTeamsWizard>`, which
loads the followed set and opens the same wizard ON it as `mode="manage"`
— an editor, never an empty wizard: the save unfollows whatever it does
not list.
Wizard (in `src/components/onboarding/`): league multi-select grouped by
sport → per-league team grid (logo, name, search filter, multi-select) →
review (in manage mode it also lists what the save will unfollow) → POST
→ syncing screen (poll `useToday` until games or ~20s; after an edit the
kept follows' games can end that wait at once, and what the refresh loads
arrives when `App` sees `refreshing` clear and refetches everything) →
dashboard. Dark kiosk styling consistent with the rest.

## Infra contracts (infra agent)

- `backend/Dockerfile`: `python:3.12-slim`, install requirements, copy
  app, `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- `frontend/Dockerfile`: `oven/bun:1` — `bun install`, `bun run build`,
  runtime stage serving via `bun server.ts`.
- `frontend/server.ts`: `Bun.serve` on port 3000 — proxies `/api/*` to
  `process.env.API_URL ?? "http://api:8000"` (preserve query string),
  serves `dist/` statics, SPA-fallback to `index.html`.
- `docker-compose.yml`: services `db` (postgres:16-alpine, volume,
  healthcheck `pg_isready`), `redis` (redis:7-alpine), `api` (build
  ./backend, env `SPORTSDASH_DATABASE_URL=postgresql+asyncpg://sportsdash:sportsdash@db:5432/sportsdash`,
  `SPORTSDASH_REDIS_URL=redis://redis:6379/0`,
  `SPORTSDASH_NTFY_URL=http://ntfy`, timezone from `.env`, depends_on db
  healthy, host port `127.0.0.1:8001:8000`), `frontend` (build ./frontend,
  ports `3000:3000`, `API_URL=http://api:8000`), `ntfy`
  (binwiederhier/ntfy, `serve`, port `8090:80`, cache volume), `backup`
  (daily `pg_dump` loop into `./backups`, 14-day pruning, nothing
  depends on it).
- `SPORTSDASH_NTFY_TOPIC` is `${SPORTSDASH_NTFY_TOPIC:?…}` — **required,
  no default**. ntfy runs without an auth file, so the topic name is the
  only secret; compose fails closed (with a message telling you to
  `openssl rand -hex 16`) rather than falling back to a guessable value.
  `.env.example` therefore documents the variable but deliberately ships
  no value for it.
- Port bindings are *not* a security boundary and must not be documented
  as one: `:3000` is LAN-published and proxies every `/api` path and
  method, so anything reachable on the LAN reaches the whole (unauthed)
  API. Binding `api` to loopback only avoids a second, unproxied entry
  point and keeps `/docs`, `/redoc` and `/openapi.json` off the LAN.
  README's *Trust model* note and the comments in `docker-compose.yml`
  say the same thing; keep all three in step.
- Root `.gitignore` (+ `.dockerignore`s), `README.md` (architecture, quick
  start, following teams via the setup wizard, adding a provider).

## Phase 13 — standings crests + World Cup host-venue map

Three changes, schemas + types mirrored together (this section is the
binding record):

### `StandingRow` (domain) / `StandingRowOut` (schema) / `types.ts`

Added three per-row display fields so EVERY team in a table shows its
crest, not just the followed handful (standings rows are not followed
teams, so `team_id` is almost always null):
`logo_url: str | None`, `abbreviation: str | None`, `color: str | None`
(`#`-hex). ESPN fills them from each `entries[].team` object
(`logos[0].href` / `abbreviation` / `color`) in `_parse_standing_entry`;
`_team_logo`/`_team_color` helpers added to the ESPN adapter (now
`espn/common.py`). Persisted by `repository._standing_row_to_dict` (so
`StandingsORM.rows` JSON still mirrors `StandingRowOut`). Mock/TheSportsDB
leave them null → the existing abbreviation-chip fallback. Frontend
`StandingsView` renders `row.logo_url ?? meta?.logo_url` (followed-team
`/teams` meta as fallback).

### `MapTeamOut` / `types.ts` `MapTeam`

Added `next_opponent: str | None` and `next_match_time: datetime | None`,
set when a competition pin is a NEXT-MATCH host venue rather than a home
ground. `GET /api/map` competition pass now resolves each whole-competition
team to the venue of its next (or, if none remain, latest) synced game; a
new curated table `app/services/wc_venues.py` (the 16 FIFA World Cup 2026
host stadiums, keyed by ESPN venue name → lat/lon/location/capacity) maps
that venue name to coordinates. Only when the venue resolves there does the
pin move; otherwise it falls back to the existing `StadiumORM` home-stadium
resolution, so non-host competitions are unchanged. `repository.list_league_games`
added for the per-team next-match lookup.

### Map side panel (frontend)

`MapView` marker click no longer opens a MapLibre popup (its `closeOnClick`
swallowed the opening click); it sets React state that drives a new
`components/MapTeamPanel.tsx` slide-in right drawer (team crest + name +
competition, stadium photo/venue/facts, and the next match). The dead
`popupHtml`/`escapeHtml`/`escapeAttr` helpers + `sd-map-popup` styles are
removed.

## Phase 14 — map hover/filters + news reader

- `MapView` markers gained a hover label (lng/lat-anchored MapLibre popup,
  `setDOMContent` so no HTML injection) and a filter bar: `FilterChip`
  toggles per source (your teams / each competition) + `GroupChip` row to
  narrow to one standings group, plus a pulsing ring on markers whose next
  match is today (`.sd-map-marker-today`, reduced-motion gated). Markers now
  reconcile against a FILTERED `visibleTeams` (Effect 2 dep) rather than the
  raw payload. Needs `MapTeamOut.group` (+ `types.ts`): the map route derives
  it from the league's stored standings (`_group_by_team`, casefolded
  team_name -> group). `next_match_time` drives the today highlight.
- (The reader's look and wording are superseded on 2026-10-11 by "Box
  Score: the News view"; it is still opened by a row.)
- News: clicking a headline opens `components/NewsDetailPanel.tsx` (slide-in
  reader: image, title, source/date/team, full summary, "Read full article"
  link out) instead of navigating away. `NewsCard` is now a button calling
  `onSelect`; the title is no longer a bare `<a>`. No backend change —
  `NewsItemOut` already carried `summary`/`url`/`image_url`. NOTE: RSS-source
  summaries are often just the headline echoed; ESPN items have real ones —
  an AI-summary upgrade (Claude API) was offered and deferred by the user.

## Phase 15 — richer box scores + matches-at-a-stadium

- `GameSummary`/`GameSummaryOut`/`types.ts` gained `team_stats: TeamStat[]`
  ({label, home, away} display strings). espn `_parse_team_stats` reads
  `boxscore.teams[].statistics` (aligned by homeAway); a curated per-sport
  spec (`_TEAM_STAT_SPECS`, soccer tuned: possession/shots/corners/fouls/
  cards/saves, "%" appended to *Pct names) drives order+labels, other sports
  fall back to the first two-sided stats. Performers expanded from 1 to up to
  3 per side (`_top_performers_for_team`, one per leaders category, deduped).
  `summary_to_out` maps team_stats. GameDetailModal renders a comparison
  (away · label · home, split bar for numeric pairs) + multi-performer lists.
- Matches-at-a-stadium is FRONTEND-ONLY: `MapView` calls `useSchedule` over the
  competition window, groups games by `venue` (competition leagues only), and
  passes `matchesByVenue` to `MapTeamPanel`, which lists every fixture at the
  clicked pin's host stadium (date · away v home · score). `localDayOffset`
  exported from hooks. Relies on map pin `venue` == game `venue` (both the
  ESPN host-stadium name for the World Cup).

## Phase 16 — leaders, bracket, team detail

- `GET /api/leaders/{league_id}` (`StatLeadersOut`/`types.ts StatLeaders`):
  player stat leaders built from the league's FOLLOWED-team rosters
  (`app/routes/leaders.py`), since ESPN exposes no league-wide leader feed
  for our comps (verified empty). `_leading_stat` parses the headline value
  from each `PlayerORM.stat_line` ("3 G · 1 A" -> (3.0,"G")) and ranks desc.
  Frontend `LeadersView` + "Leaders" tab. Scales as more teams are followed.
- `BracketView` + "Bracket" tab: FRONTEND-ONLY. Reads the competition
  schedule and reads each knockout fixture's round from its slot placeholders
  (`roundOf`: "Group X Winner"->R32, "Round of 32 N Winner"->R16, … ,
  "Semifinal N Loser"->Third Place, "Semifinal N Winner"->Final); renders
  rounds as columns. Detection works while slots are placeholders (group
  stage); resolved real-name games fall out until a round field exists.
- Team detail (#15): `components/TeamDetailPanel.tsx` exports
  `TeamDetailProvider` + `useOpenTeam()` (context handle) so any view opens a
  team overlay. App wraps content in the provider. The modal aggregates
  `useSchedule`/`useResults`/`useRoster`/`useNews` for one followed team
  (Next up · Recent results · Roster · Latest news). Wired from clickable
  Standings rows (team_id present) and the map panel's "View team page"
  (followed teams). New tabs added to `Layout.TABS`/`TabId` + `App.VIEWS`.

## Phase 17 — real icons on game cards + code-split

- `GameSideOut`/`types.ts GameSide` gained `logo_url` + `color` so EVERY game
  card shows a real crest/flag, not just followed teams (same fix as
  standings). Stored on games: `GameORM.home_logo_url/away_logo_url/
  home_color/away_color` (additive migration) populated by espn `_parse_event`
  (`_team_logo`/`_team_color` on each competitor) and `repository.upsert_games`
  (blank never wipes a resolved logo). `game_to_out` serializes them; GameCard
  + ResultsView prefer `side.logo_url ?? followed-meta`. Knockout placeholder
  sides ("Group A 2nd Place") have no team → no logo (expected).
- Code-split: `MapView` (MapLibre ~818KB) and `CalendarView` (FullCalendar
  ~229KB) are now `React.lazy` + `<Suspense>` in App.tsx — initial JS dropped
  ~384KB→97KB gzip; heavy chunks load only when their tab opens.
  (CalendarView is still lazy, but no longer holds FullCalendar: see "Box
  Score: the Calendar view", 2026-10-11.)

## Phase 18 — Golden Boot + World Cup nation pages

- `GameSummary.goals` (domain `Goal{player,team,minute,own_goal,penalty}`) +
  `GameSummaryOut.goals`/`types.ts`. espn `_parse_goals` reads soccer summary
  `keyEvents` (scoringPlay + type.type=="goal"; scorer from
  `participants[0].athlete`; own goals flagged). `GET /api/scorers/{league_id}`
  (`ScorersOut`): 404 on an unknown league. **Soccer only** — goals are parsed
  from summaries for `Sport.SOCCER` alone, so any other sport short-circuits
  to an empty board (`games_counted=0`) before the fan-out *and* before the
  cache, rather than spending one upstream call per finished game on a
  guaranteed-empty tally. For a soccer league it collects the stored FINAL
  games, keeps only the **most recent `_MAX_GAMES` (120)** of
  them (repository rows arrive oldest-first, so the tail is the current run of
  the competition rather than its opening weeks), pulls those summaries —
  the first on its own, the rest in parallel (semaphore 8; amended
  2026-10-11) — tallies goals/player (own goals excluded), ranks,
  and returns the top 30. The cap bounds the upstream fan-out: it is one
  summary call per game and finished games are never pruned, so an uncapped
  walk would grow every matchweek and again every season until the burst
  rate-limited ESPN and opened the circuit breaker for the whole app.
  `games_counted` therefore reports the games **actually tallied** (≤ 120),
  not the league's total stored finals. Cached through `services/cache`
  (`scorers:{league_id}:{n}`, 30min TTL) where `n` is the FULL final count,
  *not* the capped one, so a newly finished match still busts the cache once
  the cap has bitten. A league whose provider isn't registered returns an
  empty `ScorersOut` with `games_counted=0` rather than an error. **503
  when every summary fetch of the walk failed, or the circuit breaker
  turned any of them away** (2026-10-11 — see *An
  upstream outage is not an empty answer* below); any other partly failed
  walk still answers 200. LeadersView
  shows this Golden Boot when present, else the roster-derived board.
- `GET /api/nation/{league_id}/{name}` (`NationOut`): by-name mini-dashboard
  for a whole-competition team (group standing from stored standings +
  fixtures/results from synced games). `NationDetailPanel.tsx` modal.
  `TeamDetailProvider` now also exposes `useOpenNation(leagueId, name)`; wired
  from non-followed Standings rows + the map panel's "View {nation}" button.

## Phase 19 — modal portal fix + MLB box-score fix

- BUG: overlays rendered inside a view (GameDetailModal, EventLeaderboardModal,
  MapTeamPanel, NewsDetailPanel) sat off-center because `sd-view-enter`
  animates `transform`, establishing a containing block for `position: fixed`
  descendants. FIX: `components/Portal.tsx` (createPortal → document.body);
  every modal/panel now renders through `<Portal>` so it's viewport-fixed.
  (Team/Nation modals portaled too for consistency.)
- BUG: baseball box score showed "batting/pitching/fielding/records" with "—"
  — ESPN nests MLB team stats under category groups (no flat displayValue).
  FIX: `_parse_team_stats` now only emits a comparison for sports with a
  curated `_TEAM_STAT_SPECS` (soccer); others get no team-stats section
  (line score + performers only). 405 backend tests green.

## Phase 20 — unified team/nation profile view

- Merged the separate followed-team modal and nation modal into one
  `components/TeamProfileView.tsx` (hero: crest over optional stadium photo,
  record + recent-form strip + chips; "Next match" card; sections Fixtures /
  Recent results / Roster / News / Stadium — each shown only when present).
  `TeamDetailPanel.tsx` now holds the provider plus two loaders building a
  normalized `TeamProfile`: `FollowedTeamLoader` (useTeams/useMap/useSchedule/
  useResults/useRoster/useNews) and `NationLoader` (useNation). Form/record
  computed from results via `outcomeOf`/`formAndRecord`. `NationDetailPanel.tsx`
  removed. useOpenTeam/useOpenNation unchanged.

## Phase 21 — league-wide leaders + dropdown selector

- `GET /api/leaders/{league_id}` is now LEAGUE-WIDE for NBA/MLB/NHL via a new
  `app/providers/espn_leaders.py` (ESPN `statistics/byathlete`; per-sport
  headline stat: NBA PTS, MLB HR, NHL PTS; positional totals parsed via the
  top-level category `labels`). Players on a followed team are flagged
  `highlighted` — matched by BARE ESPN athlete id (roster ids are stored
  `"espn:<id>"`; strip the prefix). Cached through `services/cache`
  (`leaders:v2:{id}`, 15min). A feed that cannot be fetched falls through to
  the roster board, and answers **503 only when that board is empty too**
  (2026-10-11 — see *An upstream outage is not an empty answer* below).
  Soccer keeps the box-score Golden Boot; other sports keep the roster board.
  `StatLeaderOut.highlighted` + `ScorerOut.highlighted` (scorer's team
  followed) added (+ types.ts).
- `components/Select.tsx`: compact dropdown (current choice icon + label,
  dropdown of the rest; closes on outside-click/Esc) replacing the wide
  wrapping league pill rows in LeadersView + StandingsView (league logos
  sourced from `useSetupLeagues`). Leaders rows render the highlight (amber
  row + followed-team crest/name).

## Phase 22 — map panel clip fix, more dropdowns, playoff brackets

- Map/News side drawers now `fixed bottom-0 right-0 top-12` (was `inset-y-0`):
  after portaling, `inset-y-0` put them under the sticky 48px `z-50` header,
  clipping their own header. `top-12` clears the nav.
- `Select` dropdown extended to Calendar (a team FILTER: "All teams" + each
  team, filters the calendar events), Rosters, and Results (team pickers).
  The old wrapping pill rows / Calendar legend are removed.
- Playoff brackets for US sports: `app/providers/espn_playoffs.py` (ESPN
  `scoreboard?seasontype=3` over the recent ~95d) → series deduped by
  `series.id`, FILTERED to games with a `series.summary` (the reliable
  "real playoff series" signal — drops regular-season/makeup games the date
  range catches), grouped into rounds (specific round note preferred over a
  generic "Playoff Series"). `GET /api/bracket/{league_id}` (`BracketOut`,
  cached 30min through `services/cache` when it has rounds; **503 when the
  ESPN fetch failed and nothing is cached** — 2026-10-11, see *An upstream
  outage is not an empty answer* below). `BracketView` gained a bracket
  `Select` (soccer cups + US
  leagues); soccer renders knockout games, US renders series cards (teams +
  status). NOTE: 2026 ESPN data is simulated/inconsistent, so round labels are
  partial ("1st Round" + "Playoff Series") — the matchups/status are correct.

## Phase 23 — bracket coverage fix + whole-league follow (2026-06-14)

- `espn_playoffs.fetch_playoff_bracket`: bumped the scoreboard `limit`
  300 → 1000. The `?dates=…&seasontype=3` window also catches regular-season
  / makeup games, and ESPN truncates to `limit` oldest-first; at 300 the feed
  was cut off mid-first-round so NBA/NHL brackets froze in round 1 even
  though the finals were over. 1000 covers the whole postseason through the
  finals. `_round_name` now relabels a conference final (`East Final` /
  `West Finals`, stripped to a bare "Final"/"Finals") as **"Conference
  Finals"** so it stays distinct from the championship ("Stanley Cup Final" /
  "NBA Finals"). The generic placeholder round ("Playoff Series" / unnamed)
  is dropped whenever any properly-named round is present (kept only as a
  last resort), removing the junk column. Verified live: NHL → 1st Round / 2nd
  Round / Conference Finals / Stanley Cup Final; NBA → 1st Round / Semifinals
  / Conference Finals / NBA Finals; MLB empty (off-season).
- Whole-league follow generalized: `CatalogLeague.supports_follow_all` now
  defaults **True** (was per-league, cups only). `get_competition_schedule`
  works for every provider/sport, so any league — NBA, the EPL, a tour —
  can be followed in full (`follow_all`, no team picks). Wizard copy made
  sport-neutral ("Follow all" / "Whole league", soccer-ball icon dropped).
  No schema/type change (fields already existed).

## Phase 27 — upcoming games on the map + venue-resolution fix (2026-06-16)

User report: "not all teams show up on the map", and the map should
"showcase where each game is happening" for a followed whole tournament
(World Cup) or a followed team, defaulting to the next 3 days (user-picked).

### Venue-resolution robustness (bug: followed teams silently missing)
A followed team with no resolved `venue_lat/lon` is omitted from `/api/map`.
Two real gaps fixed (both general, not hardcoded):
- `espn._parse_team_location(data, provider_key=None)`: when a soccer club
  has no `team.venue`/`franchise.venue`, fall back to `team.nextEvent[]`'s
  competition `venue` — but ONLY when `provider_key` is the `homeAway=="home"`
  competitor (else it's the opponent's ground). New helpers
  `_home_venue_from_next_event` / `_is_home_competitor`. `get_team_location`
  passes `team.provider_key`. (Resolves e.g. PSG → "Parc des Princes".)
- `geocode.geocode_venue(venue)`: retries `geocode` with progressively
  trimmed variants (full → "first, last" → first segment), since Nominatim
  fails on over-specified "Stadium, Street, District, City" strings but
  resolves the bare stadium name. Used by `jobs._resolve_team_location` and
  `_resolve_competition_stadium` in place of bare `geocode`. (Resolves e.g.
  Tottenham's TheSportsDB string.)

### Upcoming games on the map (`GET /api/map?days=`)
- `GET /api/map` gains `days: int = Query(3, ge=1, le=30)`. `MapOut` gains
  `games: list[MapGameOut]` and `days: int` (echoed back). `teams` unchanged.
- `MapGameOut`: `game_id, league_id, league_name?, sport, venue?, lat, lon,
  home: GameSideOut, away: GameSideOut, start_time (UTC), phase, period_label,
  group?, followed: bool, source ("followed"|"competition")`.
- Games selected: phase in {scheduled, in_progress}, `start_time` in
  `[now, now + days]`, for leagues that are `follow_all` OR have a followed
  team. Each resolved to venue coordinates, priority:
  1. `wc_venues.resolve(game.venue)` (host tournaments — covers the WC);
  2. a followed home team's `venue_lat/lon` (home games);
  3. a venue-name index built from resolved `TeamORM` + `StadiumORM` rows
     (covers away games at any already-located ground);
  4. a Redis-cached geocode (`venuecoords:{normalized}`), filled by a
     coalesced background job — games whose venue isn't cached yet are
     omitted this round and appear on a later poll (mirrors the existing
     competition-stadium "pending" pattern). Never geocodes inline.
- `jobs.refresh_game_venue_coords()` + `kick_game_venue_coords()`
  (coalesced, never-raise): geocodes upcoming-game venues not resolvable
  in-memory and caches coords/misses in Redis. Added to `daily_refresh`.
- `repository.upcoming_games_for_leagues(session, league_ids, start, end)`.

### Frontend
- `types.ts`: `MapGame` (+ `MapResponse.games`/`days`). `api.map(days?)`.
  `useMap(days?)` (queryKey includes days; polls while a competition OR games
  are present so geocoded venues fill in).
- `MapView`: a **mode toggle** "Upcoming games" (default) ↔ "Stadiums".
  Stadiums = prior behavior (followed + active competition home/host pins).
  Games = one pin per venue hosting games in the window, grouped by
  `lat,lon`; a **1–30 day number/slider input** (default 3) drives `useMap`.
  Clicking a venue pin opens `MapVenueGamesPanel` (the matchups there).

## Phase 30: betting odds + win-probability (keyless, on-demand)

Predictions/odds layer — both keyless from ESPN, never stored (same lifecycle
as `GameSummary`). US sports (MLB/NBA/NFL/NHL) are well-covered; soccer odds
are usually absent → every field is independently nullable and the UI degrades
to nothing when unpriced.

- `domain.GameOdds` (frozen) + `schemas.GameOddsOut` + `types.ts GameOdds`
  (the contracts triple, change together): `provider?, details?,
  home_moneyline?, away_moneyline?, spread? (home-relative, neg = home fav),
  over_under?, home_win_pct? (0–100), away_win_pct?`. Attached as
  `GameDetailOut.odds: GameOddsOut | None` (and `GameDetail.odds` in TS).
  `serialize.odds_to_out`.
- `SportsProvider.get_game_odds(league, provider_game_key) -> GameOdds | None`
  (on-demand, never stored, individual/leaderboard sports → None).
  - ESPN: fetch the `/summary` `pickcenter[0]` line (`_parse_pickcenter`) and
    the core-API per-game `predictor` `gameProjection` win% (`_parse_predictor`)
    CONCURRENTLY (`asyncio.gather(return_exceptions=True)`); a 404 on either
    half degrades that half only; a sustained `TransientProviderError`
    propagates (breaker), and so does a `ProviderAccessError` (401/403) on
    the `/summary` half (amended 2026-10-11 — a denied line was returned as
    "no line", which `/odds` cached for 10 minutes while the breaker
    recorded a success). New `_CORE_BASE` host (same shared client).
  - Mock: deterministic md5-seeded line + win% (`_mock_game_odds`), fictional
    book name, only for scheduled/in_progress non-individual games.
  - TheSportsDB: None.
- `GET /api/games/{id}` now also returns `odds` (best-effort `_fetch_odds`,
  never 500s). NEW `GET /api/odds?ids=csv` → `dict[game_id, GameOddsOut]`
  (`routes/odds.py`): batch for cards (Today/Calendar/Results), Semaphore(8)
  fan-out behind one fetch per provider that goes first on its own (amended
  2026-10-11), per-game cache `odds:{game_id}` through `services/cache` (10 min,
  negatives cached so
  odds-less games aren't re-fetched every poll), gated to scheduled/in_progress
  non-individual games. Mirrors `/schedule/weather`. **503 when every game
  it had to price failed upstream** (2026-10-11 — see *An upstream outage is
  not an empty answer* below); one cached or answered game keeps it a 200.
- Frontend: `api.odds(ids)`, `useGameOdds(ids)` (clone of `useScheduleWeather`).
  `GameDetailModal` `OddsSection` = win-probability split bar (away amber /
  home sky, mirrors `TeamStats`) + moneyline/spread/total rows + book
  attribution, shown for scheduled/live games only. `GameCard` optional `odds`
  prop → a favorite chip (`favoriteChip`: favored side + win%, else moneyline);
  TodayView threads the batch map in.

## Historical archives (added 2026-07-12)

- ``StandingsArchiveORM`` (``standings_archive``): one row per
  ``(league_id, season)`` — ``season`` is the numeric key ("2025-26" →
  "2026"), ``season_label`` the provider's display form. ``StandingsORM``
  remains the rolling current snapshot; the scheduler upserts every
  standings refresh into the archive too.
- An ESPN table is labelled with its OWN season (amended 2026-10-11):
  ``_parse_season`` reads ``seasonDisplayName`` / ``season`` off the
  standings blocks that were parsed, and the payload's top-level
  ``season`` only when no block carries one. That top-level object is
  the league's current season and moves on before the table does (MLB
  from the end of the regular season: top-level 2027, table 2026), which
  labelled the live table "2027", filed it in the archive under 2027 and
  sent Results to a season list with no finished game. A refresh does
  not remove an archive row already written under the wrong key.
- ``app/providers/espn_history.py`` (standalone, espn_leaders-style — the
  ``SportsProvider`` protocol is unchanged): ``fetch_season_standings`` /
  ``fetch_season_results`` via ESPN's ``?season=`` params. Team sports
  only. ``fetch_season_standings`` never raises. ``fetch_season_results``
  raises only ``EspnHistoryError`` (amended 2026-10-11), when one of the
  season's schedule calls failed; the error carries the finals the other
  calls returned (``.games``), which a caller may show but must not cache
  as the season. A call that answered with nothing is not a failure.
- ``GET /standings/{league_id}?season=YYYY`` → archive-first with a
  one-time ESPN backfill; ``GET /history/results/{team_id}?season=YYYY``
  → the season's FINAL games, cached through ``services/cache``, never
  stored in ``games``.
- ``LeagueOut`` carries ``provider`` so the frontend gates the season
  pickers (espn team sports only).

## MMA method of victory + leaderboard events on the calendar (2026-07-27)

Two of the *Loose ends* from the root ROADMAP.md, landed together (NHL
live-intermission verification and `/metrics` are still open). Both are
strictly additive: one new nullable field on `GameOut`, four new nullable
`games` columns, and one existing endpoint (`/calendar.ics`) carrying
extra entries.

### Method of victory for finished bouts

An MMA "score" is a round count (`1-0`), which says nothing about the
fight, so a finished bout now carries HOW it ended.

- `domain.FightMethod` — a closed str-enum, same idiom as
  `Sport`/`GamePhase`/`PlayerStatus`: `ko` (covers KO *and* TKO — ESPN
  spells both "KO/TKO"), `submission`, `decision`, `disqualification`,
  `no_contest`, `draw`. Provider free text is classified into it so
  nothing downstream pattern-matches a sport's vocabulary.
- `domain.FightResult` (frozen): `method: FightMethod`, plus optional
  `detail` (the provider's own flavour — "Unanimous", "Rear-Naked
  Choke"), `round` (1-based), `clock` ("4:21"). Only `method` is
  required — a partially known result is still worth showing.
  `domain.Game.fight_result: FightResult | None = None`, after `series`.
- `schemas.FightResultOut` (`method: str`, `detail?`, `round?`,
  `clock?`) and `GameOut.fight_result: FightResultOut | None = None`,
  mirrored in `types.ts` as `FightMethod` (string union), `FightResult`
  and `Game.fight_result`. The **contracts triple** — domain, schema, TS
  — changes together as always. Every endpoint serving `GameOut`
  (`/today`, `/schedule`, `/schedule/{team_id}`, `/games/{id}`,
  `/results/{team_id}`, `/history/results/{team_id}`, `/matchup/…`,
  `/nation/…`) carries it for free — no route code changed, the field
  rides the shared serializer. `null` for every non-MMA sport, every
  unfinished bout, and any bout the provider never explained.
  (`/map`'s `MapGameOut` is its own narrower shape and does not carry
  it. `/history/results/{team_id}` is in that list for shape only — it
  404s for an MMA team, see the season-history note below.)
- Persistence: `GameORM` gains `fight_method VARCHAR(24)`,
  `fight_detail VARCHAR(64)`, `fight_round INTEGER`,
  `fight_clock VARCHAR(16)` — all nullable, all appended to
  `migrations._ADDITIVE_COLUMNS`, so an existing homelab database gains
  them at startup. `repository.upsert_games` writes them clipped, and
  **never lets an incoming `None` wipe a stored result** (only the
  schedule refresh enriches bouts, so most later refreshes carry no
  method). The merge is **per field, not per block**: an incoming
  `fight_result` overwrites `fight_method` outright, but `fight_detail` /
  `fight_round` / `fight_clock` are written only when the incoming value
  is non-`None`. The round, time and flavour come from a separate
  core-API call that can fail on its own, so a thinner incoming result
  (method only) must not erase a richer stored one — the same overlay
  `espn/individual.py::_merge_fight_results` applies upstream. (The
  crest/color columns merge on *truthiness* and only ever have one
  source; `fight_*` is a stricter `is not None` test across two, so
  "same rule as the crest/color columns" is NOT an accurate shorthand
  for it.)
- `serialize._fight_result_out(domain.FightResult | None)` and
  `serialize._row_fight_result_out(GameORM)` feed `domain_game_to_out`
  and `game_to_out` respectively, so the stored and the never-stored
  (season-history) paths emit identical JSON.
- **When `GameOut.fight_result` is present.** Only for a FINAL bout, and
  each path enforces that differently:
  - stored (`game_to_out` → `_row_fight_result_out`) returns `None`
    unless `row.phase == "final"` **and** `fight_method` is set. The
    phase gate is load-bearing, not belt-and-braces: the `fight_*`
    columns deliberately outlive the phase (nothing wipes them), so a
    bout ESPN reopens — a corrected state, a result under review — would
    otherwise keep serving the superseded finish while it is back to
    `in_progress`.
  - never-stored (`domain_game_to_out` → `_fight_result_out`) has no
    phase gate and needs none: a domain `Game` only ever carries a
    `fight_result` for a finished bout, because
    `espn/individual.py::_scoreboard_fight_result` returns `None` unless
    the parsed state is `GamePhase.FINAL`, and `_attach_fight_results`
    only targets FINAL bouts. **That path is unreachable for MMA
    today** — its one caller is `services/season_results.py`, which
    `supports_history` gates to team sports (see below). It is kept
    symmetric so the field rides along for free if that ever opens up.
  - the frontend applies the same rule independently
    (`fightResultOf(game)` is null unless `phase === "final"`), so a
    server that ever regressed here still wouldn't render a result on a
    live bout.
- ESPN adapter (`espn/individual.py`): ONE keyword classifier
  (`_classify_fight_method`, `_FIGHT_METHOD_PATTERNS`) over two sources.
  Pattern order is load-bearing — no_contest > disqualification >
  submission > ko > draw > decision — so "Technical Submission" isn't a
  knockout and "Split Draw" isn't a decision; matches are word-bounded
  and only abbreviations ESPN actually publishes (`dq`, `sub`) are
  matched, since `\bdec\b` would read a date and `\bnc\b` a state code.
  (a) `_scoreboard_fight_result` reads the already-fetched
  `status.type.detail` ("Final - Decision - Unanimous") for free — often
  just "Final", which classifies as nothing; (b) `_parse_bout_status`
  reads the core API's per-bout status feed, which also carries the round
  and stoppage time. `_merge_fight_results` lets the core feed win on the
  method while anything it omits survives from the scoreboard, so a core
  outage still leaves whatever the card spelled out.
- Fan-out bound (`espn/provider.py::_attach_fight_results`, mirroring the
  `_STAT_LINE_*` roster backfill): exactly ONE core call per finished
  bout, for bouts that are FINAL, involve the followed fighter, fall
  inside the requested window and have a known status URL; sorted
  newest-first and capped at `_FIGHT_RESULT_MAX_BOUTS = 8` with
  `_FIGHT_RESULT_CONCURRENCY = 4`. `_mma_status_urls` prefers the
  payload's own `status.$ref` (rewritten http→https, rejected otherwise)
  and otherwise builds the core URL from the card's event id + the bout's
  competition id — UFC is the one place those ids differ. Per-bout
  `httpx.HTTPError`, `TransientProviderError` *and* `ValueError` are
  swallowed so a bout-status blip can't trip the provider breaker — the
  last of those covers `json.JSONDecodeError`, because the core API
  answers an outage with an HTML error page under a `200`, and a
  best-effort garnish must degrade on that exactly as on a dropped
  connection.
- Deliberately NOT enriched: `get_live_games` — no team scope, so it
  could only fan out over every bout on every card.
- **MMA has no season archive at all**, so there is no "history" path to
  enrich or to reason about: `espn_history.supports_history(sport)` is
  `sport not in INDIVIDUAL_SPORTS and sport not in LEADERBOARD_SPORTS`,
  and MMA is an individual sport. `fetch_season_results` returns `[]`
  before it builds a URL, and `GET /history/results/{team_id}` 404s
  ("No season archive for this league") for a followed fighter. A
  fighter's past bouts come from the stored `games` rows the schedule
  refresh already enriched — i.e. `/results/{team_id}` — so nothing is
  lost, but do NOT describe this as a fan-out decision: the code path
  does not exist. (An earlier draft of this section listed
  `fetch_season_results` alongside `get_live_games` as "deliberately not
  enriched, unbounded fan-out over a whole career". That was wrong on
  both counts and is corrected here.)
- Frontend (`components/StatusBadge.tsx` owns the helpers, exported):
  `fightMethodLabel(method)` via an exhaustive
  `Record<FightMethod, string>` (adding a method upstream fails `tsc`
  rather than rendering `undefined`), `fightResultOf(game)` (null unless
  `phase === "final"`), `fightRoundLabel(result)` (`"R2 4:21"`),
  `fightResultSummary(result)` (`"KO/TKO · Punches · R2 4:21"`). The
  final pill reads `FINAL · KO/TKO` with the full summary as its title;
  `GameCard` adds a zinc `R2 4:21` chip in the slot the odds chip vacates
  once a game is final; `GameDetailModal` leads with a **Result** block —
  for an individual sport it is the only body content there is.

### Leaderboard events on the calendar and in the .ics feed

Golf `Event`s (multi-day tournaments) now appear on the Calendar grid and
in the subscription feed as multi-day ALL-DAY entries. No API shape
changed — the calendar sources them from the existing `GET /events`, so
there is no openapi drift from this half.

**GOLF ONLY — not "golf/tennis".** `domain.LEADERBOARD_SPORTS` is
`frozenset({Sport.GOLF})`. A tennis match is a two-sided `Game` (its
tournament is carried as the `series` label), never an `Event`, so no
tennis entry can reach `/events`, the calendar grid, or the `.ics` feed —
tennis has been on the Calendar as an ordinary timed fixture since Phase
4. Any "golf/tennis events" phrasing is a factual error; it was corrected
throughout the docs, the module docstrings and the code comments on
2026-07-27. This is the wording to reuse: *"leaderboard competitions
(golf tournaments — golf is the only sport modeled as an `Event`)"*. If a
second leaderboard sport is ever added it joins `LEADERBOARD_SPORTS`
first, and the docs follow the set, not the other way round. **That
happened on 2026-07-28: racing joined the set** (see *Motorsport* below),
so `LEADERBOARD_SPORTS` is now `frozenset({Sport.GOLF, Sport.RACING})`
and race weekends draw as all-day spans too; the tennis point above is
unchanged.

- `services/ics.py::games_to_ics` gains a keyword-only
  `events: Sequence[EventORM] = ()`; existing two-arg callers still
  render a games-only feed. The private `_event_lines` was renamed
  `_game_lines` (ambiguous once a domain `Event` is also a VEVENT), and
  `_event_span_lines` renders the new all-day entries:
  `UID:event:{event.id}@sportsdash`, `DTSTART;VALUE=DATE:YYYYMMDD`,
  `DTEND;VALUE=DATE:YYYYMMDD`, `SUMMARY:{event.name} ({league.name})`,
  optional `LOCATION`, and for a FINAL event a
  `DESCRIPTION:Winner: {name} ({score})` from the first leaderboard row
  (the stored board is position-ordered; read defensively, as
  `serialize.event_to_out` reads it).
- The event `UID` is **prefixed `event:`** (`ics._EVENT_UID_PREFIX`),
  mirroring the grid's `SPAN_ID_PREFIX`, for the same reason and not a
  weaker one: a game id and an event id are both `"{provider}:{key}"`
  strings and one `VCALENDAR` holds both kinds side by side, so nothing
  but the prefix keeps their identities apart. The ICS namespace is NOT
  safer than the grid's — it is the same id space in the same container.
  It only *looks* safer because nothing parses the UID back out, whereas
  the grid round-trips its id through `eventIdFromSpanId` to pick a
  modal; but a UID is what a subscribed client matches an entry on across
  refreshes, so a collision would have one kind silently overwrite the
  other. Pinned by
  `test_event_uid_is_prefixed_so_it_cannot_collide_with_a_game`, which
  renders a game and an event that share one id string. (Colons need no
  RFC 5545 escaping in a TEXT value, and game UIDs already contain one.)
- **`DTEND` is EXCLUSIVE** for `VALUE=DATE` (RFC 5545) — it is the day
  *after* the last day, so a Thu–Sun major ends on the Monday. A null
  `end_time`, or one predating the start, collapses to a single day.
- The DATE values are **local calendar days in `SPORTSDASH_TIMEZONE`**,
  not UTC dates: an all-day date is a display value, so this is the usual
  response-boundary conversion (`ics.py` reads `get_settings().tzinfo`,
  as `services/events.py` / `notify.py` / `weather.py` do). Storage and
  comparison stay UTC.
- `/calendar.ics` feeds `repository.events_between()` over the same
  −30d…+60d window (an event OVERLAPS a range where a game falls inside
  one). A `?team_id=` feed omits events entirely — an event has no team
  side, so no team's calendar owns it; the grid follows the same rule, so
  the UI and the feed agree. `events_between`'s overlap semantics are
  **Phase-5 behaviour that predates this feature**;
  `test_events_between_matches_overlap_not_just_start` (added here) is a
  regression net around code the calendar merely became the second caller
  of — do not count it as coverage of the calendar/ICS work, which is
  covered by `tests/test_calendar_events.py`.
- **A followed golfer has no per-team feed, by decision.** Individual
  sports are followed as athlete-as-team `TeamORM` rows, and a golfer
  (the one `LEADERBOARD_SPORTS` case) never appears on either side of a
  game — so the rule above leaves their `?team_id=` feed permanently
  empty. Considered and rejected: filtering events by the golfers in
  them. The only link from a stored event back to a followed golfer is
  `leaderboard[].player_id` (rewritten ESPN-id → internal-id by
  `scheduler/common.py::_tag_followed_golfers`, since renamed
  `_tag_followed_athletes`), which exists only once
  the provider publishes a field — future tournaments arrive from the
  season calendar with an empty board, so such a feed would carry the
  tournaments the golfer has already played and none of the ones they are
  about to. That is the wrong half for a *subscription*, and it fails in
  a way the user can't see. Instead the **frontend never offers the
  row**: `src/views/calendar/gameSideFollows.ts::appearsOnGameSide`
  (whose `LEADERBOARD_SPORTS` mirrors the backend set) filters
  leaderboard-sport follows out of **both** controls in the Calendar
  header — the Subscribe menu, which then notes that tournaments ride in
  the all-teams feed, and the team filter, which notes that All teams is
  the view that shows them. One predicate, because the two shipped
  disagreeing (2026-07-27): the filter went on offering golfers, and
  picking one produced a permanently empty grid — a golfer matches
  neither side of a game, and spans are hidden whenever the filter is not
  `"all"`. A team whose league has not loaded yet is kept by both rather
  than flickering out mid-choice. Pinned by
  `src/views/calendar/gameSideFollows.test.ts`. **Note the move:**
  `LEADERBOARD_SPORTS` now lives in `views/calendar/gameSideFollows.ts`,
  not in `views/CalendarView.tsx` where earlier text placed it.
  Tennis/MMA athletes are athlete-as-team too but their matches ARE
  games, so their per-team feeds work and they are deliberately NOT
  filtered. The backend is unchanged — one rule, "a per-team feed is that
  team's games", with no sport special-casing in the route.
- Frontend: `api.events({start, end}) -> Promise<SportEvent[]>` →
  `GET /events?start=&end=`, and `useEvents(start, end)`, queryKey
  `["events", start, end]`, default cache policy. `api.event(id)` was
  deliberately not added — the calendar's modal renders from the
  already-loaded event, as Today's does.
- (Superseded in part on 2026-10-11 by "Box Score: the Calendar view":
  `eventSpan` now returns `{eventId, sport, start, end}` only, and the
  title, id prefix, glyphs and span colours described in this bullet and
  the next went with FullCalendar. The exclusive `end` and the server-zone
  days stand.)
- `src/views/calendar/eventSpans.ts` — a pure helper with its own vitest
  suite, same pattern as `views/map/travel.ts`: `eventSpan(event) ->
  EventSpan {id, eventId, title, sport, start, end}` where `end` is the
  EXCLUSIVE local day key (FullCalendar's all-day `end` has iCalendar's
  off-by-one, so both sides derive it from one expression),
  `eventIdFromSpanId(spanId) -> string | null`, `SPAN_ID_PREFIX =
  "event:"`. Game ids and event ids are both `"{provider}:{key}"` and now
  share one FullCalendar list, hence the prefix rather than a loosely
  typed `extendedProps` discriminator. Its `SPORT_GLYPH` map holds
  **golf only** — the one sport that can reach it; the map keeps the
  per-sport shape for a future leaderboard sport, and `eventSpan` falls
  back to the bare event name for any sport without an entry.
- `CalendarView` draws spans with a solid `leagueFallbackColor(sport)`
  fill and `isLightColor`-derived text (the only theme-proof option —
  a translucent fill would be unreadable under the light
  `[data-theme]`s), a ⛳ glyph in the title, and opens the existing
  `EventLeaderboardModal` on click. Hidden while the team filter is set,
  matching the per-team feed — which is why the filter offers only
  game-side follows: a golfer selection would hide the very spans it was
  picked for. A filter value that leaves the options (unfollowed
  elsewhere, or recognized as a leaderboard follow once its league
  arrives) resets to `"all"`, so the Select's display and the grid cannot
  disagree — `Select` falls back to showing "All teams" for a value it
  does not list, which would otherwise read as unfiltered over a grid
  still filtered to a team that is no longer offered. The refresh button
  spins on
  `scheduleQuery.isFetching || eventsQuery.isFetching` — events are
  primary content, unlike the secondary weather batch. Changing the
  visible range **clears `selectedEventId`**: the new window re-queries
  `/api/events`, so a tournament picked in the old one is no longer on
  the grid, and a lingering id would re-open the modal by itself the next
  time the user navigated to a window that contains that tournament.
- This closes the Phase 5 *Frontend (views owner)* nice-to-have
  "CalendarView: optionally show event spans" and adds the
  `api.events`/`useEvents` pair that section prescribed but never landed.

## Audit fixes — FK enforcement, honest map pins, named markers (2026-07-27)

Four corrections from the `fix/audit-2026-07` sweep. Two are recorded in
place because they narrow an earlier contract rather than add to it — the
venue-composition rule is under *Phase 11 → Stadium enrichment*, and the
Calendar team-filter rule is under *Leaderboard events on the calendar*
above. The other two are here.

### SQLite now enforces foreign keys, and startup sweeps the orphans

**This changes runtime behaviour for the deployment most users are on.**
SQLite parses `REFERENCES` but ignores it unless asked, per connection — so
until now the desktop / homelab build was the one deployment where a delete
that orphaned children succeeded silently, while Postgres (Compose) rejected
it. That asymmetry is how `replace_followed` came to leave `standings_archive`
rows behind with no league.

- `app/db.py::_enable_sqlite_foreign_keys` is registered as a `connect`
  listener on the sync engine, and **only when the dialect is sqlite**. It
  runs `PRAGMA foreign_keys=ON` on every connection. It is the same PRAGMA
  `tests/db_engine.py` has always set, so what the suite proves is now what
  ships — previously the suite was *stricter* than production, which is the
  wrong way round for a constraint bug to hide.
- `app/migrations.py::prune_orphaned_rows(conn)` is the data-level
  counterpart, run by `init_db` inside the same `engine.begin()` block, in
  order: `create_all` → `run_additive_migrations` → `prune_orphaned_rows`.
  Last, and before the app opens any session, because enforcement turns
  pre-existing junk from invisible into fatal: under the PRAGMA, any
  statement touching such a row fails on a constraint the row never
  satisfied.
- What it sweeps: for every mapped table present in the database, every
  single-column foreign key whose parent table also exists (composite FKs
  are skipped; the schema has none). A row is an orphan when its FK column
  is **non-NULL** and no parent row matches — a nullable FK left NULL is
  not an orphan and is never touched.
- What it refuses to sweep: a table that some *other* table's foreign key
  points at — `teams` is the only case. Deleting one could violate the very
  constraint this repairs, and no code path can orphan a team anyway
  (`replace_followed` re-points a kept team at its league before any league
  is deleted, and deletes removed teams before removed leagues), so those
  rows are **counted and logged at WARNING, then left in place**.
- Deletions are logged at WARNING with the row count and both table names.
  Idempotent, and a guaranteed no-op on Postgres, which never allowed the
  rows — it still runs there so both dialects exercise the same SQL.
- **Upgrade note.** A database created by 1.3.0 or earlier can hold these
  rows. The first launch after this change sweeps them — once, at startup,
  with a WARNING line naming what went. The known case is
  `standings_archive` (a re-follow dropped the league); those rows were
  already unreachable junk, so nothing a user can see is lost. Repeated in
  user-facing terms in [docs/desktop.md](desktop.md).

### Map markers carry their own accessible name

MapLibre adopts every marker element as a `role="button"` and stamps its own
default name on any that arrives unlabeled, so the whole map read as twenty
identical buttons called "Map marker".

- `src/views/map/labels.ts` (pure string builders, unit-tested in
  `labels.test.ts`, same pattern as `views/map/travel.ts` — the repo has no
  component-test harness): `teamMarkerLabel(team, highlightToday, inTransit)`
  names the club and its ground plus the state its badge encodes ("playing
  today", "traveling to an away game"); `venueMarkerLabel(group,
  highlightToday)` names the venue and what its number counts ("N upcoming
  games"); `clusterMarkerLabel(count, hasFollowed, mode)` names a **count and
  an action**, not a place — `"12 venues, zoom in"`, with `", including teams
  you follow"` when the badge wears its amber ring, and the count abbreviated
  exactly as the badge prints it.
- A pin's name states its identity plus what its own badge shows; the fuller
  story (next match, scores, the fixture list) stays in the hover label and
  the side panel. An accessible name is announced on every reach, so it has
  to stay short. That is why an individual pin gets no "a team you follow"
  suffix while a cluster does: the cluster's ring is the only place that
  information exists.
- The name is written to the marker host **before** `addTo` (MapLibre only
  defaults a marker that arrives unlabeled) and is signed into the marker's
  `sig` (`views/map/reconcile.ts`), so a **kept** marker re-labels when its
  content moves instead of freezing at first paint — the same defect class
  this branch fixed for the hover label and the badge count. `rebindSpec`
  now sets `aria-label` alongside rebinding the spec.
- Decorative plane and fan markers are `aria-hidden="true"`
  (`views/map/markers.ts`): they are unclickable, but MapLibre would still
  file each one as a nameless button, so a crowd becomes a crowd of buttons.
- **Still missing, pre-existing:** MapLibre gives markers `role="button"` but
  never a `tabindex`, so pins are announced and still not keyboard-reachable.
  Naming them was the scoped defect; a blanket `tabindex="0"` would put 700+
  tab stops in front of a "follow all" user. Tracked in
  [ROADMAP.md](../ROADMAP.md) *Loose ends*.
- Sections *Phase 11 → Frontend* and *Phase 14* describe the marker
  rendering and the hover label this sits alongside.

## Game broadcasts ("where to watch") + highlight clips (2026-07-28)

Two additive features, landed together: one new list field on `GameOut`
(backed by one new `games` JSON column) and one new list on
`GameSummaryOut` (never stored). No route code changed; both ride the
shared serializers.

### Broadcasts: which networks carry a game

- `domain.Game.broadcasts: tuple[str, ...] = ()`, after `fight_result` —
  broadcast network names, national market first. `()` whenever the
  provider lists none. Purely additive: individual sports
  (`espn/individual.py::_parse_individual_competition`) never fill it and
  keep their shape via the default.
- Parsing (`espn/games.py::_parse_broadcasts`, called from `_parse_event`
  beside the venue read): prefers the scoreboard competition's
  `geoBroadcasts[]` — it carries a structured `market.type` plus
  `region`/`lang`, so foreign feeds can be filtered — taking
  `media.shortName` from entries with `region == "us"` and
  `lang == "en"`, ordered National → Home → Away (`market.type` compared
  case-insensitively; unknown markets sort last; the sort is stable so
  the feed's own order survives within a market). When geo yields
  nothing it falls back to the flat `broadcasts[].names` (national
  market entries first). Deduped preserving order, capped at
  `_BROADCASTS_MAX = 6`, and fully defensive — malformed entries are
  skipped, never fatal. Upcoming (`pre`) games carry both arrays too
  (verified live), so the chip works pre-game with no extra fetch.
- `schemas.GameOut.broadcasts: list[str] = []`, mirrored in `types.ts`
  as `Game.broadcasts: string[]` (placed between `fight_result` and
  `phase`, matching schemas.py order; required client-side — the backend
  defaults to `[]`). The contracts triple (domain, schema, TS) changed
  together as always, with the regenerated OpenAPI snapshot. Every
  endpoint serving `GameOut` (`/today`, `/schedule`,
  `/schedule/{team_id}`, `/games/{id}`, `/results/{team_id}`,
  `/history/results/{team_id}`, `/matchup/…`, `/nation/…`) carries it
  for free via the shared serializer. (`/map`'s `MapGameOut` is its own
  narrower shape and does NOT carry it.)
- Persistence: `GameORM.broadcasts: Mapped[list] = mapped_column(JSON,
  default=list)` (the `TeamORM.rss_feeds` precedent), with
  `("games", "broadcasts", "JSON")` appended to
  `migrations._ADDITIVE_COLUMNS` — the FIRST JSON column through the
  additive path; `tests/test_db_migrations.py` proves the DDL against
  the production sqlite engine. Rows predating the migration hold NULL,
  which both serializers read as `[]`.
- Merge rule (`repository.upsert_games`): the insert branch writes
  `list(game.broadcasts)`; the update branch overwrites **only when the
  incoming list is non-empty** — the crest/color truthiness rule, and
  accurately so this time (one source, whole-value overwrite; unlike the
  per-field `fight_*` merge). A state-only refresh or a feed that omits
  the arrays never wipes a stored lineup of networks. The column is
  always REASSIGNED a fresh list, never mutated in place — SQLAlchemy
  does not change-track in-place JSON mutation (same gotcha as
  `NotificationPrefORM.events`, repository.py's standing comment).
- Serialization: `game_to_out` emits `list(row.broadcasts or [])`
  (pre-migration NULL → `[]`); `domain_game_to_out` emits
  `list(game.broadcasts)` — parity enforced in `tests/test_serialize.py`.

### Highlight clips on the game detail (never stored)

- `domain.Clip` (frozen): `headline: str`, `url: str` (the provider's
  clip PAGE, required — the raw media URLs are unreliable/auth-gated),
  plus optional `description`, `duration_seconds`, `thumbnail_url`.
  `domain.GameSummary.clips: tuple[Clip, ...] = ()`.
- Parsing (`espn/summary.py::_parse_clips`, from the summary payload's
  top-level `videos[]`): a clip needs a `headline` and a
  `links.web.href` to be kept; entries whose
  `timeRestrictions.expirationDate` parses to a past instant are dropped
  (the provider pulls expired clips, so linking one would 404), while an
  absent or unparseable expiry keeps the clip. Capped at
  `_CLIPS_MAX = 12`; per-clip try/except skip, never fatal.
- `schemas.ClipOut` (`headline`, `description?`, `duration_seconds?`,
  `thumbnail_url?`, `url`) and `GameSummaryOut.clips: list[ClipOut] = []`,
  emitted by `serialize.summary_to_out`; mirrored in `types.ts` as
  `Clip` (field-for-field) + `GameSummary.clips` (appended last).
- Deliberately NO ORM/migration/repository change: `GameSummary` is the
  contractually never-stored container, `fetch_summary` refetches live
  per `GET /api/games/{game_id}` request (no caching), and clip URLs are
  volatile provider assets with no precedent for persistence. Anything a
  LIST view ever needs (e.g. a "has highlights" badge) would instead be
  a stored `Game` column with migration + merge rule — not this field.
- Reach: clips ride `GET /api/games/{game_id}` only (`GameDetailOut.summary`).
  They can never appear for individual sports —
  `EspnProvider.get_game_summary` returns `None` up front for
  tennis/MMA/golf — and soccer effectively ships none (empty `videos[]`
  verified across MLS/EPL), so the frontend must degrade gracefully on
  an empty list (and does — see below).

### Frontend

- `GameCard`'s left footer chip rail (StatusBadge → odds chip → fight
  chip → why-watch pill → broadcast chip) gains a display-only
  broadcast chip: the zinc fight-chip recipe at `text-[10px]`, showing
  `broadcasts[0]` truncated at `max-w-[7rem]`, with every network
  joined `" · "` in the `title` tooltip. Gated to
  `scheduled | in_progress`, and display-only because the card is one
  `<button>` — no nested interactive elements. The rail itself is
  shrinkable (`min-w-0`; the former `shrink-0` was dropped) so the
  four-chip stack cannot overflow the card border on one-column widths
  — the broadcast chip (`min-w-0` + `truncate`) collapses first while
  fixed-content chips keep their size; the venue span's behavior is
  unchanged.
- `GameDetailModal`: the matchup header gains a quiet third line —
  `broadcasts.join(" · ")`, `text-xs text-zinc-500`, only when
  non-empty (any phase) — and a self-hiding **Highlights** section
  renders in DetailBody after the play-by-play timeline: house h3 + one
  bordered box with a divided list, each clip row a single
  `<a target="_blank" rel="noreferrer">` (the NewsDetailPanel
  convention) with an optional `aspect-video object-cover
  loading="lazy" alt=""` thumbnail, truncated headline, and `m:ss`
  duration when `duration_seconds` is set. `hasSummaryContent` now
  includes `clips.length > 0`, so a clips-only summary no longer shows
  "Box score not available".

### Tests

`tests/test_espn.py`: geo-preferred ordering + foreign-feed filtering
(fixture), flat-names fallback (fixture), case-insensitive markets +
dedupe + malformed-entry skips, cap + absent-keys default; clip fixture
parse (full + minimal clip), expired/missing-url/missing-headline/
malformed skips, unparseable-expiry keep, cap. `tests/test_repository.py`:
insert stores, empty incoming preserves, non-empty overwrites.
`tests/test_serialize.py`: both serializers emit identical `broadcasts`,
pre-migration NULL reads as `[]`. `tests/test_db_migrations.py`: the
JSON column arrives on a pre-existing production-path sqlite database,
NULL on existing rows, list round-trips. Fixtures
(`espn_scoreboard.json`, `espn_basketball_summary.json`) gained
fictional networks/clips only.

## Prometheus-style `/api/metrics` (2026-07-28)

The last Phase-7 loose end, previously *Explicitly declined* in
ROADMAP.md. Hand-rolled exposition-format 0.0.4 — **no new
dependency** (half of the original objection), and every series reads
the SAME sources `/api/health` reads (the other half: two endpoints must
never tell different stories about the same breaker). The desktop
PyInstaller bundle is unchanged.

### `GET /api/metrics` (`routes/metrics.py`)

`text/plain; version=0.0.4; charset=utf-8`, `# HELP`/`# TYPE` per
family, escaped label values (`\\`, `\"`, `\n`), no timestamps, trailing
newline. Never raises — a failed database probe degrades the gauge, not
the status code. Series:

| Series | Type | Source (shared with `/api/health`) |
|---|---|---|
| `sportsdash_database_up` | gauge | the same `SELECT 1` probe `health()` runs; 1 up / 0 down |
| `sportsdash_provider_circuit_state{provider}` | gauge | `circuit_breaker.all_breakers()` × `registry.provider_ids()`; 0 closed / 1 half_open / 2 open. A provider with no live breaker (never called) reads 0, exactly as `/health` reads it as permanently `closed` |
| `sportsdash_provider_consecutive_failures{provider}` | gauge | `CircuitBreaker.failures` — a gauge, **not** a counter: it resets to 0 on any success |
| `sportsdash_provider_last_error_timestamp_seconds{provider}` | gauge | `CircuitBreaker.last_error_at` as unix seconds; the series is omitted for providers that never errored |
| `sportsdash_background_tasks` | gauge | `len(background._tasks)` — the strong-reference set is the in-flight set |
| `sportsdash_cache_hits_total` / `sportsdash_cache_misses_total` | counter | new module-level ints in `services/cache.py`'s `cache_get_json` (accessor `cache_counters()`). A hit returned a decoded value — from Redis, or (2026-10-11) from the in-process store that stands in for it when no `redis_url` is set; everything else — no client configured and nothing stored in-process, GET failure, absent or expired key, invalid JSON — is a miss, matching the cache's best-effort semantics. The `# HELP` text still says "Redis cache reads" |
| `sportsdash_scheduler_job_runs_total{job,result="success"\|"error"}` | counter | `services/metrics_state.py` job registry (below) |
| `sportsdash_scheduler_job_last_run_timestamp_seconds{job}` | gauge | same registry; omitted before a job's first run |

### `services/metrics_state.py` — scheduler-job registry

The `AsyncIOScheduler` instance is a local inside `main.lifespan`, so
the route cannot ask it anything. Instead a module-level registry
(same pattern as `circuit_breaker._breakers`): `setup_scheduler` wraps
each of the five job coroutines (`daily_refresh`, `refresh_news`,
`refresh_pregame_rosters` — added 2026-07-29 — `live_tick`,
`events_tick`) with `metrics_state.instrumented(job_id,
func)`, which seeds the job id eagerly (all jobs visible with zero
counts from startup) and ticks success/error counts plus a last-finish
timestamp per run. Jobs are defensive and never raise; if one escapes
anyway it counts as `result="error"` and re-raises so APScheduler's own
logging still fires. `reset_all()` exists for test isolation, mirroring
the breaker registry.

Scrape targets: `127.0.0.1:8001/api/metrics` on the docker host (the
api port is loopback-only), `api:8000/api/metrics` inside the compose
network, or `:3000/api/metrics` through the frontend proxy (which
forwards only `/api` paths — the reason the route is not root-mounted).

*Verified per the old ROADMAP criterion:* `tests/test_metrics.py`
asserts each series against the `/api/health` body in the same test —
DB probe down ⇒ `sportsdash_database_up 0` **and** `database: false`;
breaker open ⇒ state gauge 2 **and** `provider_health.….circuit ==
"open"` + `status: "degraded"`.

## Motorsport: F1 / NASCAR Cup / IndyCar as leaderboard leagues (2026-07-28)

**Domain.** `Sport.RACING = "racing"` (`app/models/domain.py`). Racing is
a member of BOTH `LEADERBOARD_SPORTS` (modeled as `Event`, never `Game` —
the set is now `frozenset({Sport.GOLF, Sport.RACING})`) and
`INDIVIDUAL_SPORTS` (a followed driver is a single-member "team" whose
`provider_key` is the ESPN athlete id). Racing is NOT in `WEATHER_SPORTS`.

**Catalog** (`app/providers/espn_catalog.py`). Three pickable leagues, all
`supports_follow_all=True`, provider_keys and logo_urls live-verified
2026-07 against each `racing/{key}/scoreboard` (`racing/nascar` and
`racing/indycar` 400 — the working keys are below):

| id | name | provider_key |
|---|---|---|
| `f1` | Formula 1 | `racing/f1` |
| `nascar-cup` | NASCAR Cup Series | `racing/nascar-premier` |
| `indycar` | IndyCar Series | `racing/irl` |

The picker's driver list is the season driver standings UNIONED with
the current scoreboard's field. The standings come from the `/apis/v2/`
standings host (`https://site.api.espn.com/apis/v2/sports/{provider_key}/standings`
— the `site/v2` host's `/standings` is a stub). Every
`children[].standings.entries[]` row carrying an `athlete` object is a
driver (F1's constructor child carries `team` rows and is passed over —
no child-name matching). `CatalogTeam.provider_key` = `athlete.id`,
abbreviation = `athlete.abbreviation` else name-initials, `logo_url` is
CONSTRUCTED as `https://a.espncdn.com/i/headshots/rpm/players/full/{id}.png`
(headshots appear nowhere in racing payloads; the `rpm` path is shared
across series, verified live; a missing backmarker image 404s to the
picker's initials chip). Standings rank order comes first, then the
scoreboard competitors not already present (substitutes/part-time
drivers holding no championship entry) — competitor-level `id` IS the
athlete id (the nested `athlete` object has NO id) — deduped by athlete
id, cap 120 across the union. When the standings yield nothing (or are
down) the scoreboard field stands alone; a scoreboard fetch failure
with standings in hand degrades to standings-only; empty results are
not cached; both sources down raises `EspnCatalogError` (→ 502).
`GET /setup/leagues` derives `entity_noun: "driver"` for racing.

**Parser** (`app/providers/espn/racing.py`, sibling of `golf.py`). A racing
scoreboard event is one race WEEKEND → one `Event`:

- `id = "espn:{event.id}"` (F1 ids are 9-digit; NASCAR/IndyCar ids are
  date-encoded `YYYYMMDDNNNN` — same shared events table, no collisions
  observed).
- `start_time` = event `date`; `end_time` = event `endDate`, falling back
  to the latest session `date` (NASCAR/IndyCar have no `endDate`), else None.
- `phase` from event `status.type.state` via the shared `_map_phase`
  (pre→scheduled, in→in_progress, post→final; POSTPON/CANCEL by name).
- `round_label`: only while in progress — the first session in state `"in"`
  names it by `type.abbreviation` ("FP2", "Qual", "SS", "SR"); the Race
  session (or the sole typeless competition, which IS the race for
  NASCAR/IndyCar) appends lap progress from `status.period` when truthy:
  `"Race · Lap 44"`. Bounded to 48 chars (`EventORM.round_label` is
  String(48)). Scheduled/final → `""`.
  **Since 2026-07-29** the provider extends that label in place to
  `"Race · Lap 44 of 70"` when the core API yields the race distance (see
  *Core-API board enrichment* below). The extension only ever touches a
  label that already carries the `" · Lap "` clause — a practice or
  qualifying label, and a finished weekend's empty label, are left alone —
  and the result is still bounded to 48 chars (and re-clipped by
  `repository._clip`). Without the distance the label is unchanged.
- `leaderboard`: the Race session's competitors (finishing/running order);
  when the race has no field yet, the latest session that has competitors
  (quali/practice classification); else `()` — scheduled races often ship
  no entry list at all. Rows ordered by `order`: `position = order`,
  `position_label = str(order)` (racing positions are unique — no "T" tie
  labels), `name = athlete.displayName` (fallback `fullName`), `score = ""`
  and `detail = "Winner"` when `winner` is true else `""`. That is the
  **un-enriched** board and it is still exactly what the parser alone
  produces — the site scoreboard carries no per-driver time, lap, grid slot
  or constructor, with **one 2026-07-29 exception**: on a board session in
  state `"in"` the parser reads that competitor's own `statistics` slot for a
  running gap, which is the only per-driver number the site payload has ever
  been thought to carry (and has never been observed carrying — see *Live
  gaps* below). Since 2026-07-29 the provider fills both columns from the
  core API afterwards; see *Core-API board enrichment* below. Enrichment
  only ever ADDS, so a row it cannot reach — or a column it cannot render —
  keeps precisely the values above, and a core-API outage costs nothing but
  garnish.
  A competitor whose `order` is missing/zero/non-numeric sorts to the
  END of the board and takes a trailing sequential position
  (rows-so-far + 1) — it can never surface as P1/the winner; a field
  where no competitor carries `order` (an entry list) still numbers
  1..N in feed order.
- `LeaderRow.player_id` carries the ESPN athlete id TRANSIENTLY — the
  scheduler rewrites it to the internal followed-team id (or None) before
  persisting, exactly the golf tagger contract.
- Fully defensive: malformed events/competitors log + skip, never raise.

**Provider** (`app/providers/espn/provider.py`). `get_events` /
`get_event_state` now dispatch the scoreboard parser by sport (golf →
`_parse_golf_scoreboard`, racing → `_parse_racing_scoreboard`); racing's
`get_event_state` rescans the current scoreboard exactly like golf
(racing's `summary?event=` 404s — verified live). NEW GATE (took the
documented ROADMAP loose end): `get_schedule` and
`get_competition_schedule` return `[]` early for `LEADERBOARD_SPORTS`
without any fetch — a leaderboard league has no two-sided games, and the
old tour-scoreboard scan only produced "Skipping malformed" log noise.
Golf behavior is unchanged (it already yielded nothing there); a unit test
pins that no games-path fetch is issued for a leaderboard league.

**Scheduler** (`app/scheduler/common.py` + call sites in `refresh.py`,
`live.py`). Helpers generalized, behavior identical: `_is_golf` →
`_is_leaderboard` (membership in `LEADERBOARD_SPORTS`), `_golfer_id_map` →
`_athlete_id_map`, `_tag_followed_golfers` → `_tag_followed_athletes`.
Daily pass 4 (`_refresh_events`) and `events_tick` therefore cover racing
leagues automatically (followed drivers + racing `follow_all` leagues);
FINAL notifications, missed-final resend, ICS spans, `/events` and
`/today` all work unchanged. The `events_tick` job's human name is now
"Leaderboard (golf/racing) poll".

**Config** (`backend/config/teams.yaml`). Comment template updated:
`sport` accepts ten values (`… | volleyball | racing`); tennis/MMA/golf/
racing entries under `teams:` are single athletes with ESPN athlete-id
provider_keys.

**Frontend** (landed with the backend, `frontend/src`):

- `types.ts` `Sport` union gained `"racing"` (last, mirroring the
  backend enum order); the stale `entity_noun` comment now enumerates
  `"team" | "player" | "fighter" | "golfer" | "driver"`.
- Frontend mirrors of the backend frozensets, updated in lockstep:
  `views/calendar/gameSideFollows.ts` `LEADERBOARD_SPORTS` =
  `{"golf", "racing"}` — keeps driver follows out of the calendar team
  filter and the per-team Subscribe menu, pinned by
  `gameSideFollows.test.ts` (sorted-set mirror assertion + a driver-drop
  case); `views/RosterView.tsx` `INDIVIDUAL_SPORTS` = `{"tennis", "mma",
  "racing"}` (empty-roster friendly note; golf was already absent there
  and was left as-is); `lib/seasons.ts` `NO_ARCHIVE_SPORTS` =
  `{"tennis", "mma", "golf", "racing"}` — no season-archive standings
  path for leaderboard sports.
- Exhaustive `Record<Sport, …>` maps (compile-forced):
  `components/GameCard.tsx` `SPORT_FALLBACK_COLORS.racing = "#64748b"`
  (checkered-flag slate, shared with the calendar);
  `components/onboarding/LeagueStep.tsx` `SPORT_LABELS.racing =
  "Racing"`, with `SPORT_ORDER` placing racing after golf (display
  order only — the union keeps backend enum order).
- `views/calendar/eventSpans.ts` `SPORT_GLYPH.racing = "🏁"` — span
  bars read "🏁 {race name}"; pinned in `eventSpans.test.ts`. (The glyph
  map was removed on 2026-10-11: see "Box Score: the Calendar view".)
- Verified sport-agnostic, no change needed: `LineupView.SURFACE`,
  `whyWatch.LATE_PERIOD/CLOSE_MARGIN`, `CalendarView.OUTDOOR_SPORTS`
  (games only — racing has none), and `StandingsView`'s per-sport
  branches.
- `EventLeaderboardModal` and `TodayView.EventCard` render
  `round_label` / `score` / `detail` verbatim and derive followedness
  from the backend contract (a non-null `player_id` IS a followed
  athlete); the followed team's color is purely an accent, falling
  back to `NEUTRAL_FALLBACK_COLOR` (exported from `GameCard.tsx`) when
  the catalog row carries no color (racing driver rows ship
  `color=None`).

**Tests** (`backend/tests/test_espn_racing.py`, fictional "Apex Circuit
Series" data): scoreboard/event parsing (scheduled empty board, live lap
label, non-race session label, sprint session types, final winner detail +
ordering, quali-order fallback, orderless competitors sorting last —
never P1 — with an all-orderless entry list numbering 1..N in feed
order, single typeless competition, malformed competitor/event skips),
provider dispatch + games-path gate, renamed scheduler helpers, catalog
standings⊕scoreboard union + scoreboard-only fallback +
scoreboard-outage degrade + failure modes. The 2026-07-29 enrichment added
16 more tests to the same file, all listed under *Core-API board
enrichment* below; these 22 pass unmodified.

## "On this day" (2026-07-28)

A read-only anniversary view over the season archives — no new provider
code, no new storage; everything rides the existing
`espn_history`/`season_results` pipeline.

- `app/services/on_this_day.py`: for one followed team, walks season
  keys `range(now_year, now_year - SEASONS_BACK, -1)` (`SEASONS_BACK =
  5`, the same window as the head-to-head builder) **sequentially**
  through `season_results.season_results_out` — already cached
  (`services/cache`: 6 h for this year's key and last year's, 7 d for
  older ones) and never-raising — then
  keeps the FINAL
  games whose **local** (`settings.timezone`) calendar month-day equals
  today's, excluding actual today (that slate belongs to `/today`).
  Newest first. The assembled per-team list gets its own best-effort
  cache under `onthisday:{team_id}:{MM-DD}`, TTL 24 h; correctness
  never depends on the cache (worst cost per team of a scan that finds
  nothing cached:
  5 seasons × ≤2 schedule param-set calls, all through the
  `get_with_retry` degrade paths). **The `onthisday:` cache is
  Redis-only**: the list is stored whatever the scan found, an empty one
  included, and a scan made while ESPN was failing finds nothing — so it is
  deliberately not kept in-process (2026-10-11). A redis-less install
  rebuilds the list on each request from the `history:results:` seasons,
  which are kept in-process, so only a season that came back empty or
  incomplete is asked for again. Season keys
  are ENDING years, so a
  cross-year season's Aug–Dec games surface under key year+1 — the
  month-day filter makes that transparent, and Feb 29 needs no special
  casing (empty most years).
- `GET /history/on-this-day` → `OnThisDayOut { date, teams }` where
  `date` is the local ISO day (`YYYY-MM-DD`) the month-day was taken
  from and `teams: OnThisDayTeamOut[]` =
  `{ team_id, team_name, games: GameOut[] }`. Only teams with ≥ 1
  historical game on this date appear; teams on non-espn providers or
  sports failing `espn_history.supports_history` are skipped **before
  any fetch** (silently — no error entry). The route always returns
  200: an empty `teams` list is the normal state, deliberately unlike
  `/history/results/{team_id}`'s 404-on-miss (a day without an
  anniversary is not an error).
- `schemas.OnThisDayTeamOut` / `schemas.OnThisDayOut` are additive;
  `GameOut` entries are the shared serializer's shape, so broadcasts /
  fight_result etc. ride along for free. Mirrored field-for-field in
  `types.ts` (`OnThisDayTeam`, `OnThisDay`), with the regenerated
  OpenAPI snapshot.
- Frontend: `api.ts` `onThisDay: () => get<OnThisDay>("/history/on-this-day")`;
  `hooks.ts` `useOnThisDay()` — queryKey `["on-this-day", <viewer-local
  day key via lib/time.localKeyFromDate>]`: the always-mounted kiosk
  re-renders on the Today poll, so the key flips at midnight and mounts
  a fresh fetch; `refetchInterval` 6 h lets a failed boot-time fetch
  recover (staleTime 6 h, `refetchOnWindowFocus: false`, `retry: false`
  unchanged — an error just hides the section). `views/TodayView.tsx`
  renders a self-hiding "On this day" section at the bottom — after the
  Games section, before the leaderboard modal — in BOTH the games path
  and the no-games empty state (it sits outside that conditional). It
  hides entirely while the query is pending, errored, `teams` is empty,
  or the payload's `date` differs from today's local key
  (`isOnThisDayCurrent` in `views/today/onThisDay.ts` — the comparison
  is skipped when either side is missing, so older backends omitting
  `date` never hide the section), and never gates or blanks the main
  slate (the hook is called before the early returns, the view's
  standing rule).
- Rows are NON-interactive by contract: history games are assembled on
  demand and never stored, so `/games/{id}` would 404 —
  `GameDetailModal` is deliberately not wired. Row shape: year chip
  (viewer-local year of `start_time`, tabular-nums zinc pill) +
  "AwayName A – H HomeName" with the winner bolded, derived from scores
  alone. Pure row logic lives in `views/today/onThisDay.ts`
  (`winningSide` — null on draw/missing score; `anniversaryYear` —
  LOCAL year, matching the app's localize-at-render rule; `scoreline` —
  away-first, em dash for a missing score; `isOnThisDayCurrent` — the
  midnight-rollover render guard above), with colocated vitest
  `onThisDay.test.ts` (runs under TZ=America/New_York; covers the
  UTC/local year boundary and the render guard).

## Player follows (2026-07-28)

A followed player is a per-athlete star on top of an existing team
follow. v1's rule is **the roster is the picker**: only an athlete on a
followed team's stored roster can be followed, which keeps that player's
games flowing through the existing team follow — schedule sync needs
zero changes.

### Amendment: writing routes

The "setup and notifications are the only writing routes" clause is
amended (the `repository.py` module contract above now says so too):
**three** route modules write, each committing explicitly —

1. `routes/setup.py` (`POST /setup/follow`)
2. `routes/notifications.py` (`PUT /notifications/prefs`)
3. `routes/players.py` (`PUT`/`DELETE /players/follows/{athlete_id}`) — NEW

All other routes remain read-only and never commit; repository functions
never commit (callers own transactions).

### New table: `player_follows` (`PlayerFollowORM`, app/models/orm.py)

| column      | type                     | notes                                              |
|-------------|--------------------------|----------------------------------------------------|
| athlete_id  | String(32), PK           | the **bare** ESPN athlete id (rosters store it `"espn:{id}"`-prefixed) |
| name        | String(128)              | display name at follow time                        |
| team_id     | String(64), **no FK**    | internal slug of the followed parent team          |
| league_id   | String(64), **no FK**    |                                                    |
| position    | String(32), nullable     |                                                    |
| photo_url   | String(512), nullable    |                                                    |
| followed_at | DateTime(timezone=True)  | UTC; preserved across a re-follow of the same athlete |
| last_notified_status | String(24), nullable | the roster-diff alert baseline: seeded from the roster row's status at follow time (or silently on first sighting), advanced only on confirmed delivery |

- **No FKs by design**: rosters are delete+reinserted on every roster
  sync — the daily sweep, and since 2026-07-29 the pre-game re-sync too
  (never FK the roster table) — and the followed-set rules delete a
  follow whose team is gone without one (`replace_followed` at save
  time, `prune_unbacked` at the top of every refresh pass). Roster data
  is joined logically at read time via
  `PlayerORM.id == f"espn:{athlete_id}"`.
- New table ⇒ created by `Base.metadata.create_all` at startup; the
  TABLE needs **no migrations.py entry** (that list is for added
  columns only) — but `last_notified_status`, added after the table
  shipped, has the additive entry `("player_follows",
  "last_notified_status", "VARCHAR(24)")`.
- `repository.replace_followed` deletes the `PlayerFollowORM` rows of a
  team that leaves the followed set, and only those: a kept team's
  follows survive an edit, `last_notified_status` baseline included.
  (**Amended 2026-10-10** — it used to wipe the table on every save, so
  follows had to be re-picked after each wizard run. See *Editing the
  followed set* below.)

### New repository functions (app/services/repository.py)

- `list_player_follows(session) -> list[PlayerFollowORM]` — ordered by
  name, athlete_id.
- `follow_player(session, *, athlete_id, name, team_id, league_id,
  position=None, photo_url=None, last_notified_status=None) ->
  PlayerFollowORM` — SELECT-then-upsert; a re-follow refreshes display
  fields but keeps the original `followed_at`. `last_notified_status`
  (re)seeds the roster-diff alert baseline — it is always assigned, so
  a re-follow re-seeds (or clears) it.
- `unfollow_player(session, athlete_id) -> bool` — False when not
  followed.

### New routes (app/routes/players.py, registered in routes/__init__.py)

- `GET /api/players/follows` → `list[PlayerFollowOut]`
- `PUT /api/players/follows/{athlete_id}` (body `PlayerFollowIn`: name,
  team_id, league_id, position?, photo_url?) → `PlayerFollowOut`
  - **v1 constraint** (the roster is the picker): 404 unless `team_id`
    is a followed `TeamORM` AND the athlete is on its **stored roster**
    (`PlayerORM` row keyed `(team_id, f"espn:{athlete_id}")`).
  - 409 when the same bare athlete id is already followed under a
    DIFFERENT league (ESPN athlete ids collide across sports; follows
    key on the bare id alone); the original cross-league follow is
    left untouched. Checked after both 404 validations, before the
    write.
  - The roster row's current status seeds `last_notified_status`, so
    following an injured player never alerts at follow time.
- `DELETE /api/players/follows/{athlete_id}` → 204; 404 when not
  followed.

### New schemas (app/schemas.py, mirrored in frontend/src/types.ts)

- `PlayerFollowOut { athlete_id, name, team_id, league_id, position?,
  photo_url?, followed_at }` — TS `PlayerFollow` mirrors it
  field-for-field (`athlete_id` is the bare ESPN id; `followed_at` an
  ISO 8601 UTC string).
- `PlayerFollowIn { name, team_id, league_id, position?, photo_url? }` —
  TS `PlayerFollowIn`.
- `StatLeaderOut` gains `followed: bool = false` — the player themselves
  is followed (distinct from the team-level `highlighted`); mirrored as
  `StatLeader.followed`.

### Leaders stamping rule (routes/leaders.py)

`followed` is stamped **per request, after cache retrieval** — the 900s
`leaders:{league_id}` cache stores rows with `followed=false` only, so a
new follow/unfollow is reflected immediately without invalidation or a
refetch. Matching is on the **bare** athlete id AND scoped to the
requested league — only follows with `row.league_id == league_id` stamp
(ESPN athlete ids are only unique per sport, so a follow in another
league must not stamp a same-numbered stranger here); the roster-derived
(non-ESPN-path) board's prefixed `"espn:{id}"` player ids are stripped
before matching, so both board flavors stamp.

### EVENT_TYPES addition: `player_status`

New `EventType.PLAYER_STATUS = "player_status"` (app/models/domain.py),
wired through:

- `services/notify_prefs.EVENT_TYPES` (now six, `player_status` last).
  `follow_all_default_events()` therefore seeds it **off** for
  whole-competition league scopes (it is not game_start/final); the
  plain default remains on.
- `decide()`: no code change needed — **v1 gates player alerts on the
  parent team's `team:{id}` scope**; callers pass `[parent_team_id]`
  (+ league id) exactly like a game event. Per-player `player:{id}`
  scopes are deferred.
- `services/notify._EVENT_TAGS`: `player_status` → `adhesive_bandage`
  (🩹), normal priority.
- `routes/notifications.py` grid picks it up automatically from
  EVENT_TYPES.

Two emitters, each with its own dedupe:

1. **Roster-sync status diff** (`scheduler/refresh.py::_sync_team_roster`,
   run by BOTH `refresh_rosters` and — since 2026-07-29 —
   `refresh_pregame_rosters`):
   follows loaded once per refresh; per team, the FETCHED roster is
   diffed against each follow's `last_notified_status` baseline (never
   against the roster table, which `replace_roster` has already
   overwritten). The baseline IS the dedupe — no `NotificationSentORM`
   writes here. It advances (with its own commit, per follow) only on
   confirmed delivery: a failed send leaves it behind and retries next
   sync; a muted team's diff never advances it, so un-muting later
   still alerts if the status still differs; a `None` baseline (a
   fresh follow seeded outside the route, or a pre-baseline row) seeds
   silently — no follow-time blast; and a re-injury after a delivered
   recovery alert re-fires. The event's
   `player:{athlete_id}:status:{new_status}` dedupe_key survives
   purely as a log/identity label. Message: `"{name} is {status
   label}"` + ` — {status_detail}` when present; title = the parent
   team name. **The two jobs cannot double-send**: the per-follow
   baseline IS the dedupe and advances only on confirmed delivery, so
   whichever job observes a change first alerts and the other then sees
   no difference.
2. **Starting-soon "ruled out" pass** (`scheduler/live.py::live_tick`),
   unchanged — deduped through the `NotificationSentORM` ledger with
   the send-first / mark-on-confirmed-delivery idiom: for scheduled
   games inside the `starting_soon_minutes` window, one event per
   followed player on either side whose stored status != `active`.
   Dedupe `{game_id}:player-out:{athlete_id}`; message
   `"{name} ({status_detail or status}) — {away} @ {home} starts soon"`.
   Freshness (updated 2026-07-29): statuses are as fresh as the last
   roster sync of *either* job — `refresh_pregame_rosters` re-syncs a
   followed team's roster in the hours before kickoff (see *Same-day
   pre-game roster re-sync* below), so inside that lookahead this pass
   reads same-day-fresh designations rather than yesterday's 5am sweep.
   The pass itself is unchanged: it reads `PlayerORM.status` via
   `session.get`, so a fresher players table is automatically a fresher
   alert. Outside the lookahead (or with the job disabled) the old
   caveat still holds — the status is as fresh as the daily sync.

Shared helper: `services/events.player_status_label()`
("day_to_day" → "day-to-day").

### Frontend

- API client (`api.ts`): `playerFollows()` → GET `/players/follows`;
  `followPlayer(athleteId, body)` → PUT; `unfollowPlayer(athleteId)` →
  DELETE (a new `del()` helper: DELETE that parses no body).
- Query layer (`hooks.ts`): `usePlayerFollows()` — queryKey
  `["player-follows"]`, staleTime 5 min. `useFollowPlayer()` /
  `useUnfollowPlayer()` — on success invalidate `["player-follows"]`
  AND the `["leaders"]` key prefix (every league's board;
  `StatLeader.followed` is per-request so cached boards must refetch).
  No optimistic cache surgery; stars disable while their own mutation
  is in flight — the pending set is the UNION of the follow and
  unfollow mutations' in-flight athlete ids (matched via
  `mutation.variables`; both can be pending at once, and an either/or
  would leave the second row enabled for a duplicate fire).
- UI surfaces: `RosterView` gains a trailing follow-star column (ghost
  button, amber filled star when followed, `aria-pressed`, aria-label
  "Follow {name}"/"Unfollow {name}") — shown only for team-sport
  rosters of followed teams, hidden for tennis/mma/golf/racing,
  additionally gated on the league's provider being `"espn"` at BOTH
  surfaces (RosterView and TeamProfileView) — a TheSportsDB roster
  (volleyball) hides the stars because the follow PUT validates
  against ESPN-keyed roster rows and can only 404 — and hidden
  entirely when the follows query has no data (the
  supplementary-section rule). The filled/unfollow state is scoped to
  the current team (`follow.team_id` must equal the row's team id):
  bare ESPN athlete ids are only unique per sport. `TeamProfileView`'s roster section
  carries the same star, compact — the profile is name-keyed, so the
  followed-team row is recovered by name from `useTeams()` to supply
  `team_id`/`league_id`; nations (empty roster) never show stars.
  `LeadersView` gives `row.followed` a stronger treatment than the
  team-level `highlighted`: filled amber star before the name (sr-only
  "Followed player") + brighter border/wash (`border-amber-400
  bg-amber-500/15` vs `border-amber-500 bg-amber-500/10`);
  non-followed rows unchanged. `SettingsView`'s `EVENT_LABELS` gained
  `player_status: "Player status"` so the prefs grid renders the sixth
  EventType cleanly (grid order still comes from the backend's
  `event_types` list). **Superseded 2026-08-19**: the prefs grid — and
  with it `EVENT_LABELS` — was removed from `SettingsView` because the
  notification path has never been exercised against a real device. The
  API, the scheduler and the `useNotificationPrefs` client are unchanged;
  restoring the panel means restoring this label map with it. See the
  ROADMAP loose end for what must be verified first.
- Shared helpers exported from `components/TeamProfileView.tsx`:
  `FollowStarButton` (ghost star toggle, `compact` variant),
  `bareAthleteId(playerId)` (strips the `"espn:"` prefix from roster
  row ids — follows key on the bare id, mirroring the backend
  leaders.py idiom), and `NO_PLAYER_FOLLOW_SPORTS` = `{tennis, mma,
  golf, racing}` (sports whose rosters can't contain followable
  players).

### Known v1 limits

- Per-player `player:{id}` notification scopes are deferred — player
  alerts ride the parent team's `team:{id}` scope (above).
- ~~Player status is as fresh as the last daily roster sync; the
  starting-soon ruled-out pass does not re-fetch same-day.~~ **Closed
  2026-07-29** by `refresh_pregame_rosters` (below): a followed team with
  a game inside `pregame_roster_lookahead_hours` is re-synced status-only
  every `pregame_roster_refresh_minutes`, at most once per
  `pregame_roster_cooldown_minutes`. Still true outside that window, for
  a team with no followed player, and when the job is disabled.
- Follow context requires the roster surface (the roster is the
  picker); there is no follow affordance on the Leaders board itself.
- `TeamProfileView` matches the followed team BY NAME (the profile is
  normalized); if a club and a nation ever shared an exact name the
  star could appear on a nation profile — harmless today because nation
  profiles have empty rosters. When the profile name matches MORE than
  one followed team the stars are hidden entirely (binding to the
  first match would attach follows to — and delete follows from — the
  wrong team); the nation caveat stands.
- The leaders server cache is 900s. The post-cache stamping rule means
  the `followed` flag itself is always fresh; only the underlying board
  rows can be up to 15 min old.

### Tests

`tests/test_player_follows.py` (fictional names): repository CRUD +
what `replace_followed` keeps (a kept team's follows, baseline included)
and drops (a removed team's); route validation (unknown team 404, off-roster
404, cross-league bare-id PUT 409, follow-time baseline seeding — no
alert at follow, round trip, DELETE 204/404); leaders post-cache
stamping (warm cache + new follow flips the flag with no refetch;
cached copy stays unstamped; roster-board prefixed-id stamping; the
stamp is scoped to the requested league); roster-diff baseline (fires
once, unchanged quiet, first sighting seeds silently, failed send
leaves the baseline and retries, re-injury after recovery re-fires,
muted team leaves the baseline and un-muting refires); starting-soon
player-out (ruled-out followed player fires once, active/unfollowed
don't); events_tick scheduled-within-lookahead pickup;
prune_stale_games sweeps stuck in_progress events.
`test_notifications_api.py` / `test_notify_prefs.py` EVENT_TYPES
enumerations updated to six.

## Why-watch signal on game cards (2026-07-28)

Frontend-only — no API, schema, or `types.ts` change: the signal is
computed client-side from data the cards already receive (the existing
`odds` prop; no new fetches, no TodayView changes, no re-sorting).

- `lib/whyWatch.ts` (pure; colocated `whyWatch.test.ts`):
  - `detectSignal(game, odds): UpsetSignal | null` — moved verbatim from
    `UpsetRadar.tsx`, which now imports it (zero behavior change). (The
    radar's layout and its second, live signal are in "Box Score: the
    Matchup view", 2026-10-11.)
    Needs all four of win pcts + moneylines; fires only when the market
    underdog's model win % > 50.
  - `watchability(game, odds): WhyWatch | null`
    (`WhyWatch = { score, reason }`). Deterministic weights: Tight late
    = 60 (live, both scores present, margin ≤ one score, period ≥ the
    sport's late threshold); Upset watch = 45 (`detectSignal` fires);
    Toss-up = 35 (both win pcts present, `|home_win_pct − 50| ≤ 5`);
    both sides followed = +15 (bonus only). Threshold = 35, so at least
    one primary signal is required — the bonus alone never shows.
  - One-score margins: basketball/football ≤ 3, hockey/soccer ≤ 1, else
    ≤ 2. Late thresholds: basketball/football/volleyball ≥ 4, hockey
    ≥ 3, soccer ≥ 2, baseball ≥ 8, unknown sports ≥ 4 (conservative).
  - `reason` = the strongest matched signal ("Tight late" > "Upset
    watch" > "Toss-up"). Returns null for any phase other than
    scheduled/in_progress (function-level, on top of caller gating).
- `GameCard` renders it as a rose pill (`border-rose-500/30
  bg-rose-500/10 text-rose-400`, content `🔥 {reason}`) in the footer
  chip rail, gated to `scheduled | in_progress` AND suppressed whenever
  the fight chip shows.

## Backup restore drill (2026-07-28)

`scripts/verify-backup.sh` proves the newest `backups/sportsdash-*.sql.gz`
actually restores, host-side only (no compose service, no CI job — the
risky surface is the homelab's real dumps, which CI cannot see):

- Restores into a throwaway `postgres:16-alpine` container created with
  `POSTGRES_USER=sportsdash` (the dumps carry `ALTER TABLE ... OWNER TO
  sportsdash` and have no `--clean`/`--create`, so the target must be a
  fresh empty database under that role). **No host ports are published** —
  all access goes through `docker exec`, so a stale container shadowing
  a port can never fool the drill.
- `psql -v ON_ERROR_STOP=1` plus script-level `pipefail` are
  load-bearing: without them psql shrugs past mid-file errors and a gzip
  read failure vanishes into the pipe. The dumps embed pg_dump 16's
  `\restrict` meta-commands, so they are restorable only via a psql 16
  client — the one inside the 16-alpine container is exactly right.
- Sanity contract on the restored database: `teams` > 0, `leagues` > 0,
  `games` counted (and when > 0, `MAX(start_time)` must be non-NULL),
  `standings_archive` must exist (may be 0 rows), `players` counted.
  There is no `follows` table — the followed set IS the rows in
  `teams`/`leagues`.
- Freshness: newest dump older than `SPORTSDASH_BACKUP_MAX_AGE_DAYS`
  (default 16) days → WARN; `--strict-age` makes it fatal. Warn-only is
  the default because the daily backup loop only runs while compose is
  up, which is intermittently true on the dev machine; strict is the
  intended mode for a homelab cron.
- The container is removed via an EXIT trap; any failed step exits
  nonzero with a message saying what broke.

Verified 2026-07-28 against the real dump
`sportsdash-20260721-010235.sql.gz` (7 days old at run time): restore
clean, counts teams=8 leagues=7 games=69 standings_archive=0
players=222, exit 0. Failure paths exercised the same day:
`--strict-age` on the 7-day-old dump exits 1 before any container
starts; unknown flags exit 2; the warn path (low
`SPORTSDASH_BACKUP_MAX_AGE_DAYS`, no `--strict-age`) warns and still
restores with exit 0; and a deliberately truncated copy of the dump
pushed through the identical `gzip -dc | docker exec -i psql -v
ON_ERROR_STOP=1` pipeline exits nonzero (gzip unexpected-EOF + psql
abort mid-`COPY`). No leftover `sportsdash-restore-verify-*` containers
after any run.

## Same-day pre-game roster re-sync (2026-07-29)

Closes the *Player follows* v1 limit above. Injury and scratch
designations are only final in the hours before kickoff, while the daily
5am sync can be a whole day stale — so a player ruled out at noon was
invisible until the next sweep. A new job re-fetches the roster
**status-only** for followed teams with an imminent game, and rides the
existing `player_status` diff so the scratch alerts within a tick.

**No route, schema, OpenAPI or `types.ts` change.** No migration and no
`_ADDITIVE_COLUMNS` entry either — the cooldown reuses
`TeamORM.roster_updated_at`.

### `refresh_pregame_rosters` (`app/scheduler/refresh.py`, registered in `jobs.py`)

A separate **instrumented interval job** — its own
`sportsdash_scheduler_job_runs_total{job="refresh_pregame_rosters"}`
counters, so its cost and failures are visible on `/api/metrics`. It is
**not** part of `daily_refresh` and **not** part of `live_tick` (whose
gate reaches only `live_lead_minutes` = 20 min ahead, includes
`in_progress` and hours-old scheduled rows, and fires every 45 s).
Registered between `refresh_news` and `live_tick` with `max_instances=1`,
`misfire_grace_time=300`, `coalesce=True`.

Three gates, cheapest first:

1. **Player follows.** `_followed_players_by_team()`; no follows anywhere
   → return before any game scan or provider call.
2. **Imminent game.** `repository.team_ids_with_games_starting_within(now,
   pregame_roster_lookahead_hours)` intersected with the followed-player
   team ids. Empty → return. Teams whose league sport is in
   `INDIVIDUAL_SPORTS` are skipped: a followed athlete is a single-member
   "team" with no roster, so a sync would only delete its rows and restamp
   it.
3. **Per-team cooldown.** `TeamORM.roster_updated_at` (read through
   `ensure_utc` — SQLite returns it naive) must be older than
   `pregame_roster_cooldown_minutes`. `replace_roster` is still the single
   writer of that timestamp, so a sync cools itself down. Its only
   consumer stays `routes/roster.py` → `RosterOut.fetched_at` →
   RosterView's "Updated …" label, which is simply fresher (it is not
   wired to `is_stale`; `data_stale_after_minutes` is standings-only).

**Cost bound: exactly one `/teams/{id}/roster` call per eligible team per
cooldown window.** The provider is asked for the cheap variant
(`get_roster(..., with_stat_lines=False)`), which skips the
one-`/athletes/{id}/overview`-per-player pass; the soccer TheSportsDB
photo backfill is skipped too (it shares the free key with the
stadium/About enrichment and is paced ~0.34 s/request). With stat lines
the job would cost ~400–700 ESPN calls/day for six followed teams instead
of ~15–25.

**No collateral blanking.** `replace_roster` is a blind delete-and-reinsert
of every player column, so the cheap path first carries the stored
`stat_line` / `career_stat_line` / `photo_url` forward onto the fetched
players — `_carry_forward_enrichment(stored, fetched)`, pure, keyed on the
roster player id; a fetched value wins whenever it is not empty, so a
call-up keeps its own. Without it the roster view's stat columns,
TeamProfileView's roster lines and `routes/leaders.py`'s roster-derived
board would empty out between daily syncs. Storing still goes through
`replace_roster` — one roster writer — so call-ups and trades are picked
up as usual.

**Per-team isolation.** The fetch/store/diff body is
`refresh.py::_sync_team_roster(league, team, followed, *, with_stat_lines,
backfill_photos)`, shared verbatim with `refresh_rosters` (which calls it
with both flags on). It swallows its own per-team exception, and the job
swallows everything else — `metrics_state.instrumented` re-raises whatever
escapes. Because the helper is shared, its log lines are prefixed
`"roster sync:"` rather than `"refresh_rosters:"`; each job still logs its
own job-named summary line.

### Provider protocol: `get_roster` gained `with_stat_lines`

```python
async def get_roster(self, league, team, *, with_stat_lines: bool = True) -> Roster
    # with_stat_lines=False -> the roster payload ONLY (one call). ESPN
    # skips its per-athlete season/career overview pass; TheSportsDB
    # accepts and ignores the flag (its roster is already one call).
    # Statuses ride on the roster payload, so the cheap variant loses
    # nothing the pre-game re-sync needs.
```

The change is additive, keyword-only and default-preserving, so every
existing caller and test fake is unaffected — but note that it touches
`app/providers/base.py`, one of the "already written, **do not modify**"
foundation files listed at the top of this document. It was the only way
to hit the cost bound: `EspnProvider.get_roster` called `_attach_stat_lines`
unconditionally and `_GuardedProvider.__getattr__` hides signatures, so no
capability probe was possible. `app/providers/thesportsdb.py::get_roster`
accepts and ignores the flag deliberately — `espn_catalog.py` can create
thesportsdb-provider leagues, and an unexpected-kwarg `TypeError` there
would be a silent per-team skip every 15 minutes.

### Alerting: two jobs, one ledger

The re-sync runs the same emitter-1 diff described under *Player
follows* → `player_status`, against the same per-follow
`PlayerFollowORM.last_notified_status` baseline. **The two jobs cannot
double-send**: the baseline is the dedupe and advances only on confirmed
delivery, so whichever job observes the change first alerts and the other
then sees no difference.

**Two alerts per same-day scratch is intended** (and unchanged in kind):
the roster-diff alert when the designation is first observed, then
`live_tick`'s `{game_id}:player-out:{athlete_id}` game-context reminder
inside the starting-soon window. Different ledgers by design — news
first, then a reminder tied to the fixture.

`live_tick` itself needed no change: it reads `PlayerORM.status` via
`session.get`, so a fresher players table is automatically a fresher
alert.

### Settings (`app/config.py`, env prefix `SPORTSDASH_`)

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `pregame_roster_refresh_enabled` | `SPORTSDASH_PREGAME_ROSTER_REFRESH_ENABLED` | `True` | Master switch. Off ⇒ the job returns immediately; it stays registered, so its metrics counters still exist. |
| `pregame_roster_lookahead_hours` | `SPORTSDASH_PREGAME_ROSTER_LOOKAHEAD_HOURS` | `4` | Re-sync a followed team whose next scheduled game starts within this window. |
| `pregame_roster_cooldown_minutes` | `SPORTSDASH_PREGAME_ROSTER_COOLDOWN_MINUTES` | `45` | Minimum gap between one team's re-syncs, measured off `teams.roster_updated_at`. |
| `pregame_roster_refresh_minutes` | `SPORTSDASH_PREGAME_ROSTER_REFRESH_MINUTES` | `15` | Job interval. |

All four are read **inside** the job body, not at import, so a settings
override in a test takes effect against the cached singleton. Defaults
rationale: 4 h / 45 min gives up to ~5 re-syncs per team per game, the
last comfortably inside the injury-report window, at ~15–25 ESPN calls a
day for six followed teams. `.env.example` deliberately does not list
them — it documents only the timezone and the ntfy topic, not tuning
knobs.

### Known limits (unchanged by this feature)

- A team with **no followed player** is never re-synced. The job exists
  to make a player alert same-day-fresh, not to keep every roster warm.
- Per-player `player:{id}` notification scopes are still deferred; player
  alerts still ride the parent team's `team:{id}` scope.
- Trade-following is still deferred: a traded player's follow keeps its
  original `team_id`, and the re-sync only diffs statuses on rosters it
  fetches.
- Daily photo preservation was fixed in the September 2026 audit: stored
  headshots are carried forward before choosing the bounded photo batch.
  Missing candidates rotate daily so persistent misses do not starve later
  squad members. Only cheap status refreshes preserve missing stat lines;
  full season refreshes may clear old statistics.

### Tests

`tests/test_player_follows.py` section 7 (11 tests) + one in
`tests/test_scheduler.py`: the end-to-end same-day scratch (one alert,
baseline advanced, `roster_calls == [(TEAM_ID, False)]`, zero overview
calls, stored stat lines and photos intact next to the fresh status); a
game beyond the lookahead makes zero calls; the cooldown suppresses a
second run and a zeroed cooldown lets it through without re-alerting; no
follows ⇒ no provider call; the disabling setting; per-team failure
isolation; a raising scope query never propagates; individual-sport teams
skipped; pre-game re-sync then `live_tick` ordering (roster-diff alert,
`:soon`, then `:player-out:` off the freshly written status);
`_carry_forward_enrichment` as a pure function;
`team_ids_with_games_starting_within` window/exclusion semantics; and the
job's registration (id, `max_instances`, `coalesce`,
`misfire_grace_time=300`, interval == the setting).
`tests/test_metrics.py::test_setup_scheduler_seeds_job_registry` asserts
the job-id set exactly and now expects five ids.

## Core-API board enrichment for racing leaderboards (2026-07-29)

Fills the two columns the *Motorsport* section above documented as empty.
`sports.core.api.espn.com/v2/sports/racing/leagues/{key}` (the site
`racing/{key}` key with a `leagues` segment inserted — the existing
`_core_event_path` helper) carries everything the site scoreboard omits.
Keyless; no throttling observed (~430 sequential core calls from one IP
in 25 min, zero 429/5xx).

**No new domain, schema or `types.ts` field, and no scheduler or frontend
change.** `domain.py`, `schemas.py`, `serialize.py`, `scheduler/*` and
`frontend/*` are untouched: the enrichment rides the existing free-form
display strings. Files changed: `app/providers/espn/racing.py` (all the
pure parsers, URL builders, bounds and rendering),
`app/providers/espn/provider.py` (`_enrich_racing` /
`_enrich_racing_event` / `_racing_car_details` / `_racing_distance` /
`_racing_json`, called from both `get_events` and `get_event_state`), and
`app/providers/espn/__init__.py` (re-exports).

Amended later the same day, in the same two modules (`provider.py` only for
the per-car status tuple's type, now `tuple[str, int | None]`, and one
docstring — no new fetch, no new call site, no change to the tiers, caps,
memo or degrade paths; `__init__.py` needed no new re-export): a **running
gap on a live board**, read out of the site scoreboard's own per-competitor
`statistics` slot, and an **exact status-name mapping** that stops a
disqualification rendering as a finish. Both cost **zero** extra ESPN calls
and both ride the same free-form display strings, so there is still no
domain, schema, `types.ts`, scheduler, frontend, fixture or OpenAPI change.

### Display semantics — what `score` and `detail` MEAN for racing

`LeaderRow.score` and `LeaderRow.detail` are free display strings the
frontend renders verbatim (`EventLeaderboardModal`'s Pos/Player/Score/Detail,
`TodayView.EventCard`). Enrichment **only ever adds**: a row it cannot
reach keeps the un-enriched `("", "Winner")` / `("", "")`.

`score` — the car's time, gap, or fate. On a **FINAL** board it is resolved
in this fixed order. The order is load-bearing: a lead-lap finisher carries
BOTH a total time and a gap and the gap is the useful number, so only the
leader falls through to the total time; and a retired or disqualified car
must never borrow a time it did not set (a disqualified one in particular
can carry the full race distance AND a finishing gap).

| # | condition | rendered |
|---|---|---|
| 1 | outcome is `retired` — status `STATUS_RETIRED`, or the F1-only inference below | `"DNF"` |
| 2 | outcome is `disqualified` — status `STATUS_DISQUALIFIED` only, never inferred | `"DSQ"` |
| 3 | `behindTime` present and non-zero | `"+15.080"` (a `+` is added when ESPN omits one) |
| 4 | `abs(behindLaps) >= 1` | `"+1 lap"` / `"+3 laps"` |
| 5 | `totalTime` present | `"1:39:56.180"` (the leader) |
| 6 | `lapsCompleted` present | `"160 laps"` (series that publish no time at all) |
| 7 | otherwise | `""` — unchanged from the parser |

On an **IN_PROGRESS** board the column is a *running gap* instead, taken
from the site scoreboard's own per-competitor `statistics` slot
(`events[].competitions[].competitors[].statistics`) — which lives inside
the payload the board is already parsed from, so this costs **zero extra
calls per poll**:

| # | condition (live board only) | rendered |
|---|---|---|
| 1 | `behindTime` present and non-zero | `"+2.418"` |
| 2 | `abs(behindLaps) >= 1` | `"+1 lap"` / `"+2 laps"` |
| 3 | otherwise | `""` — an empty column, exactly as before this amendment |

`totalTime` and `lapsCompleted` are deliberately NOT rendered mid-race even
though the FINAL chain renders both: `totalTime` is a *finishing* time (a
running car has not set one) and `lapsCompleted` only restates the lap
counter the round label already carries as `"Lap 44 of 70"`. On a live board
a borrowed or stale time is worse than an empty column. The read is gated on
the **board session** being in state `"in"` (`_racing_leaderboard` →
`_racing_leader_row(..., live=...)`), so a FINAL board never touches the slot
and **the FINAL semantics above are untouched**. It is also strictly
additive — `_apply_racing_facts` still merges with `score = car.score or
row.score`, so a tier-A constructor / number / grid lands on the same row
without clearing a live gap. **This path is STRUCTURALLY VERIFIED ONLY:** see
*Live gaps: the free source, unverified live* below before trusting it.

`detail` — who the car is, as ` · `-joined parts: `"Winner"` (only when
the feed flags P1, preserving the un-enriched value) · entrant · `"#7"` ·
`"Grid 2"` · then one of `"Retired lap 13"` (so the `"DNF"` in `score`
reads as a fact — the lap comes from the status `period`, falling back to
`lapsCompleted`, and degrades to a bare `"Retired"` when neither is
knowable), `"Disqualified"` (behind the `"DSQ"`), `"Fastest lap 1:22.000"`
(the single car in the field that set it), or `"Status unknown"` when the
car's outcome is undecidable. Example:
`"Winner · Corvane Works · #7 · Grid 2"`.

`"Status unknown"` is always the LAST part, and
`_racing_detail_text(..., keep_last=True)` protects it from the `_DETAIL_MAX`
bound — it trims the facts in front of it instead, because losing that part
would change what the row *means* rather than just how much of it is shown.

Bounds: `score` capped at `_SCORE_MAX` = 24 chars, `detail` at
`_DETAIL_MAX` = 80. `LeaderRow.player_id` is untouched — still the
TRANSIENT bare ESPN athlete id the scheduler rewrites to the internal
followed-team id, and also the join key matching a core competitor to a
board row.

### Fetch tiers

**Tier A — every poll, ONE call per active race.** `GET
…/events/{eventId}` (the EVENT ROOT) inlines *every* session's full
competitor array with `vehicle` (constructor + car number), `startOrder`
(grid), `order` and `winner`. It replaces one call per session on an
F1-style weekend and sidesteps the 25-item paging the
`…/competitions/{id}/competitors` collection imposes on a 39-car
stock-car field. The session whose field is used is picked with the same
rule as the site board (`_board_session` / `_competitor_items`, factored
out of `_racing_leaderboard` with no behavior change: the Race session
when it has a field, else the latest session that does — so a Saturday
board is enriched with Saturday's cars). Payloads observed: F1 89 KB,
stock car 29 KB, IndyCar 18 KB.

Plus, for an IN_PROGRESS race only, `GET
…/events/{eventId}/competitions/{compId}/statistics` → `general.laps` =
the scheduled distance, for the `"Lap 44 of 70"` label. One call per
event, then memoized for the event's lifetime. The current lap costs
nothing extra — the site scoreboard's `status.period` already counts laps
run.

Tier A deliberately fetches **no** per-car statistics, so **a live board's
constructor, car number and grid slot are free, and its gap column is
whatever the site scoreboard itself published for that car** — nothing else
is bought for a running race. See *Live gaps* below.

**Tier B — once, when the Race session is FINAL.** One
`…/competitors/{id}/statistics` per car (race time / gap / laps behind /
fastest lap) plus one `…/competitors/{id}/status` per car (classified vs
retired, and that car's laps). ESPN offers **no** bulk expansion — every
`&enable=…` / `&expand=…` variant returns the byte-identical payload and
`…/competitors/statistics` is a 400 — so this is a genuine N+1. It is
therefore bounded three ways and memoized:

- at most `_ENRICH_MAX_EVENTS = 2` events enriched per provider call,
  chosen live-races-first then most-recently-started FINAL, so a
  season-wide `get_events` window can never fan out over two dozen
  finished races;
- at most `_ENRICH_MAX_CARS = 40` cars per event, front of the field
  first, so a truncated fan-out still covers the rows anyone reads;
- at most `_ENRICH_CONCURRENCY = 8` calls in flight (measured: 22 calls
  in 0.87 s at P=8, all 200);
- the rendered `(score, detail)` per athlete id is memoized per provider
  event key in a bounded (`_ENRICH_MEMO_MAX = 64`) insertion-ordered
  dict. A finished classification never changes, so the second poll of a
  FINAL race spends **zero** core calls.

Measured worst case for one FINAL F1 race: 1 event root + 22 statistics +
22 status = 45 calls, ~13 s sequential / ~2–3 s at P=8. Worst-case
cold start for sizing: 5 racing leagues × 2 events × ~45 = ~450 core
calls on a restart plus a full daily refresh, once, ~20 s of wall time at
P=8 spread across leagues. Steady state on a live race day is 1–2 extra
calls per event per 180 s tick.

The status call is issued only when the event root actually offered a
`status.$ref`: stock-car and IndyCar competitors have no `status` key at
all and that endpoint answers 400 for them. A `$ref` is a URL arriving
inside a remote payload, so it is followed **only** when it addresses the
core API host we asked (`_CORE_REF_PREFIX`).

### Degradation

Every core fetch goes through `provider._get_json` (the same
`http_util.get_with_retry` + shared client + settings-driven
retry/backoff as every other ESPN call), and `_racing_json` swallows
`httpx.HTTPError` / `TransientProviderError` / `ValueError`. A 404
(canceled race, unpublished entry list), an exhausted-retry outage, and a
200 carrying an HTML error page all leave the site-scoreboard board
intact. `_enrich_racing` additionally wraps each event in a bare `except
Exception`, so an unannounced shape change can never take down
`get_events` for a whole league.

`_racing_json` swallowing `TransientProviderError` means **a core-API
outage does not trip the per-provider circuit breaker from this path** —
intentional, matching the existing `_attach_fight_results` /
`_attach_stat_lines` precedent for best-effort garnish. The
site-scoreboard call in the same method still trips it.

Gating: `league.sport is Sport.RACING` (golf boards are already scored by
the site scoreboard and are never enriched); `_racing_enrich_targets`
skips SCHEDULED and board-less events outright, because a canceled race
(`STATUS_CANCELED`) and every scheduled race 404 the competitors
resources — entry lists are not published pre-race. That is "no field
yet", never an outage.

### Per-series shape traps encoded in the parsers

- **Constructor = `vehicle.team` when present, else
  `vehicle.manufacturer`** — one rule, correct for all three series. F1
  has no `team` and puts the constructor in `manufacturer` (verified
  equal to the athlete record's `vehicles[0].team` for every driver
  checked; the 22-car field resolves to 11 constructors × 2 cars sharing
  a `teamColor`). Stock-car/IndyCar name the entrant in `team` ("Joe
  Gibbs Racing", "Team Penske", null on ~7 of every field) and demote
  `manufacturer` to the OEM / engine supplier.
- **Never read the constructor from the ATHLETE record.** There
  `vehicles[0].manufacturer` is always identical to `vehicles[0].engine`
  (the engine supplier), and on stock cars it is outright stale — an
  athlete listed with one make and car number while the race competitor
  says another.
- `behindLaps` is **POSITIVE** on F1 and **NEGATIVE** on
  stock-car/IndyCar → always `abs()`.
- Stock-car/IndyCar expose **no per-car race time or gap whatsoever** (no
  `totalTime`; `behindTime` pinned at `.000`), so those boards land on the
  laps-completed / laps-down rendering. Zeroed placeholders are everywhere
  (that `.000` gap, a zeroed `fastestLap` for a whole field, qualifying
  splits on a Race competition), so a stat whose numeric `value` is 0 is
  dropped rather than rendered as a real time.
- **Three** per-competitor status types exist, not two (an earlier draft of
  this section said two, from a 217-document sample that happened to
  contain no disqualification). Measured over 611 F1 cars from 30 races
  (all 24 of 2025 plus 6 of 2026):

  | id | `type.name` | `displayValue` | `completed` | count |
  |---|---|---|---|---|
  | 12 | `STATUS_CLASSIFIED` | Classified | true | 529 |
  | 8 | `STATUS_RETIRED` | Retired | false | 76 |
  | 14 | `STATUS_DISQUALIFIED` | DQ | false | 6 |

  There is still no DNS type, a **lapped** car is CLASSIFIED with
  `behindLaps >= 1`, and a **non-starter is simply absent** from the field.
  The outcome comes from the status NAME only — reading `completed: false`
  would brand every car on a live board a DNF, and it is false for a
  disqualification too — and `_parse_racing_car_status` maps that name
  through an **exact** lookup (`_STATUS_OUTCOMES`). The substring test it
  replaced (`"RETIRED" in type.name`) is exactly what let a
  disqualification render as a finish: of the six DSQ'd cars observed,
  three carry the full race distance (`"56 laps"`) and one carries
  `behindTime "+53.472"` — a 53-second finishing gap for a car that
  completed **zero** laps. An unrecognized name now yields an explicit "not
  read" (`_OUTCOME_NONE`), never a silent "classified".
- The per-competitor status resource is **F1-only**. `nascar-premier`,
  `nascar-truck` and `irl` answer
  `{"message":"getCompetitorStatus() not supported for …","code":400}`, so
  outside F1 there is no outcome ground truth *and no call to fail*. There
  is also no bulk source: on the event root a competitor's `status` is
  **always** a bare `{$ref}`, under every expansion variant.
- `statistics.pole` is misnamed: it is the GRID slot and equals
  `startOrder` (verified 22/22 F1, 39/39 stock car, 25/25 IndyCar);
  `statistics.place` equals `order`. Both therefore come free from tier A
  and need no per-car call.

### Live gaps: the free source, unverified live

The live score column documented above is **implemented but has never been
seen working**, and the two are not the same claim.

**No in-progress racing session was observable anywhere.** At the probe
(2026-07-30T02:49Z) all five ESPN racing leagues had zero events and zero
competitions in `status.type.state == "in"`, and `?dates=20260729-20260812`
confirms nothing runs for 9 days (F1 summer break, Cup off-weekend).

What IS established: the `statistics` key exists on **every** racing
competitor in **every** league and session, and it was an empty `[]` on all
**1606** competitors scanned (F1 2025-H1 + July, Cup July, IndyCar Jun–Jul,
both current scoreboards) — every one of them a FINAL or scheduled session.
What is NOT established: that it ever populates, or what it looks like when
it does. **The populated shape is a model, not an observation.**

The parser therefore treats the slot as an *unverified shape* and is
deliberately liberal about it (`_racing_site_stat_map` /
`_parse_racing_site_stats`): a flat list of `{name, value, displayValue}`
(how the site API spells `statistics` everywhere it does populate one), a
dict handed to the core flattener (`splits.categories[]` / `categories[]`),
an entry keyed by `abbreviation` when `name` is missing — and it treats
*absent* as the normal case. Zeroed placeholders are dropped by the same
`_racing_display` rule as tier B. `_racing_live_row_score` wraps the whole
parse in a bare `except Exception`, so a surprise in a shape nobody has
observed costs **that one column** — never the driver's row, never the
board. Both cases are pinned by tests: populated (gap / lap deficit /
dropped `.000` placeholder) and absent (missing key, `[]`, `"nope"`,
`[1,2,3]`, a nameless entry, an empty core nesting → today's board exactly).

**A per-car live pass was deliberately NOT built.** It is the only other
route that exists, and it costs 22 calls per poll on an F1 race and 39 on a
Cup race: the competition-level aggregate (`…/competitions/{c}/statistics`)
is 6 stats with **no competitor dimension**, every `enable=` / `expand=` /
`view=` variant of the event root and of the competitors collection returns
the **byte-identical** payload (md5-proven, 6 variants each), 24 plausible
sub-resource names (`timing`, `gaps`, `laps`, `splits`, `intervals`,
`classification`, …) 404, `site.api…/racing/f1/summary?event=` 404s on
ESPN's own bug (it substitutes the event id for the competition id),
`cdn.espn.com/core/f1/race?xhr=1` is 302→503, and
`apis/v2/scoreboard/header` returns `competitors: null`. Worse, it is not
established that anything in a per-car payload *moves* during a race:
`totalTime` / `behindTime` are finishing values, the competition aggregate
publishes `avgSpeed`, `victoryMargin`, `poleSpeed`, `poleTime` as `.000` on
a *completed* Grand Prix, `fastestLap` is zeroed on 581 of 611 cars, the
`gapToLeader` category (nascar-premier / nascar-truck / irl only, absent on
F1) has an **empty `stats` array on all ~210 cars sampled**, and
`…/competitions/{c}/situation` returns a body containing only `$ref`.
Spending a per-poll N+1 to discover whether the numbers move is not a trade
worth making blind, so: ship the free source, leave the column empty when
it is empty. The two dated probes that decide whether to build more, and
the decision rule they feed, are in `ROADMAP.md` under *Loose ends*.

### Outcome resolution: status first, then an F1-only inference

`outcome` is resolved per car as **ESPN's status if it said anything**, else
— F1 cars only (gated on `car.status_url`, which only F1 competitors have),
and only when that car's statistics were actually fetched — the inference
below, else nothing (`_OUTCOME_NONE`, which renders exactly as it did
before any of this existed). Cost: **zero additional calls**; all of it
re-renders data the enrichment already fetches.

| outcome | `score` | appended `detail` part |
|---|---|---|
| `STATUS_RETIRED`, or inferred DNF | `"DNF"` | `"Retired lap 13"` (status `period`, else `lapsCompleted`, else bare `"Retired"`) |
| `STATUS_DISQUALIFIED` | `"DSQ"` | `"Disqualified"` |
| `STATUS_CLASSIFIED`, or inferred finisher | unchanged (gap / laps / time chain) | `"Fastest lap 1:22.000"` when set |
| **undecidable** (`_OUTCOME_AMBIGUOUS`) | unchanged — the lap deficit is true either way | **`"Status unknown"`, always last** |
| nothing known (no status resource, or car past the fan-out cap) | unchanged | unchanged |

`"Status unknown"` is the honest answer rather than a hedge on the number:
the score column is a number column, and a `"DNF?"` in it reads as a data
glitch.

The inference (`_racing_inferred_outcome`) is measured over the same 611 F1
cars (529 classified / 76 retired / 6 DSQ), each clause with **zero** false
positives on the 529 classified:

| clause | evidence |
|---|---|
| `abs(behindLaps) >= 4` (`_DNF_LAPS_BEHIND`) | precision **1.000** (0/529), recall 0.974 (74/76) |
| `lapsCompleted` absent or 0 | 17 of 17 such cars were retired |
| `pitsTaken` absent | 26 of 26 retired (+3 of the 6 DSQ), 0 of 529 classified |
| a **non-zero** `totalTime` ⟹ finisher | 398 of 398, no violations |

Bottom-six-of-the-field accuracy: 178 of 180 (30 races × 6), and both misses
are in one race.

**What is left over is undecidable, not merely unmeasured.** 2025 Qatar GP
(event `600052107`, competition `401737850`, 57 laps): two cars RETIRED on
lap 55 sit directly beneath CLASSIFIED cars on lap 56 — same `behindLaps`
of 2, same pit count, and 56→55 is the same one-lap step as 57→56. Those
rows come back undecidable — the band is "laps completed and pits both
published, `abs(behindLaps)` under 4 (or absent), and no non-zero
`totalTime`": **4.5 cars per race** on average (136 of 611),
and only **1 of 30 races** actually had a retired car inside the band.

**The threshold must never leave F1.** With no ground truth outside it,
`abs(behindLaps) >= 4` fires on 5 of 37 cars at North Wilkesboro —
including two only 8 laps down at 0.982 of race distance, which at a
0.4-mile short track are still circulating — 7 of 39 at Cup Indianapolis, 4
of 38 at Truck Indianapolis and 7 of 33 at the Indy 500.

**DSQ is not inferable at all** and is never guessed: 3 of the 6 sampled
carry the full race distance and look exactly like finishers.
`"Disqualified"` only ever comes from the status resource.

### Deliberate limitations

- **Live mid-race gaps are only half implemented, and the shipped half is
  unverified against a live race.** The free source is read at zero extra
  calls (above), but it has never been observed populated, so the honest
  expectation is that a live board may well carry constructor, car number,
  grid slot and an **empty** gap column exactly as it did before. The
  expensive half — a bounded per-car live pass — is deliberately deferred
  until one of the two dated probes in `ROADMAP.md` says it is worth 12
  calls per 180 s poll. Also not implemented: an F1-only caution indicator
  off the competition status `flag` (1 call per poll per live F1 event); all
  that has been observed is `CHECKER` post-race.
- **One accepted degradation, now much narrower:** if a car's
  per-competitor `status` call fails while its `statistics` call succeeds,
  a RETIRED F1 car in the confident band renders `"DNF"` (the old `"+57
  laps"` case is fixed), and one in the undecidable band keeps its true lap
  deficit plus `"Status unknown"`. Nothing is inferred outside F1 — a stock
  car 8 laps down at a short track is still circulating — so a NASCAR /
  IndyCar board still degrades to laps completed, which is all those series
  publish anyway. It never renders a bogus TIME in any case: a retired car
  has no `totalTime` or `behindTime` at all.
- Not implemented: the optional "retry the status call for just the
  undecidable cars" pass (≤6 calls, once per event). That band only exists
  when the status call **already failed in this same pass**, so an immediate
  retry spends calls on a resource that just errored, and the memo would
  then have to distinguish a retried miss from a fresh one. If it is ever
  wanted, it is a second bounded gather inside `_racing_car_details` over
  the cars whose outcome came back undecidable.
- The "already-enriched boards are not re-fetched" guarantee is a bounded
  **in-process** memo on the provider instance (`_racing_facts_memo`, 64
  events), not a read of the persisted board — the provider is stateless
  w.r.t. the DB and rebuilds the board from the site scoreboard every
  poll. In practice this is airtight for the paths that matter:
  `active_events` never returns FINAL rows, so `events_tick` polls a
  FINAL race only on the transition tick, and that enriched board is what
  `EventORM.leaderboard` persists. The cost of a cold cache is a process
  restart followed by a daily `get_events`, which re-spends tier B for at
  most 2 events per racing league. Making it durable is a scheduler-side
  change (skip re-enrichment when the stored row's board already carries
  non-empty scores) and belongs to whoever owns `scheduler/*`. Memo
  semantics are **unchanged** by the outcome work, which means a FINAL board
  rendered during a status-resource outage keeps its inferred / `"Status
  unknown"` rows for that process's lifetime. Not a regression — the same
  outage previously memoized a retired car as `"+57 laps"` — and left alone
  deliberately: skipping the memo for a degraded board would re-spend the
  per-car fan-out on every subsequent `get_events`, trading a documented
  zero-call guarantee for a rare improvement.
- The competition-distance memo caches a parse MISS (so a series that
  never publishes `general.laps` costs one call, not one per tick) but
  never caches a fetch FAILURE — so a series whose distance endpoint is
  persistently 404 costs one extra core call per 180 s tick per live
  race. Deliberate: memoizing the failure would permanently lose the
  denominator for that event.
- Considered and deliberately not done: dedicated optional
  `LeaderRow.constructor` / `car_number` / `grid` / `gap` fields. They
  would be better if the frontend ever wants to sort or column-align on
  them (`detail` is one opaque string today, so the UI cannot right-align
  a grid slot or colour-code a DNF), but that is a deliberate contract
  change for later, not a side effect of this one.
- A knock-on left alone because `scheduler/*` is out of scope for this
  section: the FINAL notification message is
  `f"{best.name} finished {best.position_label} ({best.score})"`
  (`scheduler/live.py`), so a followed driver who was disqualified now reads
  *"finished 2 (DSQ)"* and one who retired *"finished 18 (DNF)"*. Both are
  more accurate than the old *"(+15.080)"* / *"(56 laps)"*, but the verb
  "finished" is now the wrong word for those two cases — a wording fix for
  whoever owns that file.

### Tests

`backend/tests/test_espn_racing.py` (22 → 38 → **62** tests), fictional
"Apex Circuit Series" data — fictional drivers, constructors and circuits, core
payloads built to the real shapes. FINAL enrichment fills the right
columns for leader / gap / lapped / retired and asserts an exact call
count (1 event root + N statistics + N status, zero competition-statistics
calls) with `player_id` surviving as the bare ESPN id; a FINAL board is
memoized and the second poll adds zero calls; a live race costs one event
root + one distance call, zero per-car calls, and gets
`"Race · Lap 44 of 70"` with the distance fetched once across two polls; a
404 / exhausted-retry outage / non-JSON body each leave the plain board
intact and are not memoized; a monkeypatched parser raising `TypeError`
is caught; a per-car failure costs only that car's score; golf and
SCHEDULED races are skipped with a forbidden `_get_json` stub proving no
fetch; a 45-car field caps at exactly 40 statistics + 40 status calls with
the front of the field enriched; a stock-car board degrades to laps with
the entrant from `vehicle.team`, a negated `behindLaps`, dropped `.000`
placeholders and zero status calls. Plus pure-function tests for the
`$ref` host guard (an `evil.example.com` ref and a relative ref both
ignored), zero-placeholder dropping, the status-name-only rule, the lap
label, the competition aggregate, target bounding/ordering, and
"enrichment only ever adds" (identity return on empty facts). All 22
tests pre-existing at 2026-07-28 pass unmodified.

The 2026-07-29 amendment added 24 more. **Live gaps:** a populated site slot
(signed interval, lap deficit, a dropped zeroed `.000` placeholder, and a
lap COUNT never rendered as a gap); 6 absent/junk spellings of the slot,
parametrized, each asserting today's exact board; a FINAL board ignoring the
slot; the gap surviving tier-A enrichment with no per-car call; a
monkeypatched blow-up inside the slot parse costing the score column but not
the row; both nestings plus `abbreviation` keying. **Outcome:** all three
status types mapped by exact name and an unknown name yielding "not read"; a
DSQ rendering `"DSQ · Disqualified"` in both observed shapes (full distance +
gap, and zero laps + gap); a status-call failure inferring DNF for the parked
car while the lead-lap finisher stays a finisher; a status-call failure
marking the undecidable band and nobody else; a CLASSIFIED lapped car never
hedged even though its statistics alone are undecidable; a 7-case inference
matrix (each clause plus the band); a stock car 8 laps down with no status
resource never inferred a DNF; and the `"Status unknown"` part surviving a
detail that overflows `_DETAIL_MAX`.

Two pre-existing tests changed with the behavior, not to fit it:
`test_racing_car_status_only_trusts_the_status_name` →
`test_racing_car_status_maps_all_three_types_by_name` (the helper returns an
outcome, not a bool, and `STATUS_IN_PROGRESS` now maps to "not read" rather
than "not retired"), and the `_core_status` builder takes
`kind="classified"|"retired"|"dsq"`. The four-car finished-race stub also
gained the `pitsTaken` stat that every real classified car publishes (529 of
529), so the fixture is faithful to the payload the inference reads.


## September 2026 audit — player images and map figures

- `StatLeaderOut` / `StatLeader` now include nullable `photo_url`. The league
  provider passes through an available ESPN headshot; roster-derived leaders
  use the stored player's photo. The leaders cache key is `leaders:v2:{id}`.
- TheSportsDB player photos require a matching normalized full name. Explicitly
  wrong sports and unresolved namesakes return no image. The `tsdbphoto:v2`
  cache includes name, club and sport. URLs must be HTTP(S).
- Map figures are original SVG artwork in `views/map/figures.ts`: detailed
  airliners and varied supporters, with no emoji or external image dependency.
  Plane orientation uses the projected route; walking fans account for the
  camera bearing and individual arrival times. Map pins are native buttons.
- Motion stops or parks when the map is hidden; manual navigation interrupts
  the camera cinematic, and reduced motion suppresses flights and crowds.

Photo backfill also runs against the stored soccer roster when its provider
is unavailable. That path fills missing photo columns only; squad membership,
injury status, statistics and `roster_updated_at` remain authoritative to the
last successful roster fetch.

September 10 follow-up:

- HTTP 401/403 raises `ProviderAccessError` without retrying. Adapters must
  not convert access denial to an empty successful refresh. Split schedule
  requests stop on denial; the registry records failure and its circuit
  breaker limits subsequent requests.
- `FlightCinematic` owns its intro listener, camera frames, arrival crowd,
  aircraft, route, and cleanup timer. `stop()` releases all of them, including
  an intro cancelled before its first animation frame.
- Missing basemap sprites use a related existing icon or a neutral place
  marker. This runs synchronously on `styleimagemissing`, before rendering.
- The map renderer has a separate cacheable chunk. The build checks the
  static entry dependency graph and fails if MapLibre enters the initial
  bundle; `onlyExplicitManualChunks` keeps shared bundler helpers outside it.
- The desktop build removes obsolete generated onefile resources, uses
  ad-hoc signing when Developer ID is unavailable, and verifies the complete
  bundle. Ad-hoc signing does not provide Apple notarization.

## Editing the followed set (2026-10-10)

The setup wizard is also the editor ("Manage teams"), and until now an edit
was a reinstall. `POST /setup/follow` wiped every table and left the next
refresh to rebuild it — which a refresh cannot do: it reaches back seven
days, so each kept team's older results were gone for good, along with its
venue and "About" enrichment, its roster, its player follows and its alert
toggles. The dedupe ledger went too, so a final from that afternoon was
announced a second time. The wizard opened empty, so adding one team meant
re-picking all the others from memory; and the refresh the save kicked was
skipped outright whenever one was already running, which could leave the
new team without its games until the next cron.

One contract replaces all of that: **editing the followed set keeps what is
still followed.** It is binding on four modules — the repository (what a
save deletes), the setup routes (how a submitted pick is matched to a stored
team, and what an edit starts from), the scheduler (what a kick does to a
refresh in flight) and the wizard (what it opens on, and what it says before
it deletes).

The passages this amends are corrected in place: *Phase 3b → Follow
defaults*, the *Phase 5* foundation list, *Setup & onboarding*, the
FK-enforcement note under *Audit fixes* and *Player follows*.

### `repository.replace_followed` reconciles

The signature is unchanged (`session, leagues, teams, competitions=None`).
Afterwards `leagues`, `teams` and `team_competitions` equal the arguments,
and **any other row survives only if the new set still backs it**: a
team-keyed row needs its team, a league-keyed row its league, and a game its
league plus either a followed side or a whole-competition (`follow_all`)
league. With `T` / `L` the new team / league ids and `FA` the new
`follow_all` league ids, in this order on both dialects:

1. Upsert `leagues`, flush; upsert `teams`, flush. **Parents first, and
   upserted rather than re-created**: `upsert_team` writes identity columns
   only, so a kept team keeps every enrichment column; and a kept team id
   whose league changed is re-pointed here, before step 5 deletes the old
   league — delete-first trips the FK the team still holds.
2. Hand over game tags (`_hand_over_game_tags`): where a removed team has
   a kept team that is the same team in a game, that team takes the side
   id — see *Two picks of one team* below. Here, because the heir may be a
   team step 1 has just inserted (the side columns are FKs) and the removed
   team's `team_competitions` rows, which step 3 deletes, are what say
   which leagues it was fetched in.
3. Delete every `team_competitions` row. They are derived afresh by each
   save, so this one table is still replaced wholesale.
4. The child rules
   (`_drop_unbacked_children(session, team_ids, league_ids, follow_all_ids)`).
   The SQL statements run top to bottom, with one exception: the two JSON
   tag clears (`events`, `standings`) run after all three league deletes
   (`events`, `standings`, `standings_archive`), so the team ids they are
   judged by are read as late as possible:

   | Table | Rule |
   |---|---|
   | `players` | delete `team_id ∉ T` |
   | `news_items` | delete `team_id ∉ T`; delete `league_id ∉ L`; delete league-level rows (`team_id IS NULL`) whose `league_id ∉ FA` |
   | `games` | set `home_team_id` to NULL where `∉ T`, the same for `away_team_id`; delete `league_id ∉ L`; **then** delete rows with both sides NULL and `league_id ∉ FA` |
   | `events` | delete `league_id ∉ L`; in the rest, set a leaderboard row's `player_id ∉ T` to `None` |
   | `standings` | delete `league_id ∉ L`; in the rest, set a row's `team_id ∉ T` to `None` |
   | `standings_archive` | delete `league_id ∉ L` |
   | `player_follows` | delete `team_id ∉ T` |
   | `notification_prefs` | delete every scope except `global`, `team:{T}`, `league:{L}` |
   | `notifications_sent`, `stadiums`, `app_meta` | never touched |

5. Delete teams `∉ T`, then leagues `∉ L`.
6. Insert `competitions`.

What the rules amount to:

- **A game between a kept and a removed team stays**, with the removed side
  nulled. The sides are nulled before the last delete so that delete can
  tell a game nobody follows any more from one a kept team still plays in.
  An untagged game outside `FA` is safe to drop: a per-team fetch always
  tags a side, so only a whole-competition fetch writes one.
- **Two picks of one team: the kept pick inherits the removed one's
  games.** A nation picked under two national competitions is two `teams`
  rows that fetch the same fixtures (each has the other's league as a
  sibling competition), and `upsert_games` lets a later non-NULL side id
  replace an earlier one — so every game they share carries ONE of the two
  ids, the one fetched last. Nulling that id when its pick is removed would
  leave the games untagged and the next rule would delete them, with the
  kept pick still playing in them and a refresh reaching back one week. So
  a removed team's id is nulled **unless a kept team is fetched in the
  game's league under the provider key the removed team had there**, in
  which case step 2 moves the tag to it first. What a team is fetched as
  is its own `(league_id, provider_key)` plus its `team_competitions`
  triples — the removed team's as stored, the kept teams' as passed in.
  Both halves of the match are
  required: a provider key is unique only within a league (ESPN reuses ids
  across sports), and a kept team that is not fetched in that league never
  tags a game there. Where several kept teams qualify, the heir is the one
  whose id sorts last — `refresh_schedules` walks teams in id order, so
  that is the id the next pass would write anyway. A removed team with no
  heir is untagged as before. `prune_unbacked` has no such step and needs
  none: after a save a removed team is not stored, so no side names one.
- **A removed team's highlight is cleared, not its row.** The standings row
  and the leaderboard row stay; only the follow tag inside the JSON goes.
  The edited list is REASSIGNED (SQLAlchemy does not change-track in-place
  JSON mutation), and `events.state_updated_at` is deliberately not
  re-stamped — it is the recency signal of
  `event_finals_missing_notification`, and clearing a tag is not news.
- **`notifications_sent` is never deleted, by any save.** It is what stops a
  game that survives the edit, or returns with the next refresh, from being
  announced twice.
- **NULL is never "outside" a set.** `_outside(column, ids)` is
  `column IS NOT NULL AND column NOT IN (ids)`, with the empty set spelled
  out as `column IS NOT NULL` rather than left to each dialect's
  empty-`IN`: an untagged game or a league-level article must not vanish
  because no team happens to be followed. `ids` may also be a query
  (`NOT IN (SELECT …)`, see `prune_unbacked`) and keeps the same guard — a
  subquery that returns no row is the empty list again.
- First run, an identical re-save, a `follow_all`-only set and an empty set
  all go through the same statements; an identical re-save changes no row
  in any table.

### `repository.prune_unbacked` — the same rules, at the top of every pass

```python
async def prune_unbacked(session) -> None
```

Runs `_drop_unbacked_children` against the team ids, league ids and
`follow_all` league ids **as stored** — one implementation of the rules, so
a save and the sweep can never disagree about what is a leftover.

**The stored set is handed over as queries, not as ids read into Python.**
`replace_followed` passes sets (the removed teams and leagues still exist
while its rules run, so only the new ids can say what is kept);
`prune_unbacked` passes `select(TeamORM.id)`, `select(LeagueORM.id)` and
the `follow_all` subset, and every statement then tests
`NOT IN (SELECT id FROM teams)` and the like — the pref scopes against
`SELECT 'team:' || id FROM teams` and `SELECT 'league:' || id FROM leagues`,
with `global` always kept. Each statement therefore judges by the set as it
stands when that statement runs. The sweep starts every pass, the cron's
and the startup one included, so a save can commit in the middle of it;
swept against ids read beforehand, a league that save had just made
`follow_all` lost the headline-only `league:{id}` pref seeded for it and
then notified on every event type. Only the JSON follow tags cannot be
judged in SQL: for those the team ids are read once, **after** the rows
they are compared with, so a tag on a row always names a team that existed
when the row was read.

It exists because a refresh pass or live tick that read the OLD set can
commit a write just after the save's deletes — an untagged fixture or a
league article for a competition that has just stopped being followed in
whole, a table still tagged with a team that is gone — and nothing else
would ever remove such a row. With foreign keys enforced it can only reach
what no FK guards: untagged games and league-level news of a league that is
not `follow_all`, player follows and prefs whose team or league is gone,
and stale JSON follow tags. **On a consistent database it changes
nothing.**

`_daily_refresh` calls it in its own `session_scope`, first, before
`refresh_schedules`, inside a `try/except` that logs and continues: a prune
failure must never cost the pass its refresh. A save's kick is always
followed by a pass, so a save's leftovers are gone within seconds; anything
else waits at most a day.

### `POST /setup/follow` — a stored pick keeps its row

The route reads the stored teams and the stored `follow_all` leagues
**before** it writes anything, then resolves each submitted provider key on
its own. "Stored" means a `TeamORM` with the same `(league_id,
provider_key)` — the provider's own identity for the team, where the id is a
slug of a display name the provider is free to change.

- **`id` and `rss_feeds`** come from the stored row when the key is stored.
  Otherwise the id is the slug rule and `rss_feeds` is empty, as before.
  Every row a team owns hangs off its id, and the feeds exist nowhere but
  on its row (`upsert_team` writes that column).
- **Display fields** (`name`, `abbreviation`, `logo_url`, `color`) come from
  the catalog entry when the fetched catalog lists the key — so a rebrand
  or a new crest reaches a team that is already followed — and from the
  stored row otherwise.
- **The catalog is fetched only when the selection holds a key that is not
  stored** (502 on upstream failure, as before). A selection made only of
  stored keys makes no provider call, so an edit that adds nothing saves
  with ESPN down.
- **The 400 lists only keys that are neither stored nor in the fetched
  catalog.** A followed pick the catalog has since dropped is routine, not
  an error: the golf catalog is the current tournament's field, and the
  tennis and MMA catalogs are capped rankings a followed athlete slides out
  of. `by_key[key]` is never indexed unguarded.

**The headline-only `league:{id}` pref** (*Phase 3b → Follow defaults*) is no
longer written by every save — `replace_followed` now keeps the prefs of
everything still followed and the upsert merges, so re-seeding would switch
back off whatever the user had switched on:

- It is **seeded** when a league is `follow_all` in the new set, was not
  before this save, and has no `league:{id}` row.
- It is **removed** when a kept league stops being `follow_all` and the row
  is still an untouched seed. Left behind it would go on silencing every
  alert but `game_start` and `final` for that league's teams — in
  `notify_prefs.decide` the league row decides for any team with no row of
  its own.
- **Untouched seed** (`_is_untouched_seed`): not muted, `events` non-empty,
  and every stored key equal to `follow_all_default_events()`'s value for
  that key. Key by key, not equality with today's seed: older builds seeded
  five keys and today's seed has six. A muted row, or one with any toggle
  off the seed's value, is the user's own and is kept.

Everything stays in the save's one transaction; then commit, then
`jobs.kick_daily_refresh()`, as before.

### `GET /setup/selection` and `SetupStatusOut.refreshing`

```python
class SetupStatusOut(BaseModel):
    onboarded: bool
    followed_team_count: int
    refreshing: bool = False

class CurrentSelectionOut(BaseModel):
    league_id: str
    follow_all: bool = False
    teams: list[CatalogTeamOut] = []

class CurrentSelectionsOut(BaseModel):
    selections: list[CurrentSelectionOut]
```
```ts
export interface SetupStatus { onboarded: boolean; followed_team_count: number; refreshing: boolean }
export interface CurrentSelection { league_id: string; follow_all: boolean; teams: CatalogTeam[] }
export interface CurrentSelectionsResponse { selections: CurrentSelection[] }
```

`GET /setup/selection` → `CurrentSelectionsOut`: the followed set read back
as the selections that would produce it. **Database only** — no provider
call, and it never commits — because the editor has to open while ESPN is
down, and has to show a pick the live catalog no longer lists.

- One entry per stored league whose id is in `espn_catalog.CATALOG` **and**
  which is `follow_all` or owns at least one stored team. Membership of
  `CATALOG` itself, not `get_catalog_league`, which also resolves the
  internal sibling leagues the picker never lists.
- So the sibling competitions a national-team pick attaches on its own are
  omitted: they are neither, every save re-derives them, and posted back
  they would be rejected as empty selections.
- A `follow_all` league reads back with `teams: []` even when teams are
  stored under it (the follow route merges two selections of one league).
  The wizard's two modes are exclusive, and whole-competition wins.
- A team entry is a `CatalogTeamOut` built from the stored row.
- Deterministic order: leagues as `repository.list_leagues` returns them,
  teams as `repository.list_teams` does.
- Posted back as it stands — each team by its `provider_key` — it changes
  nothing.

`SetupStatusOut.refreshing` is `jobs.daily_refresh_running()`: the
`daily_refresh` lock is held. It is true for ANY pass — the cron's and the
startup spawn's as much as a save's — and across a restart inside one run,
so after a save it clears only once a whole pass has run against the set as
it now stands. Its clearing is the client's cue to refetch.

Both scheduler hooks are called through the module (`jobs.…`), like the
catalog accessor, so tests can patch them at the source.

### Scheduler: a kick restarts the pass instead of being dropped

`daily_refresh` stays single-flight — its upserts are SELECT-then-insert, so
two overlapping runs double-insert — and an overlapping caller still skips
and never queues. That is right for the cron and the startup spawn, whose
work the run in flight is already doing. It is wrong for the wizard's kick:
the run in flight read the OLD set.

```python
# app/scheduler/refresh.py — in-process state, like the lock (one worker)
def note_follow_change() -> None      # _follow_generation += 1; synchronous
def daily_refresh_running() -> bool   # _daily_refresh_lock.locked()
def _superseded() -> bool             # a pass is running AND its generation is stale
# app/scheduler/jobs.py
def kick_daily_refresh() -> None      # note_follow_change(), then spawn daily_refresh()
```

- **The kick marks, then spawns.** `kick_daily_refresh()` (still zero-arg)
  bumps the generation synchronously, before the spawn, so the mark cannot
  trail the save by a task switch. The spawned task then skips like any
  overlapping caller — the mark is what makes the kick count. Its log line
  says so: an overlapping caller that finds the running pass superseded
  logs "a refresh is already running and will restart for the new followed
  set — skipping", not the bare "skipping" of a cron or startup overlap,
  which for a save read like the kick being dropped.
- **The lock holder loops.** Each pass records the generation it started
  from; after it, `daily_refresh` runs another pass if that generation is
  stale. Nothing may await between that check and the lock's release, or a
  kick landing in the gap would find the lock held, skip, and leave nobody
  to run the pass it asked for. However many saves land during one pass,
  that is one more pass.
- **A stale pass stops at its next checkpoint** (`if _superseded(): return`):
  after each stage in `_daily_refresh`; at the top of the team loop, the
  sibling-competition loop and the `follow_all` loop in `refresh_schedules`;
  at the top of the league loops in `_refresh_events` and
  `refresh_standings` and of the team loop in `refresh_rosters`; and
  **between a schedule or event fetch and its write**, so games or a tagged
  leaderboard asked for on behalf of the old set are discarded, not stored.
  The stadium sweep has three of its own (2026-10-11): the stage calls
  `refresh_competition_stadiums(should_stop=_superseded)`, which asks
  before each league, before each team and after a rate-limit pause — see
  *The sweep and the map route* under *Stadium cache keys carry the id
  space*.
- **Nothing is cancelled.** The provider call in flight completes and is
  thrown away. A cron-started run has no task handle, and a cancel landing
  inside a session keeps the lock held while the close unwinds, so the
  replacement would skip itself. A roster sync in flight finishes its team;
  a write for a team that is gone fails its FK and is caught like any other
  per-team error.
- **A superseded pass skips its remaining stages**, including the closing
  `prune_stale_games`, which is only safe once schedules are fresh. The next
  pass starts from the top — `prune_unbacked` first.
- Outside a pass `_superseded()` is always false (`_running_generation` is
  `None`), so every checkpoint is inert for a stage run on its own —
  `refresh_news` on its interval, the startup `refresh_locations`.

### Frontend

- **`ManageTeamsWizard`** (`components/onboarding/`) is what the gear's
  "Manage teams" mounts. It runs `useSetupSelection()` (queryKey
  `["setup-selection"]`, `staleTime: 0`, `gcTime: 0` — never served from an
  earlier open) and **latches** the seed once the query is
  `isSuccess && isFetchedAfterMount`, then renders
  `<OnboardingWizard mode="manage" initial={seed}>`. The wizard copies the
  seed into its own state on mount; the query refetches whenever the app
  invalidates its cache, the save included, and a later value must neither
  reset nor remount a wizard the user is editing.
- **A failed selection fetch never falls through to an empty seed.** The
  wizard would open as if nothing were followed, and saving one pick from
  there would unfollow everything else. Until the latch, a loading panel
  stands in (spinner and Close; on error the message and Retry), and only
  Retry or Close leave the error state. The panel has its OWN
  `useModalChrome` and is never mounted beside the wizard — the Tab trap,
  unlike ESC, is not stack-aware. Both render inside `WizardFrame`, the
  shared full-screen shell, so the editor opens as one screen whose body
  fills in.
- **Seeding** (`seedFromSelection`): the step is `"leagues"` when anything
  is followed, else `"countries"` as in a first run; `selectedCountryCodes`
  is always `[]` — empty means no narrowing, where a filter derived from the
  follows would open the editor with every other country's leagues hidden;
  every `league_id` is selected in response order, `follow_all` entries are
  flagged, and the others bring their `teams`.
- **`onboarding/selection.ts` holds the selection and every way it can
  change, as pure functions** (`toggleLeague`, `toggleFollowAll`,
  `toggleTeam`, `clearLeagueTeams`), because these rules now decide what a
  save deletes and the repo has no component-test harness. **The state
  remembers more than it follows**: deselecting a league keeps its picks,
  its whole-league flag and its place in the order (`leagueOrder`), and
  switching a league to whole-league keeps its picks, so each toggle is
  undone by toggling again. Toggling a league off and on, and follow-all on
  and off, each return the identical `FollowRequest`. Only "Clear" forgets.
- **Nothing reads the raw fields as if remembered were followed.** The
  effective view is the only reading — `isFollowAll` (selected AND
  flagged), `followAllIds`, `pickTeamLeagueIds`, `totalSelected`,
  `followedLeagues` and `toFollowRequest(state)` — and the steps are handed
  it, so a deselected league's chip never shows an active follow-all pill
  and its picks are never counted or posted.
- One sequence is deliberately not a round trip: marking then unmarking
  follow-all on a DESELECTED league that still remembers picks leaves it
  selected with them. It errs towards following, never towards deleting.
- **A remembered league keeps its card under the country filter.**
  `offeredLeagues(leagues, countryCodes, rememberedLeagueIds(state))` is
  what `LeagueStep` renders: the chosen countries' leagues, those with no
  country, and every league selected at any point this run (`leagueOrder`)
  — selected or not. No chosen country means no narrowing. The card is the
  only undo a deselect has, and manage mode opens with no country chosen,
  so every followed league outside the countries picked later is on the
  grid only by this rule: keyed on "selected", a stray click removed the
  card with the selection and left nothing to click to put it back. A
  league that was never selected is still hidden.
- **The team grid shows three sources**: `mergeSelectedTeams(catalog,
  seeded, selected)` is the catalog in its own order, then any seeded or
  selected team it does not list, once each by `provider_key`. A seeded pick
  keeps its card after it is unpicked or cleared — the only way back to a
  follow the catalog has dropped. If the catalog fails to load, the error
  row and Retry stay and the picks render beneath as normal cards; Continue
  is gated on the picks alone, never on the catalog having loaded, since
  the save needs no catalog for a stored key.
- **Manage mode says what it will unfollow.** `removedFromSeed(seed, state)`
  returns one `RemovedFollow` per seeded league the save would touch:
  `leagueRemoved` (it leaves the set), `followAllRemoved` (dropped, or
  narrowed to picked teams) and the seeded `teams` no longer followed as
  teams (unpicked, or their league dropped or switched to whole-league).
  `ReviewStep` lists them above Confirm under "Will be unfollowed — their
  stored games, rosters and alert settings are deleted" whenever the list
  is non-empty. Intro copy: manage — "SportsDash will follow exactly these.
  Everything you kept stays as it is."; first run — "SportsDash will follow
  these."
- **Closing is off while the save is in flight**: ESC hits a no-op and the
  X is disabled. The POST cannot be recalled, so a wizard closed then would
  leave a save that lands with nobody to show its error or its syncing
  step. And `confirmFollow` calls `queryClient.invalidateQueries()` itself,
  right after the POST resolves and before the syncing step — that line
  runs whether or not the wizard is still mounted.
- **`App.tsx` refetches everything, once, when `refreshing` goes from true
  to false.** `useSetupStatus` polls on `statusRefetchInterval(data)`
  (`5_000` while `data?.refreshing`, else `false`). With games kept, the
  syncing step's `/today` wait can pass before the refresh has fetched
  anything, so this — not that wait — is what brings in a newly followed
  team's data. Because `refreshing` covers every pass, a status fetched
  during the startup or cron refresh starts the same watch, and the
  dashboard refetches when that pass ends too.

### Known limits

- **Nothing age-prunes `notifications_sent` or the news of a kept follow.**
  The wipe was the only delete either table ever had; both now grow for as
  long as their team or league stays followed.
- **A league from outside the picker catalog** (possible only through a
  hand-authored `teams.yaml`) is absent from `GET /setup/selection`, so
  the first manage save removes it with its teams — and the review step
  cannot list it as unfollowed, since it was never in the seed. The
  README's `teams.yaml` note says so.
- **An individual-sport league's ESPN feed is stored under one athlete.**
  Tennis, MMA, golf and racing have no per-athlete news scope on ESPN, so
  every followed athlete's fetch returns the same league feed and
  `upsert_news` keeps the first copy of each url: the rows belong to the
  followed athlete whose id sorts first. Unfollowing that athlete deletes
  them with the rest of its news; the next news stage stores the feed's
  current 20 items under the next athlete of that league, and older
  articles are not fetched again.
- **The news, team-info and location stages have no checkpoint inside**;
  whichever is running finishes before a stale pass stops. (The stadium
  stage was on this list until 2026-10-11. It is the one stage long enough
  to matter — minutes for a large field, tens of minutes once TheSportsDB
  throttles it — and now leaves between leagues, between teams and after a
  rate-limit pause.) The
  interval `refresh_news` runs outside any pass and can write league-level
  news once for a league that has just left `follow_all` —
  `prune_unbacked` removes it at the next pass to start after that write.
- **Postgres, reasoned rather than reproduced by a test:** a stale pass's
  insert for a removed team that commits between the save's child deletes
  and its team delete fails that delete on the foreign key. The save rolls
  back whole and a retry succeeds. The old wipe had the same window.
- The close-while-saving guard and the seed latch have no automated test:
  the repo has no DOM test harness.

### Tests

`tests/test_repository.py` (a `followed` fixture holding one of every row a
follow can own, plus the rows no follow keys): a partial removal keeps
every row of the kept follows; an identical re-save changes nothing; a
kept-v-removed game stays with the
removed side nulled while an unbacked one goes; removed tags are cleared in
standings and leaderboard rows; both `follow_all` flips; a team re-pointed
before its old league is deleted; a removed league's children all go while
a kept league's archive stays; a `follow_all`-only set and an empty set
spare what they should; a removed pick's game tags pass to a kept pick
fetched under the same league and provider key (never the key alone, the
last-sorted heir, none where no kept pick is fetched, and a pick the same
save adds — a swap, which fails on the foreign key if step 2 runs before
step 1); and
`prune_unbacked` as a no-op on a consistent database, removing exactly the
leftovers on an inconsistent one, on an empty database, with no team
followed at all, and sparing what a save commits from a second session
while the sweep runs, one test per query it is handed: a league added
(before its first delete, and before the prefs delete), a league switched
to whole-competition whose games are now untagged, and a team added along
with a game, a pref and a player follow keyed by it. For the JSON tags, a
team followed and tagged just before the read of `standings`, and of
`events`, keeps its tag — which holds the team ids after both reads.
`tests/test_setup_api.py`: a re-save of the same selection changes no row
and makes no catalog fetch; a ledgered recent final is not re-notified; a
mixed selection (a stored key plus a new one) keeps the team id through a
catalog rename and accepts a stored key the catalog has dropped; an
all-stored selection skips the fetch; the seed pref is written once and
only an untouched one — today's six keys or an older build's five — is
removed; `GET /setup/selection` omits siblings and non-picker leagues (and the
next save removes such a league),
lists no teams under a whole-competition follow, and round-trips;
`refreshing` is read through `jobs`; unfollowing either of two picks of
one nation keeps every stored result; the same provider key in two leagues
is two teams; and the three conditions on the seed pref each hold alone
(a seed-like row on a league followed by team all along survives, a
league already followed in whole is not re-seeded, an empty row is not a
seed).
`tests/test_scheduler.py`: two more kicks landing during a kicked pass
re-run it exactly once (was
`test_kick_daily_refresh_is_single_flight_across_kicks`, which pinned the
overlapping kicks being dropped); a superseded pass skips its remaining
stages, discards the schedule fetch a save landed in (through
`jobs.daily_refresh()`, with a different game id per call), makes no
further fetch and does not go on to leaderboard events; the mark is seen
before the kicked task runs; the prune runs before `refresh_schedules` and
a raising prune does not stop the pass; `daily_refresh_running()` stays
true across a restart; an overlapping caller's log line says whether the
running pass will restart; and `live_tick` sends and ledgers nothing for
games deleted while it was fetching.
`tests/test_player_follows.py`: `replace_followed` keeps a kept team's
follows (was `test_replace_followed_wipes_player_follows`).
Frontend: `components/onboarding/selection.test.ts` (seeding and its round
trip, the effective view, every transition and its round trips, purity,
`offeredLeagues`, `removedFromSeed`, `mergeSelectedTeams`) and `hooks.test.ts`
(`statusRefetchInterval`). `tests/openapi_snapshot.json` gained the route
and the three schema changes.

## October 2026 — ESPN refuses day-range scoreboards

Verified live 2026-10-10: every `scoreboard?dates=YYYYMMDD-YYYYMMDD` on a
**team-sport** scoreboard (soccer, basketball, hockey, baseball, football)
answers `400 {"code":400,"message":"Failed to get events endpoint."}` — any
league, past or future, with or without `limit` / `seasontype`. Single days
(`YYYYMMDD`), whole months (`YYYYMM`) and whole years (`YYYY`) still return
200, and tennis, golf, racing and MMA scoreboards still accept ranges. This
supersedes the "ranged scoreboard" wording for `get_competition_schedule`
above wherever the two disagree.

- **Fallback, not replacement.** Range-based fetches still ask for the day
  range first. On a 400 they switch to month buckets (`dates=YYYYMM`,
  `limit=1000`) for the rest of the window. `EspnProvider.
  _get_scoreboard_window` owns this for `get_competition_schedule`,
  `get_events` and the tennis half of `get_schedule`;
  `espn_playoffs.fetch_playoff_bracket` does the same with `seasontype=3`.
  UFC already scheduled by month and is unchanged.
- **Only a plain 400 triggers it** (`_range_rejected`). 401/403
  (`ProviderAccessError`), exhausted retries (`TransientProviderError`) and
  other statuses behave exactly as before — a denial or an outage is never
  answered by trying another request form.
- **Buckets are a superset; the existing final filter is the contract.**
  Games are filtered on their UTC start date, events on span overlap, both
  after an id merge, so a bucket's extra days (and any overlap with a ranged
  chunk that did succeed) drop out. The playoff fetch had no such filter and
  now trims bucket events to its window itself.
- **ET bucketing.** Month buckets are keyed by US/Eastern like every other
  scoreboard date: an NBA tip-off at `2026-11-01T00:30Z` came back in
  `202610`, not `202611`. `_month_buckets` therefore also reads the bucket
  holding `start − 1 day`, which adds a request only when the window opens
  on the 1st.
- **`limit=1000` for buckets**, not the ranged calls' 400: a month of MLB is
  417 events (2026-08) and ESPN truncates silently, oldest first.
- **Failure semantics.** Buckets are fetched independently — one failing
  logs and is skipped — and the provider method raises only when nothing at
  all came back, so an ESPN that refuses months too still reaches the
  scheduler and the circuit breaker rather than looking like an empty
  refresh. The bracket is all-or-nothing: any failed bucket fails the
  whole fetch, since a bracket missing a month of series is worse than
  none. (Amended 2026-10-11: the failure is raised as
  `espn_playoffs.EspnPlayoffsError`, which `/bracket` answers with 503; it
  used to come back as `[]`, indistinguishable from "no playoffs".)
- Each fallback logs one WARNING (`ESPN refused ranged dates for …`), i.e.
  one wasted request per follow_all league per refresh while ESPN keeps
  refusing ranges.

Tests (hermetic, stubbed `_get_json` / `_get_scoreboard` answering 400 to
ranges and 200 to months): `test_espn.py` — `_month_buckets`, the
competition/tennis/golf fallbacks, the long-window and late-ET-game cases,
partial and total bucket failure, and that 404/403/outage do not fall back;
`test_espn_playoffs.py` — the bracket fallback, window trim and ordering.

## Stadium cache keys carry the id space (2026-10-11)

The stadium cache (`stadiums`, `StadiumORM`) was keyed
`"{provider}:{provider_key}"`, and a bare provider id is not an identity.
ESPN numbers the teams of each US league from 1, so with the NFL and the
NHL both followed in whole the two sides numbered 1 shared one row:
whichever the sweep reached first owned it, and the other was never looked
up and was plotted at its ground. The map's "followed wins" dedupe matched
on the same pair, so following an NFL team also took the NHL team with its
number off the map. And a cached miss was shared too: one league's team
that could not be located held the other league's back for the whole retry
cool-down without its ever being asked about.

One contract replaces that: **a stadium row belongs to one team in one id
space, and its key says which.** It is binding on the repository (the key,
and the read of rows an older build left), the sweep (the cache's only
writer) and the map route (a reader, and the dedupe).

Nothing else moves. `stadiums.key` is still `String(160)` and nothing
references it; there is no migration, no `init_db` step and no delete.
`MapTeamOut.team_id` is still `"{league_id}:{provider_key}"` for a
competition pin, so `schemas.py`, `types.ts` and
`tests/openapi_snapshot.json` are unchanged.

The passages this amends are corrected in place: the *Phase 12* foundation
line and both mentions of the key under *12b*.

### `repository.stadium_key` — the only place a key is built

```python
def stadium_key(league: domain.League | LeagueORM, team_provider_key: str) -> str
    # "{provider}:{scope}:{team_provider_key}"
```

`scope` names the id space the provider numbers that team in:

| League | `scope` | Why |
|---|---|---|
| `provider == "thesportsdb"` | `"all"` | the provider looks a team up by `idTeam` alone, no league involved, so the whole provider is one space |
| `provider == "espn"` and `sport == "soccer"` | `"soccer"` | a club or nation keeps its ESPN id in every competition; a side in two followed competitions must be one row, and one identity for the dedupe |
| anything else | `league.id` | the NFL, NHL, NBA, WNBA and MLB each number their teams from 1 — the NBA and WNBA too, which a key scoped by sport would still merge; the athlete ids of an individual-sport league followed in whole are kept apart the same way |

Examples: `espn:nfl:1`, `espn:nhl:1`, `espn:soccer:364` (the same key from
`epl` and from `ucl`), `thesportsdb:all:134567`.

- **Both callers must land on the same key.** The sweep holds domain
  leagues (`sport` is the enum), the map route ORM rows (`sport` is a plain
  string); `Sport` is a `str` enum, so the soccer test reads the same on
  either, and a test pins it.
- **No catalog league may have the id `"soccer"`.** Any other ESPN league
  is scoped by its own id, so one literally called `soccer` would be filed
  into the soccer id space and trade stadiums with whichever club holds the
  same number. `test_no_catalog_league_id_is_soccer` guards
  `espn_catalog.CATALOG` and `NATIONAL_SIBLING_LEAGUES`.
- Nothing else builds a key by hand (`scheduler/stadium_cache._stadium_key`
  is gone). `get_stadium(session, key)` remains an exact-key read, and
  `upsert_stadium` has one caller, the sweep.

### `repository.find_team_stadium` — the only code that knows the old key

```python
async def find_team_stadium(
    session: AsyncSession,
    league: domain.League | LeagueORM,
    team_provider_key: str,
    team_name: str,
    *,
    adopt: bool = False,
) -> StadiumORM | None
```

1. The row under `stadium_key(...)`, if there is one. It always wins,
   whatever a legacy row or the catalog name goes on to say.
2. Otherwise the legacy row `"{provider}:{team_provider_key}"`. None →
   `None`.
3. **The name proof.** The legacy row is believed only when its `team_name`
   is this team's catalog name: both sides trimmed and casefolded, the
   catalog name first clipped to the column's 128 characters (it is
   compared as `upsert_stadium` stored it), and never when the result is
   empty. A prefix is not a match. One upsert wrote the name and the facts
   together, from the name the lookup used, so a matching name proves the
   facts are this team's. A row that fails belongs to another league's
   team, or to this one before a rename; it is ignored and the team
   resolves fresh.
4. `adopt=False` (the map route): the proven legacy row itself, read-only.
5. `adopt=True` (the sweep): **a copy**, inserted under the scoped key with
   every fact of the legacy row — `team_name`, venue, coordinates, capacity,
   opened, image, location, surface, `resolved` and `fetched_at` — flushed
   and returned. The legacy row is not touched.

- **Adoption copies; it never re-keys. Legacy rows are kept on purpose.**
  Rewriting a primary key is one-way: an older build run against the same
  database (a rollback, an older desktop app) would find no rows, drop
  every pin and send the whole field back through the rate-limited
  lookups. With a copy it still finds every row it wrote. Nothing in this
  change deletes a legacy row, and none is served unless it passes the
  proof.
- **Idempotent.** Step 1 returns the scoped row before the legacy key is
  consulted, so adopting twice copies nothing.
- **A copied miss keeps its age.** `fetched_at` comes across, so the
  sweep's retry cool-down (`_STADIUM_MISS_RETRY`, 15 min) runs from when
  the lookup really missed: an old miss is retried on the pass that adopts
  it and a fresh one still waits — what would have happened had the key
  never changed.
- **Logging is how the repair is read back.** A legacy row that fails the
  proof logs its key, the name it holds and the name it was asked to match
  (`find_team_stadium: legacy row … holds …, not … — ignored for …`),
  which is what a cross-league collision or a renamed team looks like. At
  INFO from the sweep, which writes the team's own row straight afterwards
  and so reports each row once; at DEBUG from the map route, which would
  otherwise repeat the line on every poll. An adoption logs at INFO.

### The sweep and the map route

- **`scheduler/stadium_cache.refresh_competition_stadiums`** —
  single-flight; run at every startup, in the 05:00 daily refresh and on a
  map kick — builds `repository.stadium_key(league, team.provider_key)`
  and reads through `find_team_stadium(..., adopt=True)`. The area-only,
  already-located and miss-cool-down checks then judge the adopted copy
  exactly as they would a row the sweep wrote itself, and a fresh resolve
  upserts under the scoped key. An upgrade therefore spends lookups only on
  the teams whose old row was really another team's; an adopted located row
  costs neither a lookup nor the pacing sleep.
- **Each league's summary line counts both**:
  `refresh_competition_stadiums: <league> — N/M team stadium(s) located (A
  adopted from legacy rows, F resolved fresh)`. To tell an adoption from a
  row already under the team's key, the sweep reads `get_stadium(key)`
  before the adopting read. An upgrade is reported once: the next pass
  reads `0 adopted from legacy rows` and logs no refused row, because a
  refused legacy row is never consulted again once the team has a row of
  its own. Its `resolved fresh` count is then only the misses being
  retried.
- **A lookup TheSportsDB refused is not a miss** (2026-10-11).
  `stadiums.lookup_stadium` returns `None` both when TheSportsDB has no
  such team and when it could not be asked (HTTP 429, a timeout); only the
  first is kept in its process cache, and
  `stadiums.lookup_settled(team_name, sport=...)` reports which it was
  (a blank name is settled; it never makes a request). The sweep asks
  first (`_tsdb_answered`), and for an unanswered team it writes nothing
  yet: it logs `refresh_competition_stadiums: TheSportsDB is rate limiting
  (no answer for <team>) — pausing …`, sleeps
  `_TSDB_RATE_LIMIT_PAUSE_SECONDS` (30) with no database session open, and
  resolves the team once more. Whatever that second attempt gets is
  recorded as before — the stored-fixture venue geocoded, or a miss row
  with its 15-minute cool-down — so a team is never waited on twice and
  the map still gets the row that stops it kicking a sweep on every poll.
  A definitive miss is recorded at once, with no pause. From the first
  refusal on, the pass resolves every `_COMPETITION_THROTTLED_DELAY_SECONDS`
  (5) instead of every `_COMPETITION_RESOLVE_DELAY_SECONDS` (1), in every
  league still to come. A pause whose retry still got no answer is
  "fruitless"; after `_TSDB_MAX_FRUITLESS_PAUSES` (3) of them the pass
  stops waiting and behaves exactly as it did before this change, still at
  the slow pace — TheSportsDB has refused every lookup for a whole session
  before, no pause will outlast that, and each one holds up the stages of
  the pass that come after the sweep (and the status's `refreshing` flag
  with them) for another half minute. A pause that got its team an answer
  does not count.
  Measured on a copy of a real database before this: 29 teams refused in
  a row, each recorded as a miss and off the map until a later sweep —
  the map kicks a sweep only for a team with **no** row.
- **The sweep steps aside for a save** (2026-10-11).
  `refresh_competition_stadiums(should_stop: Callable[[], bool] | None =
  None)`: `daily_refresh` runs it as `_competition_stadiums_stage`, which
  passes `should_stop=_superseded`; the map kick and a direct call pass
  nothing and always run to the end. The slow pace and the pauses above
  made the stage five times longer or more from the first unanswered
  lookup — any one: a 429, a timeout and a reset are not told apart — and
  the pass a setup save asks for cannot start until the stage returns: on a
  field of 100 with one refusal at the third team, a save a minute in
  waited 460 s for the newly followed team's schedule, standings and
  roster. `should_stop` is asked **before each league** (ahead of the
  catalog request for its teams), **before each team** (ahead of the
  session read) and **straight after a rate-limit pause** (ahead of the
  second resolve). So a save waits for at most the team in hand: one
  30-second pause or one 5-second pace, plus the request in flight. On
  leaving the sweep logs `refresh_competition_stadiums: the followed set
  changed — leaving the rest of the sweep to the next pass`, once, and
  that league's summary line is not written. Rows already written stay; a
  team the sweep was pausing on gets no row, so the next sweep asks about
  it afresh rather than finding a miss to cool down. The pass that
  restarts runs the sweep again: it skips what is cached, as any sweep
  does, and resolves at the normal pace until it is refused again.
- **`GET /api/map` reads the stadium cache and never writes it.** Each
  competition team is read through `find_team_stadium(...)` without
  `adopt`: a request session is never committed, so a copy made there would
  only hold a write transaction open against the sweep and then be rolled
  back. A legacy row that is not provably the team's reads as "not cached"
  — the team is omitted and the `pending` flag kicks the sweep — so it
  waits for its own lookup instead of borrowing the ground of whoever
  shares its number. A proven legacy row is served as it stands, so pins
  survive the gap between an upgrade and the first sweep. (12b's "on miss,
  resolve … and cache it" is not what the route does: it omits the team
  and kicks the sweep.)
- **The dedupe is the same key.** `followed_identities` is a `set[str]` of
  `stadium_key(league, team.provider_key)` over the followed teams, and a
  competition team is skipped when its own key is in the set. A followed
  club is still recognised in every soccer competition it plays, and a
  followed NFL team no longer hides the NHL team that shares its number.

### Known limits

- **Legacy rows stay in the table indefinitely.** After the first sweep
  each followed competition team has a scoped row, and the legacy rows
  serve only an older build; no pin is drawn from one again. They are
  still read into the venue-name index (`repository.list_located_stadiums`
  → `venue_coords.build_index`), where a row is only the coordinates of the
  venue it names — true whichever team it was filed under, with one
  exception. A legacy row an older build cached at an area centroid
  (`venue == location`, see *Narrowed 2026-07-27* under Phase 11) is
  copied and the **copy** repaired; the original keeps the centroid under
  the area's own text, so a game whose `venue` were exactly that text would
  be placed there. Removing legacy rows is a separate decision, to be
  taken once no older build can run against the database.
- **A team renamed since it was cached fails the proof.** That costs one
  fresh lookup; believing the row instead could cost a pin in the wrong
  city, and the two cases look the same from the row.
- **The proof is the name and nothing else.** A wrong adoption needs the
  same provider id and the same display name in two id spaces under one
  legacy key. No such pair is known, and nothing else would catch one.
- **Not yet watched on a real upgraded database.** The tests seed legacy
  rows; the log lines above are how the first real sweep is to be read (see
  the root ROADMAP.md).
- **The rate-limit pause is tuned against a script, not the real limit.**
  How long TheSportsDB keeps answering 429 has not been measured, so the
  30-second pause, the 5-second pace and the cap of three fruitless pauses
  are untested live. A block that outlasts the three pauses still ends in
  miss rows for the teams after it, retried by a later sweep once their
  cool-down has passed.
- **One unanswered lookup still slows the rest of the sweep fivefold.**
  Whatever leaves a lookup unsettled — a 429, a single timeout, a
  connection reset; `lookup_settled` cannot tell them apart — sets the slow
  pace for the rest of the pass and logs "TheSportsDB is rate limiting".
  Only a setup save's wait was fixed, by the sweep stepping aside. Left
  alone, a sweep of 100 teams refused once at the third sleeps 522 s where
  it used to sleep 100, and until it ends `refreshing` stays true and the
  venue geocoding and stale-game prune behind it wait. Slowing down only
  for a real 429 needs the status carried out of `services/stadiums.py`.
- **Two refusals are still read as answers.** A 429 on `lookupvenue.php`
  after a team search that succeeded settles a partial record — no photo,
  year opened or coordinates — which then goes on to the geocoder. And
  `refresh_locations`, the followed teams' path, still reads a refused
  lookup as "TheSportsDB has nothing" and falls through to its other
  sources. Neither is changed by this fix.
- **The racing memos in `providers/espn/provider.py` and `EventORM.id`**
  still treat an ESPN event id as unique across leagues. Unchanged: the
  memos are process-lifetime dicts with nothing persisted to repair, and no
  shared event id has been observed.

### Tests

`tests/test_repository.py` (SQLite and Postgres): the id spaces — NFL ≠
NHL, NBA ≠ WNBA, `epl` == `ucl`, two TheSportsDB leagues equal, an ORM row
and a domain league equal; no catalog league id is `soccer`; a legacy row
is read only as the team it names, serving it writes nothing, adopting
copies every column (the seeded row has none null, so a column the copy
forgot cannot hide behind two NULLs) and leaves the original, a second
adopt adds nothing, and once the scoped row exists it wins over a changed
catalog name; a miss is adopted at its original `fetched_at`; and the name
proof case by case — casing and padding drift, a prefix, blanks on either
side, a name longer than the column.
`tests/test_stadium_cache.py` (the catalog and the lookup stubbed, the
lookups recorded): two leagues' team 1 get two lookups and two rows; a
legacy row is adopted with its facts intact and only the unproven team is
looked up; a second sweep looks nobody up and changes no row; a legacy row
under another name is left alone and the team resolved fresh; one row and
one lookup for a club listed by two soccer competitions; one league's miss
does not stop the other league's lookup; an adopted miss is retried or
held by its own age; and the summary and refused-row log lines, on the
upgrade pass and on the one after. The same file then runs the REAL
resolve against a scripted TheSportsDB (HTTP transport, geocoder and
`asyncio.sleep` replaced, the sweep's own delay constants asserted): a
team refused once is looked up twice, paused on once, and located from the
provider's record with no row written in between; refused twice it is
resolved from its stored fixture venue in a single write; refused for good
it is looked up twice, never three times, and its miss then cools down; a
definitive miss is looked up once with no pause; the slow pace starts at
the first refusal and carries into the next league; the pass stops
pausing after three fruitless pauses; and pauses that each got an answer
do not count against that. Then `should_stop`: told to stop, the sweep
finishes the team in hand, asks about no other, lets go of its lock and
logs that it left once, and the next sweep resolves the rest; it does not
list the next league's teams; told during a pause it does not ask again
and writes nothing for that team; and, through the real `daily_refresh`
over a field of 100 (refused once at the third team, and once at every
team), a `note_follow_change()` a minute into the stage starts the next
pass within one pause plus one pace with at most one more team asked
about, and that pass's sweep locates the whole field.
`tests/test_stadiums.py`: `lookup_settled`
after a refusal, a hit, a definitive miss and for a blank name, without
making a request.
`tests/test_api.py`: the map does not pin a team at another league's legacy
stadium — the proven side keeps its pin, the other is omitted, the sweep
is kicked, and the request issues no INSERT, UPDATE or DELETE; teams
sharing an id are pinned at their own grounds from their own rows; a
followed team no longer hides the same id in another league; a followed
soccer club is still deduped out of a second competition, with a second
located club proving that pins are drawn at all. Each of these disables
weather, which the route would otherwise fetch for football and soccer
pins. `test_map_plots_offseason_follow_all_league` now seeds
`espn:soccer:111` / `espn:soccer:222`.
`tests/test_map_coarse_locations.py`: the centroid repair is parametrized
over a row under the scoped key and one cached by an older build — the
sweep must judge an adopted copy as it would its own row, or the upgrade
carries the bad pin across.

## Data freshness — a display left running stays current (2026-10-11)

SportsDash is left open for days — a wall display, a pinned browser tab,
the desktop app — and its views stay mounted once visited. Most hooks
fetched once per key and never again; the ones that polled were Today,
News, a live box score, a map with a competition pin or an upcoming game,
and "On this day" every six hours. So a display went on showing the
standings, calendar, results, leaders, bracket and previews it had loaded
on the day it was opened, until somebody reloaded the page.

The fix has three parts, and the second and third exist because of the
first:

1. **A freshness keeper** in the frontend refetches every mounted query
   that has outlived a per-root max age.
2. **`services/cache.py` keeps an allowlist of upstream read caches
   in-process when there is no Redis** — the default on the desktop app and
   in dev. Without it every refresh of an upstream-backed endpoint would
   repeat its whole fan-out, and refreshing on a timer would multiply ESPN
   and Open-Meteo traffic.
3. **Five routes answer 503 when everything upstream failed**, instead of
   an empty 200. A refresh that lands in an ESPN or Open-Meteo outage would
   otherwise replace good data on screen with "nothing to show" and hold it
   for the row's max age.

What bounds the load is the mechanism, not a number: the max ages below,
one provider-backed refetch per tick, and the server-side cache in front of
every fan-out.

The passages this amends are corrected in place: the `services/cache.py`
contract, the `src/main.tsx` line under *File ownership*, `src/hooks.ts`,
the cache notes under *Phase 18*, *21*, *22* and *30*, *Historical
archives*, the cache-counter row of `/api/metrics`, *"On this day"* and the
bracket's failure semantics under *October 2026*.

### The keeper (`src/lib/freshness.ts`)

```ts
export const TICK_MS = 30_000;               // how often the keeper looks at the cache
export const DEFAULT_MAX_AGE_MS = 900_000;   // a root with no policy row
export const ERROR_RETRY_MS = 300_000;       // after any error that came with an answer
export const UNREACHABLE_RETRY_MS = 60_000;  // after no answer at all, or the proxy's 502
export const UPSTREAM_PER_TICK = 1;          // provider-backed refetches per tick

export type MaxAge = number | null;          // ms since the last attempt; null = never refreshed once loaded
export type FreshnessRule = MaxAge | ((key: QueryKey, data: unknown, nowMs: number) => MaxAge);
export type CostClass = "db" | "upstream";
export interface FreshnessRow { maxAge: FreshnessRule; cost: CostClass }
export const FRESHNESS_POLICY: Record<string, FreshnessRow>;  // keyed by queryKey[0]

export function isHot(game: Game | undefined, nowMs: number): boolean;
export function maxAgeFor(key: QueryKey, data: unknown, nowMs: number): MaxAge;
export function costFor(key: QueryKey): CostClass;
export function isDue(key: QueryKey, state: QueryState, nowMs: number): boolean;
export interface AgedQuery { queryKey: QueryKey; state: QueryState }
export function selectDue<T extends AgedQuery>(queries: readonly T[], nowMs: number, upstreamBudget: number): T[];
export function startFreshnessKeeper(client: QueryClient, tickMs?: number): () => void;  // returns stop()
```

No React and no DOM: the policy functions are pure with an injected clock,
and the keeper itself is one `setInterval` plus two TanStack manager
subscriptions.

- **Started from `main.tsx`, once, on the app's `QueryClient`.**
  `startFreshnessKeeper(queryClient)` sits on the line after the client is
  constructed, before the first query mounts. No hook, no view and not
  `App.tsx` knows it exists. `freshness.test.ts` reads `main.tsx` as text
  and fails when the import or the call is missing — a commented-out call
  does not count. Every other test builds its own client, so without that
  guard a merge that dropped the one wiring line would switch the whole fix
  off with the suite still green.
- **Why it lives outside the hooks.** Interval defaults on the
  `QueryClient` cannot do the job: a hook that passes `refetchInterval`,
  even as `undefined`, overrides them. The keeper reads each query's
  **age** — time since its last attempt, whoever made it — so a hook that
  polls faster simply never comes due, and one that does not poll at all
  is still kept current. **The two never add up**: on a 5-minute row a
  caller polling every 60 s makes 60 requests an hour, and one polling
  every 10 minutes, or passing `undefined`, makes 12.
- **A tick** runs every `TICK_MS`, and at once when the browser tab
  becomes visible or the network returns. If the tab is hidden or the
  browser is offline it does nothing. Otherwise it takes the **active**
  queries (watched by at least one enabled observer), drops the static
  ones, hands the rest to `selectDue(…, Date.now(), UPSTREAM_PER_TICK)` and
  refetches exactly that set with `cancelRefetch: false`, which joins a
  fetch already in flight rather than aborting and restarting it.
- **`stop()` ends the refetching only**; the key defaults set at start
  (below) stay on the client.

### When a query is due (`isDue`)

With `last = max(dataUpdatedAt, errorUpdatedAt)`, a query is due when
`now − last ≥ limit`. It is never due:

- while `fetchStatus` is not `"idle"` — fetching, or paused offline, is an
  attempt already under way;
- before its first attempt (`last === 0`) — the first load belongs to the
  hook that mounts it;
- when its row's max age is `null` — with one exception, *a row that never
  loaded*, below.

After a successful attempt `limit` is the row's max age. **After a failed
one it depends on the error** (`ApiError.status`, and for a 502 the body
`ApiError` keeps as its message):

| The last attempt failed with | Asked again after | Why |
|---|---|---|
| no HTTP status (the request got no answer), or a 502 whose body is not a JSON object (the proxy's) — "unreachable" | `min(max age, UNREACHABLE_RETRY_MS)` — 60 s | nothing is listening, so the retry is free, and a quick one shortens the outage on screen |
| anything else — 503, 504 and the backend's own 502 included | `min(max age, ERROR_RETRY_MS)` — 5 min | the backend is there but did not help |

A failed row is never asked more slowly than a healthy one, and a failed
*first* load — no `dataUpdatedAt` to measure from — is timed from the
failure. This retry is also what recovers from a backend restart with the
tab on screen, which no browser event announces.

- **A 504 gets no longer wait of its own.** It is the Bun proxy giving up
  on a fan-out that outran its 20 seconds, and "do not start a second
  fan-out" would suggest waiting out the row's full max age. That rule was
  written first and withdrawn before release, for three reasons. The app's
  `retry: 1` sends the second request one second later whatever the keeper
  decides. The backend does not stop when the proxy hangs up — it finishes
  the fan-out and caches it — and every upstream cache lives at least 10
  minutes, so a request 5 minutes later is answered from that cache; the
  row's full max age is as long as its cache lives or longer (`scorers`: 60
  minutes against 30), so that request met a cold fan-out, and a timeout,
  again. And `MatchupView` replaces a loaded preview with its error panel
  when a refetch fails, so a single timed-out refresh left the Matchup tab
  on that panel for 30 minutes.
- **What counts as unreachable depends on what answers for the backend.**
  The Bun proxy (the Docker stack) answers a refused connection with 502,
  and in the desktop app `fetch` itself rejects with no status; both are
  the first row. Under `bun run dev` the Vite proxy reports a refused
  connection as a bare HTTP 500, which is the second row: a stopped API is
  re-asked there every 5 minutes, not every minute. The keeper does not
  read 500 as unreachable, because the backend's own 500 on an upstream row
  would then be re-asked every minute.
  **In the desktop app the backend's own 500 arrives without a status.**
  That reasoning holds wherever the 500 arrives with its status: behind
  the Bun proxy and under `bun run dev`. In the desktop app it arrives
  without one. The backend's 500 is always an unhandled exception (no
  route raises one), which Starlette answers outside `CORSMiddleware`, so
  it carries no `Access-Control-Allow-Origin`. The webview calls the API
  cross-origin, rejects that response with no status, and the keeper files
  it under the first row: a crashing upstream row is asked again after a
  minute there, not five, and a never-loaded `null` row that crashes is
  retried rather than final. An `HTTPException` — a 404, the 502 below,
  the 503s — is answered inside the middleware and keeps its status on
  every install.
  Only the server's half is pinned by a test (the header is missing from a
  crash's 500 and present on a 404); that a webview then rejects the
  `fetch` is the Fetch standard's rule and has not been watched.
  **The backend's own 502 is not the proxy's.** `GET /setup/teams/{id}`
  answers 502 when the team catalog cannot be fetched (*Setup routes*) —
  the only query the backend answers that way; the save can too, but a
  mutation is not the keeper's — and that is an answer: the second row,
  and on its `null` row final. The two are told apart by the body — FastAPI's
  starts with `{` (`{"detail": …}`); the Bun proxy's is the text `Bad
  Gateway`, another proxy's an HTML page or nothing, and all of those are
  the first row. Read as unreachable, the wizard's Teams step re-asked a
  failing provider every minute and a half for as long as it stayed open,
  each time a fresh upstream call with a traceback in the backend log. A
  reverse proxy that reports a refused connection as a JSON object would
  be read as an answer, like Vite's 500.
- **A row that never loaded.** A `null` max age means *never refreshed*,
  not *never loaded*. A query on such a row that has **no data**, and whose
  last attempt failed as unreachable (the first row of the table), is asked
  again every `UNREACHABLE_RETRY_MS` until it loads; from then on it is
  never due. Without this a display that booted before the API — a kiosk
  browser after a power cut, served its app shell by the service worker —
  stayed on `App.tsx`'s "Couldn't reach the SportsDash API" splash until
  somebody pressed Retry: `setup-status` was the only mounted query and its
  row is `null`. An error that came with an answer stays final (an
  archive's 404 is not asked for every minute, nor is a team catalog the
  backend said it could not fetch — its own 502, above), and a `null` row
  that has
  data is never due whatever a later refetch of it did. Each retry puts the
  dataless query back to `pending` for the attempt — TanStack's rule — so
  the splash shows its plain loading state for a second or two, about every
  minute and a half, while the API is down.
- **A timestamp ahead of the clock is ignored.** `dataUpdatedAt` or
  `errorUpdatedAt` later than `now` was written before the system clock was
  set back (an NTP correction, a manual change) and says nothing about the
  query's age; read literally it would hold the row back for the size of
  the step plus its max age. Each stamp is dropped on its own, so a refetch
  that fails after the step is still paced by its error. When no stamp is
  left the age is unknown and the query is due at once (ranked last among
  the upstream rows, as zero overdue); the attempt stamps it afresh. A
  `null` row with data stays never due.

### The policy table (`FRESHNESS_POLICY`)

Keyed by `queryKey[0]`. The age is counted from the query's last attempt.
`cost` says what one request can reach: `"db"` endpoints read only the
database, `"upstream"` ones can call ESPN or Open-Meteo and are rationed
(next section). Where a hook polls on its own, its row is no shorter than
that hook's slowest interval, so the keeper only steps in once the hook has
stopped asking.

| Root | Max age | Cost | Why |
|---|---|---|---|
| `schedule`, `events`, `results` | 5 min | db | straight from the database, which the backend's own jobs keep moving (the live tick, the events tick, a standings refresh on every FINAL) |
| `today`, `news` | 5 min | db | both poll themselves at least this often; the row matters after a hidden or offline spell, when the keeper catches them up at once instead of on the hook's next beat |
| `standings` | never when `key[2]` is a number; otherwise 5 min | db | a numeric season is an archive. Everything else refreshes — the `"current"` sentinel and any key shape the rule has not seen: guessing wrong must cost a request, not freeze the table |
| `teams` | 10 min | db | follows can be edited from another device |
| `roster`, `nation`, `player-follows` | 15 min | db | the pre-game roster re-sync is a 15-minute job |
| `matchup` | never once the payload's game is final; 5 min while it is hot; otherwise 30 min | upstream | a preview is a dozen or so provider calls; its lines and lineups move in the run-up and during play, and a final never changes |
| `game-detail` | never once final; otherwise 5 min | upstream | the box score polls itself while live; this covers the hours before kickoff, and the kickoff itself, which that poll cannot see coming |
| `game-odds` | 30 min | upstream | two provider calls per game id, up to 60 ids |
| `leaders`, `bracket` | 30 min | upstream | one ESPN call for a board, a few for a bracket |
| `scorers`, `schedule-weather` | 60 min | upstream | the widest fan-outs: a summary per final game, a forecast per outdoor game on the calendar |
| `map` | 15 min | upstream | the map polls itself only while it has a competition pin or an upcoming game; with neither it would never ask again |
| `on-this-day` | never | upstream | keyed by local day, on its own 6-hour timer |
| `history-results` | never | upstream | a past season is immutable |
| `meta`, `setup-status`, `setup-selection`, `notification-prefs` | never | db | static for the session, or open only inside the setup wizard |
| `setup-leagues`, `setup-teams` | never | upstream | the same |
| any other root | `DEFAULT_MAX_AGE_MS` — 15 min | upstream | a hook added without a row still cannot go stale forever; nothing can tell what its endpoint costs, so it is rationed |

- **`isHot(game, now)`**: `in_progress`, or `scheduled` with kickoff
  between an hour ahead and six hours overdue. A game still "scheduled"
  past its start time is one the backend has not caught up with yet, so it
  counts; after six hours it is a postponement nobody recorded.
- **`setup-status` must stay unpolled.** `App.tsx` refetches *everything*
  when the status's `refreshing` flag clears (*Editing the followed set*);
  a keeper polling the status would let the nightly refresh trigger that
  every morning. The retry of a row that never loaded (above) is not a
  poll: it ends with the first successful load, and a test holds the
  status at that one request for the following 24 hours.
- **A new query root needs a policy row** — the binding rule for whoever
  adds a hook. `freshness.test.ts` reads `hooks.ts` as text and fails when
  a `queryKey: ["…"` root has no row, and when a row names a root
  `hooks.ts` no longer has (a row left behind by a rename does nothing
  while the renamed query silently falls back to the default). It also
  pins the standings key shape the rule reads: `season ?? "current"` in
  slot 2, with `season` a `number`.
- A root is matched with `Object.hasOwn`, so one named `constructor` is not
  a policy row.

### One upstream row per tick (`selectDue`)

Every due `"db"` query is refetched in the tick it comes due. Of the due
`"upstream"` queries at most `UPSTREAM_PER_TICK` — one — is started per
tick: the **most overdue**, measured past its own limit, so a row that has
waited through several ticks is never passed over by one that came due
later, and a failed row ranks by its retry time rather than by its age.

The same budget applies in steady state and when catching up. After a
night with the tab hidden, every db row and the single most overdue
upstream row go at once; the rest drain one per 30 s tick instead of
landing on ESPN as a burst. Three upstream rows that come due in the same
instant start at 0, 30 and 60 seconds.

Parked and static queries are excluded *before* selection, not after:
`refetchQueries` refuses both, so one selected here but skipped there would
hold the upstream slot on every tick and starve the rows behind it.

### Hidden, offline, parked, unobserved

- **Hidden browser tab: no request at all.** "Hidden" is TanStack's
  `focusManager`, i.e. `document.visibilityState === "hidden"` — a
  background tab or a minimised window; a visible window that merely lacks
  keyboard focus still refreshes. On return the keeper ticks immediately
  and catches up under the budget above.
- **Offline: no request**; the same catch-up on the browser's `online`
  event.
- **Parked (`enabled: false`): never fetched.** A query whose only
  observers are disabled is not "active". A key that also has a live
  observer is refreshed as usual.
- **Unobserved: left alone.** A query still in the cache after its view
  unmounted has no reader; its hook decides again when the view next
  mounts.
- **A view behind another app tab is none of these.** See *Known limits*.

### Key defaults for the id-list queries

`useGameOdds` and `useScheduleWeather` key on the sorted list of game ids
derived from another payload (`["game-odds", "<ids>"]`,
`["schedule-weather", "<ids>"]`). Once the schedule refreshes every few
minutes that list — and so the key — changes whenever a game kicks off or
goes final, and a new key is a new, empty query: the upset radar would
drop to its loading line and every calendar forecast glyph would vanish
until the batch returned.

`startFreshnessKeeper` therefore sets, for exactly those two roots,

```ts
client.setQueryDefaults([root], { placeholderData: keepPreviousData });
```

so the previous list's data stays painted (`isPlaceholderData: true`,
`isLoading: false`) until the new list's first request settles.

- **If that first request fails, the placeholder is dropped.** TanStack
  shows placeholder data only while a query is pending, and a new id list
  is a new query with no data of its own. So when the list changes during
  an ESPN outage — `/odds` answers 503 for the new one — the odds chips
  and the upset radar are gone until a request succeeds; the keeper asks
  again after 5 minutes. This is the one case the 503 does not cover: a
  failed **refetch** of a list already loaded keeps its data, a failed
  first request for a new list has none to keep. The list does change
  during an outage: Today's rolls over at local midnight whatever ESPN is
  doing, and the scoreboard can go on moving games through their phases
  while the summary and predictor endpoints the lines come from fail. The
  same holds for the calendar forecasts in an Open-Meteo outage:
  `/schedule/weather` answers 503 like `/odds`, so a new list keeps only
  the forecasts still in the backend's cache (a `weather:` entry lives 45
  minutes) and, once none is, has no glyph at all until a request
  succeeds. Fixing it
  needs a stable key filtered on the client, i.e. an edit to `hooks.ts`; a
  key default cannot do it. Pinned as it stands.
- **The hooks must pass no `placeholderData` of their own.** An explicit
  option on the hook, even `undefined`, beats a key default. A guard test
  reads both `useQuery` blocks in `hooks.ts`; a hook that starts setting
  its own must drop the keeper's default for that root.
- **Those two roots only.** Anywhere else the previous key is a different
  subject — one league's leaders must never sit under another league's
  heading — and a test pins that `leaders` still blanks on a key change.

### `services/cache.py` without Redis

With a `redis_url` configured the cache behaves exactly as before,
including the silent no-op when Redis is down — **a Redis failure is a
miss, never a fall-back to the in-process store.** That is decided on
every call (`_is_local_key`), because a configured `redis_url` whose
client could not be built also leaves the client `None`.

With **no** `redis_url`, `cache_get_json` / `cache_set_json` use an
in-process TTL store for keys whose prefix is on `_LOCAL_PREFIXES`, and
remain no-ops for every other key:

| Prefix | Written by | Holds | TTL |
|---|---|---|---|
| `weather:` | `services/weather.py` | one venue's forecast for one day | `weather_cache_minutes` (45) |
| `odds:` | `routes/odds.py` | one game's line — or its "no line" answer | 10 min |
| `leaders:v2:` | `routes/leaders.py` | a league's ESPN-wide board, when the feed returned rows | 15 min |
| `scorers:` | `routes/scorers.py` | a league's Golden Boot | 30 min |
| `bracket:` | `routes/bracket.py` | a league's playoff bracket, when it has rounds | 30 min |
| `history:results:` | `services/season_results.py` | a team-season of results, when non-empty and every schedule call answered | 6 h for this year's key and last year's / 7 d older |

These are the TTL'd caches of upstream-derived **read** data behind the
endpoints the frontend polls. The list is explicit on purpose. None of
them stores a fetch that failed outright; what each does with an empty or
partial answer is in the *Holds* column and under *An upstream outage is
not an empty answer* below.

- **A forecast past Open-Meteo's window is not asked for** — with or
  without Redis. A `None` from `weather.fetch` is never cached, and
  Open-Meteo answers a `start_date` more than 15 days after today's UTC
  date with HTTP 400 every time, so a calendar repeated that request for
  each far-off outdoor fixture on every refresh. `fetch` now returns
  `None` for such a `target_date` without a request
  (`_FORECAST_DAYS_AHEAD` = 15; the last day inside the window is still
  asked for). Any other out-of-range date still gets its 400 and degrades
  to `None` as before.

**Deliberately not kept in-process** — their behaviour without Redis is
unchanged, every read a miss and every write a no-op:

- `onthisday:` (`services/on_this_day.py`, 24 h) stores whatever its
  season scan assembled, an empty list included — and a scan made while
  ESPN was failing assembles an empty list, which would then be a team's
  anniversaries for the rest of the day. Its upstream cost is absorbed one
  level down: the scan reads `history:results:`, which is kept and only
  ever holds a season that was fetched whole (*"On this day"* above).
- `venuecoords:` (`services/venue_coords.py`) stores miss markers as well
  as hits, and code elsewhere counts on its being absent without Redis: the
  map route never flags a game venue pending, and
  `refresh_game_venue_coords` returns before geocoding anything.
- `tsdbphoto:v2:` (`services/player_photos.py`) stores miss markers.
- `wiki:` (`services/wiki.py`) feeds a background enrichment job, not a
  polled endpoint.
- `standings:lastgood:` (`routes/standings.py`) is a fallback snapshot,
  not a TTL cache.

The store:

- **Bounded at `_LOCAL_MAX_ENTRIES` = 2048, least recently used evicted
  first.** A read and a rewrite both count as use; a rewrite is not a new
  entry. Expiry is checked only on read, so a key nobody asks for again
  (yesterday's forecast, a finished game's odds, a scorers key superseded
  by a new final count) sits there until it is the least recently used —
  the cap is what bounds those. A display following some twenty whole
  leagues holds under 1,000 live entries, so at 2048 it is the dead ones
  that go. Most values are well under 1 KB; the largest, a team-season of
  results, is 25–110 KB.
- **Expiry on read**, against wall time — `time.time` (`cache._now`, a
  module attribute so tests move time instead of sleeping) — which is what
  Redis expires on. Not `time.monotonic`: on macOS and Linux it stands
  still while the machine sleeps, so a laptop opened in the morning was
  served last night's forecast, odds and bracket as live. An entry is live
  while `0 <= age < TTL`; an expired one is dropped by the read that finds
  it, and so is one whose age is negative — the clock was set back after
  the write, its real age cannot be known, and it would otherwise outlive
  its TTL by the size of the step.
- **A TTL of zero or less stores nothing**, as with Redis, which refuses a
  non-positive expiry.
- **Values are held as JSON text and decoded afresh on every read.**
  Callers get exactly the shapes the Redis path returns (a tuple comes back
  a list, a `datetime` a string) and cannot mutate what is stored.
- **The hit/miss counters count it**: a read served from the store is a
  hit, an absent or expired key a miss.
- **`close_cache()` empties it**, and `clear_local_cache()` does only
  that. `tests/conftest.py` clears it before and after every test — with no
  Redis in the suite the store really caches, and a board one test stored
  would otherwise answer the next test's request.
- It is module state in one process: it starts empty after a restart and
  is not shared between workers. The app runs one worker.

### An upstream outage is not an empty answer (503)

`HTTPException(503, "Upstream provider unavailable")`, never cached. On
the client a failed refetch leaves the query's previous data in place, so
the board already on screen stays, and the keeper asks again five minutes
later (the "anything else" row above).

| Route | 503 when | Still 200 |
|---|---|---|
| `GET /scorers/{league_id}` | at least one final game was walked and **every** summary fetch raised, **or** the provider's circuit breaker turned any fetch away unasked | one summary came back and none was turned away — a fetch that failed on its own counts as no goals, and `games_counted` still reports the games walked; a provider that answers "no summary" (`None`); no finals yet; a non-soccer league; an unregistered provider |
| `GET /odds?ids=` | there was at least one game to price and **every** one of them was an upstream fetch that raised | any game served from the cache — a line or a remembered "no line"; any provider answer, "no line" included; no priceable id in the request |
| `GET /bracket/{league_id}` | nothing is cached and the playoff fetch failed (`espn_playoffs.EspnPlayoffsError`) | ESPN answered and listed no series — empty `rounds`, the off-season state; a non-ESPN league or a sport without series |
| `GET /leaders/{league_id}` | the ESPN athlete-stats feed failed (`espn_leaders.EspnLeadersError`) **and** the followed rosters hold no stats to build the roster board from | the feed answered with nothing; the feed failed but the roster board has rows |
| `GET /schedule/weather?ids=` | **no** forecast was served and at least one Open-Meteo request failed (`weather.WeatherUnavailable`: a transport error, a timeout, a 429, a 5xx, a redirect or a body that is not JSON) — a walk given up after 3 such failures with nothing served included | any forecast served, cached or fresh — the games whose request failed are then simply absent; Open-Meteo answering 400 (a day outside its window; any 4xx but 429) or a 200 with nothing usable, which are "no forecast"; nothing to ask — no id, weather disabled, no outdoor scheduled game with coordinates inside the 15-day window |

- **A cached value is always served first**, outage or not: each route
  reads its cache before it asks upstream. One exception:
  `/schedule/weather` walks its games in the order asked and gives the
  walk up after 3 failed requests with nothing served (below), so a cached
  forecast further down the list is not reached and the answer is 503. On
  a refetch that is the better outcome: the client keeps every glyph it
  holds, where a 200 carrying that one forecast would drop the rest.
- **`/schedule/weather` is the one of the five in front of Open-Meteo.** A
  `weather:` entry lives 45 minutes and the keeper's row is 60, so every
  hourly refresh of a calendar is a cold walk. One that landed in an
  Open-Meteo outage answered `200 {}` and took every forecast glyph off
  the calendar until the next refresh, an hour later; before the keeper a
  loaded forecast was only asked for again on a remount, a reconnect, an
  invalidate or a manual Refresh. `weather.fetch_or_raise` raises
  `WeatherUnavailable` for a request that could not be made or was not
  answered, and `game_detail.fetch_weather(…, strict=True)` passes that
  on; only this route asks for it. `weather.fetch` wraps the same lookup and still never
  raises, so the map, `/games/{id}` and `/matchup` are unchanged.
- **The weather walk is bounded, because a 503 is asked for again
  sooner.** The empty 200 was re-asked after an hour; a 503 is re-asked
  after 5 minutes, twice (the app's `retry: 1`). The walk is sequential,
  up to 80 games, and nothing like the ESPN circuit breaker stands in
  front of Open-Meteo, so a walk that only fails stops after
  `_GIVE_UP_AFTER_FAILURES` = 3 requests. Once one forecast has been
  served it runs to the end, as it always did.
- **One failed forecast is not a 503**, only "nothing served". A venue
  whose request kept failing would otherwise leave every new list it is on
  without forecasts.
- **`espn_playoffs.fetch_playoff_bracket` and
  `espn_leaders.fetch_league_leaders` now raise a typed error** on a
  network, status or decode failure. `[]` is kept for "ESPN answered and
  there is nothing" (or an unsupported sport), so the empty list is no
  longer overloaded. For the bracket that includes a failed month bucket
  of the ranged-dates fallback (*October 2026*).
- **An empty bracket is still not cached**, so the first series of a
  postseason shows on the next request rather than a TTL later. A partly
  failed scorers walk *is* cached like any other board, for its 30
  minutes, exactly as it was with Redis — unless the breaker cut it short,
  which is a 503.
- **The first fetch of the `/scorers` and `/odds` fan-outs goes alone**
  (for `/odds`: the first real fetch per provider; cache hits pass
  straight through), and the rest follow in parallel. After an outage the
  provider's breaker is half-open and admits exactly one call. Fired
  together, that one succeeded and every other was turned away
  (`ProviderUnavailable`) before it could close the breaker: `/scorers`
  answered 200 with a single match's goals under the full match count and
  cached it for 30 minutes, and `/odds` priced one game of the batch.
  Alone, the first fetch closes the breaker and the rest go through. The
  keeper's 5-minute retry after a 503 is, on a quiet day, exactly that
  first request after the breaker's 60 s cooldown.
- **A walk the breaker cut short is a 503, not a board.** Five failures in
  a row open it mid-walk and every later fetch is turned away unasked;
  whatever got in before that is not the competition. `/odds` keeps its
  rule — a turned-away fetch is a failed fetch, and failures are never
  cached.
- **A denied line is a failure, not "no line".** ESPN's `get_game_odds`
  re-raises a `ProviderAccessError` (401/403) on its `/summary` half, as
  it already did a `TransientProviderError`; the predictor half's 404
  stays the ordinary "no projection". (`get_game_summary` returning `None`
  for another 4xx is still its "no summary" answer and still counts as no
  goals.)
- **`/leaders` was changed too.** A whole-followed league has no followed
  rosters, so a failed feed fell through to an empty roster board — "no
  player stats yet" over the leaders on screen.
- **`/matchup` and `/games/{id}` are deliberately unchanged** and still
  degrade to 200 with sections missing: their views replace loaded content
  with an error panel when a refetch fails, so a 503 there would blank
  more than it saved.
- The 503 is raised, not declared on the route, so the schema in
  `tests/openapi_snapshot.json` is unchanged. One line of the snapshot
  moved: the description of `/schedule/weather`, which is that route's
  docstring and said "Never fails the request".

### Known limits

- **The Calendar still shows the month it was mounted in.** Its date range
  is view state, set when the tab is first opened; the keeper refreshes the
  games *inside* that range every 5 minutes but nothing moves the range. A
  display left on across a month boundary shows last month, fully up to
  date, until someone navigates or reloads.
- **A view behind another app tab keeps refreshing.** Views mount on first
  visit and are then only hidden, with their observers alive, so their
  rows stay at policy age — which is why a kiosk rotation shows each tab
  current as it comes round. The cost is bounded by the max ages, the
  one-upstream-row-per-tick budget and the server-side cache, not by
  visibility. A tab that was never visited has no observers and costs
  nothing.
- **Matchup and the game-detail modal blank on a failed refresh.** Both
  replace loaded content with an error panel on `isError`, which is why
  their endpoints keep the 200-with-gaps behaviour. The panel stays until
  the next successful load: the keeper asks again after a minute when the
  backend was unreachable and after 5 minutes on any other error, a proxy
  timeout (504) included.
- **Matchup and an open box score lose their ESPN sections in an ESPN
  outage.** That is the other side of the 200-with-gaps choice, and new
  with the keeper for the preview (a mounted one was never refetched
  before) and for a box score opened before kickoff (a live one already
  polled itself). With the backend up and ESPN failing, the next refresh
  (5 minutes for a hot game or an unfinished box score, 30 otherwise)
  answers 200 with `odds` null and the box score's `summary` null, and
  that replaces the loaded payload: those sections hide themselves until a
  refresh after ESPN returns. The lineup comes from stored rosters and
  stays.
  **The cross-season record does not hide; it shrinks.**
  `head_to_head.build_record` tallies whichever of its five
  `history:results:` seasons are still cached and counts a season that
  could not be fetched as no meetings. Once this year's key and last
  year's have expired (6 h after they were filled; the three older keys
  live 7 d) the refresh returns a smaller W-D-L and a shorter meetings
  list, still headed "last 5 seasons" (`seasons` is always
  `SEASONS_BACK`). It is `null` only when no cached season holds a
  meeting. The full record returns on the first refresh after ESPN does.
  The under-count is older than the keeper: on a Redis install a preview
  opened during an outage always got it. What the keeper and the
  in-process store add is reach — a Redis-less install now holds the mixed
  cache too, and a mounted preview swaps a loaded record for the smaller
  one unprompted.
- **Odds chips and the upset radar drop when the list of games changes
  during an ESPN outage** (*Key defaults for the id-list queries*): the
  new list's first request answers 503 and there is no data of its own to
  keep. They return on the first request that succeeds. The calendar's
  forecast glyphs do the same in an Open-Meteo outage, once the new list's
  forecasts are no longer in the backend's cache.
- **A calendar forecast can go missing for an hour when Open-Meteo fails
  for only some games.** A refresh that serves at least one forecast
  answers 200, and the games whose request failed (a 429 partway through
  the walk, say) are absent from it: their glyphs are gone until the next
  hourly refresh. Only a refresh that served nothing is a 503.
- **A stopped API under `bun run dev` recovers in 5 minutes, not one**,
  and a `null` row that never loaded is not retried there at all (the
  splash's Retry button still works): the Vite proxy answers a refused
  connection with HTTP 500, which is not "unreachable" (*When a query is
  due*). The Docker stack and the desktop app get the one-minute retry.
- **In the desktop app a backend crash is paced as unreachable.** An
  unhandled exception's 500 has no CORS header, reaches the keeper as a
  rejection with no status, and is asked again after 1 minute, not 5 —
  still at most one upstream row per tick (*When a query is due*). A
  catch-all inside `CORSMiddleware` would give it a status; not done here.
- **A season asked for by number is an archive to the keeper**, even while
  that season is still in play; only the `"current"` standings and the
  near-window results refresh.
- **A degraded answer that was cached is held for its TTL** — now on
  redis-less installs too, as it always was with Redis: a partly failed
  scorers walk for 30 minutes. Restarting the app empties the in-process
  store. With Redis only, an "on this day" list assembled while ESPN was
  failing also stays (possibly empty) for 24 hours; that key is not kept
  in-process, for that reason. A team-season is no longer among these: one
  whose schedule calls did not all answer is returned but not cached.
- **No test renders a component.** The keeper is driven against a real
  `QueryClient` and `QueryObserver` under fake timers — the objects
  `useQuery` drives, minus the DOM — and has not yet been watched keeping a
  real browser current for days (see the root ROADMAP.md).
- `App.tsx`'s unfiltered `invalidateQueries()` after a refresh still
  refetches active archives; the keeper neither causes nor prevents it.

### Tests

`frontend/src/lib/freshness.test.ts`, in three layers.
*Pure policy*: archives, static data and a final box score never refresh;
current standings at 5 minutes, and any standings key the rule does not
recognise refreshes rather than freezes; an unlisted root gets the default
age and the `upstream` class; a matchup is paced by its game and a box
score refreshes until final; `isHot`'s window edges; `isDue` for young,
aged, in-flight, paused and never-attempted queries, for each error class
(unreachable at a minute — no status, or a 502 in plain text, as HTML or
with no body, and an HTML page with a brace further in; 503, 500, 504 and
the backend's own JSON 502, with or without whitespace ahead of it, alike
at five),
for a failed
5-minute row and for a failed first load; a never-refreshed row that never
loaded is retried when the backend was unreachable and only then — not
after an answered error (the backend's 502 on `setup-teams` among them),
not once it has data, not without a recorded
failure; a timestamp ahead of the clock is ignored, the data stamp and the
error stamp each on its own, and the two rules together — a never-loaded
`null` row whose only stamp is ahead of the clock is retried at once,
while a `null` row with data stays never due under a future data stamp;
`selectDue`
returning every due db row but only the most overdue upstream one, draining
a backlog in order, ranking a failed row by its retry time, ranking a row
of unknown age behind one measurably overdue and honouring a
budget of zero.
*Source guards* (`?raw` imports): `main.tsx` imports and starts the keeper;
every `hooks.ts` root has a row and every row a root; the standings key
shape; the id-list hooks pass no `placeholderData`.
*The keeper*, on a real client with `main.tsx`'s defaults: without it,
standings on screen for four days load once (the bug, kept as a control);
with it they refresh every 5 minutes while archives stay at one fetch;
nothing while the browser tab is hidden or the network is down, then one
fetch on return; a caller's interval never stacks with the keeper's; a
parked query is never fetched until a live observer watches its key, and
neither it nor a static query holds the upstream slot; an unobserved query
is left alone; a refresh in flight is not restarted; a failed hourly row is
re-asked at the pace its error calls for and recovers within minutes of the
backend's return; a preview whose half-hour refresh the proxy timed out is
off its error panel 5 minutes later, in four requests; a row that timed
out against a modelled server (a 25 s walk behind a 20 s proxy, cached for
30 minutes) is answered from that cache on the keeper's next ask, with no
third cold walk; `setup-status` failing as unreachable is re-asked until
the API is up and then left at that for 24 hours; a `setup-teams` query
the backend answered 502 for is asked twice (the load and the app's
retry) and never again; after the clock is set
back two hours a 5-minute row is asked on the next tick and every 5
minutes from then, and a row that fails from then on is paced by its
error, not asked every tick; upstream rows due together start one tick
apart, and a backlog drains one per tick when the tab returns; a custom
tick interval; `stop()`; and, for both id-list roots, that a changed list
blanks the data without the keeper and keeps the previous list's with it
while the new one loads — for those two roots only — and that a changed
list whose first request fails keeps nothing (the known limit, pinned).
Fixture names are fictional: `test-league`, `test-cup`, `test:t2`.

`tests/test_cache_contract.py`: a value served until its TTL runs out and
dropped by the read that finds it expired; the clock is `time.time`, and
an entry written before the clock was set back is dropped; a zero TTL
stores nothing; only
the allowlisted prefixes are stored (one key per cache, spelled as its
caller builds it, against the five Redis-only ones, `onthisday:` among
them); eviction of the least
recently used entry at the cap; JSON copies nobody can mutate; the
counters; `close_cache()` emptying the store; and a configured Redis —
down, or with a client that cannot be built — never touching it.
`tests/test_weather.py`: a repeat request is served in-process without
Redis, a different day at the same venue is a different forecast, and a
day past Open-Meteo's 15-day window is not asked for at all (the window's
last day still is). `tests/test_on_this_day.py`: without Redis an outage's
empty list is not remembered — the next request after ESPN returns shows
the anniversary — while the season that came back with games is reused by
the next scan and by `/history/results`.
`tests/test_history.py`: a season with a failed schedule call raises
`EspnHistoryError` carrying the other call's finals, is returned but not
cached, and is fetched whole and cached once ESPN answers; last year's
season key gets the 6 h TTL.
`tests/test_head_to_head.py`: the known limit above, pinned on the real
season cache with its clock moved by hand — a record of 2-1-2 over five
seasons is 0-1-2 from the three older seasons once ESPN is failing and
the two 6 h keys have run out, still reporting `seasons` as 5, and is
`None` only after the 7 d keys have gone too.
`tests/test_routes_misc.py`: `/scorers` is 503 only when every summary
failed, partial success is 200, and the failed walk left nothing cached;
no finals yet and a provider answering "no summary" are both 200; behind
the real circuit-breaker guard, the first `/scorers` walk and the first
`/odds` batch after a cooldown come back whole, and a walk the breaker
cut short is 503 and not cached;
`/bracket` is 503 on a failed fetch, 200 and uncached when ESPN lists no
series; `/leaders` is 503 only with no stored stats to show; and each of
the three serves its cached board through an outage without asking
upstream. `tests/test_api.py`: the same pair for `/odds` — 503 only when
every fetch failed, never cached, and cached lines and cached "no line"
answers both carrying a batch through an outage; an unregistered provider
and a request with nothing priceable are 200; one fetch goes ahead alone
and the rest overlap. For `/schedule/weather`, against the real weather
service with only Open-Meteo's transport scripted: forecasts loaded, still
served 44 minutes later with Open-Meteo failing, and a 503 at 60 — for a
refused connection, a timeout, a 500, a 503, a 429, a redirect and a 200
that is not JSON — with nothing cached from the failure; a walk that only fails stops
after three requests; two answers followed by failures, and a cached
forecast at the head of the walk, are 200 with every other game still
asked; a cached forecast behind three failures is 503 (the exception
above, pinned); Open-Meteo's 400 for every game is `200 {}`, and so is a
request with nothing to ask, which sends none; and `/games/{id}` stays
200 with `weather` null through the same outage.
`test_an_unhandled_error_is_answered_without_cors_headers`, on the real
application object: a route that crashes is answered 500 without
`Access-Control-Allow-Origin`, an unknown route 404 with it.
`tests/test_espn.py`: `get_game_odds` re-raises an
access denial on the summary half.
`tests/test_espn_playoffs.py`, `tests/test_espn_leaders.py`: a failed
fetch raises the typed error and an empty answer is still `[]`
(`test_fetch_bracket_is_empty_when_a_month_bucket_fails` became
`…_raises_when_a_month_bucket_fails`, and
`test_fetch_bracket_only_a_400_triggers_the_fallback` now expects the
error instead of `[]`). `test_no_redis_url_means_noop` became
`…_off_the_allowlist`.
