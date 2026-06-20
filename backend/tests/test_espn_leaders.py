"""ESPN byathlete leaders: label-index + positional value parsing."""
from __future__ import annotations

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
