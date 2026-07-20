#!/usr/bin/env bash
#
# Build the SportsDash macOS desktop app end to end:
#   1. Freeze the FastAPI backend into an onedir bundle (PyInstaller).
#   2. Stage the bundle under src-tauri so `bun tauri build` ships it in
#      the app's Resources (tauri.conf.json `bundle.resources`).
#   3. Build SportsDash.app (+ .dmg) with Tauri, which also builds the
#      frontend via the `build:tauri` beforeBuildCommand.
#
# Output: frontend/src-tauri/target/release/bundle/{macos,dmg}/
#
# Self-bootstrapping: creates backend/.venv (with PyInstaller, from
# requirements-dev.txt) when missing, and fails early with a clear
# message if rustc / bun / python3.12 aren't installed.
#
# Re-run this whenever the backend or frontend changes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is required but not installed. $2" >&2
    exit 1
  }
}
need rustc "Install Rust via https://rustup.rs"
need bun "Install Bun via https://bun.sh"
need python3.12 "Install Python 3.12 (e.g. 'brew install python@3.12')"

TRIPLE="$(rustc -Vv | sed -n 's/^host: //p')"
echo "==> Host target triple: $TRIPLE"

if [ ! -x "$ROOT/backend/.venv/bin/pyinstaller" ]; then
  echo "==> Bootstrapping backend/.venv (+ PyInstaller)…"
  python3.12 -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install --quiet --upgrade pip
  "$ROOT/backend/.venv/bin/pip" install --quiet \
    -r "$ROOT/backend/requirements.lock" \
    -r "$ROOT/backend/requirements-dev.txt"
fi

echo "==> Freezing backend (PyInstaller onedir)…"
cd "$ROOT/backend"
.venv/bin/pyinstaller --clean --noconfirm sportsdash-backend.spec

echo "==> Staging backend bundle for Tauri resources…"
# The onedir directory ships whole under the app's Resources
# (tauri.conf.json `bundle.resources`); lib.rs spawns the executable
# inside it.  Also drop any stale onefile sidecar from older builds.
BIN_DIR="$ROOT/frontend/src-tauri/binaries"
STAGE_DIR="$BIN_DIR/sportsdash-backend"
rm -f "$BIN_DIR"/sportsdash-backend-*
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$ROOT/backend/dist/sportsdash-backend/" "$STAGE_DIR/"
chmod +x "$STAGE_DIR/sportsdash-backend"

# Sign locally when a Developer ID certificate is in the keychain and no
# identity was chosen explicitly; otherwise build unsigned (Gatekeeper will
# warn on other machines — see docs/desktop.md "Code signing").
if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
  if [ -n "$IDENTITY" ]; then
    export APPLE_SIGNING_IDENTITY="$IDENTITY"
    echo "==> Code signing as: $IDENTITY"
  else
    echo "==> No Developer ID certificate found — building UNSIGNED."
  fi
fi

echo "==> Building SportsDash.app (Tauri)…"
cd "$ROOT/frontend"
bun install --frozen-lockfile
bun tauri build

echo "==> Done. Bundles in:"
echo "    $ROOT/frontend/src-tauri/target/release/bundle/"
