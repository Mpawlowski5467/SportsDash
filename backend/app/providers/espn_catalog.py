"""ESPN league/team catalog for the setup wizard.

A static catalog of the leagues the wizard offers, plus a live "list
the teams in this league" fetch.  Deliberately separate from
:class:`~app.providers.espn.EspnProvider`: catalog lookups are rare,
human-triggered setup calls, so each fetch uses a short-lived client
instead of a pooled one, and successful responses are cached in-process
for an hour.

Real league/team names are allowed here — the catalog and whatever the
user picks from it are live app data, not sample data.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.models.domain import INDIVIDUAL_SPORTS, LEADERBOARD_SPORTS, Sport

logger = logging.getLogger(__name__)

_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_CACHE_TTL_SECONDS = 3600.0

# Cap on athletes returned for an individual sport so the picker stays usable
# (tennis rankings are 150 deep; UFC's per-division blocks dedupe to ~100).
_INDIVIDUAL_ROSTER_CAP = 120

# Bare hex color values as ESPN emits them ("1d428a"); 3/4/6/8 digits.
_BARE_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


class EspnCatalogError(Exception):
    """Raised when the ESPN catalog endpoint cannot be fetched/decoded."""


@dataclass(frozen=True)
class CatalogLeague:
    id: str  # internal league slug used app-wide once followed
    name: str
    sport: Sport
    provider: str  # provider id serving this league ("espn")
    provider_key: str  # ESPN sport/league URL fragment
    national: bool = False  # national-team competition (wizard grouping)
    # Offer "follow the whole league/competition" (every game, no team picks).
    # Defaults True: ``get_competition_schedule`` works for every provider and
    # sport, so a fan can follow an entire league (NBA, the EPL, …) — not only
    # cups — without choosing a specific team.
    supports_follow_all: bool = True
    logo_url: str | None = None  # league logo for the picker


@dataclass(frozen=True)
class CatalogTeam:
    provider_key: str  # ESPN team id
    name: str
    abbreviation: str
    logo_url: str | None = None
    color: str | None = None  # "#"-prefixed hex


# League ``logo_url`` values are live-verified ESPN CDN league logos
# (June 2026): the ``leagues[0].logos[]`` (rel "default") href from each
# league's ``/scoreboard``.  Team sports use a stable
# ``teamlogos/leagues/500/{slug}.png`` path; soccer competitions use an
# opaque numeric id under ``leaguelogos/soccer/500/{id}.png`` (read from the
# scoreboard, not derivable from the provider_key).  ``None`` where ESPN has
# no real league logo: a generic ESPN sport icon (tennis tours) or the
# ``default-team-logo`` placeholder (Danish/Norwegian leagues), and the
# TheSportsDB volleyball catalog.  http:// hrefs are normalized to https://.
CATALOG: tuple[CatalogLeague, ...] = (
    CatalogLeague(
        id="nba",
        name="NBA",
        sport=Sport.BASKETBALL,
        provider="espn",
        provider_key="basketball/nba",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
    ),
    CatalogLeague(
        id="wnba",
        name="WNBA",
        sport=Sport.BASKETBALL,
        provider="espn",
        provider_key="basketball/wnba",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png",
    ),
    CatalogLeague(
        id="mlb",
        name="MLB",
        sport=Sport.BASEBALL,
        provider="espn",
        provider_key="baseball/mlb",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png",
    ),
    CatalogLeague(
        id="nhl",
        name="NHL",
        sport=Sport.HOCKEY,
        provider="espn",
        provider_key="hockey/nhl",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png",
    ),
    CatalogLeague(
        id="nfl",
        name="NFL",
        sport=Sport.FOOTBALL,
        provider="espn",
        provider_key="football/nfl",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png",
    ),
    CatalogLeague(
        id="epl",
        name="Premier League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/eng.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/23.png",
    ),
    CatalogLeague(
        id="laliga",
        name="LaLiga",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/esp.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/15.png",
    ),
    CatalogLeague(
        id="bundesliga",
        name="Bundesliga",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/ger.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/10.png",
    ),
    CatalogLeague(
        id="seriea",
        name="Serie A",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/ita.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/12.png",
    ),
    CatalogLeague(
        id="ligue1",
        name="Ligue 1",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fra.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/9.png",
    ),
    CatalogLeague(
        id="mls",
        name="MLS",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/usa.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/19.png",
    ),
    CatalogLeague(
        id="ucl",
        name="Champions League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.champions",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/2.png",
    ),
    # --- National-team competitions (global ESPN team ids) ---
    CatalogLeague(
        id="worldcup",
        name="FIFA World Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.world",
        national=True,
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/4.png",
    ),
    CatalogLeague(
        id="womens-worldcup",
        name="FIFA Women's World Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.wwc",
        national=True,
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/60.png",
    ),
    CatalogLeague(
        id="euros",
        name="UEFA European Championship",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.euro",
        national=True,
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/74.png",
    ),
    CatalogLeague(
        id="nations-league",
        name="UEFA Nations League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.nations",
        national=True,
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/2395.png",
    ),
    CatalogLeague(
        id="copa-america",
        name="Copa América",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/conmebol.america",
        national=True,
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/83.png",
    ),
    # --- Club competitions (whole-competition follow, not national) ---
    CatalogLeague(
        id="europa",
        name="UEFA Europa League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.europa",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/2310.png",
    ),
    CatalogLeague(
        id="conference",
        name="UEFA Europa Conference League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.europa.conf",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/20296.png",
    ),
    CatalogLeague(
        id="club-world-cup",
        name="FIFA Club World Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.cwc",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/1932.png",
    ),
    # --- Domestic leagues (plain follow) ---
    CatalogLeague(
        id="championship",
        name="EFL Championship",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/eng.2",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/24.png",
    ),
    CatalogLeague(
        id="scottish-prem",
        name="Scottish Premiership",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/sco.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/45.png",
    ),
    CatalogLeague(
        id="eredivisie",
        name="Eredivisie",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/ned.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/11.png",
    ),
    CatalogLeague(
        id="liga-portugal",
        name="Liga Portugal",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/por.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/14.png",
    ),
    CatalogLeague(
        id="super-lig",
        name="Süper Lig",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/tur.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/18.png",
    ),
    CatalogLeague(
        id="liga-mx",
        name="Liga MX",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/mex.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/22.png",
    ),
    CatalogLeague(
        id="bundesliga-2",
        name="2. Bundesliga",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/ger.2",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/97.png",
    ),
    CatalogLeague(
        id="laliga-2",
        name="LaLiga 2",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/esp.2",
        # ESPN serves this one over http://; normalized to https://.
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/107.png",
    ),
    CatalogLeague(
        id="ligue-2",
        name="Ligue 2",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fra.2",
        # ESPN serves this one over http://; normalized to https://.
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/96.png",
    ),
    # --- Lower English divisions (plain follow) ---
    CatalogLeague(
        id="league-one",
        name="EFL League One",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/eng.3",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/25.png",
    ),
    CatalogLeague(
        id="league-two",
        name="EFL League Two",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/eng.4",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/26.png",
    ),
    # --- South American domestic leagues (plain follow) ---
    CatalogLeague(
        id="brasileirao",
        name="Brasileirão Série A",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/bra.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/85.png",
    ),
    CatalogLeague(
        id="liga-argentina",
        name="Liga Profesional Argentina",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/arg.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/1.png",
    ),
    # --- South American continental cups (whole-competition follow) ---
    CatalogLeague(
        id="libertadores",
        name="CONMEBOL Libertadores",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/conmebol.libertadores",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/58.png",
    ),
    CatalogLeague(
        id="sudamericana",
        name="CONMEBOL Sudamericana",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/conmebol.sudamericana",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/1208.png",
    ),
    # --- More European domestic leagues (plain follow) ---
    CatalogLeague(
        id="belgian-pro",
        name="Belgian Pro League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/bel.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/6.png",
    ),
    CatalogLeague(
        id="greek-super",
        name="Greek Super League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/gre.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/98.png",
    ),
    CatalogLeague(
        id="austrian-bundesliga",
        name="Austrian Bundesliga",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/aut.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/5.png",
    ),
    CatalogLeague(
        id="swiss-super",
        name="Swiss Super League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/sui.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/17.png",
    ),
    CatalogLeague(
        id="danish-superliga",
        name="Danish Superliga",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/den.1",
        # ESPN has only the default-team-logo placeholder here — leave None.
    ),
    CatalogLeague(
        id="eliteserien",
        name="Norwegian Eliteserien",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/nor.1",
        # ESPN has only the default-team-logo placeholder here — leave None.
    ),
    CatalogLeague(
        id="allsvenskan",
        name="Swedish Allsvenskan",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/swe.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/16.png",
    ),
    CatalogLeague(
        id="russian-premier",
        name="Russian Premier League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/rus.1",
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/106.png",
    ),
    # --- North American cups (whole-competition follow) ---
    CatalogLeague(
        id="us-open-cup",
        name="U.S. Open Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/usa.open",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/69.png",
    ),
    CatalogLeague(
        id="concacaf-champions",
        name="Concacaf Champions Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/concacaf.champions",
        supports_follow_all=True,
        logo_url="https://a.espncdn.com/i/leaguelogos/soccer/500/2298.png",
    ),
    # --- Individual sports: a followed athlete is a single-member "team" ---
    # The tennis tours only expose a generic ESPN sport icon (not a real
    # tour logo), so they stay None; UFC has a real league logo.
    CatalogLeague(
        id="atp",
        name="ATP Tour",
        sport=Sport.TENNIS,
        provider="espn",
        provider_key="tennis/atp",
    ),
    CatalogLeague(
        id="wta",
        name="WTA Tour",
        sport=Sport.TENNIS,
        provider="espn",
        provider_key="tennis/wta",
    ),
    CatalogLeague(
        id="ufc",
        name="UFC",
        sport=Sport.MMA,
        provider="espn",
        provider_key="mma/ufc",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png",
    ),
    # Golf is a leaderboard sport: a tournament is ONE Event with a field,
    # and a followed golfer is a single-member "team" (provider_key = the
    # ESPN athlete id) — the same athlete-as-team design as tennis/MMA.
    # The route derives entity_noun "golfer" from this sport.
    CatalogLeague(
        id="pga",
        name="PGA Tour",
        sport=Sport.GOLF,
        provider="espn",
        provider_key="golf/pga",
        logo_url="https://a.espncdn.com/i/teamlogos/leagues/500/pgatour.png",
    ),
    # --- Volleyball: served by TheSportsDB (the second provider) ---
    # provider_key is the TheSportsDB league id, verified live via
    # search_all_leagues.php?s=Volleyball (free key "3").  These are the
    # European competitions the free tier actually lists; the teams fetch is
    # delegated to tsdb_catalog and tolerates the free tier's sparse data.
    CatalogLeague(
        id="cev-euro-men",
        name="CEV European Championship (Men)",
        sport=Sport.VOLLEYBALL,
        provider="thesportsdb",
        provider_key="5613",
    ),
    CatalogLeague(
        id="evl-men",
        name="European Volleyball League (Men)",
        sport=Sport.VOLLEYBALL,
        provider="thesportsdb",
        provider_key="5848",
    ),
    CatalogLeague(
        id="evl-women",
        name="European Volleyball League (Women)",
        sport=Sport.VOLLEYBALL,
        provider="thesportsdb",
        provider_key="5849",
    ),
)

# ---------------------------------------------------------------------------
# National-team sibling competitions (internal — not offered in the picker)
# ---------------------------------------------------------------------------
#
# Following a nation attaches its FULL international slate via TeamCompetition
# rows: World Cup qualifying (split per confederation), continental-cup
# qualifying, and friendlies.  These leagues are real ESPN soccer codes but
# they are NOT user-pickable wizard entries — a fan follows "England", not
# "FIFA World Cup Qualifying - UEFA".  So they live in a separate tuple that is
# merged into the by-id lookup (so ``get_catalog_league`` and the sibling
# resolver see them) but is kept out of :data:`CATALOG` (the picker list).
#
# Every code below is LIVE-VERIFIED (June 2026) against
# ``site.api.espn.com/.../soccer/{code}/scoreboard`` returning a real
# ``leagues[0].name`` (see NATIONAL_TEAM_COMPETITIONS for the verified names).
# The per-team schedule fetch already filters to the team's own games, so
# attaching a confederation a nation doesn't play in simply yields nothing.
NATIONAL_SIBLING_LEAGUES: tuple[CatalogLeague, ...] = (
    # FIFA World Cup qualifying — one endpoint per confederation.
    CatalogLeague(
        id="worldq-uefa",
        name="World Cup Qualifying — UEFA",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.uefa",
        national=True,
    ),
    CatalogLeague(
        id="worldq-conmebol",
        name="World Cup Qualifying — CONMEBOL",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.conmebol",
        national=True,
    ),
    CatalogLeague(
        id="worldq-concacaf",
        name="World Cup Qualifying — Concacaf",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.concacaf",
        national=True,
    ),
    CatalogLeague(
        id="worldq-afc",
        name="World Cup Qualifying — AFC",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.afc",
        national=True,
    ),
    CatalogLeague(
        id="worldq-caf",
        name="World Cup Qualifying — CAF",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.caf",
        national=True,
    ),
    CatalogLeague(
        id="worldq-ofc",
        name="World Cup Qualifying — OFC",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.worldq.ofc",
        national=True,
    ),
    # Continental-championship qualifying / further continental cups that a
    # nation following Euros/Copa/etc. also plays in.
    CatalogLeague(
        id="euroq",
        name="UEFA Euro Qualifying",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/uefa.euroq",
        national=True,
    ),
    CatalogLeague(
        id="afc-asian-cup",
        name="AFC Asian Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/afc.asian.cup",
        national=True,
    ),
    CatalogLeague(
        id="caf-nations",
        name="Africa Cup of Nations",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/caf.nations",
        national=True,
    ),
    CatalogLeague(
        id="concacaf-gold",
        name="Concacaf Gold Cup",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/concacaf.gold",
        national=True,
    ),
    CatalogLeague(
        id="concacaf-nations",
        name="Concacaf Nations League",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/concacaf.nations.league",
        national=True,
    ),
    # International friendlies — every nation plays these between competitions.
    CatalogLeague(
        id="friendly",
        name="International Friendly",
        sport=Sport.SOCCER,
        provider="espn",
        provider_key="soccer/fifa.friendly",
        national=True,
    ),
)


# The by-id lookup spans the picker catalog *and* the internal sibling leagues,
# so ``get_catalog_league`` / the sibling resolver / the map's per-league
# catalog fetch all see a sibling code, while ``/setup/leagues`` (which iterates
# :data:`CATALOG`) does not offer them as standalone picks.
_CATALOG_BY_ID: dict[str, CatalogLeague] = {
    league.id: league for league in (*CATALOG, *NATIONAL_SIBLING_LEAGUES)
}


def get_catalog_league(league_id: str) -> CatalogLeague | None:
    return _CATALOG_BY_ID.get(league_id)


# ---------------------------------------------------------------------------
# National-team competitions
# ---------------------------------------------------------------------------

# Each national-team picker league maps to the FULL international slate a nation
# followed there should ALSO have fixtures pulled from.  ESPN national-team ids
# are GLOBAL (France = 478 in every competition), so the SAME team
# ``provider_key`` resolves in every sibling context — the scheduler reuses it.
#
# The slate is intentionally broad: a nation persists across tournament cycles,
# so a pick attaches the World Cup + all six confederations' WC qualifying +
# continental cups/qualifying + friendlies.  Attaching a confederation a nation
# doesn't play in is harmless — ``get_schedule`` filters to the team's own
# games, so a non-matching confederation just yields nothing.  Every id below
# resolves in :data:`_CATALOG_BY_ID` (picker league or internal sibling).

# The qualifying / friendly slate every national pick shares — a nation plays
# WC qualifying in exactly one of these six, and friendlies in all windows.
_WORLDQ_AND_FRIENDLIES: tuple[str, ...] = (
    "worldcup",
    "worldq-uefa",
    "worldq-conmebol",
    "worldq-concacaf",
    "worldq-afc",
    "worldq-caf",
    "worldq-ofc",
    "friendly",
)

NATIONAL_TEAM_COMPETITIONS: dict[str, tuple[str, ...]] = {
    # Men's global + continental (UEFA): WC slate, Euros + Euro qualifying,
    # Nations League, friendlies.
    "worldcup": (
        "worldcup",
        "euros",
        "euroq",
        "nations-league",
        *_WORLDQ_AND_FRIENDLIES[1:],
    ),
    "euros": (
        "euros",
        "euroq",
        "nations-league",
        *_WORLDQ_AND_FRIENDLIES,
    ),
    "nations-league": (
        "nations-league",
        "euros",
        "euroq",
        *_WORLDQ_AND_FRIENDLIES,
    ),
    # Copa América: WC slate + the CONMEBOL/Concacaf continental cups + Concacaf
    # Nations League + friendlies (the Copa now mixes both confederations).
    "copa-america": (
        "copa-america",
        "concacaf-gold",
        "concacaf-nations",
        "afc-asian-cup",
        "caf-nations",
        *_WORLDQ_AND_FRIENDLIES,
    ),
    # Women's World Cup: WC slate + friendlies (women's qualifying largely runs
    # through the men's continental codes ESPN reuses; friendlies always apply).
    "womens-worldcup": (
        "womens-worldcup",
        *_WORLDQ_AND_FRIENDLIES,
    ),
}


def national_competition_siblings(league_id: str) -> list[CatalogLeague]:
    """Sibling catalog leagues a nation followed in ``league_id`` plays in.

    Returns the resolved :class:`CatalogLeague` objects for every sibling id
    registered in :data:`NATIONAL_TEAM_COMPETITIONS` (skipping any that are
    not in the catalog, defensively), de-duplicated, order preserved.  Empty
    list when ``league_id`` is not a national-team competition.
    """
    siblings: list[CatalogLeague] = []
    seen: set[str] = set()
    for sibling_id in NATIONAL_TEAM_COMPETITIONS.get(league_id, ()):
        if sibling_id in seen:
            continue
        sibling = _CATALOG_BY_ID.get(sibling_id)
        if sibling is not None:
            seen.add(sibling_id)
            siblings.append(sibling)
    return siblings


# ---------------------------------------------------------------------------
# Teams-in-league fetch
# ---------------------------------------------------------------------------


def _normalize_color(value: Any) -> str | None:
    """ESPN colors are usually bare hex ("1d428a") — prefix with "#"."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("#"):
        return text
    if _BARE_HEX_RE.fullmatch(text):
        return f"#{text}"
    return None


