"""The replay recorder's trimming keeps everything the GameState path reads.

Replay fixtures store ESPN responses cut down to a handful of fields, so a
replay test only means something if a trimmed response parses to exactly
the state the full one does.  Checked here against the full recorded
fixtures, per sport, for both the scoreboard and the summary fallback.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.models.domain import GameState, League, Sport
from app.providers.espn import _parse_scoreboard, _parse_summary_state
from tools.record_replay import is_terminal, trim_scoreboard, trim_summary

FIXTURES = Path(__file__).parent / "fixtures"


def _league(sport: Sport, key: str) -> League:
    return League(id=key, sport=sport, name=key, provider="espn", provider_key=key)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _comparable(state: GameState | None) -> GameState | None:
    return None if state is None else replace(state, last_update=None)


@pytest.mark.parametrize(
    ("fixture", "sport"),
    [
        ("espn_scoreboard.json", Sport.BASKETBALL),
        ("espn_hockey_scoreboard.json", Sport.HOCKEY),
        ("espn_football_scoreboard.json", Sport.FOOTBALL),
    ],
)
def test_trimmed_scoreboard_parses_to_the_same_state(fixture: str, sport: Sport) -> None:
    data = _load(fixture)
    league = _league(sport, f"{sport.value}/fixture")
    full = {game.id: _comparable(game.state) for game in _parse_scoreboard(data, league)}
    assert full, "fixture parsed to no games"
    for game_id, state in full.items():
        trimmed = trim_scoreboard(data, game_id.removeprefix("espn:"))
        games = _parse_scoreboard(trimmed, league)
        assert [g.id for g in games] == [game_id]
        assert _comparable(games[0].state) == state


def test_trim_scoreboard_drops_other_events_and_unread_fields() -> None:
    data = _load("espn_scoreboard.json")
    event_id = str(data["events"][0]["id"])
    trimmed = trim_scoreboard(data, event_id)
    assert [e["id"] for e in trimmed["events"]] == [event_id]
    competition = trimmed["events"][0]["competitions"][0]
    assert set(competition) <= {"date", "status", "competitors"}
    assert len(json.dumps(trimmed)) < len(json.dumps(data["events"][0]))
    assert trim_scoreboard(data, "no-such-event") == {"events": []}


def test_trimmed_summary_parses_to_the_same_state() -> None:
    data = _load("espn_basketball_summary.json")
    league = _league(Sport.BASKETBALL, "basketball/fixture")
    event_id = str(data["header"]["id"])
    full = _parse_summary_state(data, league, event_id)
    assert full is not None
    trimmed = _parse_summary_state(trim_summary(data), league, event_id)
    assert _comparable(trimmed) == _comparable(full)


def test_trim_summary_without_header_parses_to_none() -> None:
    league = _league(Sport.BASKETBALL, "basketball/fixture")
    assert _parse_summary_state(trim_summary({}), league, "1") is None


@pytest.mark.parametrize(
    ("stype", "terminal"),
    [
        ({"state": "post", "name": "STATUS_FINAL"}, True),
        ({"state": "post", "name": "STATUS_POSTPONED"}, True),
        ({"state": "in", "name": "STATUS_SUSPENDED"}, True),
        ({"state": "in", "name": "STATUS_HALFTIME"}, False),
        ({"state": "pre", "name": "STATUS_SCHEDULED"}, False),
    ],
)
def test_is_terminal(stype: dict[str, str], terminal: bool) -> None:
    assert is_terminal({"type": stype}) is terminal


def test_recorded_fixtures_are_small_and_well_formed() -> None:
    """Every committed replay fixture stays well under 1 MB and is indexed."""
    replay = FIXTURES / "replay"
    files = sorted(replay.glob("*.json.gz"))
    if not files:
        pytest.skip("no replay fixtures recorded yet")
    index = {g["file"] for g in json.loads((replay / "index.json").read_text())["games"]}
    for path in files:
        assert path.stat().st_size < 1_000_000, path.name
        assert path.name in index, f"{path.name} missing from index.json"
        doc = json.loads(gzip.decompress(path.read_bytes()))
        assert doc["format"] == 1
        assert doc["snapshots"], path.name
