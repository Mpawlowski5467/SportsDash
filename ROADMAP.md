# Roadmap

SportsDash is a single-user, self-hosted personal project. This is a *living
list of intentions*, not a committed schedule — items move, merge, and get
dropped as priorities shift. Grouped by rough horizon.

See the [README](README.md) for what the app already does today.

## ✅ Recently shipped

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
- **Code signing + notarization** — pipeline fully wired (2026-07-12) but
  **deliberately dormant**: SportsDash is a free open-source project, so
  releases ship unsigned (right-click → Open past Gatekeeper, or build
  from source). If that ever changes, enrolling in the Apple Developer
  Program and adding the six `APPLE_*` repo secrets (docs/desktop.md)
  turns signing on with no code changes.
- ~~**Faster launches**~~ — ✅ shipped (2026-07-20): the frozen backend now
  builds as PyInstaller *onedir* (shipped under the app's Resources and
  spawned from there), skipping onefile's per-launch self-extraction
  (~3–6s saved).
- **More desktop targets** — Intel (`x86_64`) macOS, and Windows / Linux
  builds (the Tauri + bundled-backend approach is cross-platform).

## 🛠️ Later — product

- **Mobile apps** — wrap the existing frontend with Capacitor for installable
  iOS / Android apps, going beyond today's PWA.
- **More sports & providers** — the `SportsProvider` adapter design makes new
  sources additive; candidates include more leagues and a second provider for
  redundancy. (See *Adding a provider* in the README.)
- **Richer matchup previews** — surface betting odds and head-to-head history
  in the Matchup tab, with light pre-game context.
- ~~**Historical archives**~~ — ✅ shipped (2026-07-12) for standings and
  results: season pickers on the Standings and Results views serve any
  ESPN season via `GET /standings/{league}?season=` (DB-archived after
  one fetch; the scheduler also archives each season's final table as
  leagues roll over) and `GET /history/results/{team}?season=`.
  Cross-season head-to-head records shipped in the Matchup view too
  (2026-07-13): W-D-L vs the opponent across the last 5 seasons, from the
  followed side's perspective, built on the same cached season fetches.

## 💡 Ideas — maybe someday

- **Shareable read-only views** — a link to a single team/league page for
  someone else, while the app stays single-user by design.
- **Customizable layout** — rearrange or hide tabs/widgets per preference.
- **Localization** — the news locale is already configurable; extend that to
  the rest of the UI.

## 🚫 Deliberate non-goals (for now)

- **Multi-user accounts / auth.** SportsDash is intentionally one user on your
  own hardware — no login, no tracking. A shareable read-only view (above) is
  the most this is likely to bend.
- **Paid data feeds.** Everything stays on keyless public sources (ESPN,
  TheSportsDB) so the app needs no accounts or API keys.

---

Have an idea? Since this is a personal project, the roadmap is mostly a note
to self — but the provider-adapter and tab structure are designed to make
most additions land without touching the core.
