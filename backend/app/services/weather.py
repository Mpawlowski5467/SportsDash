"""Free, keyless venue weather via Open-Meteo.

Returns current conditions plus a one-day forecast for a lat/lon, used by
the map view (stadium pins), the game-detail endpoint and the calendar's
batch (outdoor scheduled games).  Like every external-HTTP helper here
``fetch`` is defensive end to end — network errors, timeouts, bad statuses,
and unparseable bodies are logged and collapse to ``None``; it never raises,
so one un-fetchable venue can't break the map.

``fetch_or_raise`` is the same lookup for the one caller that has to tell an
outage from "no forecast" (``GET /schedule/weather``): where Open-Meteo
could not be asked, or did not answer, it raises ``WeatherUnavailable``
instead of returning ``None``.

Results are cached (``services/cache``, best-effort) keyed by coarse
coordinates, since weather changes slowly and many calls hit the same
stadiums.  A small semaphore bounds concurrent upstream calls so a fresh
many-pin map stays a good citizen of the free service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app import useragent
from app.services import http_client

from app.config import get_settings
from app.models import domain
from app.services import cache
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

_URL = "https://api.open-meteo.com/v1/forecast"
_HEADERS = useragent.headers("self-hosted")
_TIMEOUT = httpx.Timeout(15.0)
# Cap concurrent upstream calls regardless of how many pins ask at once.
_MAX_INFLIGHT = asyncio.Semaphore(8)
# Open-Meteo forecasts today and the 15 days after it, counted in the UTC the
# request asks for; a later ``start_date`` is answered 400 (checked live
# 2026-10-11: the 26th answered, the 27th was "out of allowed range").
_FORECAST_DAYS_AHEAD = 15

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


class WeatherUnavailable(Exception):
    """Open-Meteo could not be asked, or did not answer.

    A transport error, a timeout, a 429, a 5xx, a redirect or a body that is
    not JSON.  NOT raised for an answer that holds no forecast: any other
    4xx (the out-of-range 400 among them) and a 200 with nothing usable in
    it are still ``None``.
    """


def _is_answer(exc: Exception) -> bool:
    """Whether a failed request was Open-Meteo saying "no forecast".

    A 4xx other than 429: asked again, it would be refused again.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status = exc.response.status_code
    return 400 <= status < 500 and status != 429


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
    """Venue weather for ``(lat, lon)``, or ``None``.  Never raises.

    :func:`fetch_or_raise` for the callers that show no weather either way
    (a map pin, the game detail, the matchup preview): an upstream failure
    collapses to ``None`` like every other "no forecast".
    """
    try:
        return await fetch_or_raise(lat, lon, units=units, target_date=target_date)
    except WeatherUnavailable:
        return None


async def fetch_or_raise(
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
    matches is selected.  Returns ``None`` when weather is disabled, when
    the payload lacks a usable current temperature, or when the requested day
    is outside Open-Meteo's forecast window — rather than showing today's
    weather mislabeled.  A day past the window's known far edge is not asked
    for at all; any other out-of-range date is answered with HTTP 400, which
    degrades to ``None``.  Cached for ``weather_cache_minutes`` keyed by
    coarse coordinates and the target day.

    Raises :class:`WeatherUnavailable` when Open-Meteo could not be asked or
    did not answer — the one failure that is not "no forecast".  Nothing is
    cached from it.
    """
    settings = get_settings()
    if not settings.weather_enabled:
        return None
    units = units or settings.weather_units
    if target_date is not None and target_date > utcnow().date() + timedelta(
        days=_FORECAST_DAYS_AHEAD
    ):
        # Past the forecast window, so the request is a guaranteed 400 — and
        # a ``None`` is never cached, so a calendar would send it again for
        # every far-off fixture on every refresh.
        return None

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
            "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
        ),
        "timezone": "UTC",
        **_units_params(units),
    }
    if target_date is not None:
        # Ask for exactly the game's UTC day.  An out-of-range date that got
        # past the window check above returns HTTP 400, which raise_for_status
        # turns into the degrade-to-None path below — no weather rather than
        # today's.
        params["start_date"] = target_date.isoformat()
        params["end_date"] = target_date.isoformat()
    else:
        params["forecast_days"] = "1"
    try:
        async with _MAX_INFLIGHT:
            client = http_client.get_client("weather", timeout=_TIMEOUT, headers=_HEADERS)
            response = await client.get(_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning(
            "weather: lookup failed for (%s, %s)",
            lat,
            lon,
            exc_info=True,
        )
        if _is_answer(exc):
            return None
        raise WeatherUnavailable(str(exc)) from exc

    weather = _parse(payload, units, target_date=target_date)
    if weather is not None:
        await cache.cache_set_json(key, _to_cache(weather), settings.weather_cache_minutes * 60)
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
