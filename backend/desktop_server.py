"""Entrypoint for the bundled desktop backend (PyInstaller / Tauri sidecar).

The macOS app bundle is read-only, so this entrypoint redirects all
writable state — the SQLite database — into the OS user-data directory
(``~/Library/Application Support/SportsDash`` on macOS) and reads the
followed-teams YAML from the frozen bundle (PyInstaller's ``_MEIPASS``)
when running frozen, or from the repo when running from source.

Environment is configured *before* ``app.main`` is imported because the
CORS middleware reads ``get_settings()`` at import time, and the
``@lru_cache`` would otherwise pin defaults. Each ``setdefault`` lets an
explicit ``SPORTSDASH_*`` env var (e.g. from the Tauri shell) win.

Run directly for a local smoke test::

    python desktop_server.py            # serves on 127.0.0.1:8765
    SPORTSDASH_PORT=9000 python desktop_server.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_dir() -> Path:
    """Directory holding bundled data files (config/teams.yaml).

    PyInstaller unpacks ``datas`` under ``sys._MEIPASS`` at runtime; from
    source it is this file's directory (the ``backend`` package root).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    """Per-user writable directory for the database and any future state."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "SportsDash"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home()) / "SportsDash"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "SportsDash"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _configure_environment() -> None:
    data_dir = _data_dir()
    resources = _resource_dir()

    db_path = data_dir / "sportsdash.db"
    # Four slashes => absolute path for SQLAlchemy's sqlite URL form.
    os.environ.setdefault(
        "SPORTSDASH_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    os.environ.setdefault(
        "SPORTSDASH_TEAMS_CONFIG_PATH", str(resources / "config" / "teams.yaml")
    )
    # The Tauri webview loads from these origins (v2 custom-protocol hosts);
    # 127.0.0.1:1420 covers `tauri dev`. fetch() sends no credentials, so a
    # fixed allow-list is all the CORS we need.
    os.environ.setdefault(
        "SPORTSDASH_CORS_ORIGINS",
        '["tauri://localhost","http://tauri.localhost","http://localhost:1420"]',
    )
    # Desktop app is single-user on the loopback; the self-hosted ntfy/redis
    # bits stay off unless the user opts in via real env vars.
    os.environ.setdefault("SPORTSDASH_NOTIFICATIONS_ENABLED", "false")


def main() -> None:
    _configure_environment()

    host = os.environ.get("SPORTSDASH_HOST", "127.0.0.1")
    port = int(os.environ.get("SPORTSDASH_PORT", "8765"))

    # Imported only after the environment is in place (see module docstring).
    import uvicorn

    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
