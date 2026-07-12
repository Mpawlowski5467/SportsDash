"""News ingestion for followed teams, merged from three sources.

Per team a refresh gathers (1) provider news via
``registry.get_provider(league.provider).get_news(...)``, (2) the
team's configured ``rss_feeds``, and (3) an auto-generated Google News
search feed built from ``settings.news_locale``.  Items are deduped by
id (a stable hash of the url) before storage, so the same article
arriving from several sources is inserted once.

Feeds are fetched with ``httpx`` (explicit timeout — ``feedparser``'s
own urllib fetching has none and a single hung feed would wedge the
refresh job forever) and only *parsed* by ``feedparser``, in
``asyncio.to_thread`` since parsing is synchronous.  Every source is
fault-isolated: a broken provider or feed (network error, bozo XML with
no entries, missing fields) is logged and skipped — one bad source
never kills a refresh.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import html
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlparse

import feedparser
import httpx

from app.config import get_settings
from app.db import session_scope
from app.models import convert, domain
from app.models.orm import LeagueORM, TeamORM
from app.providers import registry
from app.services import repository
from app.timeutil import UTC

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SUMMARY_MAX_CHARS = 300
_FEED_TIMEOUT = httpx.Timeout(15.0)
_FEED_HEADERS = {"User-Agent": "SportsDash/1.0 (+rss reader)"}
_GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"
_DEFAULT_LOCALE = ("en", "US")


def _strip_html(text: str) -> str:
    """Cheap HTML-to-text: drop tags, decode entities, collapse whitespace.

    Tags are stripped both before AND after entity decoding: feeds ship
    plain markup, entity-encoded markup, or both, and a summary should
    end up with neither.
    """
    without_tags = _TAG_RE.sub(" ", text)
    decoded = html.unescape(without_tags)
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", decoded)).strip()


def _trim_summary(text: str) -> str:
    if len(text) <= _SUMMARY_MAX_CHARS:
        return text
    cut = text[:_SUMMARY_MAX_CHARS]
    # Trim back to the last word boundary so we never split a word.
    head, _, _ = cut.rpartition(" ")
    return (head or cut).rstrip() + "…"


def _published_at(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed is not None:
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _source_name(parsed_feed: Any, feed_url: str) -> str:
    title = getattr(getattr(parsed_feed, "feed", None), "title", None)
    if title:
        return str(title).strip()
    return urlparse(feed_url).hostname or feed_url


def _entry_source(entry: Any, default: str) -> str:
    """Per-entry source name; Google News carries the real outlet here
    (its entry links are redirect URLs, so the feed title is useless)."""
    source = getattr(entry, "source", None)
    title = getattr(source, "title", None) if source is not None else None
    if title:
        return str(title).strip()
    return default


def _image_url(entry: Any) -> str | None:
    """Best-effort thumbnail: media RSS extensions, then image enclosures."""
    for attr in ("media_thumbnail", "media_content"):
        for media in getattr(entry, attr, None) or ():
            url = str(media.get("url") or "").strip()
            if url:
                return url
    for enclosure in getattr(entry, "enclosures", None) or ():
        if not str(enclosure.get("type") or "").startswith("image/"):
            continue
        url = str(enclosure.get("href") or enclosure.get("url") or "").strip()
        if url:
            return url
    return None


def _entry_to_news_item(entry: Any, *, team_id: str, source: str) -> domain.NewsItem | None:
    title = (getattr(entry, "title", None) or "").strip()
    url = (getattr(entry, "link", None) or "").strip()
    if not title or not url:
        return None

    summary_raw = getattr(entry, "summary", None) or getattr(entry, "description", None)
    summary: str | None = None
    if summary_raw:
        stripped = _strip_html(str(summary_raw))
        summary = _trim_summary(stripped) if stripped else None

    return domain.NewsItem(
        id=hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        team_id=team_id,
        title=title,
        url=url,
        source=_entry_source(entry, source),
        published_at=_published_at(entry),
        summary=summary,
        image_url=_image_url(entry),
    )


def _parse_locale(locale: str) -> tuple[str, str]:
    """Split a ``"lang-COUNTRY"`` locale; falls back to en-US when malformed."""
    lang, _, country = locale.partition("-")
    lang = lang.strip().lower()
    country = country.strip().upper()
    if not lang or not country:
        logger.warning("news: invalid news_locale %r; falling back to en-US", locale)
        return _DEFAULT_LOCALE
    return lang, country


def build_google_news_url(team_name: str, locale: str, context: str | None = None) -> str:
    """Auto-generated Google News search feed URL for a team.

    The query quotes the team name for exact-phrase matching, plus an
    optional unquoted context term (the league name) — generic team
    names like "Athletics" otherwise match unrelated college athletics
    departments. ``hl``/``gl``/``ceid`` derive from the
    ``"lang-COUNTRY"`` locale (e.g. ``pl-PL`` → ``hl=pl-PL&gl=PL&ceid=PL:pl``).
    """
    lang, country = _parse_locale(locale)
    terms = f'"{team_name}" {context}' if context else f'"{team_name}"'
    query = quote(terms)
    return f"{_GOOGLE_NEWS_BASE}?q={query}&hl={lang}-{country}&gl={country}&ceid={country}:{lang}"


async def _feed_items(
    client: httpx.AsyncClient, feed_url: str, *, team_id: str
) -> list[domain.NewsItem]:
    """Fetch and parse one feed; raises on fetch failure (callers wrap)."""
    response = await client.get(feed_url)
    response.raise_for_status()
    parsed = await asyncio.to_thread(feedparser.parse, response.content)

    entries = list(getattr(parsed, "entries", None) or [])
    if getattr(parsed, "bozo", False) and not entries:
        logger.warning(
            "news: feed %s for %s is malformed and has no entries (%s); skipping",
            feed_url,
            team_id,
            getattr(parsed, "bozo_exception", "unknown error"),
        )
        return []

    source = _source_name(parsed, feed_url)
    items: list[domain.NewsItem] = []
    for entry in entries:
        item = _entry_to_news_item(entry, team_id=team_id, source=source)
        if item is None:
            logger.debug("news: skipping entry without title/link in %s", feed_url)
            continue
        items.append(item)
    return items


async def fetch_team_news(team: TeamORM) -> list[domain.NewsItem]:
    """Parse every feed in ``team.rss_feeds``; per-feed errors are skipped."""
    items: list[domain.NewsItem] = []
    feed_urls: list[str] = list(team.rss_feeds or [])
    if not feed_urls:
        return items
    async with httpx.AsyncClient(
        timeout=_FEED_TIMEOUT, headers=_FEED_HEADERS, follow_redirects=True
    ) as client:
        for feed_url in feed_urls:
            try:
                items.extend(await _feed_items(client, feed_url, team_id=team.id))
            except Exception:
                logger.exception("news: failed to fetch/parse feed %s for %s", feed_url, team.id)
                continue
    return items


async def fetch_google_news(team: TeamORM, league_name: str | None = None) -> list[domain.NewsItem]:
    """Fetch the auto-generated Google News search feed for a team."""
    feed_url = build_google_news_url(team.name, get_settings().news_locale, context=league_name)
    async with httpx.AsyncClient(
        timeout=_FEED_TIMEOUT, headers=_FEED_HEADERS, follow_redirects=True
    ) as client:
        return await _feed_items(client, feed_url, team_id=team.id)


# ---------------------------------------------------------------------------
# ORM -> domain helpers (providers take domain objects) — shared mappers.
# ---------------------------------------------------------------------------

_league_from_row = convert.league_from_row
_team_from_row = convert.team_from_row


def _leagues_by_id(rows: list[LeagueORM]) -> dict[str, domain.League]:
    """Build an ``id -> domain.League`` map, skipping rows with bad data."""
    leagues: dict[str, domain.League] = {}
    for row in rows:
        try:
            leagues[row.id] = _league_from_row(row)
        except Exception:
            logger.exception("news: skipping league %r: invalid stored data", row.id)
    return leagues


async def _refresh_team(team: TeamORM, league: domain.League | None) -> int:
    """Gather provider / RSS / Google News for one team; returns insert count.

    Every source is fault-isolated — a failure in one is logged and skipped
    so it never loses the others' items — and the write runs in its own
    transaction so one team's bad batch can't roll back (or, on postgres,
    abort) another team's items.
    """
    items: list[domain.NewsItem] = []

    if league is None:
        logger.error(
            "news: team %r references unknown league %r — skipping provider news",
            team.id,
            team.league_id,
        )
    else:
        try:
            provider = registry.get_provider(league.provider)
            items.extend(await provider.get_news(league, _team_from_row(team)))
        except Exception:
            logger.exception("news: provider news failed for team %s", team.id)

    try:
        items.extend(await fetch_team_news(team))
    except Exception:
        logger.exception("news: rss feeds failed for team %s", team.id)

    try:
        items.extend(await fetch_google_news(team, league.name if league else None))
    except Exception:
        logger.exception("news: google news failed for team %s", team.id)

    # The same article often arrives from several sources (same url
    # -> same id); keep the first copy, in source order.
    unique: dict[str, domain.NewsItem] = {}
    for item in items:
        unique.setdefault(item.id, item)
    if not unique:
        return 0

    try:
        async with session_scope() as session:
            return await repository.upsert_news(session, list(unique.values()))
    except Exception:
        logger.exception("news: storing items failed for team %s", team.id)
        return 0


async def _refresh_league(league: domain.League) -> int:
    """Gather provider competition-wide news for one ``follow_all`` league.

    The league counterpart to ``_refresh_team``: provider news only (RSS and
    Google News are team-keyed, off a team's name / configured feeds, and a
    whole-competition follow has no single team to key them on). Fault-isolated
    with its own write transaction, like the per-team path.
    """
    items: list[domain.NewsItem] = []
    try:
        provider = registry.get_provider(league.provider)
        items.extend(await provider.get_league_news(league))
    except Exception:
        logger.exception("news: provider league news failed for league %s", league.id)

    unique: dict[str, domain.NewsItem] = {}
    for item in items:
        unique.setdefault(item.id, item)
    if not unique:
        return 0

    try:
        async with session_scope() as session:
            return await repository.upsert_news(session, list(unique.values()))
    except Exception:
        logger.exception("news: storing league items failed for league %s", league.id)
        return 0


async def refresh_all_news() -> int:
    """Merge provider / RSS / Google News per followed team, plus competition
    news for every whole-league (``follow_all``) follow; returns insert count.

    The leagues are resolved once per run; each team/league still gets its own
    write transaction (see ``_refresh_team`` / ``_refresh_league``).
    """
    async with session_scope() as session:
        teams = await repository.list_teams(session)
        league_rows = await repository.list_leagues(session)
        follow_all_rows = await repository.list_follow_all_leagues(session)

    leagues = _leagues_by_id(league_rows)

    total_new = 0
    # Teams FIRST, then whole-competition leagues: ``upsert_news`` keeps the
    # first copy of a duplicate url (same id), so a followed team's article
    # retains its team identity instead of being downgraded to a league badge
    # when the same url also surfaces in the competition feed.
    for team in teams:
        total_new += await _refresh_team(team, leagues.get(team.league_id))
    for row in follow_all_rows:
        total_new += await _refresh_league(_league_from_row(row))
    logger.info("news: refresh complete, %d new item(s)", total_new)
    return total_new


async def refresh_news_for_team(team_id: str) -> int:
    """Refresh news for a single followed team; returns insert count.

    Scoped to one team so the manual refresh returns in a second or two
    instead of walking every followed team. Unknown ``team_id`` is a no-op
    (returns 0), matching ``GET /api/news``'s tolerance of unknown teams.
    """
    async with session_scope() as session:
        team = await repository.get_team(session, team_id)
        league_row = (
            await repository.get_league(session, team.league_id) if team is not None else None
        )
    if team is None:
        logger.warning("news: manual refresh for unknown team %r — no-op", team_id)
        return 0
    league = _league_from_row(league_row) if league_row is not None else None
    return await _refresh_team(team, league)


async def refresh_news_for_league(league_id: str) -> int:
    """Refresh competition-wide news for a single ``follow_all`` league.

    A no-op (returns 0) for an unknown league or one that isn't followed in
    whole — a partially-followed league's news comes from its team rows, not
    here.
    """
    async with session_scope() as session:
        league_row = await repository.get_league(session, league_id)
    if league_row is None or not league_row.follow_all:
        logger.warning("news: manual refresh for non-follow_all league %r — no-op", league_id)
        return 0
    return await _refresh_league(_league_from_row(league_row))


# Serializes manual refreshes so rapid clicks (or an overlapping scheduled
# run starting) can't fan out duplicate external fetches at once.
_manual_refresh_lock = asyncio.Lock()


async def trigger_refresh(team_id: str | None = None, league_id: str | None = None) -> int:
    """On-demand refresh behind ``POST /api/news/refresh``; returns insert count.

    ``team_id`` scopes the fetch to one followed team and ``league_id`` to one
    whole-competition follow (both fast); with neither, every followed team and
    competition is refreshed. Serialized by ``_manual_refresh_lock``.
    """
    async with _manual_refresh_lock:
        if team_id is not None:
            return await refresh_news_for_team(team_id)
        if league_id is not None:
            return await refresh_news_for_league(league_id)
        return await refresh_all_news()
