"""API-contract snapshot.

docs/CONTRACTS.md declares frontend/src/types.ts a mirror of
schemas.py, but nothing machine-enforced the boundary — a renamed field
compiled fine on both sides and failed only in the browser. This test
freezes the full OpenAPI schema; any route/schema change becomes an
explicit, reviewed diff.

To accept an intentional change:

    SPORTSDASH_UPDATE_OPENAPI=1 pytest tests/test_openapi_snapshot.py

then commit the updated tests/openapi_snapshot.json (and mirror the
change into frontend/src/types.ts).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "openapi_snapshot.json"


def test_openapi_matches_snapshot() -> None:
    from app.main import app

    spec = app.openapi()
    current = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    if os.environ.get("SPORTSDASH_UPDATE_OPENAPI") == "1":
        SNAPSHOT.write_text(current)

    assert SNAPSHOT.exists(), (
        "No OpenAPI snapshot committed yet — run with "
        "SPORTSDASH_UPDATE_OPENAPI=1 to create tests/openapi_snapshot.json"
    )
    stored = SNAPSHOT.read_text()
    assert current == stored, (
        "The API schema changed. If intentional, re-run with "
        "SPORTSDASH_UPDATE_OPENAPI=1, commit the snapshot diff, and mirror "
        "the change in frontend/src/types.ts (see docs/CONTRACTS.md)."
    )
