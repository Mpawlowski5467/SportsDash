"""Setup & onboarding endpoints (the setup wizard's backend).

The only routes in the app that write: a successful follow install
reconciles the stored followed set with the one submitted — whatever is
still followed keeps its rows, and only what was unfollowed is removed —
marks the app onboarded, commits explicitly, then kicks an immediate
background ``daily_refresh`` so newly followed teams' data starts
loading right away.

The wizard serves the first run and every later edit, so an edit starts
from ``GET /setup/selection``: the followed set read back as the
selections that would produce it.

The catalog accessor and the scheduler hooks (the refresh kick, the
refresh-running flag) are called through their modules
(``espn_catalog.…`` / ``jobs.…``) — never imported as bare names — so
tests can monkeypatch them at the source.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import domain
from app.models.orm import NotificationPrefORM, TeamORM
from app.providers import espn_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam, EspnCatalogError
from app.scheduler import jobs
from app.schemas import (
    CatalogCountryOut,
    CatalogLeagueOut,
    CatalogLeaguesOut,
    CatalogTeamOut,
    CatalogTeamsOut,
    CurrentSelectionOut,
    CurrentSelectionsOut,
    FollowRequest,
    LeagueOut,
    SetupStatusOut,
    TeamOut,
    TeamsOut,
)
from app.services import repository
from app.services.notify_prefs import follow_all_default_events

logger = logging.getLogger(__name__)

router = APIRouter()

# Meta key/value marking first-run setup as complete (per CONTRACTS.md).
_ONBOARDED_KEY = "onboarded"
_ONBOARDED_VALUE = "1"


def _slugify(name: str) -> str:
    """Lowercase ASCII slug for internal team ids (``nba-harborlight-pelicans``)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "team"


def _teams_out(leagues: Sequence[domain.League], teams: Sequence[domain.Team]) -> TeamsOut:
    """Shape a ``TeamsOut`` from exactly what was just written."""
    return TeamsOut(
        leagues=[
            LeagueOut(
                id=league.id,
                sport=league.sport.value,
                name=league.name,
                provider=league.provider,
                follow_all=league.follow_all,
            )
            for league in leagues
        ],
        teams=[
            TeamOut(
                id=team.id,
                league_id=team.league_id,
                name=team.name,
                abbreviation=team.abbreviation,
                logo_url=team.logo_url,
                color=team.color,
            )
            for team in teams
        ],
    )


async def _fetch_catalog_teams(league: CatalogLeague) -> list[CatalogTeam]:
    """Fetch a league's catalog teams, mapping upstream failure to a 502."""
    try:
        return await espn_catalog.get_league_teams(league)
    except EspnCatalogError:
        logger.exception("Catalog team fetch failed for league %r", league.id)
        raise HTTPException(status_code=502, detail="Failed to fetch teams from ESPN") from None


def _catalog_team_out(team: CatalogTeam | TeamORM) -> CatalogTeamOut:
    """A picker card, from the live catalog or from a followed team's row."""
    return CatalogTeamOut(
        provider_key=team.provider_key,
        name=team.name,
        abbreviation=team.abbreviation,
        logo_url=team.logo_url,
        color=team.color,
    )


def _is_untouched_seed(pref: NotificationPrefORM) -> bool:
    """Whether a ``league:{id}`` pref still holds only what the follow-all seed wrote.

    Compared key by key rather than for equality with today's seed: the
    seed has grown with the event types (older builds wrote five keys,
    today's six), and a row seeded back then is no less untouched for
    lacking the newer one.  A muted row, or one with any toggle off the
    seed's value, is the user's own and is kept; so is an empty one, which
    no seed ever wrote and which reads as all-on anyway.
    """
    seed = follow_all_default_events()
    events = pref.events or {}
    return (
        not pref.muted
        and bool(events)
        and all(enabled == seed.get(event_type) for event_type, enabled in events.items())
    )


@router.get("/setup/status", response_model=SetupStatusOut)
async def setup_status(
    session: AsyncSession = Depends(get_session),
) -> SetupStatusOut:
    onboarded = await repository.get_meta(session, _ONBOARDED_KEY)
    teams = await repository.list_teams(session)
    return SetupStatusOut(
        onboarded=onboarded == _ONBOARDED_VALUE,
        followed_team_count=len(teams),
        refreshing=jobs.daily_refresh_running(),
    )


