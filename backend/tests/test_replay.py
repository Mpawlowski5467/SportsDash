"""Replay recorded real games through the live-poll path, pregame to final.

Each fixture under ``fixtures/replay/`` is one real game as
``tools/record_replay.py`` polled it: every scoreboard (and summary
fallback) response, in order, trimmed to the fields the parse path reads.
:func:`replay` feeds them through exactly what ``live_tick`` does with
them — the real ESPN parsers into a :class:`GameState`, then
:func:`diff_states` against the previously stored state — and collects the
events it would notify.

The expected event list for each game is written by hand from what
actually happened in that game (how many periods were played, overtime,
which breaks occurred), never by snapshotting the detector's output, so a
detector change that disagrees with reality fails here.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.models.domain import (
    SPORTS_WITH_BREAKS,
    GameEvent,
    GamePhase,
    GameState,
    League,
    Sport,
)
from app.providers.espn import _parse_scoreboard, _parse_summary_state
from app.services.events import diff_states

REPLAY_DIR = Path(__file__).parent / "fixtures" / "replay"

_SPORTS = {
    "basketball": Sport.BASKETBALL,
    "football": Sport.FOOTBALL,
    "hockey": Sport.HOCKEY,
    "baseball": Sport.BASEBALL,
    "soccer": Sport.SOCCER,
}

# Phases the live-poll gate keeps polling (repository.games_needing_live_poll);
# once the stored state leaves them, live_tick stops looking at the game.
_POLLED_PHASES = (GamePhase.SCHEDULED, GamePhase.IN_PROGRESS)

# Hand-written from what happened in each game, as dedupe-key suffixes in
# emission order ("start", "period:N", "intermission:N", "final").  Every
# recorded fixture must have an entry here.  Rulings (owner, 2026-10-18):
# baseball half-inning breaks (Mid/End N) are not intermissions; every NFL
# quarter break is one, whether or not a 45s poll happened to see it.
_NFL_REGULATION = [
    "start",
    "intermission:1",
    "period:2",
    "intermission:2",  # halftime
    "period:3",
    "intermission:3",
    "period:4",
    "final",
]
_NHL_REGULATION = [
    "start",
    "intermission:1",
    "period:2",
    "intermission:2",
    "period:3",
    "final",
]
EXPECTED: dict[str, list[str]] = {
    # ALCS Game 5, TB @ CLE: nine innings, no extras.
    "mlb_2026-10-18_tb-cle-alcs5.json.gz": [
        "start",
        *(f"period:{n}" for n in range(2, 10)),
        "final",
    ],
    # MLS, CLB @ CIN: two halves, 9 + 5 minutes of stoppage time, no extra time.
    "mls_2026-10-18_clb-cin.json.gz": ["start", "intermission:1", "period:2", "final"],
    # NFL, all four decided in regulation (Final, not Final/OT).
    "nfl_2026-10-18_ari-lar.json.gz": _NFL_REGULATION,
    "nfl_2026-10-18_buf-lv.json.gz": _NFL_REGULATION,
    "nfl_2026-10-18_dal-gb.json.gz": _NFL_REGULATION,
    "nfl_2026-10-18_lac-kc.json.gz": _NFL_REGULATION,
    # NHL, CBJ @ MIN: decided in regulation, so the end of the 3rd is the
    # end of the game, not a break.
    "nhl_2026-10-18_cbj-min.json.gz": _NHL_REGULATION,
    # NHL, CAR @ CGY: 2-2 after two, decided 3-2 in regulation.
    "nhl_2026-10-18_car-cgy.json.gz": _NHL_REGULATION,
}


@dataclass(frozen=True)
class Replay:
    states: list[GameState]  # every parsed snapshot, in poll order
    events: list[GameEvent]  # what live_tick would have emitted


def _fixture_files() -> list[Path]:
    return sorted(REPLAY_DIR.glob("*.json.gz"))


@cache
def _load(name: str) -> dict[str, Any]:
    return json.loads(gzip.decompress((REPLAY_DIR / name).read_bytes()))


def _league(doc: dict[str, Any]) -> League:
    key = doc["league_key"]
    return League(
        id=key, sport=_SPORTS[key.split("/", 1)[0]], name=key, provider="espn", provider_key=key
    )


def _parse_snapshot(record: dict[str, Any], league: League, event_id: str) -> GameState | None:
    """The state live_tick would get from one recorded poll, or None (skipped)."""
    payload = record.get("payload")
    if payload is None:  # transport error or a 404 summary: live_tick skips the game
        return None
    if record["source"] == "summary":
        return _parse_summary_state(payload, league, event_id)
    game_id = f"espn:{event_id}"
    games = {game.id: game for game in _parse_scoreboard(payload, league)}
    game = games.get(game_id)
    return game.state if game is not None else None


def replay(doc: dict[str, Any]) -> Replay:
    league = _league(doc)
    event_id = doc["event_id"]
    states = [
        state
        for record in doc["snapshots"]
        if (state := _parse_snapshot(record, league, event_id)) is not None
    ]
    # What the schedule refresh stores before the first live poll.
    stored = GameState(
        game_id=f"espn:{event_id}", phase=GamePhase.SCHEDULED, home_score=0, away_score=0
    )
    events: list[GameEvent] = []
    for state in states:
        if stored.phase not in _POLLED_PHASES:
            break
        events.extend(
            diff_states(
                stored,
                state,
                home_name="Home",
                away_name="Away",
                breaks=league.sport in SPORTS_WITH_BREAKS,
            )
        )
        stored = state
    return Replay(states=states, events=events)


def _suffix(event: GameEvent) -> str:
    return event.dedupe_key.split(":", 2)[2]


def _same_but_score(a: GameState, b: GameState) -> bool:
    return replace(a, home_score=0, away_score=0, last_update=None) == replace(
        b, home_score=0, away_score=0, last_update=None
    )


_FILES = [path.name for path in _fixture_files()]
_needs_fixtures = pytest.mark.skipif(not _FILES, reason="no replay fixtures recorded yet")


@_needs_fixtures
@pytest.mark.parametrize("name", _FILES)
def test_every_fixture_has_a_hand_written_expectation(name: str) -> None:
    assert name in EXPECTED, f"{name}: write its expected event list (by hand) in EXPECTED"


@_needs_fixtures
@pytest.mark.parametrize("name", _FILES)
def test_replay_matches_what_happened(name: str) -> None:
    events = replay(_load(name)).events
    assert [_suffix(e) for e in events] == EXPECTED[name]


@_needs_fixtures
@pytest.mark.parametrize("name", _FILES)
def test_replay_never_fires_an_event_twice(name: str) -> None:
    doc = _load(name)
    keys = [e.dedupe_key for e in replay(doc).events]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"{name}: dedupe keys fired more than once: {duplicates}"
    prefix = f"espn:{doc['event_id']}:"
    assert all(k.startswith(prefix) for k in keys)


@_needs_fixtures
@pytest.mark.parametrize("name", _FILES)
def test_replay_event_order(name: str) -> None:
    """GAME_START opens, FINAL closes, periods only move forward."""
    suffixes = [_suffix(e) for e in replay(_load(name)).events]
    if not suffixes:
        return  # e.g. a game postponed before it started
    if "start" in suffixes:
        assert suffixes[0] == "start", suffixes
    if "final" in suffixes:
        assert suffixes[-1] == "final", suffixes
    periods = [int(s.split(":")[1]) for s in suffixes if s.startswith("period:")]
    assert periods == sorted(periods), suffixes


@_needs_fixtures
@pytest.mark.parametrize("name", _FILES)
def test_identical_and_score_only_snapshots_fire_nothing(name: str) -> None:
    states = replay(_load(name)).states
    for state in states:
        assert diff_states(state, state, home_name="H", away_name="A") == []
    checked = 0
    for prev, new in zip(states, states[1:], strict=False):
        if _same_but_score(prev, new):
            assert diff_states(prev, new, home_name="H", away_name="A") == [], (prev, new)
            checked += 1
    assert checked, f"{name}: no unchanged/score-only pairs to check"
