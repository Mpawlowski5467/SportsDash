"""The fight_* columns outlive the phase that produced them.

``upsert_games`` deliberately never wipes a stored method of victory, so a
row can hold one while the bout is no longer FINAL — an ESPN correction, a
result under review.  ``FightResultOut`` promises a result only for a
finished bout (and the UI gates on ``phase === "final"``), so the row
serializer has to gate on the phase rather than on the column.  All fixture
data is fictional.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.orm import GameORM, LeagueORM
from app.services import serialize

START = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)

LEAGUE = LeagueORM(
    id="ironclad-circuit",
    sport="mma",
    name="Ironclad Fighting Circuit",
    provider="espn",
    provider_key="mma/ironclad",
)


def make_row(*, phase: str) -> GameORM:
    """A bout row carrying a full method of victory, in ``phase``."""
    return GameORM(
        id="espn:bout-9",
        league_id=LEAGUE.id,
        home_name="Rowan Alder",
        away_name="Kestrel Vane",
        start_time=START,
        phase=phase,
        home_score=1,
        away_score=0,
        period=2,
        period_label="R2",
        is_intermission=False,
        state_updated_at=START,
        fight_method="submission",
        fight_detail="Rear-Naked Choke",
        fight_round=2,
        fight_clock="4:21",
    )


def test_a_final_bout_serves_its_stored_method() -> None:
    out = serialize.game_to_out(make_row(phase="final"), LEAGUE)
    assert out.fight_result is not None
    assert out.fight_result.method == "submission"


def test_a_bout_that_is_no_longer_final_serves_no_result() -> None:
    """The stored columns stay put; the response just stops claiming a result."""
    for phase in ("in_progress", "scheduled", "postponed", "canceled"):
        row = make_row(phase=phase)
        assert serialize.game_to_out(row, LEAGUE).fight_result is None
        assert row.fight_method == "submission"  # nothing was erased
