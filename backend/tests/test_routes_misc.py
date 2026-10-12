"""HTTP-level tests for the previously untested routes: /events,
/scorers, /nation, /bracket, and /leaders — plus the two fan-out routes
(/scorers, /odds) behind a real circuit breaker.

Same harness style as test_api.py: an in-memory SQLite app with the
``get_session`` dependency overridden, requests through ASGITransport,
fictional names only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.db import get_session
from tests.db_engine import create_test_schema, make_test_engine
from app.models.domain import GameOdds, GameSummary, Goal
from app.models.orm import EventORM, GameORM, LeagueORM, PlayerORM, StandingsORM, TeamORM
from app.providers import espn_leaders, espn_playoffs, registry
from app.routes import router as api_router
from app.routes import scorers as scorers_route
from app.services import cache, circuit_breaker
from app.timeutil import utcnow

LEAGUE_ID = "violet-circuit"
GOLF_LEAGUE_ID = "gilded-links"
# /scorers only walks soccer leagues (goals are parsed for no other sport).
SOCCER_LEAGUE_ID = "azure-coast-cup"
# /bracket and the league-wide /leaders board only ask ESPN for an ESPN
# league in a supported sport; seeded per test by ``_seed_espn_league``.
ESPN_LEAGUE_ID = "harbor-hoops"
# A soccer league bound to the "espn" provider id, the one the registry puts
# a circuit breaker in front of; seeded by the ``guarded_espn`` fixture.
GUARDED_LEAGUE_ID = "harbor-cup"
EVENT_ID = "mock:gl-open"


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
        session.add(
            LeagueORM(
                id=LEAGUE_ID,
                sport="basketball",
                name="Violet Circuit Basketball",
                provider="mock",
                provider_key="mock-basketball",
            )
        )
        session.add(
            LeagueORM(
                id=GOLF_LEAGUE_ID,
                sport="golf",
                name="Gilded Links Tour",
                provider="mock",
                provider_key="mock-golf",
            )
        )
        session.add(
            LeagueORM(
                id=SOCCER_LEAGUE_ID,
                sport="soccer",
                name="Azure Coast Cup",
                provider="mock",
                provider_key="mock-soccer",
            )
        )
        # Parents flushed before children — Postgres enforces the FKs.
        await session.flush()
        session.add(
            TeamORM(
                id="glimmer-foxes",
                league_id=LEAGUE_ID,
                name="Glimmer Foxes",
                abbreviation="GLF",
                provider_key="glimmer-foxes",
                rss_feeds=[],
            )
        )
        session.add(
            EventORM(
                id=EVENT_ID,
                league_id=GOLF_LEAGUE_ID,
                name="The Gilded Open",
                start_time=now + timedelta(days=2),
                phase="scheduled",
                round_label="",
                leaderboard=[],
            )
        )
        session.add(
            StandingsORM(
                league_id=LEAGUE_ID,
                season="2026",
                rows=[
                    {
                        "rank": 1,
                        "team_name": "Glimmer Foxes",
                        "abbreviation": "GLF",
                        "wins": 10,
                        "losses": 2,
                        "group": "Harbor Division",
                    }
                ],
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


# --- /events ---------------------------------------------------------------


async def test_events_lists_events_in_default_window(client: AsyncClient) -> None:
    response = await client.get("/api/events")
    assert response.status_code == 200
    payload = response.json()
    assert [event["id"] for event in payload] == [EVENT_ID]
    assert payload[0]["name"] == "The Gilded Open"
    assert payload[0]["league_id"] == GOLF_LEAGUE_ID


async def test_events_respects_explicit_window(client: AsyncClient) -> None:
    response = await client.get("/api/events", params={"start": "2000-01-01", "end": "2000-01-02"})
    assert response.status_code == 200
    assert response.json() == []


async def test_event_detail_and_404(client: AsyncClient) -> None:
    ok = await client.get(f"/api/events/{EVENT_ID}")
    assert ok.status_code == 200
    assert ok.json()["id"] == EVENT_ID

    missing = await client.get("/api/events/mock:nope")
    assert missing.status_code == 404


# --- /scorers ----------------------------------------------------------------


async def test_scorers_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/scorers/unknown-league")
    assert response.status_code == 404


async def test_scorers_unregistered_provider_degrades_to_empty(
    client: AsyncClient,
) -> None:
    # The seeded soccer league's provider ("mock") is not in the registry:
    # the route must degrade to an empty board, not 500.
    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == SOCCER_LEAGUE_ID
    assert payload["rows"] == []


class _RecordingSummaryProvider:
    """Provider that records every summary the route asks it for."""

    provider_id = "mock"

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def get_game_summary(self, league: object, provider_game_key: str) -> GameSummary:
        self.requested.append(provider_game_key)
        return GameSummary(
            game_id=provider_game_key,
            goals=(Goal(player="Wren Halloway", team="Glimmer Foxes"),),
        )


def _seed_finals(
    session: AsyncSession, league_id: str, prefix: str, count: int, now: datetime
) -> None:
    """Add ``count`` FINAL games to ``league_id``, oldest first."""
    for index in range(count):
        session.add(
            GameORM(
                id=f"mock:{prefix}-{index:04d}",
                league_id=league_id,
                home_name="Glimmer Foxes",
                away_name="Bramblewick Falcons",
                start_time=now - timedelta(days=count - index),
                phase="final",
            )
        )


async def test_scorers_caps_the_summary_fan_out_to_the_recent_games(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk costs one upstream summary per FINAL game, so it must be bounded.

    Finished games are never pruned, so an uncapped fan-out grows every
    matchweek (and across seasons) until a burst rate-limits the provider.
    The cap keeps the most recent games and reports what it actually walked.
    """
    total = scorers_route._MAX_GAMES + 5
    now = utcnow()
    async with db() as session:
        _seed_finals(session, SOCCER_LEAGUE_ID, "ac", total, now)
        await session.commit()

    provider = _RecordingSummaryProvider()
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")

    assert response.status_code == 200
    payload = response.json()
    # Bounded — and the games kept are the most recent ones, not the openers.
    assert len(provider.requested) == scorers_route._MAX_GAMES
    assert set(provider.requested) == {f"ac-{index:04d}" for index in range(5, total)}
    # The count the client sees matches what was actually tallied.
    assert payload["games_counted"] == scorers_route._MAX_GAMES
    assert payload["rows"][0]["player"] == "Wren Halloway"
    assert payload["rows"][0]["goals"] == scorers_route._MAX_GAMES


