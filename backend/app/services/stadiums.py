"""Stadium enrichment via TheSportsDB's keyless ``searchteams.php``.

ESPN ships no venue for soccer clubs, and an off-season team has no stored
fixture to borrow a venue from — so a freshly-followed club (e.g. Chelsea)
never lands on the map.  This service fills that gap by looking a team up
*by name* on TheSportsDB (already an integrated provider) and returning a
:class:`~app.models.domain.TeamLocation` carrying the stadium name, the
free-text location, seating capacity, the year opened, a stadium photo,
and — when the matched team has a venue record — coordinates parsed from
the venue's DMS map reference.

Two endpoints are used, both on the public ``"3"`` test key:

* ``searchteams.php?t={name}`` — the stadium NAME (``strStadium``), a
  free-text ``strLocation``, ``intStadiumCapacity`` and an ``idVenue``;
* ``lookupvenue.php?id={idVenue}`` — the stadium PHOTO (``strThumb``),
  the year opened (``intFormedYear``) and a ``strMap`` DMS coordinate
  string the geocoder would otherwise have to resolve.

Like every external-HTTP helper in the app this is defensive end to end:
the free tier returns several name matches (a club, its U21/women's/youth
sides, unrelated same-named clubs), is aggressively rate-limited (a 429
comes back as an HTML body, not JSON), and routinely omits fields.  Every
network call and every parse is guarded so any failure (HTTP error,
non-JSON body, timeout, missing/garbage field) is logged and degrades to
``None`` — it never raises, because the scheduler's location-refresh job
relies on the never-raise contract.  Successful lookups are cached
in-process (keyed by the normalized name + sport) so the same team is
never re-fetched within a process lifetime.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.domain import TeamLocation
from app.services import tsdb_client

logger = logging.getLogger(__name__)

# Base URL, sport-label map, coercion helpers, the shared HTTP client and
# the process-wide pacing gate all live in services.tsdb_client.
_SPORT_LABEL = tsdb_client.SPORT_LABEL

# In-process cache: a resolved (or definitively-missed) lookup is reused for
# the life of the process.  A definitive miss (the search succeeded, nothing
# matched) is cached as ``None`` so a known-missing team is not re-fetched on
# every refresh pass — but a TRANSIENT failure (a rate-limited 429, a timeout)
# is deliberately NOT cached, so one bad refresh can't permanently strand a
# real club (the off-season map bug).
_cache: dict[str, TeamLocation | None] = {}
# Separate cache for the club "About" lookup (description + founded year),
# which reads the same ``searchteams.php`` hit but returns different facts.
_info_cache: dict[str, "TeamInfo | None"] = {}


@dataclass(frozen=True)
class TeamInfo:
    """Club background facts for the team page's "About" section."""

    description: str | None = None  # history paragraph (``strDescriptionEN``)
    founded: int | None = None  # founding year (``intFormedYear``)


async def lookup_stadium(team_name: str, *, sport: str | None = None) -> TeamLocation | None:
    """Resolve a team's home stadium (+ facts) by name; ``None`` on miss.

    Searches TheSportsDB for ``team_name``, prefers the best match for
    ``sport`` (when given), and enriches it with the venue record's photo
    and coordinates.  Cached per ``(name, sport)`` and never raises: any
    failure is logged and returns ``None``.  Returns a partial
    :class:`TeamLocation` (e.g. venue + capacity, no coords) when only some
    facts are available — the caller geocodes a coord-less venue.
    """
    name = (team_name or "").strip()
    if not name:
        return None

    cache_key = f"{name.casefold()}|{(sport or '').casefold()}"
    if cache_key in _cache:
        return _cache[cache_key]

    ok, team = await _search_team(name, sport)
    if not ok:
        # Transient fetch failure — leave it UNcached so the next refresh
        # retries instead of permanently returning None for a real club.
        return None
    if team is None:
        # Definitive miss (the search succeeded, nothing matched) — cache it.
        _cache[cache_key] = None
        return None

    location = await _build_location(team)
    _cache[cache_key] = location
    return location


