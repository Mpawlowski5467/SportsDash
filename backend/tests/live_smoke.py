"""Real-network canary — NOT part of the pytest suite (no test_ prefix).

For a live-data-only app, upstream schema drift is the failure mode the
recorded-fixture tests cannot catch. This script makes one real ESPN
call and one real TheSportsDB call and asserts they still parse into
non-empty domain objects. Run weekly by
.github/workflows/live-smoke.yml, which fails the scheduled run on a
second consecutive failure but never gates a merge.

It also reports *which* fields drifted, not just pass/fail: one live
scoreboard (the first recorded league that has events today) is compared
path by path against the recorded replay fixtures in
``tests/fixtures/replay/``, which are trimmed to exactly the fields the
parsers read. A field every recorded poll carried that no live event has,
or a field whose JSON type changed, fails the run with its path named
(``tools.record_replay.shape_drift``):


    cd backend && python -m tests.live_smoke
"""

from __future__ import annotations

import asyncio
import sys


async def _drift_check(espn) -> tuple[list[str], list[str]]:
    """Field drift of one live scoreboard vs the recorded fixtures.

    Tries the recorded leagues in turn, one scoreboard call each and never
    faster than ``live_poll_seconds`` apart, and compares the first one
    with events today.  Off-season everywhere is "nothing to compare", not
    a failure.  Returns ``(failures, warnings)``.
    """
    from app.config import get_settings
    from app.providers.espn.common import _SCOREBOARD_TZ, _SITE_BASE
    from app.timeutil import utcnow
    from tools.record_replay import fixture_shape, load_fixture_events, shape_drift

    recorded = load_fixture_events()
    if not recorded:
        return ["drift: no replay fixtures to compare against"], []
    for i, (league_key, events) in enumerate(recorded.items()):
        if i:
            await asyncio.sleep(get_settings().live_poll_seconds)
        today = utcnow().astimezone(_SCOREBOARD_TZ).strftime("%Y%m%d")
        data = await espn._get_json(
            f"{_SITE_BASE}/{league_key}/scoreboard", params={"dates": today, "limit": "400"}
        )
        live = [e for e in data.get("events") or [] if isinstance(e, dict)]
        if not live:
            print(f"drift: {league_key} has no events today, trying the next league")
            continue
        drift = shape_drift(f"{league_key} scoreboard", fixture_shape(events), live)
        if not drift.failures:
            print(
                f"drift OK: {league_key} scoreboard, {len(live)} live events match "
                f"the shape of {len(events)} recorded polls"
            )
        return drift.failures, drift.warnings
    print("drift: no recorded league has events today — nothing to compare")
    return [], []


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
    try:
        drift_failures, drift_warnings = await _drift_check(espn)
        failures.extend(f"drift: {line}" for line in drift_failures)
        for line in drift_warnings:
            print(f"WARN drift: {line}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"drift: {type(exc).__name__}: {exc}")
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
