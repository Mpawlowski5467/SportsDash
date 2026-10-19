"""Record real ESPN games, pregame to final, as replay fixtures.

A developer tool, not part of the app: it runs against the live network,
so nothing under ``app/`` or the pytest suite imports its I/O half.  The
replay tests only read what it writes.

It polls exactly what ``live_tick`` polls, the way the provider polls it:

- one site-API ``/scoreboard`` call per league per tick, ``dates`` = today
  in US Eastern and ``limit=400`` (``EspnProvider.get_live_games``);
- the ``/summary?event=`` fallback, only when the game is missing from the
  scoreboard while live or overdue to start (``live_tick`` step 3);

through the same ``get_with_retry`` and outbound User-Agent, one request
at a time, every ``settings.live_poll_seconds`` (never faster).  A league
is only polled while one of its games is inside the live-poll window
(``live_lead_minutes`` before the start, until final).

Each response is trimmed to the fields the GameState parse path reads
(:func:`trim_event` / :func:`trim_summary`) and appended, with a
timestamp, to a per-game JSONL working file — so a crash or restart
resumes rather than losing the game.  Once a game is final, postponed or
canceled (plus a few identical trailing polls, which replay tests use to
prove an unchanged snapshot emits nothing), its sequence is gzipped into
``tests/fixtures/replay/<label>.json.gz`` and listed in ``index.json``.

    cd backend && python -m tools.record_replay \\
        --game baseball/mlb:401930515:mlb_2026-10-18_tb-cle \\
        --game hockey/nhl:401892533:nhl_2026-10-18_cbj-min
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("record_replay")

BACKEND = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "replay"
WORK_DIR = BACKEND / ".replay-work"
FORMAT_VERSION = 1

# Polls kept after a game reaches a terminal state: identical snapshots the
# replay tests feed through diff_states to prove they emit nothing.
TRAILING_POLLS = 3
# Give up on a game this long after its scheduled start (the live-poll
# gate's own max_age), finalizing whatever was recorded.
MAX_AGE = timedelta(hours=8)
# Pause between two requests inside one tick, so a tick is never a burst.
REQUEST_SPACING_SECONDS = 1.0

_TERMINAL_STATES = ("post",)
_TERMINAL_NAMES = ("POSTPON", "CANCEL", "SUSPEND")


# ---------------------------------------------------------------------------
# Trimming (pure; unit-tested against the full fixtures)
# ---------------------------------------------------------------------------

_STATUS_TYPE_KEYS = ("state", "name", "shortDetail", "detail", "altDetail")
_TEAM_KEYS = ("id", "displayName", "abbreviation")


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source}


def trim_status(status: Any) -> dict[str, Any] | None:
    """``status`` cut to what ``_build_state`` reads (period, clock, type)."""
    if not isinstance(status, dict):
        return None
    trimmed = _pick(status, ("period", "displayClock"))
    trimmed["type"] = _pick(status.get("type"), _STATUS_TYPE_KEYS)
    return trimmed


def _trim_competitor(competitor: Any) -> dict[str, Any]:
    trimmed = _pick(competitor, ("homeAway", "score"))
    trimmed["team"] = _pick(competitor.get("team"), _TEAM_KEYS)
    linescores = competitor.get("linescores")
    if isinstance(linescores, list):
        # Only the count is read (periods played on a final with no
        # ``status.period``), but keep the values so a fixture stays readable.
        trimmed["linescores"] = [_pick(entry, ("value",)) for entry in linescores]
    return trimmed


def trim_competition(competition: Any) -> dict[str, Any]:
    if not isinstance(competition, dict):
        return {}
    trimmed = _pick(competition, ("date",))
    status = trim_status(competition.get("status"))
    if status is not None:
        trimmed["status"] = status
    competitors = competition.get("competitors")
    if isinstance(competitors, list):
        trimmed["competitors"] = [_trim_competitor(c) for c in competitors if isinstance(c, dict)]
    return trimmed


def trim_event(event: dict[str, Any]) -> dict[str, Any]:
    """A scoreboard event cut to what ``_parse_event`` needs for a GameState."""
    trimmed = _pick(event, ("id", "date", "name", "shortName"))
    status = trim_status(event.get("status"))
    if status is not None:
        trimmed["status"] = status
    competitions = event.get("competitions")
    if isinstance(competitions, list) and competitions:
        trimmed["competitions"] = [trim_competition(competitions[0])]
    return trimmed


def trim_scoreboard(data: Any, event_id: str) -> dict[str, Any]:
    """``{"events": [the one event]}``, or ``{"events": []}`` when it is absent."""
    events = data.get("events") if isinstance(data, dict) else None
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and str(event.get("id")) == event_id:
            return {"events": [trim_event(event)]}
    return {"events": []}


def trim_summary(data: Any) -> dict[str, Any]:
    """A summary cut to ``header.competitions[0]``, which ``_parse_summary_state`` reads."""
    header = data.get("header") if isinstance(data, dict) else None
    competitions = header.get("competitions") if isinstance(header, dict) else None
    if not isinstance(competitions, list) or not competitions:
        return {"header": {}}
    return {"header": {"competitions": [trim_competition(competitions[0])]}}


def _event_status(payload: dict[str, Any]) -> dict[str, Any]:
    """The status block of a trimmed scoreboard or summary payload, or ``{}``."""
    if "events" in payload:
        events = payload["events"]
        if not events:
            return {}
        competitions = events[0].get("competitions") or [{}]
        return competitions[0].get("status") or events[0].get("status") or {}
    competitions = payload.get("header", {}).get("competitions") or [{}]
    return competitions[0].get("status") or {}


def is_terminal(status: dict[str, Any]) -> bool:
    stype = status.get("type") or {}
    name = str(stype.get("name") or "").upper()
    return stype.get("state") in _TERMINAL_STATES or any(t in name for t in _TERMINAL_NAMES)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


@dataclass
class Target:
    league_key: str  # ESPN sport/league fragment, e.g. "baseball/mlb"
    event_id: str
    label: str  # fixture basename: <sport>_<date>_<short-label>
    start_time: datetime | None = None
    last_state: str | None = None  # "pre" / "in" / "post" from the latest snapshot
    trailing: int = 0
    done: bool = False
    snapshots: int = field(default=0)

    @property
    def work_file(self) -> Path:
        return WORK_DIR / f"{self.label}.jsonl"


def parse_target(spec: str) -> Target:
    league_key, event_id, label = spec.rsplit(":", 2)
    return Target(league_key=league_key, event_id=event_id, label=label)


def _now() -> datetime:
    from app.timeutil import utcnow

    return utcnow()


def _append(target: Target, record: dict[str, Any]) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with target.work_file.open("a") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    target.snapshots += 1


def _resume(target: Target) -> None:
    """Pick up a working file left by an earlier run of the same game."""
    if not target.work_file.exists():
        return
    lines = target.work_file.read_text().splitlines()
    target.snapshots = len(lines)
    for line in reversed(lines):
        record = json.loads(line)
        if "payload" in record:
            target.last_state = (_event_status(record["payload"]).get("type") or {}).get("state")
            break
    logger.info("%s: resuming after %d recorded polls", target.label, target.snapshots)


def _in_window(target: Target, now: datetime, lead: timedelta) -> bool:
    if target.start_time is None:
        return True  # unknown until the first scoreboard poll
    return target.start_time - lead <= now <= target.start_time + MAX_AGE


async def _get(client, url: str, params: dict[str, str]):
    from app.config import get_settings
    from app.providers.http_util import get_with_retry

    settings = get_settings()
    return await get_with_retry(
        client,
        url,
        params=params,
        max_retries=settings.provider_max_retries,
        backoff_base=settings.provider_backoff_base,
        label="record_replay",
    )


def _observe(target: Target, payload: dict[str, Any]) -> None:
    status = _event_status(payload)
    if not status:
        return
    target.last_state = (status.get("type") or {}).get("state")
    if is_terminal(status) or target.trailing:
        target.trailing += 1
        if target.trailing > TRAILING_POLLS:
            target.done = True


async def _poll_league(client, league_key: str, targets: list[Target]) -> None:
    from app.providers.espn.common import _SCOREBOARD_TZ, _SITE_BASE, _parse_espn_datetime

    url = f"{_SITE_BASE}/{league_key}/scoreboard"
    today = _now().astimezone(_SCOREBOARD_TZ).strftime("%Y%m%d")
    stamp = _now().isoformat()
    try:
        response = await _get(client, url, {"dates": today, "limit": "400"})
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # recorded, like live_tick's skip-on-failure
        for target in targets:
            _append(target, {"t": stamp, "source": "scoreboard", "error": repr(exc)[:300]})
        logger.warning("%s scoreboard failed: %r", league_key, exc)
        return

    for target in targets:
        payload = trim_scoreboard(data, target.event_id)
        if payload["events"] and target.start_time is None:
            competition = payload["events"][0].get("competitions", [{}])[0]
            target.start_time = _parse_espn_datetime(competition.get("date"))
        _append(target, {"t": stamp, "source": "scoreboard", "payload": payload})
        if payload["events"]:
            _observe(target, payload)
            continue
        # live_tick's fallback: missing from the scoreboard while live, or
        # overdue to start (listed under a different scoreboard day).
        overdue = (
            target.last_state in (None, "pre")
            and target.start_time is not None
            and target.start_time <= _now()
        )
        if target.last_state != "in" and not overdue:
            continue
        await asyncio.sleep(REQUEST_SPACING_SECONDS)
        stamp = _now().isoformat()
        try:
            response = await _get(
                client, f"{_SITE_BASE}/{league_key}/summary", {"event": target.event_id}
            )
            if response.status_code == 404:
                _append(target, {"t": stamp, "source": "summary", "status": 404})
                continue
            response.raise_for_status()
            summary = trim_summary(response.json())
        except Exception as exc:
            _append(target, {"t": stamp, "source": "summary", "error": repr(exc)[:300]})
            continue
        _append(target, {"t": stamp, "source": "summary", "payload": summary})
        _observe(target, summary)


def finalize(target: Target, poll_seconds: int, note: str = "") -> Path:
    """Gzip a game's working file into its fixture and refresh index.json."""
    from app.useragent import user_agent

    records = [json.loads(line) for line in target.work_file.read_text().splitlines()]
    doc = {
        "format": FORMAT_VERSION,
        "label": target.label,
        "league_key": target.league_key,
        "event_id": target.event_id,
        "poll_seconds": poll_seconds,
        "user_agent": user_agent(),
        "snapshots": records,
    }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / f"{target.label}.json.gz"
    raw = json.dumps(doc, separators=(",", ":")).encode()
    out.write_bytes(gzip.compress(raw, mtime=0))
    _update_index(target, records, out, poll_seconds, note)
    logger.info(
        "%s: wrote %s (%d polls, %d bytes)",
        target.label,
        out.name,
        len(records),
        out.stat().st_size,
    )
    return out


