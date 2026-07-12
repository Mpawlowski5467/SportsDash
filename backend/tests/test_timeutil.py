"""Unit tests for the UTC-first time helpers.

These back the app's single most important convention (CONTRACTS.md:
"UTC internally; SQLite returns naive datetimes, so anything read from
the DB passes through ensure_utc") — and the Postgres/asyncpg path
returns AWARE datetimes, so both arms matter.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import timeutil

NY = ZoneInfo("America/New_York")


def test_ensure_utc_attaches_utc_to_naive() -> None:
    naive = datetime(2026, 6, 15, 12, 30)  # what SQLite hands back
    aware = timeutil.ensure_utc(naive)
    assert aware.tzinfo is timeutil.UTC
    assert aware.replace(tzinfo=None) == naive


def test_ensure_utc_converts_aware_to_utc() -> None:
    eastern = datetime(2026, 6, 15, 20, 0, tzinfo=NY)  # what asyncpg hands back
    aware = timeutil.ensure_utc(eastern)
    assert aware.tzinfo == timezone.utc or aware.utcoffset() == timedelta(0)
    assert aware.hour == 0 and aware.day == 16  # 20:00 EDT == 00:00 UTC next day


def test_ensure_utc_naive_and_aware_agree() -> None:
    """The same instant must compare equal whichever backend produced it."""
    instant = datetime(2026, 6, 15, 12, 30, tzinfo=timeutil.UTC)
    from_sqlite = timeutil.ensure_utc(instant.replace(tzinfo=None))
    from_asyncpg = timeutil.ensure_utc(instant.astimezone(NY))
    assert from_sqlite == from_asyncpg == instant


def test_local_day_bounds_regular_day() -> None:
    start, end = timeutil.local_day_bounds(date(2026, 6, 15), NY)
    assert start == datetime(2026, 6, 15, 4, 0, tzinfo=timeutil.UTC)  # EDT = UTC-4
    assert end - start == timedelta(hours=24)


def test_local_day_bounds_dst_spring_forward() -> None:
    # 2026-03-08 has only 23 local hours in America/New_York.
    start, end = timeutil.local_day_bounds(date(2026, 3, 8), NY)
    assert end - start == timedelta(hours=23)


def test_local_day_bounds_dst_fall_back() -> None:
    # 2026-11-01 has 25 local hours in America/New_York.
    start, end = timeutil.local_day_bounds(date(2026, 11, 1), NY)
    assert end - start == timedelta(hours=25)


def test_to_local_round_trip() -> None:
    utc_evening = datetime(2026, 1, 2, 1, 30, tzinfo=timeutil.UTC)
    local = timeutil.to_local(utc_evening, NY)
    assert (local.year, local.month, local.day, local.hour) == (2026, 1, 1, 20)
