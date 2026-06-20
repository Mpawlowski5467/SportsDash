"""Unit tests for the Open-Meteo venue-weather service."""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.config import get_settings
from app.models import domain
from app.services import weather

_SAMPLE = {
    "current": {
        "temperature_2m": 18.4,
        "weather_code": 2,
        "wind_speed_10m": 11.7,
    },
    "daily": {
        "temperature_2m_max": [21.2],
        "temperature_2m_min": [13.9],
        "precipitation_probability_max": [40],
        "weather_code": [3],
    },
}

# A multi-day forecast (with the ``time`` array Open-Meteo returns) used to
# verify a future game's day is selected, not today's.
_DATED = {
    "current": {"temperature_2m": 18.0, "weather_code": 2, "wind_speed_10m": 10.0},
    "daily": {
        "time": ["2026-06-15", "2026-06-16", "2026-06-17"],
        "temperature_2m_max": [21.2, 25.5, 30.0],
        "temperature_2m_min": [13.9, 16.0, 18.0],
        "precipitation_probability_max": [40, 10, 0],
        "weather_code": [3, 1, 0],
    },
}


def test_parse_reads_current_and_daily() -> None:
    w = weather._parse(_SAMPLE, "metric")
    assert w is not None
    assert w.temperature == 18.4
    assert w.condition == "Partly cloudy"  # WMO code 2
    assert w.code == 2
    assert w.wind_speed == 11.7
    assert w.units == "metric"
    assert w.high == 21.2
    assert w.low == 13.9
    assert w.precip_chance == 40


def test_parse_returns_none_without_current_temp() -> None:
    assert weather._parse({"current": {}}, "metric") is None
    assert weather._parse({"daily": {}}, "metric") is None
    assert weather._parse("nope", "metric") is None


def test_unknown_wmo_code_falls_back_to_dash() -> None:
    payload = {"current": {"temperature_2m": 5.0, "weather_code": 4321}}
    w = weather._parse(payload, "imperial")
    assert w is not None
    assert w.condition == "—"
    assert w.units == "imperial"


def test_cache_roundtrip() -> None:
    w = domain.Weather(
        temperature=10.0,
        condition="Rain",
        code=63,
        wind_speed=5.5,
        units="metric",
        high=12.0,
        low=8.0,
        precip_chance=80,
    )
    restored = weather._from_cache(weather._to_cache(w))
    assert restored == w


async def test_fetch_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # monkeypatch restores weather_enabled after the test.
    monkeypatch.setattr(get_settings(), "weather_enabled", False)
    assert await weather.fetch(1.0, 2.0) is None


async def test_fetch_parses_live_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requested.update(dict(request.url.params))
        return httpx.Response(200, json=_SAMPLE)

    transport = httpx.MockTransport(handler)

    # Skip Redis and route the service's client through the mock transport.
    async def no_cache_get(_key: str):
        return None

    async def no_cache_set(*_args, **_kwargs):
        return None

    monkeypatch.setattr(weather.cache, "cache_get_json", no_cache_get)
    monkeypatch.setattr(weather.cache, "cache_set_json", no_cache_set)
    real_client = httpx.AsyncClient  # bind before patching to avoid recursion
    monkeypatch.setattr(
        weather.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(transport=transport),
    )

    w = await weather.fetch(40.7128, -74.0060, units="imperial")
    assert w is not None
    assert w.temperature == 18.4
    assert w.units == "imperial"
    # Imperial maps to Fahrenheit / mph in the query.
    assert requested["temperature_unit"] == "fahrenheit"
    assert requested["wind_speed_unit"] == "mph"
    # No target_date -> today's one-day forecast, no date window.
    assert requested["forecast_days"] == "1"
    assert "start_date" not in requested


async def test_fetch_never_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)

    async def no_cache(_key: str):
        return None

    monkeypatch.setattr(weather.cache, "cache_get_json", no_cache)
    real_client = httpx.AsyncClient  # bind before patching to avoid recursion
    monkeypatch.setattr(
        weather.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(transport=transport),
    )

    assert await weather.fetch(1.0, 2.0) is None


