"""Shared HTTP retry/backoff for provider adapters.

A thin wrapper around an httpx GET that retries *transient* failures —
connection errors, timeouts, and ``429``/``5xx`` responses — with
exponential backoff and a little jitter, honoring a ``429``'s
``Retry-After`` header when present.  Permanent failures (e.g. ``404``) are
not retried and are returned for the caller to handle, while exhausted
retries raise :class:`TransientProviderError` — a non-``httpx`` type the
providers' ``except httpx.HTTPError`` degrade-blocks do not catch, so a
sustained outage propagates to the per-provider circuit breaker instead of
being silently swallowed.  This smooths over brief blips and rate limits.
"""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

# Status codes worth retrying: rate-limit + transient upstream/proxy errors.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# Cap any single backoff sleep, even if a Retry-After header is huge.
_MAX_BACKOFF = 30.0


class TransientProviderError(RuntimeError):
    """A transient upstream failure that survived every retry.

    Raised by :func:`get_with_retry` when retries are exhausted on a
    connection/timeout error or a retryable status (429/5xx).  It is
    deliberately *not* an ``httpx`` error: a provider's ``except
    httpx.HTTPError`` blocks (which degrade sparse / not-found responses to
    ``None``/``[]``) therefore do NOT swallow it, so it propagates up to the
    registry's circuit-breaker guard and counts as a real failure — while a
    genuine 404 or empty body still degrades quietly.
    """


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    max_retries: int,
    backoff_base: float,
    label: str = "http",
) -> httpx.Response:
    """GET ``url`` with retry/backoff; return the final response.

    Retries up to ``max_retries`` additional attempts on transient transport
    errors and on ``429``/``5xx`` responses, sleeping
    ``backoff_base * 2**attempt`` (plus jitter), or the server's
    ``Retry-After`` on a ``429``.  On give-up — a connection/timeout error or
    a still-retryable status after the last attempt — it raises
    :class:`TransientProviderError` so the provider's circuit breaker counts
    the failure.  Non-retryable statuses (e.g. ``404``) are returned
    unchanged; ``raise_for_status`` is intentionally left to the caller so a
    genuine not-found still degrades to ``None`` without tripping the breaker.
    """
    attempt = 0
    while True:
        try:
            response = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= max_retries:
                raise TransientProviderError(
                    f"{label} GET {url} failed after {attempt + 1} attempts: {exc}"
                ) from exc
            delay = _backoff(attempt, backoff_base)
            logger.warning(
                "%s GET %s failed (%s); retry %d/%d in %.1fs",
                label, url, exc, attempt + 1, max_retries, delay,
            )
            await asyncio.sleep(delay)
            attempt += 1
            continue

        if response.status_code in _RETRY_STATUS:
            if attempt < max_retries:
                delay = _retry_after(response) or _backoff(attempt, backoff_base)
                logger.warning(
                    "%s GET %s -> %d; retry %d/%d in %.1fs",
                    label, url, response.status_code, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            # Retries exhausted on a retryable status: surface as transient so
            # the breaker counts it, rather than returning a 5xx/429 the caller
            # would swallow into a silent degrade.
            raise TransientProviderError(
                f"{label} GET {url} -> {response.status_code} after {attempt + 1} attempts"
            )

        return response


def _backoff(attempt: int, base: float) -> float:
    delay = base * (2 ** attempt) + random.uniform(0, base)
    return min(delay, _MAX_BACKOFF)


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from a ``Retry-After`` header, when it's a numeric delay."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), _MAX_BACKOFF)
    except ValueError:
        # HTTP-date form — fall back to exponential backoff.
        return None
