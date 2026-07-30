"""Unit tests for the ESPN racing parsers, catalog branch, and scheduler helpers.

All payloads are fictional ESPN-shaped JSON; no network I/O anywhere.
Payload shape mirrors the live racing feeds (see racing.py): one
``events[]`` entry per race WEEKEND — multi-session (F1-style: FP/Qual/
Race competitions keyed by ``type.abbreviation``, sprint weekends swap in
SS/SR) or a single typeless competition (stock-car style, no event
``endDate``).  Competitors are thin: the ESPN athlete id lives on
``competitor.id`` (the nested ``athlete`` object has NO id), ``order`` is
the pre-sorted position, ``winner: true`` marks P1 only.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Iterator

import httpx
import pytest

from app.models.domain import Event, GamePhase, LeaderRow, League, Sport, Team
from app.providers.espn import (
    EspnProvider,
    _ENRICH_MAX_CARS,
    _ENRICH_MAX_EVENTS,
    _apply_racing_facts,
    _parse_racing_car_status,
    _parse_racing_event,
    _parse_racing_scoreboard,
    _parse_racing_stats,
    _racing_enrich_targets,
    _racing_facts,
    _racing_field,
    _racing_lap_label,
    _racing_race_distance,
)
from app.providers.espn.racing import (
    _OUTCOME_CLASSIFIED,
    _OUTCOME_DISQUALIFIED,
    _OUTCOME_NONE,
    _OUTCOME_RETIRED,
    _UNKNOWN_STATUS_PART,
    _RacingCar,
    _parse_racing_site_stats,
    _racing_live_score,
)
from app.providers import espn_catalog
from app.providers.espn_catalog import CatalogLeague, EspnCatalogError
from app.providers.http_util import TransientProviderError
from app.scheduler.common import _athlete_id_map, _is_leaderboard, _tag_followed_athletes

RACING_LEAGUE = League(
    id="apex-circuit",
    sport=Sport.RACING,
    name="Apex Circuit Series",
    provider="espn",
    provider_key="racing/apex",
)

RACING_CATALOG_LEAGUE = CatalogLeague(
    id="apex-circuit",
    name="Apex Circuit Series",
    sport=Sport.RACING,
    provider="espn",
    provider_key="racing/apex",
)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _status(*, state: str, name: str, period: int = 0) -> dict[str, Any]:
    return {"period": period, "type": {"state": state, "name": name}}


def _competitor(
    *,
    athlete_id: str | None = "9200001",
    name: str | None = "R. Vega",
    order: Any = 1,
    winner: bool = False,
    statistics: Any = None,
) -> dict[str, Any]:
    """One driver: the ESPN athlete id lives on ``competitor.id`` (the
    nested athlete object has NO id on the real feed).

    ``statistics`` is the site slot every racing competitor carries; it is
    ``[]`` on every session ever observed, and ``statistics=None`` here means
    "omit the key entirely" so both spellings of "nothing" are testable.
    """
    competitor: dict[str, Any] = {
        "id": athlete_id,
        "type": "athlete",
        "order": order,
        "athlete": {"displayName": name, "fullName": name},
    }
    if winner:
        competitor["winner"] = True
    if statistics is not None:
        competitor["statistics"] = statistics
    return competitor


def _site_stats(**stats: tuple[float | None, str]) -> list[dict[str, Any]]:
    """A populated SITE-scoreboard ``statistics`` slot.

    UNVERIFIED SHAPE: no racing session was in progress anywhere during the
    live probe, so this models the flat ``{name, value, displayValue}`` list
    the site API uses wherever it populates a ``statistics`` array at all.
    """
    return [
        {"name": name, "value": value, "displayValue": display}
        for name, (value, display) in stats.items()
    ]


def _session(
    *,
    session_id: str = "9400001",
    abbreviation: str | None = "Race",
    type_id: str | None = "3",
    date_str: str | None = "2026-07-26T13:00Z",
    status: dict[str, Any] | None = None,
    competitors: Any = None,
) -> dict[str, Any]:
    """One session (``competitions[]`` entry).  ``abbreviation=None`` omits
    the ``type`` object entirely — the stock-car shape (``type`` is null)."""
    session: dict[str, Any] = {"id": session_id}
    if abbreviation is not None:
        session["type"] = {"id": type_id, "abbreviation": abbreviation}
    else:
        session["type"] = None
    if date_str is not None:
        session["date"] = date_str
    if status is not None:
        session["status"] = status
    if competitors is not None:
        session["competitors"] = competitors
    return session


def _weekend(
    *,
    event_id: str = "9300001",
    name: str | None = "Grand Prix of Veltria",
    date_str: str | None = "2026-07-24T11:30Z",
    end_date: str | None = "2026-07-26T13:00Z",
    status: dict[str, Any] | None = None,
    sessions: list[dict[str, Any]] | None = None,
    circuit: str | None = "Veltria Ring",
) -> dict[str, Any]:
    """One race-weekend event.  None-valued knobs omit the key entirely so
    defensive "missing" branches can be exercised."""
    event: dict[str, Any] = {"id": event_id}
    if name is not None:
        event["name"] = name
    if date_str is not None:
        event["date"] = date_str
    if end_date is not None:
        event["endDate"] = end_date
    if status is not None:
        event["status"] = status
    if sessions is not None:
        event["competitions"] = sessions
    if circuit is not None:
        event["circuit"] = {"id": "613", "fullName": circuit}
    return event


def _sprint_weekend_sessions(
    *, race_status: dict[str, Any] | None = None, race_competitors: Any = None
) -> list[dict[str, Any]]:
    """An F1-style sprint weekend: FP1, SS (id 5), SR (id 6), Qual, Race."""
    return [
        _session(
            session_id="9400010", abbreviation="FP1", type_id="1", date_str="2026-07-24T11:30Z"
        ),
        _session(
            session_id="9400011", abbreviation="SS", type_id="5", date_str="2026-07-24T15:30Z"
        ),
        _session(
            session_id="9400012", abbreviation="SR", type_id="6", date_str="2026-07-25T10:00Z"
        ),
        _session(
            session_id="9400013", abbreviation="Qual", type_id="2", date_str="2026-07-25T14:00Z"
        ),
        _session(
            session_id="9400014",
            abbreviation="Race",
            type_id="3",
            date_str="2026-07-26T13:00Z",
            status=race_status,
            competitors=race_competitors,
        ),
    ]


# ---------------------------------------------------------------------------
# Scoreboard / event parsing
# ---------------------------------------------------------------------------


def test_racing_scoreboard_parses_weekends_and_skips_malformed() -> None:
    """One Event per race weekend; junk entries and an id-less event drop
    out without sinking the rest of the board."""
    payload = {
        "events": [
            _weekend(),
            "not-an-event",
            {"name": "Missing Id 500"},  # no id -> skipped
        ]
    }
    events = _parse_racing_scoreboard(payload, RACING_LEAGUE)
    assert [event.id for event in events] == ["espn:9300001"]
    assert events[0].league_id == RACING_LEAGUE.id
    assert events[0].name == "Grand Prix of Veltria"


def test_racing_scoreboard_empty_without_events_list() -> None:
    assert _parse_racing_scoreboard({}, RACING_LEAGUE) == []
    assert _parse_racing_scoreboard({"events": "nope"}, RACING_LEAGUE) == []
    # A non-dict payload degrades to no events rather than raising.
    assert _parse_racing_scoreboard(None, RACING_LEAGUE) == []


def test_racing_scheduled_weekend_no_label_and_empty_board() -> None:
    """Before the weekend the phase is SCHEDULED, there is no session label,
    and — entry lists not being published pre-race — the board is empty."""
    event = _parse_racing_event(
        _weekend(
            status=_status(state="pre", name="STATUS_SCHEDULED"),
            sessions=_sprint_weekend_sessions(),  # sprint types tolerated
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert event.phase is GamePhase.SCHEDULED
    assert event.round_label == ""
    assert event.leaderboard == ()
    assert event.start_time == datetime(2026, 7, 24, 11, 30, tzinfo=timezone.utc)
    assert event.end_time == datetime(2026, 7, 26, 13, 0, tzinfo=timezone.utc)
    assert event.venue == "Veltria Ring"
    assert event.last_update is not None


def test_racing_live_race_appends_lap_progress() -> None:
    """A live Race session labels the weekend with its lap count, and the
    running order becomes the board (ESPN athlete id transiently in
    ``player_id`` — the scheduler rewrites it before persisting)."""
    competitors = [
        _competitor(athlete_id="9200002", name="D. Okafor", order=2),
        _competitor(athlete_id="9200001", name="R. Vega", order=1),
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="in", name="STATUS_IN_PROGRESS"),
            sessions=_sprint_weekend_sessions(
                race_status=_status(state="in", name="STATUS_IN_PROGRESS", period=44),
                race_competitors=competitors,
            ),
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert event.phase is GamePhase.IN_PROGRESS
    assert event.round_label == "Race · Lap 44"
    leader, chaser = event.leaderboard
    assert (leader.position, leader.position_label, leader.name) == (1, "1", "R. Vega")
    assert (chaser.position, chaser.position_label, chaser.name) == (2, "2", "D. Okafor")
    # The site scoreboard carries no times/laps per driver.
    assert leader.score == "" and leader.detail == ""
    assert leader.player_id == "9200001"


def test_racing_live_non_race_session_label_has_no_laps() -> None:
    """A live practice/qualifying session names the label bare — lap
    progress is appended only on the Race session."""
    sessions = _sprint_weekend_sessions()
    sessions[3]["status"] = _status(state="in", name="STATUS_IN_PROGRESS", period=2)
    event = _parse_racing_event(
        _weekend(status=_status(state="in", name="STATUS_IN_PROGRESS"), sessions=sessions),
        RACING_LEAGUE,
    )
    assert event is not None
    assert event.round_label == "Qual"


def test_racing_final_weekend_winner_detail_and_order() -> None:
    """FINAL suppresses the label; the winner row is flagged and the board
    follows ``order`` regardless of feed ordering."""
    competitors = [
        _competitor(athlete_id="9200003", name="M. Castellane", order=3),
        _competitor(athlete_id="9200001", name="R. Vega", order=1, winner=True),
        _competitor(athlete_id="9200002", name="D. Okafor", order=2),
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="post", name="STATUS_FINAL"),
            sessions=_sprint_weekend_sessions(
                race_status=_status(state="post", name="STATUS_FINAL", period=70),
                race_competitors=competitors,
            ),
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert event.phase is GamePhase.FINAL
    assert event.round_label == ""
    assert [row.name for row in event.leaderboard] == ["R. Vega", "D. Okafor", "M. Castellane"]
    assert [row.detail for row in event.leaderboard] == ["Winner", "", ""]
    assert [row.position_label for row in event.leaderboard] == ["1", "2", "3"]


def test_racing_orderless_competitors_sort_last_never_p1() -> None:
    """A competitor whose ``order`` is missing/zero/non-numeric lands at the
    BACK of the board with a trailing sequential position — it must never
    displace the real winner from P1 (the scheduler headlines a FINAL
    notification with the best-placed followed row)."""
    competitors = [
        _competitor(athlete_id="9200007", name="S. Larkwell", order=None),
        _competitor(athlete_id="9200002", name="D. Okafor", order=2),
        _competitor(athlete_id="9200008", name="P. Mireille", order="DNS"),
        _competitor(athlete_id="9200001", name="R. Vega", order=1, winner=True),
        _competitor(athlete_id="9200009", name="Y. Trebond", order=0),
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="post", name="STATUS_FINAL"),
            sessions=[
                _session(
                    status=_status(state="post", name="STATUS_FINAL", period=70),
                    competitors=competitors,
                )
            ],
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    # Ranked rows first (winner on top), the order-less trio trailing in
    # feed order with sequential positions.
    assert [(row.name, row.position) for row in event.leaderboard] == [
        ("R. Vega", 1),
        ("D. Okafor", 2),
        ("S. Larkwell", 3),
        ("P. Mireille", 4),
        ("Y. Trebond", 5),
    ]
    assert [row.detail for row in event.leaderboard] == ["Winner", "", "", "", ""]


def test_racing_all_orderless_field_yields_feed_order() -> None:
    """A field where NO competitor carries an ``order`` (an entry list, not
    a classification) still numbers 1..N in feed order."""
    competitors = [
        _competitor(athlete_id="9200001", name="R. Vega", order=None),
        _competitor(athlete_id="9200002", name="D. Okafor", order=None),
        _competitor(athlete_id="9200003", name="M. Castellane", order=None),
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="pre", name="STATUS_SCHEDULED"),
            sessions=[_session(competitors=competitors)],
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert [(row.name, row.position, row.position_label) for row in event.leaderboard] == [
        ("R. Vega", 1, "1"),
        ("D. Okafor", 2, "2"),
        ("M. Castellane", 3, "3"),
    ]


def test_racing_board_falls_back_to_latest_session_with_entrants() -> None:
    """No competitors on the Race session yet: the latest session that has
    them (qualifying classification) stands in."""
    sessions = _sprint_weekend_sessions()
    sessions[0]["competitors"] = [_competitor(athlete_id="9200003", name="M. Castellane")]
    sessions[3]["competitors"] = [
        _competitor(athlete_id="9200002", name="D. Okafor", order=1),
        _competitor(athlete_id="9200001", name="R. Vega", order=2),
    ]
    event = _parse_racing_event(
        _weekend(status=_status(state="in", name="STATUS_IN_PROGRESS"), sessions=sessions),
        RACING_LEAGUE,
    )
    assert event is not None
    # Qual (the later session) wins over FP1, and the order is quali order.
    assert [row.name for row in event.leaderboard] == ["D. Okafor", "R. Vega"]


def test_racing_single_typeless_competition_is_the_race() -> None:
    """The stock-car shape: ONE competition with a null ``type`` and no
    event ``endDate`` — the lone competition IS the race, and the session
    date bounds the (single-day) span."""
    session = _session(
        abbreviation=None,
        date_str="2026-08-09T18:00Z",
        status=_status(state="in", name="STATUS_IN_PROGRESS", period=120),
        competitors=[
            _competitor(athlete_id="9200005", name="T. Quillfeather", order=1),
            _competitor(athlete_id="9200006", name="B. Ashdown", order=2),
        ],
    )
    event = _parse_racing_event(
        _weekend(
            event_id="202608090790",  # date-encoded ids, not the 7-digit style
            name="Apex Stock Cup at Veltria",
            date_str="2026-08-09T18:00Z",
            end_date=None,
            status=_status(state="in", name="STATUS_IN_PROGRESS"),
            sessions=[session],
            circuit=None,  # stock-car events carry no circuit key
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert event.id == "espn:202608090790"
    assert event.round_label == "Race · Lap 120"
    assert event.end_time == datetime(2026, 8, 9, 18, 0, tzinfo=timezone.utc)
    assert event.venue is None
    assert [row.name for row in event.leaderboard] == ["T. Quillfeather", "B. Ashdown"]


def test_racing_malformed_competitor_skipped() -> None:
    """A competitor with no usable name drops out without sinking the rest
    of the board (or the event)."""
    competitors = [
        _competitor(athlete_id="9200001", name="R. Vega", order=1, winner=True),
        {"id": "9200009", "order": 2, "athlete": {}},  # no name -> skipped
        "junk",
        _competitor(athlete_id="9200002", name="D. Okafor", order=3),
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="post", name="STATUS_FINAL"),
            sessions=[
                _session(
                    status=_status(state="post", name="STATUS_FINAL", period=70),
                    competitors=competitors,
                )
            ],
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert [(row.name, row.position) for row in event.leaderboard] == [
        ("R. Vega", 1),
        ("D. Okafor", 3),
    ]


# ---------------------------------------------------------------------------
# Mid-race gaps from the site scoreboard's own per-driver statistics slot
#
# STRUCTURALLY VERIFIED ONLY.  Every racing competitor carries a
# ``statistics`` slot and it was EMPTY on all 1606 competitors scanned — but
# every one of those sessions was finished or scheduled, because no racing
# session anywhere was in progress during the live probe (F1 summer break +
# a stock-car off-weekend).  So the populated shape below is a MODEL, and the
# rule the tests pin down is the one that holds either way: a gap when the
# slot carries one, and byte-for-byte today's board when it does not.
# ---------------------------------------------------------------------------


def _live_weekend(competitors: list[dict[str, Any]]) -> dict[str, Any]:
    """A race in progress, as the SITE scoreboard ships it."""
    return _weekend(
        status=_status(state="in", name="STATUS_IN_PROGRESS"),
        sessions=[
            _session(
                status=_status(state="in", name="STATUS_IN_PROGRESS", period=44),
                competitors=competitors,
            )
        ],
    )


def test_racing_live_board_renders_running_gaps_from_the_site_slot() -> None:
    """A live board fills ``score`` with the running interval / lap deficit
    when the site slot carries one — at zero extra calls, since this is the
    payload the board itself is parsed from."""
    competitors = [
        # The leader has no interval; a lap COUNT is not a gap and must not
        # be rendered as one (the round label already says "Lap 44").
        _competitor(
            athlete_id="9200001",
            name="R. Vega",
            order=1,
            statistics=_site_stats(lapsCompleted=(44.0, "44")),
        ),
        _competitor(
            athlete_id="9200002",
            name="D. Okafor",
            order=2,
            statistics=_site_stats(lapsCompleted=(44.0, "44"), behindTime=(2418.0, "2.418")),
        ),
        # A lapped car: a deficit and a pinned-at-zero interval placeholder,
        # which must never render as a real time.
        _competitor(
            athlete_id="9200003",
            name="M. Castellane",
            order=3,
            statistics=_site_stats(behindLaps=(1.0, "1"), behindTime=(0.0, ".000")),
        ),
        # The slot as every observed session ships it.
        _competitor(athlete_id="9200004", name="S. Larkwell", order=4, statistics=[]),
    ]
    event = _parse_racing_event(_live_weekend(competitors), RACING_LEAGUE)
    assert event is not None
    assert event.round_label == "Race · Lap 44"
    assert [(row.name, row.score) for row in event.leaderboard] == [
        ("R. Vega", ""),
        ("D. Okafor", "+2.418"),  # unsigned in the feed, signed on the board
        ("M. Castellane", "+1 lap"),
        ("S. Larkwell", ""),
    ]
    # Nothing else moved: the site scoreboard still says nothing per-driver
    # beyond the running order.
    assert [row.detail for row in event.leaderboard] == ["", "", "", ""]


@pytest.mark.parametrize(
    "statistics",
    [
        None,  # the key is absent entirely
        [],  # the slot as all 1606 observed competitors ship it
        "nope",  # a shape change
        [1, 2, 3],  # a list of non-objects
        [{"displayValue": "+2.418"}],  # an entry with no name to key on
        {"splits": {"categories": []}},  # the core nesting, empty
    ],
)
def test_racing_live_site_statistics_absent_renders_todays_board(statistics: Any) -> None:
    """The unpopulated / unparseable slot is the NORMAL case: the board comes
    out exactly as it does today, and nothing raises."""
    event = _parse_racing_event(
        _live_weekend(
            [
                _competitor(athlete_id="9200001", name="R. Vega", order=1, statistics=statistics),
                _competitor(athlete_id="9200002", name="D. Okafor", order=2),
            ]
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert [(row.name, row.score, row.detail) for row in event.leaderboard] == [
        ("R. Vega", "", ""),
        ("D. Okafor", "", ""),
    ]


def test_racing_live_statistics_blowup_costs_only_the_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety net under a slot whose populated shape nobody has seen: an
    unannounced shape change costs the score column, not the driver's row."""
    from app.providers.espn import racing as racing_module

    def boom(node: Any) -> Any:
        raise TypeError("statistics became a mapping of mappings overnight")

    monkeypatch.setattr(racing_module, "_parse_racing_site_stats", boom)
    event = _parse_racing_event(
        _live_weekend(
            [
                _competitor(
                    athlete_id="9200001",
                    name="R. Vega",
                    order=1,
                    statistics=_site_stats(behindTime=(2418.0, "+2.418")),
                ),
                _competitor(athlete_id="9200002", name="D. Okafor", order=2),
            ]
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert [(row.name, row.score) for row in event.leaderboard] == [
        ("R. Vega", ""),
        ("D. Okafor", ""),
    ]


def test_racing_final_board_ignores_the_site_statistics_slot() -> None:
    """FINAL semantics are untouched: a finished board's score column belongs
    to the core-API enrichment, which knows a DNF from a gap."""
    competitors = [
        _competitor(
            athlete_id="9200001",
            name="R. Vega",
            order=1,
            winner=True,
            statistics=_site_stats(behindTime=(2418.0, "2.418")),
        )
    ]
    event = _parse_racing_event(
        _weekend(
            status=_status(state="post", name="STATUS_FINAL"),
            sessions=[
                _session(
                    status=_status(state="post", name="STATUS_FINAL", period=70),
                    competitors=competitors,
                )
            ],
        ),
        RACING_LEAGUE,
    )
    assert event is not None
    assert [(row.score, row.detail) for row in event.leaderboard] == [("", "Winner")]


def test_racing_site_stats_read_either_nesting() -> None:
    """The site list shape and the core-API nesting both flatten; a live
    payload shaped like either one parses, anything else yields nothing."""
    listed = _parse_racing_site_stats(_site_stats(behindTime=(2418.0, "+2.418")))
    assert listed is not None and listed.behind_time == "+2.418"
    nested = _parse_racing_site_stats(_core_stats(behindLaps=(-2.0, "-2")))
    assert nested is not None and nested.laps_behind == 2
    # An entry keyed only by ``abbreviation`` is still usable.
    assert _parse_racing_site_stats([{"abbreviation": "behindLaps", "value": 3.0}]) is not None
    assert _parse_racing_site_stats([]) is None
    assert _parse_racing_site_stats(None) is None
    # A finishing time is not a running gap, and a lap counter is not either.
    assert (
        _racing_live_score(_parse_racing_site_stats(_site_stats(totalTime=(5996180.0, "1:39"))))
        == ""
    )
    assert _racing_live_score(None) == ""


def test_racing_event_missing_id_or_date_skipped() -> None:
    assert _parse_racing_event(_weekend(event_id=""), RACING_LEAGUE) is None
    assert _parse_racing_event(_weekend(date_str=None), RACING_LEAGUE) is None
    assert _parse_racing_event(_weekend(name=None), RACING_LEAGUE) is None


# ---------------------------------------------------------------------------
# Provider dispatch: racing parser for racing leagues, games paths gated
# ---------------------------------------------------------------------------


async def test_provider_get_events_uses_racing_parser() -> None:
    provider = EspnProvider()
    seen: list[str] = []

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        seen.append(url)
        return {
            "events": [
                _weekend(
                    status=_status(state="pre", name="STATUS_SCHEDULED"),
                    sessions=_sprint_weekend_sessions(),
                )
            ]
        }

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    events = await provider.get_events(RACING_LEAGUE, date(2026, 7, 1), date(2026, 7, 31))
    assert seen == ["https://site.api.espn.com/apis/site/v2/sports/racing/apex/scoreboard"]
    assert [event.id for event in events] == ["espn:9300001"]
    # The racing parser ran: the venue comes from the event-level circuit,
    # which the golf parser (competition-level venue) would never read.
    assert events[0].venue == "Veltria Ring"
    assert events[0].end_time == datetime(2026, 7, 26, 13, 0, tzinfo=timezone.utc)


async def test_provider_get_event_state_rescans_racing_scoreboard() -> None:
    provider = EspnProvider()

    async def fake_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        assert url.endswith("/racing/apex/scoreboard")
        return {"events": [_weekend(status=_status(state="post", name="STATUS_FINAL"))]}

    provider._get_json = fake_get_json  # type: ignore[method-assign]
    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None and event.phase is GamePhase.FINAL
    assert await provider.get_event_state(RACING_LEAGUE, "9999999") is None


async def test_provider_games_paths_skip_leaderboard_sports() -> None:
    """Leaderboard leagues never issue a games-path fetch: their Events come
    from get_events, and the old tour-scoreboard scan only produced
    "Skipping malformed" log noise."""
    golf_league = League(
        id="crestmoor-golf-tour",
        sport=Sport.GOLF,
        name="Crestmoor Golf Tour",
        provider="espn",
        provider_key="golf/crestmoor",
    )
    driver = Team(
        id="apex-circuit-r-vega",
        league_id=RACING_LEAGUE.id,
        name="R. Vega",
        abbreviation="VEG",
        provider_key="9200001",
    )
    provider = EspnProvider()

    async def forbidden(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise AssertionError(f"games path must not fetch for a leaderboard league: {url}")

    provider._get_json = forbidden  # type: ignore[method-assign]
    window = (date(2026, 7, 1), date(2026, 7, 31))
    assert await provider.get_schedule(RACING_LEAGUE, driver, *window) == []
    assert await provider.get_competition_schedule(RACING_LEAGUE, *window) == []
    assert await provider.get_competition_schedule(golf_league, *window) == []


# ---------------------------------------------------------------------------
# Core-API board enrichment
#
# The site scoreboard's competitors are thin, so the constructor / car
# number / grid come from the core event root (tier A, one call per active
# race) and the race time / gap / retirements from a per-car N+1 spent once
# the race is FINAL (tier B).  Payloads below mirror the real core shapes:
# ``vehicle`` + ``startOrder`` inline on the event root, statistics/status
# reachable only through a ``$ref``, stats nested under
# ``splits.categories[].stats[]`` with zeroed placeholders, and laps-behind
# reported POSITIVE by open-wheel feeds / NEGATIVE by stock-car ones.
# ---------------------------------------------------------------------------

_CORE_LEAGUE = "https://sports.core.api.espn.com/v2/sports/racing/leagues/apex"


def _core_ref(leaf: str, athlete_id: str, competition_id: str = "9400001") -> str:
    """A ``$ref`` spelled as the core feed spells it (query string and all)."""
    return (
        f"{_CORE_LEAGUE}/events/9300001/competitions/{competition_id}"
        f"/competitors/{athlete_id}/{leaf}?lang=en&region=us"
    )


def _core_car(
    *,
    athlete_id: str,
    order: int,
    start_order: int | None = None,
    winner: bool = False,
    number: str | None = "7",
    manufacturer: str | None = "Corvane Works",
    team: str | None = None,
    with_statistics: bool = True,
    with_status: bool = True,
) -> dict[str, Any]:
    """One competitor as the CORE event root inlines it.

    ``vehicle`` and ``startOrder`` come free; statistics and status are
    ``$ref``-only (no ``enable=``/``expand=`` variant expands them).
    """
    vehicle: dict[str, Any] = {}
    if number is not None:
        vehicle["number"] = number
    if manufacturer is not None:
        vehicle["manufacturer"] = manufacturer
    if team is not None:
        vehicle["team"] = team
    car: dict[str, Any] = {"id": athlete_id, "type": "athlete", "order": order, "vehicle": vehicle}
    if start_order is not None:
        car["startOrder"] = start_order
    if winner:
        car["winner"] = True
    if with_statistics:
        car["statistics"] = {"$ref": _core_ref("statistics", athlete_id)}
    if with_status:
        car["status"] = {"$ref": _core_ref("status", athlete_id)}
    return car


def _core_event_root(
    cars: list[dict[str, Any]],
    *,
    competition_id: str = "9400001",
    abbreviation: str | None = "Race",
) -> dict[str, Any]:
    """The core event root: every session inlined with its whole field."""
    competition: dict[str, Any] = {"id": competition_id, "session": 5, "competitors": cars}
    competition["type"] = {"id": "3", "abbreviation": abbreviation} if abbreviation else None
    return {"id": "9300001", "competitions": [competition]}


def _core_stats(**stats: tuple[float | None, str]) -> dict[str, Any]:
    """A per-car statistics payload from ``name=(value, displayValue)`` pairs."""
    return {
        "splits": {
            "id": "0",
            "categories": [
                # Stock-car/IndyCar payloads carry this present-but-empty
                # category alongside "general"; flattening must skip it.
                {"name": "gapToLeader", "stats": []},
                {
                    "name": "general",
                    "stats": [
                        {"name": name, "value": value, "displayValue": display}
                        for name, (value, display) in stats.items()
                    ],
                },
            ],
        }
    }


# The THREE per-competitor status types ESPN publishes, as it publishes
# them: ``(type id, name, displayValue, completed)``.  ``completed`` is false
# for a disqualification too, which is why the name is the only signal read.
_CORE_STATUS_TYPES = {
    "classified": ("12", "STATUS_CLASSIFIED", "Classified", True),
    "retired": ("8", "STATUS_RETIRED", "Retired", False),
    "dsq": ("14", "STATUS_DISQUALIFIED", "DQ", False),
}


def _core_status(*, kind: str = "classified", period: int = 70) -> dict[str, Any]:
    """A per-competitor status payload of the given kind."""
    type_id, name, display, completed = _CORE_STATUS_TYPES[kind]
    return {
        "period": period,
        "type": {
            "id": type_id,
            "name": name,
            "state": "post",
            "completed": completed,
            "description": display,
            "displayValue": display,
            "shortDetail": display,
        },
    }


def _core_distance(laps: int = 70) -> dict[str, Any]:
    """The competition-level aggregate: ``categories[]`` at the TOP level."""
    return {
        "categories": [
            {
                "name": "general",
                "stats": [
                    {"name": "laps", "value": float(laps), "displayValue": str(laps)},
                    {"name": "length", "value": 306.0, "displayValue": "306"},
                ],
            }
        ]
    }


def _http_404() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://core")
    return httpx.HTTPStatusError("404", request=request, response=httpx.Response(404))


class _CoreStub:
    """A fake ``EspnProvider._get_json`` that answers — and counts — calls.

    Every URL the enrichment can reach is routed explicitly and an
    unregistered one raises, so a test asserting "no fan-out here" fails
    loudly instead of silently receiving ``{}``.  Any registered payload may
    be an Exception, which is raised — that is how a per-resource 404 or
    outage is modeled.
    """

    def __init__(
        self,
        *,
        scoreboard: Any,
        event_root: Any = None,
        statistics: dict[str, Any] | None = None,
        statuses: dict[str, Any] | None = None,
        distance: Any = None,
    ) -> None:
        self.scoreboard = scoreboard
        self.event_root = event_root
        self.statistics = statistics or {}
        self.statuses = statuses or {}
        self.distance = distance
        self.calls: list[str] = []

    async def __call__(self, url: str, params: dict[str, str] | None = None) -> Any:
        self.calls.append(url)
        path = url.split("?", 1)[0]
        if path.endswith("/scoreboard"):
            return self._body(self.scoreboard, url)
        if "/competitors/" in path:
            athlete_id = path.split("/competitors/", 1)[1].split("/")[0]
            table = self.statistics if path.endswith("/statistics") else self.statuses
            return self._body(table.get(athlete_id), url)
        if path.endswith("/statistics"):
            return self._body(self.distance, url)
        if "/events/" in path:
            return self._body(self.event_root, url)
        raise AssertionError(f"unrouted core call: {url}")

    @staticmethod
    def _body(body: Any, url: str) -> Any:
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise AssertionError(f"unexpected core call: {url}")
        return body

    def count(self, *needles: str) -> int:
        return sum(1 for url in self.calls if all(needle in url for needle in needles))


def _thin_field(*rows: tuple[str, str, int]) -> list[dict[str, Any]]:
    """Site-scoreboard competitors from ``(athlete id, name, order)`` triples."""
    return [
        _competitor(athlete_id=athlete_id, name=name, order=order, winner=order == 1)
        for athlete_id, name, order in rows
    ]


def _final_scoreboard(competitors: list[dict[str, Any]]) -> dict[str, Any]:
    """A finished weekend exactly as the SITE scoreboard ships it."""
    return {
        "events": [
            _weekend(
                status=_status(state="post", name="STATUS_FINAL"),
                sessions=[
                    _session(
                        status=_status(state="post", name="STATUS_FINAL", period=70),
                        competitors=competitors,
                    )
                ],
            )
        ]
    }


def _finished_race_stub(**overrides: Any) -> _CoreStub:
    """A four-car finished race: leader, gap, lapped, retired."""
    board = _thin_field(
        ("9200001", "R. Vega", 1),
        ("9200002", "D. Okafor", 2),
        ("9200003", "M. Castellane", 3),
        ("9200004", "S. Larkwell", 4),
    )
    defaults: dict[str, Any] = {
        "scoreboard": _final_scoreboard(board),
        "event_root": _core_event_root(
            [
                _core_car(
                    athlete_id="9200001",
                    order=1,
                    start_order=2,
                    winner=True,
                    number="7",
                    manufacturer="Corvane Works",
                ),
                _core_car(
                    athlete_id="9200002",
                    order=2,
                    start_order=1,
                    number="4",
                    manufacturer="Halberd Racing",
                ),
                _core_car(
                    athlete_id="9200003",
                    order=3,
                    start_order=5,
                    number="9",
                    manufacturer="Halberd Racing",
                ),
                _core_car(
                    athlete_id="9200004",
                    order=4,
                    start_order=9,
                    number="21",
                    manufacturer="Meridian Motorsport",
                ),
            ]
        ),
        "statistics": {
            # The leader: a total time and NO gap.
            "9200001": _core_stats(
                place=(1.0, "1"),
                pole=(2.0, "2"),
                lapsCompleted=(70.0, "70"),
                totalTime=(5996180.0, "1:39:56.180"),
                pitsTaken=(2.0, "2"),
                # Qualifying splits are zeroed on a Race competition.
                qual3TimeMS=(0.0, "0.000"),
            ),
            # A lead-lap finisher: BOTH a total time and a gap; the gap wins.
            "9200002": _core_stats(
                place=(2.0, "2"),
                lapsCompleted=(70.0, "70"),
                totalTime=(6011260.0, "1:40:11.260"),
                behindTime=(15080.0, "+15.080"),
                pitsTaken=(2.0, "2"),
            ),
            # A lapped car: no time at all, just laps behind (+ the one
            # fastest lap in the field).  ``pitsTaken`` is published for every
            # classified car in the real corpus (529 of 529).
            "9200003": _core_stats(
                place=(3.0, "3"),
                lapsCompleted=(69.0, "69"),
                behindLaps=(1.0, "1"),
                fastestLap=(82000.0, "1:22.000"),
                pitsTaken=(3.0, "3"),
            ),
            # A retirement: laps completed and nothing else — no time, no
            # ``pitsTaken`` (absent on 26 of the 76 retired cars sampled).
            "9200004": _core_stats(place=(4.0, "4"), lapsCompleted=(13.0, "13")),
        },
        "statuses": {
            "9200001": _core_status(period=70),
            "9200002": _core_status(period=70),
            "9200003": _core_status(period=69),
            "9200004": _core_status(kind="retired", period=13),
        },
    }
    defaults.update(overrides)
    return _CoreStub(**defaults)


async def test_racing_final_enrichment_fills_score_and_detail() -> None:
    """A FINAL board gains the race time / gap in ``score`` and the
    constructor + car number + grid slot in ``detail`` — and a lapped or
    retired car reads sensibly in BOTH columns instead of borrowing a time
    it never set."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert [(row.name, row.score, row.detail) for row in event.leaderboard] == [
        ("R. Vega", "1:39:56.180", "Winner · Corvane Works · #7 · Grid 2"),
        ("D. Okafor", "+15.080", "Halberd Racing · #4 · Grid 1"),
        ("M. Castellane", "+1 lap", "Halberd Racing · #9 · Grid 5 · Fastest lap 1:22.000"),
        ("S. Larkwell", "DNF", "Meridian Motorsport · #21 · Grid 9 · Retired lap 13"),
    ]
    # The transient ESPN athlete id survives enrichment untouched — the
    # scheduler still has to rewrite it to the internal followed-team id.
    assert [row.player_id for row in event.leaderboard] == [
        "9200001",
        "9200002",
        "9200003",
        "9200004",
    ]
    # ONE event root (not one call per session, and no competitors
    # collection) + one statistics and one status per car.  A finished race
    # needs no lap-distance call.
    assert stub.count("/events/9300001") == 9
    assert stub.count("/competitors/", "/statistics") == 4
    assert stub.count("/competitors/", "/status") == 4
    assert stub.count("/competitions/9400001/statistics") == 0


async def test_racing_final_enrichment_memoized_not_refetched() -> None:
    """A finished classification never changes: the second poll re-renders
    from the memo and spends ZERO core calls."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    provider._get_json = stub  # type: ignore[method-assign]

    first = await provider.get_event_state(RACING_LEAGUE, "9300001")
    core_calls = stub.count("/events/9300001")
    second = await provider.get_event_state(RACING_LEAGUE, "9300001")

    assert first is not None and second is not None
    assert [row.score for row in second.leaderboard] == [row.score for row in first.leaderboard]
    assert [row.detail for row in second.leaderboard] == [row.detail for row in first.leaderboard]
    # Only the site scoreboard was fetched again.
    assert stub.count("/events/9300001") == core_calls
    assert stub.count("/scoreboard") == 2


async def test_racing_live_enrichment_is_one_call_and_no_per_car_fanout() -> None:
    """Tier A: a LIVE race gets the constructor/number/grid and the race
    distance for its lap label from two calls — and none of the per-car
    statistics the FINAL tier spends."""
    provider = EspnProvider()
    board = _thin_field(("9200001", "R. Vega", 1), ("9200002", "D. Okafor", 2))
    stub = _CoreStub(
        scoreboard={
            "events": [
                _weekend(
                    status=_status(state="in", name="STATUS_IN_PROGRESS"),
                    sessions=[
                        _session(
                            status=_status(state="in", name="STATUS_IN_PROGRESS", period=44),
                            competitors=board,
                        )
                    ],
                )
            ]
        },
        event_root=_core_event_root(
            [
                _core_car(athlete_id="9200001", order=1, start_order=2, number="7"),
                _core_car(
                    athlete_id="9200002", order=2, start_order=1, number="4", manufacturer=None
                ),
            ]
        ),
        distance=_core_distance(70),
    )
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    # Lap progress from the site scoreboard, the denominator from the core
    # competition aggregate.
    assert event.round_label == "Race · Lap 44 of 70"
    # No per-car statistics in this tier, so no time or gap yet — but the
    # free facts are already there.  A car whose vehicle names no entrant
    # still gets its number and grid.
    assert [(row.score, row.detail) for row in event.leaderboard] == [
        ("", "Corvane Works · #7 · Grid 2"),
        ("", "#4 · Grid 1"),
    ]
    assert stub.count("/competitors/") == 0
    assert stub.count("/events/9300001") == 2

    # The distance is a constant for the event's lifetime: the next poll
    # re-fetches only the event root.
    await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert stub.count("/competitions/9400001/statistics") == 1
    assert stub.count("/events/9300001") == 3


async def test_racing_live_gap_survives_tier_a_enrichment() -> None:
    """A running gap read from the site slot is never overwritten by tier A:
    the enrichment only ADDS, so the live board ends up with both the gap and
    the constructor/number/grid — and still spends no per-car call."""
    provider = EspnProvider()
    stub = _CoreStub(
        scoreboard={
            "events": [
                _live_weekend(
                    [
                        _competitor(athlete_id="9200001", name="R. Vega", order=1, statistics=[]),
                        _competitor(
                            athlete_id="9200002",
                            name="D. Okafor",
                            order=2,
                            statistics=_site_stats(behindTime=(2418.0, "+2.418")),
                        ),
                    ]
                )
            ]
        },
        event_root=_core_event_root(
            [
                _core_car(athlete_id="9200001", order=1, start_order=2, number="7"),
                _core_car(athlete_id="9200002", order=2, start_order=1, number="4"),
            ]
        ),
        distance=_core_distance(70),
    )
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert [(row.score, row.detail) for row in event.leaderboard] == [
        ("", "Corvane Works · #7 · Grid 2"),
        ("+2.418", "Corvane Works · #4 · Grid 1"),
    ]
    assert stub.count("/competitors/") == 0


async def test_racing_disqualification_renders_dsq_not_a_finish() -> None:
    """A disqualified car reads "DSQ · Disqualified" instead of borrowing the
    race distance or the finishing gap it is still published with — the two
    shapes the six real DSQs in the sample take."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    # Full race distance and a finishing gap, then DQ'd: today this rendered
    # "+15.080" as if the car had placed second.
    stub.statuses["9200002"] = _core_status(kind="dsq", period=70)
    # Zero laps completed and a 53-second "gap": the other observed shape.
    stub.statistics["9200003"] = _core_stats(behindTime=(53472.0, "+53.472"))
    stub.statuses["9200003"] = _core_status(kind="dsq", period=0)
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert [(row.name, row.score, row.detail) for row in event.leaderboard] == [
        ("R. Vega", "1:39:56.180", "Winner · Corvane Works · #7 · Grid 2"),
        ("D. Okafor", "DSQ", "Halberd Racing · #4 · Grid 1 · Disqualified"),
        ("M. Castellane", "DSQ", "Halberd Racing · #9 · Grid 5 · Disqualified"),
        ("S. Larkwell", "DNF", "Meridian Motorsport · #21 · Grid 9 · Retired lap 13"),
    ]


async def test_racing_status_failure_infers_retirement_from_statistics() -> None:
    """With the status resource gone, an F1 car's statistics still separate the
    clear cases: a car parked 57 laps down is a DNF, and a lead-lap finisher
    with a race time is not."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    stub.statuses["9200002"] = TransientProviderError("status down")  # lead-lap finisher
    stub.statuses["9200004"] = _http_404()  # retired on lap 13
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    rendered = {row.name: (row.score, row.detail) for row in event.leaderboard}
    # abs(behindLaps) >= 4 / no ``pitsTaken``: measured precision 1.000 over
    # 611 cars.  The retirement lap comes from ``lapsCompleted`` now that the
    # status ``period`` is gone.
    assert rendered["S. Larkwell"] == ("DNF", "Meridian Motorsport · #21 · Grid 9 · Retired lap 13")
    # A nonzero ``totalTime`` proves a finisher (398 of 398), so no caveat.
    assert rendered["D. Okafor"] == ("+15.080", "Halberd Racing · #4 · Grid 1")
    assert _UNKNOWN_STATUS_PART not in rendered["D. Okafor"][1]


async def test_racing_status_failure_marks_the_undecidable_band() -> None:
    """A lapped car and a car that retired two laps from the end are provably
    indistinguishable from statistics (2025 Qatar GP), so the row says
    "Status unknown" rather than implying a finish — and keeps the lap deficit,
    which is true either way."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    stub.statuses["9200003"] = _http_404()  # one lap down, pitted, no race time
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    rendered = {row.name: (row.score, row.detail) for row in event.leaderboard}
    assert rendered["M. Castellane"] == (
        "+1 lap",
        f"Halberd Racing · #9 · Grid 5 · Fastest lap 1:22.000 · {_UNKNOWN_STATUS_PART}",
    )
    # Nobody else is hedged: their status answered.
    assert all(
        _UNKNOWN_STATUS_PART not in detail
        for name, (_, detail) in rendered.items()
        if name != "M. Castellane"
    )


async def test_racing_classified_lapped_car_with_status_is_never_hedged() -> None:
    """The status resource stays authoritative: a CLASSIFIED car one lap down
    renders as a plain lap deficit, with no DNF and no caveat, even though its
    statistics alone would land in the undecidable band."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    lapped = next(row for row in event.leaderboard if row.name == "M. Castellane")
    assert lapped.score == "+1 lap"
    assert lapped.detail == "Halberd Racing · #9 · Grid 5 · Fastest lap 1:22.000"


async def test_racing_enrichment_outage_keeps_the_plain_board() -> None:
    """The board parsed from the site scoreboard is the product: a core-API
    404 (canceled race / unpublished entry list), an exhausted-retry outage,
    and a 200 whose body isn't JSON all leave it intact and un-enriched."""
    board = _thin_field(("9200001", "R. Vega", 1), ("9200002", "D. Okafor", 2))
    for failure in (
        _http_404(),
        TransientProviderError("core api down"),
        ValueError("Expecting value: line 1 column 1"),
    ):
        provider = EspnProvider()
        stub = _CoreStub(scoreboard=_final_scoreboard(board), event_root=failure)
        provider._get_json = stub  # type: ignore[method-assign]

        event = await provider.get_event_state(RACING_LEAGUE, "9300001")
        assert event is not None
        assert [(row.name, row.position, row.score, row.detail) for row in event.leaderboard] == [
            ("R. Vega", 1, "", "Winner"),
            ("D. Okafor", 2, "", ""),
        ]
        # A failure is never memoized as an answer — the next poll retries.
        assert stub.count("/events/9300001") == 1


async def test_racing_enrichment_unexpected_shape_keeps_the_plain_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety net under the fetch-level degrade: an unannounced shape
    change blows up the garnish, never the board."""
    from app.providers.espn import provider as provider_module

    def boom(event_root: Any) -> Any:
        raise TypeError("competitors became a mapping overnight")

    monkeypatch.setattr(provider_module, "_racing_field", boom)
    provider = EspnProvider()
    stub = _finished_race_stub()
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert [(row.name, row.score) for row in event.leaderboard] == [
        ("R. Vega", ""),
        ("D. Okafor", ""),
        ("M. Castellane", ""),
        ("S. Larkwell", ""),
    ]


async def test_racing_enrichment_per_car_failure_keeps_tier_a_facts() -> None:
    """One car's statistics/status blowing up costs that car its time, not
    the whole board's enrichment."""
    provider = EspnProvider()
    stub = _finished_race_stub()
    stub.statistics["9200002"] = _http_404()
    stub.statuses["9200002"] = TransientProviderError("status down")
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    scores = {row.name: row.score for row in event.leaderboard}
    details = {row.name: row.detail for row in event.leaderboard}
    assert scores["D. Okafor"] == ""
    assert details["D. Okafor"] == "Halberd Racing · #4 · Grid 1"
    # Everyone else is unaffected.
    assert scores["R. Vega"] == "1:39:56.180"
    assert scores["S. Larkwell"] == "DNF"


async def test_racing_enrichment_skipped_for_golf_and_scheduled_races() -> None:
    """Enrichment is racing-only, and never spent on a weekend with no field
    or one whose entry list ESPN has not published (those resources 404)."""
    golf_league = League(
        id="crestmoor-golf-tour",
        sport=Sport.GOLF,
        name="Crestmoor Golf Tour",
        provider="espn",
        provider_key="golf/crestmoor",
    )
    provider = EspnProvider()

    async def forbidden(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        raise AssertionError(f"enrichment must not fetch here: {url}")

    provider._get_json = forbidden  # type: ignore[method-assign]
    golf_event = Event(
        id="espn:9300009",
        league_id=golf_league.id,
        name="Crestmoor Invitational",
        start_time=datetime(2026, 7, 23, 11, 0, tzinfo=timezone.utc),
        phase=GamePhase.FINAL,
        leaderboard=(
            LeaderRow(position=1, position_label="1", name="Marlow Fenwick", score="-10"),
        ),
    )
    assert await provider._enrich_racing(golf_league, [golf_event]) == [golf_event]

    scheduled = _parse_racing_event(
        _weekend(
            status=_status(state="pre", name="STATUS_SCHEDULED"),
            sessions=[_session(competitors=[_competitor(athlete_id="9200001", name="R. Vega")])],
        ),
        RACING_LEAGUE,
    )
    assert scheduled is not None
    assert await provider._enrich_racing(RACING_LEAGUE, [scheduled]) == [scheduled]


async def test_racing_enrichment_caps_the_per_car_fanout() -> None:
    """A stock-car-sized field cannot fan out past ``_ENRICH_MAX_CARS``, and
    what survives the cap is the FRONT of the field — the rows anyone
    actually reads."""
    size = _ENRICH_MAX_CARS + 5
    rows = [(f"920{index:04d}", f"Driver {index}", index) for index in range(1, size + 1)]
    provider = EspnProvider()
    stub = _CoreStub(
        scoreboard=_final_scoreboard(_thin_field(*rows)),
        event_root=_core_event_root(
            [
                _core_car(athlete_id=athlete_id, order=order, start_order=order, number=str(order))
                for athlete_id, _, order in rows
            ]
        ),
        statistics={
            athlete_id: _core_stats(lapsCompleted=(160.0, "160")) for athlete_id, _, _ in rows
        },
        statuses={athlete_id: _core_status(period=160) for athlete_id, _, _ in rows},
    )
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert stub.count("/competitors/", "/statistics") == _ENRICH_MAX_CARS
    assert stub.count("/competitors/", "/status") == _ENRICH_MAX_CARS
    # The back of the field kept its tier-A facts and stayed time-less.
    scores = [row.score for row in event.leaderboard]
    assert scores[:_ENRICH_MAX_CARS] == ["160 laps"] * _ENRICH_MAX_CARS
    assert scores[_ENRICH_MAX_CARS:] == [""] * 5
    assert all(row.detail for row in event.leaderboard)


async def test_racing_enrichment_stock_car_degrades_to_laps() -> None:
    """A stock-car board has no per-car time and no status resource at all:
    the entrant comes from ``vehicle.team`` (``manufacturer`` there is the
    OEM), laps-behind arrives NEGATIVE, and no status call is issued."""
    provider = EspnProvider()
    board = _thin_field(("9200005", "T. Quillfeather", 1), ("9200006", "B. Ashdown", 2))
    stub = _CoreStub(
        scoreboard={
            "events": [
                _weekend(
                    event_id="9300001",
                    end_date=None,
                    status=_status(state="post", name="STATUS_FINAL"),
                    circuit=None,
                    sessions=[
                        _session(
                            abbreviation=None,
                            status=_status(state="post", name="STATUS_FINAL", period=160),
                            competitors=board,
                        )
                    ],
                )
            ]
        },
        event_root=_core_event_root(
            [
                _core_car(
                    athlete_id="9200005",
                    order=1,
                    start_order=4,
                    winner=True,
                    number="18",
                    manufacturer="Quillbrook Motors",
                    team="Ashdown Speedworks",
                    with_status=False,
                ),
                _core_car(
                    athlete_id="9200006",
                    order=2,
                    start_order=11,
                    number="33",
                    manufacturer="Quillbrook Motors",
                    # ``team`` is null for a slice of every stock-car field;
                    # the OEM is the honest fallback there.
                    with_status=False,
                ),
            ],
            abbreviation=None,
        ),
        statistics={
            "9200005": _core_stats(
                lapsCompleted=(160.0, "160"),
                behindLaps=(0.0, "0"),
                # Pinned-at-zero placeholders must never render as a time.
                behindTime=(0.0, ".000"),
                fastestLap=(0.0, "0.000"),
            ),
            "9200006": _core_stats(
                lapsCompleted=(158.0, "158"),
                behindLaps=(-2.0, "-2"),
                behindTime=(0.0, ".000"),
            ),
        },
    )
    provider._get_json = stub  # type: ignore[method-assign]

    event = await provider.get_event_state(RACING_LEAGUE, "9300001")
    assert event is not None
    assert [(row.score, row.detail) for row in event.leaderboard] == [
        ("160 laps", "Winner · Ashdown Speedworks · #18 · Grid 4"),
        ("+2 laps", "Quillbrook Motors · #33 · Grid 11"),
    ]
    assert stub.count("/competitors/", "/status") == 0


# ---------------------------------------------------------------------------
# Enrichment helpers (pure)
# ---------------------------------------------------------------------------


def test_racing_field_ignores_refs_off_the_core_host() -> None:
    """A ``$ref`` is a URL arriving inside a remote payload; one that doesn't
    address the core API is never followed."""
    car = _core_car(athlete_id="9200001", order=1, start_order=3)
    car["statistics"] = {"$ref": "https://evil.example.com/v2/sports/racing/steal"}
    car["status"] = {"$ref": "/relative/path"}
    competition_id, cars = _racing_field(_core_event_root([car]))
    assert competition_id == "9400001"
    assert cars["9200001"].statistics_url is None
    assert cars["9200001"].status_url is None
    assert (cars["9200001"].team, cars["9200001"].number, cars["9200001"].grid) == (
        "Corvane Works",
        "7",
        3,
    )
    # Nothing to enrich from: a canceled/unrun weekend has no field.
    assert _racing_field({"competitions": []}) == (None, {})
    assert _racing_field("nope") == (None, {})


def test_racing_stats_absolute_laps_behind_and_drops_zero_placeholders() -> None:
    stats = _parse_racing_stats(
        _core_stats(
            lapsCompleted=(158.0, "158"),
            behindLaps=(-2.0, "-2"),  # stock-car sign
            behindTime=(0.0, ".000"),  # placeholder
            fastestLap=(0.0, "0.000"),  # placeholder
        )
    )
    assert stats is not None
    assert (stats.laps_completed, stats.laps_behind) == (158, 2)
    assert stats.behind_time is None and stats.fastest_lap is None and stats.total_time is None
    # A payload with no categories at all yields nothing to render.
    assert _parse_racing_stats({"splits": {"categories": []}}) is None
    assert _parse_racing_stats(None) is None


def test_racing_car_status_maps_all_three_types_by_name() -> None:
    """All THREE published types are mapped by exact name — a substring test
    is what let ``STATUS_DISQUALIFIED`` through as a finisher."""
    assert _parse_racing_car_status(_core_status(kind="retired", period=13)) == (
        _OUTCOME_RETIRED,
        13,
    )
    assert _parse_racing_car_status(_core_status(kind="dsq", period=70)) == (
        _OUTCOME_DISQUALIFIED,
        70,
    )
    assert _parse_racing_car_status(_core_status(period=70)) == (_OUTCOME_CLASSIFIED, 70)
    # A live board is ``completed: false`` for every car; inferring
    # retirement from that flag would brand the whole field a DNF.  An
    # unrecognized name is an honest "not read" — never a silent "finished".
    assert _parse_racing_car_status(
        {"period": 44, "type": {"name": "STATUS_IN_PROGRESS", "completed": False}}
    ) == (_OUTCOME_NONE, 44)
    assert _parse_racing_car_status(None) == (_OUTCOME_NONE, None)


def _f1_car(*, with_status_ref: bool = True) -> _RacingCar:
    """One tier-A car; ``with_status_ref`` is what makes it an F1 entrant (no
    other series exposes a per-competitor status resource at all)."""
    return _RacingCar(
        team="Halberd Racing",
        number="9",
        grid=5,
        order=3,
        winner=False,
        statistics_url=_core_ref("statistics", "9200003"),
        status_url=_core_ref("status", "9200003") if with_status_ref else None,
    )


@pytest.mark.parametrize(
    ("stats", "expected_score", "hedged"),
    [
        # abs(behindLaps) >= 4: 0 false positives over 529 classified cars.
        (
            _core_stats(lapsCompleted=(13.0, "13"), behindLaps=(57.0, "57"), pitsTaken=(1.0, "1")),
            "DNF",
            False,
        ),
        # ``lapsCompleted`` absent or zero: 17 of 17 such cars were retired.
        (_core_stats(place=(19.0, "19"), pitsTaken=(0.0, "0")), "DNF", False),
        # ``pitsTaken`` absent: 26 of 26, and never a classified car.
        (_core_stats(lapsCompleted=(41.0, "41"), behindLaps=(16.0, "16")), "DNF", False),
        # A nonzero ``totalTime`` proves a finisher: 398 of 398.
        (
            _core_stats(
                lapsCompleted=(70.0, "70"),
                totalTime=(5996180.0, "1:39:56.180"),
                pitsTaken=(2.0, "2"),
            ),
            "1:39:56.180",
            False,
        ),
        # The undecidable band: one to three laps down, pitted, no race time.
        (
            _core_stats(lapsCompleted=(69.0, "69"), behindLaps=(1.0, "1"), pitsTaken=(3.0, "3")),
            "+1 lap",
            True,
        ),
        (
            _core_stats(lapsCompleted=(55.0, "55"), behindLaps=(3.0, "3"), pitsTaken=(3.0, "3")),
            "+3 laps",
            True,
        ),
        # Lead lap, pitted, but no time published either way.
        (_core_stats(lapsCompleted=(70.0, "70"), pitsTaken=(2.0, "2")), "70 laps", True),
    ],
)
def test_racing_statistics_only_inference_matrix(
    stats: dict[str, Any], expected_score: str, hedged: bool
) -> None:
    """The fallback used when an F1 car's status call failed: confident DNF
    where the corpus measured zero false positives, an explicit caveat where
    a retirement and a lapped finish are provably the same numbers."""
    parsed = _parse_racing_stats(stats)
    assert parsed is not None
    facts = _racing_facts({"9200003": _f1_car()}, {"9200003": parsed}, {})
    rendered = facts["9200003"]
    assert rendered.score == expected_score
    assert (_UNKNOWN_STATUS_PART in rendered.detail) is hedged


def test_racing_no_status_resource_is_never_inferred_from_laps() -> None:
    """Stock-car/IndyCar cars have no status resource (it answers 400), and
    their lap counts are not comparable to F1's: a car eight laps down at a
    short track is still circulating.  Nothing is inferred, nothing is hedged
    — the row renders exactly as it did before the fallback existed."""
    stats = _parse_racing_stats(_core_stats(lapsCompleted=(442.0, "442"), behindLaps=(-8.0, "-8")))
    assert stats is not None
    facts = _racing_facts({"9200005": _f1_car(with_status_ref=False)}, {"9200005": stats}, {})
    assert facts["9200005"].score == "+8 laps"
    assert facts["9200005"].detail == "Halberd Racing · #9 · Grid 5"


def test_racing_unknown_status_marker_survives_a_long_detail() -> None:
    """The caveat is the one detail part whose loss would change what the row
    MEANS, so the bound trims the facts in front of it instead."""
    car = replace(
        _f1_car(),
        team="Interlagos Grand Prix Racing Constructors International",
        winner=True,
    )
    stats = _parse_racing_stats(
        _core_stats(lapsCompleted=(69.0, "69"), behindLaps=(1.0, "1"), pitsTaken=(3.0, "3"))
    )
    assert stats is not None
    detail = _racing_facts({"9200003": car}, {"9200003": stats}, {})["9200003"].detail
    assert len(detail) <= 80
    assert detail.endswith(f"· {_UNKNOWN_STATUS_PART}")


def test_racing_lap_label_only_extends_a_lap_label() -> None:
    assert _racing_lap_label("Race · Lap 44", 70) == "Race · Lap 44 of 70"
    assert _racing_lap_label("Race · Lap 44", None) == "Race · Lap 44"
    assert _racing_lap_label("Race · Lap 44", 0) == "Race · Lap 44"
    # A practice/qualifying label and a finished weekend's empty one are
    # left exactly as the parser built them.
    assert _racing_lap_label("Qual", 70) == "Qual"
    assert _racing_lap_label("", 70) == ""


def test_racing_race_distance_reads_the_top_level_aggregate() -> None:
    assert _racing_race_distance(_core_distance(160)) == 160
    assert _racing_race_distance({"categories": []}) is None
    assert _racing_race_distance(None) is None


def test_apply_racing_facts_only_ever_adds() -> None:
    """Enrichment never blanks a column the site scoreboard filled, and a
    row with no matching car is returned untouched."""
    board = (
        LeaderRow(
            position=1,
            position_label="1",
            name="R. Vega",
            score="",
            detail="Winner",
            player_id="9200001",
        ),
        LeaderRow(
            position=2,
            position_label="2",
            name="D. Okafor",
            score="",
            detail="",
            player_id="9200002",
        ),
        LeaderRow(
            position=3,
            position_label="3",
            name="M. Castellane",
            score="",
            detail="",
            player_id=None,
        ),
    )
    _, cars = _racing_field(
        _core_event_root([_core_car(athlete_id="9200001", order=1, start_order=2, winner=True)])
    )
    facts = _racing_facts(cars, {}, {})
    enriched = _apply_racing_facts(board, facts)
    assert [(row.name, row.score, row.detail) for row in enriched] == [
        ("R. Vega", "", "Winner · Corvane Works · #7 · Grid 2"),
        ("D. Okafor", "", ""),
        ("M. Castellane", "", ""),
    ]
    # No facts at all: the same board object comes straight back.
    assert _apply_racing_facts(board, {}) is board


def test_racing_enrich_targets_are_bounded_live_first() -> None:
    """Live races come first, then the most recently started finished ones —
    and never more than the cap, so a season-wide window fetch cannot fan
    out over two dozen finished races."""

    def event(key: str, phase: GamePhase, day: int) -> Event:
        return Event(
            id=f"espn:{key}",
            league_id=RACING_LEAGUE.id,
            name=f"Round {key}",
            start_time=datetime(2026, 7, day, 13, 0, tzinfo=timezone.utc),
            phase=phase,
            leaderboard=(LeaderRow(position=1, position_label="1", name="R. Vega", score=""),),
        )

    events = [
        event("a", GamePhase.FINAL, 5),
        event("b", GamePhase.FINAL, 26),
        event("c", GamePhase.SCHEDULED, 30),  # no entry list published yet
        event("d", GamePhase.IN_PROGRESS, 12),
        event("e", GamePhase.FINAL, 19),
    ]
    assert [e.id for e in _racing_enrich_targets(events)][:_ENRICH_MAX_EVENTS] == [
        "espn:d",
        "espn:b",
    ]
    assert len(_racing_enrich_targets(events)) == _ENRICH_MAX_EVENTS
    # A board-less event is not worth a call even when it is live.
    assert _racing_enrich_targets([replace_board(event("f", GamePhase.IN_PROGRESS, 12))]) == []


def replace_board(event: Event) -> Event:
    """The same event with an empty leaderboard (a pre-race weekend)."""
    return Event(
        id=event.id,
        league_id=event.league_id,
        name=event.name,
        start_time=event.start_time,
        phase=event.phase,
        leaderboard=(),
    )


# ---------------------------------------------------------------------------
# Scheduler helpers: sport-neutral leaderboard tagging
# ---------------------------------------------------------------------------


def test_athlete_id_map_covers_golf_and_racing() -> None:
    golf_league = League(
        id="crestmoor-golf-tour",
        sport=Sport.GOLF,
        name="Crestmoor Golf Tour",
        provider="espn",
        provider_key="golf/crestmoor",
    )
    hoops_league = League(
        id="crestline-basketball",
        sport=Sport.BASKETBALL,
        name="Crestline Basketball",
        provider="espn",
        provider_key="basketball/crestline",
    )
    leagues = {lg.id: lg for lg in (RACING_LEAGUE, golf_league, hoops_league)}
    teams = [
        Team(
            id="apex-circuit-r-vega",
            league_id=RACING_LEAGUE.id,
            name="R. Vega",
            abbreviation="VEG",
            provider_key="9200001",
        ),
        Team(
            id="crestmoor-marlow-fenwick",
            league_id=golf_league.id,
            name="Marlow Fenwick",
            abbreviation="MF",
            provider_key="90001",
        ),
        # A game-sport team sharing a provider_key string must not mis-tag.
        Team(
            id="crestline-harborlight",
            league_id=hoops_league.id,
            name="Harborlight Pelicans",
            abbreviation="HLP",
            provider_key="9200001",
        ),
    ]
    assert _is_leaderboard(RACING_LEAGUE) and _is_leaderboard(golf_league)
    assert not _is_leaderboard(hoops_league)
    mapping = _athlete_id_map(teams, leagues)
    assert mapping == {"9200001": "apex-circuit-r-vega", "90001": "crestmoor-marlow-fenwick"}


def test_tag_followed_athletes_rewrites_player_ids() -> None:
    event = Event(
        id="espn:9300001",
        league_id=RACING_LEAGUE.id,
        name="Grand Prix of Veltria",
        start_time=datetime(2026, 7, 24, 11, 30, tzinfo=timezone.utc),
        phase=GamePhase.FINAL,
        leaderboard=(
            LeaderRow(
                position=1, position_label="1", name="R. Vega", score="", player_id="9200001"
            ),
            LeaderRow(
                position=2, position_label="2", name="D. Okafor", score="", player_id="9200002"
            ),
            LeaderRow(
                position=3, position_label="3", name="M. Castellane", score="", player_id=None
            ),
        ),
    )
    tagged = _tag_followed_athletes(event, {"9200001": "apex-circuit-r-vega"})
    assert [row.player_id for row in tagged.leaderboard] == [
        "apex-circuit-r-vega",  # followed: ESPN id -> internal team id
        None,  # unfollowed: dropped, never persisted as an ESPN id
        None,  # already None: untouched
    ]


# ---------------------------------------------------------------------------
# Catalog: racing branch (driver standings unioned with the scoreboard field)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> Iterator[None]:
    """The 1h teams cache is module-global; isolate each test from it."""
    espn_catalog._cache.clear()
    yield
    espn_catalog._cache.clear()


def _driver_standings_payload() -> dict[str, Any]:
    """An ``/apis/v2`` standings shape: a driver child + a constructor child."""
    return {
        "name": "Apex Circuit Series",
        "children": [
            {
                "name": "Driver Standings",
                "standings": {
                    "entries": [
                        {
                            "athlete": {
                                "id": "9200001",
                                "displayName": "R. Vega",
                                "abbreviation": "VEG",
                            },
                            "stats": [{"name": "rank", "value": 1.0}],
                        },
                        {
                            # No abbreviation: exercise the initials fallback.
                            "athlete": {"id": "9200002", "displayName": "Dara Okafor"},
                            "stats": [],
                        },
                        # Malformed (missing id) must be skipped, not crash.
                        {"athlete": {"displayName": "Nameless"}},
                    ]
                },
            },
            {
                "name": "Constructor Standings",
                "standings": {
                    # Constructor rows carry ``team`` — silently passed over.
                    "entries": [{"team": {"id": "77", "displayName": "Vega Motorworks"}}]
                },
            },
        ],
    }


def _racing_scoreboard_payload() -> dict[str, Any]:
    """A racing scoreboard whose Qual + Race sessions share a driver."""
    qual = _session(
        session_id="9400013",
        abbreviation="Qual",
        type_id="2",
        competitors=[_competitor(athlete_id="9200001", name="R. Vega", order=1)],
    )
    race = _session(
        competitors=[
            _competitor(athlete_id="9200001", name="R. Vega", order=1),
            _competitor(athlete_id="9200005", name="T. Quillfeather", order=2),
        ]
    )
    return {"events": [_weekend(sessions=[qual, race])]}


async def test_get_league_teams_racing_unions_standings_with_scoreboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ranked drivers lead; a scoreboard-only entrant (a substitute with no
    championship entry) is appended after them, deduped by athlete id."""
    seen: list[str] = []

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> Any:
        seen.append(url)
        if "/apis/v2/" in url:
            return _driver_standings_payload()
        return _racing_scoreboard_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    teams = await espn_catalog.get_league_teams(RACING_CATALOG_LEAGUE)

    # Standings off the /apis/v2/ host (the site host's is a stub), then
    # the site scoreboard for this weekend's extras.
    assert seen == [
        "https://site.api.espn.com/apis/v2/sports/racing/apex/standings",
        "https://site.api.espn.com/apis/site/v2/sports/racing/apex/scoreboard",
    ]
    # Standings rank order first; the scoreboard's Vega deduped away; the
    # scoreboard-only Quillfeather pickable at the back.
    assert [t.provider_key for t in teams] == ["9200001", "9200002", "9200005"]
    vega, okafor, quillfeather = teams
    assert vega.name == "R. Vega"
    assert vega.abbreviation == "VEG"
    # Headshots aren't in the payload; the shared racing headshot path is
    # constructed from the athlete id.
    assert vega.logo_url == "https://a.espncdn.com/i/headshots/rpm/players/full/9200001.png"
    assert vega.color is None
    assert okafor.abbreviation == "DO"  # initials fallback
    assert quillfeather.name == "T. Quillfeather"


async def test_get_league_teams_racing_scoreboard_failure_standings_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scoreboard outage with standings in hand degrades to standings-only
    (no weekend extras this refresh) instead of a 502."""

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> Any:
        if "/apis/v2/" in url:
            return _driver_standings_payload()
        raise EspnCatalogError("scoreboard down")

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    teams = await espn_catalog.get_league_teams(RACING_CATALOG_LEAGUE)
    assert [t.provider_key for t in teams] == ["9200001", "9200002"]


async def test_get_league_teams_racing_falls_back_to_scoreboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty standings feed falls back to the current race's competitors,
    deduped across sessions by the competitor-level athlete id."""
    seen: list[str] = []

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> Any:
        seen.append(url)
        if "/apis/v2/" in url:
            return {"children": []}
        return _racing_scoreboard_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    teams = await espn_catalog.get_league_teams(RACING_CATALOG_LEAGUE)

    assert seen == [
        "https://site.api.espn.com/apis/v2/sports/racing/apex/standings",
        "https://site.api.espn.com/apis/site/v2/sports/racing/apex/scoreboard",
    ]
    assert [t.provider_key for t in teams] == ["9200001", "9200005"]
    assert [t.name for t in teams] == ["R. Vega", "T. Quillfeather"]


async def test_get_league_teams_racing_standings_failure_uses_scoreboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standings outage degrades to the scoreboard instead of a 502."""

    async def fake_fetch(url: str, params: dict[str, str], league_id: str) -> Any:
        if "/apis/v2/" in url:
            raise EspnCatalogError("standings down")
        return _racing_scoreboard_payload()

    monkeypatch.setattr(espn_catalog, "_fetch_json", fake_fetch)
    teams = await espn_catalog.get_league_teams(RACING_CATALOG_LEAGUE)
    assert [t.provider_key for t in teams] == ["9200001", "9200005"]


async def test_get_league_teams_racing_both_sources_down_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(url: str, params: dict[str, str], league_id: str) -> Any:
        raise EspnCatalogError("everything down")

    monkeypatch.setattr(espn_catalog, "_fetch_json", boom)
    with pytest.raises(EspnCatalogError):
        await espn_catalog.get_league_teams(RACING_CATALOG_LEAGUE)
