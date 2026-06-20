# Roadmap

SportsDash is a single-user, self-hosted personal project. This is a *living
list of intentions*, not a committed schedule — items move, merge, and get
dropped as priorities shift. Grouped by rough horizon.

See the [README](README.md) for what the app already does today.

## ✅ Recently shipped

- **Native macOS desktop app** — a real `SportsDash.app` (Tauri shell) with
  the FastAPI backend frozen in via PyInstaller and run as a sidecar, so it
  launches with no browser, no Docker, and no Python install. One-command
  build (`scripts/build-desktop.sh`). See [docs/desktop.md](docs/desktop.md).
- **Branded app icon** generated from the SportsDash logo.

## 🎯 Near-term — distribution & packaging

The desktop app exists; making it *easy to get and trust* is the next step.

- **CI release workflow** — a GitHub Actions job that builds the `.dmg` and
  attaches it to a tagged release, so non-technical users can download a
  ready-to-run app instead of building it.
- **Code signing + notarization** — an Apple Developer ID so the app opens
  without the Gatekeeper "unidentified developer" warning.
- **Faster launches** — switch the frozen backend from PyInstaller *onefile*
  to *onedir* to skip the per-launch self-extraction (~3–6s today).
- **More desktop targets** — Intel (`x86_64`) macOS, and Windows / Linux
  builds (the Tauri + sidecar approach is cross-platform).

## 🛠️ Later — product

- **Mobile apps** — wrap the existing frontend with Capacitor for installable
  iOS / Android apps, going beyond today's PWA.
- **More sports & providers** — the `SportsProvider` adapter design makes new
  sources additive; candidates include more leagues and a second provider for
  redundancy. (See *Adding a provider* in the README.)
- **Richer matchup previews** — surface betting odds and head-to-head history
  in the Matchup tab, with light pre-game context.
- **Historical archives** — past-season standings, results, and head-to-head
  records rather than only the live/near window.

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