@router.get("/setup/selection", response_model=CurrentSelectionsOut)
async def setup_selection(
    session: AsyncSession = Depends(get_session),
) -> CurrentSelectionsOut:
    """The followed set as wizard selections — what an edit starts from.

    Database only, never the provider: the editor has to open while ESPN
    is down or slow, and has to show a pick the live catalog no longer
    lists.  Saved back through ``POST /setup/follow`` as it stands (each
    team by its provider key), it changes nothing.

    A stored league is a selection only if the user chose it: the picker
    offers it, and it is followed in whole or owns a followed team.  That
    leaves out the sibling competitions a national-team pick attaches on
    its own — they are neither, every save re-derives them, and sent back
    they would be rejected as empty selections.  "Offered" is membership
    of ``CATALOG`` itself: ``get_catalog_league`` also resolves the
    internal sibling leagues the picker never lists.
    """
    offered = {league.id for league in espn_catalog.CATALOG}
    teams_by_league: dict[str, list[CatalogTeamOut]] = {}
    for team in await repository.list_teams(session):
        teams_by_league.setdefault(team.league_id, []).append(_catalog_team_out(team))

    selections: list[CurrentSelectionOut] = []
    for league in await repository.list_leagues(session):
        if league.id not in offered:
            continue
        if league.follow_all:
            # No teams, even if some are stored under it (the follow route
            # merges two selections of one league): the wizard's two modes
            # are exclusive, and whole-competition is the one that wins.
            selections.append(CurrentSelectionOut(league_id=league.id, follow_all=True))
        elif league.id in teams_by_league:
            selections.append(
                CurrentSelectionOut(league_id=league.id, teams=teams_by_league[league.id])
            )
    return CurrentSelectionsOut(selections=selections)


@router.get("/setup/leagues", response_model=CatalogLeaguesOut)
async def setup_leagues() -> CatalogLeaguesOut:
    """The static league catalog — no network, safe to call any time."""
    return CatalogLeaguesOut(
        leagues=[
            CatalogLeagueOut(
                id=league.id,
                name=league.name,
                sport=league.sport.value,
                provider=league.provider,
                national=league.national,
                supports_follow_all=league.supports_follow_all,
                entity_noun=(
                    "player"
                    if league.sport is domain.Sport.TENNIS
                    else "fighter"
                    if league.sport is domain.Sport.MMA
                    else "golfer"
                    if league.sport is domain.Sport.GOLF
                    else "driver"
                    if league.sport is domain.Sport.RACING
                    else "team"
                ),
                logo_url=getattr(league, "logo_url", None),
                country_code=espn_catalog.league_country_code(league),
            )
            for league in espn_catalog.CATALOG
        ],
        countries=[
            CatalogCountryOut(
                code=country.code,
                name=country.name,
                league_count=sum(
                    1
                    for league in espn_catalog.CATALOG
                    if espn_catalog.league_country_code(league) == country.code
                ),
            )
            for country in espn_catalog.countries_with_leagues()
        ],
    )


