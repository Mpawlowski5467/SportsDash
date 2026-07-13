"""Cross-season head-to-head record builder + its matchup wiring."""

from __future__ import annotations

import pytest

from app.models.orm import LeagueORM, TeamORM
from app.schemas import GameOut, GameSideOut
from app.services import head_to_head, season_results
from app.timeutil import utcnow

LEAGUE = LeagueORM(
    id="emerald-pitch",
    sport="soccer",
    name="Emerald Pitch",
    provider="espn",
    provider_key="soccer/emerald",
)
TEAM = TeamORM(
    id="moss-rovers",
    league_id="emerald-pitch",
    name="Moss Rovers",
    abbreviation="MOS",
    provider_key="77",
    rss_feeds=[],
)


def _meeting(
    game_id: str,
    *,
    us_home: bool,
    us_score: int | None,
    them_score: int | None,
    opponent: str = "Tidewater FC",
) -> GameOut:
    us = GameSideOut(team_id=TEAM.id, name=TEAM.name, score=us_score)
    them = GameSideOut(team_id=None, name=opponent, score=them_score)
    return GameOut(
        id=game_id,
        league_id=LEAGUE.id,
        sport="soccer",
        home=us if us_home else them,
        away=them if us_home else us,
        start_time=utcnow(),
        phase="final",
        period=2,
        period_label="FT",
        is_intermission=False,
        followed_team_ids=[TEAM.id],
    )


def _install_seasons(
    monkeypatch: pytest.MonkeyPatch, by_season: dict[int, list[GameOut]]
) -> list[int]:
    scanned: list[int] = []

    async def fake_results(league_row, team_row, season: int) -> list[GameOut]:
        scanned.append(season)
        return by_season.get(season, [])

    monkeypatch.setattr(season_results, "season_results_out", fake_results)
    return scanned


async def test_record_tallies_wins_losses_draws(monkeypatch: pytest.MonkeyPatch) -> None:
    year = utcnow().year
    scanned = _install_seasons(
        monkeypatch,
        {
            year: [
                _meeting("espn:m1", us_home=True, us_score=2, them_score=0),
                # A different opponent must not count.
                _meeting(
                    "espn:x1", us_home=True, us_score=5, them_score=0, opponent="Rivermark Owls"
                ),
            ],
            year - 1: [
                _meeting("espn:m2", us_home=False, us_score=1, them_score=1),
                _meeting("espn:m3", us_home=False, us_score=0, them_score=3),
            ],
        },
    )

    record = await head_to_head.build_record(LEAGUE, TEAM, "Tidewater FC")
    assert record is not None
    assert (record.wins, record.draws, record.losses) == (1, 1, 1)
    assert [game.id for game in record.meetings] == ["espn:m1", "espn:m2", "espn:m3"]
    assert record.seasons == head_to_head.SEASONS_BACK
    assert len(scanned) == head_to_head.SEASONS_BACK


async def test_record_matches_opponent_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    year = utcnow().year
    _install_seasons(
        monkeypatch,
        {year: [_meeting("espn:m1", us_home=True, us_score=1, them_score=0)]},
    )
    record = await head_to_head.build_record(LEAGUE, TEAM, "  tidewater fc ")
    assert record is not None
    assert record.wins == 1


async def test_record_none_when_no_meetings(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_seasons(monkeypatch, {})
    assert await head_to_head.build_record(LEAGUE, TEAM, "Tidewater FC") is None


async def test_record_none_for_unsupported_league() -> None:
    volleyball = LeagueORM(
        id="evl",
        sport="volleyball",
        name="EVL",
        provider="thesportsdb",
        provider_key="5613",
    )
    assert await head_to_head.build_record(volleyball, TEAM, "Tidewater FC") is None


async def test_record_caps_meetings_but_counts_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    year = utcnow().year
    games = [_meeting(f"espn:m{i}", us_home=True, us_score=1, them_score=0) for i in range(12)]
    _install_seasons(monkeypatch, {year: games})
    record = await head_to_head.build_record(LEAGUE, TEAM, "Tidewater FC")
    assert record is not None
    assert record.wins == 12
    assert len(record.meetings) == 8


async def test_record_skips_scoreless_games(monkeypatch: pytest.MonkeyPatch) -> None:
    year = utcnow().year
    _install_seasons(
        monkeypatch,
        {year: [_meeting("espn:m1", us_home=True, us_score=None, them_score=None)]},
    )
    assert await head_to_head.build_record(LEAGUE, TEAM, "Tidewater FC") is None
