"""Unit tests for the venue-coords helpers (normalize + in-memory index).

The Redis-backed cache helpers (``get_cached`` / ``set_coords``) no-op
without a configured Redis, so they're exercised at the API/integration
level; here we cover the pure pieces.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services import venue_coords


def test_normalize_collapses_punctuation_and_case() -> None:
    assert venue_coords.normalize("St. James' Park") == "st james park"
    assert venue_coords.normalize("  Parc des Princes  ") == "parc des princes"
    assert venue_coords.normalize("MetLife Stadium") == "metlife stadium"
    assert venue_coords.normalize(None) == ""
    assert venue_coords.normalize("") == ""


def test_build_index_from_teams_and_stadiums() -> None:
    teams = [
        SimpleNamespace(home_venue="Anfield", venue_lat=53.4308, venue_lon=-2.9608),
        # No coords → skipped.
        SimpleNamespace(home_venue="Ghost Park", venue_lat=None, venue_lon=None),
    ]
    stadiums = [
        SimpleNamespace(venue="Estádio do Maracanã", lat=-22.9121, lon=-43.2302),
        SimpleNamespace(venue=None, lat=1.0, lon=2.0),  # no name → skipped
    ]
    index = venue_coords.build_index(teams, stadiums)
    assert index["anfield"] == (53.4308, -2.9608)
    assert index["estadio do maracana"] == (-22.9121, -43.2302)
    assert "ghost park" not in index
    assert len(index) == 2


def test_build_index_team_wins_tie() -> None:
    """A team's own coordinates win over a stadium-cache entry for the same venue."""
    teams = [SimpleNamespace(home_venue="Shared Ground", venue_lat=1.0, venue_lon=2.0)]
    stadiums = [SimpleNamespace(venue="Shared Ground", lat=9.0, lon=9.0)]
    index = venue_coords.build_index(teams, stadiums)
    assert index["shared ground"] == (1.0, 2.0)
