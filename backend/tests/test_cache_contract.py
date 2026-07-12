"""services/cache.py promises the app behaves identically when Redis is
unset, unreachable, or erroring — every function must silently no-op.
That contract is easy to break without noticing, so it gets pinned here.
"""

from __future__ import annotations

import pytest

from app.services import cache


class _ExplodingClient:
    """Stands in for a redis client whose every call fails."""

    async def get(self, key: str) -> str:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int) -> None:
        raise ConnectionError("redis down")

    async def aclose(self) -> None:
        raise ConnectionError("redis down")


class _StaticClient:
    def __init__(self, payload: str | None) -> None:
        self._payload = payload

    async def get(self, key: str) -> str | None:
        return self._payload


async def test_no_redis_url_means_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_get_client", lambda: None)
    assert await cache.cache_get_json("k") is None
    assert await cache.cache_set_json("k", {"a": 1}, 60) is None


async def test_erroring_redis_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_client", _ExplodingClient())
    assert await cache.cache_get_json("k") is None
    assert await cache.cache_set_json("k", {"a": 1}, 60) is None
    await cache.close_cache()  # must swallow the close failure too
    assert cache._client is None


async def test_invalid_json_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_client", _StaticClient("{not json"))
    assert await cache.cache_get_json("k") is None


async def test_valid_json_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_client", _StaticClient('{"a": 1}'))
    assert await cache.cache_get_json("k") == {"a": 1}
