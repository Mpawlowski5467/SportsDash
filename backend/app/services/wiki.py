"""Free, keyless club background via the Wikipedia REST API.

A fallback enrichment source for a team's "About" section: a clean prose
summary (``extract``) and a lead image, used when the primary source
(TheSportsDB ``strDescriptionEN``) has nothing for a club.  Like every
external-HTTP helper here it is defensive end to end — network errors,
timeouts, bad statuses, disambiguation/missing pages, and unparseable
bodies are logged and collapse to ``None``; it never raises, so an
un-fetchable club can't break the team-info refresh job.

Two endpoints, both keyless on the public Wikipedia API:

* ``w/api.php?action=query&list=search`` — resolve a club name to the best
  article title (sport-qualified so "Chelsea" finds the football club, not
  the London district);
* ``api/rest_v1/page/summary/{title}`` — the resolved page's ``extract``
  (lead paragraph) and ``thumbnail``/``originalimage``.

Results are cached in Redis (best-effort) with a long TTL, since a club's
history changes rarely.  A small semaphore bounds concurrent upstream calls
so a fresh many-team refresh stays a good citizen of the free service.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.services import cache

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "SportsDash/1.0 (self-hosted; club info enrichment)"}
_TIMEOUT = httpx.Timeout(15.0)
_MAX_INFLIGHT = asyncio.Semaphore(4)

# A sport-appropriate qualifier appended to the search query so a bare club
# name resolves to the club's article rather than a place / disambiguation.
_SPORT_QUALIFIER: dict[str, str] = {
    "soccer": "football club",
    "basketball": "basketball team",
    "baseball": "baseball team",
    "hockey": "ice hockey team",
    "football": "American football team",
    "volleyball": "volleyball team",
}

# Page titles that are never a club's main article — skip them so a search
# never lands the "About" box on a rivalry / list / season page.
_BAD_TITLE_MARKERS = ("rivalry", "list of", "season", "(disambiguation)")


@dataclass(frozen=True)
class WikiSummary:
    title: str
    extract: str | None = None  # lead-paragraph prose for the "About" section
    image_url: str | None = None  # lead image (a crest or photo), when present


async def team_summary(name: str, *, sport: str | None = None) -> WikiSummary | None:
    """Resolve a club name to its Wikipedia summary; ``None`` on any miss.

    Searches for the best article title (sport-qualified), then fetches that
    page's REST summary.  Cached per ``(name, sport)`` and never raises:
    disabled service, no search hit, a non-"standard" page, or any
    upstream/parse failure all return ``None`` so the caller falls back to
    whatever it already has.
    """
    settings = get_settings()
    if not getattr(settings, "wiki_enabled", True):
        return None
    clean = (name or "").strip()
    if not clean:
        return None

    lang = getattr(settings, "wiki_lang", "en")
    key = f"wiki:{lang}:{clean.casefold()}|{(sport or '').casefold()}"
    cached = await cache.cache_get_json(key)
    if isinstance(cached, dict):
        restored = _from_cache(cached)
        if restored is not None:
            return restored

    title = await _resolve_title(clean, sport, lang)
    if title is None:
        return None
    summary = await _fetch_summary(title, lang)
    if summary is not None:
        ttl = getattr(settings, "wiki_cache_minutes", 10080) * 60
        await cache.cache_set_json(key, _to_cache(summary), ttl)
    return summary


async def _resolve_title(name: str, sport: str | None, lang: str) -> str | None:
    """Best article title for ``name`` via the search API, or ``None``."""
    qualifier = _SPORT_QUALIFIER.get((sport or "").casefold(), "")
    query = f"{name} {qualifier}".strip()
    return await _search_title(query, lang)


async def _search_title(query: str, lang: str) -> str | None:
    """Best search-result title for ``query``, or ``None``.

    Skips the junk titles in :data:`_BAD_TITLE_MARKERS`.
    """
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": "5",
        "format": "json",
    }
    payload = await _get_json(f"https://{lang}.wikipedia.org/w/api.php", params)
    if not isinstance(payload, dict):
        return None
    results = (payload.get("query") or {}).get("search")
    if not isinstance(results, list):
        return None
    for result in results:
        if not isinstance(result, dict):
            continue
        title = result.get("title")
        if not isinstance(title, str) or not title:
            continue
        lowered = title.casefold()
        if any(marker in lowered for marker in _BAD_TITLE_MARKERS):
            continue
        return title
    return None


async def _fetch_summary(title: str, lang: str) -> WikiSummary | None:
    """REST summary (extract + image) for an exact page title, or ``None``."""
    encoded = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    payload = await _get_json(url, None)
    if not isinstance(payload, dict):
        return None
    # Only accept a real article; a disambiguation page has no usable history.
    if payload.get("type") not in (None, "standard"):
        return None
    extract = payload.get("extract")
    extract = extract.strip() if isinstance(extract, str) and extract.strip() else None
    image_url = _image_from(payload)
    if extract is None and image_url is None:
        return None
    resolved_title = payload.get("title")
    return WikiSummary(
        title=resolved_title if isinstance(resolved_title, str) and resolved_title else title,
        extract=extract,
        image_url=image_url,
    )


def _image_from(payload: dict) -> str | None:
    for field in ("originalimage", "thumbnail"):
        block = payload.get(field)
        if isinstance(block, dict):
            source = block.get("source")
            if isinstance(source, str) and source:
                return source
    return None


async def _get_json(url: str, params: dict[str, str] | None) -> object | None:
    """Fetch + JSON-decode one URL; ``None`` on any failure (never raises)."""
    try:
        async with _MAX_INFLIGHT:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
            ) as client:
                response = await client.get(url, params=params)
            response.raise_for_status()
            if not response.content or not response.text.strip():
                return None
            return response.json()
    except Exception:
        logger.warning("wiki: lookup failed (%s) — returning None", url, exc_info=True)
        return None


def _to_cache(summary: WikiSummary) -> dict[str, str | None]:
    return {
        "title": summary.title,
        "extract": summary.extract,
        "image_url": summary.image_url,
    }


def _from_cache(data: dict) -> WikiSummary | None:
    title = data.get("title")
    if not isinstance(title, str) or not title:
        return None
    extract = data.get("extract")
    image_url = data.get("image_url")
    return WikiSummary(
        title=title,
        extract=extract if isinstance(extract, str) and extract else None,
        image_url=image_url if isinstance(image_url, str) and image_url else None,
    )
