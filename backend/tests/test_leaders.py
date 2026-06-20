"""Leaders board: parsing the headline stat from a roster line."""
from __future__ import annotations

from app.routes.leaders import _leading_stat


def test_leading_stat_parses_value_and_label() -> None:
    assert _leading_stat("3 G · 1 A") == (3.0, "G")
    assert _leading_stat("24.1 PPG · 7.8 REB · 5.2 AST") == (24.1, "PPG")
    assert _leading_stat("0.312 AVG · 12 HR") == (0.312, "AVG")
    # A zero is a real (rankable) value, not a missing one.
    assert _leading_stat("0 G · 0 A") == (0.0, "G")


def test_leading_stat_none_when_unparseable() -> None:
    assert _leading_stat(None) is None
    assert _leading_stat("") is None
    assert _leading_stat("No stats available") is None
