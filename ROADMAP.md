# Roadmap

SportsDash is a single-user, self-hosted personal project. This is a *living
list of intentions*, not a committed schedule — items move, merge, and get
dropped as priorities shift. Grouped by rough horizon.

See the [README](README.md) for what the app already does today.

Every open item below says what it needs **and how you would know it worked**
— the italic *Verify* line. That line is not decoration: four of the things
that shipped on 2026-07-27 were pinned by tests and had still never been
looked at running, and the map bug fixed that day was a confidently wrong
pin that every gate passed happily. A green suite is not a sighting.

## ✅ Recently shipped

- **Race leaderboards carry constructors and race times** (2026-07-29) —
  taking the deferral in the motorsport item below: the per-driver times
  that live on ESPN's core API only are now fetched, so a finished race
  board reads a constructor, car number and grid slot next to a race time,
  a gap ("+15.080", "+1 lap"), laps completed, DNF, or DSQ. Two tiers — one
  core call per active race every poll, and a bounded, memoized per-car
  fan-out once at FINAL, so re-polling a finished race costs nothing. No
  schema change: it rides the leaderboard row's existing display strings.
  Every core fetch degrades to the plain board on any failure. A second pass
  the same day added two things at **zero** extra calls. **A live board now
  renders a running gap** ("+2.418", "+1 lap") when ESPN's scoreboard payload
  — the one the board is already parsed from — carries one for that car;
  that slot exists on every racing competitor but has only ever been observed
  **empty**, so the column may simply stay empty, and the parse is fail-safe
  so a wrong guess about the populated shape costs that one column and
  nothing else (see *Loose ends*). And **a real bug is fixed**: the
  retirement check was a substring test, so a disqualification rendered as a
  finish — six observed cars, three carrying the full race distance and one a
  53-second finishing gap for a car that completed zero laps. The three
  status types (a third one, DSQ, does exist) are now mapped exactly, DSQ
  renders "DSQ · Disqualified", and a car whose status call fails is either
  inferred DNF from measured F1-only thresholds (zero false positives over
  529 classified cars) or labelled "Status unknown" — never implied to have
  finished, and never inferred outside F1, where a car 8 laps down at a short
  track is still circulating. The payload shapes come from a live probe of
  ESPN's core API, but the enriched board has not been looked at in a running
  browser yet, and **no racing session anywhere has been observed in
  progress**, so the live-gap rendering is structurally verified only.
- **Same-day pre-game roster re-sync** (2026-07-29) — closing the
  same-day-status deferral in the player-follows item below: a followed
  team whose game starts within four hours has its roster re-checked every
  15 minutes (status-only, at most once every 45 minutes per team), so a
  lunchtime scratch alerts the same afternoon instead of waiting for the
  next 5am sweep, and the ruled-out heads-up before kickoff reads
  same-day-fresh designations. One roster request per team per window, not
  a per-player fan-out; it only runs for teams you actually follow a
  player on. Per-player notification scopes and trade-following remain
  deferred. Pinned by tests — including mutation checks on the cooldown,
  the stat-line carry-forward and the cheap-fetch flag — but watching it
  fire on a real same-day designation change is still outstanding.
- **Motorsport** (2026-07-28) — F1, NASCAR Cup, and IndyCar land as the
  tenth sport: race weekends ride the golf leaderboard-`Event` pipeline
  (Calendar span bars, live "Race · Lap 44" labels, driver follows as
  athlete-as-team). Per-driver times/laps live on ESPN's core API only and
  were not fetched at the time — leaderboard rows carried running order
  and the winner. **They are fetched as of 2026-07-29** (see above), and a
  live lap label can now read "Race · Lap 44 of 70".
- **Player follows** (2026-07-28) — star individual players on followed
  rosters: highlighted rows on the Leaders boards, injury-status change
  alerts, and a ruled-out heads-up shortly before a game. Per-player
  notification scopes are still v2; same-day status re-fetch **shipped
  2026-07-29** (see above).
- **Where to watch + highlight clips** (2026-07-28) — game cards show a
  broadcast-network chip, and the box-score modal a Highlights section
  of ESPN clips (team sports only; expired clips are dropped).
