"""Push notifications via a self-hosted ntfy server.

Sends are best-effort: any failure is logged and reported through the
boolean return value, never raised, so a flaky ntfy server can never
break the live polling loop.  When ``notifications_enabled`` is False
the send is skipped and treated as handled (returns True) so callers
mark the event as notified and never retry it.

Notifications are published in ntfy's JSON form (ntfy 1.18.0 and later)
rather than as ``Title`` / ``Tags`` / ``Priority`` headers.  httpx encodes
``str`` header values as ASCII, so a title such as "Getafe @ Atlético
Madrid" raised UnicodeEncodeError before the request left the process —
and a FINAL, retried every tick for the resend window, failed every time.
Nothing provider-derived travels in a header now.
"""

from __future__ import annotations

import logging

import httpx

from app.services import http_client

from app.config import get_settings
from app.models.domain import EventType, GameEvent

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = httpx.Timeout(10.0)

_EVENT_TAGS: dict[EventType, str] = {
    EventType.STARTING_SOON: "alarm_clock",
    EventType.GAME_START: "stopwatch",
    EventType.PERIOD_START: "arrows_counterclockwise",
    EventType.INTERMISSION: "pause_button",
    EventType.FINAL: "checkered_flag",
    EventType.PLAYER_STATUS: "adhesive_bandage",
}

_HIGH_PRIORITY_EVENTS: frozenset[EventType] = frozenset({EventType.GAME_START, EventType.FINAL})

# The JSON form takes the priority as ntfy's integer level, not the name
# the ``Priority`` header accepted.
_PRIORITY_LEVELS: dict[str, int] = {
    "min": 1,
    "low": 2,
    "default": 3,
    "high": 4,
    "max": 5,
    "urgent": 5,
}


async def send(
    title: str,
    message: str,
    *,
    tags: str | None = None,
    priority: str | None = None,
) -> bool:
    """POST a notification as JSON to the ntfy server root.

    The body carries ``topic`` (``ntfy_topic``), ``title`` and ``message``;
    ``tags`` (the comma-separated argument, as a list) and ``priority``
    (the name, as ntfy's 1-5 level) are added only when they hold a value
    ntfy can take.  The only header set here is a bearer ``Authorization``
    when ``ntfy_token`` is configured.  Returns True on success, False
    (after logging) on any failure.
    """
    settings = get_settings()
    if not settings.notifications_enabled:
        logger.debug("Notifications disabled; skipping %r", title)
        return True

    # ntfy reads a JSON publish only at its root, never at the topic URL.
    url = f"{settings.ntfy_url.rstrip('/')}/"
    payload: dict[str, object] = {
        "topic": settings.ntfy_topic,
        "title": title,
        "message": message,
    }
    tag_list = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    if tag_list:
        payload["tags"] = tag_list
    # An unknown name is dropped, not forwarded: the field must be an integer.
    level = _PRIORITY_LEVELS.get(priority or "")
    if level is not None:
        payload["priority"] = level
    headers: dict[str, str] = {}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    try:
        client = http_client.get_client("ntfy", timeout=_REQUEST_TIMEOUT)
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — notification failures must never propagate
        logger.warning("Failed to send ntfy notification %r to %s: %s", title, url, exc)
        return False

    logger.debug("Sent ntfy notification %r to %s", title, url)
    return True


async def send_event(event: GameEvent) -> bool:
    """Send a :class:`GameEvent` with type-appropriate tags and priority.

    GAME_START and FINAL go out at high priority; every event type maps
    to a fitting ntfy tag (emoji shortcode).
    """
    priority = "high" if event.type in _HIGH_PRIORITY_EVENTS else None
    return await send(
        event.title,
        event.message,
        tags=_EVENT_TAGS.get(event.type),
        priority=priority,
    )
