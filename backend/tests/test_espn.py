"""Unit tests for the pure ESPN payload parsers.

All payloads are fictional ESPN-shaped JSON; no network I/O anywhere.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app import timeutil
from app.models.domain import GamePhase, League, PlayerStatus, Sport, Team
from app.providers.espn import (
    EspnProvider,
    _career_line_from_overview,
    _core_event_path,
    _parse_athlete,
    _chunk_date_range,
    _format_stat_line,
    _merge_games,
    _parse_game_summary,
    _parse_individual_scoreboard,
    _parse_news,
    _parse_pickcenter,
    _parse_plays,
    _parse_predictor,
    _parse_roster,
    _parse_win_probability,
    _play_period_label,
    _parse_schedule,
    _parse_scoreboard,
    _parse_standings,
    _parse_summary_state,
    _parse_team_location,
    _parse_tennis_rankings,
    _stat_line_from_overview,
)
from app.providers.espn_catalog import get_catalog_league

FIXTURES = Path(__file__).parent / "fixtures"

BASKETBALL_LEAGUE = League(
    id="crestline-basketball",
    sport=Sport.BASKETBALL,
    name="Crestline Basketball Association",
    provider="espn",
    provider_key="basketball/crestline",
)
SOCCER_LEAGUE = League(
    id="harborline-soccer",
    sport=Sport.SOCCER,
    name="Harborline Soccer Circuit",
    provider="espn",
    provider_key="soccer/harborline",
)
BASEBALL_LEAGUE = League(
    id="meridian-baseball",
    sport=Sport.BASEBALL,
    name="Meridian Baseball Association",
    provider="espn",
    provider_key="baseball/meridian",
)
HOCKEY_LEAGUE = League(
    id="glacierline-hockey",
    sport=Sport.HOCKEY,
    name="Glacierline Hockey Loop",
    provider="espn",
    provider_key="hockey/glacierline",
)
FOOTBALL_LEAGUE = League(
    id="harvest-football",
    sport=Sport.FOOTBALL,
    name="Harvest Football Alliance",
    provider="espn",
    provider_key="football/harvest",
)
# A whole-competition (follow_all) tournament league — the World Cup
# analog exercised by get_competition_schedule.
COMPETITION_LEAGUE = League(
    id="coastline-nations-cup",
    sport=Sport.SOCCER,
    name="Coastline Nations Cup",
    provider="espn",
    provider_key="soccer/coastline.nations",
    follow_all=True,
)
# Individual-sport leagues: a "team" is a single athlete.
TENNIS_LEAGUE = League(
    id="coastline-tennis",
    sport=Sport.TENNIS,
    name="Coastline Tennis Series",
    provider="espn",
    provider_key="tennis/coastline",
)
MMA_LEAGUE = League(
    id="summit-fc",
    sport=Sport.MMA,
    name="Summit Fighting Championship",
    provider="espn",
    provider_key="mma/summit",
)


@pytest.fixture(scope="module")
def scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def _event(
    *,
    event_id: str,
    date: str,
    home: tuple[str, str, str],
    away: tuple[str, str, str],
    home_score: Any,
    away_score: Any,
    status: dict[str, Any],
    venue: str | None = None,
) -> dict[str, Any]:
    """Build a minimal ESPN-shaped event with fictional teams."""
    home_id, home_name, home_abbr = home
    away_id, away_name, away_abbr = away
    competition: dict[str, Any] = {
        "id": event_id,
        "date": date,
        "competitors": [
            {
                "id": away_id,
                "homeAway": "away",
                "score": away_score,
                "team": {"id": away_id, "displayName": away_name, "abbreviation": away_abbr},
            },
            {
                "id": home_id,
                "homeAway": "home",
                "score": home_score,
                "team": {"id": home_id, "displayName": home_name, "abbreviation": home_abbr},
            },
        ],
        "status": status,
    }
    if venue is not None:
        competition["venue"] = {"fullName": venue}
    return {"id": event_id, "date": date, "competitions": [competition]}


def _status(
    *,
    state: str,
    name: str,
    period: int = 0,
    display_clock: str | None = None,
    detail: str = "",
    short_detail: str = "",
    alt_detail: str | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "period": period,
        "type": {
            "state": state,
            "name": name,
            "detail": detail,
            "shortDetail": short_detail,
        },
    }
    if alt_detail is not None:
        status["type"]["altDetail"] = alt_detail
    if display_clock is not None:
        status["displayClock"] = display_clock
    return status


# ---------------------------------------------------------------------------
# Scoreboard fixture
# ---------------------------------------------------------------------------


def test_scoreboard_parses_two_games_and_skips_malformed(scoreboard_data: dict[str, Any]) -> None:
    games = _parse_scoreboard(scoreboard_data, BASKETBALL_LEAGUE)
    assert [game.id for game in games] == ["espn:9100001", "espn:9100002"]
    assert all(game.league_id == BASKETBALL_LEAGUE.id for game in games)


def test_parse_event_extracts_side_logos_and_colors() -> None:
    from app.providers.espn import _parse_event

    event = {
        "id": "9100777",
        "date": "2026-06-13T22:00Z",
        "competitions": [
            {
                "id": "9100777",
                "date": "2026-06-13T22:00Z",
                "status": {"type": {"state": "pre", "completed": False}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "0",
                        "team": {
                            "id": "1",
                            "displayName": "Brackenford United",
                            "abbreviation": "BRK",
                            "logo": "https://cdn.example/brk.png",
                            "color": "1d428a",
                        },
                    },
                    {
                        "homeAway": "away",
                        "score": "0",
                        "team": {
                            "id": "2",
                            "displayName": "Marlowe City",
                            "abbreviation": "MAR",
                            "logos": [{"href": "https://cdn.example/mar.png"}],
                            "color": "#ff0000",
                        },
                    },
                ],
            }
        ],
    }
    game = _parse_event(event, SOCCER_LEAGUE)
    assert game is not None
    assert game.home_logo_url == "https://cdn.example/brk.png"
    assert game.away_logo_url == "https://cdn.example/mar.png"
    assert game.home_color == "#1d428a"  # bare hex normalized
    assert game.away_color == "#ff0000"


def test_scoreboard_in_progress_game(scoreboard_data: dict[str, Any]) -> None:
    games = _parse_scoreboard(scoreboard_data, BASKETBALL_LEAGUE)
    game = next(g for g in games if g.id == "espn:9100001")

    assert game.home_name == "Crestfall Lynx"
    assert game.away_name == "Rivermont Gulls"
    assert game.home_abbreviation == "CFL"
    assert game.away_abbreviation == "RVG"
    assert game.venue == "Crestfall Fieldhouse"
    assert game.start_time == datetime(2026, 6, 11, 23, 30, tzinfo=timezone.utc)
    # No team context on a scoreboard call: internal team ids stay unset.
    assert game.home_team_id is None and game.away_team_id is None

    state = game.state
    assert state is not None
    assert state.game_id == "espn:9100001"
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.home_score == 67
    assert state.away_score == 71
    assert state.period == 3
    assert state.period_label == "Q3"
    assert state.clock == "7:42"
    assert state.is_intermission is False


def test_scoreboard_final_game(scoreboard_data: dict[str, Any]) -> None:
    games = _parse_scoreboard(scoreboard_data, BASKETBALL_LEAGUE)
    game = next(g for g in games if g.id == "espn:9100002")

    # homeAway mapping must win over competitor ordering (away listed first).
    assert game.home_name == "Dunwick Spires"
    assert game.away_name == "Bellharbor Manticores"

    state = game.state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.home_score == 97
    assert state.away_score == 104
    assert state.period == 4
    assert state.period_label == "Q4"
    assert state.clock is None
    assert state.is_intermission is False


# ---------------------------------------------------------------------------
# Status / period normalization across sports
# ---------------------------------------------------------------------------


def test_basketball_overtime_labels() -> None:
    event = _event(
        event_id="9100010",
        date="2026-06-10T01:00Z",
        home=("41", "Gravenford Owls", "GRV"),
        away=("42", "Telmark Foxes", "TLM"),
        home_score="119",
        away_score="117",
        status=_status(state="in", name="STATUS_IN_PROGRESS", period=6, display_clock="2:11"),
    )
    games = _parse_scoreboard({"events": [event]}, BASKETBALL_LEAGUE)
    assert games[0].state is not None
    assert games[0].state.period_label == "2OT"
    assert games[0].state.clock == "2:11"


def test_soccer_halftime_is_intermission() -> None:
    event = _event(
        event_id="9200001",
        date="2026-06-11T18:00Z",
        home=("12", "Larkmoor Albion", "LKM"),
        away=("13", "Pellwick Rangers", "PLW"),
        home_score="1",
        away_score="0",
        status=_status(state="in", name="STATUS_HALFTIME", period=1, display_clock="45'"),
    )
    games = _parse_scoreboard({"events": [event]}, SOCCER_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period == 1
    assert state.period_label == "1st Half"
    assert state.is_intermission is True
    assert state.clock == "45'"


def test_soccer_second_half_clock() -> None:
    event = _event(
        event_id="9200002",
        date="2026-06-11T18:00Z",
        home=("12", "Larkmoor Albion", "LKM"),
        away=("13", "Pellwick Rangers", "PLW"),
        home_score="1",
        away_score="2",
        status=_status(state="in", name="STATUS_IN_PROGRESS", period=2, display_clock="67'"),
    )
    games = _parse_scoreboard({"events": [event]}, SOCCER_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.period == 2
    assert state.period_label == "2nd Half"
    assert state.clock == "67'"
    assert state.is_intermission is False


@pytest.mark.parametrize(
    ("short_detail", "expected_label", "expected_period"),
    [
        ("Top 3rd", "Top 3", 3),
        ("Bot 7th", "Bot 7", 7),
        ("Bottom 9th", "Bot 9", 9),
    ],
)
def test_baseball_inning_labels(
    short_detail: str, expected_label: str, expected_period: int
) -> None:
    event = _event(
        event_id="9300001",
        date="2026-06-11T20:00Z",
        home=("21", "Saltmarsh Herons", "SLT"),
        away=("22", "Cobblewick Badgers", "CBW"),
        home_score="4",
        away_score="2",
        status=_status(
            state="in",
            name="STATUS_IN_PROGRESS",
            period=expected_period,
            display_clock="0:00",
            short_detail=short_detail,
        ),
    )
    games = _parse_scoreboard({"events": [event]}, BASEBALL_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.period == expected_period
    assert state.period_label == expected_label
    assert state.clock is None  # baseball never reports a clock


@pytest.mark.parametrize(
    ("short_detail", "expected_label", "expected_period"),
    [
        ("Mid 5th", "Mid 5", 5),
        ("Middle 3rd", "Mid 3", 3),
        ("End of the 6th", "End 6", 6),
    ],
)
def test_baseball_half_inning_breaks_are_intermissions(
    short_detail: str, expected_label: str, expected_period: int
) -> None:
    """Mid/End half-inning breaks (as seen in live traffic) are intermissions."""
    event = _event(
        event_id="9300002",
        date="2026-06-12T18:00Z",
        home=("21", "Saltmarsh Herons", "SLT"),
        away=("22", "Cobblewick Badgers", "CBW"),
        home_score="3",
        away_score="4",
        status=_status(
            state="in",
            name="STATUS_IN_PROGRESS",
            period=expected_period,
            short_detail=short_detail,
        ),
    )
    games = _parse_scoreboard({"events": [event]}, BASEBALL_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.period == expected_period
    assert state.period_label == expected_label
    assert state.is_intermission is True
    assert state.clock is None


def test_scheduled_baseball_does_not_leak_inning_one() -> None:
    """Live ESPN reports ``period: 1`` pre-game; period must stay 0 until tip-off."""
    event = _event(
        event_id="9300003",
        date="2026-06-12T22:40Z",
        home=("21", "Saltmarsh Herons", "SLT"),
        away=("22", "Cobblewick Badgers", "CBW"),
        home_score="0",
        away_score="0",
        status=_status(
            state="pre",
            name="STATUS_SCHEDULED",
            period=1,
            display_clock="0:00",
            detail="Scheduled",
            short_detail="6/12 - 6:40 PM EDT",
        ),
    )
    games = _parse_scoreboard({"events": [event]}, BASEBALL_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.phase is GamePhase.SCHEDULED
    assert state.period == 0
    assert state.period_label == ""
    assert state.clock is None
    assert state.is_intermission is False


def test_postponed_baseball_does_not_leak_inning_one() -> None:
    event = _event(
        event_id="9300004",
        date="2026-06-12T23:40Z",
        home=("21", "Saltmarsh Herons", "SLT"),
        away=("22", "Cobblewick Badgers", "CBW"),
        home_score="0",
        away_score="0",
        status=_status(state="post", name="STATUS_POSTPONED", period=1),
    )
    games = _parse_scoreboard({"events": [event]}, BASEBALL_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.phase is GamePhase.POSTPONED
    assert state.period == 0
    assert state.period_label == ""


def test_postponed_and_canceled_from_status_name() -> None:
    postponed = _event(
        event_id="9100020",
        date="2026-06-12T00:00Z",
        home=("41", "Gravenford Owls", "GRV"),
        away=("42", "Telmark Foxes", "TLM"),
        home_score="0",
        away_score="0",
        status=_status(state="post", name="STATUS_POSTPONED"),
    )
    canceled = _event(
        event_id="9100021",
        date="2026-06-12T00:00Z",
        home=("41", "Gravenford Owls", "GRV"),
        away=("42", "Telmark Foxes", "TLM"),
        home_score="0",
        away_score="0",
        status=_status(state="post", name="STATUS_CANCELED"),
    )
    games = _parse_scoreboard({"events": [postponed, canceled]}, BASKETBALL_LEAGUE)
    assert games[0].state is not None and games[0].state.phase is GamePhase.POSTPONED
    assert games[1].state is not None and games[1].state.phase is GamePhase.CANCELED


# ---------------------------------------------------------------------------
# Hockey normalization (fixture mirrors live NHL scoreboard shapes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hockey_scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_hockey_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def hockey_games(hockey_scoreboard_data: dict[str, Any]) -> dict[str, Any]:
    games = _parse_scoreboard(hockey_scoreboard_data, HOCKEY_LEAGUE)
    return {game.id: game for game in games}


def test_hockey_scoreboard_skips_malformed_event(hockey_games: dict[str, Any]) -> None:
    assert sorted(hockey_games) == [
        "espn:9500001",
        "espn:9500002",
        "espn:9500003",
        "espn:9500004",
        "espn:9500005",
    ]


def test_hockey_live_second_period_with_clock(hockey_games: dict[str, Any]) -> None:
    state = hockey_games["espn:9500001"].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert (state.home_score, state.away_score) == (2, 1)
    assert state.period == 2
    assert state.period_label == "P2"
    assert state.clock == "14:02"
    assert state.is_intermission is False


def test_hockey_regulation_final_third_period(hockey_games: dict[str, Any]) -> None:
    state = hockey_games["espn:9500002"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (4, 2)
    assert state.period == 3
    assert state.period_label == "P3"
    assert state.clock is None
    assert state.is_intermission is False


def test_hockey_overtime_final(hockey_games: dict[str, Any]) -> None:
    """``Final/OT`` (altDetail "OT"), period 4 → label "OT"."""
    state = hockey_games["espn:9500003"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.period == 4
    assert state.period_label == "OT"
    assert state.clock is None


def test_hockey_shootout_final(hockey_games: dict[str, Any]) -> None:
    """``Final/SO`` wins over the raw period 5 (which would label "2OT")."""
    state = hockey_games["espn:9500004"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    # The shootout winner's score includes the +1 bump ESPN applies.
    assert (state.home_score, state.away_score) == (2, 1)
    assert state.period == 5
    assert state.period_label == "SO"
    assert state.clock is None


def test_hockey_end_of_period_is_intermission(hockey_games: dict[str, Any]) -> None:
    state = hockey_games["espn:9500005"].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period == 1
    assert state.period_label == "P1"
    assert state.is_intermission is True
    assert state.clock is None  # "0:00" must not leak through the break


def test_hockey_playoff_double_overtime_label() -> None:
    """Playoff period 5 without any SO marker is double overtime."""
    event = _event(
        event_id="9500010",
        date="2026-06-11T23:00Z",
        home=("301", "Glacierport Narwhals", "GPN"),
        away=("302", "Embermont Yetis", "EMY"),
        home_score="3",
        away_score="3",
        status=_status(
            state="in",
            name="STATUS_IN_PROGRESS",
            period=5,
            display_clock="8:12",
            detail="8:12 - 2nd Overtime",
        ),
    )
    games = _parse_scoreboard({"events": [event]}, HOCKEY_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.period_label == "2OT"
    assert state.clock == "8:12"
    assert state.is_intermission is False


def test_hockey_live_shootout_status_name() -> None:
    event = _event(
        event_id="9500011",
        date="2026-06-11T23:00Z",
        home=("307", "Drelford Glaciers", "DRG"),
        away=("308", "Karswick Aurorans", "KAR"),
        home_score="1",
        away_score="1",
        status=_status(
            state="in",
            name="STATUS_SHOOTOUT",
            period=5,
            display_clock="0:00",
            detail="Shootout",
        ),
    )
    games = _parse_scoreboard({"events": [event]}, HOCKEY_LEAGUE)
    state = games[0].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period_label == "SO"
    assert state.is_intermission is False


# ---------------------------------------------------------------------------
# Football normalization
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def football_scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_football_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def football_games(football_scoreboard_data: dict[str, Any]) -> dict[str, Any]:
    games = _parse_scoreboard(football_scoreboard_data, FOOTBALL_LEAGUE)
    return {game.id: game for game in games}


def test_football_fourth_quarter_final(football_games: dict[str, Any]) -> None:
    state = football_games["espn:9600001"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (31, 24)
    assert state.period == 4
    assert state.period_label == "Q4"
    assert state.clock is None
    assert state.is_intermission is False


def test_football_overtime_final(football_games: dict[str, Any]) -> None:
    state = football_games["espn:9600002"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert (state.home_score, state.away_score) == (27, 30)
    assert state.period == 5
    assert state.period_label == "OT"
    assert state.clock is None


def test_football_halftime_is_intermission(football_games: dict[str, Any]) -> None:
    state = football_games["espn:9600003"].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period == 2
    assert state.period_label == "Q2"
    assert state.is_intermission is True
    assert state.clock is None


# ---------------------------------------------------------------------------
# Team schedule
# ---------------------------------------------------------------------------


def test_schedule_sets_internal_team_id_on_matching_side_only() -> None:
    team = Team(
        id="gravenford-owls",
        league_id=BASKETBALL_LEAGUE.id,
        name="Gravenford Owls",
        abbreviation="GRV",
        provider_key="41",
    )
    home_event = _event(
        event_id="9400001",
        date="2026-06-14T23:00Z",
        home=("41", "Gravenford Owls", "GRV"),
        away=("42", "Telmark Foxes", "TLM"),
        home_score={"value": 0.0, "displayValue": "0"},
        away_score={"value": 0.0, "displayValue": "0"},
        status=_status(state="pre", name="STATUS_SCHEDULED"),
    )
    away_event = _event(
        event_id="9400002",
        date="2026-06-16T23:00Z",
        home=("43", "Wrenfield Stags", "WRN"),
        away=("41", "Gravenford Owls", "GRV"),
        home_score={"value": 102.0, "displayValue": "102"},
        away_score={"value": 96.0, "displayValue": "96"},
        status=_status(state="post", name="STATUS_FINAL", period=4),
    )
    games = _parse_schedule({"events": [home_event, away_event]}, BASKETBALL_LEAGUE, team)
    assert len(games) == 2

    as_home = next(g for g in games if g.id == "espn:9400001")
    assert as_home.home_team_id == team.id
    assert as_home.away_team_id is None
    assert as_home.state is not None and as_home.state.phase is GamePhase.SCHEDULED

    as_away = next(g for g in games if g.id == "espn:9400002")
    assert as_away.away_team_id == team.id
    assert as_away.home_team_id is None
    # Dict-shaped schedule scores are parsed.
    assert as_away.state is not None
    assert as_away.state.home_score == 102
    assert as_away.state.away_score == 96
    assert as_away.state.phase is GamePhase.FINAL


# ---------------------------------------------------------------------------
# Soccer schedule double-call + merge
# ---------------------------------------------------------------------------

SOCCER_TEAM = Team(
    id="larkmoor-albion",
    league_id=SOCCER_LEAGUE.id,
    name="Larkmoor Albion",
    abbreviation="LKM",
    provider_key="12",
)


def _soccer_result_event(event_id: str, date: str) -> dict[str, Any]:
    return _event(
        event_id=event_id,
        date=date,
        home=("12", "Larkmoor Albion", "LKM"),
        away=("13", "Pellwick Rangers", "PLW"),
        home_score="2",
        away_score="1",
        status=_status(state="post", name="STATUS_FULL_TIME", period=2),
    )


def _soccer_fixture_event(event_id: str, date: str) -> dict[str, Any]:
    return _event(
        event_id=event_id,
        date=date,
        home=("14", "Marrowgate Wanderers", "MRW"),
        away=("12", "Larkmoor Albion", "LKM"),
        home_score="0",
        away_score="0",
        status=_status(state="pre", name="STATUS_SCHEDULED"),
    )


def test_merge_games_dedupes_by_id_and_earlier_batch_wins() -> None:
    # The same game finished in the results batch but still listed as a
    # stale fixture: the earlier (results) batch must win the merge.
    results = _parse_schedule(
        {"events": [_soccer_result_event("9200100", "2026-06-01T14:00Z")]},
        SOCCER_LEAGUE,
        SOCCER_TEAM,
    )
    fixtures = _parse_schedule(
        {
            "events": [
                _soccer_fixture_event("9200100", "2026-06-01T14:00Z"),
                _soccer_fixture_event("9200101", "2026-06-20T14:00Z"),
            ]
        },
        SOCCER_LEAGUE,
        SOCCER_TEAM,
    )
    merged = _merge_games(results, fixtures)
    assert [game.id for game in merged] == ["espn:9200100", "espn:9200101"]
    duplicate = merged[0]
    assert duplicate.state is not None
    assert duplicate.state.phase is GamePhase.FINAL  # results version kept


async def test_soccer_schedule_merges_results_and_fixtures() -> None:
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        if params and params.get("fixture") == "true":
            return {"events": [_soccer_fixture_event("9200111", "2026-06-20T14:00Z")]}
        return {"events": [_soccer_result_event("9200110", "2026-06-01T14:00Z")]}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        SOCCER_LEAGUE, SOCCER_TEAM, date(2026, 5, 1), date(2026, 7, 1)
    )
    assert calls == [None, {"fixture": "true"}]
    assert [game.id for game in games] == ["espn:9200110", "espn:9200111"]
    assert games[0].state is not None and games[0].state.phase is GamePhase.FINAL
    assert games[1].state is not None and games[1].state.phase is GamePhase.SCHEDULED


async def test_soccer_schedule_survives_one_failed_call() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if params is None:
            raise httpx.ConnectError("bare call down")
        return {"events": [_soccer_fixture_event("9200121", "2026-06-20T14:00Z")]}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        SOCCER_LEAGUE, SOCCER_TEAM, date(2026, 5, 1), date(2026, 7, 1)
    )
    # Half a schedule beats none: the fixtures half still comes through.
    assert [game.id for game in games] == ["espn:9200121"]


async def test_soccer_schedule_raises_when_both_calls_fail() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("everything down")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    with pytest.raises(httpx.ConnectError):
        await provider.get_schedule(SOCCER_LEAGUE, SOCCER_TEAM, date(2026, 5, 1), date(2026, 7, 1))


async def test_non_soccer_schedule_makes_single_bare_call() -> None:
    provider = EspnProvider()
    team = Team(
        id="gravenford-owls",
        league_id=BASKETBALL_LEAGUE.id,
        name="Gravenford Owls",
        abbreviation="GRV",
        provider_key="41",
    )
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        return {"events": []}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    await provider.get_schedule(BASKETBALL_LEAGUE, team, date(2026, 5, 1), date(2026, 7, 1))
    assert calls == [None]


# ---------------------------------------------------------------------------
# Hockey/football schedule seasontype 2+3 double-call + merge
# ---------------------------------------------------------------------------

HOCKEY_TEAM = Team(
    id="glacierport-narwhals",
    league_id=HOCKEY_LEAGUE.id,
    name="Glacierport Narwhals",
    abbreviation="GPN",
    provider_key="301",
)
FOOTBALL_TEAM = Team(
    id="harrowgate-plowmen",
    league_id=FOOTBALL_LEAGUE.id,
    name="Harrowgate Plowmen",
    abbreviation="HGP",
    provider_key="401",
)


async def test_hockey_schedule_fetches_both_seasontypes() -> None:
    """The bare endpoint defaults to the current phase only; the adapter
    must request regular season (2) and postseason (3) explicitly."""
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    def _hockey_event(event_id: str, date: str, status: dict[str, Any]) -> dict[str, Any]:
        return _event(
            event_id=event_id,
            date=date,
            home=("301", "Glacierport Narwhals", "GPN"),
            away=("302", "Embermont Yetis", "EMY"),
            home_score={"value": 3.0, "displayValue": "3"},
            away_score={"value": 2.0, "displayValue": "2"},
            status=status,
        )

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        if params and params.get("seasontype") == "3":
            return {
                "events": [
                    _hockey_event(
                        "9500102",
                        "2026-06-20T00:00Z",
                        _status(state="pre", name="STATUS_SCHEDULED"),
                    )
                ]
            }
        return {
            "events": [
                _hockey_event(
                    "9500101",
                    "2026-04-10T23:00Z",
                    _status(state="post", name="STATUS_FINAL", period=3),
                )
            ]
        }

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        HOCKEY_LEAGUE, HOCKEY_TEAM, date(2026, 4, 1), date(2026, 7, 1)
    )
    assert calls == [{"seasontype": "2"}, {"seasontype": "3"}]
    assert [game.id for game in games] == ["espn:9500101", "espn:9500102"]
    # Object-shaped schedule scores still parse on the merged path.
    assert games[0].state is not None
    assert games[0].state.phase is GamePhase.FINAL
    assert (games[0].state.home_score, games[0].state.away_score) == (3, 2)
    assert games[1].state is not None
    assert games[1].state.phase is GamePhase.SCHEDULED


async def test_football_schedule_tolerates_empty_postseason() -> None:
    """Off-season seasontype=3 returns zero events (verified live); the
    regular-season half must still come through."""
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        if params and params.get("seasontype") == "3":
            return {"events": []}
        return {
            "events": [
                _event(
                    event_id="9600101",
                    date="2026-09-20T17:00Z",
                    home=("401", "Harrowgate Plowmen", "HGP"),
                    away=("402", "Cinderfall Drakes", "CFD"),
                    home_score=None,
                    away_score=None,
                    status=_status(state="pre", name="STATUS_SCHEDULED"),
                )
            ]
        }

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        FOOTBALL_LEAGUE, FOOTBALL_TEAM, date(2026, 8, 1), date(2027, 2, 1)
    )
    assert calls == [{"seasontype": "2"}, {"seasontype": "3"}]
    assert [game.id for game in games] == ["espn:9600101"]


async def test_hockey_schedule_survives_one_failed_seasontype_call() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if params and params.get("seasontype") == "2":
            raise httpx.ConnectError("regular-season call down")
        return {
            "events": [
                _event(
                    event_id="9500111",
                    date="2026-06-20T00:00Z",
                    home=("301", "Glacierport Narwhals", "GPN"),
                    away=("302", "Embermont Yetis", "EMY"),
                    home_score=None,
                    away_score=None,
                    status=_status(state="pre", name="STATUS_SCHEDULED"),
                )
            ]
        }

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        HOCKEY_LEAGUE, HOCKEY_TEAM, date(2026, 5, 1), date(2026, 7, 1)
    )
    # Half a schedule beats none: the postseason half still comes through.
    assert [game.id for game in games] == ["espn:9500111"]


# ---------------------------------------------------------------------------
# Summary (single game state)
# ---------------------------------------------------------------------------


def test_parse_summary_state() -> None:
    data = {
        "header": {
            "id": "9100001",
            "competitions": [
                {
                    "id": "9100001",
                    "date": "2026-06-11T23:30Z",
                    "competitors": [
                        {
                            "id": "55",
                            "homeAway": "home",
                            "score": "82",
                            "team": {"id": "55", "displayName": "Crestfall Lynx"},
                        },
                        {
                            "id": "77",
                            "homeAway": "away",
                            "score": "80",
                            "team": {"id": "77", "displayName": "Rivermont Gulls"},
                        },
                    ],
                    "status": {
                        "displayClock": "4:55",
                        "period": 4,
                        "type": {"state": "in", "name": "STATUS_IN_PROGRESS"},
                    },
                }
            ],
        }
    }
    state = _parse_summary_state(data, BASKETBALL_LEAGUE, "9100001")
    assert state is not None
    assert state.game_id == "espn:9100001"
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.home_score == 82
    assert state.away_score == 80
    assert state.period_label == "Q4"
    assert state.clock == "4:55"

    assert _parse_summary_state({}, BASKETBALL_LEAGUE, "9100001") is None
    assert _parse_summary_state({"header": {}}, BASKETBALL_LEAGUE, "9100001") is None


def _final_summary(
    *,
    event_id: str,
    status_name: str,
    home_score: str,
    away_score: str,
    home_linescores: list[str],
    away_linescores: list[str],
) -> dict[str, Any]:
    """A finished-game summary header: real ones omit ``status.period``."""
    return {
        "header": {
            "id": event_id,
            "competitions": [
                {
                    "id": event_id,
                    "date": "2026-06-11T00:30Z",
                    "competitors": [
                        {
                            "id": "55",
                            "homeAway": "home",
                            "score": home_score,
                            "linescores": [{"displayValue": value} for value in home_linescores],
                            "team": {"id": "55", "displayName": "Crestfall Lynx"},
                        },
                        {
                            "id": "77",
                            "homeAway": "away",
                            "score": away_score,
                            "linescores": [{"displayValue": value} for value in away_linescores],
                            "team": {"id": "77", "displayName": "Rivermont Gulls"},
                        },
                    ],
                    "status": {
                        "type": {
                            "id": "3",
                            "name": status_name,
                            "state": "post",
                            "completed": True,
                            "description": "Final",
                            "detail": "Final",
                            "shortDetail": "Final",
                        }
                    },
                }
            ],
        }
    }


def test_summary_final_recovers_period_from_linescores() -> None:
    """Final summaries omit ``status.period``; linescores reveal periods played."""
    data = _final_summary(
        event_id="9100030",
        status_name="STATUS_FINAL",
        home_score="107",
        away_score="106",
        home_linescores=["22", "27", "26", "32"],
        away_linescores=["25", "24", "29", "28"],
    )
    state = _parse_summary_state(data, BASKETBALL_LEAGUE, "9100030")
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.period == 4
    assert state.period_label == "Q4"
    assert state.clock is None


def test_summary_final_baseball_uses_longest_linescore_side() -> None:
    # The home side skips the bottom of the 9th when already ahead.
    data = _final_summary(
        event_id="9300030",
        status_name="STATUS_FINAL",
        home_score="5",
        away_score="2",
        home_linescores=["1", "0", "0", "3", "0", "0", "1", "0"],
        away_linescores=["0", "0", "2", "0", "0", "0", "0", "0", "0"],
    )
    state = _parse_summary_state(data, BASEBALL_LEAGUE, "9300030")
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.period == 9
    assert state.period_label == "Inning 9"


def test_summary_full_time_soccer_recovers_second_half() -> None:
    data = _final_summary(
        event_id="9200030",
        status_name="STATUS_FULL_TIME",
        home_score="1",
        away_score="2",
        home_linescores=["0", "1"],
        away_linescores=["1", "1"],
    )
    state = _parse_summary_state(data, SOCCER_LEAGUE, "9200030")
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.period == 2
    assert state.period_label == "2nd Half"
    assert state.clock is None


# ---------------------------------------------------------------------------
# Game summary / box-score drill-down (Phase 7b)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def basketball_summary_data() -> dict[str, Any]:
    with (FIXTURES / "espn_basketball_summary.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def basketball_summary_no_boxscore_data() -> dict[str, Any]:
    path = FIXTURES / "espn_basketball_summary_no_boxscore.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_game_summary_basketball_periods_and_performers(
    basketball_summary_data: dict[str, Any],
) -> None:
    summary = _parse_game_summary(basketball_summary_data, BASKETBALL_LEAGUE, "9100200")
    assert summary is not None
    assert summary.game_id == "espn:9100200"
    # Four quarter columns, both sides aligned, labeled Q1..Q4.
    assert [p.label for p in summary.periods] == ["Q1", "Q2", "Q3", "Q4"]
    assert [(p.home, p.away) for p in summary.periods] == [
        (28, 26),
        (24, 30),
        (31, 22),
        (29, 27),
    ]
    # Per-period home points sum to the final total.
    assert sum(p.home for p in summary.periods) == 112
    assert sum(p.away for p in summary.periods) == 105
    assert summary.home_total == 112
    assert summary.away_total == 105
    # Top performers per side (home first), one per leaders category with a
    # short stat detail; home has points + rebounds leaders, away just points.
    assert [(p.name, p.side, p.detail) for p in summary.performers] == [
        ("Della Marsh", "home", "34 PTS"),
        ("Petra Kohl", "home", "12 REB"),
        ("Soraya Vance", "away", "29 PTS"),
    ]
    # This fixture has no boxscore team statistics → no comparison rows.
    assert summary.team_stats == ()


def test_parse_team_stats_soccer_curated_and_percent() -> None:
    from app.providers.espn import _parse_team_stats

    boxscore = {
        "teams": [
            {
                "homeAway": "home",
                "statistics": [
                    {"name": "possessionPct", "label": "Possession", "displayValue": "53.7"},
                    {"name": "totalShots", "label": "SHOTS", "displayValue": "15"},
                    {"name": "foulsCommitted", "label": "Fouls", "displayValue": "11"},
                ],
            },
            {
                "homeAway": "away",
                "statistics": [
                    {"name": "possessionPct", "label": "Possession", "displayValue": "46.3"},
                    {"name": "totalShots", "label": "SHOTS", "displayValue": "9"},
                    {"name": "foulsCommitted", "label": "Fouls", "displayValue": "14"},
                ],
            },
        ]
    }
    stats = _parse_team_stats(boxscore, Sport.SOCCER)
    # Curated order/labels; percentage stats gain a trailing "%".
    assert [(s.label, s.home, s.away) for s in stats] == [
        ("Possession", "53.7%", "46.3%"),
        ("Shots", "15", "9"),
        ("Fouls", "11", "14"),
    ]


def test_parse_goals_from_key_events() -> None:
    from app.providers.espn import _parse_goals

    data = {
        "keyEvents": [
            {"type": {"type": "kickoff", "text": "Kickoff"}, "scoringPlay": False},
            {
                "type": {"type": "goal", "text": "Goal"},
                "scoringPlay": True,
                "team": {"displayName": "Larkmoor Albion"},
                "participants": [{"athlete": {"displayName": "Ada Frost"}}],
                "clock": {"displayValue": "28'"},
                "text": "Goal! left footed shot",
                "shortText": "Ada Frost Goal",
            },
            {
                "type": {"type": "goal", "text": "Goal"},
                "scoringPlay": True,
                "team": {"displayName": "Larkmoor Albion"},
                "participants": [{"athlete": {"displayName": "Ada Frost"}}],
                "clock": {"displayValue": "67'"},
                "text": "Goal! Penalty scored",
                "shortText": "Ada Frost Penalty",
            },
            {
                "type": {"type": "goal", "text": "Own Goal"},
                "scoringPlay": True,
                "team": {"displayName": "Pellwick Rangers"},
                "participants": [{"athlete": {"displayName": "Sam Reed"}}],
                "text": "Own Goal by Sam Reed",
                "shortText": "Sam Reed Own Goal",
            },
        ]
    }
    goals = _parse_goals(data)
    assert [(g.player, g.team, g.minute, g.penalty, g.own_goal) for g in goals] == [
        ("Ada Frost", "Larkmoor Albion", "28'", False, False),
        ("Ada Frost", "Larkmoor Albion", "67'", True, False),
        ("Sam Reed", "Pellwick Rangers", None, False, True),
    ]


def test_parse_goals_empty_for_non_soccer_payload() -> None:
    from app.providers.espn import _parse_goals

    assert _parse_goals({}) == []
    assert _parse_goals({"keyEvents": []}) == []


def test_parse_team_stats_empty_without_two_sides() -> None:
    from app.providers.espn import _parse_team_stats

    assert _parse_team_stats(None, Sport.SOCCER) == []
    assert _parse_team_stats({"teams": []}, Sport.SOCCER) == []


def test_game_summary_no_boxscore_yields_empty_periods(
    basketball_summary_no_boxscore_data: dict[str, Any],
) -> None:
    summary = _parse_game_summary(basketball_summary_no_boxscore_data, BASKETBALL_LEAGUE, "9100201")
    # A valid header with no linescores/leaders: a summary with empty
    # periods/performers (the detail modal shows scores only), not None.
    assert summary is not None
    assert summary.periods == ()
    assert summary.performers == ()
    assert summary.home_total is None
    assert summary.away_total is None


def test_game_summary_returns_none_without_header() -> None:
    assert _parse_game_summary({}, BASKETBALL_LEAGUE, "9100200") is None
    assert _parse_game_summary({"header": {}}, BASKETBALL_LEAGUE, "9100200") is None
    # Header present but no home/away competitors -> None.
    no_sides = {"header": {"competitions": [{"id": "9100200", "competitors": []}]}}
    assert _parse_game_summary(no_sides, BASKETBALL_LEAGUE, "9100200") is None


def test_game_summary_hockey_period_labels() -> None:
    data = _final_summary(
        event_id="9500200",
        status_name="STATUS_FINAL",
        home_score="2",
        away_score="1",
        home_linescores=["1", "1", "0"],
        away_linescores=["1", "0", "0"],
    )
    summary = _parse_game_summary(data, HOCKEY_LEAGUE, "9500200")
    assert summary is not None
    assert [p.label for p in summary.periods] == ["P1", "P2", "P3"]
    assert [(p.home, p.away) for p in summary.periods] == [(1, 1), (1, 0), (0, 0)]
    assert summary.home_total == 2
    assert summary.away_total == 1


def test_game_summary_basketball_overtime_label() -> None:
    data = _final_summary(
        event_id="9100210",
        status_name="STATUS_FINAL",
        home_score="120",
        away_score="118",
        home_linescores=["28", "24", "31", "25", "12"],
        away_linescores=["26", "30", "22", "30", "10"],
    )
    summary = _parse_game_summary(data, BASKETBALL_LEAGUE, "9100210")
    assert summary is not None
    # A fifth column past the four regulation quarters is overtime.
    assert [p.label for p in summary.periods] == ["Q1", "Q2", "Q3", "Q4", "OT"]


def test_game_summary_baseball_inning_columns_pad_short_side() -> None:
    # The home side, ahead, skips the bottom of the 9th: 8 home / 9 away.
    data = _final_summary(
        event_id="9300210",
        status_name="STATUS_FINAL",
        home_score="4",
        away_score="2",
        home_linescores=["1", "0", "0", "1", "0", "0", "1", "1"],
        away_linescores=["0", "0", "1", "0", "1", "0", "0", "0", "0"],
    )
    summary = _parse_game_summary(data, BASEBALL_LEAGUE, "9300210")
    assert summary is not None
    assert [p.label for p in summary.periods] == [str(n) for n in range(1, 10)]
    # The missing home half-inning is padded with 0.
    assert summary.periods[-1].home == 0
    assert summary.periods[-1].away == 0


async def test_provider_get_game_summary_parses_fixture(
    basketball_summary_data: dict[str, Any],
) -> None:
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        return basketball_summary_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    summary = await provider.get_game_summary(BASKETBALL_LEAGUE, "9100200")
    assert calls == [{"event": "9100200"}]
    assert summary is not None
    assert summary.game_id == "espn:9100200"
    assert len(summary.periods) == 4


async def test_provider_get_game_summary_returns_none_on_http_failure() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("summary endpoint down")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    # Never raises: an HTTP failure degrades to None.
    assert await provider.get_game_summary(BASKETBALL_LEAGUE, "9100200") is None


async def test_provider_get_game_summary_none_for_individual_sports() -> None:
    provider = EspnProvider()
    called = False

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    # Tennis/MMA have no reliable summary endpoint: None without any fetch.
    assert await provider.get_game_summary(TENNIS_LEAGUE, "9700001") is None
    assert await provider.get_game_summary(MMA_LEAGUE, "9700002") is None
    assert called is False


async def test_get_game_summary_propagates_transient_but_swallows_404() -> None:
    from app.providers.http_util import TransientProviderError

    provider = EspnProvider()

    async def transient(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise TransientProviderError("summary endpoint timed out")

    provider._get_json = transient  # type: ignore[method-assign]
    # A sustained transient outage now propagates so the breaker counts it: it
    # is not an httpx error, so the method's `except httpx.HTTPError` won't
    # swallow it (the ESPN side of the dead-breaker fix).
    with pytest.raises(TransientProviderError):
        await provider.get_game_summary(BASKETBALL_LEAGUE, "9100200")

    async def not_found(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        )

    provider._get_json = not_found  # type: ignore[method-assign]
    # A genuine not-found still degrades to None (no breaker hit).
    assert await provider.get_game_summary(BASKETBALL_LEAGUE, "9100200") is None


# ---------------------------------------------------------------------------
# Win-probability series + play-by-play (on the game summary)
# ---------------------------------------------------------------------------


def test_parse_win_probability_scales_and_orders() -> None:
    data = {
        "winprobability": [
            {"homeWinPercentage": 0.5},
            {"homeWinPercentage": 0.62},
            {"homeWinPercentage": 0.31},
        ]
    }
    assert _parse_win_probability(data) == (50.0, 62.0, 31.0)


def test_parse_win_probability_empty() -> None:
    assert _parse_win_probability({}) == ()
    assert _parse_win_probability({"winprobability": []}) == ()
    assert _parse_win_probability("nope") == ()


def test_parse_win_probability_downsamples_long_series() -> None:
    data = {"winprobability": [{"homeWinPercentage": i / 1000} for i in range(1000)]}
    out = _parse_win_probability(data)
    assert len(out) == 160
    assert out[0] == 0.0
    assert out[-1] == round(999 / 1000 * 100, 1)  # last point preserved


def test_play_period_label() -> None:
    assert _play_period_label({"displayValue": "5th Inning"}) == "5th Inning"
    assert _play_period_label({"type": "Bottom", "number": 5}) == "Bottom 5"
    assert _play_period_label({}) == ""


def test_parse_plays_soccer_keyevents() -> None:
    data = {
        "keyEvents": [
            {
                "type": {"text": "Goal"},
                "text": "Goal! Larkmoor Albion 1, Coldstream 0",
                "scoringPlay": True,
                "period": {"number": 1, "displayValue": "1st Half"},
                "clock": {"displayValue": "23'"},
                "team": {"displayName": "Larkmoor Albion"},
            },
            {
                "type": {"text": "Yellow Card"},
                "text": "Booking",
                "scoringPlay": False,
                "period": {"displayValue": "2nd Half"},
            },
        ]
    }
    plays = _parse_plays(data, Sport.SOCCER)
    assert len(plays) == 2
    assert plays[0].scoring is True
    assert plays[0].clock == "23'"
    assert plays[0].team == "Larkmoor Albion"
    assert plays[1].scoring is False
    assert plays[1].period_label == "2nd Half"


def test_parse_plays_us_sport_scoring_plays() -> None:
    data = {
        "scoringPlays": [
            {
                "text": "Home run to deep center",
                "period": {"displayValue": "3rd Inning"},
                "homeScore": 0,
                "awayScore": 2,
                "scoringPlay": True,
                "team": {"displayName": "Meridian Mallards"},
            }
        ]
    }
    plays = _parse_plays(data, Sport.BASEBALL)
    assert len(plays) == 1
    assert plays[0].home_score == 0
    assert plays[0].away_score == 2
    assert plays[0].scoring is True


def test_parse_plays_us_sport_falls_back_to_scoring_filter() -> None:
    data = {
        "plays": [
            {"text": "Strike", "scoringPlay": False, "period": {"displayValue": "1st"}},
            {
                "text": "RBI single",
                "scoringPlay": True,
                "period": {"displayValue": "5th"},
                "homeScore": 1,
                "awayScore": 0,
            },
        ]
    }
    plays = _parse_plays(data, Sport.BASEBALL)
    assert [p.text for p in plays] == ["RBI single"]


def test_parse_plays_empty() -> None:
    assert _parse_plays({}, Sport.BASKETBALL) == []
    assert _parse_plays({"keyEvents": []}, Sport.SOCCER) == []


# ---------------------------------------------------------------------------
# Odds + win-probability (pickcenter + predictor)
# ---------------------------------------------------------------------------

# Fictional sportsbook name — sample data never names a real book.
_PICKCENTER_SAMPLE = {
    "pickcenter": [
        {
            "provider": {"name": "Summit Sportsbook"},
            "details": "CRS -150",
            "spread": -2.5,
            "overUnder": 7.5,
            "homeTeamOdds": {"moneyLine": -150, "favorite": True},
            "awayTeamOdds": {"moneyLine": 130, "favorite": False},
        }
    ]
}

_PREDICTOR_SAMPLE = {
    "homeTeam": {"statistics": [{"name": "gameProjection", "displayValue": "63.0"}]},
    "awayTeam": {"statistics": [{"name": "gameProjection", "displayValue": "37.0"}]},
}


def test_parse_pickcenter_reads_line() -> None:
    provider, details, home_ml, away_ml, spread, over_under = _parse_pickcenter(_PICKCENTER_SAMPLE)
    assert provider == "Summit Sportsbook"
    assert details == "CRS -150"
    assert home_ml == -150
    assert away_ml == 130
    assert spread == -2.5
    assert over_under == 7.5


def test_parse_pickcenter_empty_or_malformed() -> None:
    # Soccer commonly returns an empty/absent pickcenter — every field None.
    assert _parse_pickcenter({"pickcenter": []}) == (None,) * 6
    assert _parse_pickcenter({"pickcenter": [None]}) == (None,) * 6
    assert _parse_pickcenter({}) == (None,) * 6
    assert _parse_pickcenter("nope") == (None,) * 6


def test_parse_predictor_reads_projection() -> None:
    assert _parse_predictor(_PREDICTOR_SAMPLE) == (63.0, 37.0)


def test_parse_predictor_missing_degrades() -> None:
    assert _parse_predictor({}) == (None, None)
    assert _parse_predictor({"homeTeam": {"statistics": []}}) == (None, None)


def test_core_event_path_inserts_leagues_segment() -> None:
    # The core API addresses a league with a `leagues` segment the site API
    # omits — `provider_key` carries the site form, so this MUST re-insert it
    # or the predictor endpoint 404s (no win-probability). Regression guard.
    assert _core_event_path("baseball/mlb") == "baseball/leagues/mlb"
    assert _core_event_path("soccer/fifa.world") == "soccer/leagues/fifa.world"


async def test_provider_get_game_odds_combines_line_and_projection() -> None:
    provider = EspnProvider()
    seen_urls: list[str] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        seen_urls.append(url)
        if "predictor" in url:
            return _PREDICTOR_SAMPLE
        return _PICKCENTER_SAMPLE

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    odds = await provider.get_game_odds(BASKETBALL_LEAGUE, "9100200")
    assert odds is not None
    assert odds.provider == "Summit Sportsbook"
    assert odds.home_moneyline == -150
    assert odds.away_moneyline == 130
    assert odds.spread == -2.5
    assert odds.over_under == 7.5
    assert odds.home_win_pct == 63.0
    assert odds.away_win_pct == 37.0
    # The predictor URL hits the core API with the `/leagues/` segment.
    predictor_url = next(u for u in seen_urls if "predictor" in u)
    assert "/basketball/leagues/crestline/" in predictor_url
    assert "sports.core.api.espn.com" in predictor_url


async def test_provider_get_game_odds_none_for_individual_sports() -> None:
    provider = EspnProvider()
    called = False

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    assert await provider.get_game_odds(TENNIS_LEAGUE, "9700001") is None
    assert await provider.get_game_odds(MMA_LEAGUE, "9700002") is None
    assert called is False


async def test_provider_get_game_odds_predictor_404_keeps_line() -> None:
    """A missing projection (404) still yields the betting line."""
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if "predictor" in url:
            raise httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(404),
            )
        return _PICKCENTER_SAMPLE

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    odds = await provider.get_game_odds(BASKETBALL_LEAGUE, "9100200")
    assert odds is not None
    assert odds.home_moneyline == -150
    assert odds.home_win_pct is None
    assert odds.away_win_pct is None


async def test_provider_get_game_odds_soccer_line_absent_uses_projection() -> None:
    """No pickcenter (typical soccer) but a projection — odds carry win-prob only."""
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if "predictor" in url:
            return _PREDICTOR_SAMPLE
        return {"pickcenter": []}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    odds = await provider.get_game_odds(SOCCER_LEAGUE, "9200300")
    assert odds is not None
    assert odds.provider is None
    assert odds.home_moneyline is None
    assert odds.home_win_pct == 63.0


async def test_provider_get_game_odds_all_empty_is_none() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return {}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    assert await provider.get_game_odds(BASKETBALL_LEAGUE, "9100200") is None


async def test_provider_get_game_odds_propagates_transient() -> None:
    from app.providers.http_util import TransientProviderError

    provider = EspnProvider()

    async def transient(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise TransientProviderError("odds endpoint timed out")

    provider._get_json = transient  # type: ignore[method-assign]
    with pytest.raises(TransientProviderError):
        await provider.get_game_odds(BASKETBALL_LEAGUE, "9100200")


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


def _standing_entry(name: str, stats: dict[str, float]) -> dict[str, Any]:
    return {
        "team": {"displayName": name},
        "stats": [{"name": key, "value": value} for key, value in stats.items()],
    }


def test_parse_standings_soccer_fields() -> None:
    data = {
        "season": {"displayName": "2026"},
        "children": [
            {
                "standings": {
                    "entries": [
                        _standing_entry(
                            "Larkmoor Albion",
                            {
                                "rank": 2,
                                "wins": 10,
                                "losses": 4,
                                "ties": 6,
                                "points": 36,
                                "pointDifferential": 11,
                            },
                        ),
                        _standing_entry(
                            "Pellwick Rangers",
                            {
                                "rank": 1,
                                "wins": 13,
                                "losses": 3,
                                "ties": 4,
                                "points": 43,
                                "pointDifferential": 19,
                            },
                        ),
                        "not-an-entry",
                    ]
                }
            }
        ],
    }
    standings = _parse_standings(data, SOCCER_LEAGUE)
    assert standings.league_id == SOCCER_LEAGUE.id
    assert standings.season == "2026"
    assert [row.team_name for row in standings.rows] == [
        "Pellwick Rangers",
        "Larkmoor Albion",
    ]
    assert [row.rank for row in standings.rows] == [1, 2]
    # A nameless child block carries no group label.
    assert all(row.group is None for row in standings.rows)
    top = standings.rows[0]
    assert (top.wins, top.losses, top.draws, top.points, top.goal_diff) == (13, 3, 4, 43, 19)
    assert top.win_pct is None and top.games_back is None


def test_parse_standings_carries_team_logo_abbr_color() -> None:
    # Every row carries its crest/abbr/color (from entry.team) so the table
    # shows logos for all teams, not just the followed handful.
    data = {
        "standings": {
            "entries": [
                {
                    "team": {
                        "id": "501",
                        "displayName": "Gravenford Owls",
                        "abbreviation": "GVO",
                        "color": "1d428a",
                        "logos": [{"href": "https://cdn.example/gvo.png", "rel": ["default"]}],
                    },
                    "stats": [
                        {"name": "rank", "value": 1},
                        {"name": "wins", "value": 30},
                        {"name": "losses", "value": 10},
                    ],
                },
            ]
        }
    }
    standings = _parse_standings(data, BASKETBALL_LEAGUE)
    row = standings.rows[0]
    assert row.logo_url == "https://cdn.example/gvo.png"
    assert row.abbreviation == "GVO"
    assert row.color == "#1d428a"  # bare hex normalized to "#"-prefixed


def test_parse_standings_basketball_fields() -> None:
    data = {
        "standings": {
            "entries": [
                _standing_entry(
                    "Gravenford Owls",
                    {"rank": 1, "wins": 30, "losses": 10, "winPercent": 0.75, "gamesBehind": 0.0},
                ),
                _standing_entry(
                    "Telmark Foxes",
                    {"rank": 2, "wins": 26, "losses": 14, "winPercent": 0.65, "gamesBehind": 4.0},
                ),
            ]
        }
    }
    standings = _parse_standings(data, BASKETBALL_LEAGUE)
    assert [row.rank for row in standings.rows] == [1, 2]
    # The un-nested top-level standings block carries no group.
    assert all(row.group is None for row in standings.rows)
    second = standings.rows[1]
    assert second.win_pct == 0.65
    assert second.games_back == 4.0
    assert second.draws is None and second.points is None and second.goal_diff is None


def test_parse_standings_groups_from_children_names() -> None:
    data = {
        "season": {"displayName": "2026"},
        "children": [
            {
                "name": "Eastreach Conference",
                "abbreviation": "ER",
                "standings": {
                    "entries": [
                        _standing_entry(
                            "Gravenford Owls",
                            {"rank": 2, "wins": 26, "losses": 14, "winPercent": 0.65},
                        ),
                        _standing_entry(
                            "Telmark Foxes",
                            {"rank": 1, "wins": 30, "losses": 10, "winPercent": 0.75},
                        ),
                    ]
                },
            },
            {
                "name": "Westmark Conference",
                "abbreviation": "WM",
                "standings": {
                    "entries": [
                        _standing_entry(
                            "Wrenfield Stags",
                            {"rank": 1, "wins": 28, "losses": 12, "winPercent": 0.7},
                        ),
                        _standing_entry(
                            "Dunwick Spires",
                            {"rank": 2, "wins": 22, "losses": 18, "winPercent": 0.55},
                        ),
                    ]
                },
            },
        ],
    }
    standings = _parse_standings(data, BASKETBALL_LEAGUE)
    # Flattened group-by-group in payload order, rank-sorted within each.
    assert [(row.group, row.rank, row.team_name) for row in standings.rows] == [
        ("Eastreach Conference", 1, "Telmark Foxes"),
        ("Eastreach Conference", 2, "Gravenford Owls"),
        ("Westmark Conference", 1, "Wrenfield Stags"),
        ("Westmark Conference", 2, "Dunwick Spires"),
    ]


def test_parse_standings_mixed_top_level_and_children_blocks() -> None:
    data = {
        "standings": {
            "entries": [
                _standing_entry("Bellharbor Manticores", {"rank": 1, "wins": 31, "losses": 9})
            ]
        },
        "children": [
            {
                "abbreviation": "ER",  # no name: abbreviation is the fallback
                "standings": {
                    "entries": [
                        _standing_entry("Telmark Foxes", {"rank": 1, "wins": 30, "losses": 10})
                    ]
                },
            }
        ],
    }
    standings = _parse_standings(data, BASKETBALL_LEAGUE)
    assert [(row.group, row.rank, row.team_name) for row in standings.rows] == [
        (None, 1, "Bellharbor Manticores"),
        ("ER", 1, "Telmark Foxes"),
    ]


def test_parse_standings_hockey_two_conferences() -> None:
    """Hockey rows: W/L/OTL/points, playoffSeed rank, conference groups."""
    summary_stats = [
        # Null-valued summary stats as live NHL payloads carry them
        # ("overall" has value null + a non-numeric displayValue).
        {"name": "overall", "value": None, "displayValue": "53-22-7, 113 PTS"},
    ]
    glacierport = _standing_entry(
        "Glacierport Narwhals",
        {"playoffSeed": 1, "wins": 53, "losses": 22, "otLosses": 7, "points": 113},
    )
    glacierport["stats"].extend(summary_stats)
    embermont = _standing_entry(
        "Embermont Yetis",
        # The same value also arrives under "overtimeLosses"; either
        # spelling must be accepted.
        {"playoffSeed": 2, "wins": 48, "losses": 26, "overtimeLosses": 8, "points": 104},
    )
    data = {
        "season": {"displayName": "2025-26"},
        "children": [
            {
                "name": "Eastreach Conference",
                "abbreviation": "ER",
                "standings": {"entries": [embermont, glacierport]},
            },
            {
                "name": "Westmark Conference",
                "abbreviation": "WM",
                "standings": {
                    "entries": [
                        _standing_entry(
                            "Frosthelm Wardens",
                            {
                                "playoffSeed": 1,
                                "wins": 50,
                                "losses": 24,
                                "otLosses": 8,
                                "points": 108,
                            },
                        )
                    ]
                },
            },
        ],
    }
    standings = _parse_standings(data, HOCKEY_LEAGUE)
    assert standings.season == "2025-26"
    assert [(row.group, row.rank, row.team_name) for row in standings.rows] == [
        ("Eastreach Conference", 1, "Glacierport Narwhals"),
        ("Eastreach Conference", 2, "Embermont Yetis"),
        ("Westmark Conference", 1, "Frosthelm Wardens"),
    ]
    top = standings.rows[0]
    assert (top.wins, top.losses, top.ot_losses, top.points) == (53, 22, 7, 113)
    assert top.draws is None and top.win_pct is None and top.games_back is None
    second = standings.rows[1]
    assert second.ot_losses == 8  # via the "overtimeLosses" spelling
    assert second.points == 104


def test_parse_standings_football_ties() -> None:
    data = {
        "season": {"displayName": "2026"},
        "children": [
            {
                "name": "Harvest Conference",
                "standings": {
                    "entries": [
                        _standing_entry(
                            "Cinderfall Drakes",
                            {
                                "playoffSeed": 2,
                                "wins": 10,
                                "losses": 6,
                                "ties": 1,
                                "winPercent": 0.618,
                            },
                        ),
                        _standing_entry(
                            "Harrowgate Plowmen",
                            {
                                "playoffSeed": 1,
                                "wins": 13,
                                "losses": 4,
                                "ties": 0,
                                "winPercent": 0.765,
                            },
                        ),
                        # No winPercent stat: derived with ties as half-wins.
                        _standing_entry(
                            "Mossbridge Anvils",
                            {"playoffSeed": 3, "wins": 8, "losses": 8, "ties": 1},
                        ),
                    ]
                },
            }
        ],
    }
    standings = _parse_standings(data, FOOTBALL_LEAGUE)
    assert [(row.rank, row.team_name) for row in standings.rows] == [
        (1, "Harrowgate Plowmen"),
        (2, "Cinderfall Drakes"),
        (3, "Mossbridge Anvils"),
    ]
    assert all(row.group == "Harvest Conference" for row in standings.rows)
    top = standings.rows[0]
    assert (top.wins, top.losses, top.draws, top.win_pct) == (13, 4, 0, 0.765)
    assert top.points is None and top.ot_losses is None and top.games_back is None
    derived = standings.rows[2]
    assert derived.draws == 1
    assert derived.win_pct == 0.5  # (8 + 0.5) / 17


def test_parse_standings_inline_subgroup_when_only_one_level() -> None:
    """A child whose own entries are the table -> group set, subgroup None
    (the single-level shape: today's conference-only / one-table leagues)."""
    data = {
        "season": {"displayName": "2026"},
        "children": [
            {
                "name": "English Premier League 2025-2026",
                "standings": {
                    "entries": [
                        _standing_entry("Pellwick Rangers", {"rank": 1, "wins": 13, "losses": 3})
                    ]
                },
            }
        ],
    }
    standings = _parse_standings(data, SOCCER_LEAGUE)
    assert [(row.group, row.subgroup, row.team_name) for row in standings.rows] == [
        ("English Premier League 2025-2026", None, "Pellwick Rangers")
    ]


def test_parse_standings_division_nested_children_of_children() -> None:
    """``?level=3`` shape: conference children carry division children.

    Each row gets group=conference + subgroup=division, ranks restart per
    the finest (division) grouping, and groups stay in payload order.
    """
    with (FIXTURES / "espn_basketball_standings_divisions.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    standings = _parse_standings(data, BASKETBALL_LEAGUE)
    assert standings.season == "2025-26"
    assert [(row.group, row.subgroup, row.rank, row.team_name) for row in standings.rows] == [
        ("Eastreach Conference", "Tidewater Division", 1, "Telmark Foxes"),
        ("Eastreach Conference", "Tidewater Division", 2, "Gravenford Owls"),
        ("Eastreach Conference", "Highland Division", 1, "Bellharbor Manticores"),
        ("Eastreach Conference", "Highland Division", 2, "Marrow Creek Bisons"),
        ("Westmark Conference", "Sandsea Division", 1, "Wrenfield Stags"),
        ("Westmark Conference", "Sandsea Division", 2, "Dunwick Spires"),
        ("Westmark Conference", "Summit Division", 1, "Ironvale Cougars"),
    ]
    # The empty conference-level ``standings.entries`` produce no rows; only
    # the division (finest) tables are flattened.
    assert all(row.subgroup is not None for row in standings.rows)


async def test_get_standings_requests_division_depth() -> None:
    """``get_standings`` asks ESPN for division depth (``level=3``)."""
    provider = EspnProvider()
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((url, params))
        with (FIXTURES / "espn_basketball_standings_divisions.json").open(
            encoding="utf-8"
        ) as handle:
            return json.load(handle)

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    standings = await provider.get_standings(BASKETBALL_LEAGUE)
    assert calls and calls[0][1] == {"level": "3"}
    assert standings.rows[0].subgroup == "Tidewater Division"


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


def test_parse_roster_grouped_athletes_and_injuries() -> None:
    team = Team(
        id="gravenford-owls",
        league_id=BASKETBALL_LEAGUE.id,
        name="Gravenford Owls",
        abbreviation="GRV",
        provider_key="41",
    )
    data = {
        "athletes": [
            {
                "position": "guards",
                "items": [
                    {
                        "id": 8001,
                        "fullName": "Orin Latchford",
                        "jersey": "7",
                        "position": {"abbreviation": "PG"},
                        "injuries": [],
                    },
                    {
                        "id": 8002,
                        "fullName": "Bram Hollowell",
                        "jersey": 23,
                        "position": {"abbreviation": "SG"},
                        "injuries": [{"status": "Out", "details": {"type": "Ankle"}}],
                    },
                ],
            },
            {
                "id": 8003,
                "fullName": "Cassius Wrenmore",
                "jersey": "31",
                "position": {"abbreviation": "C"},
                "injuries": [{"status": "Day-To-Day"}],
            },
            {"jersey": "99"},
        ]
    }
    roster = _parse_roster(data, BASKETBALL_LEAGUE, team)
    assert roster.team_id == team.id
    assert len(roster.players) == 3  # the id/name-less athlete is skipped

    by_id = {player.id: player for player in roster.players}
    assert by_id["espn:8001"].status is PlayerStatus.ACTIVE
    assert by_id["espn:8001"].status_detail is None
    assert by_id["espn:8001"].jersey_number == "7"

    assert by_id["espn:8002"].status is PlayerStatus.OUT
    assert by_id["espn:8002"].status_detail == "Out - Ankle"
    assert by_id["espn:8002"].jersey_number == "23"

    assert by_id["espn:8003"].status is PlayerStatus.DAY_TO_DAY
    assert by_id["espn:8003"].status_detail == "Day-To-Day"


def test_parse_roster_injuries_win_over_active_status_type() -> None:
    """NHL/NFL rosters keep ``status.type`` "active" while ``injuries[]``
    says otherwise (verified live); the injury list must win."""
    team = Team(
        id="glacierport-narwhals",
        league_id=HOCKEY_LEAGUE.id,
        name="Glacierport Narwhals",
        abbreviation="GPN",
        provider_key="301",
    )
    active_status = {"id": "1", "name": "Active", "type": "active", "abbreviation": "Active"}
    data = {
        "athletes": [
            {
                "position": "Centers",
                "items": [
                    {
                        "id": 8101,
                        "fullName": "Anders Velkstrom",
                        "jersey": "19",
                        "position": {"abbreviation": "C"},
                        "status": active_status,
                        # NHL injury entries carry no details dict.
                        "injuries": [{"status": "Out", "date": "2026-06-01T17:21Z"}],
                    },
                    {
                        "id": 8102,
                        "fullName": "Marek Tindwall",
                        "jersey": "8",
                        "position": {"abbreviation": "D"},
                        "status": active_status,
                        "injuries": [{"status": "Questionable", "date": "2026-06-10T15:08Z"}],
                    },
                    {
                        "id": 8103,
                        "fullName": "Joren Aspvik",
                        "jersey": "31",
                        "position": {"abbreviation": "G"},
                        "status": active_status,
                        "injuries": [{"status": "Injured Reserve", "date": "2026-05-12T11:00Z"}],
                    },
                ],
            }
        ]
    }
    roster = _parse_roster(data, HOCKEY_LEAGUE, team)
    by_id = {player.id: player for player in roster.players}

    assert by_id["espn:8101"].status is PlayerStatus.OUT
    assert by_id["espn:8101"].status_detail == "Out"

    assert by_id["espn:8102"].status is PlayerStatus.DAY_TO_DAY
    assert by_id["espn:8102"].status_detail == "Questionable"

    assert by_id["espn:8103"].status is PlayerStatus.INJURED
    assert by_id["espn:8103"].status_detail == "Injured Reserve"


def test_parse_roster_status_type_fallback_without_injuries() -> None:
    """With no injuries[] entry, a non-active ``status.type`` still maps."""
    team = Team(
        id="harrowgate-plowmen",
        league_id=FOOTBALL_LEAGUE.id,
        name="Harrowgate Plowmen",
        abbreviation="HGP",
        provider_key="401",
    )
    data = {
        "athletes": [
            {
                "id": 8201,
                "fullName": "Corwin Bramleigh",
                "status": {"id": "5", "name": "Day-To-Day", "type": "day-to-day"},
                "injuries": [],
            },
            {
                "id": 8202,
                "fullName": "Stellan Mossworth",
                "status": {"id": "3", "name": "Injured Reserve", "type": "injured-reserve"},
            },
            {
                "id": 8203,
                "fullName": "Tobias Fenncroft",
                "status": {"id": "1", "name": "Active", "type": "active"},
                "injuries": [],
            },
        ]
    }
    roster = _parse_roster(data, FOOTBALL_LEAGUE, team)
    by_id = {player.id: player for player in roster.players}

    assert by_id["espn:8201"].status is PlayerStatus.DAY_TO_DAY
    assert by_id["espn:8201"].status_detail == "Day-To-Day"

    assert by_id["espn:8202"].status is PlayerStatus.INJURED
    assert by_id["espn:8202"].status_detail == "Injured Reserve"

    assert by_id["espn:8203"].status is PlayerStatus.ACTIVE
    assert by_id["espn:8203"].status_detail is None


# ---------------------------------------------------------------------------
# Player stat lines (athlete "overview" -> Player.stat_line)
# ---------------------------------------------------------------------------


def test_format_stat_line_per_sport() -> None:
    # Basketball: PPG · REB · AST, in roster order.
    assert (
        _format_stat_line(
            Sport.BASKETBALL,
            {"avgPoints": "24.1", "avgRebounds": "7.8", "avgAssists": "5.2"},
        )
        == "24.1 PPG · 7.8 REB · 5.2 AST"
    )
    # Hockey: G · A · PTS.
    assert (
        _format_stat_line(Sport.HOCKEY, {"goals": "31", "assists": "44", "points": "75"})
        == "31 G · 44 A · 75 PTS"
    )
    # Baseball batter: AVG (derived from hits/at-bats) · HR · RBI.
    assert (
        _format_stat_line(
            Sport.BASEBALL,
            {"hits": "162", "atBats": "557", "homeRuns": "22", "RBIs": "74"},
        )
        == ".291 AVG · 22 HR · 74 RBI"
    )
    # Baseball pitcher: ERA · K, ERA derived from earned runs over innings
    # (``178.0`` IP, 62 ER -> 3.13; no ``atBats`` -> not read as a batter).
    assert (
        _format_stat_line(
            Sport.BASEBALL,
            {"innings": "178.0", "earnedRuns": "62", "strikeouts": "178", "homeRuns": "18"},
        )
        == "3.13 ERA · 178 K"
    )
    # ESPN-supplied ERA is used verbatim when present.
    assert (
        _format_stat_line(Sport.BASEBALL, {"innings": "200.0", "ERA": "3.12", "strikeouts": "210"})
        == "3.12 ERA · 210 K"
    )
    # Innings ``27.2`` is 27 + 2/3 IP, not 27.2 decimal: 12 ER -> 3.90 ERA.
    assert (
        _format_stat_line(
            Sport.BASEBALL, {"innings": "27.2", "earnedRuns": "12", "strikeouts": "33"}
        )
        == "3.90 ERA · 33 K"
    )
    # Football QB: TD · INT · YDS (commas in the YDS display preserved).
    assert (
        _format_stat_line(
            Sport.FOOTBALL,
            {"passingTouchdowns": "28", "interceptions": "9", "passingYards": "4,015"},
        )
        == "28 TD · 9 INT · 4,015 YDS"
    )
    # Soccer: G · A.
    assert _format_stat_line(Sport.SOCCER, {"goals": "12", "assists": "5"}) == "12 G · 5 A"


def test_format_stat_line_pass_catcher_with_trick_play_carries() -> None:
    # A TE/WR career split carries a few trick-play rushing attempts; the
    # line must reflect the dominant RECEIVING production, not the rushing
    # yards (regression: the rushing branch used to win on any carry).
    kmet_career = {
        "receptions": "288",
        "receivingYards": "2,939",
        "receivingTouchdowns": "21",
        "rushingAttempts": "7",
        "rushingYards": "8",
        "rushingTouchdowns": "0",
    }
    assert _format_stat_line(Sport.FOOTBALL, kmet_career) == "288 REC · 2,939 YDS · 21 TD"
    # A true running back (rushing >> receiving) still shows the rushing line.
    rb = {
        "rushingAttempts": "240",
        "rushingYards": "1,087",
        "rushingTouchdowns": "9",
        "receptions": "42",
        "receivingYards": "380",
    }
    assert _format_stat_line(Sport.FOOTBALL, rb) == "1,087 YDS · 9 TD"


def test_format_stat_line_none_when_no_relevant_stats() -> None:
    # A defender's offensive-only zeros yield no football skill line.
    assert (
        _format_stat_line(
            Sport.FOOTBALL,
            {"receptions": "0", "receivingYards": "0", "rushingAttempts": "0"},
        )
        is None
    )
    # Basketball with no scoring average -> no line.
    assert _format_stat_line(Sport.BASKETBALL, {"avgRebounds": "7.8"}) is None
    # Individual sports never carry a roster line.
    assert _format_stat_line(Sport.TENNIS, {"avgPoints": "10"}) is None


def test_stat_line_from_overview_prefers_regular_season() -> None:
    with (FIXTURES / "espn_basketball_overview.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    # Regular Season split wins over the (alphabetically earlier) Career split.
    assert _stat_line_from_overview(data, Sport.BASKETBALL) == "24.1 PPG · 7.8 REB · 5.2 AST"


def test_stat_line_from_overview_baseball_batter() -> None:
    with (FIXTURES / "espn_baseball_overview_batter.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert _stat_line_from_overview(data, Sport.BASEBALL) == ".291 AVG · 22 HR · 74 RBI"


def test_stat_line_from_overview_none_without_statistics() -> None:
    with (FIXTURES / "espn_overview_no_stats.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert _stat_line_from_overview(data, Sport.BASKETBALL) is None
    # A bare/garbage payload is also tolerated (never raises).
    assert _stat_line_from_overview({}, Sport.BASKETBALL) is None
    assert _stat_line_from_overview("not-a-dict", Sport.BASKETBALL) is None


def test_career_line_from_overview_picks_career_split() -> None:
    with (FIXTURES / "espn_basketball_overview.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    # The Career split (not Regular Season) drives the career line.
    assert _career_line_from_overview(data, Sport.BASKETBALL) == "22.9 PPG · 8.4 REB · 5.7 AST"


def test_career_line_from_overview_none_without_career_split() -> None:
    # A payload with only a Regular Season split has no career line.
    season_only = {
        "statistics": {
            "names": ["avgPoints"],
            "splits": [{"displayName": "Regular Season", "stats": ["24.1"]}],
        }
    }
    assert _career_line_from_overview(season_only, Sport.BASKETBALL) is None
    assert _career_line_from_overview({}, Sport.BASKETBALL) is None
    assert _career_line_from_overview("not-a-dict", Sport.BASKETBALL) is None


def test_parse_athlete_extracts_headshot() -> None:
    team = Team(
        id="gravenford-owls",
        league_id=BASKETBALL_LEAGUE.id,
        name="Gravenford Owls",
        abbreviation="GRV",
        provider_key="501",
    )
    player = _parse_athlete(
        {
            "id": "9001",
            "fullName": "Cassius Wrenmore",
            "position": {"abbreviation": "PG"},
            "jersey": "7",
            "headshot": {"href": "https://a.espncdn.com/i/headshots/x.png"},
        },
        team,
    )
    assert player is not None
    assert player.photo_url == "https://a.espncdn.com/i/headshots/x.png"
    # A missing/empty headshot degrades to None (never raises).
    no_photo = _parse_athlete({"id": "9002", "fullName": "No Photo"}, team)
    assert no_photo is not None
    assert no_photo.photo_url is None


async def test_get_roster_attaches_stat_lines_and_tolerates_missing() -> None:
    """get_roster fans out to per-athlete overviews; a player with no
    statistics keeps stat_line None, and the rest are populated."""
    team = Team(
        id="gravenford-owls",
        league_id=BASKETBALL_LEAGUE.id,
        name="Gravenford Owls",
        abbreviation="GRV",
        provider_key="501",
    )
    with (FIXTURES / "espn_basketball_roster.json").open(encoding="utf-8") as handle:
        roster_payload = json.load(handle)
    with (FIXTURES / "espn_basketball_overview.json").open(encoding="utf-8") as handle:
        overview_payload = json.load(handle)
    with (FIXTURES / "espn_overview_no_stats.json").open(encoding="utf-8") as handle:
        no_stats_payload = json.load(handle)

    provider = EspnProvider()
    overview_urls: list[str] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if url.endswith("/roster"):
            return roster_payload
        overview_urls.append(url)
        if url.endswith("/athletes/9003/overview"):
            # Cassius Wrenmore has no statistics -> stat_line stays None.
            return no_stats_payload
        if url.endswith("/athletes/9004/overview"):
            # Simulate an upstream failure for one athlete; must not raise.
            raise httpx.ConnectError("overview down")
        return overview_payload

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    roster = await provider.get_roster(BASKETBALL_LEAGUE, team)

    by_id = {player.id: player for player in roster.players}
    assert len(by_id) == 4
    # The overview is fetched once per athlete (using the league's
    # basketball/crestline path) — the bare athlete id, no espn: prefix.
    assert any(
        url.endswith("/basketball/crestline/athletes/9001/overview") for url in overview_urls
    )

    assert by_id["espn:9001"].stat_line == "24.1 PPG · 7.8 REB · 5.2 AST"
    assert by_id["espn:9002"].stat_line == "24.1 PPG · 7.8 REB · 5.2 AST"
    assert by_id["espn:9003"].stat_line is None  # no statistics in overview
    assert by_id["espn:9004"].stat_line is None  # overview call failed
    # The same single overview fetch also yields the career line.
    assert by_id["espn:9001"].career_stat_line == "22.9 PPG · 8.4 REB · 5.7 AST"
    assert by_id["espn:9003"].career_stat_line is None
    assert by_id["espn:9004"].career_stat_line is None


async def test_get_roster_individual_sport_skips_stat_lines() -> None:
    """Tennis/MMA "teams" are single athletes with empty rosters; no
    overview calls happen."""
    team = Team(
        id="vance-orrick",
        league_id=TENNIS_LEAGUE.id,
        name="Vance Orrick",
        abbreviation="ORR",
        provider_key="7001",
    )
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise AssertionError(f"individual sport must not fetch: {url}")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    roster = await provider.get_roster(TENNIS_LEAGUE, team)
    assert roster.players == ()


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def news_data() -> dict[str, Any]:
    with (FIXTURES / "espn_news.json").open(encoding="utf-8") as handle:
        return json.load(handle)


NEWS_TEAM = Team(
    id="gravenford-owls",
    league_id=BASKETBALL_LEAGUE.id,
    name="Gravenford Owls",
    abbreviation="GRV",
    provider_key="41",
)


def test_parse_news_skips_premium_and_linkless_articles(news_data: dict[str, Any]) -> None:
    items = _parse_news(news_data, team=NEWS_TEAM)
    # Fixture holds 4 articles: one premium and one without a web link
    # must be dropped, leaving the playoff story and the injury note.
    assert [item.title for item in items] == [
        "Gravenford Owls clinch a playoff berth behind Latchford's 41 points",
        "Bram Hollowell out two weeks with an ankle sprain",
    ]
    assert all(item.team_id == NEWS_TEAM.id for item in items)
    assert all(item.league_id is None for item in items)
    assert all(item.source == "ESPN" for item in items)


def test_parse_news_league_scope_tags_league_not_team(news_data: dict[str, Any]) -> None:
    # A whole-competition feed: no team filter, items keyed by league_id with
    # team_id left None.
    items = _parse_news(news_data, league_id="worldcup")
    assert items, "expected the same non-premium, linked articles"
    assert all(item.team_id is None for item in items)
    assert all(item.league_id == "worldcup" for item in items)


def test_parse_news_fields(news_data: dict[str, Any]) -> None:
    items = _parse_news(news_data, team=NEWS_TEAM)
    story = items[0]
    assert story.url == "https://sports.example.com/crestline/story/owls-clinch-playoff-berth"
    assert story.id == hashlib.sha1(story.url.encode("utf-8")).hexdigest()[:16]
    assert story.published_at == datetime(2026, 6, 11, 14, 5, tzinfo=timezone.utc)
    assert story.summary is not None and story.summary.startswith("Orin Latchford")
    assert story.image_url == "https://img.example.com/photo/2026/0611/owls-clinch_16-9.jpg"

    injury_note = items[1]  # null description, empty images list
    assert injury_note.summary is None
    assert injury_note.image_url is None
    assert injury_note.published_at == datetime(2026, 6, 9, 21, 45, tzinfo=timezone.utc)


def test_parse_news_tolerates_malformed_payloads() -> None:
    assert _parse_news(None, team=NEWS_TEAM) == []
    assert _parse_news({}, team=NEWS_TEAM) == []
    assert _parse_news({"articles": "nope"}, team=NEWS_TEAM) == []
    assert (
        _parse_news({"articles": ["not-an-article", {"headline": "No link"}]}, team=NEWS_TEAM) == []
    )


async def test_get_news_queries_team_endpoint(news_data: dict[str, Any]) -> None:
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = params
        return news_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    items = await provider.get_news(BASKETBALL_LEAGUE, NEWS_TEAM)
    assert captured["url"].endswith("/basketball/crestline/news")
    assert captured["params"] == {"team": "41", "limit": "20"}
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Team location (map view): /teams/{id} venue parsing
# ---------------------------------------------------------------------------

LOCATION_TEAM = Team(
    id="crestline-foxglen-wardens",
    league_id=BASKETBALL_LEAGUE.id,
    name="Foxglen Wardens",
    abbreviation="FOX",
    provider_key="8801",
)


@pytest.fixture(scope="module")
def team_venue_data() -> dict[str, Any]:
    with (FIXTURES / "espn_team_venue.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def test_parse_team_location_reads_franchise_venue(team_venue_data: dict[str, Any]) -> None:
    """US franchise sports carry the venue under team.franchise.venue.

    The geocode query combines the full name with the address city/state/
    country so Nominatim can resolve a common stadium name; ESPN ships no
    usable coordinates here, so lat/lon are None for the geocoder to fill.
    """
    location = _parse_team_location(team_venue_data)
    assert location is not None
    assert location.venue == "Foxglen Coliseum, Foxglen, CA, USA"
    assert location.lat is None
    assert location.lon is None


def test_parse_team_location_reads_top_level_venue() -> None:
    data = {
        "team": {
            "venue": {
                "fullName": "Harborline Park",
                "address": {"city": "Harborline", "state": "ME"},
            }
        }
    }
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "Harborline Park, Harborline, ME"
    assert (location.lat, location.lon) == (None, None)


def test_parse_team_location_prefers_top_level_over_franchise() -> None:
    """A populated top-level venue wins over the franchise venue."""
    data = {
        "team": {
            "venue": {"fullName": "Primary Stadium", "address": {"city": "Cinderbay"}},
            "franchise": {"venue": {"fullName": "Old Ground"}},
        }
    }
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "Primary Stadium, Cinderbay"


def test_parse_team_location_none_when_no_venue() -> None:
    """Soccer teams expose no venue on /teams/{id} — returns None.

    (The scheduler then falls back to the team's most common home-game
    venue from stored games before geocoding.)
    """
    assert _parse_team_location({"team": {"venue": None, "franchise": None}}) is None
    assert _parse_team_location({"team": {}}) is None


def _soccer_team_with_next_event(home_id: str) -> dict[str, Any]:
    """A soccer /teams/{id} payload: no venue, but a nextEvent at a stadium."""
    return {
        "team": {
            "id": "160",
            "venue": None,
            "franchise": None,
            "nextEvent": [
                {
                    "competitions": [
                        {
                            "venue": {
                                "fullName": "Parc des Princes",
                                "address": {"city": "Paris", "country": "France"},
                            },
                            "competitors": [
                                {"homeAway": "home", "id": home_id},
                                {"homeAway": "away", "id": "169"},
                            ],
                        }
                    ]
                }
            ],
        }
    }


def test_parse_team_location_uses_next_event_home_venue() -> None:
    """Soccer clubs get their venue from nextEvent when they're the home side.

    ESPN omits the venue on the soccer team payload, but the next fixture's
    competition venue is the home side's ground — used when our provider_key
    is the home competitor (resolves e.g. PSG → Parc des Princes).
    """
    data = _soccer_team_with_next_event(home_id="160")
    location = _parse_team_location(data, provider_key="160")
    assert location is not None
    assert location.venue == "Parc des Princes, Paris, France"
    assert (location.lat, location.lon) == (None, None)


def test_parse_team_location_ignores_next_event_when_away() -> None:
    """An away next match must not strand the club at the opponent's stadium."""
    data = _soccer_team_with_next_event(home_id="169")  # we (160) are away
    assert _parse_team_location(data, provider_key="160") is None


def test_parse_team_location_next_event_needs_provider_key() -> None:
    """Without a provider_key we can't tell whose ground it is — skip it."""
    data = _soccer_team_with_next_event(home_id="160")
    assert _parse_team_location(data) is None
    assert _parse_team_location({}) is None
    assert _parse_team_location(None) is None
    # A venue object with no usable name is also None.
    assert _parse_team_location({"team": {"venue": {"address": {"city": "X"}}}}) is None


def test_parse_team_location_falls_back_to_short_name() -> None:
    data = {"team": {"venue": {"shortName": "The Hollow"}}}
    location = _parse_team_location(data)
    assert location is not None
    assert location.venue == "The Hollow"


async def test_get_team_location_queries_team_endpoint(team_venue_data: dict[str, Any]) -> None:
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = params
        return team_venue_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    location = await provider.get_team_location(BASKETBALL_LEAGUE, LOCATION_TEAM)
    assert captured["url"].endswith("/basketball/crestline/teams/8801")
    assert captured["params"] is None
    assert location is not None
    assert location.venue == "Foxglen Coliseum, Foxglen, CA, USA"
    assert (location.lat, location.lon) == (None, None)


async def test_get_team_location_returns_none_on_http_failure() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("teams endpoint down")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    # Never raises: a transport failure degrades to None.
    assert await provider.get_team_location(BASKETBALL_LEAGUE, LOCATION_TEAM) is None


# ---------------------------------------------------------------------------
# Provider object
# ---------------------------------------------------------------------------


async def test_live_games_scoreboard_passes_limit(scoreboard_data: dict[str, Any]) -> None:
    """ESPN silently caps scoreboards at 100 events; limit=400 must be sent."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = params
        return scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_live_games(BASKETBALL_LEAGUE)
    assert captured["url"].endswith("/basketball/crestline/scoreboard")
    assert captured["params"] is not None
    assert captured["params"]["limit"] == "400"
    assert len(captured["params"]["dates"]) == 8  # YYYYMMDD
    assert len(games) == 2


async def test_live_games_scoreboard_date_is_eastern_not_utc(
    scoreboard_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ESPN buckets scoreboard days by US/Eastern (espn.py documents that a
    UTC date after 8pm ET 'silently misses every live evening game'). At
    01:30 UTC on Jan 2 — 20:30 ET on Jan 1 — the requested date must be the
    previous ET day, not the UTC one."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["params"] = params
        return scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    monkeypatch.setattr(
        timeutil,
        "utcnow",
        lambda: datetime(2026, 1, 2, 1, 30, tzinfo=timezone.utc),
    )
    await provider.get_live_games(BASKETBALL_LEAGUE)
    assert captured["params"]["dates"] == "20260101"


async def test_provider_close_without_use_is_safe() -> None:
    provider = EspnProvider()
    assert provider.provider_id == "espn"
    await provider.close()  # never created a client; must be a no-op
    assert provider._client is None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_includes_nhl_and_nfl() -> None:
    nhl = get_catalog_league("nhl")
    assert nhl is not None
    assert (nhl.name, nhl.sport, nhl.provider, nhl.provider_key) == (
        "NHL",
        Sport.HOCKEY,
        "espn",
        "hockey/nhl",
    )

    nfl = get_catalog_league("nfl")
    assert nfl is not None
    assert (nfl.name, nfl.sport, nfl.provider, nfl.provider_key) == (
        "NFL",
        Sport.FOOTBALL,
        "espn",
        "football/nfl",
    )


# ---------------------------------------------------------------------------
# Whole-competition schedule (follow_all): ranged scoreboard + chunking
# ---------------------------------------------------------------------------


def test_chunk_date_range_short_range_is_single_chunk() -> None:
    """A range within the 45-day budget is fetched in one call."""
    chunks = _chunk_date_range(date(2026, 6, 11), date(2026, 7, 19))
    assert chunks == [(date(2026, 6, 11), date(2026, 7, 19))]


def test_chunk_date_range_at_threshold_stays_single() -> None:
    # Exactly max_days apart (inclusive 46-day span) is still one chunk.
    start = date(2026, 6, 1)
    end = start + timedelta(days=45)
    assert _chunk_date_range(start, end) == [(start, end)]


def test_chunk_date_range_long_range_splits_on_month_boundaries() -> None:
    """A multi-month range is cut on calendar-month boundaries, tiling
    the whole span with no gaps or overlaps."""
    chunks = _chunk_date_range(date(2026, 1, 15), date(2026, 4, 10))
    assert chunks == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 4, 10)),
    ]
    # Contiguous: each chunk starts the day after the previous one ends.
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
        assert next_start == prev_end + timedelta(days=1)


def test_chunk_date_range_crosses_year_boundary() -> None:
    chunks = _chunk_date_range(date(2026, 12, 10), date(2027, 2, 5))
    assert chunks == [
        (date(2026, 12, 10), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 31)),
        (date(2027, 2, 1), date(2027, 2, 5)),
    ]


def test_chunk_date_range_inverted_range_is_empty() -> None:
    assert _chunk_date_range(date(2026, 6, 20), date(2026, 6, 10)) == []


@pytest.fixture(scope="module")
def competition_scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_competition_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def test_competition_scoreboard_parses_multi_event_fixture(
    competition_scoreboard_data: dict[str, Any],
) -> None:
    """The fictional fixture has 4 valid games across June plus one
    malformed event the parser must skip."""
    games = _parse_scoreboard(competition_scoreboard_data, COMPETITION_LEAGUE)
    assert sorted(game.id for game in games) == [
        "espn:9700001",
        "espn:9700002",
        "espn:9700003",
        "espn:9700004",
    ]
    # Whole-competition games carry no internal team ids (no team scope).
    assert all(g.home_team_id is None and g.away_team_id is None for g in games)
    # All starts are tz-aware UTC.
    assert all(g.start_time.utcoffset() == timedelta(0) for g in games)


async def test_get_competition_schedule_filters_to_window(
    competition_scoreboard_data: dict[str, Any],
) -> None:
    """A short window keeps in-window fixtures and drops the two out of
    range (the June 10 result before the window, the June 26 fixture
    after it)."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = params
        return competition_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_competition_schedule(
        COMPETITION_LEAGUE, date(2026, 6, 14), date(2026, 6, 21)
    )
    # The single short-range call hits the scoreboard endpoint with a
    # ranged ET-bucketed dates param and the load-bearing limit=400.
    assert captured["url"].endswith("/soccer/coastline.nations/scoreboard")
    assert captured["params"] == {"dates": "20260614-20260621", "limit": "400"}
    # In-window games kept and sorted ascending by start; the June 10
    # result and the June 26 fixture are dropped.
    assert [game.id for game in games] == ["espn:9700002", "espn:9700003"]
    assert games[0].start_time <= games[1].start_time


async def test_get_competition_schedule_chunks_long_range_by_month(
    competition_scoreboard_data: dict[str, Any],
) -> None:
    """A >45-day range is split into month chunks (one call each) and
    the per-chunk results are merged by game id."""
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        # Every chunk returns the same fixture payload; the merge must
        # dedupe by game id so games aren't double-counted.
        return competition_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_competition_schedule(
        COMPETITION_LEAGUE, date(2026, 5, 1), date(2026, 7, 31)
    )
    # May / June / July → three month-aligned ranged calls.
    assert [params["dates"] for params in calls] == [  # type: ignore[index]
        "20260501-20260531",
        "20260601-20260630",
        "20260701-20260731",
    ]
    assert all(params["limit"] == "400" for params in calls)  # type: ignore[index]
    # All four June fixtures land in the window exactly once (no dupes
    # despite each chunk returning the full payload).
    assert [game.id for game in games] == [
        "espn:9700001",
        "espn:9700002",
        "espn:9700003",
        "espn:9700004",
    ]


async def test_get_competition_schedule_survives_one_failed_chunk(
    competition_scoreboard_data: dict[str, Any],
) -> None:
    """One failing chunk logs and is skipped; surviving chunks still
    contribute their games (a partial schedule beats none)."""
    provider = EspnProvider()

    # Only the May chunk returns the fixtures payload; June fails, July
    # is empty.  In real ESPN traffic each ranged call returns only its
    # own window, so a dropped chunk means losing only that window's
    # games — here the May chunk still delivers, the call doesn't raise.
    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        assert params is not None
        if params["dates"].startswith("202606"):
            raise httpx.ConnectError("June chunk down")
        if params["dates"].startswith("202605"):
            return competition_scoreboard_data
        return {"events": []}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_competition_schedule(
        COMPETITION_LEAGUE, date(2026, 5, 1), date(2026, 7, 31)
    )
    # The surviving May chunk's four in-window fixtures still come
    # through despite the failed June chunk.
    assert [game.id for game in games] == [
        "espn:9700001",
        "espn:9700002",
        "espn:9700003",
        "espn:9700004",
    ]


async def test_get_competition_schedule_raises_when_all_chunks_fail() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("everything down")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    with pytest.raises(httpx.ConnectError):
        await provider.get_competition_schedule(
            COMPETITION_LEAGUE, date(2026, 5, 1), date(2026, 7, 31)
        )


# ===========================================================================
# Phase 4: tennis + UFC (an athlete is a single-member "team")
# ===========================================================================

# A followed player: the TeamORM provider_key is the ESPN athlete id.
TENNIS_PLAYER = Team(
    id="coastline-rowan-ashgrove",
    league_id=TENNIS_LEAGUE.id,
    name="Rowan Ashgrove",
    abbreviation="R. Ashgrove",
    provider_key="70001",
)
# A followed fighter.
MMA_FIGHTER = Team(
    id="summit-cassius-dunmore",
    league_id=MMA_LEAGUE.id,
    name="Cassius Dunmore",
    abbreviation="C. Dunmore",
    provider_key="80001",
)


@pytest.fixture(scope="module")
def tennis_scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_tennis_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def ufc_scoreboard_data() -> dict[str, Any]:
    with (FIXTURES / "espn_ufc_scoreboard.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def tennis_rankings_data() -> dict[str, Any]:
    with (FIXTURES / "espn_tennis_rankings.json").open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Period normalization: Set n / R n
# ---------------------------------------------------------------------------


def _individual_competition(
    *,
    competition_id: str,
    state: str,
    name: str,
    period: int,
    home: tuple[str, str],
    away: tuple[str, str],
    home_linescores: list[bool] | None = None,
    away_linescores: list[bool] | None = None,
    display_clock: str | None = None,
    homeaway: bool = True,
    date_str: str = "2026-06-12T12:30Z",
) -> dict[str, Any]:
    """Build one tennis/MMA competition (athlete competitors).

    ``home``/``away`` are ``(athlete_id, displayName)``.  Linescores carry
    per-set/round ``winner`` flags so set/round counts are derivable.  When
    ``homeaway`` is False the competitors carry only ``order`` (UFC shape).
    """

    def competitor(
        athlete_id: str, name: str, order: int, side: str, won: list[bool] | None
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": athlete_id,
            "type": "athlete",
            "order": order,
            "athlete": {"displayName": name, "shortName": name},
        }
        if homeaway:
            entry["homeAway"] = side
        if won is not None:
            entry["linescores"] = [{"value": 6.0, "winner": flag} for flag in won]
        return entry

    status: dict[str, Any] = {
        "period": period,
        "type": {"state": state, "name": name, "detail": name, "shortDetail": name},
    }
    if display_clock is not None:
        status["displayClock"] = display_clock
    return {
        "id": competition_id,
        "date": date_str,
        "round": {"id": "5", "displayName": "Quarterfinal"},
        "status": status,
        "competitors": [
            competitor(away[0], away[1], 2, "away", away_linescores),
            competitor(home[0], home[1], 1, "home", home_linescores),
        ],
    }


def test_tennis_set_label_and_no_clock(tennis_scoreboard_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_individual_scoreboard(tennis_scoreboard_data, TENNIS_LEAGUE)}
    state = games["espn:9810001"].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period == 2
    assert state.period_label == "Set 2"
    # Tennis carries no clock and never an intermission.
    assert state.clock is None
    assert state.is_intermission is False
    # Set counts: home took set 1 (6-3), neither has won set 2 yet.
    assert (state.home_score, state.away_score) == (1, 0)


def test_tennis_final_two_set_linescores(tennis_scoreboard_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_individual_scoreboard(tennis_scoreboard_data, TENNIS_LEAGUE)}
    state = games["espn:9810002"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    # 6-2 6-4: a straight-sets 2-0 win.
    assert (state.home_score, state.away_score) == (2, 0)
    assert state.period == 2
    assert state.period_label == "Set 2"


def test_tennis_scoreboard_unwraps_groupings_and_skips_malformed(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    """events -> groupings -> competitions nesting is flattened; the
    one-sided competition and the bad-date one are skipped."""
    games = _parse_individual_scoreboard(tennis_scoreboard_data, TENNIS_LEAGUE)
    # Two singles competitions + one scheduled doubles competition; the
    # one-competitor and bad-date entries are dropped.
    assert sorted(g.id for g in games) == [
        "espn:9810001",
        "espn:9810002",
        "espn:9810003",
    ]


def test_tennis_series_label_tournament_and_round(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    games = {g.id: g for g in _parse_individual_scoreboard(tennis_scoreboard_data, TENNIS_LEAGUE)}
    assert games["espn:9810001"].series == "Harborfield Open · Quarterfinal"
    assert games["espn:9810002"].series == "Harborfield Open · Round 1"
    # Athletes (no homeAway needed here — tennis carries it) map straight.
    assert games["espn:9810001"].home_name == "Rowan Ashgrove"
    assert games["espn:9810001"].away_name == "Niall Brackwater"
    assert games["espn:9810001"].home_abbreviation == "R. Ashgrove"


def test_mma_round_label_and_clock(ufc_scoreboard_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_individual_scoreboard(ufc_scoreboard_data, MMA_LEAGUE)}
    state = games["espn:9910001"].state
    assert state is not None
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period == 2
    assert state.period_label == "R2"
    assert state.clock == "3:18"  # rounds count down while live
    assert state.is_intermission is False


def test_mma_decision_final(ufc_scoreboard_data: dict[str, Any]) -> None:
    games = {g.id: g for g in _parse_individual_scoreboard(ufc_scoreboard_data, MMA_LEAGUE)}
    state = games["espn:9910002"].state
    assert state is not None
    assert state.phase is GamePhase.FINAL
    assert state.period == 3
    assert state.period_label == "R3"
    assert state.clock is None


def test_mma_no_homeaway_uses_order_and_card_name(
    ufc_scoreboard_data: dict[str, Any],
) -> None:
    """UFC bouts carry no homeAway: order 1 -> home, 2 -> away; the card
    name is the series label, and every bout inherits the card start
    time (bouts have no per-fight time)."""
    games = {g.id: g for g in _parse_individual_scoreboard(ufc_scoreboard_data, MMA_LEAGUE)}
    bout = games["espn:9910001"]
    assert bout.home_name == "Cassius Dunmore"  # order 1
    assert bout.away_name == "Bryce Harlow"  # order 2
    assert bout.series == "SFC 250: Dunmore vs. Harlow"
    # All bouts share the card start time.
    assert bout.start_time == datetime(2026, 6, 13, 0, 0, tzinfo=timezone.utc)
    assert games["espn:9910003"].start_time == bout.start_time
    assert games["espn:9910002"].start_time == bout.start_time


def test_mma_scoreboard_skips_one_sided_bout(ufc_scoreboard_data: dict[str, Any]) -> None:
    games = _parse_individual_scoreboard(ufc_scoreboard_data, MMA_LEAGUE)
    # The solo-competitor bout (9910004) is dropped; three valid bouts remain.
    assert sorted(g.id for g in games) == [
        "espn:9910001",
        "espn:9910002",
        "espn:9910003",
    ]


# ---------------------------------------------------------------------------
# Tennis rankings -> Standings
# ---------------------------------------------------------------------------


def test_tennis_rankings_parse_rank_and_points(
    tennis_rankings_data: dict[str, Any],
) -> None:
    standings = _parse_tennis_rankings(tennis_rankings_data, TENNIS_LEAGUE)
    assert standings.league_id == TENNIS_LEAGUE.id
    assert standings.season == "2026"
    # Sorted by current rank; the malformed string entry and the
    # nameless athlete are skipped.
    assert [(row.rank, row.team_name, row.points) for row in standings.rows] == [
        (1, "Idris Calloway", 10240),
        (2, "Rowan Ashgrove", 8120),
        (3, "Niall Brackwater", 6035),
        (4, "Teague Vellmoor", None),
    ]
    # Single group: the tour/ranking name.
    assert all(row.group == "Coastline Tennis Series Rankings" for row in standings.rows)


def test_tennis_rankings_tag_followed_athlete(
    tennis_rankings_data: dict[str, Any],
) -> None:
    standings = _parse_tennis_rankings(
        tennis_rankings_data, TENNIS_LEAGUE, {"70001": TENNIS_PLAYER.id}
    )
    by_name = {row.team_name: row for row in standings.rows}
    assert by_name["Rowan Ashgrove"].team_id == TENNIS_PLAYER.id
    assert by_name["Idris Calloway"].team_id is None


def test_tennis_rankings_tolerates_malformed_payload() -> None:
    empty = _parse_tennis_rankings({}, TENNIS_LEAGUE)
    assert empty.rows == ()
    assert _parse_tennis_rankings({"rankings": "nope"}, TENNIS_LEAGUE).rows == ()


# ---------------------------------------------------------------------------
# Provider methods for an athlete "team"
# ---------------------------------------------------------------------------


async def test_tennis_schedule_scans_draw_for_followed_player(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    """get_schedule for a player scans the tour scoreboard and returns
    only the player's matches, with the player's internal id on the
    matching side and series populated."""
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        return tennis_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        TENNIS_LEAGUE, TENNIS_PLAYER, date(2026, 6, 8), date(2026, 6, 14)
    )
    # One ranged scoreboard call (the window fits one chunk), limit=400.
    assert calls == [{"dates": "20260608-20260614", "limit": "400"}]
    # Only Rowan's two singles matches; the doubles match (other players)
    # is filtered out.
    assert [g.id for g in games] == ["espn:9810002", "espn:9810001"]
    assert all(g.home_team_id == TENNIS_PLAYER.id for g in games)
    assert games[0].series == "Harborfield Open · Round 1"
    # Sorted ascending by start time.
    assert games[0].start_time <= games[1].start_time


async def test_tennis_schedule_filters_to_window(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    """A window after the player's matches returns nothing even though the
    scoreboard still ships the whole draw."""
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return tennis_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        TENNIS_LEAGUE, TENNIS_PLAYER, date(2026, 6, 20), date(2026, 6, 25)
    )
    assert games == []


async def test_mma_schedule_fetches_month_buckets(
    ufc_scoreboard_data: dict[str, Any],
) -> None:
    """UFC schedules at month granularity (?dates=YYYYMM); a window
    spanning two months issues one call per month and keeps only the
    followed fighter's bouts."""
    provider = EspnProvider()
    calls: list[dict[str, str] | None] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append(params)
        # Only the June bucket carries the card; July is empty.
        if params and params.get("dates") == "202606":
            return ufc_scoreboard_data
        return {"events": []}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        MMA_LEAGUE, MMA_FIGHTER, date(2026, 6, 1), date(2026, 7, 15)
    )
    assert [params["dates"] for params in calls] == ["202606", "202607"]  # type: ignore[index]
    assert all(params["limit"] == "400" for params in calls)  # type: ignore[index]
    # Only Cassius Dunmore's bout (he is order 1 -> home).
    assert [g.id for g in games] == ["espn:9910001"]
    assert games[0].home_team_id == MMA_FIGHTER.id
    assert games[0].away_team_id is None
    assert games[0].series == "SFC 250: Dunmore vs. Harlow"


async def test_mma_schedule_survives_one_failed_month(
    ufc_scoreboard_data: dict[str, Any],
) -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        assert params is not None
        if params["dates"] == "202607":
            raise httpx.ConnectError("July bucket down")
        return ufc_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_schedule(
        MMA_LEAGUE, MMA_FIGHTER, date(2026, 6, 1), date(2026, 7, 15)
    )
    # June still delivers the bout despite the failed July bucket.
    assert [g.id for g in games] == ["espn:9910001"]


async def test_individual_schedule_raises_when_all_chunks_fail() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("everything down")

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    with pytest.raises(httpx.ConnectError):
        await provider.get_schedule(
            TENNIS_LEAGUE, TENNIS_PLAYER, date(2026, 6, 8), date(2026, 6, 14)
        )


async def test_tennis_live_games_scans_whole_tour(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    """get_live_games is tour-wide (no team scope): every match on the
    day's scoreboard comes back for live_tick to match by id."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = params
        return tennis_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_live_games(TENNIS_LEAGUE)
    assert captured["url"].endswith("/tennis/coastline/scoreboard")
    assert captured["params"]["limit"] == "400"
    assert len(captured["params"]["dates"]) == 8  # YYYYMMDD
    assert sorted(g.id for g in games) == [
        "espn:9810001",
        "espn:9810002",
        "espn:9810003",
    ]


async def test_mma_live_games_uses_month_bucket(
    ufc_scoreboard_data: dict[str, Any],
) -> None:
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["params"] = params
        return ufc_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    games = await provider.get_live_games(MMA_LEAGUE)
    # UFC live scan asks for the current month bucket (YYYYMM).
    assert len(captured["params"]["dates"]) == 6
    assert {g.id for g in games} >= {"espn:9910001", "espn:9910002"}


async def test_get_game_state_for_bout_scans_scoreboard(
    ufc_scoreboard_data: dict[str, Any],
) -> None:
    """The /summary endpoint is unreliable for individual sports, so
    get_game_state finds the competition by id on the scoreboard."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        return ufc_scoreboard_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    state = await provider.get_game_state(MMA_LEAGUE, "9910001")
    assert "/scoreboard" in captured["url"]  # not /summary
    assert state is not None
    assert state.game_id == "espn:9910001"
    assert state.phase is GamePhase.IN_PROGRESS
    assert state.period_label == "R2"
    # An unknown competition id yields None.
    assert await provider.get_game_state(MMA_LEAGUE, "does-not-exist") is None


async def test_tennis_get_standings_uses_rankings_endpoint(
    tennis_rankings_data: dict[str, Any],
) -> None:
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["url"] = url
        return tennis_rankings_data

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    standings = await provider.get_standings(TENNIS_LEAGUE)
    assert captured["url"].endswith("/tennis/coastline/rankings")
    assert standings.rows[0].team_name == "Idris Calloway"
    assert standings.rows[0].rank == 1


async def test_mma_get_standings_is_empty() -> None:
    provider = EspnProvider()
    called = False

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    standings = await provider.get_standings(MMA_LEAGUE)
    assert standings.league_id == MMA_LEAGUE.id
    assert standings.rows == ()
    assert called is False  # no network call for an empty MMA table


async def test_individual_get_roster_is_empty() -> None:
    provider = EspnProvider()
    called = False

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    tennis_roster = await provider.get_roster(TENNIS_LEAGUE, TENNIS_PLAYER)
    mma_roster = await provider.get_roster(MMA_LEAGUE, MMA_FIGHTER)
    assert tennis_roster.team_id == TENNIS_PLAYER.id
    assert tennis_roster.players == ()
    assert mma_roster.players == ()
    assert called is False  # individual sports never hit the roster endpoint


async def test_individual_get_news_is_best_effort() -> None:
    """Individual-sport news omits the team param and survives upstream
    failure with an empty list (the news service still adds Google News)."""
    provider = EspnProvider()
    captured: dict[str, Any] = {}

    async def ok_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        captured["params"] = params
        return {"articles": []}

    provider._get_json = ok_get_json  # type: ignore[method-assign]
    assert await provider.get_news(TENNIS_LEAGUE, TENNIS_PLAYER) == []
    # No per-athlete team param on the individual-sport news endpoint.
    assert captured["params"] == {"limit": "20"}

    async def failing_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("news down")

    provider._get_json = failing_get_json  # type: ignore[method-assign]
    assert await provider.get_news(MMA_LEAGUE, MMA_FIGHTER) == []


def test_individual_period_normalization_scheduled_guard(
    tennis_scoreboard_data: dict[str, Any],
) -> None:
    """The scheduled/postponed -> (0, "", None, False) guard holds for
    individual sports too (the scheduled doubles match)."""
    games = {g.id: g for g in _parse_individual_scoreboard(tennis_scoreboard_data, TENNIS_LEAGUE)}
    state = games["espn:9810003"].state
    assert state is not None
    assert state.phase is GamePhase.SCHEDULED
    assert state.period == 0
    assert state.period_label == ""
    assert state.clock is None
    assert state.is_intermission is False
