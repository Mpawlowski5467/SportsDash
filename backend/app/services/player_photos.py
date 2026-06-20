"""Keyless player headshots via TheSportsDB ``searchplayers.php``.

The fallback that gives soccer players a face: ESPN soccer rosters carry a
headshot for only a couple of players, so the rest fall back to an initials
chip.  TheSportsDB is *purpose-built* for player images — a cutout / thumb /
render per player — and returns the player's club, so a common name resolves
to the right person.  Keyless (TheSportsDB is already an integrated
provider), Redis-cached, and never-raises.

Built to be a good citizen of the free tier under bulk roster backfill:

* one request per player (not a search→summary two-step);
* requests are SERIALIZED and spaced (a polite trickle) so a squad's backfill
  doesn't burst-hammer the shared free key;
* FAIL-FAST on a ``429``: TheSportsDB answers an over-limit request with a
  30-second ``Retry-After``, so this best-effort garnish does NOT wait it out
  (that would block the daily roster job for minutes) — it skips the player
  and tries again on the next refresh;
* TRANSIENT failures (429/5xx/timeout) are **not** cached — so a rate-limited
  player is retried next refresh and a squad fills in gradually over several
  daily runs, rather than a brief 429 storm poisoning the cache;
* successful hits cache for a month and genuine "no such player" misses for a
  few days (a cutout rarely changes), so steady state makes ~no requests.

REALITY: the free tier rate-limits bulk per-player lookups, so coverage fills
in GRADUALLY (a handful per refresh) rather than all at once.  For instant
full coverage a keyed provider (e.g. API-Football) would be required.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings
from app.providers.http_util import TransientProviderError, get_with_retry
from app.services import cache

logger = logging.getLogger(__name__)

_BASE = "https://www.thesportsdb.com/api/v1/json/3/"
_HEADERS = {"User-Agent": "SportsDash/1.0 (self-hosted)"}
_TIMEOUT = httpx.Timeout(15.0)
# Serialize lookups (a roster backfill walks many players) and space them, so
# the shared free key sees a polite trickle, not a burst.
_MAX_INFLIGHT = asyncio.Semaphore(1)
# Seconds to wait after each request before the next is allowed — a gentle
# ~3 req/s cap on top of serialization.
_REQUEST_SPACING = 0.34

_SUCCESS_TTL = 60 * 60 * 24 * 30   # 30 days — a player's cutout rarely changes
_MISS_TTL = 60 * 60 * 24 * 3       # 3 days — re-check in case TheSportsDB adds them

# App ``Sport`` value -> TheSportsDB ``strSport`` label, to prefer the right
# code when a name is shared across sports.  Unknown sports skip the filter.
_SPORT_LABEL: dict[str, str] = {
    "basketball": "Basketball",
    "baseball": "Baseball",
    "soccer": "Soccer",
    "hockey": "Ice Hockey",
    "football": "American Football",
    "volleyball": "Volleyball",
}


async def lookup_photo(
    name: str, *, team_name: str | None = None, sport: str | None = None
) -> str | None:
    """Resolve a player's headshot URL by name; ``None`` on any miss.

    Searches TheSportsDB for ``name``, prefers the candidate whose club /
    sport matches (so a shared name resolves to the right player), and
    returns its cutout (then thumb, then render).  Cached per ``(name,
    team)``; a transient upstream failure returns ``None`` *without* caching
    so it retries next refresh, while a hit or a genuine miss is cached.
    Never raises.
    """
    clean = (name or "").strip()
    if not clean:
        return None

    key = f"tsdbphoto:{clean.casefold()}|{(team_name or '').strip().casefold()}"
    cached = await cache.cache_get_json(key)
    if isinstance(cached, dict) and "url" in cached:
        url = cached.get("url")
        return url if isinstance(url, str) and url else None

    try:
        payload = await _search(clean)
    except TransientProviderError:
        # Rate-limited / upstream blip: do NOT cache, so the player is
        # retried on the next roster refresh instead of being denied a photo
        # until the cache TTL expires.
        return None
    except Exception:
        logger.warning(
            "player_photos: lookup failed for %r — returning None", clean, exc_info=True
        )
        return None

    url = _pick_photo(payload, team_name, sport)
    await cache.cache_set_json(key, {"url": url}, _SUCCESS_TTL if url else _MISS_TTL)
    return url


async def _search(name: str) -> object | None:
    """Fetch ``searchplayers.php`` for ``name``; ``None`` on a genuine empty.

    Raises :class:`TransientProviderError` (from ``get_with_retry``) on an
    exhausted 429/5xx/timeout so the caller can skip caching; a 404 or empty
    body is a genuine "no such player" and returns ``None``.
    """
    settings = get_settings()
    async with _MAX_INFLIGHT:
        async with httpx.AsyncClient(
            base_url=_BASE,
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            # max_retries=0 → FAIL-FAST: a 429 raises TransientProviderError
            # immediately rather than sleeping out its 30s Retry-After (which
            # would stall the daily roster job); the player retries next run.
            response = await get_with_retry(
                client,
                "searchplayers.php",
                params={"p": name},
                max_retries=0,
                backoff_base=settings.provider_backoff_base,
                label="tsdb-player",
            )
        # Space the next caller (only after a real response — a fail-fast
        # transient raised above and skipped this).
        await asyncio.sleep(_REQUEST_SPACING)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    if not response.content or not response.text.strip():
        return None
    return response.json()


def _pick_photo(
    payload: object, team_name: str | None, sport: str | None
) -> str | None:
    """Choose the best candidate's image URL from a ``searchplayers`` payload.

    Filters to the right sport when known, then prefers the candidate whose
    club matches ``team_name`` (disambiguating shared names); falls back to
    the first candidate.  Returns its cutout / thumb / render, or ``None``.
    """
    players = payload.get("player") if isinstance(payload, dict) else None
    if not isinstance(players, list) or not players:
        return None
    candidates = [p for p in players if isinstance(p, dict)]
    if not candidates:
        return None

    label = _SPORT_LABEL.get((sport or "").casefold())
    if label is not None:
        sport_matches = [
            p for p in candidates if _clean(p.get("strSport")) == label
        ]
        if sport_matches:
            candidates = sport_matches

    if team_name:
        wanted = team_name.strip().casefold()
        team_matches = [
            p
            for p in candidates
            if _clean(p.get("strTeam")) and _team_matches(_clean(p["strTeam"]), wanted)
        ]
        if team_matches:
            candidates = team_matches

    chosen = candidates[0]
    return (
        _clean(chosen.get("strCutout"))
        or _clean(chosen.get("strThumb"))
        or _clean(chosen.get("strRender"))
    )


def _team_matches(candidate_team: str, wanted: str) -> bool:
    """Loose club match: either name contains the other (handles "Chelsea" vs
    "Chelsea FC", "Paris Saint-Germain" vs "Paris SG")."""
    a = candidate_team.casefold()
    return wanted in a or a in wanted


def _clean(value: object) -> str | None:
    """A non-empty trimmed string, or ``None`` (TheSportsDB uses null/"")."""
    if isinstance(value, str):
        text = value.strip()
        if text and text.lower() != "null":
            return text
    return None
