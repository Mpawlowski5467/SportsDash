"""Optional Redis JSON cache.

Entirely best-effort: when ``settings.redis_url`` is unset, or Redis is
unreachable, or any call fails, every function silently no-ops (returns
``None``) and logs at debug level.  The app must behave identically
with or without Redis.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None


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


async def cache_get_json(key: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except Exception:
        logger.debug("cache: GET %s failed", key, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.debug("cache: invalid JSON under %s", key, exc_info=True)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = _get_client()
    if client is None:
        return None
    try:
        payload = json.dumps(value, default=str)
        await client.set(key, payload, ex=ttl_seconds)
    except Exception:
        logger.debug("cache: SET %s failed", key, exc_info=True)
    return None


async def close_cache() -> None:
    global _client
    client = _client
    _client = None
    if client is None:
        return None
    try:
        await client.aclose()
    except Exception:
        logger.debug("cache: close failed", exc_info=True)
    return None
