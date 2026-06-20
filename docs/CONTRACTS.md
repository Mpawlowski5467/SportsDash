# SportsDash — Module Contracts

> **Update (mock provider removed):** SportsDash is now live-data only. The
> mock provider (`backend/app/providers/mock.py`), the demo install
> (`POST /api/setup/demo`, `seed.DEMO_CONFIG` / `install_demo`), and the
> onboarding "Try the demo" path have been removed — all data comes from the
> ESPN and TheSportsDB providers. The "Mock provider" and "demo mode" sections
> below are retained as historical design context and no longer reflect the
> shipping code.

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
- **Fictional names only** in any sample config, mock data, or test
  fixture — never real teams or leagues.
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
async def close_all() -> None                                  # close every registered provider
```
Registers `espn` and `mock` at import time.

### `app/providers/espn.py` (owner: providers agent)

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

### `app/providers/mock.py` (owner: providers agent)

`class MockProvider`, `provider_id = "mock"`. Deterministic fictional data
so a fresh install demos every feature: per team — a final yesterday, one
game today already in progress (started ~40 min ago, mid-game
period/score), one tonight (~+4h), and a regular fixture grid every 2–3
days for ±3 weeks (past ones final with plausible scores). The grid is a
pure function of absolute days since a fixed epoch — never of "today" —
and game ids encode league/day/pairing slots (`mock:{league}-d{N}-g{i}`),
not team slugs, so fixtures and ids are stable across midnights, restarts,
and registration order. Only the live/tonight anchor times live in process
memory (restart re-anchors the live demo game; accepted). Standings with 8
fictional teams per league (followed teams present with `team_id`,
sport-appropriate columns), rosters of ~12 fictional players with 1
`injured` and 1 `day_to_day` (with `status_detail`). Same inputs → same
output (seed any RNG with a stable md5-derived hash, never builtin
`hash()`).

### `app/services/repository.py` (owner: persistence agent)

All functions take `session: AsyncSession` first; **no commits inside** —
callers commit (`session_scope()` commits on success; route handlers that
write must commit explicitly).

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
    # scheduled/in_progress state. All provider strings are clipped to
    # their column widths on write. Returns number of rows touched.
async def prune_stale_games(session, now_utc: datetime) -> int
    # delete ghost rows: 'scheduled' with start older than now-3d,
    # 'in_progress' older than now-2d (daily refresh would have healed
    # them if any provider still knew the fixture). Run by daily_refresh.
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
async def save_standings(session, standings: domain.Standings) -> None
async def get_standings(session, league_id: str) -> StandingsORM | None
async def replace_roster(session, roster: domain.Roster) -> None
    # delete + reinsert players for the team; set teams.roster_updated_at
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
POST `{settings.ntfy_url}/{settings.ntfy_topic}`, body = message, headers
`Title`, optional `Tags`/`Priority`, `Authorization: Bearer` when
`ntfy_token` set. Returns False (never raises) on failure. When
`notifications_enabled` is False, skip sending and return True (treat as
handled).

### `app/services/news.py` (owner: persistence agent)

```python
async def fetch_team_news(team: TeamORM) -> list[domain.NewsItem]   # parse all team.rss_feeds via feedparser in asyncio.to_thread, errors → log + skip feed
async def refresh_all_news() -> int                                  # own session_scope; returns new-item count
```
NewsItem id = sha1 hex of url (first 16 chars), source = feed title or hostname.

### `app/services/cache.py` (owner: persistence agent)

Thin optional Redis JSON cache; every function is a silent no-op (or None)
when `redis_url` is unset or Redis is down:
```python
async def cache_get_json(key: str) -> Any | None
async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None
async def close_cache() -> None
```

### `app/scheduler/jobs.py` (owner: scheduler agent)

```python
def setup_scheduler() -> AsyncIOScheduler   # configured, NOT started
async def refresh_schedules() -> None       # window: -7d … +45d per team
async def refresh_standings() -> None
async def refresh_rosters() -> None
async def refresh_news() -> None            # delegates to services.news.refresh_all_news
async def live_tick() -> None
async def daily_refresh() -> None           # schedules + standings + rosters (+ news)
```
Jobs use `session_scope()`, resolve providers via
`registry.get_provider(league.provider)`, and must catch/log per-league
and per-team errors so one bad source never kills a whole job. Schedule:
`daily_refresh` daily at `settings.daily_refresh_hour` (in
`settings.tzinfo`); news every `news_refresh_minutes`; `live_tick` every
`live_poll_seconds` (`max_instances=1`, `coalesce=True`).

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
   `send_event` → on success `mark_notified`.

### `app/seed.py` (owner: scheduler agent)

```python
async def seed_from_config(path: str | None = None) -> None   # parse YAML, upsert leagues+teams via repository
```
YAML shape (`backend/config/teams.yaml`, fictional sample data, provider
`mock` by default so a fresh install works instantly):
```yaml
leagues:
  - id: pinnacle-basketball
    sport: basketball
    name: Pinnacle Basketball League
    provider: mock
    provider_key: mock-basketball
