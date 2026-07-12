"""Notification-preference endpoints (Phase 3b).

Two routes power the Settings panel:

- ``GET /notifications/prefs`` returns one :class:`NotificationPrefOut`
  per *configurable scope* — ``global`` plus every followed team and
  every followed league (whole-competition follows included) — so the
  UI can render a full grid even for scopes that have no stored row yet.
  Each scope reports its own resolved per-event booleans (a type is
  enabled unless that scope explicitly stored it ``False``); cross-scope
  resolution (team → league → global) is the live tick's job, not the
  editor's.
- ``PUT /notifications/prefs`` merges one scope's update, commits, and
  returns the freshly rebuilt full set.

This is the only writing route outside ``setup`` — it commits
explicitly (read-only routes never do).  Scope-construction and the
event-type ordering both come from :mod:`app.services.notify_prefs` so
the resolver and the editor never disagree about the canonical set.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.orm import LeagueORM, NotificationPrefORM, TeamORM
from app.schemas import NotificationPrefOut, NotificationPrefsOut, NotificationPrefUpdate
from app.services import notify_prefs, repository

logger = logging.getLogger(__name__)

router = APIRouter()

#: Label shown for the catch-all ``global`` scope.
_GLOBAL_LABEL = "All notifications"


def _events_for_scope(pref: NotificationPrefORM | None) -> dict[str, bool]:
    """Resolved per-event booleans for a single scope's editor row.

    Every notifiable type is enabled unless this scope explicitly stored
    it ``False`` — so the UI always shows the real state of all five
    types, whether or not a row exists.
    """
    stored = pref.events if pref is not None else {}
    return {
        event_type: stored.get(event_type, True) is not False
        for event_type in notify_prefs.EVENT_TYPES
    }


def _pref_out(scope: str, label: str, pref: NotificationPrefORM | None) -> NotificationPrefOut:
    return NotificationPrefOut(
        scope=scope,
        label=label,
        muted=bool(pref.muted) if pref is not None else False,
        events=_events_for_scope(pref),
    )


async def _build_prefs(session: AsyncSession) -> NotificationPrefsOut:
    """Assemble the full editable set: global + every followed team/league.

    Stored rows fill in real values; configurable scopes with no row yet
    fall back to the all-on / un-muted default.
    """
    prefs_by_scope = {row.scope: row for row in await repository.get_notification_prefs(session)}
    leagues: list[LeagueORM] = await repository.list_leagues(session)
    teams: list[TeamORM] = await repository.list_teams(session)

    prefs = [_pref_out("global", _GLOBAL_LABEL, prefs_by_scope.get("global"))]
    for team in teams:
        scope = f"team:{team.id}"
        prefs.append(_pref_out(scope, team.name, prefs_by_scope.get(scope)))
    for league in leagues:
        scope = f"league:{league.id}"
        prefs.append(_pref_out(scope, league.name, prefs_by_scope.get(scope)))

    return NotificationPrefsOut(event_types=list(notify_prefs.EVENT_TYPES), prefs=prefs)


@router.get("/notifications/prefs", response_model=NotificationPrefsOut)
async def get_notification_prefs(
    session: AsyncSession = Depends(get_session),
) -> NotificationPrefsOut:
    return await _build_prefs(session)


@router.put("/notifications/prefs", response_model=NotificationPrefsOut)
async def put_notification_pref(
    update: NotificationPrefUpdate,
    session: AsyncSession = Depends(get_session),
) -> NotificationPrefsOut:
    """Merge one scope's preferences, commit, return the full refreshed set."""
    await repository.upsert_notification_pref(
        session, update.scope, muted=update.muted, events=update.events
    )
    await session.commit()
    logger.info("Updated notification prefs for scope %r", update.scope)
    return await _build_prefs(session)