def _parse_team_entry(entry: Any, league: CatalogLeague) -> CatalogTeam | None:
    """Parse one ``teams[]`` entry; None (+warning) if malformed."""
    try:
        if not isinstance(entry, dict):
            raise ValueError("team entry is not an object")
        raw_team = entry.get("team")
        team_obj: dict[str, Any] = raw_team if isinstance(raw_team, dict) else {}
        team_id = str(team_obj.get("id") or "").strip()
        name = team_obj.get("displayName")
        if not team_id or not (isinstance(name, str) and name):
            raise ValueError("missing team id or displayName")

        abbreviation = team_obj.get("abbreviation")
        if not (isinstance(abbreviation, str) and abbreviation):
            abbreviation = name[:3].upper()

        logo_url: str | None = None
        logos = team_obj.get("logos")
        if isinstance(logos, list) and logos and isinstance(logos[0], dict):
            href = logos[0].get("href")
            if isinstance(href, str) and href:
                logo_url = href

        return CatalogTeam(
            provider_key=team_id,
            name=name,
            abbreviation=abbreviation[:8],
            logo_url=logo_url,
            color=_normalize_color(team_obj.get("color")),
        )
    except Exception:
        logger.warning(
            "Skipping malformed ESPN catalog team entry for league %s",
            league.id,
            exc_info=True,
        )
        return None