- **Prometheus `/api/metrics`** (2026-07-28) — shipped after the earlier
  decline; the how and the verify live in the closed loose end below.
- **On this day** (2026-07-28) — a Today-view card of each followed
  team's past games on today's date, over the last five seasons of the
  archives (`GET /api/history/on-this-day`).
- **Why-watch signal** (2026-07-28) — a frontend-only card flag ("Tight
  late" / "Upset watch" / "Toss-up") computed from odds data the cards
  already fetch.
- **Backup restore drill** (2026-07-28) — `scripts/verify-backup.sh`
  restores the newest dump into a throwaway container and sanity-checks
  the row counts; exercised the same day against a real dump, including
  its failure paths.
- **Golf events on the Calendar** (2026-07-27) — multi-day tournaments now
  draw on the Calendar grid as all-day span bars that open their leaderboard
  on click, and ride along in the `.ics` subscription as all-day entries, so
  a subscribed calendar shows a major as a bar across the weekend rather than
  nothing at all. (Golf only at the time — racing joined the leaderboard
  `Event` model on 2026-07-28, see above; a tennis match is a two-sided
  game, so it was already on the grid as an ordinary fixture.) Driven against live PGA Tour data the same
  day; the remaining unverified leg is a real calendar client, in *Loose
  ends*.
- **Tournament spans use the server's timezone** (2026-07-27) — the grid
  dated a span from the *browser's* zone while the `.ics` feed dated it from
  `SPORTSDASH_TIMEZONE`, so a viewer west of the server saw a tournament
  start a day early and disagree with the calendar they had subscribed to
  from that same app. Caught by running the Calendar against live data from
  a machine in a different zone than the server. Spans now render against
  `GET /api/meta`'s timezone — which nothing in the frontend had ever read —
  while kickoff times, which genuinely are moments, stay local to the viewer.
- **UFC method of victory** (2026-07-27) — a finished bout now shows *how*
  it ended, not just who won: the status pill reads "FINAL · KO/TKO", the
  card carries an `R2 4:21` chip, and the detail modal leads with the full
  result (method, the promotion's own wording, round and stoppage time).
- **Map pins stopped inventing precision** (2026-07-27) — TheSportsDB's
  `strLocation` is free text and is often a county or a city ("Dorset,
  England"), and the enrichment used to hand that back as the *venue* when
  it knew no stadium. Geocoding an administrative area returns its centroid,
  which the pipeline then cached and plotted as a confident home-ground fix
  tens of km from the real one. `stadiums._compose_venue` now returns `None`
  rather than promote an area to a venue, which un-shadows the fallback that
  already existed (the team's most-common stored home-game venue); rows
  already poisoned are recognised by `stadiums.is_area_only_venue` and
  re-resolved by both `refresh_locations` and `refresh_competition_stadiums`.
  A team with no trustworthy venue is left off the map instead of pinned
  somewhere plausible, and the team panel labels an area as *Location*, never
  under "Home venue". **The repair has not run against the live database
  yet** — see *Loose ends*.
- **Map markers say what they are** (2026-07-27) — MapLibre files every
  marker as a `role="button"` and names them all "Map marker", so the map
  read as twenty identical buttons to a screen reader. Each pin now builds
  its own accessible name from the data it already draws
  (`views/map/labels.ts`, unit-tested): a team pin names its club and ground
  plus what its badge encodes, a venue pin names the venue and its count, and a
  cluster names a count and its action ("12 venues, zoom in"). The name is
  signed into the marker's reconcile signature, so a kept pin re-labels after
  a poll instead of freezing at first paint, and the decorative plane and fan
  markers are `aria-hidden` rather than a crowd of nameless buttons.
- **The Calendar's team filter only offers teams that play games**
  (2026-07-27) — the Subscribe menu had already stopped offering a followed
  golfer a per-team feed (a tournament belongs to no team, so the feed is
  empty forever), but the team filter beside it went on listing them, and
  picking one produced a permanently blank grid: a golfer matches neither
  side of any game, and the tournament spans hide themselves whenever the
  filter is not "all". Both controls now ask one predicate
  (`views/calendar/gameSideFollows.ts`) instead of two rules that drifted
  apart, each explains the absence rather than silently dropping the row, and
  a filter selection that leaves the options resets to "All teams".
- **SQLite enforces foreign keys** (2026-07-27) — re-picking your teams
  could fail permanently once the daily refresh had archived a standings
  table, because `replace_followed` dropped leagues without their archives.
  The durable half of the fix is `PRAGMA foreign_keys=ON` for the shipping
  SQLite engine: the desktop / homelab build now enforces what Postgres
  always did, and startup sweeps the orphaned rows an older database may
  hold. Desktop users see this once, on the first launch after upgrading —
  [docs/desktop.md](docs/desktop.md#upgrading-an-existing-database) says what
  to expect.
- **Native macOS desktop app** — a real `SportsDash.app` (Tauri shell) with
  the FastAPI backend frozen in via PyInstaller and bundled inside it, so it
  launches with no browser, no Docker, and no Python install. One-command
  build (`scripts/build-desktop.sh`). See [docs/desktop.md](docs/desktop.md).
- **Branded app icon** generated from the SportsDash logo.

## 🎯 Near-term — distribution & packaging

The desktop app exists; making it *easy to get and trust* is the next step.

- ~~**CI release workflow**~~ — ✅ shipped (2026-07-12): tagging `v*` builds
  the `.dmg` on a macOS runner and attaches it to the GitHub Release
  ([v1.0.0](https://github.com/Mpawlowski5467/SportsDash/releases/tag/v1.0.0)).
  The release sequence — including the *two* files the version lives in — is
  the checklist in [CONTRIBUTING.md](CONTRIBUTING.md#release-checklist).
- **Code signing + notarization** — pipeline fully wired (2026-07-12) but
  **deliberately dormant**: SportsDash is a free open-source project, so
  releases ship unsigned (right-click → Open past Gatekeeper, or build
  from source). If that ever changes, enrolling in the Apple Developer
  Program and adding the six `APPLE_*` repo secrets (docs/desktop.md)
  turns signing on with no code changes.
  *Verify:* after the secrets exist, push a throwaway `v*` tag and run
  `spctl -a -vvv SportsDash.app` (expect `accepted … Developer ID`) and
  `xcrun stapler validate SportsDash.app` on the downloaded `.dmg`; then
  open it on a machine that has never built the app, where an unsigned
  bundle would still raise the unidentified-developer dialog.
- ~~**Faster launches**~~ — ✅ shipped (2026-07-20): the frozen backend now
  builds as PyInstaller *onedir* (shipped under the app's Resources and
  spawned from there), skipping onefile's per-launch self-extraction
  (~3–6s saved).
- **More desktop targets** — Intel (`x86_64`) macOS, and Windows / Linux
  builds (the Tauri + bundled-backend approach is cross-platform). Each needs
  an `x86_64`/host Python plus PyInstaller and the matching Rust target;
  `scripts/build-desktop.sh` and the release workflow currently assume
  `aarch64-apple-darwin` throughout.
  *Verify:* per target, the built app has to launch on hardware (or a VM)
  that is **not** the build host and has no Python installed — that is the
  whole point of the bundle, and a frozen backend that quietly falls back to
  a system interpreter looks identical on the developer's machine. Confirm
  the setup wizard appears against an empty database, then `GET
  /api/health` through the app's own port answers `200`.

## 🛠️ Later — product

- **Mobile apps** — wrap the existing frontend with Capacitor for installable
  iOS / Android apps, going beyond today's PWA. The API base is already
  build-time configurable (`VITE_API_BASE`, as the Tauri build uses), so the
  work is packaging and the network story, not the app.
  *Verify:* install on a real handset and reach a SportsDash server on the
  LAN — the simulator shares the host's network and so proves nothing about
  the case that actually breaks. Check the views that assume a pointer
  (Map pin hover labels, the kiosk rotation) and that the `.ics`
  `webcal://` links hand off to the phone's calendar app.
- **More sports & providers** — the `SportsProvider` adapter design makes new
  sources additive; candidates include more leagues and a second provider for
  redundancy. (See *Adding a provider* in the README.) Motorsport shipped
  2026-07-28 (F1 / NASCAR Cup / IndyCar via ESPN, riding the leaderboard
  `Event` pipeline); the item stays open for further sports and for
  provider redundancy.
  *Verify:* a new provider is done when the existing suite passes against it
  unchanged — the scheduler, services and routes resolve providers by the
  league's `provider` field and must not need a line. Add recorded-JSON
  fixture tests (never live calls) covering a missing-keys payload, and
  check the sport's `period` / `period_label` / `is_intermission` mapping
  against a real broadcast before trusting the notifications it drives.
- ~~**Richer matchup previews**~~ — ✅ shipped (v1.0.0): the Matchup tab
  leads with betting odds and win probability (moneyline, spread and
  over/under via `GET /odds`), then recent form, last meetings, projected
  lineups, injuries and venue weather — each section hides itself when a
  league has nothing to show.
- ~~**Historical archives**~~ — ✅ shipped (2026-07-12) for standings and
  results: season pickers on the Standings and Results views serve any
  ESPN season via `GET /standings/{league}?season=` (DB-archived after
  one fetch; the scheduler also archives each season's final table as
  leagues roll over) and `GET /history/results/{team}?season=`.
  Cross-season head-to-head records shipped in the Matchup view too
  (2026-07-13): W-D-L vs the opponent across the last 5 seasons, from the
  followed side's perspective, built on the same cached season fetches.

## 🔧 Loose ends — coverage & correctness

Known gaps inside features that already ship, rather than new directions.
Most are small; the ones that are not say so.

- **NHL intermission, verified live** — the between-periods mapping in
  `providers/espn/common.py` is a defensive heuristic that has never been
  checked against a live NHL feed, and the code says so in a comment. It
  treats a game as in intermission when it is live, is not a shootout, and
  either the status name contains `END_PERIOD` / `END_OF_PERIOD` or the
  detail text contains `END OF`. Nothing can be confirmed in July: there are
  no live games. Next chance is the 2026-27 season, **from October 2026**.
  *Verify:* with a followed NHL game actually between periods, read
  `status.type` off the scoreboard payload and check three things — that
  `state` is still `in` (the heuristic requires `live`, so if ESPN flips to
  `post` between periods the mapping never fires at all), that `name` or
  `detail` really carries one of those spellings, and that period 5 in a
  playoff game reads `2OT` rather than `SO` (the shootout branch is
  detail-text-driven and wins over the raw period). In the UI: the badge
  should read the intermission label with **no clock** — a running clock
  during the break is the tell that the branch was missed — and exactly one
  `Intermission` ntfy alert should arrive per break, with the score. If the
  spellings differ, they are the only thing that changes; the branch shape
  is right.
- **Live mid-race gaps on a racing board, seen actually moving** — the free
  half of this shipped 2026-07-29 and is *unconfirmed against a live race*:
  a live board now renders a running gap ("+2.418", "+1 lap") out of the
  site scoreboard's own per-competitor `statistics` slot, at zero extra
  calls, because that slot is already inside the payload the board is parsed
  from. What is established is only that the slot **exists** on every racing
  competitor in every league and session, and that it was an empty `[]` on
  all 1606 scanned — every one of them a finished or scheduled session,
  because no racing session was live anywhere in ESPN's five racing leagues
  on the probe day (F1 summer break, Cup off-weekend, nothing at all for
  9 days). The populated shape is therefore a *model*: the parser is
  deliberately liberal about it and fail-safe — a surprise costs that one
  column, never the row or the board — and both the populated and the absent
  case are pinned by tests. The expensive half, a bounded per-car live pass,
  is **deliberately deferred**: it is the only other route that exists (22
  calls per poll on an F1 race, 39 on a Cup race; no bulk expansion exists,
  proven byte-identical across every `enable=` / `expand=` / `view=`
  variant), and it is not even established that anything in a per-car
  payload *moves* during a race — `behindTime` / `totalTime` are finishing
  values, the `gapToLeader` category is present-but-empty on all ~210
  stock-car/IndyCar cars sampled, and `…/competitions/{c}/situation` returns
  a body containing only `$ref`.
  *Verify:* two dated probes, each the same 7-URL set fired three times
  ~15 min apart once the scoreboard shows `state == "in"` (~21 calls each) —
  **2026-08-08T21:00Z**, NASCAR Xfinity @ Iowa (`nascar-secondary`, event =
  competition `202608080747`), the soonest observable live session anywhere;
  and **2026-08-23T13:00Z**, the F1 Dutch GP race (event `600057441`,
  competition `401839097`), the only series with per-car times at all — with
  a cheap warm-up on FP1 at 2026-08-21T10:30Z. The set: the site scoreboard
  (**is `competitions[0].competitors[].statistics` non-empty?** — the
  decisive question, and free in production — and what is `status.period`?),
  the core event root (do competitor items gain any inline field beyond
  `{id,uid,type,order,startOrder,winner,athlete,status,statistics,vehicle}`?),
  then the competition's `status` (does a `flag` appear outside F1?),
  `situation` (still `$ref`-only?) and `statistics` (does the aggregate grow
  past its 6 stats?), plus per-car `statistics` for the leader and one
  mid-pack car (do `lapsCompleted` / `behindLaps` / `behindTime` /
  `gapToLeader` populate live?). Decision rule: if the site-scoreboard slot
  populates, live gaps are **already shipped** at zero marginal cost and only
  the modeled test fixture — plus `_racing_site_stat_map`'s key set, if the
  stat names differ from the core vocabulary — needs correcting to what was
  observed; else if per-car `behindTime` moves live, build the bounded pass
  (Race session only, `state == "in"` only, one live event, top 8 by `order`
  plus any followed driver, hard cap 12 cars, further gated on
  `status.period` having changed since the last poll → ≤12 calls per 180 s
  poll); else do not build it, and leave the gap column empty until FINAL.
  Independently: if the F1 probe shows the competition `flag` turning
  YELLOW/RED live (it reads `CHECKER` post-race, and only F1 publishes it at
  all), a caution indicator costs 1 call per poll per live F1 event — do not
  add it before it is observed. Two scheduling traps: `leagues[0].calendar`
  times are **wrong** (it misreports both the Dutch GP and Cup Indy — use
  `event.date` / `competitions[].date`), and outside F1 there are no practice
  or qualifying sessions at all (every NASCAR and IndyCar event carries
  exactly one competition with `type: null`), so the first observable live
  session in those series is the race itself. Finally, once a board does show
  numbers: plausible numbers prove nothing on their own — check that a
  leader's gap actually changes between two 180 s ticks rather than freezing
  at first paint, compare one against the broadcast timing screen since a
  stale gap and a live one look identical, and watch `/api/metrics` for a
  per-car fan-out that is not actually capped.
- **"finished 18 (DNF)" is the wrong verb** — a knock-on of the DSQ/DNF
  rendering above, left alone because it lives in a file that pass did not
  touch: `scheduler/live.py` builds the final-event alert as
  `f"{best.name} finished {best.position_label} ({best.score})"`, so a
  followed driver who retired now reads *"finished 18 (DNF)"* and one who was
  disqualified *"finished 2 (DSQ)"*. Both are more accurate than the old
  *"(56 laps)"* / *"(+15.080)"*, so this is a wording nit, not a bug.
  *Verify:* it is a string, so a unit test on `_final_event` is enough —
  a followed driver with `score == "DNF"` should not be described as having
  finished. Reading one real alert for a retired followed driver would also
  do it, and needs a race weekend with a driver you follow retiring from it.
- **Golf tournaments on the Calendar, in a real calendar app** — the spans
  were driven against live PGA Tour data on 2026-07-27 (six tournaments
  synced from ESPN, one finished with a 144-row leaderboard) and the grid,
  the leaderboard modal and the all-day `.ics` entries all render correctly;
  a day-shift bug found during that check is fixed and covered. What is
  still untested is the leg that involves a client we do not control:
  *Verify:* subscribe Apple or Google Calendar to the `webcal://` URL from
  the Calendar tab's **Subscribe** menu and confirm a Thu–Sun tournament
  occupies Thursday through Sunday there — not Thursday through Monday.
  RFC 5545 makes `DTEND` exclusive for all-day events, so the feed says the
  Monday on purpose; an off-by-one here looks identical to a correct feed
  until a real client draws it.
- **The area-centroid repair, run against the live database** — the fix
  above changes what gets *written*; the rows already poisoned are only
  corrected when the repair pass runs, and it runs inside
  `refresh_locations` and `refresh_competition_stadiums`, both spawned at
  startup. Until the API restarts, nothing in the live database has moved.
  *Verify:* restart the API and watch the log for roughly three lines of the
  form `refresh_locations: <team-id> was pinned at the centroid of 'Dorset,
  England' — clearing for re-resolution` and the
  `refresh_competition_stadiums: … — re-resolving` counterpart. No lines
  means the repair never fired: it fingerprints a row by `home_venue` being
  byte-identical to `venue_location`, so check that first. Then the
  database directly — `SELECT id, home_venue, venue_location FROM teams
  WHERE home_venue = venue_location;` and `SELECT key, venue, location FROM
  stadiums WHERE venue = location;` must both return **zero rows**; any
  survivor was written by a path the fix did not find and is worth chasing.
  Finally `GET /api/map`: a club whose stadium TheSportsDB does not know
  should reappear at the ground **its own stored fixtures name** (spot-check
  the coordinates against the real stadium, not just that a pin exists), and
  the competition pins that were sitting on city centroids should be **gone
  and stay gone** — that is the intended trade-off, not a regression, and
  they return on their own once a tournament puts them at a known host
  venue. Click a pin for a team whose ground is genuinely unknown: the panel
  heading must read "Unknown venue" with the area listed separately as
  *Location*.
- **The two frontend fixes, seen in a running browser** — the marker names
  and the Calendar team filter both shipped against unit tests and the
  installed MapLibre source, with the app not running (the dev server and
  API were not up, and standing the live stack up mid-edit would have hit
  the upstream providers). Neither has been looked at.
  *Verify (markers):* open the Map view and run
  `[...document.querySelectorAll('.maplibregl-marker')].map(e => e.getAttribute('aria-label'))`
  in the devtools console — no entry may be `null` or `"Map marker"`. In
  Stadiums mode expect club-plus-ground names, in Upcoming games mode
  `"<venue>, N upcoming games"`, and zoomed out `"N venues, zoom in"` (with
  `", including teams you follow"` on a badge wearing the amber ring); the
  plane and fan elements must report `aria-hidden="true"`. Then the part the
  unit tests cannot reach: leave the map on a venue with a live game and
  wait one 30s poll — a marker whose count or today-pulse changed must show
  a **changed** `aria-label` *without* its pulse animation restarting. A
  name that is correct at first paint and stale after a poll is the exact
  bug this branch fixed twice already.
  *Verify (filter):* with a golfer followed, the Calendar's team filter
  lists no golfer and shows the explanatory note beside it; the Subscribe
  menu and the filter now agree; the tournament spans still draw under "All
  teams"; and the header row still lays out sanely at a medium width — the
  left group grew a note, so the Refresh/Subscribe/Export group may wrap to
  a second line (the row is `flex-wrap`, so a wrap is expected, an overflow
  is not). With **no** golfer followed the UI is byte-identical to before,
  so that case proves nothing.
- **A competition team should keep its stadium too** — a followed team with
  no upstream venue falls back to its most-common stored home-game venue; a
  whole-competition team (a national side) cannot, because
  `repository.most_common_home_venue()` is keyed by `team_id` and
  competition teams have no `teams` row. That asymmetry is why the fix above
  drops those pins rather than re-resolving them. Adding
  `most_common_home_venue_by_name(session, league_id, team_name)` — the same
  `GROUP BY` over `games`, matching `home_name` casefolded within the league
  — would let `scheduler/stadium_cache.py` use the identical fallback.
  *Verify:* a unit test that stores two home games at one venue for a name
  with no `teams` row and asserts the query returns it (and returns nothing
  for a name that only ever played away); then, live, the competition pins
  that disappeared above come back — at their actual ground, at a plausible
  coordinate, not at the city centre they used to sit on.
  Considered instead, and still available: *plot* the area centroid but mark
  it approximate — a nullable `approximate` flag through `TeamLocation`, the
  two ORM tables, `MapTeamOut`, and a visually distinct marker with no
  confident fly-to. That trades an absent pin for a visibly uncertain one;
  it was not built because shipping only the backend half would have left
  the same confident pin on screen.
- **Keyboard-reachable map pins** — MapLibre gives every marker
  `role="button"` but never a `tabindex`, so the pins are now *named* but
  still cannot be reached or activated by keyboard alone. A blanket
  `tabindex="0"` is the wrong fix: a "follow all" user would meet 700+ tab
  stops. It wants a design decision — a roving tabindex over the visible
  set, or a parallel list view of the plotted teams and venues.
  *Verify:* Tab from the map's filter bar and reach every visible pin (and
  only the visible ones) in a sensible order, activate one with Enter and
  Space, and land focus inside the side panel it opens with Escape
  returning it to the pin. Count the tab stops with every league followed —
  if that number scales with the pin count, the roving tabindex is not
  actually roving.
- **A golfer in the Calendar filter that shows their tournaments** — the
  richer alternative to the exclusion that shipped: rather than removing a
  followed golfer from the team filter, selecting one would narrow the grid
  to that golfer's events. Not buildable on the client today.
  `SportEvent.followed_player_ids` looks like the link but is derived purely
  from the *stored leaderboard*, and a future tournament arrives from the
  season calendar with an empty board — so filtering on it would show the
  tournaments the golfer has already played and none of the ones they are
  about to, which is exactly the wrong half for a calendar, and it would
  fail invisibly. (The per-team `.ics` feed declines it for the same reason;
  docs/CONTRACTS.md records the argument.) Doing it properly needs a real
  entry list: a nullable, additive backend field linking an event to
  followed golfers independently of the board, populated from an ESPN
  field/entrants endpoint at schedule-sync time. Backend + contract change,
  not a views change.
  *Verify:* the field is only worth having if it is populated **before** the
  event starts, so the test that matters is a recorded-fixture case where a
  tournament with an empty leaderboard still lists its entrants. Then, live:
  pick a followed golfer in the filter and confirm their *upcoming*
  tournaments are on the grid, not only past ones — comparing against the
  tour's published field, since a grid showing something looks correct
  either way.
- ~~**A golf league is fetched down the games path it can never satisfy**~~ —
  ✅ shipped (2026-07-28, with motorsport): `get_schedule` and
  `get_competition_schedule` now return `[]` early for `LEADERBOARD_SPORTS`
  leagues without any fetch, so a *Follow all* PGA Tour (or F1) schedule
  refresh no longer wastes upstream calls or logs a run of `Skipping
  malformed ESPN event` noise. Pinned by
  `tests/test_espn_racing.py::test_provider_games_paths_skip_leaderboard_sports`;
  the live-log sighting from the old *Verify* line (an events-fetched line
  and zero skip lines for `pga` on a real refresh) has not been re-run and
  is worth a glance on the next schedule refresh.
- **No component-test harness** — every defect found on the map and the
  panels this cycle was a React lifecycle bug (markers freezing at the
  values they were born with, a filter value outliving its options, Escape
  being swallowed), and **none of them was caught by a test**. The current
  pattern is deliberate: logic is extracted into pure helpers with their own
  vitest suites (`views/map/travel.ts`, `views/map/labels.ts`,
  `views/map/reconcile.ts`, `views/calendar/eventSpans.ts`,
  `views/calendar/gameSideFollows.ts`) and the components stay thin. That
  works for rules and not at all for effects — there is no jsdom, no
  `@testing-library/react`, and 92 vitest tests that never render a
  component.
  *Verify:* the harness earns its place only if it reproduces a bug that
  actually happened. Before adopting one, write the failing test first: mount
  the map, change a marker's underlying data, and assert the accessible name
  and hover label follow — it must fail against the pre-fix reconciler.
  Same for the calendar filter: unfollow the selected team and assert the
  Select and the grid still agree. A harness that only proves components
  render is not worth the maintenance.
- **Prometheus-style `/metrics`** — **shipped 2026-07-28** as
  `GET /api/metrics` (`backend/app/routes/metrics.py`), reversing the
  earlier decline: hand-rolled text-format 0.0.4 exposition, so the
  "would add a dependency" half of the objection no longer applies, and
  every series is read from the SAME sources `/api/health` reads, which
  answers the other half. Adds what `/api/health` cannot: history —
  cache hit/miss counters, per-job scheduler run counts and last-run
  timestamps, background-task gauge. Scrape `127.0.0.1:8001/api/metrics`
  (or `:3000/api/metrics` through the proxy).
  *Verify criterion met:* `backend/tests/test_metrics.py` asserts the
  agreement directly — the same request pair must show the DB probe and
  breaker state identically in both endpoints (probe down ⇒
  `sportsdash_database_up 0` and `database: false`; breaker open ⇒
  state gauge `2` and `circuit: "open"` + `status: "degraded"`).
- **The 11 `react-hooks/exhaustive-deps` warnings** — pre-existing and
  **deliberately advisory**: `eslint.config.js` sets the rule to `warn` on
  purpose, because several of the effects it flags omit a dependency
  intentionally (a mount-only camera setup, a cinematic that must not
  restart on every render). CI runs `eslint` with
  `--report-unused-disable-directives-severity=error`, so a *stale* disable
  comment fails the build while these warnings do not — that asymmetry is
  the point. Most of the 11 are the same shape: a `?? []` fallback inside a
  `useMemo` dependency.
  *Verify:* they are only fixable one at a time, each with a reason. Silence
  one by hoisting the fallback into its own `useMemo` (never by widening the
  dep array of an effect that must not re-run), then confirm the view still
  behaves — for the map, that a poll does not restart the fly-to or the
  marker pulse animations. `bun run lint` should end with a smaller count
  and still `0 errors`; the number in this bullet is then stale and should
  move with it.

## 💡 Ideas — maybe someday

- **Shareable read-only views** — a link to a single team/league page for
  someone else, while the app stays single-user by design. It needs a real
  answer for what a link exposes, since the API has no auth and the trust
  model (README) assumes every reachable client is you.
  *Verify:* the security question is the feature. A share link must reach
  exactly one team's read-only data and nothing else — check that it cannot
  reach `POST /api/setup/follow`, `PUT /api/notifications/prefs`, or another
  team's page by editing the URL, and that revoking it takes effect on the
  next request rather than the next restart.
- **Customizable layout** — rearrange or hide tabs/widgets per preference,
  persisted like the theme and kiosk toggles already are.
  *Verify:* hide a tab, reload, and confirm it stays hidden and that kiosk
  auto-rotation skips it instead of advancing to a blank view; then clear
  `localStorage` and confirm the default layout returns rather than an empty
  shell.
- **Localization** — the news locale is already configurable; extend that to
  the rest of the UI.
  *Verify:* switch locale and check the places dates are built rather than
  formatted — the Calendar's local day keys, the `.ics` all-day dates, and
  "today" — still resolve to the same days. UTC-internally is the rule that
  breaks first here: a translated UI that shifts a game to the wrong day is
  worse than an untranslated one.

## 🚫 Deliberate non-goals (for now)

- **Multi-user accounts / auth.** SportsDash is intentionally one user on your
  own hardware — no login, no tracking. A shareable read-only view (above) is
  the most this is likely to bend.
- **Paid data feeds.** Everything stays on keyless public sources (ESPN,
  TheSportsDB) so the app needs no accounts or API keys.
- **A mock provider / demo mode.** Both were removed and are not coming
  back — SportsDash is live-data only. Two data paths meant every bug had to
  be reproduced twice, and the one that mattered was always the live one.

---

Have an idea? Since this is a personal project, the roadmap is mostly a note
to self — but the provider-adapter and tab structure are designed to make
most additions land without touching the core.
