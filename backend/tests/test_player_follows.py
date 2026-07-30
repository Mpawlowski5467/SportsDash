"""Player follows: repository CRUD, routes, leaders stamping, status alerts.

All fixture data is fictional.  Six layers:

1. Repository CRUD + the ``replace_followed`` wipe.
2. Route validation for the ``/players/follows`` writing routes (v1
   constraint: the athlete must sit on a followed team's stored roster;
   cross-league bare-id collisions are rejected with 409).
3. Leaders ``followed`` stamping is post-cache: a warm 900s cache plus a
   brand-new follow flips the flag without a refetch — and the stamp is
   scoped to the requested league (ESPN athlete ids collide across
   sports).
4. ``refresh_rosters`` status diff against the per-follow
   ``last_notified_status`` baseline: a change fires once and advances
   the baseline only on confirmed delivery (failed sends and muted teams
   retry next sync), first sightings seed silently, and a re-injury
   after a recovery alerts again.
5. ``live_tick`` starting-soon window: an already-ruled-out followed
   player on a side of an imminent game gets one player-out alert.
6. ``events_tick`` picks up a SCHEDULED event inside the lookahead, so a
   single-day race's flip to FINAL is observed live (a followed driver
   gets the finish notification), not on the next daily refresh.
7. ``refresh_pregame_rosters``: a same-day scratch on a team playing
   within the lookahead alerts within a tick — one roster call per team
   per cooldown window, no per-athlete overview calls, and the stat lines
   the daily sync paid for survive the cheap re-sync.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app import background
from app.config import get_settings
from app.db import get_session
from app.models import domain
from app.models.domain import GamePhase, PlayerStatus
from app.models.orm import GameORM, NotificationSentORM, PlayerFollowORM, PlayerORM, TeamORM
from app.providers import espn_leaders, registry
from app.routes import router as api_router
from app.scheduler import common as scheduler_common
from app.scheduler import jobs
from app.scheduler import live as scheduler_live
from app.scheduler import refresh as scheduler_refresh
from app.scheduler import stadium_cache as scheduler_stadium_cache
from app.services import cache, notify, repository
from app.timeutil import utcnow
from tests.db_engine import create_test_schema, make_test_engine

PROVIDER_ID = "fakeplayers"

LEAGUE_ID = "cascadia-basketball"
TEAM_ID = "cascadia-basketball-gullwharf-herons"
OTHER_TEAM_ID = "cascadia-basketball-bramblebay-otters"

ATHLETE_ID = "9001"  # bare ESPN athlete id; roster rows carry "espn:9001"
OTHER_ATHLETE_ID = "9002"

LEAGUE = domain.League(
    id=LEAGUE_ID,
    sport=domain.Sport.BASKETBALL,
    name="Cascadia Basketball League",
    provider=PROVIDER_ID,
    provider_key="basketball/cascadia",
)
TEAM = domain.Team(
    id=TEAM_ID,
    league_id=LEAGUE_ID,
    name="Gullwharf Herons",
    abbreviation="GWH",
    provider_key="gullwharf-herons",
)
OTHER_TEAM = domain.Team(
    id=OTHER_TEAM_ID,
    league_id=LEAGUE_ID,
    name="Bramblebay Otters",
    abbreviation="BBO",
    provider_key="bramblebay-otters",
)


def _player(
    athlete_id: str,
    name: str,
    *,
    status: PlayerStatus = PlayerStatus.ACTIVE,
    status_detail: str | None = None,
    stat_line: str | None = None,
    career_stat_line: str | None = None,
    photo_url: str | None = None,
    team_id: str = TEAM_ID,
) -> domain.Player:
    return domain.Player(
        id=f"espn:{athlete_id}",
        team_id=team_id,
        name=name,
        position="G",
        status=status,
        status_detail=status_detail,
        stat_line=stat_line,
        career_stat_line=career_stat_line,
        photo_url=photo_url,
    )


def _roster(*players: domain.Player, team_id: str = TEAM_ID) -> domain.Roster:
    return domain.Roster(team_id=team_id, players=tuple(players), fetched_at=utcnow())


class FakeProvider:
    """Canned rosters, event states + empty live scoreboards for the scheduler tests."""

    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self.rosters: dict[str, domain.Roster] = {}
        self.event_states: dict[str, domain.Event] = {}
        self.event_state_calls: list[str] = []
        # (team id, with_stat_lines) per get_roster call, plus the
        # per-athlete overview calls the real ESPN provider makes inside the
        # stat-line pass — the cost the pre-game re-sync must not pay.
        self.roster_calls: list[tuple[str, bool]] = []
        self.overview_calls: list[str] = []
        self.roster_errors: set[str] = set()

    async def get_roster(self, league, team, *, with_stat_lines: bool = True):
        self.roster_calls.append((team.id, with_stat_lines))
        if team.id in self.roster_errors:
            raise RuntimeError(f"boom: roster {team.id}")
        roster = self.rosters.get(team.id)
        if roster is None:
            return _roster(team_id=team.id)
        if with_stat_lines:
            # Mirror the real provider: one athlete-overview request each.
            self.overview_calls.extend(player.id for player in roster.players)
        return roster

    async def get_event_state(self, league, provider_event_key):
        self.event_state_calls.append(provider_event_key)
        return self.event_states.get(provider_event_key)

    async def get_live_games(self, league):
        return []

    async def get_game_state(self, league, provider_game_key):
        return None

    async def get_schedule(self, league, team, start, end):
        return []

    async def get_competition_schedule(self, league, start, end):
        return []

    async def get_standings(self, league):
        return domain.Standings(league_id=league.id, season="2026", rows=(), fetched_at=utcnow())

    async def get_news(self, league, team):
        return []

    async def close(self) -> None:
        return None


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def app(db: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def patched_scope(db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every job module's ``session_scope`` at the throwaway database."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session
            await session.commit()

    for module in (scheduler_common, scheduler_refresh, scheduler_stadium_cache, scheduler_live):
        monkeypatch.setattr(module, "session_scope", scope)


@pytest.fixture
def fake_provider() -> AsyncIterator[FakeProvider]:
    provider = FakeProvider()
    saved = registry._providers.get(PROVIDER_ID)
    registry.register_provider(provider)
    try:
        yield provider
    finally:
        if saved is not None:
            registry._providers[PROVIDER_ID] = saved
        else:
            registry._providers.pop(PROVIDER_ID, None)


@pytest.fixture
def sent_events(monkeypatch: pytest.MonkeyPatch) -> list:
    """Spy replacing ``notify.send_event``; collects the events, delivers OK."""
    events: list = []

    async def spy(event) -> bool:
        events.append(event)
        return True

    monkeypatch.setattr(notify, "send_event", spy)
    return events


async def _seed_followed_set(
    db: async_sessionmaker[AsyncSession], *, teams: tuple[domain.Team, ...] = (TEAM,)
) -> None:
    async with db() as session:
        await repository.upsert_league(session, LEAGUE)
        for team in teams:
            await repository.upsert_team(session, team)
        await session.commit()


async def _seed_roster(db: async_sessionmaker[AsyncSession], roster: domain.Roster) -> None:
    async with db() as session:
        await repository.replace_roster(session, roster)
        await session.commit()


async def _follow(
    db: async_sessionmaker[AsyncSession],
    athlete_id: str = ATHLETE_ID,
    name: str = "Rooke Marshwood",
    team_id: str = TEAM_ID,
    league_id: str = LEAGUE_ID,
    last_notified_status: str | None = None,
) -> None:
    async with db() as session:
        await repository.follow_player(
            session,
            athlete_id=athlete_id,
            name=name,
            team_id=team_id,
            league_id=league_id,
            last_notified_status=last_notified_status,
        )
        await session.commit()


async def _baseline(
    db: async_sessionmaker[AsyncSession], athlete_id: str = ATHLETE_ID
) -> str | None:
    """The follow's stored ``last_notified_status`` (the alert baseline)."""
    async with db() as session:
        row = await session.get(PlayerFollowORM, athlete_id)
        assert row is not None
        return row.last_notified_status


# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------


async def test_follow_list_unfollow_round_trip(db: async_sessionmaker[AsyncSession]) -> None:
    async with db() as session:
        row = await repository.follow_player(
            session,
            athlete_id=ATHLETE_ID,
            name="Rooke Marshwood",
            team_id=TEAM_ID,
            league_id=LEAGUE_ID,
            position="PG",
            photo_url="https://img.example/rooke.png",
        )
        await session.commit()
        assert row.followed_at is not None

    async with db() as session:
        rows = await repository.list_player_follows(session)
        assert [(r.athlete_id, r.name, r.team_id, r.league_id) for r in rows] == [
            (ATHLETE_ID, "Rooke Marshwood", TEAM_ID, LEAGUE_ID)
        ]
        assert rows[0].position == "PG"

        assert await repository.unfollow_player(session, ATHLETE_ID) is True
        await session.commit()

    async with db() as session:
        assert await repository.list_player_follows(session) == []
        assert await repository.unfollow_player(session, ATHLETE_ID) is False


async def test_refollow_updates_fields_and_keeps_one_row(
    db: async_sessionmaker[AsyncSession],
) -> None:
    async with db() as session:
        await repository.follow_player(
            session,
            athlete_id=ATHLETE_ID,
            name="Rooke Marshwood",
            team_id=TEAM_ID,
            league_id=LEAGUE_ID,
        )
        await session.commit()

    async with db() as session:
        # A re-follow (e.g. after a trade to another followed team) is an
        # update in place, not a second row.
        await repository.follow_player(
            session,
            athlete_id=ATHLETE_ID,
            name="Rooke Marshwood",
            team_id=OTHER_TEAM_ID,
            league_id=LEAGUE_ID,
            position="SG",
        )
        await session.commit()

    async with db() as session:
        rows = await repository.list_player_follows(session)
        assert len(rows) == 1
        assert rows[0].team_id == OTHER_TEAM_ID
        assert rows[0].position == "SG"


async def test_replace_followed_wipes_player_follows(
    db: async_sessionmaker[AsyncSession],
) -> None:
    """A wizard re-run clears follows — they're re-picked afterwards."""
    await _seed_followed_set(db)
    await _follow(db)

    async with db() as session:
        await repository.replace_followed(session, [LEAGUE], [TEAM])
        await session.commit()

    async with db() as session:
        assert await repository.list_player_follows(session) == []


# ---------------------------------------------------------------------------
# Routes — validation + round trip
# ---------------------------------------------------------------------------

_PUT_BODY = {
    "name": "Rooke Marshwood",
    "team_id": TEAM_ID,
    "league_id": LEAGUE_ID,
    "position": "PG",
    "photo_url": None,
}


async def test_put_unknown_team_404(client: AsyncClient, db: async_sessionmaker) -> None:
    await _seed_followed_set(db)
    resp = await client.put(
        f"/api/players/follows/{ATHLETE_ID}",
        json={**_PUT_BODY, "team_id": "unfollowed-team"},
    )
    assert resp.status_code == 404
    assert "not a followed team" in resp.json()["detail"]


