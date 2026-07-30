"""Engine wiring and the startup repair steps in ``app.db`` / ``app.migrations``.

The interesting case is sqlite, which parses ``REFERENCES`` but only
enforces it when a per-connection PRAGMA asks it to.  These tests build a
real file-backed database through ``app.db.get_engine`` (the production
path, not the test-only engine in ``tests/db_engine``) so the deployment
that actually ships — the desktop/homelab sqlite build — is the one under
test.  All fixture data is fictional.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app import db
from app.migrations import prune_orphaned_rows
from app.models.orm import GameORM, LeagueORM, StandingsArchiveORM, TeamORM
from tests.db_engine import create_test_schema, make_test_engine

LEAGUE_ID = "verdant-league"
GONE_LEAGUE_ID = "amber-league"  # followed once, dropped by a later re-follow


def _league(league_id: str) -> LeagueORM:
    return LeagueORM(
        id=league_id,
        sport="soccer",
        name=league_id.replace("-", " ").title(),
        provider="mock",
        provider_key=f"mock-{league_id}",
    )


def _archive(league_id: str) -> StandingsArchiveORM:
    return StandingsArchiveORM(
        league_id=league_id,
        season="2026",
        season_label="2025-26",
        rows=[],
    )


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'sportsdash.db'}"


@pytest.fixture
async def app_engine(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncEngine]:
    """The engine ``app.db`` builds in production, pointed at a temp file."""
    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(database_url=sqlite_url))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    try:
        yield db.get_engine()
    finally:
        await db.dispose_engine()


async def test_production_sqlite_engine_enforces_foreign_keys(app_engine: AsyncEngine) -> None:
    """The shipped engine sets the PRAGMA, so the desktop build is strict too.

    Tests have always run with foreign keys on; production sqlite had not,
    which is exactly why an orphaning delete could go unnoticed for a
    release.  Both halves are checked: the PRAGMA is on for a connection,
    and a write that orphans a row is actually rejected.
    """
    async with app_engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1

    await db.init_db()
    sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(_archive(GONE_LEAGUE_ID))  # no such league row
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_init_db_prunes_rows_a_pre_pragma_database_orphaned(
    app_engine: AsyncEngine, sqlite_url: str
) -> None:
    """Startup clears orphans an older, unenforced build left behind.

    Reproduces the real defect: a build without the PRAGMA deleted a
    league and kept its archived standings.  The next startup must sweep
    that row (nothing can ever match it again) while leaving every row
    whose league still exists alone.
    """
    await db.init_db()

    # A build with no PRAGMA — i.e. every release before this one.
    legacy = create_async_engine(sqlite_url)
    legacy_sessions = async_sessionmaker(legacy, expire_on_commit=False)
    try:
        async with legacy_sessions() as session:
            session.add_all([_league(LEAGUE_ID), _league(GONE_LEAGUE_ID)])
            await session.flush()
            session.add_all([_archive(LEAGUE_ID), _archive(GONE_LEAGUE_ID)])
            # An orphaned TEAM as well: reported, never deleted — dropping a
            # row other tables reference could break the constraint we just
            # started enforcing.
            session.add(
                TeamORM(
                    id="amber-thistledown-rovers",
                    league_id=GONE_LEAGUE_ID,
                    name="Thistledown Rovers",
                    abbreviation="THR",
                    provider_key="thistledown-rovers",
                    rss_feeds=[],
                )
            )
            await session.flush()
            # The unenforced delete that leaves the children behind.
            await session.execute(delete(LeagueORM).where(LeagueORM.id == GONE_LEAGUE_ID))
            await session.commit()
    finally:
        await legacy.dispose()

    await db.init_db()  # the next startup

    sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    async with sessions() as session:
        archived = (await session.execute(select(StandingsArchiveORM.league_id))).scalars().all()
        teams = (await session.execute(select(TeamORM.league_id))).scalars().all()
    assert list(archived) == [LEAGUE_ID]
    assert list(teams) == [GONE_LEAGUE_ID]


async def test_broadcasts_json_column_arrives_on_a_pre_existing_database(
    app_engine: AsyncEngine,
) -> None:
    """The first JSON entry in the additive path lands on the shipped engine.

    Simulates a database from a build predating ``games.broadcasts`` by
    dropping the column ``create_all`` just made (sqlite drops its data
    with it), then boots again: the additive migration must add it back
    as a JSON column, existing rows read NULL — the serializer treats
    that as ``[]`` — and a JSON list round-trips through it.
    """
    await db.init_db()
    sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(_league(LEAGUE_ID))
        await session.flush()
        session.add(
            GameORM(
                id="mock:g-tv",
                league_id=LEAGUE_ID,
                home_name="Verdant Harriers",
                away_name="Thistledown Rovers",
                start_time=datetime(2026, 6, 26, 23, 0, tzinfo=timezone.utc),
            )
        )
        await session.commit()

    # A build without the column — i.e. every release before this one.
    async with app_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE games DROP COLUMN broadcasts"))

    await db.init_db()  # the next startup

    async with sessions() as session:
        row = await session.get(GameORM, "mock:g-tv")
        assert row is not None
        assert row.broadcasts is None  # pre-existing row: NULL, never []
        row.broadcasts = ["Verdant Sports Network"]
        await session.commit()

    async with sessions() as session:
        row = await session.get(GameORM, "mock:g-tv")
        assert row is not None
        assert row.broadcasts == ["Verdant Sports Network"]


async def test_prune_orphaned_rows_leaves_a_healthy_database_untouched() -> None:
    """Idempotent no-op when every parent is present — on both dialects.

    Runs against the shared test engine, so the postgres CI job executes
    the sweep's SQL too (where an orphan cannot exist in the first place).
    """
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(_league(LEAGUE_ID))
            await session.flush()
            session.add(_archive(LEAGUE_ID))
            await session.commit()

        async with engine.begin() as conn:
            await conn.run_sync(prune_orphaned_rows)
            await conn.run_sync(prune_orphaned_rows)

        async with sessions() as session:
            archived = (
                (await session.execute(select(StandingsArchiveORM.league_id))).scalars().all()
            )
        assert list(archived) == [LEAGUE_ID]
    finally:
        await engine.dispose()
