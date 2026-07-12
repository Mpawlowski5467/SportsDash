"""Roster-derived projected lineup arrangement."""

from __future__ import annotations

from app.models.domain import Player, PlayerStatus, Sport
from app.services import lineup


def _player(name: str, position: str, status: PlayerStatus = PlayerStatus.ACTIVE) -> Player:
    return Player(id=name, team_id="t", name=name, position=position, status=status)


def _roster(*positions: str) -> list[Player]:
    return [_player(f"P{i}", pos) for i, pos in enumerate(positions)]


def test_soccer_lineup_has_gk_and_formation() -> None:
    # 1 GK + 4 DEF + 3 MID + 3 FWD = a full 4-3-3.
    roster = _roster(
        "GK",
        "GK",
        "DF",
        "DF",
        "DF",
        "DF",
        "DF",
        "MF",
        "MF",
        "MF",
        "FW",
        "FW",
        "FW",
    )
    team = lineup.build_team_lineup(Sport.SOCCER, roster)
    assert team is not None
    assert team.formation == "4-3-3"
    assert [s.unit for s in team.slots].count("GK") == 1
    assert len(team.slots) == 11
    # The extra goalkeeper didn't make the XI.
    assert any(p.position == "GK" for p in team.bench)


def test_basketball_starting_five() -> None:
    roster = _roster("PG", "SG", "SF", "PF", "C", "PG", "C", "SF")
    team = lineup.build_team_lineup(Sport.BASKETBALL, roster)
    assert team is not None
    assert len(team.slots) == 5
    assert {s.unit for s in team.slots} == {"G", "F", "C"}
    assert team.formation is None
    # Slots carry a 1-based order across the lineup.
    assert [s.order for s in team.slots] == [1, 2, 3, 4, 5]


def test_baseball_catcher_not_centerfield() -> None:
    # Exact-token match must put "C" in the battery, not the outfield.
    roster = _roster("SP", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")
    team = lineup.build_team_lineup(Sport.BASEBALL, roster)
    assert team is not None
    catcher = next(s for s in team.slots if s.player.position == "C")
    assert catcher.unit == "C"
    assert any(s.unit == "P" for s in team.slots)
    assert {s.unit for s in team.slots} >= {"P", "C", "IF", "OF", "DH"}


def test_active_players_preferred_over_injured() -> None:
    roster = [
        _player("Hurt", "PG", PlayerStatus.OUT),
        _player("Fit", "PG", PlayerStatus.ACTIVE),
        _player("AlsoFit", "SG", PlayerStatus.ACTIVE),
        _player("Center", "C", PlayerStatus.ACTIVE),
    ]
    team = lineup.build_team_lineup(Sport.BASKETBALL, roster)
    assert team is not None
    guards = [s.player.name for s in team.slots if s.unit == "G"]
    # Two guard slots, both active; the injured guard drops to the bench.
    assert "Fit" in guards and "AlsoFit" in guards
    assert "Hurt" not in guards
    assert any(p.name == "Hurt" for p in team.bench)


def test_empty_roster_returns_none() -> None:
    assert lineup.build_team_lineup(Sport.SOCCER, []) is None


def test_individual_and_leaderboard_sports_have_no_lineup() -> None:
    roster = _roster("GK", "DF", "MF")
    assert lineup.build_team_lineup(Sport.TENNIS, roster) is None
    assert lineup.build_team_lineup(Sport.GOLF, roster) is None


def test_accepts_orm_like_player_with_string_status() -> None:
    # The repository hands back rows whose ``status`` is a plain string; the
    # arranger must treat "active" the same as PlayerStatus.ACTIVE.
    class Row:
        def __init__(self, name: str, position: str, status: str):
            self.id = name
            self.team_id = "t"
            self.name = name
            self.position = position
            self.jersey_number = None
            self.status = status
            self.status_detail = None
            self.stat_line = None
            self.career_stat_line = None
            self.photo_url = None

    roster = [
        Row("Bench", "PG", "out"),
        Row("Start", "PG", "active"),
        Row("Two", "SG", "active"),
        Row("Five", "C", "active"),
    ]
    team = lineup.build_team_lineup(Sport.BASKETBALL, roster)
    assert team is not None
    guards = [s.player.name for s in team.slots if s.unit == "G"]
    assert guards == ["Start", "Two"]
    assert all(isinstance(s.player, Player) for s in team.slots)
