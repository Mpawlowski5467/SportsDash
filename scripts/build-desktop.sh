#!/usr/bin/env bash
#
# Build the SportsDash macOS desktop app end to end:
#   1. Freeze the FastAPI backend into a single binary (PyInstaller).
#   2. Stage it as the Tauri sidecar, named with the Rust host target triple.
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

echo "==> Freezing backend (PyInstaller)…"
cd "$ROOT/backend"
.venv/bin/pyinstaller --clean --noconfirm sportsdash-backend.spec

echo "==> Staging backend as Tauri sidecar…"
BIN_DIR="$ROOT/frontend/src-tauri/binaries"
mkdir -p "$BIN_DIR"
cp "$ROOT/backend/dist/sportsdash-backend" "$BIN_DIR/sportsdash-backend-$TRIPLE"
chmod +x "$BIN_DIR/sportsdash-backend-$TRIPLE"

echo "==> Building SportsDash.app (Tauri)…"
cd "$ROOT/frontend"
bun install --frozen-lockfile
bun tauri build

echo "==> Done. Bundles in:"
echo "    $ROOT/frontend/src-tauri/target/release/bundle/"
