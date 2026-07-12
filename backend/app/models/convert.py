"""ORM row → domain object mappers.

The single home for these conversions — they were previously copy-pasted
across the scheduler, news service, and two routes, and the copies had
already drifted (one dropped ``League.follow_all``).  Any new column on
``LeagueORM``/``TeamORM`` that the domain models carry gets added here,
once.
"""

from __future__ import annotations

from app.models import domain
from app.models.orm import LeagueORM, TeamORM


def league_from_row(row: LeagueORM) -> domain.League:
    return domain.League(
        id=row.id,
        sport=domain.Sport(row.sport),
        name=row.name,
        provider=row.provider,
        provider_key=row.provider_key,
        follow_all=row.follow_all,
    )


def team_from_row(row: TeamORM) -> domain.Team:
    return domain.Team(
        id=row.id,
        league_id=row.league_id,
        name=row.name,
        abbreviation=row.abbreviation,
        provider_key=row.provider_key,
        logo_url=row.logo_url,
        color=row.color,
        rss_feeds=tuple(row.rss_feeds or ()),
    )