teams:
  - id: ashport-comets
    league_id: pinnacle-basketball
    name: Ashport Comets
    abbreviation: ASH
    provider_key: ashport-comets
    color: "#f59e0b"
    rss_feeds: []
```
Sample contains 3 leagues (basketball/baseball/soccer) and 4 followed teams
(2 in the soccer league).

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
`games_to_ics(rows, leagues_by_id) -> str` (hand-rolled VCALENDAR, UTC
`DTSTART`, escaped text).

Route table (all under `/api`, response models from `schemas.py`):

| Route | Response | Notes |
|---|---|---|
| `GET /health` | `{"status": "ok"}` | |
| `GET /meta` | `MetaOut` | version "1.0.0" |
| `GET /teams` | `TeamsOut` | |
| `GET /today` | `TodayOut` | local day via `local_day_bounds(local_today(tz), tz)`; sorted by start_time |
| `GET /schedule?start=YYYY-MM-DD&end=YYYY-MM-DD&team_id=` | `list[GameOut]` | dates are local days, inclusive; defaults: start = today−7d, end = today+45d |
| `GET /schedule/{team_id}?start=&end=` | `list[GameOut]` | same semantics, path team |
| `GET /standings/{league_id}` | `StandingsOut` | 404 unknown league; empty rows + `fetched_at: null` if not yet fetched |
| `GET /roster/{team_id}` | `RosterOut` | 404 unknown team |
| `GET /results/{team_id}?limit=25` | `list[GameOut]` | finals, newest first |
| `GET /news?team_id=&limit=50` | `list[NewsItemOut]` | |
| `GET /calendar.ics` | `text/calendar` | all games −30d…+60d, `Content-Disposition: attachment; filename=sportsdash.ics` |

404s raise `HTTPException(404, "...")`. Unknown query params ignored.

## Frontend contracts

Stack: React 19 + TypeScript ~5.8, Vite ^6, Tailwind **v4** via
`@tailwindcss/vite` (CSS-first config — `@import "tailwindcss";` in
`src/index.css`, no tailwind.config), TanStack Query ^5, FullCalendar ^6.1.
Dark-mode-first kiosk styling: near-black background (`bg-zinc-950`),
zinc-100 text, dense layout, team colors as accents. No router — tab
switching via `useState` in `App.tsx`.

### File ownership

- **scaffold agent**: `package.json`, `tsconfig.json`, `vite.config.ts`
  (react + tailwindcss plugins; dev server proxies `/api` →
  `http://localhost:8000`), `index.html` (dark `<html class="dark">`, title
  SportsDash), `src/main.tsx` (QueryClientProvider; QueryClient defaults:
  `staleTime: 30_000`, `refetchOnWindowFocus: false`, `retry: 1`),
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

### ESPN adapter (espn.py owner)

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
  when absent.

## Phase 2: NHL + NFL (per ROADMAP.md)

Foundation already landed (do not redo): `Sport.HOCKEY`/`Sport.FOOTBALL`,
`StandingRow.ot_losses` through domain/schemas/types/repository,
`types.ts` Sport union. All payload facts below were verified against
real ESPN traffic (see ROADMAP Phase 2).

### ESPN adapter (espn.py + espn_catalog.py owner)

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
    {n}th") → is_intermission=True — defensive, live shape re-probe due
    June 15 (SCF game 6).
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

**espn.py (adapter owner):**
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
the route/seed after `replace_followed`.

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

### espn.py (adapter owner)
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
in replace_followed wipe), `serialize.event_to_out`/`events_to_out`,
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

