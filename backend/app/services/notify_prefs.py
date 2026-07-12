"""Notification-preference resolution — a pure, side-effect-free module.

Given the stored per-scope preferences and the facts about a single
notifiable event (its type, the followed-team ids on the game, and the
game's league), :func:`decide` answers one question: *should this event
notify?*  It has no I/O and no dependency on the ORM beyond a structural
type, so it is exhaustively unit-testable with plain stand-in objects.

Resolution is **most-specific-scope-wins**:

1. Any ``team:{id}`` scope for one of the game's followed teams is the
   most specific signal — if present it decides.
2. Otherwise the game's ``league:{id}`` scope decides.
3. Otherwise the ``global`` scope decides.
4. Absent any matching row (or an absent ``events`` key), the default is
   ENABLED.

Within whichever scope is consulted, ``muted`` blocks every event type,
and an explicit ``events[event_type] is False`` disables just that type.

A game may have two followed teams.  When *either* of them carries a
``team:{id}`` scope, those team scopes — and only those — are the
most-specific layer; the league/global layers are not consulted.  We
define the multi-team rule as: **if any followed-team scope for the game
blocks the event (muted, or events[type] is False), the event is
suppressed**; the event fires only if every present team scope allows
it.  This "any mute wins" choice is deliberate — muting one of two teams
sharing a fixture should reliably silence that fixture for the user who
asked, rather than letting the other team's (possibly default) scope
re-enable it.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from app.models.domain import EventType

# The five notifiable event types, in canonical order.
EVENT_TYPES: tuple[str, ...] = (
    EventType.STARTING_SOON.value,
    EventType.GAME_START.value,
    EventType.PERIOD_START.value,
    EventType.INTERMISSION.value,
    EventType.FINAL.value,
)


@runtime_checkable
class _PrefLike(Protocol):
    """Structural type for a stored preference row.

    Only the two fields resolution needs are required, so tests can pass
    plain stand-ins (or ``NotificationPrefORM`` instances) interchangeably.
    """

    muted: bool
    events: Mapping[str, bool]


def default_events() -> dict[str, bool]:
    """Every notifiable event type enabled — the implicit default scope."""
    return {event_type: True for event_type in EVENT_TYPES}


def follow_all_default_events() -> dict[str, bool]:
    """Seed events for a whole-competition follow.

    Whole competitions carry far more fixtures than a single followed
    team, so only the headline transitions are on by default
    (``game_start`` + ``final``); the noisier per-period events stay off
    until the user opts in.
    """
    return {
        event_type: event_type in (EventType.GAME_START.value, EventType.FINAL.value)
        for event_type in EVENT_TYPES
    }


def _scope_allows(pref: _PrefLike | None, event_type: str) -> bool:
    """Whether a single resolved scope permits this event type.

    ``None`` (no stored row) is the enabled default.  A muted scope blocks
    everything; otherwise an explicit ``events[event_type] is False``
    disables just that type and anything absent stays enabled.
    """
    if pref is None:
        return True
    if pref.muted:
        return False
    return pref.events.get(event_type, True) is not False


def decide(
    prefs_by_scope: Mapping[str, _PrefLike],
    event_type: str,
    team_ids: list[str],
    league_id: str | None,
) -> bool:
    """Resolve whether ``event_type`` should notify for this game.

    ``prefs_by_scope`` maps scope strings (``"global"``, ``"team:{id}"``,
    ``"league:{id}"``) to preference rows.  ``team_ids`` are the game's
    followed-team ids (drop ``None`` before calling).  See the module
    docstring for the full resolution rules.
    """
    team_scopes = [
        prefs_by_scope[f"team:{team_id}"]
        for team_id in team_ids
        if f"team:{team_id}" in prefs_by_scope
    ]
    if team_scopes:
        # Most specific layer: any blocking team scope suppresses the event.
        return all(_scope_allows(pref, event_type) for pref in team_scopes)

    if league_id is not None:
        league_pref = prefs_by_scope.get(f"league:{league_id}")
        if league_pref is not None:
            return _scope_allows(league_pref, event_type)

    return _scope_allows(prefs_by_scope.get("global"), event_type)