async def lookup_team_info(team_name: str, *, sport: str | None = None) -> TeamInfo | None:
    """Resolve a club's "About" facts (description + founded) by name.

    Reads the same ``searchteams.php`` hit as :func:`lookup_stadium` —
    ``strDescriptionEN`` and ``intFormedYear`` — and returns a
    :class:`TeamInfo`.  Cached per ``(name, sport)`` and never raises: any
    failure or a hit with neither fact returns ``None`` so the caller can
    fall back to another source (e.g. Wikipedia).
    """
    name = (team_name or "").strip()
    if not name:
        return None

    cache_key = f"{name.casefold()}|{(sport or '').casefold()}"
    if cache_key in _info_cache:
        return _info_cache[cache_key]

    ok, team = await _search_team(name, sport)
    if not ok:
        # Transient fetch failure — don't cache, so the next refresh retries.
        return None
    info: TeamInfo | None = None
    if team is not None:
        description = _clean(team.get("strDescriptionEN"))
        founded = _coerce_int(team.get("intFormedYear"))
        if description is not None or founded is not None:
            info = TeamInfo(description=description, founded=founded)
    _info_cache[cache_key] = info
    return info


async def _search_team(name: str, sport: str | None) -> tuple[bool, dict | None]:
    """Fetch ``searchteams.php`` and pick the best match for name + sport.

    Returns ``(ok, team)``:

    * ``ok`` is ``False`` ONLY when the HTTP call itself failed
      (network/timeout/429/non-JSON) — a *transient* miss the caller must
      NOT cache, or one rate-limited refresh would poison a real club's
      lookup with ``None`` for the rest of the process (the off-season map
      bug: a club that TheSportsDB knows perfectly well never re-resolves).
    * ``team`` is the chosen dict on a hit, or ``None`` on a *definitive*
      miss (the search succeeded but no candidate had a usable record) —
      which the caller is free to cache.
    """
    payload = await _get_json("searchteams.php", {"t": name})
    if payload is None:
        # The call failed — distinct from "no such team".  Signal transient.
        return False, None
    teams = payload.get("teams") if isinstance(payload, dict) else None
    if not isinstance(teams, list) or not teams:
        return True, None

    candidates = [team for team in teams if isinstance(team, dict)]
    label = _SPORT_LABEL.get((sport or "").casefold())
    if label is not None:
        sport_matches = [team for team in candidates if _clean(team.get("strSport")) == label]
        if sport_matches:
            candidates = sport_matches

    return True, _best_match(candidates, name)


def _best_match(candidates: list[dict], name: str) -> dict | None:
    """Pick the candidate that best matches ``name`` and has a stadium.

    Prefers an exact (case-insensitive) name match, then the first
    candidate that actually carries a stadium name; falls back to the
    first candidate so a partial record (location only) can still be used.
    """
    folded = name.casefold()
    with_stadium = [team for team in candidates if _clean(team.get("strStadium"))]

    exact = [
        team
        for team in with_stadium
        if _clean(team.get("strTeam")) and _clean(team["strTeam"]).casefold() == folded
    ]
    if exact:
        return exact[0]
    if with_stadium:
        return with_stadium[0]
    return candidates[0] if candidates else None


async def _build_location(team: dict) -> TeamLocation | None:
    """Assemble a :class:`TeamLocation` from a search hit (+ venue lookup).

    The search result supplies the stadium name, location text and
    capacity; a follow-up ``lookupvenue.php`` (when the hit carries an
    ``idVenue``) adds the photo, the year opened and coordinates.  Returns
    ``None`` only when the team has no stadium name *and* no location text
    at all.
    """
    stadium = _clean(team.get("strStadium"))
    location = _clean(team.get("strLocation"))
    if stadium is None and location is None:
        return None

    capacity = _coerce_int(team.get("intStadiumCapacity"))
    image_url = _clean(team.get("strStadiumThumb"))
    opened: int | None = None
    lat: float | None = None
    lon: float | None = None

    venue = await _lookup_venue(team.get("idVenue"))
    if venue is not None:
        capacity = capacity or _coerce_int(venue.get("intCapacity"))
        image_url = image_url or _clean(venue.get("strThumb")) or _clean(venue.get("strFanart1"))
        opened = _coerce_int(venue.get("intFormedYear"))
        location = location or _clean(venue.get("strLocation"))
        coords = _parse_dms_map(_clean(venue.get("strMap")))
        if coords is not None:
            lat, lon = coords
        # A venue's name can be richer/more correct than the team's field.
        stadium = stadium or _clean(venue.get("strVenue"))

    venue_name = _compose_venue(stadium, location)
    return TeamLocation(
        venue=venue_name,
        lat=lat,
        lon=lon,
        capacity=capacity,
        opened=opened,
        image_url=image_url,
        location=location,
        surface=None,
    )


