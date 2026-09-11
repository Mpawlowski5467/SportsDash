"""ESPN byathlete leaders: label-index + positional value parsing."""

from __future__ import annotations

import httpx
import pytest

from app.models.domain import Sport
from app.providers import espn_leaders


def test_label_index_finds_stat_position() -> None:
    categories = [
        {"name": "general", "labels": ["GP", "MIN"]},
        {"name": "offensive", "labels": ["PTS", "FGM", "FGA"]},
    ]
    assert espn_leaders._label_index(categories, "offensive", "PTS") == 0
    assert espn_leaders._label_index(categories, "offensive", "FGA") == 2
    assert espn_leaders._label_index(categories, "offensive", "REB") is None
    assert espn_leaders._label_index(categories, "missing", "PTS") is None


def test_value_at_reads_positional_total() -> None:
    block = {
        "categories": [
            {"name": "offensive", "totals": ["28.4", "10.1", "21.7"]},
        ]
    }
    assert espn_leaders._value_at(block, "offensive", 0) == 28.4
    assert espn_leaders._value_at(block, "offensive", 2) == 21.7
    # Out-of-range index or missing category -> None.
    assert espn_leaders._value_at(block, "offensive", 9) is None
    assert espn_leaders._value_at(block, "defensive", 0) is None


def test_supports_only_us_team_sports() -> None:
    assert espn_leaders.supports(Sport.BASKETBALL)
    assert espn_leaders.supports(Sport.BASEBALL)
    assert espn_leaders.supports(Sport.HOCKEY)
    assert not espn_leaders.supports(Sport.SOCCER)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", None, {}, "bad"])
def test_nonfinite_or_invalid_statistics_are_skipped(value):
    assert (
        espn_leaders._value_at(
            {"categories": [{"name": "offensive", "totals": [value]}]}, "offensive", 0
        )
        is None
    )


@pytest.mark.parametrize("categories", [None, 12, "bad", {}])
def test_malformed_category_lists_are_skipped(categories):
    assert espn_leaders._value_at({"categories": categories}, "offensive", 0) is None


async def test_leaders_include_provider_headshots_and_skip_bad_records(monkeypatch):
    payload = {
        "categories": [{"name": "offensive", "labels": ["PTS"]}],
        "athletes": [
            {
                "athlete": {
                    "id": "101",
                    "displayName": "Rowan Vale",
                    "headshot": {"href": "https://img.example/rowan.png"},
                },
                "categories": [{"name": "offensive", "totals": [25]}],
            },
            {"athlete": {"id": "102", "displayName": "Sable Marsh"}, "categories": 42},
        ],
    }
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        espn_leaders.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            **kwargs,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)),
        ),
    )
    rows = await espn_leaders.fetch_league_leaders("basketball/harbor", Sport.BASKETBALL)
    assert len(rows) == 1
    assert rows[0].photo_url == "https://img.example/rowan.png"