### espn.py (adapter owner)
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
  for `active_events` (in-progress golf), call `get_event_state`,
  re-tag followed golfers, `upsert_events`. Notifications (modest, via
  notify + prefs): tournament FINAL with the followed golfer's finishing
  position. Use a dedupe key `"{event_id}:final"`. Round-by-round is out
  of scope this phase. Register events_tick in setup_scheduler.

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
  background events — nice-to-have; at minimum don't break.
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
Pure parser functions over fetched JSON (like espn.py), lazy httpx
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
  leave unset). Add `[data-theme="light"]`, `[data-theme="newsprint"]`,
  `[data-theme="stadium"]` each overriding the ramp the app actually uses:
  `--color-zinc-950/900/800/700/600/500/400/300/200/100` (background→text
  scale) plus accent `--color-amber-300/400/500` and status `--color-red-*`,
  `--color-emerald-*`. Set `color-scheme` per theme (light vs dark) so
  scrollbars/form controls follow. Pick tasteful, WCAG-AA-contrast ramps:
  - light: near-white bgs (zinc-950→#f8fafc … zinc-900→#f1f5f9), dark text
    (zinc-100→#18181b … zinc-400→#52525b), amber accent kept readable.
  - newsprint: warm paper (cream/ivory bg ramp), ink-brown text, a muted
    crimson/maroon accent; optionally a serif `--font-sans` override for
    headings via a `.theme-newsprint` heading rule.
  - stadium: dark base (keep dark ramp) but accent driven by a
    `--sd-accent` var — remap `--color-amber-400`(and 300/500) to
    `var(--sd-accent, <default>)`. A small effect sets `--sd-accent` from
    the team being viewed (see below); default to a vibrant amber.
- `src/calendar.css`: change the hardcoded `--fc-*` hex values to reference
  the palette vars (e.g. `--fc-page-bg-color: var(--color-zinc-950)`), so
  FullCalendar re-themes with the rest.
- Theme switcher: persisted in localStorage `sportsdash.theme` (values
  `dark|light|newsprint|stadium`, default `dark`). Apply `data-theme` to
  `document.documentElement` as early as possible (an inline script in
  `index.html` head, or first thing in `main.tsx`) to avoid a flash.
  Expose a switcher control in SettingsView (the gear → Notifications panel
  area gets a "Appearance" section) AND a quick theme button in the header
  next to the gear is optional. Keep `types`/api untouched (pure client
  state).
- Stadium dynamic accent: a tiny `ThemeContext`/effect that, only when
  theme==="stadium", sets `--sd-accent` on `document.documentElement` to
  the color of the team currently in focus (e.g. the selected team in
  Roster/Results/Standings, or the first followed team on Today). Keep it
  simple and safe; default accent when none.

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
  and theme-aware (works in all 4 themes).

## Phase 12: national-team follow model + competition map plotting

Foundation landed: `StadiumORM` (key `"{provider}:{provider_key}"`,
venue+coords+facts+resolved flag — caches stadium resolution decoupled
from TeamORM so competition teams without a team row can be plotted);
`MapTeamOut.league_name` + `source` ("followed"|"competition") + TS.

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
- `repository`: stadium-cache helpers — `get_stadium(session, key)`,
  `upsert_stadium(session, key, team_name, location_fields...)`.
- A competition is ACTIVE if it has at least one game in the DB within a
  near window (e.g. now-2d .. now+21d) — reuse `games_between` on the
  league. (When the World Cup ends, no near games → inactive → its teams
  drop off the map automatically.)
- `GET /api/map` assembles TWO sources:
  1. followed teams with resolved coords (as today) → `source="followed"`.
  2. for each ACTIVE `follow_all` league: its catalog teams
     (`espn_catalog.get_league_teams`), each resolved via the stadium
     cache (StadiumORM by `"{provider}:{provider_key}"`; on miss, resolve
     via the Phase 11 stadium-enrichment/geocode pipeline and cache it —
     bounded so the request doesn't hang; unresolved teams are simply
     omitted and fill in on later calls) → `source="competition"`,
     carrying league_name + the team's logo/color from the catalog.
  Dedupe: a team that is both followed and in an active followed
  competition appears once as "followed".
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
`backend/config/teams.yaml` is comments-only (a template); the fictional
demo config moved to a `DEMO_CONFIG` constant in `seed.py`, installed via
`POST /setup/demo`. First-run UX: frontend checks setup status and shows
a full-screen wizard until onboarded.

Real league/team names ARE allowed here: the catalog and whatever the
user picks are live app data, not sample data. Test fixtures and the
demo config stay fictional.

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
async def replace_followed(session, leagues: list[domain.League], teams: list[domain.Team]) -> None
    # cached sports data is disposable: wipe games, players, news,
    # standings, notifications_sent, teams, leagues; insert the new set
```
Onboarded flag: meta key `"onboarded"` = `"1"`.

### Setup routes (`app/routes/setup.py`, under /api)

| Route | Response | Notes |
|---|---|---|
| `GET /setup/status` | `SetupStatusOut{onboarded, followed_team_count}` | |
| `GET /setup/leagues` | `CatalogLeaguesOut{leagues: [{id,name,sport,provider}]}` | static, no network |
| `GET /setup/teams/{league_id}` | `CatalogTeamsOut{league_id, teams: [CatalogTeamOut]}` | live ESPN fetch; 404 unknown league; 502 on upstream failure |
| `POST /setup/follow` | `TeamsOut` | body `FollowRequest{selections: [{league_id, team_provider_keys}]}`; 400 on empty/unknown keys; `replace_followed` + set onboarded + commit + `kick_daily_refresh()` |
| `POST /setup/demo` | `TeamsOut` | installs DEMO_CONFIG the same way |

Internal ids from a follow: league id = catalog id; team id =
`f"{league_id}-{slugify(team name)}"`. `scheduler/jobs.py` gains
`def kick_daily_refresh() -> None` (create_task + held reference; routes
must not import `app.main` — circular).

### Frontend onboarding

`types.ts`/`api.ts`/`hooks.ts` mirror the five routes
(`useSetupStatus()` etc.; setup mutations via plain `api.*` calls +
`queryClient.invalidateQueries()`). `App.tsx`: splash while status
loads; full-screen `<OnboardingWizard mode="first-run" .../>` when not
onboarded; gear button in `Layout` reopens it as `mode="manage"`.
Wizard (in `src/components/onboarding/`): choose path (pick teams /
demo) → league multi-select grouped by sport → per-league team grid
(logo, name, search filter, multi-select) → review → POST → syncing
screen (poll `useToday` until games or ~20s) → dashboard. Dark kiosk
styling consistent with the rest.

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
  healthy), `frontend` (build ./frontend, ports `3000:3000`,
  `API_URL=http://api:8000`), `ntfy` (binwiederhier/ntfy, `serve`, port
  `8090:80`, cache volume).
- `.env.example` documenting `SPORTSDASH_TIMEZONE`, `SPORTSDASH_NTFY_TOPIC`, etc.
- Root `.gitignore` (+ `.dockerignore`s), `README.md` (architecture, quick
  start, switching a league from `mock` to `espn`, adding a provider).

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
`_team_logo`/`_team_color` helpers added to `espn.py`. Persisted by
`repository._standing_row_to_dict` (so `StandingsORM.rows` JSON still
mirrors `StandingRowOut`). Mock/TheSportsDB leave them null → the
existing abbreviation-chip fallback. Frontend `StandingsView` renders
`row.logo_url ?? meta?.logo_url` (followed-team `/teams` meta as fallback).

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

## Phase 18 — Golden Boot + World Cup nation pages

- `GameSummary.goals` (domain `Goal{player,team,minute,own_goal,penalty}`) +
  `GameSummaryOut.goals`/`types.ts`. espn `_parse_goals` reads soccer summary
  `keyEvents` (scoringPlay + type.type=="goal"; scorer from
  `participants[0].athlete`; own goals flagged). `GET /api/scorers/{league_id}`
  (`ScorersOut`): walks the league's FINAL games, pulls each summary in
  parallel (semaphore 8), tallies goals/player (own goals excluded), ranks,
  caches in Redis (`scorers:{id}:{n}`, 30min TTL). LeadersView shows this
  Golden Boot when present, else the roster-derived board.
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
  `"espn:<id>"`; strip the prefix). Cached in Redis (`leaders:{id}`, 15min).
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
  Redis 30min). `BracketView` gained a bracket `Select` (soccer cups + US
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
    propagates (breaker). New `_CORE_BASE` host (same shared client).
  - Mock: deterministic md5-seeded line + win% (`_mock_game_odds`), fictional
    book name, only for scheduled/in_progress non-individual games.
  - TheSportsDB: None.
- `GET /api/games/{id}` now also returns `odds` (best-effort `_fetch_odds`,
  never 500s). NEW `GET /api/odds?ids=csv` → `dict[game_id, GameOddsOut]`
  (`routes/odds.py`): batch for cards (Today/Calendar/Results), Semaphore(8)
  fan-out, per-game Redis cache `odds:{game_id}` (10 min, negatives cached so
  odds-less games aren't re-fetched every poll), gated to scheduled/in_progress
  non-individual games. Mirrors `/schedule/weather`.
- Frontend: `api.odds(ids)`, `useGameOdds(ids)` (clone of `useScheduleWeather`).
  `GameDetailModal` `OddsSection` = win-probability split bar (away amber /
  home sky, mirrors `TeamStats`) + moneyline/spread/total rows + book
  attribution, shown for scheduled/live games only. `GameCard` optional `odds`
  prop → a favorite chip (`favoriteChip`: favored side + win%, else moneyline);
  TodayView threads the batch map in.
