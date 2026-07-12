"""TheSportsDB league/team catalog fetch for the setup wizard.

The second catalog provider behind :func:`app.providers.espn_catalog.get_league_teams`.
TheSportsDB serves the volleyball catalog leagues; this module turns a
:class:`~app.providers.espn_catalog.CatalogLeague` (provider ``"thesportsdb"``)
into the list of pickable :class:`~app.providers.espn_catalog.CatalogTeam`.

Like the ESPN catalog, these are rare, human-triggered setup calls, so each
fetch uses a short-lived :class:`httpx.AsyncClient` instead of a pooled one,
and successful results are cached in-process for an hour.  Deliberately
independent of :class:`app.providers.thesportsdb.TheSportsDbProvider`: a
catalog teams-fetch shares no client or lifecycle with the live provider.

Honest data limits: TheSportsDB's free tier (key ``"3"``) is sparse and
rate-limited — ``lookup_all_teams.php`` for these volleyball leagues may
return few teams, a placeholder set, or none.  Parsing is defensive so the
wizard never crashes regardless of what the upstream returns; an empty
result is simply an empty picker.
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.providers.espn_catalog import CatalogTeam, EspnCatalogError
from app.providers.http_util import TransientProviderError
from app.services import tsdb_client

if TYPE_CHECKING:  # avoid an import cycle at module load
    from app.providers.espn_catalog import CatalogLeague

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 3600.0

# TheSportsDB colors are already "#"-prefixed hex ("#1d59af") — accept those;
# tolerate a bare-hex form too, defensively.
_HEX_RE = re.compile(r"#?(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


# league id -> (monotonic fetch time, teams).  Process-local, mirrors the
# espn_catalog cache; the wizard is the only consumer.
_cache: dict[str, tuple[float, list[CatalogTeam]]] = {}


def _normalize_color(value: Any) -> str | None:
    """TheSportsDB ``strColour1`` is usually "#"-prefixed hex; normalize it."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not _HEX_RE.fullmatch(text):
        return None
    return text if text.startswith("#") else f"#{text}"


def _abbreviation(team: dict[str, Any], name: str) -> str:
    """A short label: prefer ``strTeamShort``, else first 3 chars of the name."""
    short = team.get("strTeamShort")
    if isinstance(short, str) and short.strip():
        return short.strip()[:8]
    return name[:3].upper()


def _parse_team_entry(entry: Any) -> CatalogTeam | None:
    """Parse one ``teams[]`` entry; None (+warning) if malformed."""
    try:
        if not isinstance(entry, dict):
            raise ValueError("team entry is not an object")
        team_id = str(entry.get("idTeam") or "").strip()
        name = entry.get("strTeam")
        if not team_id or not (isinstance(name, str) and name.strip()):
            raise ValueError("missing idTeam or strTeam")
        name = name.strip()

        # Modern TheSportsDB exposes the badge as ``strBadge``; older payloads
        # used ``strTeamBadge`` — accept either.
        logo_url: str | None = None
        for key in ("strBadge", "strTeamBadge"):
            candidate = entry.get(key)
            if isinstance(candidate, str) and candidate.strip():
                logo_url = candidate.strip()
                break

        return CatalogTeam(
            provider_key=team_id,
            name=name,
            abbreviation=_abbreviation(entry, name),
            logo_url=logo_url,
            color=_normalize_color(entry.get("strColour1")),
        )
    except Exception:
        logger.warning(
            "Skipping malformed TheSportsDB catalog team entry", exc_info=True
        )
        return None


def _parse_teams_payload(data: Any) -> list[CatalogTeam]:
    """Extract ``teams[]`` defensively (TheSportsDB returns ``{"teams": null}``
    for a league with no teams)."""
    entries: list[Any] = []
    if isinstance(data, dict):
        raw = data.get("teams")
        if isinstance(raw, list):
            entries = raw
    teams: list[CatalogTeam] = []
    for entry in entries:
        team = _parse_team_entry(entry)
        if team is not None:
            teams.append(team)
    return teams


async def _fetch_json(endpoint: str, params: dict[str, str], league_id: str) -> Any:
    """GET via the shared paced TSDB client; raise EspnCatalogError on failure."""
    try:
        response = await tsdb_client.paced_get(endpoint, params, label="tsdb-catalog")
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError, TransientProviderError) as exc:
        raise EspnCatalogError(
            f"Failed to fetch TheSportsDB catalog for league {league_id!r}: {exc}"
        ) from exc


async def get_tsdb_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
    """List a TheSportsDB catalog league's teams (1h in-process cache).

    Fetches ``lookup_all_teams.php?id={league.provider_key}`` and maps each
    ``teams[]`` entry onto a :class:`CatalogTeam` (provider_key = ``idTeam``,
    name = ``strTeam``, abbreviation from ``strTeamShort``, logo from
    ``strBadge``/``strTeamBadge``, color from ``strColour1``).  Raises
    :class:`EspnCatalogError` on HTTP/decoding failure so the route maps it to
    a 502.  An empty/placeholder upstream simply yields an empty picker.
    """
    cached = _cache.get(league.id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return list(cached[1])

    data = await _fetch_json(
        "lookup_all_teams.php",
        {"id": league.provider_key},
        league.id,
    )
    teams = _parse_teams_payload(data)

    if teams:
        _cache[league.id] = (now, teams)
    else:
        # Don't cache an empty result: the free tier is flaky, and a retry an
        # hour later is cheap (these are rare setup calls).
        logger.warning(
            "TheSportsDB catalog returned no parseable teams for league %s",
            league.id,
        )
    return list(teams)
