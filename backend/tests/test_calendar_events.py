"""Leaderboard competitions in the iCalendar feed.

A golf tournament (golf is the only sport modeled as an ``Event``) spans
DAYS, so it renders as an all-day ``VEVENT`` with ``VALUE=DATE`` values
rather than a clocked window — and RFC 5545 makes ``DTEND`` EXCLUSIVE for
DATE values, which is the classic off-by-one this module pins down.

The rendering tests pin ``SPORTSDASH_TIMEZONE`` so the emitted DATEs can
be asserted literally: the values are local calendar days in the
configured timezone (the same response-boundary conversion ``/schedule``
and ``/today`` apply), not UTC dates.  Fictional names throughout.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.models.orm import EventORM, GameORM, LeagueORM, TeamORM
from app.routes import router as api_router
from app.services.ics import games_to_ics
from app.timeutil import utcnow
from tests.db_engine import create_test_schema, make_test_engine

GOLF_LEAGUE_ID = "gilded-links"
GOLF_LEAGUE_NAME = "Gilded Links Tour"
BALL_LEAGUE_ID = "violet-circuit"
TEAM_FOXES = "glimmer-foxes"

EVENT_OPEN = "mock:gl-open"
EVENT_INVITATIONAL = "mock:gl-invitational"

# UTC-8/-7: an instant late in the local evening falls on the NEXT UTC day,
# so a naive UTC-date implementation shifts every boundary case by one.
FEED_TIMEZONE = "America/Los_Angeles"


@pytest.fixture
def pinned_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "timezone", FEED_TIMEZONE)


def make_league(
    league_id: str = GOLF_LEAGUE_ID,
    sport: str = "golf",
    name: str = GOLF_LEAGUE_NAME,
) -> LeagueORM:
    return LeagueORM(
        id=league_id,
        sport=sport,
        name=name,
        provider="mock",
        provider_key=f"mock-{sport}",
    )


def make_event(
    event_id: str = EVENT_OPEN,
    *,
    start_time: datetime,
    end_time: datetime | None,
    name: str = "The Gilded Open",
    phase: str = "scheduled",
    venue: str | None = None,
    leaderboard: list | None = None,
) -> EventORM:
    return EventORM(
        id=event_id,
        league_id=GOLF_LEAGUE_ID,
        name=name,
        start_time=start_time,
        end_time=end_time,
        venue=venue,
        phase=phase,
        round_label="",
        leaderboard=leaderboard if leaderboard is not None else [],
    )


def lines_of(body: str) -> list[str]:
    return body.split("\r\n")


def event_uid(event_id: str) -> str:
    """The UID line a leaderboard event renders.

    Spelled out literally rather than imported from ``ics`` so the wire
    format is pinned: the ``event:`` prefix keeps an event's identity apart
    from a game's inside the one ``VCALENDAR`` that holds both kinds.
    """
    return f"UID:event:{event_id}@sportsdash"


def game_uid(game_id: str) -> str:
    return f"UID:{game_id}@sportsdash"


# ---------------------------------------------------------------------------
# All-day rendering
# ---------------------------------------------------------------------------


def test_event_spans_its_days_with_an_exclusive_dtend(pinned_timezone: None) -> None:
    """A Thursday–Sunday major: DTEND is the MONDAY (exclusive)."""
    row = make_event(
        # Local 07:00 on Jul 16 .. local 16:00 on Jul 19 (Pacific).
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
        venue="Gilded Links, Sea Course",
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert body.count("BEGIN:VEVENT") == 1
    assert event_uid(EVENT_OPEN) in body
    assert "DTSTART;VALUE=DATE:20260716" in body
    assert "DTEND;VALUE=DATE:20260720" in body
    assert f"SUMMARY:The Gilded Open ({GOLF_LEAGUE_NAME})" in body
    # Comma in the venue is RFC 5545 TEXT-escaped, like a game's location.
    assert "LOCATION:Gilded Links\\, Sea Course" in body
    # An all-day entry carries DATE values only — never the timed forms.
    assert not any(line.startswith(("DTSTART:", "DTEND:")) for line in lines_of(body)), (
        "all-day event must not emit timed DTSTART/DTEND"
    )


def test_event_without_end_time_is_a_single_day(pinned_timezone: None) -> None:
    row = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=None,
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DTSTART;VALUE=DATE:20260716" in body
    assert "DTEND;VALUE=DATE:20260717" in body


def test_dates_are_local_days_not_utc_days(pinned_timezone: None) -> None:
    """02:00 UTC is still the previous evening in the configured timezone."""
    row = make_event(
        # Local Jul 15 19:00 .. Jul 18 20:00 (Pacific).
        start_time=datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc),
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DTSTART;VALUE=DATE:20260715" in body
    assert "DTEND;VALUE=DATE:20260719" in body


def test_end_before_start_collapses_to_a_single_day(pinned_timezone: None) -> None:
    """Bad provider data must not emit a backwards (invalid) span."""
    row = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DTSTART;VALUE=DATE:20260716" in body
    assert "DTEND;VALUE=DATE:20260717" in body


def test_span_crosses_a_month_boundary(pinned_timezone: None) -> None:
    row = make_event(
        start_time=datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 31, 23, 0, tzinfo=timezone.utc),
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DTSTART;VALUE=DATE:20260730" in body
    assert "DTEND;VALUE=DATE:20260801" in body


def test_final_event_describes_the_winner(pinned_timezone: None) -> None:
    row = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
        phase="final",
        leaderboard=[
            {"position": 1, "position_label": "1", "name": "Soren Larkspur", "score": "-14"},
            {"position": 2, "position_label": "2", "name": "Iva Thornebury", "score": "-11"},
        ],
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DESCRIPTION:Winner: Soren Larkspur (-14)" in body


def test_scheduled_event_has_no_description(pinned_timezone: None) -> None:
    row = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
        leaderboard=[
            {"position": 1, "position_label": "1", "name": "Soren Larkspur", "score": "-3"},
        ],
    )

    body = games_to_ics([], {GOLF_LEAGUE_ID: make_league()}, events=[row])

    assert "DESCRIPTION:" not in body


def test_games_and_events_share_one_vcalendar(pinned_timezone: None) -> None:
    game = GameORM(
        id="mock:vc-1",
        league_id=BALL_LEAGUE_ID,
        home_team_id=TEAM_FOXES,
        away_team_id=None,
        home_name="Glimmer Foxes",
        away_name="Quarry Hawks",
        home_abbreviation="GLF",
        away_abbreviation="QRH",
        start_time=datetime(2026, 7, 17, 23, 30, tzinfo=timezone.utc),
        venue="Foxhollow Arena",
        phase="scheduled",
        home_score=0,
        away_score=0,
        period=0,
        period_label="",
        is_intermission=False,
    )
    event = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
    )
    leagues = {
        GOLF_LEAGUE_ID: make_league(),
        BALL_LEAGUE_ID: make_league(BALL_LEAGUE_ID, "basketball", "Violet Circuit Basketball"),
    }

    body = games_to_ics([game], leagues, events=[event])

    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert body.count("BEGIN:VCALENDAR") == 1
    assert body.count("BEGIN:VEVENT") == 2
    assert body.count("END:VEVENT") == 2
    # The game keeps its timed form alongside the event's all-day one.
    assert "DTSTART:20260717T233000Z" in body
    assert "DTSTART;VALUE=DATE:20260716" in body


def test_event_uid_is_prefixed_so_it_cannot_collide_with_a_game(
    pinned_timezone: None,
) -> None:
    """Same id string on both kinds -> two distinct UIDs in one VCALENDAR.

    Game ids and event ids are both ``"{provider}:{key}"`` and share this
    one container, so only the prefix keeps them apart — a shared UID would
    have a subscribed client treat the two entries as one.
    """
    shared_id = "mock:same-key"
    game = GameORM(
        id=shared_id,
        league_id=BALL_LEAGUE_ID,
        home_team_id=TEAM_FOXES,
        away_team_id=None,
        home_name="Glimmer Foxes",
        away_name="Quarry Hawks",
        home_abbreviation="GLF",
        away_abbreviation="QRH",
        start_time=datetime(2026, 7, 17, 23, 30, tzinfo=timezone.utc),
        venue="Foxhollow Arena",
        phase="scheduled",
        home_score=0,
        away_score=0,
        period=0,
        period_label="",
        is_intermission=False,
    )
    event = make_event(
        shared_id,
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
    )
    leagues = {
        GOLF_LEAGUE_ID: make_league(),
        BALL_LEAGUE_ID: make_league(BALL_LEAGUE_ID, "basketball", "Violet Circuit Basketball"),
    }

    body = games_to_ics([game], leagues, events=[event])

    uids = [line for line in lines_of(body) if line.startswith("UID:")]
    assert uids == [game_uid(shared_id), event_uid(shared_id)]
    assert len(set(uids)) == 2


def test_event_with_unknown_league_still_renders(pinned_timezone: None) -> None:
    """Degrade to the league id in the summary rather than dropping the event."""
    row = make_event(
        start_time=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
        end_time=None,
    )

    body = games_to_ics([], {}, events=[row])

    assert f"SUMMARY:The Gilded Open ({GOLF_LEAGUE_ID})" in body


# ---------------------------------------------------------------------------
# Route: /api/calendar.ics
# ---------------------------------------------------------------------------


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def seeded(db: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with db() as session:
        session.add(make_league())
        session.add(make_league(BALL_LEAGUE_ID, "basketball", "Violet Circuit Basketball"))
        # Parents flushed before children — Postgres enforces the FKs.
        await session.flush()
        session.add(
            TeamORM(
                id=TEAM_FOXES,
                league_id=BALL_LEAGUE_ID,
                name="Glimmer Foxes",
                abbreviation="GLF",
                provider_key="glimmer-foxes",
                rss_feeds=[],
            )
        )
        await session.flush()
        session.add(
            GameORM(
                id="mock:vc-soon",
                league_id=BALL_LEAGUE_ID,
                home_team_id=TEAM_FOXES,
                away_team_id=None,
                home_name="Glimmer Foxes",
                away_name="Quarry Hawks",
                home_abbreviation="GLF",
                away_abbreviation="QRH",
                start_time=now + timedelta(days=2),
                venue="Foxhollow Arena",
                phase="scheduled",
            )
        )
        # Straddles the window start: began 40 days ago, ran until 10 days ago.
        session.add(
            make_event(
                EVENT_OPEN,
                start_time=now - timedelta(days=40),
                end_time=now - timedelta(days=10),
            )
        )
        # Starts beyond the +60d horizon.
        session.add(
            make_event(
                EVENT_INVITATIONAL,
                start_time=now + timedelta(days=120),
                end_time=now + timedelta(days=123),
                name="The Gilded Invitational",
            )
        )
        await session.commit()


@pytest.fixture
async def client(db: async_sessionmaker[AsyncSession], seeded: None) -> AsyncIterator[AsyncClient]:
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_feed_includes_events_overlapping_the_window(client: AsyncClient) -> None:
    resp = await client.get("/api/calendar.ics")

    assert resp.status_code == 200
    body = resp.text
    # The tournament started before the -30d window opened but was still
    # running inside it, so it belongs in the feed.
    assert event_uid(EVENT_OPEN) in body
    assert event_uid(EVENT_INVITATIONAL) not in body
    assert game_uid("mock:vc-soon") in body
    assert body.count("BEGIN:VEVENT") == 2


async def test_team_filtered_feed_omits_events(client: AsyncClient) -> None:
    """Events have no team side; a per-team feed is that team's games only."""
    resp = await client.get("/api/calendar.ics", params={"team_id": TEAM_FOXES})

    assert resp.status_code == 200
    body = resp.text
    assert game_uid("mock:vc-soon") in body
    assert event_uid(EVENT_OPEN) not in body
    assert body.count("BEGIN:VEVENT") == 1
