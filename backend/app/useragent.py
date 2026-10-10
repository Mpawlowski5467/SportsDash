"""The one outbound ``User-Agent``, and why it has to name the project.

ESPN's edge rejects a bare product token.  Measured 2026-08-19: every
request to ``site.api.espn.com`` carrying ``SportsDash/1.0`` came back
``403 Forbidden`` — deterministically, 5/5, from the same address that got
``200`` seconds later for a different string — which silently emptied
schedules, scoreboards, standings, rosters and the team catalog.  What
passes is a User-Agent naming the project's repository:

    SportsDash/1.0                                  -> 403
    SportsDash/1.4.0                                -> 403
    SportsDash/1.0 (self-hosted)                    -> 403
    SportsDash/1.0 (+https://example.com)           -> 403
    SportsDash/1.0 (+<PROJECT_URL>)                 -> 200

So it is the project URL specifically, not merely the presence of a
comment, and ``sports.core.api.espn.com`` never blocked at all — only the
``site.api`` host.  Nominatim's usage policy asks for the same thing from
the other direction (a descriptive agent naming the application, or it
blocks), so one string satisfies both and every call site can stop
inventing its own.

``SPORTSDASH_USER_AGENT`` overrides the whole string, because this is a
rule on someone else's edge that can move again: recovering from the next
one should be a config change, not an edit to six modules.
"""

from __future__ import annotations

from app.config import get_settings
from app.version import APP_VERSION

PROJECT_URL = "https://github.com/Mpawlowski5467/SportsDash"


def user_agent(note: str | None = None) -> str:
    """Return the outbound User-Agent, optionally with a descriptive ``note``.

    The note is a courtesy to whoever reads the far end's logs ("what is
    this thing doing on my API?") and rides *inside* the same comment as the
    project URL, which is the part upstream edges actually key on.  A
    configured ``SPORTSDASH_USER_AGENT`` replaces the string outright,
    note included — an operator overriding it has a reason, and quietly
    appending to their value would defeat it.
    """
    configured = get_settings().user_agent
    if configured:
        return configured
    comment = f"{note}; +{PROJECT_URL}" if note else f"+{PROJECT_URL}"
    return f"SportsDash/{APP_VERSION} ({comment})"


def headers(note: str | None = None) -> dict[str, str]:
    """The User-Agent as a one-key header dict, ready for httpx."""
    return {"User-Agent": user_agent(note)}
