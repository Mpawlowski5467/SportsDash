"""Per-provider circuit breaker.

The app binds each league to exactly one provider, so there is no second
source to fail over to — but a provider that is down or rate-limiting
shouldn't be hammered on every poll.  A breaker tracks consecutive
failures per provider and, once a threshold is crossed, "opens": calls
fail fast with :class:`ProviderUnavailable` until a cooldown elapses, when
a single probe ("half-open") decides whether to close again or stay open.

State lives in process (one worker), keyed by provider id; the registry
wraps remote providers so every network call records success/failure, and
``/health`` reads the breaker snapshots.  All timing uses
:func:`app.timeutil.utcnow` (tz-aware UTC) so it stays consistent with the
rest of the app.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from app import timeutil

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"        # healthy — calls pass through
    OPEN = "open"            # failing fast — calls rejected until cooldown
    HALF_OPEN = "half_open"  # cooldown elapsed — one probe allowed


class ProviderUnavailable(RuntimeError):
    """Raised when a provider's circuit is open (failing fast)."""

    def __init__(self, provider_id: str, retry_at: datetime | None = None) -> None:
        super().__init__(f"Provider {provider_id!r} is unavailable (circuit open)")
        self.provider_id = provider_id
        self.retry_at = retry_at


@dataclass
class CircuitBreaker:
    """Consecutive-failure breaker for a single provider."""

    name: str
    failure_threshold: int = 5
    reset_after: timedelta = timedelta(seconds=60)

    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    # True while a half-open probe is outstanding; gates every other caller
    # to exactly one probe per cooldown (see allow / record_* / abort_probe).
    _probe_in_flight: bool = False

    def allow(self) -> bool:
        """Whether a call may proceed now (and arm a half-open probe).

        Half-open admits exactly ONE probe: the caller that flips OPEN →
        HALF_OPEN arms ``_probe_in_flight`` and returns True; because there
        is no ``await`` between the flag set and the return, this is race-free
        under asyncio.  Every other caller fails fast until that probe records
        an outcome (record_success / record_failure) or unwinds (abort_probe).
        """
        if self.state is CircuitState.OPEN:
            if (
                self.opened_at is not None
                and timeutil.utcnow() - self.opened_at >= self.reset_after
            ):
                # Cooldown elapsed: arm a single probe.
                self.state = CircuitState.HALF_OPEN
                self._probe_in_flight = True
                logger.info("circuit %s: cooldown elapsed → half-open probe", self.name)
                return True
            return False
        if self.state is CircuitState.HALF_OPEN:
            # A probe is already deciding; everyone else fails fast.
            return not self._probe_in_flight
        return True  # CLOSED

    def record_success(self) -> None:
        if self.state is not CircuitState.CLOSED:
            logger.info("circuit %s: success → closed", self.name)
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = None
        self._probe_in_flight = False

    def record_failure(self, error: str | None = None) -> None:
        now = timeutil.utcnow()
        self.last_error_at = now
        self.last_error = error
        self._probe_in_flight = False
        if self.state is CircuitState.HALF_OPEN:
            # The probe failed — re-open immediately for another cooldown.
            self._open(now)
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._open(now)

    def abort_probe(self) -> None:
        """Disarm a half-open probe that was admitted but never recorded.

        Reached only when an armed probe call unwinds without recording an
        outcome (e.g. its coroutine is cancelled between ``allow`` returning
        True and ``record_success``/``record_failure``).  Re-open for a fresh
        cooldown so the next window arms a new single probe — otherwise the
        breaker would linger HALF_OPEN with the gate disarmed and let every
        later caller through at once (the thundering herd this guards).
        """
        if self._probe_in_flight:
            self._probe_in_flight = False
            self.state = CircuitState.OPEN
            self.opened_at = timeutil.utcnow()

    def _open(self, now: datetime) -> None:
        if self.state is not CircuitState.OPEN:
            logger.warning(
                "circuit %s: OPEN after %d failures (last: %s)",
                self.name,
                self.failures,
                self.last_error,
            )
        self.state = CircuitState.OPEN
        self.opened_at = now

    @property
    def retry_at(self) -> datetime | None:
        if self.state is CircuitState.OPEN and self.opened_at is not None:
            return self.opened_at + self.reset_after
        return None

    def snapshot(self) -> dict[str, object]:
        """A JSON-friendly view for the health endpoint."""
        return {
            "state": self.state.value,
            "failures": self.failures,
            "last_error_at": (
                self.last_error_at.isoformat() if self.last_error_at else None
            ),
        }


_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    """The (lazily created) breaker for a provider id."""
    breaker = _breakers.get(name)
    if breaker is None:
        breaker = CircuitBreaker(name=name)
        _breakers[name] = breaker
    return breaker


def all_breakers() -> dict[str, CircuitBreaker]:
    """A snapshot copy of every known breaker, keyed by provider id."""
    return dict(_breakers)


def reset_all() -> None:
    """Drop all breaker state (used by tests for isolation)."""
    _breakers.clear()
