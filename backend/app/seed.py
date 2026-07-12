"""Seed leagues and followed teams (first boot only).

Followed teams live in the DB once onboarded; the YAML config is only a
bootstrap and is consulted exclusively while the teams table is empty.
A missing or broken config file is logged but never prevents the app
from booting — the shipped file is a comments-only template, and the
normal first-run path is the frontend setup wizard (``/setup/follow``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.db import session_scope
from app.models import domain
from app.services import repository

logger = logging.getLogger(__name__)


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"missing required field {key!r}")
    return str(value)


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_league(raw: Any) -> domain.League:
    if not isinstance(raw, dict):
        raise ValueError("league entry must be a mapping")
    return domain.League(
        id=_require_str(raw, "id"),
        sport=domain.Sport(_require_str(raw, "sport")),
        name=_require_str(raw, "name"),
        provider=_require_str(raw, "provider"),
        provider_key=_require_str(raw, "provider_key"),
    )


def _parse_team(raw: Any) -> domain.Team:
    if not isinstance(raw, dict):
        raise ValueError("team entry must be a mapping")
    feeds_raw = raw.get("rss_feeds") or []
    if not isinstance(feeds_raw, list):
        raise ValueError("rss_feeds must be a list of URLs")
    return domain.Team(
        id=_require_str(raw, "id"),
        league_id=_require_str(raw, "league_id"),
        name=_require_str(raw, "name"),
        abbreviation=_require_str(raw, "abbreviation"),
        provider_key=_require_str(raw, "provider_key"),
        logo_url=_optional_str(raw, "logo_url"),
        color=_optional_str(raw, "color"),
        rss_feeds=tuple(str(feed) for feed in feeds_raw),
    )


async def seed_from_config(path: str | None = None) -> None:
    """Parse the teams YAML and upsert leagues + teams via the repository.

    Seeds ONLY while the teams table is empty: after onboarding the DB
    is the source of truth for followed teams, and a stale shipped
    config must never clobber the user's picks.  When an explicit user
    config does seed teams, the app is also marked onboarded — the user
    authored their own config, so the wizard would only get in the way.
    """
    async with session_scope() as session:
        existing = await repository.list_teams(session)
    if existing:
        logger.info(
            "seed_from_config: %d team(s) already in the DB — skipping config seed "
            "(followed teams live in the DB once onboarded)",
            len(existing),
        )
        return

    config_path = Path(path if path is not None else get_settings().teams_config_path)
    if not config_path.is_file():
        logger.error(
            "Teams config not found at %s — no leagues or teams seeded. "
            "Create the file or point SPORTSDASH_TEAMS_CONFIG_PATH at it.",
            config_path.resolve(),
        )
        return

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        logger.exception("Failed to read/parse teams config %s — nothing seeded", config_path)
        return
    if not isinstance(raw, dict):
        logger.error(
            "Teams config %s must be a mapping with 'leagues' and 'teams' lists — nothing seeded",
            config_path,
        )
        return

    leagues_raw = raw.get("leagues") or []
    teams_raw = raw.get("teams") or []
    if not isinstance(leagues_raw, list) or not isinstance(teams_raw, list):
        logger.error(
            "Teams config %s: 'leagues' and 'teams' must be lists — nothing seeded", config_path
        )
        return

    leagues: list[domain.League] = []
    for entry in leagues_raw:
        try:
            leagues.append(_parse_league(entry))
        except Exception:
            logger.exception("Skipping invalid league entry in %s: %r", config_path, entry)

    teams: list[domain.Team] = []
    for entry in teams_raw:
        try:
            teams.append(_parse_team(entry))
        except Exception:
            logger.exception("Skipping invalid team entry in %s: %r", config_path, entry)

    league_ids = {league.id for league in leagues}
    async with session_scope() as session:
        for league in leagues:
            await repository.upsert_league(session, league)
        seeded_teams = 0
        for team in teams:
            if team.league_id not in league_ids:
                existing = await repository.get_league(session, team.league_id)
                if existing is None:
                    logger.error("Skipping team %r: unknown league_id %r", team.id, team.league_id)
                    continue
            await repository.upsert_team(session, team)
            seeded_teams += 1
        if seeded_teams:
            # A user-authored config with actual teams counts as onboarding.
            await repository.set_meta(session, "onboarded", "1")

    logger.info(
        "Seeded %d league(s) and %d team(s) from %s", len(leagues), seeded_teams, config_path
    )