async def test_fetch_uses_cache_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = weather._to_cache(
        domain.Weather(
            temperature=7.0,
            condition="Overcast",
            code=3,
            wind_speed=3.0,
            units="metric",
        )
    )

    async def cache_hit(_key: str):
        return cached

    def explode(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("HTTP client must not be built on a cache hit")

    monkeypatch.setattr(weather.cache, "cache_get_json", cache_hit)
    monkeypatch.setattr(weather.httpx, "AsyncClient", explode)

    w = await weather.fetch(5.0, 5.0)
    assert w is not None
    assert w.condition == "Overcast"


def test_outdoor_sport_gate() -> None:
    assert domain.Sport.SOCCER in domain.WEATHER_SPORTS
    assert domain.Sport.BASEBALL in domain.WEATHER_SPORTS
    assert domain.Sport.BASKETBALL not in domain.WEATHER_SPORTS
    assert domain.Sport.HOCKEY not in domain.WEATHER_SPORTS


# --- Target-date (game-day) forecast -------------------------------------


def test_parse_selects_daily_row_for_target_date() -> None:
    w = weather._parse(_DATED, "metric", target_date=date(2026, 6, 17))
    assert w is not None
    # The game day's row (index 2), NOT today's (index 0: 21.2/13.9/40).
    assert (w.high, w.low, w.precip_chance) == (30.0, 18.0, 0)
    # Current conditions still come from the `current` block, not the daily row.
    assert w.temperature == 18.0


def test_parse_returns_none_when_target_date_not_in_window() -> None:
    # Defence-in-depth for a malformed 200 whose daily window lacks the date.
    assert weather._parse(_DATED, "metric", target_date=date(2026, 7, 30)) is None
    # A dated request with no daily block at all -> None (no forecast).
    assert (
        weather._parse({"current": {"temperature_2m": 5.0}}, "metric",
                       target_date=date(2026, 6, 17))
        is None
    )


def test_parse_target_date_none_keeps_today_first_row() -> None:
    # No target_date -> index 0 (today), backward behavior preserved.
    w = weather._parse(_DATED, "metric")
    assert w is not None
    assert (w.high, w.low, w.precip_chance) == (21.2, 13.9, 40)


def _route_through(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient  # bind before patching to avoid recursion
    monkeypatch.setattr(
        weather.httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport)
    )


async def test_fetch_sends_start_end_date_for_target_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requested.update(dict(request.url.params))
        return httpx.Response(200, json=_DATED)

    async def no_cache_get(_key: str):
        return None

    async def no_cache_set(*_a, **_k):
        return None

    monkeypatch.setattr(weather.cache, "cache_get_json", no_cache_get)
    monkeypatch.setattr(weather.cache, "cache_set_json", no_cache_set)
    _route_through(monkeypatch, handler)

    w = await weather.fetch(40.7128, -74.0060, units="metric", target_date=date(2026, 6, 17))
    assert w is not None
    assert w.high == 30.0  # the game day's row
    assert requested["start_date"] == "2026-06-17"
    assert requested["end_date"] == "2026-06-17"
    assert "forecast_days" not in requested


async def test_fetch_returns_none_on_out_of_range_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real out-of-range path: Open-Meteo answers a date beyond its window
    # with HTTP 400, which raise_for_status turns into the degrade-to-None
    # path — no weather rather than today's, mislabeled.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": True, "reason": "start_date is out of allowed range"}
        )

    async def no_cache(_key: str):
        return None

    monkeypatch.setattr(weather.cache, "cache_get_json", no_cache)
    _route_through(monkeypatch, handler)

    assert await weather.fetch(1.0, 2.0, target_date=date(2030, 1, 1)) is None


async def test_fetch_target_date_cache_key_isolated_from_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A "today" cache entry must NOT satisfy a dated lookup — the prior single
    # key-per-venue collision was the bug.
    today_entry = weather._to_cache(
        domain.Weather(
            temperature=99.0, condition="Clear sky", code=0, wind_speed=1.0, units="metric"
        )
    )
    seen_keys: list[str] = []

    async def cache_get(key: str):
        seen_keys.append(key)
        return today_entry if key.endswith(",today") else None

    async def no_cache_set(*_a, **_k):
        return None

    monkeypatch.setattr(weather.cache, "cache_get_json", cache_get)
    monkeypatch.setattr(weather.cache, "cache_set_json", no_cache_set)
    _route_through(monkeypatch, lambda _r: httpx.Response(200, json=_DATED))

    w = await weather.fetch(40.7128, -74.0060, units="metric", target_date=date(2026, 6, 17))
    assert w is not None
    # Did NOT return the stale "today" entry (99.0) — fetched the dated forecast.
    assert w.temperature == 18.0
    # The lookup key carried the date, not "today".
    assert any(k.endswith(",2026-06-17") for k in seen_keys)