async def test_put_athlete_not_on_roster_404(client: AsyncClient, db: async_sessionmaker) -> None:
    await _seed_followed_set(db)
    await _seed_roster(db, _roster(_player(OTHER_ATHLETE_ID, "Sable Quillan")))
    resp = await client.put(f"/api/players/follows/{ATHLETE_ID}", json=_PUT_BODY)
    assert resp.status_code == 404
    assert "stored roster" in resp.json()["detail"]


async def test_put_same_bare_id_in_other_league_409(
    client: AsyncClient, db: async_sessionmaker
) -> None:
    """ESPN athlete ids collide across sports; the single-column PK must not
    let a follow in one league silently overwrite the same bare id's follow
    in another."""
    await _seed_followed_set(db)
    await _seed_roster(db, _roster(_player(ATHLETE_ID, "Rooke Marshwood")))
    # The same bare id already followed under a DIFFERENT league (a hockey
    # player who happens to share the number).
    await _follow(
        db,
        ATHLETE_ID,
        "Torvald Iceberre",
        team_id="cascadia-hockey-glacierport-terns",
        league_id="cascadia-hockey",
    )

    resp = await client.put(f"/api/players/follows/{ATHLETE_ID}", json=_PUT_BODY)
    assert resp.status_code == 409
    assert "cascadia-hockey" in resp.json()["detail"]

    # The original follow is untouched.
    async with db() as session:
        rows = await repository.list_player_follows(session)
        assert [(r.athlete_id, r.name, r.league_id) for r in rows] == [
            (ATHLETE_ID, "Torvald Iceberre", "cascadia-hockey")
        ]


