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
# Re-run this whenever the backend or frontend changes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"

TRIPLE="$(rustc -Vv | sed -n 's/^host: //p')"
echo "==> Host target triple: $TRIPLE"

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
bun tauri build

echo "==> Done. Bundles in:"
echo "    $ROOT/frontend/src-tauri/target/release/bundle/"
