"""Playoff brackets for the US team sports (NBA/MLB/NHL).

Soccer cups use single-game knockouts (handled by the frontend from synced
fixtures); the US leagues play best-of-N *series*, which ESPN exposes on the
playoff scoreboard (``seasontype=3``).  Each game carries a ``series`` block
(id + human summary like "NY leads series 3-0") and a ``notes`` headline
naming the round.  We fetch the whole playoff window, dedupe games into
series, and group series into rounds (ordered by when each round started).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app import timeutil
from app.models.domain import Sport

logger = logging.getLogger(__name__)

_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_PLAYOFF_SPORTS = frozenset({Sport.BASKETBALL, Sport.BASEBALL, Sport.HOCKEY})
# Conference qualifier dropped so East/West halves of a round merge into one
# round column ("East 1st Round" + "West 1st Round" -> "1st Round").
_CONF_PREFIX = re.compile(r"^(eastern|western|east|west)\s+", re.IGNORECASE)
# A note that actually names a playoff round (vs a generic "Playoff Series").
_REAL_ROUND = re.compile(
    r"round|final|semifinal|quarterfinal|conference|stanley cup|championship|world series",
    re.IGNORECASE,
)
# Round labels ESPN attaches to mislabeled / out-of-sequence games that don't
# actually name a round; dropped once any properly-named round is present.
_GENERIC_ROUND_LABELS = frozenset({"", "playoff", "playoffs", "playoff series"})


@dataclass(frozen=True)
class PlayoffSide:
    name: str
    abbreviation: str | None
    logo_url: str | None


@dataclass(frozen=True)
class PlayoffSeries:
    team1: PlayoffSide
    team2: PlayoffSide
    summary: str  # e.g. "NY leads series 3-0", "LAL win series 4-2"
    # "East" / "West" recovered from the round note, used to place the series
    # on the left/right half of a two-sided bracket; None for the league
    # championship (no conference prefix) and sports without conferences.
    conference: str | None = None


@dataclass(frozen=True)
class PlayoffRound:
    name: str
    series: list[PlayoffSeries]


def supports(sport: Sport) -> bool:
    return sport in _PLAYOFF_SPORTS


def _round_name(note: str, fallback: str | None) -> str:
    """Human round label, with the East/West halves merged into one column.

    The conference qualifier is stripped so "East 1st Round" / "West 1st
    Round" collapse to a single "1st Round".  When stripping leaves only a
    bare "Final"/"Finals" (the conference final) it is relabelled
    "Conference Finals" so it stays distinct from the league championship
    ("Stanley Cup Final" / "NBA Finals"), which carries no conference prefix.
    """
    base = note.split(" - ")[0].strip() if note else (fallback or "").strip()
    had_conf = bool(_CONF_PREFIX.match(base))
    stripped = _CONF_PREFIX.sub("", base).strip()
    if had_conf and stripped.lower() in {"final", "finals"}:
        return "Conference Finals"
    return stripped or base


# Map the conference word ESPN uses to a stable short label the frontend
# splits the two-sided bracket on (left = East, right = West).
_CONF_NORMALIZE = {
    "eastern": "East",
    "east": "East",
    "western": "West",
    "west": "West",
}


def _conference(note: str, fallback: str | None) -> str | None:
    """The "East"/"West" conference named by a round note, or None.

    Reads the same "<Conference> <Round>" headline that ``_round_name``
    strips (e.g. "Western Conference Finals" -> "West"). None when the note
    carries no conference qualifier — the case for the league championship
    ("NBA Finals", "Stanley Cup Final") and for non-conference sports — which
    is the signal the frontend uses to place a series in the center column.
    """
    base = note.split(" - ")[0].strip() if note else (fallback or "").strip()
    match = _CONF_PREFIX.match(base)
    if match is None:
        return None
    return _CONF_NORMALIZE.get(match.group(1).lower())


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _side(competitor: dict) -> PlayoffSide:
    team = competitor.get("team") if isinstance(competitor, dict) else None
    team = team if isinstance(team, dict) else {}
    return PlayoffSide(
        name=team.get("displayName") or team.get("name") or "",
        abbreviation=team.get("abbreviation"),
        logo_url=team.get("logo"),
    )


async def fetch_playoff_bracket(provider_key: str, sport: Sport) -> list[PlayoffRound]:
    """Playoff rounds (each a list of series) for a league; ``[]`` if none.

    Never raises — a network/parse failure logs and returns ``[]`` so the
    bracket route degrades to "no active bracket".
    """
    if sport not in _PLAYOFF_SPORTS:
        return []
    now = timeutil.utcnow()
    start = (now - timedelta(days=95)).strftime("%Y%m%d")
    end = (now + timedelta(days=7)).strftime("%Y%m%d")
    url = f"{_SITE_BASE}/{provider_key}/scoreboard"
    # ``limit`` is load-bearing: a full playoff window plus the regular-season
    # / makeup games the date range also catches runs to a few hundred events,
    # and ESPN truncates to ``limit`` (oldest first).  300 cut the feed off
    # mid-first-round, freezing the bracket there; 1000 covers the whole
    # postseason through the finals with headroom.
    params = {"dates": f"{start}-{end}", "seasontype": "3", "limit": "1000"}
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
        logger.warning("espn_playoffs: fetch failed for %s", provider_key, exc_info=True)
        return []
    if not isinstance(data, dict):
        return []
    return _rounds_from_events(data.get("events") or [])


def _rounds_from_events(events: list) -> list[PlayoffRound]:
    """Group a scoreboard's events into ordered playoff rounds.

    Pure (no I/O): keeps only games carrying a real ``series.summary``,
    dedupes them into series, buckets series into rounds, drops the generic
    placeholder round when any named round exists, and orders rounds by when
    each first played.  Split out from the fetch so it can be unit-tested.
    """
    # Dedupe games into series; remember when each round first played.
    series: dict[str, dict] = {}
    round_started: dict[str, datetime] = {}
    for event in events:
        comps = event.get("competitions") if isinstance(event, dict) else None
        if not isinstance(comps, list) or not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors")
        if not isinstance(competitors, list) or len(competitors) < 2:
            continue
        block = comp.get("series") if isinstance(comp.get("series"), dict) else {}
        summary = block.get("summary") or ""
        # Only real playoff series carry a series summary ("X leads series 2-1");
        # this filters out regular-season / makeup games the date range catches.
        if not summary:
            continue
        notes = comp.get("notes")
        note = (
            notes[0].get("headline", "")
            if isinstance(notes, list) and notes and isinstance(notes[0], dict)
            else ""
        )
        round_name = _round_name(note, block.get("title"))
        conference = _conference(note, block.get("title"))
        sides = [_side(competitors[0]), _side(competitors[1])]
        key = str(block.get("id") or "") or (
            "|".join(sorted(s.abbreviation or s.name for s in sides)) + ":" + round_name
        )
        if key not in series:
            series[key] = {
                "round": round_name,
                "sides": sides,
                "summary": summary,
                "conference": conference,
            }
        series[key]["summary"] = summary
        # Upgrade a generic round label ("Playoff Series") to a specific one
        # ("Conference Finals") if any of the series' games names it.
        if _REAL_ROUND.search(round_name) and not _REAL_ROUND.search(series[key]["round"]):
            series[key]["round"] = round_name
        date = _parse_date(comp.get("date")) or _parse_date(event.get("date"))
        if date is not None:
            current = round_started.get(round_name)
            if current is None or date < current:
                round_started[round_name] = date

    by_round: dict[str, list[PlayoffSeries]] = {}
    for entry in series.values():
        by_round.setdefault(entry["round"], []).append(
            PlayoffSeries(
                team1=entry["sides"][0],
                team2=entry["sides"][1],
                summary=entry["summary"],
                conference=entry["conference"],
            )
        )

    # Drop the generic placeholder round ("Playoff Series") that ESPN pins on
    # mislabeled / out-of-sequence games whenever any properly-named round is
    # present, so it doesn't appear as a junk column (and, dated earliest,
    # sort ahead of the 1st round).  Keep it only when it's all we have, so a
    # sparsely-labeled bracket still renders something.
    named = {name for name in by_round if name.strip().lower() not in _GENERIC_ROUND_LABELS}
    if named:
        by_round = {name: by_round[name] for name in named}

    far_future = datetime.max.replace(tzinfo=timezone.utc)
    ordered = sorted(by_round, key=lambda r: round_started.get(r, far_future))
    return [PlayoffRound(name=name, series=by_round[name]) for name in ordered]
