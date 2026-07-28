# SportsDash desktop app (macOS)

SportsDash can be packaged as a native macOS app — a real `SportsDash.app`
you launch from the dock, with **no browser, no Docker, and no Python
install required**. This is an *additional* delivery path; the Docker /
browser deployment (`docker-compose.yml`, `server.ts`) is unchanged and
keeps working exactly as before.

## How it works

The desktop app bundles two things the user normally runs separately:

```
SportsDash.app
├─ the React UI            (frontend/dist, loaded by the Tauri webview)
└─ sportsdash-backend/     (the FastAPI app frozen by PyInstaller as an
   ├─ sportsdash-backend    *onedir* bundle — the executable …
   └─ _internal/            … plus its libraries and data files,
                            shipped under Contents/Resources and spawned
                            as a child process on launch)
```

On launch the Tauri shell ([frontend/src-tauri/src/lib.rs](../frontend/src-tauri/src/lib.rs)):

1. spawns the bundled backend (`Contents/Resources/sportsdash-backend/sportsdash-backend`)
   on `127.0.0.1:8765`,
2. waits until it is accepting connections,
3. reveals the window (hidden until then, so the UI never paints against a
   backend that isn't up yet),
4. and kills the backend when the app quits.

The UI talks to the backend at `http://127.0.0.1:8765/api` — baked in at
build time via `VITE_API_BASE` in [frontend/.env.tauri](../frontend/.env.tauri),
loaded only by the `vite build --mode tauri` build, so the Docker frontend
build is untouched.

### Data location

The app bundle is read-only, so the SQLite database lives in the user's
data directory instead:

```
~/Library/Application Support/SportsDash/sportsdash.db
```

A fresh launch starts with an empty DB and opens the in-app **setup
wizard** — the same first-run flow as a fresh server install. The leagues
and teams you pick land in that database and are refreshed in the
background. (The bundled `config/teams.yaml` is a comments-only template,
so nothing is pre-seeded.)

#### Upgrading an existing database

Nothing to do — but one thing happens on the first launch after upgrading
from **1.3.0 or earlier**, and it is worth knowing about because it writes
to your database.

SQLite reads the `REFERENCES` in a schema but does not *enforce* them
unless asked to, and SportsDash never asked. The Docker deployment runs on
PostgreSQL, which enforces them unconditionally — so the desktop app was
the one place where deleting something could quietly leave rows pointing at
a parent that no longer existed. Re-picking your teams in the setup wizard
did exactly that: it dropped leagues and left their archived standings
tables behind.

Foreign keys are now enforced here too, which means those leftover rows
have to go — under enforcement, anything that touched one would fail on a
rule the row never satisfied. So the app sweeps them **once, at startup**,
and logs what it removed:

```
migrations: removed 42 orphaned standings_archive row(s) (no matching leagues)
```

They were unreachable junk — archived tables for leagues you had already
stopped following — so nothing you can see in the app is lost. The sweep is
safe to run repeatedly (later launches find nothing), and it never deletes
a row that something else still references: a followed team in that
position is counted and left alone rather than removed.

Your picks, games, standings, rosters and news are untouched. There is no
migration step and no downgrade concern — an older build simply stops
enforcing the rule again.

## Building it

One command does everything:

```bash
./scripts/build-desktop.sh
```

That script:

1. freezes the backend with PyInstaller
   ([backend/sportsdash-backend.spec](../backend/sportsdash-backend.spec)) →
   an onedir bundle `backend/dist/sportsdash-backend/`,
2. stages it under `frontend/src-tauri/binaries/sportsdash-backend/`, which
   `bundle.resources` in `tauri.conf.json` ships whole into the app's
   `Contents/Resources/` (Tauri's `externalBin` sidecar mechanism only
   bundles single files, so the onedir directory goes through resources),
3. runs `bun tauri build`, which builds the frontend (`--mode tauri`) and
   compiles the app.

Output:

```
frontend/src-tauri/target/release/bundle/macos/SportsDash.app
frontend/src-tauri/target/release/bundle/dmg/SportsDash_<version>_aarch64.dmg
```

`<version>` is whatever [tauri.conf.json](../frontend/src-tauri/tauri.conf.json)
declares. Drag the `.app` to `/Applications`, or share the `.dmg`.

### Prerequisites (one-time)

- Rust toolchain (`rustup`) — Tauri's build system
- Tauri CLI — installed as a frontend dev dependency (`@tauri-apps/cli`)
- PyInstaller — installed in `backend/.venv`

## Code signing / Gatekeeper

Signing and notarization are **fully wired** — locally and in the release
workflow — and turn on automatically once an Apple Developer ID
certificate exists. Until then builds are unsigned: on first launch macOS
Gatekeeper warns that the app is from an unidentified developer;
right-click → **Open** (or `xattr -dr com.apple.quarantine
SportsDash.app`) gets past it.

### One-time setup (requires an Apple Developer account, $99/yr)

1. **Enroll** at <https://developer.apple.com/programs/> with your Apple ID.
   Note your 10-character **Team ID** (Membership page).
2. **Create the certificate**: Keychain Access → Certificate Assistant →
   *Request a Certificate from a Certificate Authority* (save to disk) →
   upload the CSR at <https://developer.apple.com/account/resources/certificates>
   choosing type **Developer ID Application** → download and double-click
   the `.cer` to install it in your keychain.
3. **Local builds now sign automatically** — `scripts/build-desktop.sh`
   picks up the identity from the keychain.
4. **For CI releases**, export the certificate: Keychain Access → right-click
   the "Developer ID Application: …" cert → Export as `.p12` with a
   password, then add six repository secrets:

   ```sh
   base64 -i DeveloperID.p12 | tr -d '\n' | gh secret set APPLE_CERTIFICATE
   gh secret set APPLE_CERTIFICATE_PASSWORD   # the .p12 export password
   gh secret set APPLE_SIGNING_IDENTITY       # e.g. "Developer ID Application: Your Name (TEAMID)"
   gh secret set APPLE_ID                     # your Apple ID email
   gh secret set APPLE_PASSWORD               # app-specific password from appleid.apple.com
   gh secret set APPLE_TEAM_ID                # the 10-char Team ID
   ```

   The next `v*` tag then produces a **signed and notarized** `.dmg`
   (Tauri imports the cert into a temporary keychain, signs with the
   hardened runtime, and staples the notarization ticket) — no Gatekeeper
   friction for anyone who downloads it.

## Notes & trade-offs

- **Port 8765** is fixed for the desktop build. If something else is using
  it, the backend won't bind; change it in both `.env.tauri` and
  `BACKEND_PORT` in `lib.rs`.
- **Startup time**: the backend is a PyInstaller *onedir* bundle, so
  launches skip the old *onefile* self-extraction (~3–6s saved to first
  paint). The trade-off is a folder of files under `Contents/Resources/`
  instead of a single binary in the app bundle.
- **Notifications (ntfy) are off by default** in the desktop build
  (`SPORTSDASH_NOTIFICATIONS_ENABLED=false`); the self-hosted ntfy server
  isn't part of the bundle.
- **Apple Silicon only** as built (`aarch64-apple-darwin`). An Intel build
  needs an `x86_64` Python + PyInstaller and the matching Rust target.
