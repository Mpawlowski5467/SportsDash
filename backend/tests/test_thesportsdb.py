"""Unit tests for the pure TheSportsDB payload parsers and provider.

All payloads are fictional TheSportsDB-shaped JSON; no network I/O
anywhere.  TheSportsDB's free tier is sparse and rate-limited, so the
provider must degrade to empty/None on failure and never raise out — the
async tests exercise that contract by monkeypatching ``_get_json``.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models.domain import GamePhase, League, PlayerStatus, Sport, Team
from app.providers.thesportsdb import (
    TheSportsDbProvider,
    _build_state,
    _map_phase,
    _parse_event,
    _parse_events,
    _parse_event_datetime,
    _parse_player,
    _parse_roster,
    _parse_standings,
    _parse_team_location,
)

FIXTURES = Path(__file__).parent / "fixtures"

VOLLEYBALL_LEAGUE = League(
    id="tidewater-volleyball",
    sport=Sport.VOLLEYBALL,
    name="Tidewater Volleyball League",
    provider="thesportsdb",
    provider_key="9001",
)
# A followed team: TeamORM.provider_key is the TheSportsDB team id.
SPIKERS = Team(
    id="tidewater-saltmarsh-spikers",
    league_id=VOLLEYBALL_LEAGUE.id,
    name="Saltmarsh Spikers",
    abbreviation="SAL",
    provider_key="70101",
)


@pytest.fixture(scope="module")
def events_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_volleyball_events.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def event_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_volleyball_event.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def table_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_volleyball_table.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def players_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_volleyball_players.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def team_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_volleyball_team.json").open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Time parsing (the UTC assumption)
# ---------------------------------------------------------------------------

def test_parse_event_datetime_combines_date_and_time_as_utc() -> None:
    event = {"dateEvent": "2026-06-09", "strTime": "18:00:00"}
    parsed = _parse_event_datetime(event)
    assert parsed == datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)


def test_parse_event_datetime_falls_back_to_timestamp() -> None:
    event = {"dateEvent": "", "strTime": "", "strTimestamp": "2026-06-09T18:00:00"}
    parsed = _parse_event_datetime(event)
    assert parsed == datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)


def test_parse_event_datetime_missing_time_is_midnight() -> None:
    event = {"dateEvent": "2026-06-09"}
    parsed = _parse_event_datetime(event)
    assert parsed == datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_event_datetime_missing_date_is_none() -> None:
    assert _parse_event_datetime({"dateEvent": "", "strTime": "18:00:00"}) is None
    assert _parse_event_datetime({}) is None


# ---------------------------------------------------------------------------
# Phase mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("FT", GamePhase.FINAL),
        ("Match Finished", GamePhase.FINAL),
        ("Finished", GamePhase.FINAL),
        ("NS", GamePhase.SCHEDULED),
        ("Not Started", GamePhase.SCHEDULED),
        ("Set 3", GamePhase.IN_PROGRESS),
        ("LIVE", GamePhase.IN_PROGRESS),
    ],
)
def test_map_phase_from_status(status: str, expected: GamePhase) -> None:
    assert _map_phase({"strStatus": status}) is expected


def test_map_phase_empty_status_infers_from_score() -> None:
    # No status, but a recorded score => the match has ended.
    assert _map_phase({"strStatus": "", "intHomeScore": "3", "intAwayScore": "1"}) is (
        GamePhase.FINAL
    )
    # No status, no score => not yet played.
    assert _map_phase({"strStatus": "", "intHomeScore": None}) is GamePhase.SCHEDULED


# ---------------------------------------------------------------------------
# State building: sets won, "Set n" labels, no clock
# ---------------------------------------------------------------------------

def test_build_state_final_three_one() -> None:
    state = _build_state(
        "thesportsdb:3300001",
        {"strStatus": "FT", "intHomeScore": "3", "intAwayScore": "1"},
    )
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (3, 1)
    # Four sets were played (3 + 1); the last decided set is the period.
    assert state.period == 4
    assert state.period_label == "Set 4"
    assert state.clock is None
    assert state.is_intermission is False


def test_build_state_scheduled_has_zero_period() -> None:
    state = _build_state(
        "thesportsdb:3300002",
        {"strStatus": "NS", "intHomeScore": None, "intAwayScore": None},
    )
    assert state.phase is GamePhase.SCHEDULED
    assert (state.home_score, state.away_score) == (0, 0)
    assert state.period == 0
    assert state.period_label == ""
    assert state.clock is None


def test_build_state_in_progress_uses_set_in_play() -> None:
    # 2-1 in progress => the fourth set is underway.
    state = _build_state(
        "thesportsdb:3300003",
        {"strStatus": "Set 4", "strProgress": "4", "intHomeScore": "2", "intAwayScore": "1"},
    )
    assert state.phase is GamePhase.IN_PROGRESS
    assert (state.home_score, state.away_score) == (2, 1)
    assert state.period == 4
    assert state.period_label == "Set 4"
    assert state.clock is None


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def test_parse_events_finished_match(events_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_events(events_data, VOLLEYBALL_LEAGUE)}
    game = games["thesportsdb:3300001"]
    assert game.league_id == VOLLEYBALL_LEAGUE.id
    assert game.home_name == "Saltmarsh Spikers"
    assert game.away_name == "Harborline Setters"
    assert game.venue == "Saltmarsh Hall"
    assert game.start_time == datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)
    # No team context: internal ids stay unset.
    assert game.home_team_id is None and game.away_team_id is None
    state = game.state
    assert state is not None
    assert state.game_id == "thesportsdb:3300001"
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (3, 1)
    assert state.period_label == "Set 4"


def test_parse_events_scheduled_match(events_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_events(events_data, VOLLEYBALL_LEAGUE)}
    game = games["thesportsdb:3300002"]
    assert game.start_time == datetime(2026, 6, 18, 19, 30, tzinfo=timezone.utc)
    state = game.state
    assert state is not None
    assert state.phase is GamePhase.SCHEDULED
    assert (state.home_score, state.away_score) == (0, 0)
    assert state.period == 0
    assert state.period_label == ""


def test_parse_events_skips_malformed_event(events_data: dict[str, Any]) -> None:
    """The dateless event (3300099) is skipped; three valid games remain."""
    games = _parse_events(events_data, VOLLEYBALL_LEAGUE)
    assert sorted(g.id for g in games) == [
        "thesportsdb:3300001",
        "thesportsdb:3300002",
        "thesportsdb:3300003",
    ]


def test_parse_events_tags_followed_team_side(events_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_events(events_data, VOLLEYBALL_LEAGUE, SPIKERS)}
    # The Spikers are home in 3300001 and away in 3300002.
    assert games["thesportsdb:3300001"].home_team_id == SPIKERS.id
    assert games["thesportsdb:3300001"].away_team_id is None
    assert games["thesportsdb:3300002"].away_team_id == SPIKERS.id
    assert games["thesportsdb:3300002"].home_team_id is None


def test_parse_events_tolerates_malformed_payloads() -> None:
    assert _parse_events(None, VOLLEYBALL_LEAGUE) == []
    assert _parse_events({}, VOLLEYBALL_LEAGUE) == []
    assert _parse_events({"events": None}, VOLLEYBALL_LEAGUE) == []
    assert _parse_events({"events": "nope"}, VOLLEYBALL_LEAGUE) == []
    assert _parse_event("not-a-dict", VOLLEYBALL_LEAGUE) is None


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def test_parse_standings_wins_losses_points(table_data: dict[str, Any]) -> None:
    standings = _parse_standings(table_data, VOLLEYBALL_LEAGUE, "2026")
    assert standings.league_id == VOLLEYBALL_LEAGUE.id
    assert standings.season == "2026"
    # Re-ranked by provider rank; the string junk entry is skipped.
    assert [(row.rank, row.team_name) for row in standings.rows] == [
        (1, "Pellwick Aces"),
        (2, "Saltmarsh Spikers"),
        (3, "Harborline Setters"),
    ]
    top = standings.rows[0]
    assert (top.wins, top.losses, top.points) == (9, 1, 27)
    # Volleyball tables are flat: no conference groups.
    assert all(row.group is None for row in standings.rows)


def test_parse_standings_tags_followed_team(table_data: dict[str, Any]) -> None:
    standings = _parse_standings(table_data, VOLLEYBALL_LEAGUE, "2026", SPIKERS)
    by_name = {row.team_name: row for row in standings.rows}
    assert by_name["Saltmarsh Spikers"].team_id == SPIKERS.id
    assert by_name["Pellwick Aces"].team_id is None


def test_parse_standings_empty_table_is_empty() -> None:
    empty = _parse_standings({}, VOLLEYBALL_LEAGUE, "2026")
    assert empty.rows == ()
    assert _parse_standings({"table": None}, VOLLEYBALL_LEAGUE, "2026").rows == ()
    assert _parse_standings({"table": "nope"}, VOLLEYBALL_LEAGUE, "2026").rows == ()


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def test_parse_roster_players_and_statuses(players_data: dict[str, Any]) -> None:
    roster = _parse_roster(players_data, SPIKERS)
    assert roster.team_id == SPIKERS.id
    # The id/name-less player is skipped.
    assert len(roster.players) == 3
    by_id = {player.id: player for player in roster.players}

    assert by_id["thesportsdb:990001"].name == "Orin Latchford"
    assert by_id["thesportsdb:990001"].position == "Outside Hitter"
    assert by_id["thesportsdb:990001"].jersey_number == "7"
    assert by_id["thesportsdb:990001"].status is PlayerStatus.ACTIVE
    assert by_id["thesportsdb:990001"].status_detail is None

    assert by_id["thesportsdb:990002"].status is PlayerStatus.OUT
    assert by_id["thesportsdb:990002"].status_detail == "Out"

    assert by_id["thesportsdb:990003"].status is PlayerStatus.DAY_TO_DAY
    assert by_id["thesportsdb:990003"].status_detail == "Day-To-Day"


def test_parse_roster_empty_is_empty_roster() -> None:
    """A missing/null player list (the common national-team case) is an
    empty roster, never a crash."""
    empty = _parse_roster({}, SPIKERS)
    assert empty.team_id == SPIKERS.id
    assert empty.players == ()
    assert _parse_roster({"player": None}, SPIKERS).players == ()
    assert _parse_roster({"player": "nope"}, SPIKERS).players == ()


def test_parse_player_extracts_photo_prefers_cutout() -> None:
    """The cutout (transparent headshot) wins, then thumb, then render."""
    cutout = _parse_player(
        {
            "idPlayer": "1",
            "strPlayer": "Orin Latchford",
            "strCutout": "https://img/cutout.png",
            "strThumb": "https://img/thumb.jpg",
        },
        SPIKERS,
    )
    assert cutout is not None
    assert cutout.photo_url == "https://img/cutout.png"
    # No cutout: fall back to the thumb.
    thumb = _parse_player(
        {"idPlayer": "2", "strPlayer": "Pell Wickham", "strThumb": "https://img/t.jpg"},
        SPIKERS,
    )
    assert thumb is not None and thumb.photo_url == "https://img/t.jpg"
    # Nothing usable -> None (never raises).
    none = _parse_player({"idPlayer": "3", "strPlayer": "No Photo"}, SPIKERS)
    assert none is not None and none.photo_url is None


# ---------------------------------------------------------------------------
# Provider methods
# ---------------------------------------------------------------------------

async def test_get_schedule_merges_past_and_next_and_filters(
    events_data: dict[str, Any],
) -> None:
    provider = TheSportsDbProvider()
    calls: list[str] = []

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        calls.append(endpoint)
        # Both feeds return the same league payload; the merge dedupes by id
        # and the team/window filter keeps only the Spikers' in-window games.
        return events_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        VOLLEYBALL_LEAGUE, SPIKERS, date(2026, 6, 1), date(2026, 6, 30)
    )
    assert calls == ["eventspastleague.php", "eventsnextleague.php"]
    # Only the Spikers' games (3300001 home, 3300002 away, 3300003 home),
    # sorted ascending by start time, no duplicates despite both feeds.
    assert [g.id for g in games] == [
        "thesportsdb:3300001",
        "thesportsdb:3300003",
        "thesportsdb:3300002",
    ]
    assert all(
        g.home_team_id == SPIKERS.id or g.away_team_id == SPIKERS.id for g in games
    )


async def test_get_schedule_filters_to_window(events_data: dict[str, Any]) -> None:
    provider = TheSportsDbProvider()

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return events_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    # A window covering only June 10-15 keeps just the June 13 match.
    games = await provider.get_schedule(
        VOLLEYBALL_LEAGUE, SPIKERS, date(2026, 6, 10), date(2026, 6, 15)
    )
    assert [g.id for g in games] == ["thesportsdb:3300003"]


async def test_get_schedule_survives_one_failed_feed(
    events_data: dict[str, Any],
) -> None:
    """A half schedule beats none: one failing feed still yields the other."""
    provider = TheSportsDbProvider()

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        if endpoint == "eventspastleague.php":
            return None  # past feed down (e.g. rate-limited)
        return events_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        VOLLEYBALL_LEAGUE, SPIKERS, date(2026, 6, 1), date(2026, 6, 30)
    )
    assert [g.id for g in games] == [
        "thesportsdb:3300001",
        "thesportsdb:3300003",
        "thesportsdb:3300002",
    ]


async def test_get_schedule_both_feeds_down_is_empty() -> None:
    provider = TheSportsDbProvider()

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return None

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        VOLLEYBALL_LEAGUE, SPIKERS, date(2026, 6, 1), date(2026, 6, 30)
    )
    assert games == []


async def test_get_game_state_via_lookupevent(event_data: dict[str, Any]) -> None:
    provider = TheSportsDbProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        captured["endpoint"] = endpoint
        captured["params"] = params
        return event_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    state = await provider.get_game_state(VOLLEYBALL_LEAGUE, "3300001")
    assert captured["endpoint"] == "lookupevent.php"
    assert captured["params"] == {"id": "3300001"}
    assert state is not None
    assert state.game_id == "thesportsdb:3300001"
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (3, 1)
    assert state.period_label == "Set 4"


async def test_get_game_state_unknown_or_failed_is_none() -> None:
    provider = TheSportsDbProvider()

    async def empty_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return {"events": None}

    provider._get_json = empty_get_json  # type: ignore[method-assign]
    assert await provider.get_game_state(VOLLEYBALL_LEAGUE, "nope") is None

    async def failed_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return None

    provider._get_json = failed_get_json  # type: ignore[method-assign]
    assert await provider.get_game_state(VOLLEYBALL_LEAGUE, "3300001") is None


async def test_get_standings_via_lookuptable(table_data: dict[str, Any]) -> None:
    provider = TheSportsDbProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        captured["endpoint"] = endpoint
        captured["params"] = params
        return table_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    standings = await provider.get_standings(VOLLEYBALL_LEAGUE)
    assert captured["endpoint"] == "lookuptable.php"
    assert captured["params"]["l"] == "9001"
    assert standings.rows[0].team_name == "Pellwick Aces"
    assert standings.rows[0].rank == 1


async def test_get_standings_empty_on_failure() -> None:
    provider = TheSportsDbProvider()

    async def failed_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return None

    provider._get_json = failed_get_json  # type: ignore[method-assign]
    standings = await provider.get_standings(VOLLEYBALL_LEAGUE)
    assert standings.league_id == VOLLEYBALL_LEAGUE.id
    assert standings.rows == ()


async def test_get_roster_via_lookup_all_players(players_data: dict[str, Any]) -> None:
    provider = TheSportsDbProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        captured["endpoint"] = endpoint
        captured["params"] = params
        return players_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    roster = await provider.get_roster(VOLLEYBALL_LEAGUE, SPIKERS)
    assert captured["endpoint"] == "lookup_all_players.php"
    assert captured["params"] == {"id": "70101"}
    assert len(roster.players) == 3


async def test_get_roster_empty_on_failure() -> None:
    provider = TheSportsDbProvider()

    async def failed_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return None

    provider._get_json = failed_get_json  # type: ignore[method-assign]
    roster = await provider.get_roster(VOLLEYBALL_LEAGUE, SPIKERS)
    assert roster.team_id == SPIKERS.id
    assert roster.players == ()


# ---------------------------------------------------------------------------
# Team location (map view): lookupteam.php venue parsing
# ---------------------------------------------------------------------------

def test_parse_team_location_combines_stadium_and_location(
    team_data: dict[str, Any],
) -> None:
    """Free tier ships a stadium name + free-text location, no coordinates.

    The two are combined into one geocodable venue string for the geocode
    service; lat/lon stay None (the free tier carries none).
    """
    location = _parse_team_location(team_data)
    assert location is not None
    assert location.venue == "Saltmarsh Hall, Tidewater, Saltmarsh, Coastland"
    assert location.lat is None
    assert location.lon is None


def test_parse_team_location_returns_present_coordinates() -> None:
    """When a (paid/older) schema does carry lat/lon, they are returned."""
    data = {
        "teams": [
            {
                "strStadium": "Harborline Hall",
                "strLocation": "Harborline",
                "strStadiumLat": "41.50012",
                "strStadiumLng": "-73.20455",
            }
        ]
    }
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "Harborline Hall, Harborline"
    assert location.lat == 41.50012
    assert location.lon == -73.20455


def test_parse_team_location_zero_coordinates_are_treated_as_missing() -> None:
    """TheSportsDB uses "0"/"" as a no-data sentinel for coordinates."""
    data = {"teams": [{"strStadium": "Null Island Hall", "strStadiumLat": "0", "strStadiumLng": "0"}]}
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "Null Island Hall"
    assert (location.lat, location.lon) == (None, None)


def test_parse_team_location_falls_back_to_stadium_location_field() -> None:
    """Older schemas name the field strStadiumLocation; honor it too."""
    data = {"teams": [{"strStadiumLocation": "Quarrydale Sports Hall"}]}
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "Quarrydale Sports Hall"


def test_parse_team_location_none_when_no_venue() -> None:
    assert _parse_team_location({"teams": [{"strTeam": "No Venue FC"}]}) is None
    assert _parse_team_location({"teams": []}) is None
    assert _parse_team_location({"teams": None}) is None
    assert _parse_team_location({}) is None
    assert _parse_team_location(None) is None


async def test_get_team_location_via_lookupteam(team_data: dict[str, Any]) -> None:
    provider = TheSportsDbProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        captured["endpoint"] = endpoint
        captured["params"] = params
        return team_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    location = await provider.get_team_location(VOLLEYBALL_LEAGUE, SPIKERS)
    assert captured["endpoint"] == "lookupteam.php"
    assert captured["params"] == {"id": "70101"}
    assert location is not None
    assert location.venue == "Saltmarsh Hall, Tidewater, Saltmarsh, Coastland"


async def test_get_team_location_none_on_failure() -> None:
    provider = TheSportsDbProvider()

    async def failed_get_json(
        endpoint: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return None

    provider._get_json = failed_get_json  # type: ignore[method-assign]
    assert await provider.get_team_location(VOLLEYBALL_LEAGUE, SPIKERS) is None


async def test_leaderboard_methods_and_news_are_empty() -> None:
    provider = TheSportsDbProvider()
    # Volleyball is not a leaderboard sport and has no provider news.
    assert await provider.get_events(VOLLEYBALL_LEAGUE, date(2026, 6, 1), date(2026, 6, 30)) == []
    assert await provider.get_event_state(VOLLEYBALL_LEAGUE, "3300001") is None
    assert await provider.get_news(VOLLEYBALL_LEAGUE, SPIKERS) == []
    assert await provider.get_competition_schedule(
        VOLLEYBALL_LEAGUE, date(2026, 6, 1), date(2026, 6, 30)
    ) == []


async def test_get_json_raises_transient_on_rate_limited_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained free-tier rate-limiting (429 HTML) must raise transient.

    The free tier answers a rate-limited request with HTTP 429 and an HTML
    body.  After retries are exhausted this now surfaces as
    ``TransientProviderError`` — NOT swallowed to None — so the provider's
    circuit breaker can finally open instead of hammering a dead source.
    This is the regression test for the once-dead thesportsdb breaker.
    """
    from app.providers import http_util

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(http_util.asyncio, "sleep", _no_sleep)

    provider = TheSportsDbProvider()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429, text="<!doctype html><html>rate limited</html>"
        )
    )
    provider._client = httpx.AsyncClient(base_url="https://example.test/", transport=transport)
    try:
        with pytest.raises(http_util.TransientProviderError):
            await provider._get_json("eventspastleague.php", {"id": "9001"})
    finally:
        await provider.close()


