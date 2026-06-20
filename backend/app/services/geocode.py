"""Free, keyless forward geocoding via OpenStreetMap Nominatim.

Used by the location-refresh job to turn a stadium's name (and city) into
coordinates for the map view.  Nominatim's usage policy requires a
descriptive ``User-Agent`` identifying the application and caps callers at
**one request per second**, so every call is serialized behind an
``asyncio.Lock`` and spaced out by at least :data:`_MIN_INTERVAL` seconds.

Like every external-HTTP helper in the app this is defensive end to end:
network errors, timeouts, non-200 responses, and unparseable bodies are
logged and turned into ``None`` — it never raises, because the scheduler
relies on the never-raise contract (one un-geocodable venue must not abort
a whole refresh).  Results are cached on the team row by the scheduler, so
a resolved team is never geocoded again.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy *requires* a descriptive User-Agent naming the
# application (a generic one gets blocked).
_HEADERS = {"User-Agent": "SportsDash/1.0 (self-hosted)"}
_TIMEOUT = httpx.Timeout(15.0)

# Nominatim allows at most one request per second; serialize calls and
# enforce the minimum spacing so a batch refresh stays a good citizen.
_MIN_INTERVAL = 1.0
_rate_lock = asyncio.Lock()
_last_request_at = 0.0


async def geocode(query: str) -> tuple[float, float] | None:
    """Resolve a free-text place query to ``(lat, lon)``; ``None`` on miss.

    Serialized and rate-limited to <=1 request/second across the whole
    process.  Never raises: any failure (empty query, network/timeout,
    bad status, unparseable JSON, missing/garbage coordinates) is logged
    and returns ``None``.
    """
    query = (query or "").strip()
    if not query:
        return None

    params = {"q": query, "format": "json", "limit": 1}
    try:
        async with _rate_lock:
            await _respect_rate_limit()
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers=_HEADERS
            ) as client:
                response = await client.get(_NOMINATIM_URL, params=params)
            _mark_request()
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.exception("geocode: lookup failed for %r — returning None", query)
        return None

    return _parse_first_coords(payload, query)


def _venue_query_variants(venue: str) -> list[str]:
    """Progressively-trimmed geocode queries for a venue string, most-specific first.

    Nominatim often fails on an over-specified
    ``"Stadium, Street, District, City"`` string yet resolves the bare
    ``"Stadium"`` or ``"Stadium, City"``.  Given the comma-separated
    segments we try, in order: the full string, the first segment plus the
    last (stadium + city), then the first segment alone — deduped, preserving
    order.  A single-segment venue yields just itself.
    """
    parts = [segment.strip() for segment in venue.split(",") if segment.strip()]
    if not parts:
        return []
    candidates = [venue.strip()]
    if len(parts) > 2:
        candidates.append(f"{parts[0]}, {parts[-1]}")
    if len(parts) > 1:
        candidates.append(parts[0])
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        folded = candidate.casefold()
        if candidate and folded not in seen:
            seen.add(folded)
            ordered.append(candidate)
    return ordered


async def geocode_venue(venue: str) -> tuple[float, float] | None:
    """Geocode a stadium/venue string, retrying with trimmed variants.

    Wraps :func:`geocode` with :func:`_venue_query_variants` so an
    over-specified venue string (e.g. TheSportsDB's "Tottenham Hotspur
    Stadium, Bill Nicholson Way, Tottenham, London", which Nominatim can't
    resolve, while the bare stadium name resolves fine) still lands on the
    map.  Returns the first variant that resolves, or ``None``.  Never
    raises (``geocode`` is itself never-raise).
    """
    for variant in _venue_query_variants(venue or ""):
        coords = await geocode(variant)
        if coords is not None:
            return coords
    return None


async def _respect_rate_limit() -> None:
    """Sleep just long enough that requests are >=1s apart.

    Called while holding ``_rate_lock`` so the spacing is enforced across
    concurrent callers, not merely within one.
    """
    now = time.monotonic()
    elapsed = now - _last_request_at
    if elapsed < _MIN_INTERVAL:
        await asyncio.sleep(_MIN_INTERVAL - elapsed)


def _mark_request() -> None:
    global _last_request_at
    _last_request_at = time.monotonic()


def _parse_first_coords(payload: object, query: str) -> tuple[float, float] | None:
    """Pull ``lat``/``lon`` (Nominatim returns them as strings) from result 0."""
    if not isinstance(payload, list) or not payload:
        logger.info("geocode: no results for %r", query)
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    try:
        lat = float(first["lat"])
        lon = float(first["lon"])
    except (KeyError, TypeError, ValueError):
        logger.warning("geocode: result for %r missing usable coordinates", query)
        return None
    return lat, lon
