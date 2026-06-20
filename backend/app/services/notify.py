"""Push notifications via a self-hosted ntfy server.

Sends are best-effort: any failure is logged and reported through the
boolean return value, never raised, so a flaky ntfy server can never
break the live polling loop.  When ``notifications_enabled`` is False
the send is skipped and treated as handled (returns True) so callers
mark the event as notified and never retry it.
"""
from __future__ import annotations

import logging

import httpx

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
}

_HIGH_PRIORITY_EVENTS: frozenset[EventType] = frozenset(
    {EventType.GAME_START, EventType.FINAL}
)


async def send(
    title: str,
    message: str,
    *,
    tags: str | None = None,
    priority: str | None = None,
) -> bool:
    """POST a notification to ``{ntfy_url}/{ntfy_topic}``.

    The message is the UTF-8 request body; ``Title`` (and optionally
    ``Tags`` / ``Priority``) travel as headers, plus a bearer
    ``Authorization`` header when ``ntfy_token`` is configured.  Returns
    True on success, False (after logging) on any failure.
    """
    settings = get_settings()
    if not settings.notifications_enabled:
        logger.debug("Notifications disabled; skipping %r", title)
        return True

    url = f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic}"
    headers: dict[str, str] = {"Title": title}
    if tags:
        headers["Tags"] = tags
    if priority:
        headers["Priority"] = priority
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url, content=message.encode("utf-8"), headers=headers
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — notification failures must never propagate
        logger.warning(
            "Failed to send ntfy notification %r to %s: %s", title, url, exc
        )
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