async def test_get_json_returns_none_on_non_json_200_body() -> None:
    """A healthy 200 whose body isn't JSON degrades to None (not a breaker hit).

    Unlike a 429 (a clear outage), an anomalous non-JSON 200 is treated as a
    sparse/garbage result and returns None, so one odd response can't trip the
    breaker.
    """
    provider = TheSportsDbProvider()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>not json</html>")
    )
    provider._client = httpx.AsyncClient(base_url="https://example.test/", transport=transport)
    try:
        assert await provider._get_json("eventspastleague.php", {"id": "9001"}) is None
    finally:
        await provider.close()


async def test_get_json_returns_none_on_empty_body() -> None:
    """Some empty results come back as an empty body (verified live for
    sparse volleyball standings); that must be None, not a crash."""
    provider = TheSportsDbProvider()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=""))
    provider._client = httpx.AsyncClient(base_url="https://example.test/", transport=transport)
    try:
        assert await provider._get_json("lookuptable.php", {"l": "9001"}) is None
    finally:
        await provider.close()


async def test_provider_close_without_use_is_safe() -> None:
    provider = TheSportsDbProvider()
    assert provider.provider_id == "thesportsdb"
    await provider.close()  # never created a client; must be a no-op
    assert provider._client is None


# ---------------------------------------------------------------------------
# Stadium enrichment by team name (app.services.stadiums) — the Chelsea fix
# ---------------------------------------------------------------------------
# ESPN gives soccer clubs no venue and off-season teams no fixtures to borrow
# one from, so a freshly-followed club never lands on the map.  The stadium
# service looks a team up *by name* on TheSportsDB's searchteams.php and
# enriches it with the venue record (photo + DMS coordinates).  All payloads
# here are fictional TheSportsDB-shaped JSON; the async path is exercised by
# monkeypatching the module's single ``_get_json`` fetch helper.

