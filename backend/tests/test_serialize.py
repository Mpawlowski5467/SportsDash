"""Unit tests for the ORM/domain -> API-schema serializers.

Every endpoint that returns games shares ``app.services.serialize``, so
its two game serializers must agree on the shape they produce: one reads
a stored row, the other a never-stored provider object (season history).
A field only one of them fills is a field that appears and disappears
depending on which route served it.  All fixture data is fictional.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import domain
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

FINISH = domain.FightResult(
    method=domain.FightMethod.SUBMISSION,
    detail="Rear-Naked Choke",
    round=2,
    clock="4:21",
)


def make_row(
    *,
    fight_method: str | None = None,
    fight_detail: str | None = None,
    fight_round: int | None = None,
    fight_clock: str | None = None,
) -> GameORM:
    """A finished bout row, with the fight_* columns under test."""
    return GameORM(
        id="espn:bout-1",
        league_id=LEAGUE.id,
        home_name="Rowan Alder",
        away_name="Kestrel Vane",
        start_time=START,
        phase="final",
        home_score=1,
        away_score=0,
        period=2,
        period_label="R2",
        is_intermission=False,
        state_updated_at=START,
        fight_method=fight_method,
        fight_detail=fight_detail,
        fight_round=fight_round,
        fight_clock=fight_clock,
    )


def make_domain_game(fight_result: domain.FightResult | None = None) -> domain.Game:
    """The same bout as a provider-domain object (the history path)."""
    return domain.Game(
        id="espn:bout-1",
        league_id=LEAGUE.id,
        home_name="Rowan Alder",
        away_name="Kestrel Vane",
        start_time=START,
        fight_result=fight_result,
        state=domain.GameState(
            game_id="espn:bout-1",
            phase=domain.GamePhase.FINAL,
            home_score=1,
            away_score=0,
            period=2,
            period_label="R2",
        ),
    )


def test_game_to_out_rebuilds_a_fight_result_from_the_row_columns() -> None:
    out = serialize.game_to_out(
        make_row(
            fight_method="submission",
            fight_detail="Rear-Naked Choke",
            fight_round=2,
            fight_clock="4:21",
        ),
        LEAGUE,
    )
    assert out.fight_result is not None
    assert out.fight_result.method == "submission"
    assert out.fight_result.detail == "Rear-Naked Choke"
    assert out.fight_result.round == 2
    assert out.fight_result.clock == "4:21"


def test_game_to_out_omits_a_fight_result_when_the_method_is_unknown() -> None:
    """A round and a time are not a result on their own.

    Every sport but MMA — and any bout ESPN never said the finish for —
    leaves ``fight_method`` NULL and must serialize as ``null``.
    """
    assert serialize.game_to_out(make_row(), LEAGUE).fight_result is None
    partial = make_row(fight_round=2, fight_clock="4:21")
    assert serialize.game_to_out(partial, LEAGUE).fight_result is None


def test_domain_game_to_out_matches_the_stored_shape() -> None:
    """The history path serializes an unstored bout identically.

    ``FightMethod`` is a str-enum; the response model carries its value,
    not the member, so both serializers produce the same JSON.
    """
    from_domain = serialize.domain_game_to_out(make_domain_game(FINISH), LEAGUE)
    from_row = serialize.game_to_out(
        make_row(
            fight_method="submission",
            fight_detail="Rear-Naked Choke",
            fight_round=2,
            fight_clock="4:21",
        ),
        LEAGUE,
    )
    assert from_domain.fight_result == from_row.fight_result
    assert serialize.domain_game_to_out(make_domain_game(), LEAGUE).fight_result is None
