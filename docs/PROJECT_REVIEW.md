# SportsDash project review

Reviewed September 8, 2026; remaining issues addressed September 10. Overall assessment after both passes: **9/10** for a personal sports dashboard.

The project has a strong foundation: typed frontend/API contracts, broad provider and database tests, sensible empty states, optional caching, and a working desktop wrapper. This review covered the frontend, API, providers, refresh jobs, database behavior, map animation, build configuration, and deployment documentation. It included desktop and phone-size browser checks. It is not a guarantee that every live provider, league, device, or deployment is fault-free.

## Changes made

| Area | Result |
| --- | --- |
| Fans and aircraft | Replaced the map artwork with original SVG figures: people with different poses, scarves, flags, skin tones, and team-colored clothing; a detailed airliner with wings, engines, windows, and team accents. Map camera, location, and weather controls use vector icons. |
| Crowd animation | Increased readability and spacing. Fixed fades being overwritten by MapLibre, trailing fans disappearing before reaching the gate, delayed starts after a hidden tab, and duplicate parked animation loops. Home-game focus now starts its crowd. |
| Flight animation | Aircraft follow the route as projected through the camera, including pitch and rotation. A local tangent prevents the plane reversing near the camera horizon. Switching views or taking control cancels the cinematic; hidden ambient animations pause. Reduced-motion preferences are respected. |
| Accessibility and mobile | Venue pins are keyboard-operable buttons. Decorative figures do not become fake buttons. Small screens get a usable navigation row, stacked calendar controls, a correctly sized map, and reachable drawer titles/close buttons. |
| Player photos | Added headshots to leaders and projected lineups. Photo lookup checks normalized full names and sport, disambiguates namesakes by club, and rejects malformed image URLs. Ambiguous results retain initials. |
| Photo refresh reliability | Retains existing photos before backfill, rotates the daily capped batch to avoid starving later players, and can fill photos from stored rosters when the roster provider fails. This recovery does not mark an old roster fresh or overwrite injury/stat information. Full roster refreshes can still clear obsolete stats. |
| Other correctness | Rejects non-finite and malformed leader statistics, removes unstable React dependencies and lint warnings, moves ticker persistence out of React state updaters, and synchronizes the desktop Cargo lockfile version. |
| Regression protection | Added map lifecycle/motion and backend photo/provider cases; expanded version consistency checks; regenerated the OpenAPI snapshot. API tests no longer start real database workers, and shared clients are closed before test event loops end. |
| Provider denials | HTTP 401/403 now fails immediately, preserves the stored refresh snapshot, and counts toward the provider circuit breaker. Split schedules stop on denial rather than continuing requests or treating the response as successful empty data. |
| Map maintenance and delivery | Extracted flight state, listeners, timers, aircraft, route and arrival crowd into `FlightCinematic`. Missing basemap icons reuse a related sprite or a neutral place marker. The renderer has a separate cacheable chunk, with a build check preventing it from entering the initial page. |
| Desktop packaging | Fixed obsolete generated onefile executables blocking the newer backend folder. Builds without Developer ID now seal the complete app with an ad-hoc signature, then verify the embedded backend and resources. |
| Tooling | Selected Node 22 LTS in frontend configuration and CI/release workflows. Local checks on Node 22 complete without the earlier Node deprecation notice. |

## Verification

Initial browser checks used a copy of the existing database, with scheduled refresh and notifications disabled. Installer checks used a separate database copy with the normal refresh lifecycle and notifications disabled. The temporary fictional-fixture map page used for repeatable flight/crowd checks was removed afterward.

- Desktop navigation: Today, Calendar, Matchup, League/standings/leaders/bracket, Results, News, and Map.
- Phone layout at 390 × 844: navigation, calendar, map, venue drawer, and news drawer; no horizontal page overflow in the checked views.
- Map: home arrival, away flight and stadium arrival, repeated interactions, hiding the map, reduced motion, and keyboard activation of a venue pin.
- Photo backfill: the review database improved from **17/30 to 29/30** players with photos. All **27** headshots displayed by the leaders page and **11** in the inspected projected lineup loaded successfully.
- Backend: **813 tests pass on SQLite and the same 813 pass on PostgreSQL 16**; approximately **88% SQLite line coverage**. Ruff lint and formatting checks pass. The SQLite suite also passes with resource, unraisable-exception, and thread-exception warnings treated as errors.
- Frontend: **138 tests** across **18 files**; TypeScript and ESLint checks pass with no lint warnings. New flight tests cover cancelled/repeated intros, exact arrival, hidden views, reduced motion, and complete resource cleanup.
- Production web and Tauri builds pass. The packaged Apple Silicon `.app` and `.dmg` build successfully. The disk image mounts and its app passes `codesign --verify --deep --strict`. Copying the app from the installer to an isolated installation directory and launching it starts its embedded backend, which serves version 1.4.0 and the database-backed API. The final production browser build also loads the map and opens a stadium drawer against that backend.
- All six services in an isolated Docker Compose stack start successfully: PostgreSQL, Redis, ntfy, API, frontend, and backup. The production proxy serves health/setup responses and the setup UI. The backup container writes a valid gzip archive. Test containers use separate volumes and loopback ports; notifications are disabled.
- At stadium zoom, the final map replay rendered fans with **no browser errors or warnings**. Repeated intro requests followed by hiding the map left no plane or crowd markers.
- The map view is approximately **68 KB minified / 23 KB gzip**, with MapLibre separately cached at **1.06 MB / 284 KB gzip**. The renderer is absent from the initial page's static dependency graph. An intentionally regressed configuration was rejected by the new build check.
- `git diff --check` passes.

## Remaining limits and priorities

1. **Live data availability.** ESPN alternated between successful responses and access denials during live checks. The packaged backend reported a degraded provider through its health endpoint, while stored data remained readable. Fresh setup displayed a retryable team-catalog error when the provider refused access. These are external availability limits; the app cannot promise fresh data or every photo. The remaining photo lookup returned no usable image, so initials remain appropriate.
2. **Map payload.** The total map download remains approximately 307 KB gzip. Separating the renderer improves reuse across app updates; it does not remove the cost of a first map visit. Vite still reports the large renderer dependency. Further component separation and performance work should follow measurements on target devices.
3. **Apple distribution and native visual QA.** The generated installer is ad-hoc signed, not Apple-notarized; Developer ID/notarization credentials were unavailable. Native visual inspection could not finish because macOS computer-use permissions remained pending. Package integrity, process launch, and embedded-backend checks passed; the frontend was visually checked in the browser. The existing `/Applications/SportsDash.app` was not replaced.
4. **Deployment scope.** SportsDash remains a single-user application for a trusted network. Its documented Docker setup has no application login; public multi-user hosting requires a separate authentication and deployment design.

The rating reflects a useful, visually improved project with substantial regression coverage, verified PostgreSQL behavior, and a validated local installer. Provider availability, Apple notarization, and device-specific visual/performance checks remain outside a guarantee of production readiness.