def _update_index(
    target: Target, records: list[dict[str, Any]], out: Path, poll_seconds: int, note: str
) -> None:
    index_path = FIXTURE_DIR / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"games": []}
    payloads = [r["payload"] for r in records if "payload" in r and _event_status(r["payload"])]
    first_event = next((p["events"][0] for p in payloads if p.get("events")), {})
    last_status = _event_status(payloads[-1]) if payloads else {}
    times = [datetime.fromisoformat(r["t"]) for r in records]
    # From the second poll on: the first is the start-time lookup, after
    # which the recorder idles until the live-poll window opens.
    gaps = [(b - a).total_seconds() for a, b in zip(times[1:], times[2:], strict=False)]
    entry = {
        "file": out.name,
        "league_key": target.league_key,
        "event_id": target.event_id,
        "matchup": first_event.get("shortName") or first_event.get("name"),
        "start_time": (first_event.get("competitions") or [{}])[0].get("date"),
        "recorded_from": records[0]["t"] if records else None,
        "recorded_to": records[-1]["t"] if records else None,
        "polls": len(records),
        "errors": sum(1 for r in records if "error" in r),
        "summary_fallbacks": sum(1 for r in records if r.get("source") == "summary"),
        # Polls more than twice the interval apart: the recorder (or the
        # machine) was paused, so the replay skips whatever happened then.
        "gaps_over_2x_poll": sum(1 for g in gaps if g > 2 * poll_seconds),
        "final_detail": (last_status.get("type") or {}).get("detail"),
        "bytes": out.stat().st_size,
        "notes": note,
    }
    games = [g for g in index["games"] if g.get("file") != out.name] + [entry]
    index["games"] = sorted(games, key=lambda g: g["file"])
    index_path.write_text(json.dumps(index, indent=2) + "\n")


