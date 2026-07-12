"""Unit tests for the venue-aware geocode fallback.

``geocode_venue`` retries Nominatim with progressively-trimmed variants of a
venue string, because the service fails on an over-specified
"Stadium, Street, District, City" string yet resolves the bare stadium name.
The underlying ``geocode`` is stubbed so these tests make no network calls.
"""

from __future__ import annotations

from app.services import geocode


def test_venue_query_variants_trims_progressively() -> None:
    variants = geocode._venue_query_variants(
        "Tottenham Hotspur Stadium, Bill Nicholson Way, Tottenham, London"
    )
    assert variants == [
        "Tottenham Hotspur Stadium, Bill Nicholson Way, Tottenham, London",
        "Tottenham Hotspur Stadium, London",
        "Tottenham Hotspur Stadium",
    ]


def test_venue_query_variants_two_segments() -> None:
    # Two segments: the "first, last" variant equals the full string, so it
    # dedupes to [full, first].
    assert geocode._venue_query_variants("Anfield, Liverpool") == [
        "Anfield, Liverpool",
        "Anfield",
    ]


def test_venue_query_variants_single_segment() -> None:
    assert geocode._venue_query_variants("Wembley Stadium") == ["Wembley Stadium"]


def test_venue_query_variants_empty() -> None:
    assert geocode._venue_query_variants("") == []
    assert geocode._venue_query_variants("   ") == []


async def test_geocode_venue_falls_back_to_trimmed_variant(monkeypatch) -> None:
    """The full string misses; the bare stadium name resolves."""
    calls: list[str] = []

    async def fake_geocode(query: str):
        calls.append(query)
        return (51.6043, -0.0662) if query == "Tottenham Hotspur Stadium" else None

    monkeypatch.setattr(geocode, "geocode", fake_geocode)
    coords = await geocode.geocode_venue(
        "Tottenham Hotspur Stadium, Bill Nicholson Way, Tottenham, London"
    )
    assert coords == (51.6043, -0.0662)
    # Tried the full string first, then the trimmed ones until one resolved.
    assert calls[0].startswith("Tottenham Hotspur Stadium, Bill")
    assert calls[-1] == "Tottenham Hotspur Stadium"


async def test_geocode_venue_returns_first_hit(monkeypatch) -> None:
    """When the full string resolves we never try the trimmed variants."""
    calls: list[str] = []

    async def fake_geocode(query: str):
        calls.append(query)
        return (53.4308, -2.9608)

    monkeypatch.setattr(geocode, "geocode", fake_geocode)
    coords = await geocode.geocode_venue("Anfield, Liverpool")
    assert coords == (53.4308, -2.9608)
    assert calls == ["Anfield, Liverpool"]


async def test_geocode_venue_none_when_all_variants_miss(monkeypatch) -> None:
    async def fake_geocode(query: str):
        return None

    monkeypatch.setattr(geocode, "geocode", fake_geocode)
    assert await geocode.geocode_venue("Nowhere Stadium, Mystery City") is None
