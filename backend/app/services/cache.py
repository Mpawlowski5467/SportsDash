"""Optional Redis JSON cache, with an in-process stand-in for a few keys.

Entirely best-effort: when Redis is unreachable, or any call fails, every
function silently no-ops (returns ``None``) and logs at debug level.  The
app must return the same data with or without Redis; only how often it has
to ask upstream differs.

When ``settings.redis_url`` is unset there is no Redis to ask, and a plain
no-op would make every request to an upstream-backed endpoint repeat its
whole fan-out — the default on the desktop app and in dev.  So the keys on
:data:`_LOCAL_PREFIXES` are kept in a bounded in-process store instead;
every other key still no-ops.  With a ``redis_url`` configured the store is
never touched: a Redis failure is a miss, not a fall-back to it.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None

# Key prefixes kept in-process when no redis_url is configured: the positive,
# TTL'd caches of upstream-derived read data behind the endpoints the
# frontend polls.  An explicit list on purpose: the caches left off it mean
# something else — venuecoords: and tsdbphoto: also store miss markers,
# standings:lastgood: is a fallback snapshot rather than a TTL cache, wiki:
# feeds a background enrichment job — and code elsewhere counts on the venue
# one being absent without Redis (the map never flags a venue pending, the
# venue geocode job does not run).  They keep missing here.
#
# onthisday: (services/on_this_day.py, 24 h) is left off as well.  It stores
# whatever its season scan assembled, and a scan that ran while ESPN was
# failing assembles an EMPTY list — which would then be a team's
# anniversaries for the rest of the day.  Its upstream cost is already
# absorbed one level down: the scan reads history:results:, which only ever
# holds a season that was fetched whole.
_LOCAL_PREFIXES = (
    "weather:",  # services/weather.py — a venue's day, weather_cache_minutes
    "odds:",  # routes/odds.py — one game, 10 min
    "leaders:v2:",  # routes/leaders.py — a league's board, 15 min
    "scorers:",  # routes/scorers.py — a league's Golden Boot, 30 min
    "bracket:",  # routes/bracket.py — a league's playoff bracket, 30 min
    "history:results:",  # services/season_results.py — a team-season, 6 h / 7 d
)
# Entry cap for the in-process store.  Expiry is only checked on read, so a
# key nobody asks for again (yesterday's forecast, a finished game's odds, a
# scorers key superseded by a new final count) sits there until it is the
# least recently used entry and a write pushes it out — this cap is what
# bounds those.  A display following some 20 whole leagues holds about 400
# venue forecasts for the map plus a few hundred odds, calendar-weather and
# team-season keys, under 1,000 live entries, and a refresh rewrites its key
# rather than adding one — so at 2048 it is the dead entries that go.  Most
# values are well under 1 KB; the largest, a team-season of results, is
# 25-110 KB (a soccer league to an MLB season).
_LOCAL_MAX_ENTRIES = 2048

# key -> (stored at, TTL seconds, JSON text), least recently used first.  The
# value is held serialized, exactly as Redis would hold it, so every read
# decodes a fresh copy: callers get the shapes the Redis path returns (a tuple
# comes back a list, a datetime a string) and cannot mutate what is stored.
_local: OrderedDict[str, tuple[float, int, str]] = OrderedDict()

# The store's clock: WALL time, which is what Redis expires on.  Not
# time.monotonic(): on macOS and Linux that stands still while the machine
# sleeps, so a laptop opened in the morning would be served last night's
# forecast as live for the rest of its TTL.  A module attribute so tests can
# move time, not sleep.
_now = time.time

# Cumulative read outcomes, read by ``/api/metrics``.  A "hit" is a call
# that returned a decoded value, from Redis or from the in-process store;
# everything else — no client configured and no stored entry, GET failure,
# key absent or expired, invalid JSON — counts as a miss, because
# best-effort semantics serve every one of those paths as a miss anyway.
_hits = 0
_misses = 0


def cache_counters() -> tuple[int, int]:
    """``(hits, misses)`` so far, for the metrics endpoint."""
    return _hits, _misses


def _get_client() -> aioredis.Redis | None:
    """Lazily build the client; None when no redis_url is configured."""
    global _client
    if _client is not None:
        return _client
    redis_url = get_settings().redis_url
    if not redis_url:
        return None
    try:
        _client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    except Exception:
        logger.debug("cache: could not build redis client", exc_info=True)
        return None
    return _client


def _is_local_key(key: str) -> bool:
    """Whether ``key`` belongs to the in-process store right now.

    Asked on every clientless call rather than decided once: a configured
    ``redis_url`` whose client could not be built also leaves the client
    ``None``, and that must stay a no-op instead of turning to the store.
    """
    return not get_settings().redis_url and key.startswith(_LOCAL_PREFIXES)


def _local_get(key: str) -> str | None:
    """The live JSON text under ``key``; an expired entry is dropped."""
    entry = _local.get(key)
    if entry is None:
        return None
    stored_at, ttl_seconds, payload = entry
    # Live only while 0 <= age < TTL.  A negative age means the wall clock was
    # set back after the write: how old the entry really is cannot be known,
    # and measured against the new clock it would outlive its TTL, so it goes.
    if not 0 <= _now() - stored_at < ttl_seconds:
        del _local[key]
        return None
    _local.move_to_end(key)
    return payload


def _local_set(key: str, payload: str, ttl_seconds: int) -> None:
    """Store ``payload`` as the most recently used entry, evicting past the cap."""
    if ttl_seconds <= 0:
        # Redis refuses a non-positive expiry too (the SET errors and is
        # swallowed), so a cache configured down to zero stores nothing.
        return
    _local[key] = (_now(), ttl_seconds, payload)
    _local.move_to_end(key)
    while len(_local) > _LOCAL_MAX_ENTRIES:
        _local.popitem(last=False)


def clear_local_cache() -> None:
    """Empty the in-process store (shutdown, and between tests)."""
    _local.clear()


async def cache_get_json(key: str) -> Any | None:
    global _hits, _misses
    client = _get_client()
    if client is None:
        raw = _local_get(key) if _is_local_key(key) else None
    else:
        try:
            raw = await client.get(key)
        except Exception:
            logger.debug("cache: GET %s failed", key, exc_info=True)
            _misses += 1
            return None
    if raw is None:
        _misses += 1
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.debug("cache: invalid JSON under %s", key, exc_info=True)
        _misses += 1
        return None
    _hits += 1
    return value


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = _get_client()
    if client is None and not _is_local_key(key):
        return None
    try:
        payload = json.dumps(value, default=str)
        if client is None:
            _local_set(key, payload, ttl_seconds)
        else:
            await client.set(key, payload, ex=ttl_seconds)
    except Exception:
        logger.debug("cache: SET %s failed", key, exc_info=True)
    return None


async def close_cache() -> None:
    global _client
    clear_local_cache()
    client = _client
    _client = None
    if client is None:
        return None
    try:
        await client.aclose()
    except Exception:
        logger.debug("cache: close failed", exc_info=True)
    return None
