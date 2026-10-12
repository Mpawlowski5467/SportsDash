"""Free, keyless club background via the Wikipedia REST API.

A fallback enrichment source for a team's "About" section: a clean prose
summary (``extract``) and a lead image, used when the primary source
(TheSportsDB ``strDescriptionEN``) has nothing for a club.  Like every
external-HTTP helper here it is defensive end to end — network errors,
timeouts, bad statuses, disambiguation/missing pages, and unparseable
bodies are logged and collapse to ``None``; it never raises, so an
un-fetchable club can't break the team-info refresh job.

Two endpoints, both keyless on the public Wikipedia API:

* ``w/api.php?action=query&list=search`` — the articles that might be the
  club's (sport-qualified, so the club's own article is among the hits and
  not only the town it is named after);
* ``api/rest_v1/page/summary/{title}`` — one page's ``description`` (what
  it is, in a phrase), ``extract`` (lead paragraph) and
  ``thumbnail``/``originalimage``.

The search only PROPOSES articles; it never picks one.  Its ranking is full
text, so the qualifier words pull in other teams of the same sport and city
(a search for one club has returned a defunct neighbour first), and the
order changes from one request to the next.  An article is used only when
it is verifiably the team's own:

* its title is the team's name — the same words, give or take case,
  accents and punctuation — optionally followed by a "(…)" that names the
  sport, see :func:`_is_title_for`;
* it is a real article, not a disambiguation page;
* it says it is that sport's, so a town, a person or another sport's team
  filed under the very same title is not mistaken for the club, see
  :func:`_names_sport`.

Anything short of that is a miss: no description is better than another
club's.  In particular a title is not stretched to fit a shorter name.
"Lyon" is not "FC Lyon" — a different club from the one that name means —
so "Chelsea" is not "Chelsea F.C." either: the name alone cannot tell
those two cases apart.

Results are cached in Redis (best-effort) with a long TTL, since a club's
history changes rarely.  A small semaphore bounds concurrent upstream calls
so a fresh many-team refresh stays a good citizen of the free service.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass

import httpx

from app import useragent
from app.config import get_settings
from app.services import cache

logger = logging.getLogger(__name__)

_HEADERS = useragent.headers("self-hosted; club info enrichment")
_TIMEOUT = httpx.Timeout(15.0)
_MAX_INFLIGHT = asyncio.Semaphore(4)

# Bump whenever the rule for accepting an article changes.  It versions the
# Redis key, so a summary resolved under an older rule is never read back,
# and the team-info job compares it with the version its stored descriptions
# were resolved under and drops the ones that predate it (see
# ``scheduler.refresh._clear_unverified_descriptions``).
MATCH_VERSION = 2


@dataclass(frozen=True)
class _SportTerms:
    """How one sport appears in a search, in an article title and in an article."""

    # Appended to the search query so the club's article is among the hits.
    query: str
    # The "(…)" disambiguators a title may carry and still be the team's own
    # (folded, see ``_fold``).  A year range or a place is not one of them:
    # those mark a different club that shared the name.
    suffixes: frozenset[str]
    # One of these must appear in what the article says it is (English, like
    # the query: another language edition's articles seldom pass).  Spelled
    # out, never a league's initials: this is a substring test.
    evidence: tuple[str, ...]


# Keyed by ``Sport`` value; every sport has a row (a test holds that), since
# a sport without one has nothing to check an article against and gets no
# description at all.  The individual sports search by the bare name — a
# "team" there is one athlete.
_SPORT_TERMS: dict[str, _SportTerms] = {
    "soccer": _SportTerms(
        "football club",
        frozenset({"football", "football club", "association football", "soccer", "soccer club"}),
        ("football club", "association football", "soccer"),
    ),
    "basketball": _SportTerms(
        "basketball team",
        frozenset({"basketball", "basketball team", "basketball club"}),
        ("basketball",),
    ),
    "baseball": _SportTerms(
        "baseball team", frozenset({"baseball", "baseball team"}), ("baseball",)
    ),
    "hockey": _SportTerms(
        "ice hockey team", frozenset({"ice hockey", "ice hockey team", "hockey"}), ("hockey",)
    ),
    "football": _SportTerms(
        "American football team",
        frozenset({"american football", "american football team"}),
        ("american football", "national football league", "canadian football", "gridiron"),
    ),
    "volleyball": _SportTerms(
        "volleyball team", frozenset({"volleyball", "volleyball club"}), ("volleyball",)
    ),
    "tennis": _SportTerms("", frozenset({"tennis", "tennis player"}), ("tennis",)),
    "mma": _SportTerms("", frozenset({"fighter", "mixed martial artist"}), ("mixed martial",)),
    "golf": _SportTerms("", frozenset({"golfer"}), ("golf",)),
    "racing": _SportTerms("", frozenset({"racing driver"}), ("racing", "racer", "motorsport")),
}

# How much of an article's lead says what the article IS: the opening "… are
# a professional ice hockey team based in …" clause, not the sentences after
# it — a town's lead goes on to mention the club that plays there.
_LEAD_OPENING_CHARS = 200

_TITLE_SUFFIX = re.compile(r"^(?P<base>.+?)\s*\((?P<suffix>[^()]*)\)$")
_WORD_BREAKS = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class WikiSummary:
    title: str
    extract: str | None = None  # lead-paragraph prose for the "About" section
    image_url: str | None = None  # lead image (a crest or photo), when present


async def team_summary(name: str, *, sport: str | None = None) -> WikiSummary | None:
    """The Wikipedia summary of the team called ``name``; ``None`` on any miss.

    Searches (sport-qualified) for candidate articles and returns the first
    that is verifiably this team's — see the module docstring for what that
    takes.  Cached per ``(name, sport)`` and never raises: disabled service,
    an unrecognised sport, no matching article, or any upstream/parse
    failure all return ``None`` so the caller falls back to whatever it
    already has.
    """
    settings = get_settings()
    if not getattr(settings, "wiki_enabled", True):
        return None
    clean = (name or "").strip()
    terms = _SPORT_TERMS.get((sport or "").casefold())
    if not clean or terms is None:
        return None

    lang = getattr(settings, "wiki_lang", "en")
    key = f"wiki:v{MATCH_VERSION}:{lang}:{clean.casefold()}|{(sport or '').casefold()}"
    cached = await cache.cache_get_json(key)
    if isinstance(cached, dict):
        restored = _from_cache(cached)
        if restored is not None:
            return restored

    titles = await _search_titles(f"{clean} {terms.query}".strip(), lang)
    for title in _candidates(titles, clean, terms):
        summary = await _fetch_summary(title, lang, clean, terms)
        if summary is not None:
            ttl = getattr(settings, "wiki_cache_minutes", 10080) * 60
            await cache.cache_set_json(key, _to_cache(summary), ttl)
            return summary
    if titles:
        logger.debug("wiki: none of %d search hit(s) is %r's article", len(titles), clean)
    return None


def _fold(text: str) -> str:
    """``text`` without case, accents or punctuation, for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    # Dots and apostrophes close up ("St." is "St", "F.C." is "FC"); anything
    # else that is not a letter or digit separates words.
    plain = plain.replace("&", " and ").replace(".", "").replace("'", "").replace("\u2019", "")
    return " ".join(_WORD_BREAKS.sub(" ", plain).split())


