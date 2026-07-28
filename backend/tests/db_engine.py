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

SQLite ignores foreign keys unless ``PRAGMA foreign_keys=ON`` is set per
connection, so the default engine turns it on: without it a delete that
orphans children passes locally and only blows up on postgres (a real
past bug — replace_followed forgetting standings_archive).
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.models.orm import Base

TEST_DATABASE_URL = os.environ.get("SPORTSDASH_TEST_DATABASE_URL")


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_test_engine() -> AsyncEngine:
    if TEST_DATABASE_URL:
        # NullPool: every test disposes its engine; don't strand server
        # connections across the suite.
        return create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return engine


async def create_test_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if TEST_DATABASE_URL:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
