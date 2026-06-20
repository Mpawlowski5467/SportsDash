# SportsDash — Expansion Roadmap

Planned 2026-06-12, grounded in live probes of every data source named
below (see "Research findings" notes inline). Nothing here is
implemented yet; phases are ordered by dependency and risk, not by
calendar promises. The contracts doc remains binding — each phase lands
with its own CONTRACTS.md additions, mock-provider parity (demo mode
must demo every sport), and tests.

> **Update (mock provider removed):** SportsDash is now live-data only — the
> mock provider and demo-mode install have since been removed. References
> below to "mock-provider parity" and "demo mode" are historical roadmap
> context, not current requirements.

## Phase 0 — Hotfixes surfaced by the research (do first, tiny)

- **Soccer fixtures bug (affects the app TODAY):** ESPN splits soccer
  team schedules — the default endpoint returns *completed* games only;
  upcoming fixtures require a second call with `?fixture=true`. Our
  adapter makes one bare call, so a followed soccer club currently never
  sees upcoming games. Fix: two calls + merge, for soccer only.
- **Ranged scoreboard calls need `limit=400`** — default silently caps
  at 100 events.
- **`StandingRow.group`** (conference/division/table name): NBA/MLB/NHL
  standings interleave conferences when flattened today. Additive
  nullable field + grouped rendering in StandingsView.

## Phase 1 — News that actually works (zero-config)

Verified: ESPN `/news?team={id}` works for every league probed
(including national teams); articles carry headline, description,
published, `links.web.href`, `images[]`, and team/league category tags.
Google News RSS is keyless, feedparser-compatible (probed: bozo=false,
100 items, real source attribution), and locale-aware
(`hl=pl&gl=PL&ceid=PL:pl`).

- Extend `SportsProvider` with optional `get_news(league, team)`;
  ESPN adapter implements it; merge with the existing RSS path in the
  news job (provider news + RSS + auto-generated Google News query per
  team). Dedupe stays URL/id-based.
- `NewsItem.image_url` (additive) + thumbnails in NewsView.
- `SPORTSDASH_NEWS_LOCALE` setting feeding the Google News query
  (`pl-PL` gives Polish-language coverage of any followed team).
- Risks: ESPN's team filter is tag-based (league roundups leak in);
  Google links are redirect URLs (display `source`, not hostname).

## Phase 2 — NHL + NFL (and college, cheaply)

Both verified end-to-end on the same API surface the adapter already
speaks; this is mostly enum + normalization + catalog work.

- `Sport.HOCKEY`, `Sport.FOOTBALL`. Catalog: NHL, NFL (college
  football/basketball confirmed available — add behind the same
  pattern when wanted).
- Hockey normalization: P1–P3, `period>regulation` → OT (`2OT` etc. in
  playoffs), period 5 → SO in regular season; **prefer
  `status.type.detail/altDetail` (`Final/OT`, `Final/SO`) over raw
  period**. Known quirks to handle: shootout winner's score jumps +1 at
  the final, schedule endpoint needs `?seasontype=2` *and* `3` (defaults
  to current phase only), schedule scores arrive as objects while
  scoreboard scores are strings, roster `status.type` stays `active`
  while `injuries[]` says Out (derive from injuries first).
- NHL standings: `wins/losses/otLosses/points` (+ new
  `StandingRow.ot_losses`); render `W-L-OTL · PTS`.
- NFL: quarters map like basketball (period 5 → OT); offseason
  scoreboard without `?dates` returns *next season's week 1* — always
  pass the ET date; full upcoming season schedule is already available.
- Mock engines for both sports (hockey: 3×20min + OT/SO; football:
  4×15min) so demo mode stays complete.
- Open item: NHL live-intermission shape is unverified (no live game in
  the probe window). **Stanley Cup Final Game 6 is June 15 ~00:00 UTC —
  re-probe live then** before finalizing `is_intermission` mapping;
  defensively map `state=in` + `END_PERIOD`/clock `0:00` until then.

## Phase 3 — National teams, more soccer, whole-competition mode

Verified: 17 additional ESPN soccer codes work (World Cup **live in the
feed right now**, Euro, Nations League, Copa América, Club World Cup,
Europa/Conference, eng.2, sco.1, ned.1, por.1, tur.1, mex.1, ger.2,
esp.2, fra.2). National-team ids are global across competitions
(France=478 everywhere); `uefa.nations/teams` is the best browse
directory (54 nations); `fifa.friendly` exists for friendlies.

- Catalog: add the 17 codes, with `uefa.nations` powering a "National
  teams" picker section in onboarding.
- **Team-across-competitions**: a followed national team should surface
  its games from World Cup + qualifiers + Nations League + friendlies.
  Design: keep one followed Team row, add a league-membership list
  (Team.league_id becomes "primary"; scheduler fans schedule fetches
  across the membership set). This is THE model change of the phase.
- **Follow whole competitions** (`follow_all` on a league): sync the
  full fixture list via the verified mechanism — tournament leagues
  expose `calendar` stage windows + season span; one ranged scoreboard
  call (`dates=YYYYMMDD-YYYYMMDD&limit=400`) returns every fixture (all
  104 WC matches in one call). Domestic leagues: chunk by month.
  Today/Calendar group competition games under a league header rather
  than per-team colors. Knockout TBDs re-sync after each stage; gate on
  `season.year` (off-cycle tournaments serve the previous edition).