@router.get("/setup/teams/{league_id}", response_model=CatalogTeamsOut)
async def setup_teams(league_id: str) -> CatalogTeamsOut:
    league = espn_catalog.get_catalog_league(league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")
    teams = await _fetch_catalog_teams(league)
    return CatalogTeamsOut(
        league_id=league.id,
        teams=[_catalog_team_out(team) for team in teams],
    )


@router.post("/setup/follow", response_model=TeamsOut)
async def setup_follow(
    request: FollowRequest, session: AsyncSession = Depends(get_session)
) -> TeamsOut:
    if not request.selections:
        raise HTTPException(status_code=400, detail="No leagues selected")

    # The set as it stands, read before anything is written.  A team that is
    # already followed is recognised by its league and provider key — the
    # provider's own identity for it, where the id is a slug of a display
    # name the provider is free to change.  The leagues followed in whole
    # are what later tells one that has just become a whole-competition
    # follow from one that already was (see the pref seeding below).
    stored = {
        (team.league_id, team.provider_key): team for team in await repository.list_teams(session)
    }
    was_follow_all = {league.id for league in await repository.list_follow_all_leagues(session)}

    # Both maps are keyed by catalog id so a sibling competition upserted by
    # one national-team pick and a directly-followed league coincide cleanly.
    leagues_by_id: dict[str, domain.League] = {}
    teams: list[domain.Team] = []
    competitions: list[tuple[str, str, str]] = []
    for selection in request.selections:
        catalog_league = espn_catalog.get_catalog_league(selection.league_id)
        if catalog_league is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown league id {selection.league_id!r}"
            )

        # Whole-competition follow: no team picks needed, no team validation.
        if selection.follow_all:
            leagues_by_id[catalog_league.id] = domain.League(
                id=catalog_league.id,
                sport=catalog_league.sport,
                name=catalog_league.name,
                provider=catalog_league.provider,
                provider_key=catalog_league.provider_key,
                follow_all=True,
            )
            continue

        # De-dupe while preserving the user's pick order.
        team_keys = list(dict.fromkeys(selection.team_provider_keys))
        if not team_keys:
            # Neither teams nor follow_all — an empty, meaningless selection.
            raise HTTPException(
                status_code=400,
                detail=f"No teams selected for league {selection.league_id!r}",
            )

        # Only a pick that is not followed yet needs the live catalog: a
        # stored team already holds everything the catalog would supply.  So
        # an edit that adds no team saves with the provider down, and one
        # that does add keeps the picks the catalog has since dropped —
        # which is routine, not rare: the golf catalog is the current
        # tournament's field, and the tennis and MMA ones are capped
        # rankings a followed athlete slides out of.
        by_key: dict[str, CatalogTeam] = {}
        if any((catalog_league.id, key) not in stored for key in team_keys):
            catalog_teams = await _fetch_catalog_teams(catalog_league)
            by_key = {team.provider_key: team for team in catalog_teams}

        # Each pick with the stored row it keeps, if it has one.  Its display
        # fields follow the catalog whenever the catalog lists it, so a
        # rebrand or a new crest reaches a team that is already followed; a
        # stored team the catalog has dropped stays as it was saved.  A key
        # that is neither listed nor stored is the only kind left unknown.
        picks: list[tuple[CatalogTeam | TeamORM, TeamORM | None]] = []
        unknown: list[str] = []
        for key in team_keys:
            kept = stored.get((catalog_league.id, key))
            shown = by_key.get(key) or kept
            if shown is None:
                unknown.append(key)
            else:
                picks.append((shown, kept))
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown team key(s) for league {selection.league_id!r}: " + ", ".join(unknown)
                ),
            )

        # A directly-followed league must not lose its follow_all flag if a
        # later selection also follows the whole competition; merge instead.
        existing = leagues_by_id.get(catalog_league.id)
        leagues_by_id[catalog_league.id] = domain.League(
            id=catalog_league.id,
            sport=catalog_league.sport,
            name=catalog_league.name,
            provider=catalog_league.provider,
            provider_key=catalog_league.provider_key,
            follow_all=bool(existing and existing.follow_all),
        )

        # National-team picks also pull fixtures from their sibling
        # competitions; ESPN team ids are global, so the same provider_key is
        # reused, and every sibling league is upserted alongside the primary.
        siblings = espn_catalog.national_competition_siblings(catalog_league.id)
        for sibling in siblings:
            leagues_by_id.setdefault(
                sibling.id,
                domain.League(
                    id=sibling.id,
                    sport=sibling.sport,
                    name=sibling.name,
                    provider=sibling.provider,
                    provider_key=sibling.provider_key,
                ),
            )

        for shown, kept in picks:
            # A team that is already followed keeps its id, because every
            # row it owns hangs off it and a fresh slug would move with the
            # provider's display name; and its feeds, which only its row
            # holds — the catalog has none, and the upsert writes the field.
            if kept is not None:
                team_id = kept.id
                rss_feeds = tuple(kept.rss_feeds or ())
            else:
                team_id = f"{catalog_league.id}-{_slugify(shown.name)}"[:64]
                rss_feeds = ()
            teams.append(
                domain.Team(
                    id=team_id,
                    league_id=catalog_league.id,
                    name=shown.name,
                    abbreviation=shown.abbreviation,
                    provider_key=shown.provider_key,
                    logo_url=shown.logo_url,
                    color=shown.color,
                    rss_feeds=rss_feeds,
                )
            )
            for sibling in siblings:
                if sibling.id == catalog_league.id:
                    continue
                competitions.append((team_id, sibling.id, shown.provider_key))

    leagues = list(leagues_by_id.values())
    await repository.replace_followed(session, leagues, teams, competitions)
    await repository.set_meta(session, _ONBOARDED_KEY, _ONBOARDED_VALUE)
    # Whole-competition follows carry far more fixtures than a single team,
    # so their league scope starts with only the headline events on
    # (game_start + final) to avoid notification spam.  Followed teams need
    # no seeded row — the absent-row default is all-events-on.  Stays in the
    # same transaction as the save.
    #
    # The seed is written once, when a league FIRST becomes a
    # whole-competition follow and has no row of its own: replace_followed
    # keeps the prefs of everything still followed, and the upsert merges,
    # so seeding on every save would switch back off whatever the user has
    # since switched on.  The other direction needs undoing as well.  Left
    # on a league that is now followed by team, the seed would go on
    # silencing all but the start and final alerts of those teams (the
    # league row decides for any team with no row of its own), so it is
    # removed — unless the user has made the row their own.
    prefs = await repository.prefs_by_scope(session)
    for league in leagues:
        scope = f"league:{league.id}"
        pref = prefs.get(scope)
        if league.follow_all:
            if league.id not in was_follow_all and pref is None:
                await repository.upsert_notification_pref(
                    session,
                    scope=scope,
                    events=follow_all_default_events(),
                )
        elif league.id in was_follow_all and pref is not None and _is_untouched_seed(pref):
            await session.delete(pref)
    await session.commit()
    jobs.kick_daily_refresh()
    logger.info(
        "Setup follow: now following %d team(s) across %d league(s)",
        len(teams),
        len(leagues),
    )
    return _teams_out(leagues, teams)
