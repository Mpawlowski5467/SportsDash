"""Async engine / session management.

The engine is created lazily so tests can point
``SPORTSDASH_DATABASE_URL`` somewhere else before first use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite parses REFERENCES but ignores it unless asked, per connection.

    Postgres (docker-compose) enforces foreign keys unconditionally, so
    without this the DESKTOP/homelab sqlite build is the one deployment
    where a delete that orphans children succeeds silently — which is how
    ``replace_followed`` came to leave ``standings_archive`` rows behind
    with no league.  Same PRAGMA the test engine sets (tests/db_engine.py),
    so what the suite proves is what ships.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        if _engine.dialect.name == "sqlite":
            event.listen(_engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    """Create all tables, apply additive column migrations, repair orphans.

    Single-user homelab app: no full migration framework, just
    idempotent add-column-if-missing steps (see ``app.migrations``).
    The orphan sweep runs last, before the app opens any session: it
    clears child rows a pre-PRAGMA sqlite database was able to keep after
    their parent was deleted, which foreign-key enforcement would now
    trip over.
    """
    from app.migrations import prune_orphaned_rows, run_additive_migrations
    from app.models.orm import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_additive_migrations)
        await conn.run_sync(prune_orphaned_rows)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_session_factory()() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for scheduler jobs and scripts (commits on success)."""
    async with get_session_factory()() as session:
        yield session
        await session.commit()
