# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec freezing the SportsDash backend into a single binary.

Produces a self-contained `sportsdash-backend` executable (no system
Python needed) that the Tauri desktop app spawns as a sidecar. Built
with:  pyinstaller sportsdash-backend.spec  (run from the backend dir).

Several deps import submodules dynamically and would otherwise be missed
by static analysis:
  * uvicorn   — loop/protocol/lifespan implementations chosen at runtime
  * apscheduler — executors, jobstores, and trigger plugins
  * sqlalchemy.dialects.sqlite + aiosqlite — the async sqlite dialect
  * feedparser — pulls in sgmllib3k
The whole `app` package is collected so every route/provider/service is
present regardless of import path.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("config/teams.yaml", "config")]
binaries = []
hiddenimports = []

for pkg in ("uvicorn", "apscheduler", "feedparser", "aiosqlite"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("sqlalchemy.dialects")
hiddenimports += collect_submodules("app")

a = Analysis(
    ["desktop_server.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sportsdash-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
