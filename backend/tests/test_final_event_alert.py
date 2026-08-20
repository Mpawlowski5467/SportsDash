"""The FINAL alert for a followed golfer/driver, and the verb it picks.

``_final_event`` is pure — a domain Event in, a GameEvent out — so these
need no database, network or event loop.  All driver and series names are
fictional.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.domain import Event, GamePhase, LeaderRow
from app.scheduler.live import _final_event

RACE_ID = "mock:gp-2026-0817"
GOLF_ID = "mock:open-2026-0719"


def make_event(*, event_id: str = RACE_ID, rows: list[LeaderRow]) -> Event:
    return Event(
        id=event_id,
        league_id="pinnacle-racing",
        name="Karst City Grand Prix",
        start_time=datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc),
        phase=GamePhase.FINAL,
        leaderboard=tuple(rows),
    )


def row(
    position: int, name: str, score: str, *, followed: bool = False, label: str | None = None
) -> LeaderRow:
    return LeaderRow(
        position=position,
        position_label=label if label is not None else str(position),
        name=name,
        score=score,
        player_id=f"racing-{name.lower().replace(' ', '-')}" if followed else None,
    )


def test_a_retired_driver_is_not_described_as_having_finished() -> None:
    """The bug: "finished 18 (DNF)" says both that they did and didn't."""
    event = make_event(
        rows=[
            row(1, "Soren Larkspur", "2:31:04.882"),
            row(18, "Nico Vantage", "DNF", followed=True),
        ]
    )

    alert = _final_event(event)

    assert alert is not None
    assert "finished" not in alert.message
    assert alert.message == "Nico Vantage retired (classified 18)"


def test_a_disqualified_driver_is_not_credited_with_the_place() -> None:
    event = make_event(rows=[row(2, "Nico Vantage", "DSQ", followed=True)])

    alert = _final_event(event)

    assert alert is not None
    assert alert.message == "Nico Vantage was disqualified (classified 2)"


def test_a_driver_who_finished_still_reads_as_finishing() -> None:
    """The unchanged path: a gap, a time or a lap count is a real result."""
    event = make_event(
        rows=[
            row(1, "Soren Larkspur", "2:31:04.882"),
            row(4, "Nico Vantage", "+15.080", followed=True),
        ]
    )

    alert = _final_event(event)

    assert alert is not None
    assert alert.message == "Nico Vantage finished 4 (+15.080)"


def test_a_golfers_to_par_score_is_never_mistaken_for_a_retirement() -> None:
    """Golf scores are to-par strings, so no golf line may take the new verb."""
    event = make_event(
        event_id=GOLF_ID,
        rows=[row(3, "Iva Thornebury", "-11", followed=True, label="T3")],
    )

    alert = _final_event(event)

    assert alert is not None
    assert alert.message == "Iva Thornebury finished T3 (-11)"


def test_the_best_placed_followed_athlete_still_leads_the_headline() -> None:
    """A retirement changes the verb, not which followed row leads."""
    event = make_event(
        rows=[
            row(3, "Iva Thornebury", "+8.114", followed=True),
            row(19, "Nico Vantage", "DNF", followed=True),
        ]
    )

    alert = _final_event(event)

    assert alert is not None
    assert alert.message.startswith("Iva Thornebury finished 3 (+8.114)")
    assert "also: Nico Vantage 19" in alert.message
