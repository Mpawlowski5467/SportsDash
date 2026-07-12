"""Free, keyless venue weather via Open-Meteo.

Returns current conditions plus a one-day forecast for a lat/lon, used by
the map view (stadium pins) and the game-detail endpoint (outdoor scheduled
games).  Like every external-HTTP helper here it is defensive end to end —
network errors, timeouts, bad statuses, and unparseable bodies are logged
and collapse to ``None``; it never raises, so one un-fetchable venue can't
break the map.

Results are cached in Redis (best-effort) keyed by coarse coordinates, since
weather changes slowly and many calls hit the same stadiums.  A small
semaphore bounds concurrent upstream calls so a fresh many-pin map stays a
good citizen of the free service.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from app.services import http_client

from app.config import get_settings
from app.models import domain
from app.services import cache

logger = logging.getLogger(__name__)

_URL = "https://api.open-meteo.com/v1/forecast"
_HEADERS = {"User-Agent": "SportsDash/1.0 (self-hosted)"}
_TIMEOUT = httpx.Timeout(15.0)
# Cap concurrent upstream calls regardless of how many pins ask at once.
_MAX_INFLIGHT = asyncio.Semaphore(8)

# WMO weather-code -> human label.  A curated, grouped subset covering the
# codes Open-Meteo returns; unknown codes fall back to "—" (the frontend
# maps the numeric code to an icon, so this is display text only).
_WMO_LABELS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}


def _units_params(units: str) -> dict[str, str]:
    if units == "imperial":
        return {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
    return {"temperature_unit": "celsius", "wind_speed_unit": "kmh"}


async def fetch(
    lat: float,
    lon: float,
    *,
    units: str | None = None,
    target_date: date | None = None,
) -> domain.Weather | None:
    """Venue weather for ``(lat, lon)``.

    With no ``target_date`` (the map's current-conditions use) returns
    today's conditions plus a one-day forecast.  With a ``target_date`` (the
    game-detail use) returns that UTC calendar day's forecast: the request
    asks Open-Meteo for exactly that day and the daily row whose ``time``
    matches is selected.  Returns ``None`` when weather is disabled, on any
    upstream/parse failure, when the payload lacks a usable current
    temperature, or when the requested day is outside Open-Meteo's forecast
    window (it answers an out-of-range date with HTTP 400 → degrades to
    ``None`` rather than showing today's weather mislabeled).  Cached for
    ``weather_cache_minutes`` keyed by coarse coordinates and the target day.
    """
    settings = get_settings()
    if not settings.weather_enabled:
        return None
    units = units or settings.weather_units

    date_key = target_date.isoformat() if target_date is not None else "today"
    key = f"weather:{lat:.2f},{lon:.2f},{units},{date_key}"
    cached = await cache.cache_get_json(key)
    if isinstance(cached, dict):
        restored = _from_cache(cached)
        if restored is not None:
            return restored

    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,weather_code"
        ),
        "timezone": "UTC",
        **_units_params(units),
    }
    if target_date is not None:
        # Ask for exactly the game's UTC day.  An out-of-range date returns
        # HTTP 400, which raise_for_status turns into the degrade-to-None
        # path below — so a far-future game shows no weather rather than
        # today's.
        params["start_date"] = target_date.isoformat()
        params["end_date"] = target_date.isoformat()
    else:
        params["forecast_days"] = "1"
    try:
        async with _MAX_INFLIGHT:
            client = http_client.get_client(
                "weather", timeout=_TIMEOUT, headers=_HEADERS
            )
            response = await client.get(_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning(
            "weather: lookup failed for (%s, %s) — returning None",
            lat,
            lon,
            exc_info=True,
        )
        return None

    weather = _parse(payload, units, target_date=target_date)
    if weather is not None:
        await cache.cache_set_json(
            key, _to_cache(weather), settings.weather_cache_minutes * 60
        )
    return weather


def _parse(
    payload: object, units: str, *, target_date: date | None = None
) -> domain.Weather | None:
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    temp = _num(current.get("temperature_2m"))
    if temp is None:
        return None
    code = _int(current.get("weather_code")) or 0
    wind = _num(current.get("wind_speed_10m")) or 0.0

    high = low = None
    precip = None
    daily = payload.get("daily")
    if isinstance(daily, dict):
        if target_date is None:
            high = _first_num(daily.get("temperature_2m_max"))
            low = _first_num(daily.get("temperature_2m_min"))
            precip = _first_int(daily.get("precipitation_probability_max"))
        else:
            idx = _daily_index_for_date(daily.get("time"), target_date)
            if idx is None:
                # Requested day isn't in the returned window: show no weather
                # rather than today's row mislabeled as the game day's.
                return None
            high = _num_at(daily.get("temperature_2m_max"), idx)
            low = _num_at(daily.get("temperature_2m_min"), idx)
            precip = _int_at(daily.get("precipitation_probability_max"), idx)
    elif target_date is not None:
        # A dated request whose payload has no daily block at all -> no
        # forecast for that day.
        return None

    return domain.Weather(
        temperature=round(temp, 1),
        condition=_WMO_LABELS.get(code, "—"),
        code=code,
        wind_speed=round(wind, 1),
        units=units,
        high=round(high, 1) if high is not None else None,
        low=round(low, 1) if low is not None else None,
        precip_chance=precip,
    )


def _to_cache(weather: domain.Weather) -> dict[str, Any]:
    return {
        "temperature": weather.temperature,
        "condition": weather.condition,
        "code": weather.code,
        "wind_speed": weather.wind_speed,
        "units": weather.units,
        "high": weather.high,
        "low": weather.low,
        "precip_chance": weather.precip_chance,
    }


def _from_cache(data: dict[str, Any]) -> domain.Weather | None:
    temp = _num(data.get("temperature"))
    if temp is None:
        return None
    return domain.Weather(
        temperature=temp,
        condition=str(data.get("condition") or "—"),
        code=_int(data.get("code")) or 0,
        wind_speed=_num(data.get("wind_speed")) or 0.0,
        units=str(data.get("units") or "metric"),
        high=_num(data.get("high")),
        low=_num(data.get("low")),
        precip_chance=_int(data.get("precip_chance")),
    )


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _first_num(value: object) -> float | None:
    if isinstance(value, list) and value:
        return _num(value[0])
    return None


def _first_int(value: object) -> int | None:
    if isinstance(value, list) and value:
        return _int(value[0])
    return None


def _daily_index_for_date(time_value: object, target: date) -> int | None:
    """Index into Open-Meteo's ``daily`` arrays for ``target``.

    Matches against ``daily.time`` (``YYYY-MM-DD`` strings, since the request
    uses a whole-day UTC timezone).  Returns None when the returned window
    doesn't contain the date — defence-in-depth for a malformed 200, since an
    out-of-range date is rejected upstream with HTTP 400 before parsing.
    """
    target_iso = target.isoformat()
    if isinstance(time_value, list):
        for i, value in enumerate(time_value):
            if str(value) == target_iso:
                return i
    return None


def _num_at(value: object, idx: int) -> float | None:
    if isinstance(value, list) and 0 <= idx < len(value):
        return _num(value[idx])
    return None


def _int_at(value: object, idx: int) -> int | None:
    if isinstance(value, list) and 0 <= idx < len(value):
        return _int(value[idx])
    return None
