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
└─ sportsdash-backend      (the FastAPI app frozen by PyInstaller, run as
                            a Tauri "sidecar" process)
```

On launch the Tauri shell ([frontend/src-tauri/src/lib.rs](../frontend/src-tauri/src/lib.rs)):

1. spawns the bundled backend on `127.0.0.1:8765`,
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

A fresh launch starts with an empty DB, seeds leagues/teams from the
bundled `config/teams.yaml`, and refreshes live data in the background —
the same first-run flow as a fresh server install.

## Building it

One command does everything:

```bash
./scripts/build-desktop.sh
```

That script:

1. freezes the backend with PyInstaller
   ([backend/sportsdash-backend.spec](../backend/sportsdash-backend.spec)) →
   a single `sportsdash-backend` binary,
2. stages it as the Tauri sidecar
   (`frontend/src-tauri/binaries/sportsdash-backend-<target-triple>`),
3. runs `bun tauri build`, which builds the frontend (`--mode tauri`) and
   compiles the app.

Output:

```
frontend/src-tauri/target/release/bundle/macos/SportsDash.app
frontend/src-tauri/target/release/bundle/dmg/SportsDash_1.0.0_aarch64.dmg
```

Drag the `.app` to `/Applications`, or share the `.dmg`.

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
- **Startup time**: the backend is a PyInstaller *onefile* binary, which
  self-extracts on each launch (~3–6s to first paint). Switching the spec
  to a onedir build would make launches faster at the cost of a folder of
  files instead of a single binary.
- **Notifications (ntfy) are off by default** in the desktop build
  (`SPORTSDASH_NOTIFICATIONS_ENABLED=false`); the self-hosted ntfy server
  isn't part of the bundle.
- **Apple Silicon only** as built (`aarch64-apple-darwin`). An Intel build
  needs an `x86_64` Python + PyInstaller and the matching Rust target.
