"""Playoff bracket helpers: round-name normalization + sport support."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app import timeutil
from app.models.domain import Sport
from app.providers import espn_playoffs


def test_round_name_strips_conference_and_game_suffix() -> None:
    assert espn_playoffs._round_name("East 1st Round - Game 5", None) == "1st Round"
    assert espn_playoffs._round_name("West Semifinals - Game 2", None) == "Semifinals"
    assert espn_playoffs._round_name("Western Conference Finals", None) == "Conference Finals"
    # The championship round keeps its name (no conference prefix).
    assert espn_playoffs._round_name("NBA Finals - Game 5", None) == "NBA Finals"
    assert espn_playoffs._round_name("", "Stanley Cup Final") == "Stanley Cup Final"


def test_round_name_relabels_bare_conference_final() -> None:
    # Stripping the conference qualifier leaves a bare "Final"/"Finals" — the
    # conference final, which must stay distinct from the championship.
    assert espn_playoffs._round_name("East Final - Game 1", None) == "Conference Finals"
    assert espn_playoffs._round_name("West Finals - Game 4", None) == "Conference Finals"


def _event(note: str, t1: str, t2: str, summary: str, date: str) -> dict:
    return {
        "date": date,
        "competitions": [
            {
                "date": date,
                "competitors": [
                    {"team": {"displayName": t1, "abbreviation": t1, "logo": None}},
                    {"team": {"displayName": t2, "abbreviation": t2, "logo": None}},
                ],
                "series": {"summary": summary, "title": "Playoff Series"},
                "notes": [{"headline": note}],
            }
        ],
    }


def test_rounds_from_events_groups_orders_and_drops_generic() -> None:
    events = [
        # A mislabeled "Playoff Series" ghost game, dated earliest — must be
        # dropped, not sorted ahead of the 1st round as a junk column.
        _event("", "LAL", "HOU", "LAL win series 4-2", "2026-03-17T01:30Z"),
        _event("West Finals - Game 1", "OKC", "SA", "SA wins series 4-3", "2026-05-21T19:00Z"),
        _event("East 1st Round - Game 1", "NY", "ATL", "NY wins series 4-2", "2026-04-18T19:00Z"),
        _event("West 1st Round - Game 1", "OKC", "PHX", "OKC wins series 4-0", "2026-04-19T19:00Z"),
        _event("NBA Finals - Game 1", "SA", "NY", "NY wins series 4-1", "2026-06-05T19:00Z"),
        _event("East Finals - Game 1", "NY", "CLE", "NY wins series 4-0", "2026-05-20T19:00Z"),
    ]
    rounds = espn_playoffs._rounds_from_events(events)
    # Ordered by when each round first played; the generic ghost round is gone.
    assert [r.name for r in rounds] == ["1st Round", "Conference Finals", "NBA Finals"]
    # East + West halves merged into single round columns.
    assert len(rounds[0].series) == 2
    assert len(rounds[1].series) == 2
    assert rounds[2].series[0].summary == "NY wins series 4-1"


def test_rounds_from_events_keeps_generic_when_only_label() -> None:
    # When ESPN labels nothing specifically, the generic round still renders
    # (degrade gracefully rather than show an empty bracket).
    events = [
        _event("", "CAR", "OTT", "CAR leads series 2-0", "2026-04-18T19:00Z"),
    ]
    rounds = espn_playoffs._rounds_from_events(events)
    assert len(rounds) == 1
    assert len(rounds[0].series) == 1


def test_rounds_from_events_ignores_non_series_games() -> None:
    # Games without a series summary (regular-season / makeup) are skipped.
    events = [
        {
            "date": "2026-04-01T19:00Z",
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "A", "abbreviation": "A"}},
                        {"team": {"displayName": "B", "abbreviation": "B"}},
                    ],
                    "series": {},
                }
            ],
        },
    ]
    assert espn_playoffs._rounds_from_events(events) == []


def test_supports_us_team_sports_only() -> None:
    assert espn_playoffs.supports(Sport.BASKETBALL)
    assert espn_playoffs.supports(Sport.BASEBALL)
    assert espn_playoffs.supports(Sport.HOCKEY)
    assert not espn_playoffs.supports(Sport.SOCCER)


# ---------------------------------------------------------------------------
# fetch_playoff_bracket: a refused day range degrades to month buckets
# ---------------------------------------------------------------------------

# "Today" for the fetch tests: the window is [2026-03-07, 2026-06-17].
_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
_RANGE = "20260307-20260617"


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://x")
    return httpx.HTTPStatusError(
        str(status), request=request, response=httpx.Response(status, request=request)
    )


def _stub_scoreboard(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]
) -> list[dict[str, str]]:
    """Serve ``responses`` (keyed by ``dates``) in place of the network.

    A value that is an exception is raised; a missing key is an empty
    scoreboard.  Returns the list the issued params are recorded in.
    """
    calls: list[dict[str, str]] = []

    async def fake(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> object:
        calls.append(params)
        answer = responses.get(params["dates"], {"events": []})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(espn_playoffs, "_get_scoreboard", fake)
    monkeypatch.setattr(timeutil, "utcnow", lambda: _NOW)
    return calls


async def test_fetch_bracket_uses_the_ranged_call_when_espn_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event("NBA Finals - Game 1", "SA", "NY", "NY leads series 1-0", "2026-06-05T19:00Z")]
    calls = _stub_scoreboard(monkeypatch, {_RANGE: {"events": events}})
    rounds = await espn_playoffs.fetch_playoff_bracket("basketball/nba", Sport.BASKETBALL)
    assert calls == [{"dates": _RANGE, "seasontype": "3", "limit": "1000"}]
    assert [r.name for r in rounds] == ["NBA Finals"]


async def test_fetch_bracket_falls_back_to_month_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESPN's team-sport scoreboards refuse day ranges with a 400; the
    bracket must come from the month buckets instead of going empty."""
    calls = _stub_scoreboard(
        monkeypatch,
        {
            _RANGE: _status_error(400),
            "202603": {
                "events": [
                    # In the bucket but before the window opens (March 7).
                    _event(
                        "Play-In Round", "MIA", "CHI", "MIA wins series 1-0", "2026-03-02T23:00Z"
                    )
                ]
            },
            "202604": {
                "events": [
                    _event(
                        "East 1st Round - Game 5",
                        "NY",
                        "ATL",
                        "NY leads series 3-2",
                        "2026-04-29T23:00Z",
                    )
                ]
            },
            "202605": {
                "events": [
                    _event(
                        "East 1st Round - Game 6",
                        "NY",
                        "ATL",
                        "NY wins series 4-2",
                        "2026-05-02T23:00Z",
                    ),
                    _event(
                        "East Finals - Game 1",
                        "NY",
                        "CLE",
                        "NY wins series 4-0",
                        "2026-05-20T23:00Z",
                    ),
                ]
            },
            "202606": {
                "events": [
                    _event(
                        "NBA Finals - Game 1",
                        "SA",
                        "NY",
                        "NY leads series 1-0",
                        "2026-06-05T23:00Z",
                    )
                ]
            },
        },
    )
    rounds = await espn_playoffs.fetch_playoff_bracket("basketball/nba", Sport.BASKETBALL)
    # One refused ranged attempt, then every month the window touches —
    # still postseason-only, still with the load-bearing limit.
    assert calls == [
        {"dates": _RANGE, "seasontype": "3", "limit": "1000"},
        {"dates": "202603", "seasontype": "3", "limit": "1000"},
        {"dates": "202604", "seasontype": "3", "limit": "1000"},
        {"dates": "202605", "seasontype": "3", "limit": "1000"},
        {"dates": "202606", "seasontype": "3", "limit": "1000"},
    ]
    # The pre-window play-in is trimmed, as the ranged call would have.
    assert [r.name for r in rounds] == ["1st Round", "Conference Finals", "NBA Finals"]
    # Buckets are concatenated oldest-first, so a series spanning two
    # months ends on its LATEST game's summary.
    assert rounds[0].series[0].summary == "NY wins series 4-2"


async def test_fetch_bracket_is_empty_when_a_month_bucket_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-or-nothing: a bracket missing a month of series would mislead,
    so a failed bucket degrades to 'no active bracket' (never raises)."""
    calls = _stub_scoreboard(
        monkeypatch,
        {
            _RANGE: _status_error(400),
            "202604": {
                "events": [
                    _event(
                        "East 1st Round - Game 1",
                        "NY",
                        "ATL",
                        "NY leads series 1-0",
                        "2026-04-18T23:00Z",
                    )
                ]
            },
            "202605": httpx.ConnectError("May bucket down"),
        },
    )
    assert await espn_playoffs.fetch_playoff_bracket("basketball/nba", Sport.BASKETBALL) == []
    assert [params["dates"] for params in calls] == [_RANGE, "202603", "202604", "202605"]


async def test_fetch_bracket_only_a_400_triggers_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_scoreboard(monkeypatch, {_RANGE: _status_error(404)})
    assert await espn_playoffs.fetch_playoff_bracket("basketball/nba", Sport.BASKETBALL) == []
    assert [params["dates"] for params in calls] == [_RANGE]
