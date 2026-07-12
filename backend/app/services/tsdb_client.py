"""Shared access layer for TheSportsDB's keyless free tier.

Owns the base URL, the sport-label map, the string/number coercion
helpers, one long-lived HTTP client, and — most importantly — a single
process-wide pacing gate.  Four modules hit the same aggressively
rate-limited shared key (the provider, the setup catalog, stadium
enrichment, and player photos); before this module each invented its own
politeness scheme, and the ones with none contributed to real 429 storms
(the negative-cache poisoning bug).  Every TSDB request in the process
now flows through :func:`acquire_slot`, so total request rate stays a
polite trickle no matter how many callers are active.

Error semantics stay with the callers: :func:`paced_get` raises exactly
like ``http_util.get_with_retry`` (``TransientProviderError`` on
exhausted retries, non-retryable responses returned as-is), and each
caller keeps wrapping that in its own never-raise / fail-fast contract.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.providers.http_util import get_with_retry

logger = logging.getLogger(__name__)

TSDB_BASE_URL = "https://www.thesportsdb.com/api/v1/json/3/"
HEADERS = {"User-Agent": "SportsDash/1.0 (self-hosted)"}
TIMEOUT = httpx.Timeout(15.0)

# Map the app's ``Sport`` value onto TheSportsDB's ``strSport`` label so a
# name search can prefer the right code (several sports share a club name —
# "Arsenal" is a dozen soccer clubs, "Chelsea" has U21/women's/youth sides).
# Unknown sports simply skip the filter.
SPORT_LABEL: dict[str, str] = {
    "basketball": "Basketball",
    "baseball": "Baseball",
    "soccer": "Soccer",
    "hockey": "Ice Hockey",
    "football": "American Football",
    "tennis": "Tennis",
    "mma": "Fighting",
    "golf": "Golf",
    "volleyball": "Volleyball",
}


def clean_str(value: Any) -> str | None:
    """A non-empty trimmed string, or ``None``.

    TheSportsDB represents missing values as ``null``, ``""``, or the
    literal string ``"null"`` — all collapse to ``None``.
    """
    if isinstance(value, str):
        text = value.strip()
        if text and text.lower() != "null":
            return text
    return None


def coerce_int(value: Any) -> int | None:
    """Best-effort int from TheSportsDB's string-encoded numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Shared client + process-wide pacing
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None

# Serialize requests and space them: one in flight at a time, with a gap
# between starts (~3 req/s worst case) — the politest scheme any caller
# previously used, now applied to all of them.
_gate = asyncio.Lock()
_MIN_SPACING_SECONDS = 0.34
_next_allowed: float = 0.0  # event-loop clock timestamp


def get_client() -> httpx.AsyncClient:
    """The shared long-lived TSDB client (lazy; closed via close_client)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=TSDB_BASE_URL,
            timeout=TIMEOUT,
            headers=HEADERS,
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    """Close the shared client (app shutdown)."""
    global _client
    client = _client
    _client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.debug("tsdb_client: close failed", exc_info=True)


async def acquire_slot() -> None:
    """Wait for the next process-wide TSDB request slot.

    Callers that manage their own HTTP client (the provider) call this
    directly before each request; everything else goes through
    :func:`paced_get`, which calls it internally.
    """
    global _next_allowed
    async with _gate:
        loop = asyncio.get_running_loop()
        wait = _next_allowed - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _next_allowed = loop.time() + _MIN_SPACING_SECONDS


async def paced_get(
    endpoint: str,
    params: dict[str, str] | None = None,
    *,
    max_retries: int = 2,
    label: str = "tsdb",
) -> httpx.Response:
    """GET ``endpoint`` on the shared client after taking a pacing slot.

    Raises exactly like :func:`app.providers.http_util.get_with_retry`:
    ``TransientProviderError`` when retries are exhausted on a 429/5xx or
    transport error; non-retryable responses (e.g. 404) are returned
    unchanged for the caller to interpret.  ``max_retries=0`` gives the
    fail-fast behavior batch jobs want (a 429 surfaces immediately instead
    of sleeping out its Retry-After mid-job).
    """
    await acquire_slot()
    return await get_with_retry(
        get_client(),
        endpoint,
        params=params,
        max_retries=max_retries,
        backoff_base=get_settings().provider_backoff_base,
        label=label,
    )