async def test_put_get_delete_round_trip(client: AsyncClient, db: async_sessionmaker) -> None:
    await _seed_followed_set(db)
    await _seed_roster(db, _roster(_player(ATHLETE_ID, "Rooke Marshwood")))

    resp = await client.put(f"/api/players/follows/{ATHLETE_ID}", json=_PUT_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["athlete_id"] == ATHLETE_ID
    assert data["name"] == "Rooke Marshwood"
    assert data["team_id"] == TEAM_ID
    assert data["league_id"] == LEAGUE_ID
    assert data["position"] == "PG"
    assert data["photo_url"] is None
    assert data["followed_at"] is not None

    resp = await client.get("/api/players/follows")
    assert resp.status_code == 200
    assert [row["athlete_id"] for row in resp.json()] == [ATHLETE_ID]

    resp = await client.delete(f"/api/players/follows/{ATHLETE_ID}")
    assert resp.status_code == 204

    resp = await client.get("/api/players/follows")
    assert resp.json() == []


async def test_delete_unfollowed_athlete_404(client: AsyncClient, db: async_sessionmaker) -> None:
    resp = await client.delete(f"/api/players/follows/{ATHLETE_ID}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Leaders — the followed flag is stamped after cache retrieval
# ---------------------------------------------------------------------------


def _espn_league() -> domain.League:
    """The leaders ESPN path needs provider == "espn" + a supported sport."""
    return domain.League(
        id=LEAGUE_ID,
        sport=domain.Sport.BASKETBALL,
        name="Cascadia Basketball League",
        provider="espn",
        provider_key="basketball/cascadia",
    )


async def test_leaders_followed_stamp_flips_on_warm_cache(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new follow flips ``followed`` on a cached board without a refetch."""
    async with db() as session:
        await repository.upsert_league(session, _espn_league())
        await session.commit()

    cached_board = {
        "league_id": LEAGUE_ID,
        "league_name": "Cascadia Basketball League",
        "sport": "basketball",
        "stat_label": "PPG",
        "rows": [
            {
                "rank": 1,
                "player_id": ATHLETE_ID,
                "name": "Rooke Marshwood",
                "position": "PG",
                "team_id": "",
                "team_name": "Gullwharf Herons",
                "team_logo_url": None,
                "team_color": None,
                "value": 27.5,
                "stat_label": "PPG",
                "detail": "",
                "highlighted": False,
                "followed": False,
            }
        ],
    }

    async def warm_cache_get(key: str):
        return cached_board

    async def fail_fetch(provider_key, sport):
        raise AssertionError("cache hit must not refetch the ESPN board")

    monkeypatch.setattr(cache, "cache_get_json", warm_cache_get)
    monkeypatch.setattr(espn_leaders, "fetch_league_leaders", fail_fetch)

    # Before any follow: the cached row comes through unstamped.
    resp = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["followed"] is False

    # Follow the athlete; the SAME warm cache now serves followed=True.
    await _follow(db)
    resp = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["followed"] is True
    assert row["highlighted"] is False  # team-level flag untouched


async def test_leaders_fresh_board_is_cached_unstamped(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 900s cache stores followed=False; the response is stamped."""
    async with db() as session:
        await repository.upsert_league(session, _espn_league())
        await session.commit()
    await _follow(db)

    async def cold_cache_get(key: str):
        return None

    stored: dict = {}

    async def capture_cache_set(key: str, value, ttl_seconds: int) -> None:
        stored[key] = value

    async def fetch(provider_key, sport):
        return [
            espn_leaders.LeaderEntry(
                athlete_id=ATHLETE_ID,
                player="Rooke Marshwood",
                team="GWH",
                position="PG",
                value=27.5,
                stat_label="PPG",
            )
        ]

    monkeypatch.setattr(cache, "cache_get_json", cold_cache_get)
    monkeypatch.setattr(cache, "cache_set_json", capture_cache_set)
    monkeypatch.setattr(espn_leaders, "fetch_league_leaders", fetch)

    resp = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["followed"] is True
    # The cached copy must NOT carry the per-request flag.
    assert stored[f"leaders:{LEAGUE_ID}"]["rows"][0]["followed"] is False


async def test_leaders_roster_board_stamps_followed(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    """Roster-derived boards (prefixed player ids) stamp on the bare id."""
    await _seed_followed_set(db)  # provider "fakeplayers" -> roster-derived path
    await _seed_roster(
        db,
        _roster(
            _player(ATHLETE_ID, "Rooke Marshwood", stat_line="27.5 PPG · 6.1 AST"),
            _player(OTHER_ATHLETE_ID, "Sable Quillan", stat_line="19.2 PPG · 3.3 AST"),
        ),
    )
    await _follow(db)

    resp = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert resp.status_code == 200
    by_name = {row["name"]: row for row in resp.json()["rows"]}
    assert by_name["Rooke Marshwood"]["followed"] is True
    assert by_name["Sable Quillan"]["followed"] is False


async def test_leaders_stamp_is_scoped_to_the_requested_league(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    """ESPN athlete ids collide across sports: a follow of the SAME bare id
    in another league must not stamp this league's board."""
    await _seed_followed_set(db)
    await _seed_roster(
        db, _roster(_player(ATHLETE_ID, "Rooke Marshwood", stat_line="27.5 PPG · 6.1 AST"))
    )
    # Same bare id, followed in a DIFFERENT league (a hockey player).
    await _follow(
        db,
        ATHLETE_ID,
        "Torvald Iceberre",
        team_id="cascadia-hockey-glacierport-terns",
        league_id="cascadia-hockey",
    )

    resp = await client.get(f"/api/leaders/{LEAGUE_ID}")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert [row["name"] for row in rows] == ["Rooke Marshwood"]
    assert rows[0]["followed"] is False


# ---------------------------------------------------------------------------
# refresh_rosters — status-change alerts for followed players
# ---------------------------------------------------------------------------


async def test_follow_route_seeds_baseline_no_alert_at_follow(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """Following an already-injured player seeds the baseline from the roster
    row — no alert at follow time, and none on the next unchanged sync."""
    await _seed_followed_set(db)
    injured = _player(
        ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT, status_detail="Out - ankle"
    )
    await _seed_roster(db, _roster(injured))

    resp = await client.put(f"/api/players/follows/{ATHLETE_ID}", json=_PUT_BODY)
    assert resp.status_code == 200
    assert await _baseline(db) == "out"

    # The next sync sees the same status: baseline diff is empty, no alert.
    fake_provider.rosters[TEAM_ID] = _roster(injured)
    await scheduler_refresh.refresh_rosters()
    assert sent_events == []


async def test_roster_diff_fires_once_and_seeds_first_sighting_silently(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    await _seed_followed_set(db)
    await _follow(db)  # no baseline (followed outside the route)

    # Sync 1: first sighting — no baseline, so NO alert; seeds silently.
    fake_provider.rosters[TEAM_ID] = _roster(_player(ATHLETE_ID, "Rooke Marshwood"))
    await scheduler_refresh.refresh_rosters()
    assert sent_events == []
    assert await _baseline(db) == "active"

    # Sync 2: active -> out fires exactly one alert and advances the baseline.
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(
            ATHLETE_ID,
            "Rooke Marshwood",
            status=PlayerStatus.OUT,
            status_detail="Out - ankle",
        )
    )
    await scheduler_refresh.refresh_rosters()
    assert len(sent_events) == 1
    event = sent_events[0]
    assert event.type is domain.EventType.PLAYER_STATUS
    assert event.dedupe_key == f"player:{ATHLETE_ID}:status:out"
    assert event.title == "Gullwharf Herons"
    assert event.message == "Rooke Marshwood is out — Out - ankle"
    assert await _baseline(db) == "out"

    # Sync 3: unchanged status — nothing new.
    await scheduler_refresh.refresh_rosters()
    assert len(sent_events) == 1

    # The baseline is the dedupe now: roster-diff alerts write NO ledger rows.
    async with db() as session:
        assert (await session.execute(select(NotificationSentORM))).scalars().all() == []


async def test_roster_diff_re_injury_after_recovery_fires_again(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """The docstring promise: a re-injury after a recovery is a fresh alert,
    never suppressed by some permanent record of the first injury."""
    await _seed_followed_set(db)
    await _follow(db, last_notified_status="active")

    out_player = _player(
        ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT, status_detail="Out - knee"
    )
    active_player = _player(ATHLETE_ID, "Rooke Marshwood")

    fake_provider.rosters[TEAM_ID] = _roster(out_player)
    await scheduler_refresh.refresh_rosters()  # active -> out: fires
    fake_provider.rosters[TEAM_ID] = _roster(active_player)
    await scheduler_refresh.refresh_rosters()  # out -> active: fires
    fake_provider.rosters[TEAM_ID] = _roster(out_player)
    await scheduler_refresh.refresh_rosters()  # out AGAIN: a real re-injury — fires

    keys = [event.dedupe_key for event in sent_events]
    assert keys == [
        f"player:{ATHLETE_ID}:status:out",
        f"player:{ATHLETE_ID}:status:active",
        f"player:{ATHLETE_ID}:status:out",
    ]
    assert await _baseline(db) == "out"


async def test_roster_diff_failed_send_leaves_baseline_and_retries(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ntfy send must not advance the baseline: the same diff
    re-fires on the next sync instead of being lost forever."""
    await _seed_followed_set(db)
    await _follow(db, last_notified_status="active")

    attempts: list = []
    deliverable = False

    async def flaky_send(event) -> bool:
        attempts.append(event)
        return deliverable

    monkeypatch.setattr(notify, "send_event", flaky_send)

    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT, status_detail="Out - hip")
    )
    await scheduler_refresh.refresh_rosters()  # send fails
    assert len(attempts) == 1
    assert await _baseline(db) == "active"  # NOT advanced

    deliverable = True
    await scheduler_refresh.refresh_rosters()  # same diff retries and delivers
    assert [event.dedupe_key for event in attempts] == [
        f"player:{ATHLETE_ID}:status:out",
        f"player:{ATHLETE_ID}:status:out",
    ]
    assert await _baseline(db) == "out"

    await scheduler_refresh.refresh_rosters()  # delivered: no third send
    assert len(attempts) == 2


async def test_roster_diff_ignores_unfollowed_players(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    await _seed_followed_set(db)
    await _follow(db)  # follows ATHLETE_ID only

    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood"),
        _player(OTHER_ATHLETE_ID, "Sable Quillan"),
    )
    await scheduler_refresh.refresh_rosters()

    # The unfollowed teammate's status change alerts nobody.
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood"),
        _player(
            OTHER_ATHLETE_ID,
            "Sable Quillan",
            status=PlayerStatus.DAY_TO_DAY,
            status_detail="Day-To-Day - wrist",
        ),
    )
    await scheduler_refresh.refresh_rosters()
    assert sent_events == []


async def test_roster_diff_respects_muted_team_scope(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """v1 gating rides the parent team's scope; a mute suppresses WITHOUT
    advancing the baseline, so un-muting later still alerts on the next
    sync if the status still differs."""
    await _seed_followed_set(db)
    await _follow(db, last_notified_status="active")
    async with db() as session:
        await repository.upsert_notification_pref(session, f"team:{TEAM_ID}", muted=True)
        await session.commit()

    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT, status_detail="Out - hip")
    )
    await scheduler_refresh.refresh_rosters()

    assert sent_events == []
    assert await _baseline(db) == "active"  # NOT advanced while muted

    # Un-mute: the still-differing status alerts on the very next sync.
    async with db() as session:
        await repository.upsert_notification_pref(session, f"team:{TEAM_ID}", muted=False)
        await session.commit()
    await scheduler_refresh.refresh_rosters()

    assert [event.dedupe_key for event in sent_events] == [f"player:{ATHLETE_ID}:status:out"]
    assert await _baseline(db) == "out"


# ---------------------------------------------------------------------------
# live_tick — "ruled out" alert in the starting-soon window
# ---------------------------------------------------------------------------

GAME_ID = f"{PROVIDER_ID}:soon-game"


def _scheduled_row(
    start_delta: timedelta,
    *,
    game_id: str = GAME_ID,
    team_id: str = TEAM_ID,
    league_id: str = LEAGUE_ID,
) -> GameORM:
    return GameORM(
        id=game_id,
        league_id=league_id,
        home_team_id=team_id,
        away_team_id=None,
        home_name="Gullwharf Herons",
        away_name="Bramblebay Otters",
        home_abbreviation="GWH",
        away_abbreviation="BBO",
        start_time=utcnow() + start_delta,
        phase=GamePhase.SCHEDULED.value,
    )


async def _seed_soon_game(
    db: async_sessionmaker[AsyncSession],
    *,
    start_delta: timedelta = timedelta(minutes=10),
    game_id: str = GAME_ID,
    team_id: str = TEAM_ID,
    league_id: str = LEAGUE_ID,
) -> None:
    async with db() as session:
        session.add(
            _scheduled_row(start_delta, game_id=game_id, team_id=team_id, league_id=league_id)
        )
        await session.commit()


async def test_starting_soon_alerts_ruled_out_followed_player(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    await _seed_followed_set(db)
    await _seed_roster(
        db,
        _roster(
            _player(
                ATHLETE_ID,
                "Rooke Marshwood",
                status=PlayerStatus.OUT,
                status_detail="Out - ankle",
            ),
            _player(OTHER_ATHLETE_ID, "Sable Quillan"),  # active: no alert
        ),
    )
    await _follow(db, ATHLETE_ID, "Rooke Marshwood")
    await _follow(db, OTHER_ATHLETE_ID, "Sable Quillan")
    await _seed_soon_game(db)

    await jobs.live_tick()

    by_key = {event.dedupe_key: event for event in sent_events}
    assert f"{GAME_ID}:soon" in by_key  # the ordinary starting-soon alert
    out_event = by_key[f"{GAME_ID}:player-out:{ATHLETE_ID}"]
    assert out_event.type is domain.EventType.PLAYER_STATUS
    assert out_event.message == (
        "Rooke Marshwood (Out - ankle) — Bramblebay Otters @ Gullwharf Herons starts soon"
    )
    # The active followed teammate is not alerted.
    assert f"{GAME_ID}:player-out:{OTHER_ATHLETE_ID}" not in by_key

    # A second tick inside the window re-sends nothing (ledger dedupe).
    sent_events.clear()
    await jobs.live_tick()
    assert [event.dedupe_key for event in sent_events] == []


async def test_starting_soon_no_alert_for_active_or_unfollowed(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    await _seed_followed_set(db)
    await _seed_roster(
        db,
        _roster(
            _player(ATHLETE_ID, "Rooke Marshwood"),  # followed but active
            _player(
                OTHER_ATHLETE_ID,
                "Sable Quillan",
                status=PlayerStatus.OUT,
                status_detail="Out - knee",
            ),  # ruled out but NOT followed
        ),
    )
    await _follow(db, ATHLETE_ID, "Rooke Marshwood")
    await _seed_soon_game(db)

    await jobs.live_tick()

    keys = [event.dedupe_key for event in sent_events]
    assert f"{GAME_ID}:soon" in keys
    assert not any(":player-out:" in key for key in keys)


# ---------------------------------------------------------------------------
# events_tick — a SCHEDULED event inside the lookahead is observed live
# ---------------------------------------------------------------------------

RACING_LEAGUE_ID = "meridian-racing-series"
DRIVER_TEAM_ID = "meridian-racing-vale-arden"
DRIVER_ESPN_ID = "7104"
EVENT_ID = f"{PROVIDER_ID}:harborline-250"

RACING_LEAGUE = domain.League(
    id=RACING_LEAGUE_ID,
    sport=domain.Sport.RACING,
    name="Meridian Racing Series",
    provider=PROVIDER_ID,
    provider_key="racing/meridian",
)
DRIVER_TEAM = domain.Team(
    id=DRIVER_TEAM_ID,
    league_id=RACING_LEAGUE_ID,
    name="Vale Arden",
    abbreviation="ARD",
    provider_key=DRIVER_ESPN_ID,  # a followed driver is a single-member team
)


def _race(phase: GamePhase, *, leaderboard: tuple[domain.LeaderRow, ...] = ()) -> domain.Event:
    return domain.Event(
        id=EVENT_ID,
        league_id=RACING_LEAGUE_ID,
        name="Harborline 250",
        start_time=utcnow() + timedelta(minutes=10),
        end_time=utcnow() + timedelta(hours=4),
        phase=phase,
        leaderboard=leaderboard,
    )


async def _drain_kicked_tasks() -> None:
    """Await the fire-and-forget standings kick a FINAL transition spawns."""
    for task in list(background._tasks):
        await asyncio.gather(task, return_exceptions=True)


async def test_events_tick_observes_scheduled_event_inside_lookahead(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """A single-day race stored SCHEDULED and starting within the lookahead
    is polled by ``events_tick`` — its flip is observed live (here straight
    to FINAL, which notifies the followed driver), instead of waiting for
    the once-daily refresh and delivering the result next morning."""
    async with db() as session:
        await repository.upsert_league(session, RACING_LEAGUE)
        await repository.upsert_team(session, DRIVER_TEAM)
        await repository.upsert_events(session, [_race(GamePhase.SCHEDULED)])
        await session.commit()

    # The provider now reports the race finished, followed driver on top.
    fake_provider.event_states["harborline-250"] = _race(
        GamePhase.FINAL,
        leaderboard=(
            domain.LeaderRow(
                position=1,
                position_label="1",
                name="Vale Arden",
                score="1:58:22",
                player_id=DRIVER_ESPN_ID,
            ),
            domain.LeaderRow(
                position=2,
                position_label="2",
                name="Corin Blakely",
                score="+4.1s",
                player_id="7105",  # not followed
            ),
        ),
    )

    await jobs.events_tick()
    await _drain_kicked_tasks()

    # The SCHEDULED-within-lookahead row was polled, persisted, finalized.
    assert fake_provider.event_state_calls == ["harborline-250"]
    async with db() as session:
        row = await repository.get_event(session, EVENT_ID)
        assert row is not None
        assert row.phase == GamePhase.FINAL.value

    # ...and the followed driver's finish notified once.
    assert [event.dedupe_key for event in sent_events] == [f"{EVENT_ID}:final"]
    assert "Vale Arden" in sent_events[0].message


# ---------------------------------------------------------------------------
# refresh_pregame_rosters — same-day status re-sync before an imminent game
# ---------------------------------------------------------------------------
#
# The daily sync runs once at 5am, so a player scratched at noon used to be
# invisible until the next morning.  This job re-fetches the roster PAYLOAD
# ONLY (statuses ride on it) for teams with a followed player and a game
# inside the lookahead, and runs the same baseline diff as the daily sync.
# The cost bound is the reason it exists: no per-athlete overview calls, one
# roster call per team per cooldown window.

PREGAME_GAME_ID = f"{PROVIDER_ID}:pregame-game"
OTHER_GAME_ID = f"{PROVIDER_ID}:pregame-game-other"


async def _backdate_roster_sync(
    db: async_sessionmaker[AsyncSession],
    team_id: str = TEAM_ID,
    *,
    minutes: int = 90,
) -> None:
    """Age a team's ``roster_updated_at`` past the pre-game cooldown.

    ``replace_roster`` stamps it, so a freshly-seeded roster would look like
    a sync that just happened and the cooldown would (correctly) skip it.
    """
    async with db() as session:
        row = await session.get(TeamORM, team_id)
        assert row is not None
        row.roster_updated_at = utcnow() - timedelta(minutes=minutes)
        await session.commit()


async def _stored_players(
    db: async_sessionmaker[AsyncSession], team_id: str = TEAM_ID
) -> dict[str, PlayerORM]:
    async with db() as session:
        rows = await repository.get_roster(session, team_id)
    return {row.id: row for row in rows}


async def _seed_pregame_scenario(
    db: async_sessionmaker[AsyncSession],
    *,
    start_delta: timedelta = timedelta(hours=2),
) -> None:
    """A followed team, an enriched stored roster, a follow, an imminent game.

    The stored player carries the stat lines and the TheSportsDB headshot the
    daily sync paid for — a cheap re-sync must not blank them.
    """
    await _seed_followed_set(db)
    await _seed_roster(
        db,
        _roster(
            _player(
                ATHLETE_ID,
                "Rooke Marshwood",
                stat_line="18.4 PPG, 4.1 APG",
                career_stat_line="15.2 PPG, 3.6 APG",
                photo_url="https://img.example/rooke.png",
            )
        ),
    )
    await _follow(db, last_notified_status="active")
    await _seed_soon_game(db, start_delta=start_delta, game_id=PREGAME_GAME_ID)
    await _backdate_roster_sync(db)


async def test_pregame_resync_alerts_same_day_scratch_and_keeps_stat_lines(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """The whole feature in one test: a scratch announced hours before tip-off
    alerts now, at the cost of ONE roster call and no overview calls, and the
    season/career lines + headshot the cheap fetch didn't ask for survive."""
    await _seed_pregame_scenario(db)

    # What the cheap fetch returns: fresh status, no stat lines, no photo.
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(
            ATHLETE_ID,
            "Rooke Marshwood",
            status=PlayerStatus.OUT,
            status_detail="Out - ankle",
        )
    )

    await jobs.refresh_pregame_rosters()

    # Cost: one roster call, asked for WITHOUT the per-athlete stat-line pass.
    assert fake_provider.roster_calls == [(TEAM_ID, False)]
    assert fake_provider.overview_calls == []

    # The alert rides the same machinery as the daily sync's diff.
    assert [event.dedupe_key for event in sent_events] == [f"player:{ATHLETE_ID}:status:out"]
    assert sent_events[0].message == "Rooke Marshwood is out — Out - ankle"
    assert await _baseline(db) == "out"

    # The status is fresh, and the enrichment the daily sync paid for is intact.
    row = (await _stored_players(db))[f"espn:{ATHLETE_ID}"]
    assert row.status == PlayerStatus.OUT.value
    assert row.status_detail == "Out - ankle"
    assert row.stat_line == "18.4 PPG, 4.1 APG"
    assert row.career_stat_line == "15.2 PPG, 3.6 APG"
    assert row.photo_url == "https://img.example/rooke.png"


async def test_pregame_resync_skips_team_whose_game_is_beyond_the_lookahead(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """Tomorrow's game is the daily sync's business — no call, no alert."""
    await _seed_pregame_scenario(db, start_delta=timedelta(hours=10))
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT)
    )

    await jobs.refresh_pregame_rosters()

    assert fake_provider.roster_calls == []
    assert sent_events == []
    assert await _baseline(db) == "active"


async def test_pregame_resync_cooldown_suppresses_a_second_run(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sync cools itself down: ``replace_roster`` stamps
    ``roster_updated_at``, so the next tick inside the window is a no-op and
    the job can run every 15 minutes without re-fetching the same roster."""
    await _seed_pregame_scenario(db)
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT)
    )

    await jobs.refresh_pregame_rosters()
    await jobs.refresh_pregame_rosters()  # immediately after: inside the cooldown

    assert fake_provider.roster_calls == [(TEAM_ID, False)]
    assert len(sent_events) == 1

    # Shrink the cooldown and the very next run re-fetches (proving it was
    # the cooldown, not some other gate, that suppressed the second call).
    monkeypatch.setattr(get_settings(), "pregame_roster_cooldown_minutes", 0)
    await jobs.refresh_pregame_rosters()
    assert fake_provider.roster_calls == [(TEAM_ID, False), (TEAM_ID, False)]
    # ...and the unchanged status still alerts only once (baseline dedupe).
    assert len(sent_events) == 1


async def test_pregame_resync_makes_no_provider_call_without_player_follows(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """The cheapest gate first: nobody's players are followed, so the job
    touches no provider at all (there is nothing a fresh status could tell)."""
    await _seed_followed_set(db)
    await _seed_roster(db, _roster(_player(ATHLETE_ID, "Rooke Marshwood")))
    await _seed_soon_game(db, start_delta=timedelta(hours=2), game_id=PREGAME_GAME_ID)
    await _backdate_roster_sync(db)

    await jobs.refresh_pregame_rosters()

    assert fake_provider.roster_calls == []
    assert sent_events == []


async def test_pregame_resync_disabled_by_setting(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_pregame_scenario(db)
    monkeypatch.setattr(get_settings(), "pregame_roster_refresh_enabled", False)

    await jobs.refresh_pregame_rosters()

    assert fake_provider.roster_calls == []


async def test_pregame_resync_isolates_one_teams_provider_failure(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """One team's failing fetch must not cost the other team its alert."""
    await _seed_pregame_scenario(db)
    async with db() as session:
        await repository.upsert_team(session, OTHER_TEAM)
        await session.commit()
    await _seed_roster(
        db,
        _roster(
            _player(OTHER_ATHLETE_ID, "Sable Quillan", team_id=OTHER_TEAM_ID),
            team_id=OTHER_TEAM_ID,
        ),
    )
    await _follow(
        db,
        OTHER_ATHLETE_ID,
        "Sable Quillan",
        team_id=OTHER_TEAM_ID,
        last_notified_status="active",
    )
    await _seed_soon_game(
        db, start_delta=timedelta(hours=2), game_id=OTHER_GAME_ID, team_id=OTHER_TEAM_ID
    )
    await _backdate_roster_sync(db, OTHER_TEAM_ID)

    fake_provider.roster_errors.add(TEAM_ID)
    fake_provider.rosters[OTHER_TEAM_ID] = _roster(
        _player(
            OTHER_ATHLETE_ID,
            "Sable Quillan",
            status=PlayerStatus.DAY_TO_DAY,
            status_detail="Day-To-Day - wrist",
            team_id=OTHER_TEAM_ID,
        ),
        team_id=OTHER_TEAM_ID,
    )

    await jobs.refresh_pregame_rosters()  # must not raise

    assert sorted(fake_provider.roster_calls) == [(OTHER_TEAM_ID, False), (TEAM_ID, False)]
    assert [event.dedupe_key for event in sent_events] == [
        f"player:{OTHER_ATHLETE_ID}:status:day_to_day"
    ]
    # The failed team keeps its stored roster and its un-advanced baseline.
    assert (await _stored_players(db))[f"espn:{ATHLETE_ID}"].status == PlayerStatus.ACTIVE.value
    assert await _baseline(db) == "active"


async def test_pregame_resync_never_raises_when_the_scope_query_fails(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """House rule: the job swallows its own failures (``instrumented``
    re-raises anything that escapes, which would abort the scheduler run)."""
    await _seed_pregame_scenario(db)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom: scope query")

    monkeypatch.setattr(repository, "team_ids_with_games_starting_within", boom)

    await jobs.refresh_pregame_rosters()

    assert fake_provider.roster_calls == []
    assert sent_events == []


async def test_pregame_resync_skips_individual_sport_athlete_teams(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
) -> None:
    """A followed driver is a single-member "team" with no roster: fetching
    one would come back empty and the store would wipe its rows."""
    async with db() as session:
        await repository.upsert_league(session, RACING_LEAGUE)
        await repository.upsert_team(session, DRIVER_TEAM)
        await session.commit()
    await _follow(
        db,
        DRIVER_ESPN_ID,
        "Vale Arden",
        team_id=DRIVER_TEAM_ID,
        league_id=RACING_LEAGUE_ID,
        last_notified_status="active",
    )
    await _seed_soon_game(
        db,
        start_delta=timedelta(hours=2),
        game_id=f"{PROVIDER_ID}:stray-race-row",
        team_id=DRIVER_TEAM_ID,
        league_id=RACING_LEAGUE_ID,
    )

    await jobs.refresh_pregame_rosters()

    assert fake_provider.roster_calls == []


async def test_pregame_resync_then_live_tick_alerts_ruled_out_from_fresh_status(
    db: async_sessionmaker[AsyncSession],
    patched_scope: None,
    fake_provider: FakeProvider,
    sent_events: list,
) -> None:
    """End to end: the re-sync writes the same-day designation, and
    ``live_tick``'s starting-soon pass (which reads the STORED status) then
    alerts on it — data the 5am sync knew nothing about."""
    await _seed_pregame_scenario(db, start_delta=timedelta(minutes=10))
    fake_provider.rosters[TEAM_ID] = _roster(
        _player(
            ATHLETE_ID,
            "Rooke Marshwood",
            status=PlayerStatus.OUT,
            status_detail="Out - ankle",
        )
    )

    await jobs.refresh_pregame_rosters()
    await jobs.live_tick()

    keys = [event.dedupe_key for event in sent_events]
    assert keys == [
        f"player:{ATHLETE_ID}:status:out",  # the re-sync's roster diff
        f"{PREGAME_GAME_ID}:soon",
        f"{PREGAME_GAME_ID}:player-out:{ATHLETE_ID}",  # from the fresh status
    ]


async def test_carry_forward_enrichment_keeps_stored_lines_and_photos() -> None:
    """The regression that would silently empty the roster view: a cheap
    fetch's ``None`` never overwrites a stored stat line or headshot, while
    the fetch's own values (and a call-up it introduces) win."""
    stored = [
        PlayerORM(
            team_id=TEAM_ID,
            id=f"espn:{ATHLETE_ID}",
            name="Rooke Marshwood",
            status=PlayerStatus.ACTIVE.value,
            stat_line="18.4 PPG",
            career_stat_line="15.2 PPG",
            photo_url="https://img.example/rooke.png",
        ),
        PlayerORM(
            team_id=TEAM_ID,
            id=f"espn:{OTHER_ATHLETE_ID}",
            name="Sable Quillan",
            status=PlayerStatus.ACTIVE.value,
            stat_line="9.1 PPG",
            career_stat_line=None,
            photo_url=None,
        ),
    ]
    fetched = _roster(
        _player(ATHLETE_ID, "Rooke Marshwood", status=PlayerStatus.OUT),
        _player(OTHER_ATHLETE_ID, "Sable Quillan", stat_line="9.4 PPG"),
        _player("9003", "Wren Tallow"),  # a call-up the stored roster lacks
    )

    merged = scheduler_refresh._carry_forward_enrichment(stored, fetched)
    by_id = {player.id: player for player in merged.players}

    kept = by_id[f"espn:{ATHLETE_ID}"]
    assert kept.stat_line == "18.4 PPG"
    assert kept.career_stat_line == "15.2 PPG"
    assert kept.photo_url == "https://img.example/rooke.png"
    assert kept.status is PlayerStatus.OUT  # the fetch's whole point
    # A value the fetch DID carry wins over the stored one.
    assert by_id[f"espn:{OTHER_ATHLETE_ID}"].stat_line == "9.4 PPG"
    assert by_id[f"espn:{OTHER_ATHLETE_ID}"].career_stat_line is None
    # The call-up keeps its own (empty) enrichment rather than borrowing.
    assert by_id["espn:9003"].stat_line is None

    # Nothing stored yet (a brand-new team) is a passthrough.
    assert scheduler_refresh._carry_forward_enrichment([], fetched) is fetched


async def test_team_ids_with_games_starting_within_window_only(
    db: async_sessionmaker[AsyncSession],
) -> None:
    """The scope query: SCHEDULED sides starting inside the window only —
    nothing already under way, nothing beyond the horizon, ids not rows."""
    await _seed_followed_set(db, teams=(TEAM, OTHER_TEAM))
    async with db() as session:
        session.add(
            _scheduled_row(timedelta(hours=1), game_id="in-window", team_id=TEAM_ID),
        )
        session.add(
            _scheduled_row(timedelta(hours=9), game_id="beyond-horizon", team_id=OTHER_TEAM_ID),
        )
        started = _scheduled_row(timedelta(minutes=-30), game_id="already-started")
        started.phase = GamePhase.IN_PROGRESS.value
        session.add(started)
        await session.commit()

    async with db() as session:
        found = await repository.team_ids_with_games_starting_within(
            session, utcnow(), timedelta(hours=4)
        )
    assert found == {TEAM_ID}