async def test_scorers_skips_the_fan_out_entirely_for_non_soccer_leagues(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only soccer summaries carry goals, so no other sport may fan out.

    A basketball league's finished games would each cost one upstream
    summary call to tally a guaranteed-empty board; the route must answer
    from the league row alone.
    """
    now = utcnow()
    async with db() as session:
        _seed_finals(session, LEAGUE_ID, "vc", 6, now)
        await session.commit()

    provider = _RecordingSummaryProvider()
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{LEAGUE_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert provider.requested == []
    assert payload["games_counted"] == 0
    assert payload["rows"] == []


class _FlakySummaryProvider(_RecordingSummaryProvider):
    """Recording provider whose fetch raises for the game keys in ``down``.

    Every game it does answer has one goal by a scorer named after the game,
    so a tally shows exactly which summaries made it in.
    """

    def __init__(self, down: set[str]) -> None:
        super().__init__()
        self.down = down

    async def get_game_summary(self, league: object, provider_game_key: str) -> GameSummary:
        self.requested.append(provider_game_key)
        if provider_game_key in self.down:
            raise RuntimeError("boom: upstream summary fetch failed")
        return GameSummary(
            game_id=provider_game_key,
            goals=(Goal(player=f"Scorer {provider_game_key}", team="Glimmer Foxes"),),
        )


_AC_KEYS = {"ac-0000", "ac-0001", "ac-0002"}


async def test_scorers_is_503_only_when_every_summary_fetch_failed(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outage is not an empty Golden Boot.

    With every summary failing the tally would be an empty board — a 200
    that replaces the table on screen.  That is a 503, and it is not cached;
    as soon as a single summary comes back the walk answers again.
    """
    async with db() as session:
        _seed_finals(session, SOCCER_LEAGUE_ID, "ac", 3, utcnow())
        await session.commit()
    provider = _FlakySummaryProvider(down=set(_AC_KEYS))
    monkeypatch.setitem(registry._providers, "mock", provider)

    outage = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")
    assert outage.status_code == 503
    assert outage.json() == {"detail": "Upstream provider unavailable"}

    # One summary back: a partial tally is still an answer.
    provider.down = {"ac-0000", "ac-0001"}
    partial = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")
    assert partial.status_code == 200
    payload = partial.json()
    assert [(row["player"], row["goals"]) for row in payload["rows"]] == [("Scorer ac-0002", 1)]
    assert payload["games_counted"] == 3
    # The failed walk left nothing behind: the second request walked again.
    assert sorted(provider.requested) == sorted([*_AC_KEYS, *_AC_KEYS])


async def test_scorers_cached_board_is_served_through_an_outage(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Redis in this suite: the board is held in-process for its TTL, so
    a refresh during an outage gets the stored table, not a 503 or a rewalk."""
    async with db() as session:
        _seed_finals(session, SOCCER_LEAGUE_ID, "ac", 3, utcnow())
        await session.commit()
    provider = _FlakySummaryProvider(down=set())
    monkeypatch.setitem(registry._providers, "mock", provider)

    fresh = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")
    assert fresh.status_code == 200
    assert len(fresh.json()["rows"]) == 3

    provider.down = set(_AC_KEYS)
    during_outage = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")

    assert during_outage.status_code == 200
    assert during_outage.json() == fresh.json()
    assert len(provider.requested) == 3  # the walk did not run again


async def test_scorers_with_no_finals_is_an_empty_board_not_a_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing walked is not "every fetch failed".  A competition with no
    finished match yet — pre-season, or just followed — has an empty board,
    and a 503 there would be retried every five minutes for nothing."""
    provider = _FlakySummaryProvider(down=set())
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")

    assert response.status_code == 200
    assert response.json()["rows"] == []
    assert response.json()["games_counted"] == 0
    assert provider.requested == []


class _NoSummaryProvider(_RecordingSummaryProvider):
    """A provider that answers every game with "no summary" (``None``)."""

    async def get_game_summary(  # type: ignore[override]
        self, league: object, provider_game_key: str
    ) -> None:
        self.requested.append(provider_game_key)
        return None


async def test_scorers_provider_with_no_summaries_is_not_an_outage(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` is the provider's ANSWER — it has no summary for the game —
    and an answer is not a failed fetch: no goals, a 200."""
    async with db() as session:
        _seed_finals(session, SOCCER_LEAGUE_ID, "ac", 3, utcnow())
        await session.commit()
    provider = _NoSummaryProvider()
    monkeypatch.setitem(registry._providers, "mock", provider)

    response = await client.get(f"/api/scorers/{SOCCER_LEAGUE_ID}")

    assert response.status_code == 200
    assert response.json()["rows"] == []
    assert response.json()["games_counted"] == 3
    assert len(provider.requested) == 3


# --- /scorers and /odds behind the provider's circuit breaker ----------------


class _GuardedEspnStub:
    """Stands in for the ESPN provider under its real id.

    Registered as ``"espn"``, so ``registry.get_provider`` wraps it in the
    real circuit-breaker guard.  Each call yields to the loop once, as a
    network round trip does: that is what lets a fan-out's other calls reach
    the breaker while the first is still in flight.
    """

    provider_id = "espn"

    def __init__(self) -> None:
        self.summaries: list[str] = []
        self.odds: list[str] = []
        self.down: set[str] = set()

    async def get_game_summary(self, league: object, provider_game_key: str) -> GameSummary:
        self.summaries.append(provider_game_key)
        await asyncio.sleep(0)
        if provider_game_key in self.down:
            raise RuntimeError("boom: upstream summary fetch failed")
        return GameSummary(
            game_id=provider_game_key,
            goals=(Goal(player=f"Scorer {provider_game_key}", team="Glimmer Foxes"),),
        )

    async def get_game_odds(self, league: object, provider_game_key: str) -> GameOdds:
        self.odds.append(provider_game_key)
        await asyncio.sleep(0)
        return GameOdds(
            provider="Harbor Book", details="GLF -2.5", home_moneyline=-150, away_moneyline=130
        )

    async def close(self) -> None:
        return None


@pytest.fixture
async def guarded_espn(
    db: async_sessionmaker[AsyncSession], seeded: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_GuardedEspnStub]:
    """An ESPN soccer league — 20 finals, 10 fixtures — behind a fresh breaker."""
    now = utcnow()
    async with db() as session:
        session.add(
            LeagueORM(
                id=GUARDED_LEAGUE_ID,
                sport="soccer",
                name="Harbor Cup",
                provider="espn",
                provider_key="soccer/harbor",
            )
        )
        await session.flush()
        for index in range(20):
            session.add(
                GameORM(
                    id=f"espn:f{index:02d}",
                    league_id=GUARDED_LEAGUE_ID,
                    home_name="Glimmer Foxes",
                    away_name="Bramblewick Falcons",
                    start_time=now - timedelta(days=20 - index),
                    phase="final",
                )
            )
        for index in range(10):
            session.add(
                GameORM(
                    id=f"espn:s{index:02d}",
                    league_id=GUARDED_LEAGUE_ID,
                    home_name="Glimmer Foxes",
                    away_name="Bramblewick Falcons",
                    start_time=now + timedelta(hours=index + 1),
                    phase="scheduled",
                )
            )
        await session.commit()

    stub = _GuardedEspnStub()
    monkeypatch.setitem(registry._providers, "espn", stub)
    # A guard keeps the breaker it was built with, so drop both: the next
    # lookup builds a guard around the stub with a breaker of its own.
    registry._guards.pop("espn", None)
    circuit_breaker.reset_all()
    try:
        yield stub
    finally:
        registry._guards.pop("espn", None)
        circuit_breaker.reset_all()


def _open_then_cool_down() -> circuit_breaker.CircuitBreaker:
    """Leave ESPN's breaker as an outage does once its cooldown has run out:
    open, and about to admit exactly one probe."""
    breaker = circuit_breaker.get_breaker("espn")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure("down")
    assert breaker.state is circuit_breaker.CircuitState.OPEN
    assert breaker.opened_at is not None
    breaker.opened_at -= breaker.reset_after + timedelta(seconds=1)
    return breaker


async def test_scorers_first_walk_after_a_breaker_cooldown_is_the_whole_board(
    client: AsyncClient, guarded_espn: _GuardedEspnStub
) -> None:
    """ESPN is back, and this request is the first to find out.

    A half-open breaker admits one call.  With the whole walk fired at once
    that call succeeded and the other nineteen were turned away unasked: a
    200 "board" of one match, labelled with the full match count and cached
    for 30 minutes over the table on screen.  The first fetch now goes
    alone, closes the breaker, and the rest follow.
    """
    breaker = _open_then_cool_down()

    response = await client.get(f"/api/scorers/{GUARDED_LEAGUE_ID}")

    assert response.status_code == 200
    assert breaker.state is circuit_breaker.CircuitState.CLOSED
    assert len(guarded_espn.summaries) == 20
    assert len(response.json()["rows"]) == 20
    assert response.json()["games_counted"] == 20


async def test_scorers_walk_the_breaker_cut_short_is_503_and_not_cached(
    client: AsyncClient, guarded_espn: _GuardedEspnStub
) -> None:
    """A walk the breaker stopped is an outage, however much got in first.

    Two summaries answer, then ESPN starts failing: five failures open the
    breaker and the rest of the walk is turned away without being asked.
    Tallying the two would cache a two-match board for half an hour.
    """
    guarded_espn.down = {f"f{index:02d}" for index in range(2, 20)}

    response = await client.get(f"/api/scorers/{GUARDED_LEAGUE_ID}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Upstream provider unavailable"}
    assert len(guarded_espn.summaries) < 20  # the breaker did cut it short
    assert not [key for key in cache._local if key.startswith("scorers:")]


async def test_odds_first_batch_after_a_breaker_cooldown_prices_every_game(
    client: AsyncClient, guarded_espn: _GuardedEspnStub
) -> None:
    """The same half-open probe, on the odds fan-out: one game priced, nine
    turned away, and every chip but one gone until the next poll."""
    breaker = _open_then_cool_down()
    ids = ",".join(f"espn:s{index:02d}" for index in range(10))

    response = await client.get("/api/odds", params={"ids": ids})

    assert response.status_code == 200
    assert breaker.state is circuit_breaker.CircuitState.CLOSED
    assert len(response.json()) == 10
    assert len(guarded_espn.odds) == 10


# --- /nation -----------------------------------------------------------------


async def test_nation_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/nation/unknown-league/Glimmer%20Foxes")
    assert response.status_code == 404


async def test_nation_reads_standing_from_stored_rows(client: AsyncClient) -> None:
    response = await client.get(f"/api/nation/{LEAGUE_ID}/glimmer%20foxes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Glimmer Foxes"  # display name from the row
    assert payload["standing"]["rank"] == 1
    assert payload["standing"]["wins"] == 10


# --- /bracket ------------------------------------------------------------------


async def test_bracket_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/bracket/unknown-league")
    assert response.status_code == 404


async def test_bracket_non_espn_provider_is_empty(client: AsyncClient) -> None:
    response = await client.get(f"/api/bracket/{LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == LEAGUE_ID
    assert payload["rounds"] == []


async def _seed_espn_league(db: async_sessionmaker[AsyncSession]) -> None:
    async with db() as session:
        session.add(
            LeagueORM(
                id=ESPN_LEAGUE_ID,
                sport="basketball",
                name="Harbor Hoops",
                provider="espn",
                provider_key="basketball/harbor",
            )
        )
        await session.commit()


def _playoff_scoreboard() -> dict[str, Any]:
    """A playoff scoreboard holding one game of one series."""

    def side(abbr: str) -> dict[str, Any]:
        return {"team": {"displayName": f"{abbr} Club", "abbreviation": abbr}}

    return {
        "events": [
            {
                "date": "2026-06-05T23:00Z",
                "competitions": [
                    {
                        "date": "2026-06-05T23:00Z",
                        "competitors": [side("GLF"), side("BWF")],
                        "series": {"id": "s1", "summary": "GLF leads series 1-0"},
                        "notes": [{"headline": "Harbor Finals - Game 1"}],
                    }
                ],
            }
        ]
    }


def _stub_playoff_scoreboard(monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> list[str]:
    """Answer ESPN's playoff scoreboard from ``answers``, one per call.

    Stubs the provider's network call only, so the real fetch (and its
    failure handling) runs.  An exception in ``answers`` is raised.  Returns
    the list each call is recorded in.
    """
    calls: list[str] = []

    async def fake(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> object:
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(espn_playoffs, "_get_scoreboard", fake)
    return calls


async def test_bracket_fetch_failure_is_503_not_an_empty_bracket(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scoreboard that cannot be fetched must not read as "no active
    playoffs".  One that ANSWERS with no series is still the 200 off-season
    state — and both are asked for again, since neither is cached."""
    await _seed_espn_league(db)
    calls = _stub_playoff_scoreboard(
        monkeypatch, [httpx.ConnectError("scoreboard down"), {"events": []}, {"events": []}]
    )

    outage = await client.get(f"/api/bracket/{ESPN_LEAGUE_ID}")
    assert outage.status_code == 503
    assert outage.json() == {"detail": "Upstream provider unavailable"}

    off_season = await client.get(f"/api/bracket/{ESPN_LEAGUE_ID}")
    assert off_season.status_code == 200
    assert off_season.json()["rounds"] == []

    assert (await client.get(f"/api/bracket/{ESPN_LEAGUE_ID}")).status_code == 200
    assert len(calls) == 3


async def test_bracket_cached_rounds_are_served_through_an_outage(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_espn_league(db)
    calls = _stub_playoff_scoreboard(
        monkeypatch, [_playoff_scoreboard(), httpx.ConnectError("scoreboard down")]
    )

    fresh = await client.get(f"/api/bracket/{ESPN_LEAGUE_ID}")
    assert fresh.status_code == 200
    assert [rnd["name"] for rnd in fresh.json()["rounds"]] == ["Harbor Finals"]

    during_outage = await client.get(f"/api/bracket/{ESPN_LEAGUE_ID}")

    assert during_outage.status_code == 200
    assert during_outage.json() == fresh.json()
    assert len(calls) == 1  # held in-process: ESPN was not asked again


# --- /leaders -----------------------------------------------------------------


async def test_leaders_unknown_league_404(client: AsyncClient) -> None:
    response = await client.get("/api/leaders/unknown-league")
    assert response.status_code == 404


async def test_leaders_empty_rosters_yield_empty_board(client: AsyncClient) -> None:
    response = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["league_id"] == LEAGUE_ID
    assert payload["rows"] == []


def _stub_leaders_feed(monkeypatch: pytest.MonkeyPatch, answers: list[httpx.Response]) -> list[str]:
    """Answer ESPN's athlete-stats feed from ``answers``, one per request.

    Swaps the transport under the provider's own client, so the real fetch
    (status check, decode, failure handling) runs.  Returns the request log.
    """
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return answers.pop(0)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        espn_leaders.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handler)),
    )
    return requests


def _leaders_feed() -> dict[str, Any]:
    return {
        "categories": [{"name": "offensive", "labels": ["PTS"]}],
        "athletes": [
            {
                "athlete": {"id": "101", "displayName": "Rowan Vale", "teamShortName": "HBR"},
                "categories": [{"name": "offensive", "totals": [28.4]}],
            }
        ],
    }


async def test_leaders_feed_failure_is_503_only_with_no_stored_stats_to_show(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whole-followed league has no followed rosters, so a failed feed used
    to answer an empty board — "no player stats yet" over the leaders on
    screen.  That is a 503.  A feed that ANSWERS with nothing, and a failed
    feed with stored roster stats to fall back on, both still answer 200."""
    await _seed_espn_league(db)
    requests = _stub_leaders_feed(
        monkeypatch,
        [
            httpx.Response(503, text="feed down"),
            httpx.Response(200, json={"categories": [], "athletes": []}),
            httpx.Response(503, text="feed down"),
        ],
    )

    outage = await client.get(f"/api/leaders/{ESPN_LEAGUE_ID}")
    assert outage.status_code == 503
    assert outage.json() == {"detail": "Upstream provider unavailable"}

    nothing_listed = await client.get(f"/api/leaders/{ESPN_LEAGUE_ID}")
    assert nothing_listed.status_code == 200
    assert nothing_listed.json()["rows"] == []

    async with db() as session:
        session.add(
            TeamORM(
                id="harbor-gulls",
                league_id=ESPN_LEAGUE_ID,
                name="Harbor Gulls",
                abbreviation="HBG",
                provider_key="7",
                rss_feeds=[],
            )
        )
        await session.flush()
        session.add(
            PlayerORM(
                id="espn:202",
                team_id="harbor-gulls",
                name="Sable Marsh",
                stat_line="19.2 PPG · 3.3 AST",
            )
        )
        await session.commit()

    roster_board = await client.get(f"/api/leaders/{ESPN_LEAGUE_ID}")
    assert roster_board.status_code == 200
    assert [row["name"] for row in roster_board.json()["rows"]] == ["Sable Marsh"]
    assert len(requests) == 3  # no failure was cached: each request asked ESPN


async def test_leaders_cached_board_is_served_through_an_outage(
    db: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_espn_league(db)
    requests = _stub_leaders_feed(
        monkeypatch,
        [httpx.Response(200, json=_leaders_feed()), httpx.Response(503, text="feed down")],
    )

    fresh = await client.get(f"/api/leaders/{ESPN_LEAGUE_ID}")
    assert fresh.status_code == 200
    assert [row["name"] for row in fresh.json()["rows"]] == ["Rowan Vale"]

    during_outage = await client.get(f"/api/leaders/{ESPN_LEAGUE_ID}")

    assert during_outage.status_code == 200
    assert during_outage.json() == fresh.json()
    assert len(requests) == 1  # held in-process: ESPN was not asked again
