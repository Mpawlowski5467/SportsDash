"""Test database engine factory.

By default every DB-touching test runs on a fresh in-memory SQLite
engine — fast and hermetic. Setting ``SPORTSDASH_TEST_DATABASE_URL``
(e.g. ``postgresql+asyncpg://…``, as the CI postgres job does) points
the same fixtures at a real server instead, so the asyncpg path is
exercised too: tz-AWARE datetimes coming back from the driver (SQLite
returns naive — the difference behind a real past bug), enforced
VARCHAR widths, and real upsert semantics.

On a shared server the schema is dropped and recreated per test for
isolation; in-memory SQLite gets a fresh database per engine anyway.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.models.orm import Base

TEST_DATABASE_URL = os.environ.get("SPORTSDASH_TEST_DATABASE_URL")


def make_test_engine() -> AsyncEngine:
    if TEST_DATABASE_URL:
        # NullPool: every test disposes its engine; don't strand server
        # connections across the suite.
        return create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    return create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)


async def create_test_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if TEST_DATABASE_URL:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