async def _lookup_venue(id_venue: object) -> dict | None:
    """Fetch ``lookupvenue.php`` for an ``idVenue``; ``None`` if absent/failed.

    TheSportsDB encodes ``idVenue`` as a string and uses ``"0"``/``""`` for
    "no venue record", so those are treated as absent (no call made).
    """
    venue_id = _clean(str(id_venue)) if id_venue is not None else None
    if venue_id is None or venue_id == "0":
        return None
    payload = await _get_json("lookupvenue.php", {"id": venue_id})
    venues = payload.get("venues") if isinstance(payload, dict) else None
    if not isinstance(venues, list) or not venues or not isinstance(venues[0], dict):
        return None
    return venues[0]


def _compose_venue(stadium: str | None, location: str | None) -> str | None:
    """Build one geocodable venue string from the stadium + location text.

    "Stamford Bridge" + "Fulham, London" -> "Stamford Bridge, Fulham,
    London".  When only one is known (or they are the same string) it is
    used alone, mirroring the provider's ``_parse_team_location``.
    """
    if stadium and location and stadium.casefold() != location.casefold():
        return f"{stadium}, {location}"
    return stadium or location


# ---------------------------------------------------------------------------
# Coercion / parsing helpers (pure)
# ---------------------------------------------------------------------------

_clean = tsdb_client.clean_str


def _coerce_int(value: object) -> int | None:
    """Best-effort positive int from TheSportsDB's string-encoded numbers.

    A zero/negative (the free tier's "unknown" sentinel for capacity/year)
    is treated as missing.
    """
    if isinstance(value, bool):
        return None
    number: int | None = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        number = int(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if text:
            try:
                number = int(float(text))
            except ValueError:
                number = None
    if number is None or number <= 0:
        return None
    return number


# DMS map references look like ``51°28′54″N 0°11′28″W`` (TheSportsDB uses a
# variety of degree/minute/second glyphs and ASCII fallbacks).  Capture the
# numbers and the hemisphere letter for each of the two coordinates.
_DMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)[°\s]+"  # degrees
    r"(?:(\d+(?:\.\d+)?)[′'`\s]+)?"  # minutes (optional)
    r"(?:(\d+(?:\.\d+)?)[″\"\s]*)?"  # seconds (optional)
    r"([NSEW])",
    re.IGNORECASE,
)


def _parse_dms_map(text: str | None) -> tuple[float, float] | None:
    """Parse a ``strMap`` DMS string into ``(lat, lon)`` decimal degrees.

    Returns ``None`` when the string is absent or does not contain a
    latitude *and* a longitude in a recognizable degrees/minutes/seconds
    form (some records carry an embeddable map URL instead, which the
    geocoder handles via the venue name).
    """
    if not text:
        return None
    matches = _DMS_RE.findall(text)
    lat: float | None = None
    lon: float | None = None
    for degrees, minutes, seconds, hemi in matches:
        try:
            value = float(degrees)
            value += float(minutes) / 60.0 if minutes else 0.0
            value += float(seconds) / 3600.0 if seconds else 0.0
        except ValueError:
            continue
        hemi = hemi.upper()
        if hemi in ("S", "W"):
            value = -value
        if hemi in ("N", "S") and lat is None and -90.0 <= value <= 90.0:
            lat = value
        elif hemi in ("E", "W") and lon is None and -180.0 <= value <= 180.0:
            lon = value
    if lat is None or lon is None:
        return None
    return round(lat, 5), round(lon, 5)


async def _get_json(endpoint: str, params: dict[str, str]) -> object | None:
    """Fetch + JSON-decode one endpoint; ``None`` on any failure.

    The free tier answers a rate-limited request with an HTML body (HTTP
    429) and an empty body for some misses, so JSON decoding is guarded;
    every failure is logged and returns ``None`` so the never-raise
    contract holds.
    """
    try:
        # max_retries=0 → FAIL-FAST like the photo lookup: stadium resolves
        # run inside batch refresh jobs, so a 429 skips the team (retried
        # next refresh, uncached) instead of sleeping out its Retry-After.
        response = await tsdb_client.paced_get(
            endpoint, params, max_retries=0, label="tsdb-stadiums"
        )
        response.raise_for_status()
        if not response.content or not response.text.strip():
            return None
        return response.json()
    except Exception:
        logger.warning(
            "stadiums: lookup failed (%s params=%s) — returning None",
            endpoint,
            params,
            exc_info=True,
        )
        return None
