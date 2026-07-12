"""Playoff bracket helpers: round-name normalization + sport support."""

from __future__ import annotations

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