def _parse_teams_payload(data: Any, league: CatalogLeague) -> list[CatalogTeam]:
    """Extract ``sports[0].leagues[0].teams[]`` defensively."""
    entries: list[Any] = []
    if isinstance(data, dict):
        sports = data.get("sports")
        if isinstance(sports, list) and sports and isinstance(sports[0], dict):
            leagues = sports[0].get("leagues")
            if isinstance(leagues, list) and leagues and isinstance(leagues[0], dict):
                raw = leagues[0].get("teams")
                if isinstance(raw, list):
                    entries = raw
    teams: list[CatalogTeam] = []
    for entry in entries:
        team = _parse_team_entry(entry, league)
        if team is not None:
            teams.append(team)
    return teams


# ---------------------------------------------------------------------------
# Individual sports: an athlete is a single-member "team"
# ---------------------------------------------------------------------------
#
# The normal ``/teams`` endpoint is empty for tennis and MMA.  Instead we read
# the per-tour/division ``/rankings`` endpoint, whose ``rankings[].ranks[]``
# rows each carry an ``athlete`` object (id, displayName, shortname, headshot,
# flag).  A followed athlete becomes a ``CatalogTeam`` with provider_key = the
# ESPN athlete id, so the existing single-member-"team" Game machinery applies.


def _parse_athlete(athlete: Any) -> CatalogTeam | None:
    """Parse one ``ranks[].athlete`` object; None (+warning) if malformed."""
    try:
        if not isinstance(athlete, dict):
            raise ValueError("athlete is not an object")
        athlete_id = str(athlete.get("id") or "").strip()
        name = athlete.get("displayName")
        if not athlete_id or not (isinstance(name, str) and name):
            raise ValueError("missing athlete id or displayName")

        # "J. Sinner" / "K. Usman" makes a tidy short label; fall back to the
        # first few letters of the name when ESPN omits it.
        abbreviation = athlete.get("shortname") or athlete.get("shortName")
        if not (isinstance(abbreviation, str) and abbreviation.strip()):
            abbreviation = name[:3].upper()

        logo_url: str | None = None
        headshot = athlete.get("headshot")
        if isinstance(headshot, str) and headshot:
            logo_url = headshot
        elif isinstance(headshot, dict):  # tolerate the {"href": ...} shape
            href = headshot.get("href")
            if isinstance(href, str) and href:
                logo_url = href

        return CatalogTeam(
            provider_key=athlete_id,
            name=name,
            abbreviation=abbreviation.strip()[:8],
            logo_url=logo_url,
            color=_normalize_color(athlete.get("color")),
        )
    except Exception:
        logger.warning("Skipping malformed ESPN athlete entry", exc_info=True)
        return None


