"""The live canary's drift report names the fields that changed.

``tests/live_smoke.py`` compares one live scoreboard against the shape of
the recorded replay fixtures (``tools.record_replay.shape_drift``).  These
tests drive that comparison with recorded events edited the ways upstream
drift actually happens — a field removed, a field retyped — and check the
report says which, without touching the network.
"""

from __future__ import annotations

import copy

import pytest

from tools.record_replay import field_paths, fixture_shape, load_fixture_events, shape_drift


@pytest.fixture(scope="module")
def nfl_events() -> list[dict]:
    events = load_fixture_events().get("football/nfl")
    if not events:
        pytest.skip("no NFL replay fixture recorded")
    return events


def _live(events: list[dict], count: int = 3) -> list[dict]:
    """A few recorded events standing in for one live scoreboard."""
    return copy.deepcopy(events[-count:])


def test_field_paths_collapse_list_positions() -> None:
    paths = field_paths({"a": [{"b": "x"}, {"b": 2}], "c": None, "d": True})
    assert paths == {"a[].b": {"string", "number"}, "c": {"null"}, "d": {"boolean"}}


def test_recorded_events_have_no_drift(nfl_events: list[dict]) -> None:
    drift = shape_drift("nfl", fixture_shape(nfl_events), _live(nfl_events))
    assert drift.failures == [] and drift.warnings == []


def test_extra_live_fields_are_not_drift(nfl_events: list[dict]) -> None:
    live = _live(nfl_events)
    for event in live:
        event["competitions"][0]["odds"] = [{"details": "KC -3.5"}]
        event["status"]["type"]["altDetail"] = "OT"
    assert shape_drift("nfl", fixture_shape(nfl_events), live).failures == []


def test_a_field_removed_everywhere_is_named(nfl_events: list[dict]) -> None:
    live = _live(nfl_events)
    for event in live:
        del event["competitions"][0]["status"]["type"]["shortDetail"]
    drift = shape_drift("football/nfl scoreboard", fixture_shape(nfl_events), live)
    assert drift.failures == [
        "football/nfl scoreboard: competitions[].status.type.shortDetail missing in 3/3 "
        f"live events (was string in {len(nfl_events)}/{len(nfl_events)} recorded polls)"
    ]


def test_a_field_missing_from_some_events_only_warns(nfl_events: list[dict]) -> None:
    live = _live(nfl_events)
    del live[0]["competitions"][0]["date"]
    drift = shape_drift("nfl", fixture_shape(nfl_events), live)
    assert drift.failures == []
    assert [w.split(" missing")[0] for w in drift.warnings] == ["nfl: competitions[].date"]


def test_a_retyped_field_is_named(nfl_events: list[dict]) -> None:
    live = _live(nfl_events)
    for event in live:
        for competitor in event["competitions"][0]["competitors"]:
            competitor["score"] = int(competitor["score"])
    drift = shape_drift("nfl", fixture_shape(nfl_events), live)
    assert drift.failures == ["nfl: competitions[].competitors[].score is now number (was string)"]


def test_optional_fields_may_be_absent(nfl_events: list[dict]) -> None:
    """Linescores appear only once a game is under way: never required."""
    shape = fixture_shape(nfl_events)
    path = "competitions[].competitors[].linescores[].value"
    assert path in shape.seen and not shape.required(path)
    live = _live(nfl_events)
    for event in live:
        for competitor in event["competitions"][0]["competitors"]:
            competitor.pop("linescores", None)
    assert shape_drift("nfl", shape, live).failures == []


def test_no_live_events_is_nothing_to_compare(nfl_events: list[dict]) -> None:
    drift = shape_drift("nfl", fixture_shape(nfl_events), [])
    assert drift.failures == [] and drift.warnings == []
