"""Circuit-breaker state machine + registry guard tests."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.providers import registry
from app.providers.http_util import TransientProviderError
from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    ProviderUnavailable,
)


def test_opens_after_threshold_failures() -> None:
    breaker = CircuitBreaker(name="t", failure_threshold=3)
    for _ in range(2):
        breaker.record_failure("boom")
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True
    breaker.record_failure("boom")
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False  # cooldown not elapsed


def test_success_resets_failures() -> None:
    breaker = CircuitBreaker(name="t", failure_threshold=3)
    breaker.record_failure("x")
    breaker.record_failure("x")
    breaker.record_success()
    assert breaker.failures == 0
    assert breaker.state is CircuitState.CLOSED


def test_half_open_probe_then_recovers() -> None:
    breaker = CircuitBreaker(
        name="t", failure_threshold=1, reset_after=timedelta(seconds=30)
    )
    breaker.record_failure("down")
    assert breaker.state is CircuitState.OPEN
    # Elapse the cooldown — the next allow() arms a half-open probe.
    assert breaker.opened_at is not None
    breaker.opened_at = breaker.opened_at - timedelta(seconds=31)
    assert breaker.allow() is True
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_probe_failure_reopens() -> None:
    breaker = CircuitBreaker(
        name="t", failure_threshold=1, reset_after=timedelta(seconds=30)
    )
    breaker.record_failure("down")
    breaker.opened_at = breaker.opened_at - timedelta(seconds=31)  # type: ignore[operator]
    assert breaker.allow() is True  # half-open
    breaker.record_failure("still down")
    assert breaker.state is CircuitState.OPEN


def test_snapshot_shape() -> None:
    breaker = CircuitBreaker(name="t")
    breaker.record_failure("oops")
    snap = breaker.snapshot()
    assert snap["state"] == "closed"
    assert snap["failures"] == 1
    assert snap["last_error_at"] is not None


# --- Registry guard ------------------------------------------------------


class _Flaky:
    """Provider stand-in whose one network method can be toggled to fail."""

    provider_id = "espn"

    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.calls = 0

    async def get_standings(self, league):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream down")
        return "standings"

    async def close(self) -> None:
        return None


async def test_guard_opens_and_fails_fast() -> None:
    breaker = CircuitBreaker(name="espn", failure_threshold=3)
    flaky = _Flaky(fail=True)
    guard = registry._GuardedProvider(flaky, breaker)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await guard.get_standings(None)
    assert breaker.state is CircuitState.OPEN
    assert flaky.calls == 3

    # Now the breaker is open: the call is rejected WITHOUT hitting upstream.
    with pytest.raises(ProviderUnavailable):
        await guard.get_standings(None)
    assert flaky.calls == 3  # unchanged — failed fast


async def test_guard_records_success() -> None:
    breaker = CircuitBreaker(name="espn", failure_threshold=2)
    flaky = _Flaky(fail=True)
    guard = registry._GuardedProvider(flaky, breaker)

    with pytest.raises(RuntimeError):
        await guard.get_standings(None)
    assert breaker.failures == 1

    flaky.fail = False
    assert await guard.get_standings(None) == "standings"
    assert breaker.failures == 0
    assert breaker.state is CircuitState.CLOSED


async def test_guard_passes_close_through_unbroken() -> None:
    breaker = CircuitBreaker(name="espn", failure_threshold=1)
    breaker.record_failure("x")  # OPEN
    guard = registry._GuardedProvider(_Flaky(fail=False), breaker)
    # close() is not guarded, so it works even with the breaker open.
    await guard.close()


def test_registry_guards_remote_but_not_local() -> None:
    espn = registry.get_provider("espn")
    assert type(espn).__name__ == "_GuardedProvider"
    assert espn.provider_id == "espn"

    # A provider whose id isn't in _GUARDED_PROVIDERS is returned unwrapped.
    class _LocalProvider:
        provider_id = "local-test"

        async def close(self) -> None: ...

    registry.register_provider(_LocalProvider())
    try:
        local = registry.get_provider("local-test")
        assert type(local).__name__ != "_GuardedProvider"
        assert local.provider_id == "local-test"
    finally:
        registry._providers.pop("local-test", None)
        registry._guards.pop("local-test", None)


# --- Half-open single-probe gate -----------------------------------------


def _half_open(breaker: CircuitBreaker) -> None:
    """Drive a fresh breaker to OPEN then elapse its cooldown in place."""
    breaker.record_failure("down")
    assert breaker.state is CircuitState.OPEN
    assert breaker.opened_at is not None
    breaker.opened_at = breaker.opened_at - (breaker.reset_after + timedelta(seconds=1))


def test_half_open_admits_single_probe_blocks_concurrent() -> None:
    breaker = CircuitBreaker(name="t", failure_threshold=1)
    _half_open(breaker)
    # First caller after the cooldown arms the one probe and proceeds.
    assert breaker.allow() is True
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker._probe_in_flight is True
    # A concurrent caller, before the probe records an outcome, fails fast —
    # the old `return True  # CLOSED or HALF_OPEN` would have let it through.
    assert breaker.allow() is False
    assert breaker.allow() is False
    # The probe succeeds → closed and calls flow again.
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker._probe_in_flight is False
    assert breaker.allow() is True


def test_half_open_probe_failure_disarms_and_reopens() -> None:
    breaker = CircuitBreaker(name="t", failure_threshold=1)
    _half_open(breaker)
    assert breaker.allow() is True  # armed
    breaker.record_failure("still down")
    assert breaker.state is CircuitState.OPEN
    assert breaker._probe_in_flight is False  # disarmed so the next cooldown re-arms
    assert breaker.allow() is False  # fresh cooldown not elapsed


def test_abort_probe_reopens_and_rearms_single_probe() -> None:
    breaker = CircuitBreaker(name="t", failure_threshold=1)
    _half_open(breaker)
    assert breaker.allow() is True  # armed probe

    # The probe call unwinds without recording (e.g. cancelled): abort must
    # RE-OPEN, not leave the breaker half-open with the gate disarmed (which
    # would let every later caller through at once).
    breaker.abort_probe()
    assert breaker.state is CircuitState.OPEN
    assert breaker._probe_in_flight is False
    assert breaker.allow() is False  # fresh cooldown not elapsed yet

    # After a fresh cooldown exactly ONE probe is re-armed.
    assert breaker.opened_at is not None
    breaker.opened_at = breaker.opened_at - (breaker.reset_after + timedelta(seconds=1))
    assert breaker.allow() is True
    assert breaker._probe_in_flight is True
    assert breaker.allow() is False  # second concurrent caller still blocked


async def test_guard_aborts_probe_on_cancellation() -> None:
    breaker = CircuitBreaker(name="espn", failure_threshold=1)
    _half_open(breaker)
    started = asyncio.Event()

    class _Hanging:
        provider_id = "espn"

        async def get_standings(self, league):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.Event().wait()  # never completes

        async def close(self) -> None:
            return None

    guard = registry._GuardedProvider(_Hanging(), breaker)
    task = asyncio.create_task(guard.get_standings(None))
    await started.wait()
    assert breaker.state is CircuitState.HALF_OPEN  # the probe is armed
    assert breaker._probe_in_flight is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The guard's finally disarmed and re-opened, so the breaker is not stuck
    # half-open with the single-probe gate disabled.
    assert breaker._probe_in_flight is False
    assert breaker.state is CircuitState.OPEN


class _Transient:
    """Provider stand-in whose network method raises a transient error."""

    provider_id = "espn"

    async def get_standings(self, league):  # type: ignore[no-untyped-def]
        raise TransientProviderError("upstream timeout")

    async def close(self) -> None:
        return None


async def test_guard_records_failure_on_transient_provider_error() -> None:
    # The bridge for bug #1: a provider that surfaces a sustained outage as
    # TransientProviderError now trips its breaker through the guard.
    breaker = CircuitBreaker(name="espn", failure_threshold=2)
    guard = registry._GuardedProvider(_Transient(), breaker)

    for _ in range(2):
        with pytest.raises(TransientProviderError):
            await guard.get_standings(None)
    assert breaker.state is CircuitState.OPEN
    # And now it fails fast without touching upstream.
    with pytest.raises(ProviderUnavailable):
        await guard.get_standings(None)