def _split_title(title: str) -> tuple[str, str | None]:
    """An article title as (its name, its trailing "(…)" disambiguator if any)."""
    match = _TITLE_SUFFIX.match(title)
    return (match["base"], match["suffix"]) if match else (title, None)


def _is_title_for(title: str, name: str, terms: _SportTerms) -> bool:
    """Whether an article ``title`` is the team ``name``'s own.

    The title has to be the name: the same words in the same order once both
    are folded.  The one thing it may add is a trailing "(…)" naming the
    sport — "Texas Rangers (baseball)".  Any other parenthetical
    ("(1972–96)", "(Montevideo)", "(original)") marks a different club that
    shared the name, and is no match.
    """
    base, suffix = _split_title(title)
    if suffix is not None and _fold(suffix) not in terms.suffixes:
        return False
    wanted = _fold(name)
    return bool(wanted) and _fold(base) == wanted


def _candidates(titles: list[str], name: str, terms: _SportTerms) -> list[str]:
    """The search hits that could be ``name``'s article, surest first.

    A title that spells the sport out does so because the bare title belongs
    to something else, so it goes ahead of the bare one; search order is
    kept otherwise.
    """
    matching = dict.fromkeys(title for title in titles if _is_title_for(title, name, terms))
    return sorted(matching, key=lambda title: _split_title(title)[1] is None)


async def _search_titles(query: str, lang: str) -> list[str]:
    """The article titles the search API returns for ``query``, in its order."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": "10",
        "format": "json",
    }
    payload = await _get_json(f"https://{lang}.wikipedia.org/w/api.php", params)
    if not isinstance(payload, dict):
        return []
    query_block = payload.get("query")
    results = query_block.get("search") if isinstance(query_block, dict) else None
    if not isinstance(results, list):
        return []
    titles: list[str] = []
    for result in results:
        title = result.get("title") if isinstance(result, dict) else None
        if isinstance(title, str) and title:
            titles.append(title)
    return titles


async def _fetch_summary(
    title: str, lang: str, name: str, terms: _SportTerms
) -> WikiSummary | None:
    """REST summary (extract + image) of ``title`` if it is ``name``'s article."""
    encoded = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    payload = await _get_json(url, None)
    if not isinstance(payload, dict):
        return None
    # Only accept a real article; a disambiguation page has no usable history.
    if payload.get("type") not in (None, "standard"):
        return None
    # The summary follows redirects, so the page that answers is held to the
    # same rule as the title that was asked for.
    resolved_title = payload.get("title")
    if not isinstance(resolved_title, str) or not resolved_title:
        resolved_title = title
    if not _is_title_for(resolved_title, name, terms):
        return None
    extract = payload.get("extract")
    extract = extract.strip() if isinstance(extract, str) and extract.strip() else None
    if not _names_sport(payload.get("description"), extract, terms):
        return None
    image_url = _image_from(payload)
    if extract is None and image_url is None:
        return None
    return WikiSummary(title=resolved_title, extract=extract, image_url=image_url)


def _names_sport(description: object, extract: str | None, terms: _SportTerms) -> bool:
    """Whether the page says it is something of the team's sport.

    The title rule cannot tell a club from the town it is named after, a
    player from a namesake, or one sport's team from another's: all of them
    can be filed under the very same title.  What the page says it is can —
    its short description ("Major League Baseball franchise in …", "Suburb
    of West London, England") or the opening of its lead.
    """
    described = description if isinstance(description, str) else ""
    text = f"{described} {(extract or '')[:_LEAD_OPENING_CHARS]}".casefold()
    return any(term in text for term in terms.evidence)


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
