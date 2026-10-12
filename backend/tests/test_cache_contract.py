"""services/cache.py promises the app keeps working when Redis is unset,
unreachable, or erroring — no call may raise.  That contract is easy to
break without noticing, so it gets pinned here.

Unset is not the same as down: with no ``redis_url`` an allowlist of
upstream read caches is kept in a bounded in-process store instead (the
second half of this file); a configured Redis that fails stays a no-op.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.config import get_settings
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


async def test_no_redis_url_means_noop_off_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
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


# ---------------------------------------------------------------------------
# No redis_url: the in-process store for the allowlisted read caches
# ---------------------------------------------------------------------------

# One key per allowlisted cache, spelled the way its caller builds it.
_LOCAL_KEYS = [
    "weather:40.71,-74.01,metric,today",
    "odds:espn:401",
    "leaders:v2:harbor-hoops",
    "scorers:azure-coast-cup:12",
    "bracket:harbor-hoops",
    "history:results:ember-otters:2024",
]
# The Redis-only caches: miss markers, a last-good fallback, a job's cache —
# and the on-this-day list, which is cached even when the scan behind it
# found nothing because ESPN was failing.
_REDIS_ONLY_KEYS = [
    "venuecoords:foxhollow arena",
    "tsdbphoto:v2:wren halloway|glimmer foxes|soccer",
    "wiki:v2:en:glimmer foxes|soccer",
    "standings:lastgood:violet-circuit",
    "onthisday:ember-otters:10-11",
]


class _Clock:
    """The store's clock, moved by hand so no test has to sleep."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(cache, "_now", clock)
    return clock


async def test_local_store_serves_a_value_until_its_ttl_runs_out(clock: _Clock) -> None:
    await cache.cache_set_json("odds:espn:401", {"odds": {"spread": -2.5}}, 600)

    clock.now += 599
    assert await cache.cache_get_json("odds:espn:401") == {"odds": {"spread": -2.5}}

    clock.now += 1
    assert await cache.cache_get_json("odds:espn:401") is None
    # Expiry is checked on the read, which also drops the dead entry.
    assert "odds:espn:401" not in cache._local

    # A cache configured down to a zero TTL stores nothing, as with Redis
    # (which refuses a non-positive expiry).
    await cache.cache_set_json("weather:1.00,2.00,metric,today", {"temperature": 9.0}, 0)
    assert "weather:1.00,2.00,metric,today" not in cache._local


