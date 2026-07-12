"""League-wide player stat leaders from ESPN's per-athlete stats API.

The standard scoreboard/standings feeds carry no league leaders, but
``site.web.api.espn.com/.../statistics/byathlete`` ranks every qualified
athlete by a sortable stat.  We pull one headline stat per sport (NBA
points, MLB home runs, NHL points) — soccer has no athlete-stats feed
there, so it uses the box-score Golden Boot instead (``routes/scorers``).

The response shape: a top-level ``categories[]`` defines each category's
ordered ``labels`` (e.g. offensive → PTS, FGM, …); each athlete's
``categories[].totals`` is a positional array aligned to those labels.
So we find the headline label's index once, then read each athlete's
value at that index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.models.domain import Sport

logger = logging.getLogger(__name__)

_WEB_BASE = "https://site.web.api.espn.com/apis/common/v3/sports"

# sport -> (sort key, category name, display label, unit shown on the board)
_SPECS: dict[Sport, tuple[str, str, str, str]] = {
    Sport.BASKETBALL: ("offensive.avgPoints:desc", "offensive", "PTS", "PPG"),
    Sport.BASEBALL: ("batting.homeRuns:desc", "batting", "HR", "HR"),
    Sport.HOCKEY: ("offensive.points:desc", "offensive", "PTS", "PTS"),
}


@dataclass(frozen=True)
class LeaderEntry:
    athlete_id: str
    player: str
    team: str  # ESPN short team name (e.g. "CHI")
    position: str | None
    value: float
    stat_label: str  # unit shown (e.g. "PPG", "HR")


def supports(sport: Sport) -> bool:
    return sport in _SPECS


def _label_index(categories_def, category: str, label: str) -> int | None:
    if not isinstance(categories_def, list):
        return None
    for cat in categories_def:
        if isinstance(cat, dict) and cat.get("name") == category:
            labels = cat.get("labels")
            if isinstance(labels, list) and label in labels:
                return labels.index(label)
    return None


def _value_at(athlete_block, category: str, index: int) -> float | None:
    for cat in athlete_block.get("categories") or []:
        if isinstance(cat, dict) and cat.get("name") == category:
            totals = cat.get("totals")
            if isinstance(totals, list) and index < len(totals):
                try:
                    return float(str(totals[index]).replace(",", ""))
                except (ValueError, TypeError):
                    return None
            return None
    return None


async def fetch_league_leaders(
    provider_key: str, sport: Sport, limit: int = 30
) -> list[LeaderEntry]:
    """Top ``limit`` athletes by the sport's headline stat; ``[]`` on miss.

    ``provider_key`` is the ESPN ``sport/league`` fragment (e.g.
    ``"basketball/nba"``).  Never raises — a network/parse failure logs and
    returns ``[]`` so the leaders route can fall back gracefully.
    """
    spec = _SPECS.get(sport)
    if spec is None:
        return []
    sort_key, category, label, unit = spec
    url = f"{_WEB_BASE}/{provider_key}/statistics/byathlete"
    params = {"limit": str(limit), "sort": sort_key, "region": "us", "lang": "en"}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": "SportsDash/1.0"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("espn_leaders: fetch failed for %s", provider_key, exc_info=True)
        return []
    if not isinstance(data, dict):
        return []

    index = _label_index(data.get("categories"), category, label)
    if index is None:
        return []

    out: list[LeaderEntry] = []
    for block in data.get("athletes") or []:
        if not isinstance(block, dict):
            continue
        athlete = block.get("athlete")
        if not isinstance(athlete, dict):
            continue
        athlete_id = str(athlete.get("id") or "").strip()
        name = athlete.get("displayName")
        if not athlete_id or not (isinstance(name, str) and name):
            continue
        value = _value_at(block, category, index)
        if value is None:
            continue
        position = athlete.get("position")
        pos_abbr = position.get("abbreviation") if isinstance(position, dict) else None
        team = athlete.get("teamShortName")
        out.append(
            LeaderEntry(
                athlete_id=athlete_id,
                player=name,
                team=team if isinstance(team, str) else "",
                position=pos_abbr if isinstance(pos_abbr, str) else None,
                value=value,
                stat_label=unit,
            )
        )
    return out