async def record(targets: list[Target], poll_seconds: int) -> None:
    import httpx

    from app.config import get_settings
    from app.useragent import headers

    settings = get_settings()
    lead = timedelta(minutes=settings.live_lead_minutes)
    for target in targets:
        _resume(target)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.provider_timeout_seconds),
        headers=headers(),
        follow_redirects=True,
    ) as client:
        while not all(t.done for t in targets):
            tick_started = asyncio.get_running_loop().time()
            now = _now()
            by_league: dict[str, list[Target]] = {}
            for target in targets:
                if target.done:
                    continue
                if target.start_time is not None and now > target.start_time + MAX_AGE:
                    logger.warning("%s: past max age, finalizing as recorded", target.label)
                    target.done = True
                    finalize(target, poll_seconds, note="stopped at max age before a final")
                    continue
                if _in_window(target, now, lead):
                    by_league.setdefault(target.league_key, []).append(target)
            for i, (league_key, league_targets) in enumerate(by_league.items()):
                if i:
                    await asyncio.sleep(REQUEST_SPACING_SECONDS)
                await _poll_league(client, league_key, league_targets)
                for target in league_targets:
                    if target.done:
                        finalize(target, poll_seconds)
            elapsed = asyncio.get_running_loop().time() - tick_started
            await asyncio.sleep(max(0.0, poll_seconds - elapsed))


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--game",
        action="append",
        required=True,
        metavar="LEAGUE_KEY:EVENT_ID:LABEL",
        help="e.g. baseball/mlb:401930515:mlb_2026-10-18_tb-cle (repeatable)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="tick interval; defaults to, and may not undercut, settings.live_poll_seconds",
    )
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help="gzip existing working files without polling (e.g. after a stopped run)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    floor = get_settings().live_poll_seconds
    poll_seconds = args.poll_seconds or floor
    if poll_seconds < floor:
        parser.error(f"--poll-seconds may not be faster than live_poll_seconds ({floor}s)")
    targets = [parse_target(spec) for spec in args.game]
    if args.finalize_only:
        for target in targets:
            finalize(target, poll_seconds, note="finalized from a stopped run")
        return 0
    asyncio.run(record(targets, poll_seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
