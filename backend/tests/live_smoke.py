"""Real-network canary — NOT part of the pytest suite (no test_ prefix).

For a live-data-only app, upstream schema drift is the failure mode the
recorded-fixture tests cannot catch. This script makes one real ESPN
call and one real TheSportsDB call and asserts they still parse into
non-empty domain objects. Run weekly (non-blocking) by
.github/workflows/live-smoke.yml:

    cd backend && python -m tests.live_smoke
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from app.models.domain import League, Sport
    from app.providers.espn import EspnProvider
    from app.services import stadiums, tsdb_client

    failures: list[str] = []

    espn = EspnProvider()
    league = League(
        id="nba",
        sport=Sport.BASKETBALL,
        name="NBA",
        provider="espn",
        provider_key="basketball/nba",
    )
    try:
        standings = await espn.get_standings(league)
        if not standings.rows:
            failures.append("espn: standings parsed to zero rows (schema drift?)")
        else:
            print(f"espn OK: standings parsed {len(standings.rows)} rows")
        games = await espn.get_live_games(league)
        print(f"espn OK: scoreboard parsed {len(games)} games (may be 0 off-season)")
    except Exception as exc:  # noqa: BLE001 — a canary reports, it doesn't crash
        failures.append(f"espn: {type(exc).__name__}: {exc}")
    finally:
        await espn.close()

    try:
        location = await stadiums.lookup_stadium("Arsenal", sport="soccer")
        if location is None or not location.venue:
            failures.append("tsdb: Arsenal stadium lookup returned no venue")
        else:
            print(f"tsdb OK: Arsenal -> {location.venue}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"tsdb: {type(exc).__name__}: {exc}")
    finally:
        await tsdb_client.close_client()

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