async def test_local_store_expires_on_wall_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TTL has to be measured on a clock that keeps counting while the
    machine sleeps, as Redis's does.  ``time.monotonic`` does not on macOS
    or Linux: a laptop closed overnight came back still holding last night's
    forecast, odds and bracket as live."""
    assert cache._now is time.time  # the clock the app runs on, before any stand-in
    clock = _Clock()
    monkeypatch.setattr(cache, "_now", clock)

    await cache.cache_set_json("weather:40.71,-74.01,metric,today", {"temperature": 9.0}, 2700)
    clock.now += 9 * 3600  # the lid was closed for the night
    assert await cache.cache_get_json("weather:40.71,-74.01,metric,today") is None

    # A wall clock can also be set BACK.  An entry written "in the future"
    # has an age nobody can know, and would outlive its TTL by the size of
    # the step, so the read that finds it drops it.
    await cache.cache_set_json("odds:espn:401", {"odds": None}, 600)
    clock.now += 599
    assert await cache.cache_get_json("odds:espn:401") == {"odds": None}
    clock.now -= 700
    assert await cache.cache_get_json("odds:espn:401") is None
    assert "odds:espn:401" not in cache._local


async def test_local_store_holds_only_the_allowlisted_caches(clock: _Clock) -> None:
    for key in _LOCAL_KEYS + _REDIS_ONLY_KEYS:
        await cache.cache_set_json(key, {"key": key}, 60)

    for key in _LOCAL_KEYS:
        assert await cache.cache_get_json(key) == {"key": key}
    # Everything else is still the no-op it was: never stored, always a miss.
    for key in _REDIS_ONLY_KEYS:
        assert await cache.cache_get_json(key) is None
    assert set(cache._local) == set(_LOCAL_KEYS)


async def test_local_store_evicts_the_least_recently_used_entry_at_the_cap(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "_LOCAL_MAX_ENTRIES", 3)
    for game in ("a", "b", "c"):
        await cache.cache_set_json(f"odds:{game}", game, 60)
    # Reading "a" and rewriting "b" make "c" the least recently used.
    assert await cache.cache_get_json("odds:a") == "a"
    await cache.cache_set_json("odds:b", "b2", 60)
    assert len(cache._local) == 3  # a rewrite is not a new entry

    await cache.cache_set_json("odds:d", "d", 60)

    assert await cache.cache_get_json("odds:c") is None
    assert await cache.cache_get_json("odds:a") == "a"
    assert await cache.cache_get_json("odds:b") == "b2"
    assert await cache.cache_get_json("odds:d") == "d"
    assert len(cache._local) == 3


async def test_local_store_hands_out_json_copies_nobody_can_mutate(clock: _Clock) -> None:
    kicked_off = datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc)
    board = {"rows": [{"player": "Wren Halloway", "goals": 3}], "span": (1, 2), "at": kicked_off}
    await cache.cache_set_json("scorers:azure-coast-cup:12", board, 60)

    # The caller keeps working on what it stored...
    board["rows"].append({"player": "Sable Quillan", "goals": 1})
    first = await cache.cache_get_json("scorers:azure-coast-cup:12")
    # ...and gets back what Redis would return: the JSON shapes of the value
    # as it was stored (tuple -> list, datetime -> text), not the live object.
    assert first == {
        "rows": [{"player": "Wren Halloway", "goals": 3}],
        "span": [1, 2],
        "at": str(kicked_off),
    }

    # A reader mutating its copy changes nothing stored.
    first["rows"].clear()
    first["span"].append(3)
    second = await cache.cache_get_json("scorers:azure-coast-cup:12")
    assert second["rows"] == [{"player": "Wren Halloway", "goals": 3}]
    assert second["span"] == [1, 2]


async def test_local_store_counts_hits_and_misses(clock: _Clock) -> None:
    hits_before, misses_before = cache.cache_counters()

    assert await cache.cache_get_json("bracket:harbor-hoops") is None  # absent
    await cache.cache_set_json("bracket:harbor-hoops", {"rounds": []}, 60)
    assert await cache.cache_get_json("bracket:harbor-hoops") == {"rounds": []}  # hit
    clock.now += 60
    assert await cache.cache_get_json("bracket:harbor-hoops") is None  # expired
    assert await cache.cache_get_json("wiki:v2:en:glimmer foxes|soccer") is None  # not stored

    hits, misses = cache.cache_counters()
    assert hits == hits_before + 1
    assert misses == misses_before + 3


async def test_close_cache_empties_the_local_store(clock: _Clock) -> None:
    await cache.cache_set_json("leaders:v2:harbor-hoops", {"rows": []}, 900)
    assert await cache.cache_get_json("leaders:v2:harbor-hoops") == {"rows": []}

    await cache.close_cache()

    assert await cache.cache_get_json("leaders:v2:harbor-hoops") is None
    assert not cache._local


async def test_configured_redis_never_falls_back_to_the_local_store(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stored while no Redis was configured, so the store really holds it.
    await cache.cache_set_json("odds:espn:401", {"odds": None}, 600)
    assert await cache.cache_get_json("odds:espn:401") == {"odds": None}

    monkeypatch.setattr(get_settings(), "redis_url", "redis://cache.invalid:6379/0")

    # Redis is configured but down: a miss and a no-op, exactly as before.
    monkeypatch.setattr(cache, "_client", _ExplodingClient())
    assert await cache.cache_get_json("odds:espn:401") is None
    await cache.cache_set_json("odds:espn:402", {"odds": None}, 600)
    assert list(cache._local) == ["odds:espn:401"]

    # Configured but the client cannot even be built: the same.
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise ValueError("unusable redis url")

    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache.aioredis, "from_url", refuse)
    assert await cache.cache_get_json("odds:espn:401") is None
    await cache.cache_set_json("odds:espn:403", {"odds": None}, 600)
    assert list(cache._local) == ["odds:espn:401"]
