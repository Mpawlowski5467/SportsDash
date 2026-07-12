"""Async engine / session management.

The engine is created lazily so tests can point
``SPORTSDASH_DATABASE_URL`` somewhere else before first use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    """Create all tables, then apply additive column migrations.

    Single-user homelab app: no full migration framework, just
    idempotent add-column-if-missing steps (see ``app.migrations``).
    """
    from app.migrations import run_additive_migrations
    from app.models.orm import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_additive_migrations)


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