def _parse_rankings_payload(data: Any, league: CatalogLeague) -> list[CatalogTeam]:
    """Extract ranked athletes from a ``/rankings`` payload, deduped by id.

    Tennis tours expose a single ranking block; UFC exposes ~24 (pound-for-
    pound plus a champion/contenders block per division), so the same fighter
    can appear several times — first occurrence wins, original order kept.
    Capped to :data:`_INDIVIDUAL_ROSTER_CAP`.
    """
    blocks: list[Any] = []
    if isinstance(data, dict):
        raw = data.get("rankings")
        if isinstance(raw, list):
            blocks = raw

    athletes: list[CatalogTeam] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        ranks = block.get("ranks")
        if not isinstance(ranks, list):
            continue
        for row in ranks:
            if not isinstance(row, dict):
                continue
            athlete = _parse_athlete(row.get("athlete"))
            if athlete is None or athlete.provider_key in seen:
                continue
            seen.add(athlete.provider_key)
            athletes.append(athlete)
            if len(athletes) >= _INDIVIDUAL_ROSTER_CAP:
                return athletes
    return athletes


# ---------------------------------------------------------------------------
# Golf: a leaderboard sport — the field IS the pickable list of golfers
# ---------------------------------------------------------------------------
#
# Golf has no working ``/teams`` or ``/rankings`` endpoint (both verified
# empty / non-JSON live), so the field is read off the current event's
# scoreboard.  Each ``competitions[0].competitors[]`` entry carries the
# golfer under ``athlete`` (whose ``id`` is null) with the real ESPN
# athlete id on ``competitor.id`` — the same scoreboard shape as tennis/
# MMA.  This means the wizard offers exactly the golfers playing the
# current/most-recent tournament (future commitments are not exposed by
# ESPN — verified), which is the intended "playing this week" semantics.


