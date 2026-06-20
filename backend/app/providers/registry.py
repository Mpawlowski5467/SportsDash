"""Provider registry.

Maps provider ids ("espn", "thesportsdb", ...) to live
:class:`SportsProvider` instances.  The built-in providers are
instantiated and registered at import time; anything else can register
itself via :func:`register_provider`.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from app.providers.base import SportsProvider
from app.providers.espn import EspnProvider
from app.providers.thesportsdb import TheSportsDbProvider
from app.services import circuit_breaker

logger = logging.getLogger(__name__)

# Providers that make real network calls — these get a circuit-breaker guard
# so a down/rate-limited source fails fast instead of being hammered.  A
# deterministic, local provider (id not listed here) is never guarded.
_GUARDED_PROVIDERS = frozenset({"espn", "thesportsdb"})
# Methods on a guarded provider that must NOT count toward the breaker
# (resource teardown, not a data call).
_UNGUARDED_METHODS = frozenset({"close"})

_providers: dict[str, SportsProvider] = {}
_guards: dict[str, "_GuardedProvider"] = {}


class _GuardedProvider:
    """Wraps a provider so every async data call passes a circuit breaker.

    When the breaker is open the call is rejected with
    :class:`~app.services.circuit_breaker.ProviderUnavailable` (callers
    already degrade or isolate per-job); otherwise the call runs and its
    outcome records success/failure, which is what trips/recovers the
    breaker.  Non-coroutine attributes and ``close`` pass straight through.
    """

    def __init__(
        self, provider: SportsProvider, breaker: circuit_breaker.CircuitBreaker
    ) -> None:
        self._provider = provider
        self._breaker = breaker
        self.provider_id = provider.provider_id

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._provider, name)
        if name in _UNGUARDED_METHODS or not inspect.iscoroutinefunction(attr):
            return attr
        return self._guard(attr)

    def _guard(self, method: Callable[..., Any]) -> Callable[..., Any]:
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            if not self._breaker.allow():
                raise circuit_breaker.ProviderUnavailable(
                    self._provider.provider_id, self._breaker.retry_at
                )
            recorded = False
            try:
                result = await method(*args, **kwargs)
            except circuit_breaker.ProviderUnavailable:
                raise
            except Exception as exc:
                self._breaker.record_failure(f"{type(exc).__name__}: {exc}")
                recorded = True
                raise
            else:
                self._breaker.record_success()
                recorded = True
                return result
            finally:
                if not recorded:
                    # The call unwound without recording an outcome (e.g. the
                    # coroutine was cancelled): disarm any half-open probe so
                    # the breaker re-arms next cooldown instead of lingering
                    # half-open with the single-probe gate disabled.
                    self._breaker.abort_probe()

        return guarded


def register_provider(provider: SportsProvider) -> None:
    """Register (or replace) a provider under its ``provider_id``."""
    if provider.provider_id in _providers:
        logger.debug("Replacing registered provider %r", provider.provider_id)
    _providers[provider.provider_id] = provider
    _guards.pop(provider.provider_id, None)  # rebuild the guard on next lookup


def get_provider(provider_id: str) -> SportsProvider:
    """Look up a provider by id; raises ``KeyError`` if unknown.

    Remote providers are returned wrapped in a circuit-breaker guard; the
    mock provider is returned as-is.
    """
    try:
        provider = _providers[provider_id]
    except KeyError:
        raise KeyError(
            f"Unknown provider {provider_id!r}; registered: {sorted(_providers)}"
        ) from None

    if provider_id not in _GUARDED_PROVIDERS:
        return provider

    guard = _guards.get(provider_id)
    if guard is None or guard._provider is not provider:
        guard = _GuardedProvider(provider, circuit_breaker.get_breaker(provider_id))
        _guards[provider_id] = guard
    return guard  # type: ignore[return-value]


async def close_all() -> None:
    """Close every registered provider, never letting one failure stop the rest."""
    for provider in _providers.values():
        try:
            await provider.close()
        except Exception:
            logger.exception("Error closing provider %r", provider.provider_id)


register_provider(EspnProvider())
register_provider(TheSportsDbProvider())
