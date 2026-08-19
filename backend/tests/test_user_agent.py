"""The outbound User-Agent, and the rule that keeps it in one place.

Measured 2026-08-19: ``site.api.espn.com`` answers ``403 Forbidden`` to a
bare ``SportsDash/1.0`` and ``200`` to a string naming the project's
repository, so the URL in this header is load-bearing — losing it empties
schedules, standings, rosters and the team catalog while every hermetic
test in the suite still passes.  That is exactly the failure these tests
exist to make loud.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import useragent
from app.config import get_settings
from app.version import APP_VERSION


def test_default_user_agent_names_the_project_url() -> None:
    assert useragent.user_agent() == f"SportsDash/{APP_VERSION} (+{useragent.PROJECT_URL})"


def test_note_rides_inside_the_comment_without_displacing_the_url() -> None:
    ua = useragent.user_agent("self-hosted")
    assert ua == f"SportsDash/{APP_VERSION} (self-hosted; +{useragent.PROJECT_URL})"
    # The URL is the half upstream keys on; a note may join it, never replace it.
    assert f"+{useragent.PROJECT_URL}" in ua


def test_configured_override_replaces_the_whole_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator setting SPORTSDASH_USER_AGENT means it — notes included."""
    monkeypatch.setattr(get_settings(), "user_agent", "Custom/9.9 (+https://example.invalid)")
    assert useragent.user_agent() == "Custom/9.9 (+https://example.invalid)"
    assert useragent.user_agent("self-hosted") == "Custom/9.9 (+https://example.invalid)"


def test_headers_is_exactly_one_user_agent_header() -> None:
    assert useragent.headers("rss reader") == {"User-Agent": useragent.user_agent("rss reader")}


_UA_LITERAL = re.compile(r"""["']User-Agent["']\s*:\s*["']""")


def test_no_module_hardcodes_its_own_user_agent() -> None:
    """Every outbound header must come from :mod:`app.useragent`.

    The bug this pins is not hypothetical: the string was copied into
    eleven modules, so when ESPN began rejecting it there were eleven
    places to find and no test that noticed any of them.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = sorted(
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "useragent.py" and _UA_LITERAL.search(path.read_text())
    )
    assert offenders == []