- **Notification preferences become mandatory here** (a followed World
  Cup = 104 games of push spam otherwise): per-follow event-type
  toggles + per-follow mute. Settings table + small UI panel off the
  gear menu; the wizard sets sane defaults (followed teams: everything;
  whole competitions: start/final only).
- Ekstraklasa: **not on ESPN** (verified against their full 244-league
  catalog). Tracked under Phase 6 sources instead — do not fake it.

## Phase 4 — Tennis + UFC (athlete following, still the Game model)

Both verified to fit the existing 1-v-1 Game shape; the new concept is
following *athletes* instead of teams.

- New `athletes` follow model (id, sport, league, name, country flag,
  headshot) + onboarding step + an "Athletes" section in the picker.
- Tennis (`tennis/atp`, `tennis/wta`): matches carry homeAway and 2
  competitors; sets = periods ("Set 2"); no clock (baseball-style).
  Adapter must unwrap the `events→groupings→competitions` nesting,
  filter the whole-draw payload (entire tournament, 400–700KB) down to
  the requested day, dedupe joint ATP/WTA tournaments by
  event+competition id, and discover a followed athlete's matches by
  scanning the draw (the `/overview nextGame` endpoint is unreliable —
  verified empty even with a confirmed next match). Rankings endpoint
  (top 150, points) maps onto Standings for a per-tour table. Game gains
  an additive `series` field ("Wimbledon · QF") for context labels.
- UFC (`mma/ufc`): month-granularity schedule (`?dates=YYYYMM`); each
  card = an event with 7–12 fights, exactly 2 competitors each (no
  homeAway — map order 1/2), rounds = periods. **No per-fight start
  times** — poll the whole card as a unit while live, and notify
  per-fight transitions. Best athlete support of all probed sports:
  `/overview upcomingFight` carries the exact card + bout ids. Method
  of victory needs one core-API call per finished fight (enrich only
  followed fighters; rewrite `$ref` http→https; parse extra
  defensively).

## Phase 5 — Golf + the Event/leaderboard model

Golf breaks home/away: a tournament is one event with a 147-player
leaderboard. This phase introduces the second first-class domain
object, designed so future leaderboard sports (motorsport, athletics,
cycling) reuse it:

- `Event` domain model + table: id, league_id, name, start/end, phase,
  current round/session label, `leaderboard` JSON
  (pos/athlete/score/detail rows), fetched_at. `EventOut` + an Events
  panel on Today (active events) and Calendar (event spans).
- Golf specifics (verified): leaderboard order is pre-sorted (ties not
  flagged), score-to-par is a string ("-10", "E"), rounds live in
  per-competitor linescores, "thru" derivable from per-hole linescores;
  tournament schedule endpoint gives the season calendar (majors
  flagged). Golfer following: scan the current event's competitor list
  (future commitments are NOT exposed — verified; set expectations:
  "playing this week" not "next start").
- Event notifications: round start, followed golfer finishes round /
  takes the lead, final result.

## Phase 6 — Volleyball (eyes open: the data is the project)

Probed honestly: ESPN has none. TheSportsDB free tier has only 5
European volleyball competitions — **no PlusLiga, no VNL, no
SuperLega** — with sparse fixtures (CEV Men's European Championship has
Poland's Sept 2026 fixtures, scores as set counts, no live data on the
free tier).

- Start with what exists: a TheSportsDB provider (second real provider —
  finally exercising the multi-provider design) covering the CEV
  European Championship + national teams it does carry, finals-only
  (poll past-events; no live states). Plus Phase 1 news (Google News
  `hl=pl` covers PlusLiga clubs well) so the teams are at least
  *followable* for news + manual calendar.
- Then evaluate: TheSportsDB premium tier coverage, or a custom
  PlusLiga adapter against plusliga.pl's undocumented JSON (brittle;
  scraping maintenance burden — decide deliberately, not by default).

## Phase 7 — Platform polish (continuous, slot in anywhere)

- Game detail drill-down (box scores / linescores / scorers via the
  summary endpoints — data verified present).
- Kiosk: auto-rotating tabs, idle clock screen.
- Calendar: per-team `.ics` feeds + `webcal://` subscribe URLs.
- Results: streaks / last-10 chips from the accumulating archive.
- PWA manifest for phone home-screen install.
- Ops: pg_dump backup cron in compose, `/api/health` deep-check
  (provider reachability), Prometheus-style metrics endpoint if the
  homelab wants it.

## Cross-cutting rules for every phase

- Mock provider gains an engine per new sport/event type — demo mode
  always demos everything.
- Sport enum additions ripple: StandingsView columns, StatusBadge
  labels, ICS durations, mock engines — grep them all per phase.
- Fictional names in all fixtures/demo data, real names only in catalog
  + user data (established rule).
- Each phase ends with the standard gauntlet: pytest, live smoke,
  tsc/build, compose e2e, and a review pass.
