"""Curated coordinates for the 16 FIFA World Cup 2026 host stadiums.

A national team in a host-country tournament does not play at its *home*
stadium — it plays across the host venues in the USA, Mexico and Canada.
The map therefore plots each World Cup nation at the venue of its NEXT
match, and that venue name (carried on the synced game, e.g.
``"MetLife Stadium"``) is resolved to coordinates here.

This is a small, fixed, well-known set, so a curated table is more
reliable than geocoding: no network dependency, exact placement, and a
city + capacity for the info panel.  Venue names are keyed exactly as
ESPN reports them on the World Cup fixtures, with a few aliases for the
recently-renamed Mexican grounds.  ``resolve`` returns ``None`` for any
venue not in this set, so non-host competitions fall back to the normal
home-stadium resolution untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class HostVenue:
    name: str  # canonical stadium name
    lat: float
    lon: float
    location: str  # "City, Region, Country" for the info panel
    capacity: int  # approximate 2026 World Cup configuration


# The 16 host venues, keyed by their ESPN fixture venue name.
_VENUES: tuple[HostVenue, ...] = (
    # --- United States (11) ---
    HostVenue("MetLife Stadium", 40.8135, -74.0745, "East Rutherford, New Jersey, USA", 82_500),
    HostVenue("AT&T Stadium", 32.7473, -97.0945, "Arlington, Texas, USA", 80_000),
    HostVenue("Mercedes-Benz Stadium", 33.7555, -84.4008, "Atlanta, Georgia, USA", 71_000),
    HostVenue("NRG Stadium", 29.6847, -95.4107, "Houston, Texas, USA", 72_000),
    HostVenue(
        "GEHA Field at Arrowhead Stadium", 39.0489, -94.4839, "Kansas City, Missouri, USA", 76_400
    ),
    HostVenue("SoFi Stadium", 33.9534, -118.3391, "Inglewood, California, USA", 70_000),
    HostVenue("Hard Rock Stadium", 25.9580, -80.2389, "Miami Gardens, Florida, USA", 65_000),
    HostVenue("Gillette Stadium", 42.0909, -71.2643, "Foxborough, Massachusetts, USA", 65_000),
    HostVenue(
        "Lincoln Financial Field", 39.9008, -75.1674, "Philadelphia, Pennsylvania, USA", 69_000
    ),
    HostVenue("Levi's Stadium", 37.4030, -121.9700, "Santa Clara, California, USA", 68_500),
    HostVenue("Lumen Field", 47.5952, -122.3316, "Seattle, Washington, USA", 69_000),
    # --- Canada (2) ---
    HostVenue("BC Place", 49.2768, -123.1119, "Vancouver, British Columbia, Canada", 54_500),
    HostVenue("BMO Field", 43.6332, -79.4185, "Toronto, Ontario, Canada", 45_000),
    # --- Mexico (3) ---
    HostVenue("Estadio Banorte", 19.3029, -99.1505, "Mexico City, Mexico", 83_000),
    HostVenue("Estadio Akron", 20.6819, -103.4628, "Guadalajara, Mexico", 48_000),
    HostVenue("Estadio BBVA", 25.6690, -100.2441, "Monterrey, Mexico", 53_500),
)


def _normalize(name: str) -> str:
    """Lowercase, collapse whitespace, drop punctuation for forgiving match.

    Apostrophes are removed rather than split on, so "Levi's Stadium" and
    "Levis Stadium" normalize alike.
    """
    lowered = name.casefold().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


_BY_NAME: dict[str, HostVenue] = {_normalize(v.name): v for v in _VENUES}

# Aliases for the same grounds under older / alternative names so a venue
# string drift on ESPN's side doesn't strand a nation back at its home crest.
_ALIASES: dict[str, str] = {
    "estadio azteca": "Estadio Banorte",
    "estadio guillermo canedo": "Estadio Banorte",
    "estadio bbva bancomer": "Estadio BBVA",
    "estadio akron de guadalajara": "Estadio Akron",
    "arrowhead stadium": "GEHA Field at Arrowhead Stadium",
}
for _alias, _canonical in _ALIASES.items():
    _BY_NAME[_normalize(_alias)] = _BY_NAME[_normalize(_canonical)]


def resolve(venue: str | None) -> HostVenue | None:
    """The host venue for an ESPN venue name, or ``None`` if not a host venue."""
    if not venue:
        return None
    return _BY_NAME.get(_normalize(venue))
