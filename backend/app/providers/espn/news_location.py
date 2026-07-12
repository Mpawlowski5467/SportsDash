"""News article and team home-venue/location parsers.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any


from app.models.domain import (
    NewsItem,
    Team,
    TeamLocation,
)

from app.providers.espn.common import _parse_espn_datetime

logger = logging.getLogger(__name__)


def _parse_article(
    article: Any, *, team: Team | None = None, league_id: str | None = None
) -> NewsItem | None:
    """Parse one news article; None for premium or malformed entries.

    Tag with ``team.id`` for a followed-team feed, or ``league_id`` for a
    whole-competition feed (``team`` is then None and ``team_id`` stays None).
    """
    scope = team.id if team is not None else league_id
    try:
        if not isinstance(article, dict):
            raise ValueError("article is not an object")
        if article.get("premium"):
            # Paywalled content is useless in the dashboard; skip quietly.
            return None
        links = article.get("links")
        web = links.get("web") if isinstance(links, dict) else None
        href = web.get("href") if isinstance(web, dict) else None
        if not (isinstance(href, str) and href):
            raise ValueError("missing web link")
        title = article.get("headline")
        if not (isinstance(title, str) and title):
            raise ValueError("missing headline")
        summary = article.get("description")
        if not (isinstance(summary, str) and summary):
            summary = None
        image_url: str | None = None
        images = article.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            raw_url = images[0].get("url")
            if isinstance(raw_url, str) and raw_url:
                image_url = raw_url
        return NewsItem(
            id=hashlib.sha1(href.encode("utf-8")).hexdigest()[:16],
            team_id=team.id if team is not None else None,
            title=title,
            url=href,
            source="ESPN",
            published_at=_parse_espn_datetime(article.get("published")),
            summary=summary,
            image_url=image_url,
            league_id=league_id,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN article for %s", scope, exc_info=True)
        return None


def _parse_news(
    data: Any, *, team: Team | None = None, league_id: str | None = None
) -> list[NewsItem]:
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []
    return [
        item
        for article in articles
        if (item := _parse_article(article, team=team, league_id=league_id)) is not None
    ]


def _venue_geocode_query(venue: dict[str, Any]) -> str | None:
    """A geocodable venue string from an ESPN ``venue`` object.

    Combines the venue ``fullName`` with its ``address`` city/state/country
    so the downstream geocoder (Nominatim) has enough context to resolve a
    stadium with a common name (e.g. "Wembley Stadium, London, England").
    Returns ``None`` when the object carries no usable name.
    """
    if not isinstance(venue, dict):
        return None
    name = venue.get("fullName") or venue.get("shortName")
    if not isinstance(name, str) or not name.strip():
        return None
    parts = [name.strip()]
    address = venue.get("address")
    if isinstance(address, dict):
        for key in ("city", "state", "country"):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return ", ".join(parts)


def _parse_team_location(data: Any, provider_key: str | None = None) -> TeamLocation | None:
    """Home-venue location for the map view from a ``/teams/{id}`` payload.

    ESPN keeps the home venue in different places by sport: US franchise
    sports (NBA/NFL/MLB/NHL) carry it under ``team.franchise.venue`` while
    a few leagues expose a top-level ``team.venue``; soccer teams have no
    venue on this endpoint at all.  For those we fall back to the team's
    ``nextEvent`` fixture, whose competition ``venue`` is the *home* side's
    ground — used only when ``provider_key`` is the home competitor, so a
    club with an away next match isn't stranded at the opponent's stadium
    (the scheduler still has the stored-game / enrichment fallbacks beyond
    that).  ESPN does not ship usable coordinates here — only a ``$ref`` to
    a venue resource — so lat/lon are left ``None`` for the geocode service
    to fill.  Returns ``None`` when no venue name is present.
    """
    if not isinstance(data, dict):
        return None
    team = data.get("team")
    if not isinstance(team, dict):
        return None
    venue = team.get("venue")
    if not isinstance(venue, dict) or not venue.get("fullName"):
        franchise = team.get("franchise")
        if isinstance(franchise, dict) and isinstance(franchise.get("venue"), dict):
            venue = franchise["venue"]
    query = _venue_geocode_query(venue) if isinstance(venue, dict) else None
    if query is None:
        # Soccer clubs expose no team/franchise venue here; their next
        # fixture usually carries one (the home side's ground).
        query = _home_venue_from_next_event(team, provider_key)
    if query is None:
        return None
    # Coordinates are deliberately None: the team endpoint exposes only a
    # venue resource reference, never lat/lon.  The geocode service resolves
    # the combined venue string.
    return TeamLocation(venue=query, lat=None, lon=None)


def _home_venue_from_next_event(team: dict[str, Any], provider_key: str | None) -> str | None:
    """Geocodable home-venue string from ``team.nextEvent``, when this team hosts.

    A competition's ``venue`` is the home side's stadium, so this is only
    trusted when ``provider_key`` matches the home competitor — otherwise we
    can't tell whose ground it is and return ``None`` rather than risk
    placing the club at an opponent's stadium.
    """
    if provider_key is None:
        return None
    key = str(provider_key)
    events = team.get("nextEvent")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if not isinstance(competition, dict):
                continue
            venue = competition.get("venue")
            if not isinstance(venue, dict):
                continue
            if not _is_home_competitor(competition, key):
                continue
            query = _venue_geocode_query(venue)
            if query is not None:
                return query
    return None


def _is_home_competitor(competition: dict[str, Any], provider_key: str) -> bool:
    """Whether ``provider_key`` is the ``homeAway == "home"`` competitor."""
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        return False
    for competitor in competitors:
        if not isinstance(competitor, dict) or competitor.get("homeAway") != "home":
            continue
        cid = competitor.get("id")
        if cid is None and isinstance(competitor.get("team"), dict):
            cid = competitor["team"].get("id")
        if cid is not None and str(cid) == provider_key:
            return True
    return False
