"""World Cup host-venue resolution + next-match selection for the map."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.orm import GameORM
from app.routes.map_view import _next_match_by_team
from app.services import wc_venues


def test_resolve_known_host_venue() -> None:
    venue = wc_venues.resolve("MetLife Stadium")
    assert venue is not None
    assert venue.name == "MetLife Stadium"
    assert "USA" in venue.location
    # Coordinates land in the New York / New Jersey area.
    assert 40.0 < venue.lat < 41.5
    assert -75.0 < venue.lon < -73.0


def test_resolve_is_punctuation_and_case_insensitive() -> None:
    assert wc_venues.resolve("levi's stadium") is wc_venues.resolve("Levi's Stadium")
    assert wc_venues.resolve("LEVIS STADIUM") is not None


def test_resolve_alias_maps_to_canonical() -> None:
    # The renamed Mexico City ground resolves under its old name too.
    azteca = wc_venues.resolve("Estadio Azteca")
    assert azteca is not None
    assert azteca.name == "Estadio Banorte"


def test_resolve_non_host_venue_is_none() -> None:
    assert wc_venues.resolve("Wembley Stadium") is None
    assert wc_venues.resolve(None) is None
    assert wc_venues.resolve("") is None


def _game(home: str, away: str, venue: str, start: datetime, phase: str) -> GameORM:
    return GameORM(
        home_name=home, away_name=away, venue=venue, start_time=start, phase=phase
    )


def test_next_match_prefers_earliest_upcoming() -> None:
    now = datetime.now(timezone.utc)
    games = [
        _game("Brazil", "Morocco", "MetLife Stadium", now - timedelta(days=2), "final"),
        _game("Brazil", "Haiti", "Lincoln Financial Field", now + timedelta(days=3), "scheduled"),
        _game("Spain", "Brazil", "SoFi Stadium", now + timedelta(days=8), "scheduled"),
    ]
    by_team = _next_match_by_team(games)
    brazil = by_team["brazil"]
    # Earliest still-to-play match wins, with the opponent relative to Brazil.
    assert brazil.venue == "Lincoln Financial Field"
    assert brazil.opponent == "Haiti"


def test_next_match_falls_back_to_latest_past_when_none_upcoming() -> None:
    now = datetime.now(timezone.utc)
    games = [
        _game("Ghana", "Iran", "BC Place", now - timedelta(days=10), "final"),
        _game("Iran", "Ghana", "BMO Field", now - timedelta(days=4), "final"),
    ]
    ghana = _next_match_by_team(games)["ghana"]
    # No upcoming game → most recent played, opponent relative to Ghana.
    assert ghana.venue == "BMO Field"
    assert ghana.opponent == "Iran"