def _parse_golf_competitor(competitor: Any) -> CatalogTeam | None:
    """Parse one golf scoreboard ``competitors[]`` entry; None if malformed."""
    try:
        if not isinstance(competitor, dict):
            raise ValueError("competitor is not an object")
        # The athlete id lives on the competitor itself (athlete.id is null
        # on the scoreboard, verified live) — same as tennis/MMA.
        athlete_id = str(competitor.get("id") or "").strip()
        raw_athlete = competitor.get("athlete")
        athlete: dict[str, Any] = raw_athlete if isinstance(raw_athlete, dict) else {}
        name = athlete.get("displayName") or athlete.get("fullName")
        if not athlete_id or not (isinstance(name, str) and name):
            raise ValueError("missing athlete id or name")

        abbreviation = athlete.get("shortName") or athlete.get("shortname")
        if not (isinstance(abbreviation, str) and abbreviation.strip()):
            abbreviation = name[:3].upper()

        logo_url: str | None = None
        headshot = athlete.get("headshot")
        if isinstance(headshot, str) and headshot:
            logo_url = headshot
        elif isinstance(headshot, dict):
            href = headshot.get("href")
            if isinstance(href, str) and href:
                logo_url = href

        return CatalogTeam(
            provider_key=athlete_id,
            name=name,
            abbreviation=abbreviation.strip()[:8],
            logo_url=logo_url,
            color=None,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN golf competitor entry", exc_info=True)
        return None


def _parse_golf_field_payload(data: Any, league: CatalogLeague) -> list[CatalogTeam]:
    """Extract the golfer field from a golf scoreboard payload, deduped by id.

    Reads ``events[0].competitions[0].competitors[]`` (the current/most-
    recent tournament's field).  Capped to :data:`_INDIVIDUAL_ROSTER_CAP`
    so a 147-player field still produces a usable picker.
    """
    competitors: list[Any] = []
    if isinstance(data, dict):
        events = data.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            competitions = events[0].get("competitions")
            if (
                isinstance(competitions, list)
                and competitions
                and isinstance(competitions[0], dict)
            ):
                raw = competitions[0].get("competitors")
                if isinstance(raw, list):
                    competitors = raw

    field: list[CatalogTeam] = []
    seen: set[str] = set()
    for entry in competitors:
        golfer = _parse_golf_competitor(entry)
        if golfer is None or golfer.provider_key in seen:
            continue
        seen.add(golfer.provider_key)
        field.append(golfer)
        if len(field) >= _INDIVIDUAL_ROSTER_CAP:
            break
    return field


# league id -> (monotonic fetch time, teams).  Process-local; the wizard
# is the only consumer, so a tiny TTL dict beats dragging Redis in.
_cache: dict[str, tuple[float, list[CatalogTeam]]] = {}


async def _fetch_json(url: str, params: dict[str, str], league_id: str) -> Any:
    """GET ``url`` with a short-lived client; raise EspnCatalogError on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": "SportsDash/1.0"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EspnCatalogError(
            f"Failed to fetch ESPN catalog for league {league_id!r}: {exc}"
        ) from exc


async def get_league_teams(league: CatalogLeague) -> list[CatalogTeam]:
    """List a catalog league's pickable entities from ESPN (1h in-process cache).

    Dispatches on ``league.provider``: ESPN leagues are served here;
    TheSportsDB leagues (the volleyball catalog) are delegated to
    :func:`app.providers.tsdb_catalog.get_tsdb_league_teams`, which keeps its
    own 1h cache.

    For ESPN, team sports use the ``/teams`` endpoint; individual sports
    (tennis, MMA), whose ``/teams`` is empty, fall back to the ``/rankings``
    endpoint and return ranked athletes as single-member "teams"
    (provider_key = athlete id).  Golf (a leaderboard sport) has neither, so
    its field is read off the current tournament's scoreboard competitors.
    Raises :class:`EspnCatalogError` on HTTP/decoding failure so the route can
    map it to a 502 — the same contract holds for both providers.
    """
    if league.provider == "thesportsdb":
        # Imported lazily to avoid a load-time import cycle (tsdb_catalog
        # imports CatalogTeam/EspnCatalogError from this module).
        from app.providers import tsdb_catalog

        return await tsdb_catalog.get_tsdb_league_teams(league)

    cached = _cache.get(league.id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return list(cached[1])

    if league.sport in LEADERBOARD_SPORTS:
        # Golf: neither /teams nor /rankings work; the field is the current
        # tournament's scoreboard competitors (checked before the generic
        # individual-sport branch, as golf is in INDIVIDUAL_SPORTS too).
        data = await _fetch_json(f"{_SITE_BASE}/{league.provider_key}/scoreboard", {}, league.id)
        teams = _parse_golf_field_payload(data, league)
        noun = "golfers"
    elif league.sport in INDIVIDUAL_SPORTS:
        data = await _fetch_json(f"{_SITE_BASE}/{league.provider_key}/rankings", {}, league.id)
        teams = _parse_rankings_payload(data, league)
        noun = "athletes"
    else:
        data = await _fetch_json(
            f"{_SITE_BASE}/{league.provider_key}/teams",
            {"limit": "1000"},
            league.id,
        )
        teams = _parse_teams_payload(data, league)
        noun = "teams"

    if teams:
        _cache[league.id] = (now, teams)
    else:
        # Don't cache an empty result: more likely a payload-shape hiccup
        # than a league with zero entries, and these calls are rare anyway.
        logger.warning("ESPN catalog returned no parseable %s for league %s", noun, league.id)
    return list(teams)
