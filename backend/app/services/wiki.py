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
import re
import unicodedata
from collections.abc import Callable
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

# A sport-appropriate qualifier appended to a *player* search so a bare
# name resolves to the athlete's article (and the club term in the query
# biases toward the right person when several share a name).
_PLAYER_SPORT_QUALIFIER: dict[str, str] = {
    "soccer": "footballer",
    "basketball": "basketball player",
    "baseball": "baseball player",
    "hockey": "ice hockey player",
    "football": "American football player",
    "volleyball": "volleyball player",
}

# Page titles that are never a club's main article — skip them so a search
# never lands the "About" box on a rivalry / list / season page.
_BAD_TITLE_MARKERS = ("rivalry", "list of", "season", "(disambiguation)")


@dataclass(frozen=True)
class WikiSummary:
    title: str
    extract: str | None = None   # lead-paragraph prose for the "About" section
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


async def player_photo(
    name: str, *, team_name: str | None = None, sport: str | None = None
) -> str | None:
    """Resolve a player's Wikipedia lead image URL; ``None`` on any miss.

    The fallback that gives soccer players a headshot: ESPN soccer rosters
    carry a photo for only a couple of players, so the rest are looked up
    here.  Searches a club+sport-qualified query (e.g. ``"Cole Palmer
    Chelsea footballer"``) so a common name resolves to the right athlete,
    then returns that page's lead image (``originalimage``/``thumbnail``).

    Precision over recall: a result whose title doesn't clearly match the
    player's name is discarded (:func:`_title_matches_name`) — a wrong face
    is worse than the initials chip the UI falls back to.  Cached per
    ``(name, team, sport)`` with the long ``wiki_cache_minutes`` TTL —
    misses included, so a photoless squad can't re-flood the free service on
    every roster refresh — and never raises.
    """
    settings = get_settings()
    if not getattr(settings, "wiki_enabled", True):
        return None
    clean = (name or "").strip()
    if not clean:
        return None

    lang = getattr(settings, "wiki_lang", "en")
    key = (
        f"wikiphoto:{lang}:{clean.casefold()}|"
        f"{(team_name or '').strip().casefold()}|{(sport or '').casefold()}"
    )
    cached = await cache.cache_get_json(key)
    if isinstance(cached, dict) and "image_url" in cached:
        url = cached.get("image_url")
        return url if isinstance(url, str) and url else None

    title = await _resolve_player_title(clean, team_name, sport, lang)
    image_url: str | None = None
    if title is not None:
        summary = await _fetch_summary(title, lang)
        if summary is not None:
            image_url = summary.image_url

    ttl = getattr(settings, "wiki_cache_minutes", 10080) * 60
    await cache.cache_set_json(key, {"image_url": image_url}, ttl)
    return image_url


async def _resolve_title(name: str, sport: str | None, lang: str) -> str | None:
    """Best article title for ``name`` via the search API, or ``None``."""
    qualifier = _SPORT_QUALIFIER.get((sport or "").casefold(), "")
    query = f"{name} {qualifier}".strip()
    return await _search_title(query, lang)


async def _search_title(
    query: str, lang: str, accept: Callable[[str], bool] | None = None
) -> str | None:
    """Best search-result title for ``query``, or ``None``.

    Skips the junk titles in :data:`_BAD_TITLE_MARKERS`; an optional
    ``accept`` predicate adds a further per-title guard (e.g. the player
    name-match below), so a returned title has passed both filters.
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
        if accept is not None and not accept(title):
            continue
        return title
    return None


async def _resolve_player_title(
    name: str, team_name: str | None, sport: str | None, lang: str
) -> str | None:
    """Best article title for a *player*, name-matched for precision.

    The query carries the club and a player qualifier ("Cole Palmer Chelsea
    footballer") so the search ranks the right athlete first; the
    name-match guard then rejects any result that fell through to a club /
    competition page because the player has no article of their own.
    """
    qualifier = _PLAYER_SPORT_QUALIFIER.get((sport or "").casefold(), "")
    query = " ".join(
        part.strip() for part in (name, team_name, qualifier) if part and part.strip()
    )
    if not query:
        return None
    return await _search_title(query, lang, accept=lambda t: _title_matches_name(t, name))


def _title_matches_name(title: str, name: str) -> bool:
    """True when an article title clearly belongs to ``name``.

    A person's article title is their name, sometimes with a parenthetical
    qualifier ("Cole Palmer (footballer, born 2002)"), so every significant
    name token must appear among the title's tokens — accent-insensitively,
    since rosters and Wikipedia spell diacritics inconsistently.  This
    rejects a search that landed on a club / list / disambiguation page.
    """
    base = re.sub(r"\(.*?\)", " ", title)  # drop "(footballer, born 2002)"
    title_tokens = set(_normalize_tokens(base))
    if not title_tokens:
        return False
    name_tokens = [t for t in _normalize_tokens(name) if len(t) >= 3]
    if not name_tokens:
        # A very short / initials-only name — match on whatever tokens exist.
        name_tokens = _normalize_tokens(name)
    if not name_tokens:
        return False
    return all(token in title_tokens for token in name_tokens)


def _normalize_tokens(text: str) -> list[str]:
    """Lowercased, accent-stripped, alphanumeric tokens of ``text``."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return [tok for tok in re.split(r"[^a-z0-9]+", stripped.casefold()) if tok]


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
