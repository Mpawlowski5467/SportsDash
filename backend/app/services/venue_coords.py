"""Venue-name → coordinate resolution for the map's "upcoming games" mode.

Game rows carry only a venue *name*, not coordinates.  The map resolves a
game's venue with no network in the common cases — the World Cup host table
(:mod:`app.services.wc_venues`), a followed team's own resolved coordinates,
or a name index built from already-located ``TeamORM`` / ``StadiumORM`` rows.
A followed team's *away* game, though, can sit at a ground we've never
located; those venue names are geocoded by a background job
(:func:`app.scheduler.jobs.refresh_game_venue_coords`) and cached here, so a
request never geocodes inline (Nominatim is ≤1 req/s).

Best-effort throughout: with no Redis configured every read is a miss and
every write a no-op, so the map still works from the host table + the
in-memory index — it just won't gain the geocoded away grounds.
"""

from __future__ import annotations

import re
import unicodedata

from app.services import cache

# A resolved stadium doesn't move, so hits live a week; a miss is re-checked
# sooner, so a transient Nominatim failure doesn't strand a venue for long.
_HIT_TTL_SECONDS = 7 * 24 * 3600
_MISS_TTL_SECONDS = 6 * 3600


def normalize(venue: str | None) -> str:
    """Fold accents/case, collapse non-alphanumerics — a forgiving match key.

    The same key is used for the in-memory venue index and the Redis cache,
    so a game's venue string and a stored stadium's venue string join even
    with punctuation/whitespace/diacritic drift (e.g. "St. James' Park" vs
    "St James Park", or "Estádio do Maracanã" vs "Estadio do Maracana").
    """
    decomposed = unicodedata.normalize("NFKD", venue or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


def build_index(teams: list, stadiums: list) -> dict[str, tuple[float, float]]:
    """Map ``normalize(venue)`` → ``(lat, lon)`` from located teams + stadiums.

    Pure: takes the already-fetched ``TeamORM`` rows (with ``home_venue`` +
    ``venue_lat``/``venue_lon``) and ``StadiumORM`` rows (with ``venue`` +
    ``lat``/``lon``) and indexes every one that has both a venue name and
    coordinates.  Teams win ties (listed last) — they're the user's own.
    """
    index: dict[str, tuple[float, float]] = {}
    for stadium in stadiums:
        key = normalize(getattr(stadium, "venue", None))
        lat = getattr(stadium, "lat", None)
        lon = getattr(stadium, "lon", None)
        if key and lat is not None and lon is not None:
            index[key] = (lat, lon)
    for team in teams:
        key = normalize(getattr(team, "home_venue", None))
        lat = getattr(team, "venue_lat", None)
        lon = getattr(team, "venue_lon", None)
        if key and lat is not None and lon is not None:
            index[key] = (lat, lon)
    return index


def _key(venue: str) -> str:
    return f"venuecoords:{normalize(venue)}"


async def get_cached(venue: str | None) -> tuple[float, float] | None:
    """Cached coordinates for a venue name, or ``None`` (miss / not yet resolved)."""
    if not normalize(venue):
        return None
    payload = await cache.cache_get_json(_key(venue))  # type: ignore[arg-type]
    if not isinstance(payload, dict):
        return None
    lat, lon = payload.get("lat"), payload.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


async def has_entry(venue: str | None) -> bool:
    """Whether a venue has any cached entry — a hit OR a recorded miss.

    Lets the background job skip a venue it has already attempted (so a
    persistent geocode miss isn't re-tried on every pass), while
    :func:`get_cached` still reports a miss as unresolved to the route.
    """
    if not normalize(venue):
        return True  # nothing resolvable; treat as "handled"
    return await cache.cache_get_json(_key(venue)) is not None  # type: ignore[arg-type]


async def set_coords(venue: str | None, coords: tuple[float, float] | None) -> None:
    """Cache resolved coordinates, or a miss marker, for a venue name."""
    if not normalize(venue):
        return
    if coords is None:
        await cache.cache_set_json(_key(venue), {"miss": True}, _MISS_TTL_SECONDS)  # type: ignore[arg-type]
    else:
        await cache.cache_set_json(
            _key(venue),
            {"lat": coords[0], "lon": coords[1]},
            _HIT_TTL_SECONDS,  # type: ignore[arg-type]
        )