from app.services import stadiums  # noqa: E402


@pytest.fixture(scope="module")
def searchteams_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_searchteams.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def venue_data() -> dict[str, Any]:
    with (FIXTURES / "tsdb_venue.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(autouse=True)
def _clear_stadium_cache() -> None:
    """The stadium service caches per process; reset between tests."""
    stadiums._cache.clear()


def test_parse_dms_map_decodes_degrees_minutes_seconds() -> None:
    # 51 deg 28' 54" N, 0 deg 11' 28" W (a fictional venue's map reference).
    assert stadiums._parse_dms_map("51\u00b028\u203254\u2033N 0\u00b011\u203228\u2033W") == (
        51.48167,
        -0.19111,
    )


def test_parse_dms_map_handles_southern_and_eastern_hemispheres() -> None:
    coords = stadiums._parse_dms_map("33\u00b051\u203230\u2033S 151\u00b012\u203240\u2033E")
    assert coords is not None
    lat, lon = coords
    assert lat < 0 and lon > 0  # southern + eastern


def test_parse_dms_map_none_when_no_coordinates() -> None:
    assert stadiums._parse_dms_map(None) is None
    assert stadiums._parse_dms_map("") is None
    assert stadiums._parse_dms_map("https://maps.example.com/?q=stadium") is None


def test_coerce_int_strips_commas_and_rejects_zero() -> None:
    assert stadiums._coerce_int("41,798") == 41798
    assert stadiums._coerce_int("0") is None      # free-tier "unknown" sentinel
    assert stadiums._coerce_int("") is None
    assert stadiums._coerce_int(None) is None
    assert stadiums._coerce_int("not a number") is None


async def test_lookup_stadium_enriches_with_venue_record(
    searchteams_data: dict[str, Any], venue_data: dict[str, Any]
) -> None:
    """The full happy path: search hit + venue lookup -> facts + coords.

    The soccer hit is chosen over the U21 / basketball same-named sides;
    the venue record supplies the photo, the year opened and the DMS
    coordinates the search result lacked.
    """
    calls: list[tuple[str, dict[str, str]]] = []

    async def fake_get_json(endpoint: str, params: dict[str, str]):
        calls.append((endpoint, params))
        if endpoint == "searchteams.php":
            return searchteams_data
        if endpoint == "lookupvenue.php":
            return venue_data
        return None

    stadiums._get_json = fake_get_json  # type: ignore[assignment]
    location = await stadiums.lookup_stadium("Saltmarsh Spikers", sport="soccer")

    assert location is not None
    assert location.venue == "Tidewater Ground, Fulwater, Saltmarsh"
    assert location.capacity == 41798
    assert location.opened == 1877                      # from the venue record
    assert location.image_url == "https://images.example.com/venues/90301-thumb.jpg"
    assert location.location == "Fulwater, Saltmarsh"
    assert location.lat == 51.48167
    assert location.lon == -0.19111
    # Searched once, then followed the chosen hit's idVenue exactly once.
    assert calls[0] == ("searchteams.php", {"t": "Saltmarsh Spikers"})
    assert ("lookupvenue.php", {"id": "90301"}) in calls


async def test_lookup_stadium_filters_by_sport(
    searchteams_data: dict[str, Any]
) -> None:
    """A basketball lookup must pick the Basketball hit, not the soccer one."""

    async def fake_get_json(endpoint: str, params: dict[str, str]):
        if endpoint == "searchteams.php":
            return searchteams_data
        return None  # the basketball hit has no idVenue -> no venue lookup

    stadiums._get_json = fake_get_json  # type: ignore[assignment]
    location = await stadiums.lookup_stadium("Saltmarsh Spikers", sport="basketball")
    assert location is not None
    assert location.venue == "Tidewater Fieldhouse, Fulwater, Saltmarsh"
    assert location.capacity == 8400


async def test_lookup_stadium_works_without_venue_record(
    searchteams_data: dict[str, Any]
) -> None:
    """When the venue lookup yields nothing, the search facts still come back.

    Coordinates stay None (the caller geocodes the venue name), but the
    stadium name, capacity and location text are returned.
    """

    async def fake_get_json(endpoint: str, params: dict[str, str]):
        if endpoint == "searchteams.php":
            return searchteams_data
        return None

    stadiums._get_json = fake_get_json  # type: ignore[assignment]
    location = await stadiums.lookup_stadium("Saltmarsh Spikers", sport="soccer")
    assert location is not None
    assert location.venue == "Tidewater Ground, Fulwater, Saltmarsh"
    assert location.capacity == 41798
    assert location.lat is None and location.lon is None


async def test_lookup_stadium_none_on_no_match_and_caches_it() -> None:
    """A miss returns None and is cached so it is not re-fetched."""
    calls = 0

    async def fake_get_json(endpoint: str, params: dict[str, str]):
        nonlocal calls
        calls += 1
        return {"teams": None}

    stadiums._get_json = fake_get_json  # type: ignore[assignment]
    assert await stadiums.lookup_stadium("Nowhere FC", sport="soccer") is None
    assert await stadiums.lookup_stadium("Nowhere FC", sport="soccer") is None
    assert calls == 1  # second call served from the negative cache


async def test_lookup_stadium_never_raises_on_blank_name() -> None:
    assert await stadiums.lookup_stadium("", sport="soccer") is None
    assert await stadiums.lookup_stadium("   ") is None
