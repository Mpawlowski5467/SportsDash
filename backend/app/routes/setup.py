"""Setup & onboarding endpoints (the first-run wizard's backend).

The only routes in the app that write: a successful follow install
replaces the followed set, marks the app onboarded, commits explicitly,
then kicks an immediate background ``daily_refresh`` so the freshly
followed teams' data starts loading right away.

The catalog accessor and the refresh kick are called through their
modules (``espn_catalog.…`` / ``jobs.…``) — never imported as bare
names — so tests can monkeypatch them at the source.
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
from app.providers import espn_catalog
from app.providers.espn_catalog import CatalogLeague, CatalogTeam, EspnCatalogError
from app.scheduler import jobs
from app.schemas import (
    CatalogLeagueOut,
    CatalogLeaguesOut,
    CatalogTeamOut,
    CatalogTeamsOut,
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
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "team"


def _teams_out(
    leagues: Sequence[domain.League], teams: Sequence[domain.Team]
) -> TeamsOut:
    """Shape a ``TeamsOut`` from exactly what was just written."""
    return TeamsOut(
        leagues=[
            LeagueOut(
                id=league.id,
                sport=league.sport.value,
                name=league.name,
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
        raise HTTPException(
            status_code=502, detail="Failed to fetch teams from ESPN"
        ) from None


@router.get("/setup/status", response_model=SetupStatusOut)
async def setup_status(
    session: AsyncSession = Depends(get_session),
) -> SetupStatusOut:
    onboarded = await repository.get_meta(session, _ONBOARDED_KEY)
    teams = await repository.list_teams(session)
    return SetupStatusOut(
        onboarded=onboarded == _ONBOARDED_VALUE, followed_team_count=len(teams)
    )


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
                    "player" if league.sport is domain.Sport.TENNIS
                    else "fighter" if league.sport is domain.Sport.MMA
                    else "golfer" if league.sport is domain.Sport.GOLF
                    else "team"
                ),
                logo_url=getattr(league, "logo_url", None),
            )
            for league in espn_catalog.CATALOG
        ]
    )


@router.get("/setup/teams/{league_id}", response_model=CatalogTeamsOut)
async def setup_teams(league_id: str) -> CatalogTeamsOut:
    league = espn_catalog.get_catalog_league(league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="Unknown league")
    teams = await _fetch_catalog_teams(league)
    return CatalogTeamsOut(
        league_id=league.id,
        teams=[
            CatalogTeamOut(
                provider_key=team.provider_key,
                name=team.name,
                abbreviation=team.abbreviation,
                logo_url=team.logo_url,
                color=team.color,
            )
            for team in teams
        ],
    )


@router.post("/setup/follow", response_model=TeamsOut)
async def setup_follow(
    request: FollowRequest, session: AsyncSession = Depends(get_session)
) -> TeamsOut:
    if not request.selections:
        raise HTTPException(status_code=400, detail="No leagues selected")

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

        catalog_teams = await _fetch_catalog_teams(catalog_league)
        by_key = {team.provider_key: team for team in catalog_teams}
        unknown = [key for key in team_keys if key not in by_key]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown team key(s) for league {selection.league_id!r}: "
                    + ", ".join(unknown)
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

        for key in team_keys:
            entry = by_key[key]
            team_id = f"{catalog_league.id}-{_slugify(entry.name)}"[:64]
            teams.append(
                domain.Team(
                    id=team_id,
                    league_id=catalog_league.id,
                    name=entry.name,
                    abbreviation=entry.abbreviation,
                    provider_key=entry.provider_key,
                    logo_url=entry.logo_url,
                    color=entry.color,
                )
            )
            for sibling in siblings:
                if sibling.id == catalog_league.id:
                    continue
                competitions.append((team_id, sibling.id, entry.provider_key))

    leagues = list(leagues_by_id.values())
    await repository.replace_followed(session, leagues, teams, competitions)
    await repository.set_meta(session, _ONBOARDED_KEY, _ONBOARDED_VALUE)
    # Whole-competition follows carry far more fixtures than a single team,
    # so seed their league scope with only the headline events on
    # (game_start + final) to avoid notification spam.  Followed teams need
    # no seeded row — the absent-row default is all-events-on.  Stays in the
    # same transaction as the re-follow (replace_followed already wiped any
    # stale pref rows).
    for league in leagues:
        if league.follow_all:
            await repository.upsert_notification_pref(
                session,
                scope=f"league:{league.id}",
                events=follow_all_default_events(),
            )
    await session.commit()
    jobs.kick_daily_refresh()
    logger.info(
        "Setup follow: now following %d team(s) across %d league(s)",
        len(teams),
        len(leagues),
    )
    return _teams_out(leagues, teams)
