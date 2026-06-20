"""UTC-first time helpers.

Everything is stored and computed in UTC; conversion to the user's
configured timezone happens only at the API response boundary (and in
the frontend).  SQLite returns naive datetimes even for
``DateTime(timezone=True)`` columns, so anything read from the DB must
pass through :func:`ensure_utc` before being compared or serialized.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC to naive datetimes; convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return ensure_utc(dt).astimezone(tz)


def local_today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants spanning the local calendar day [00:00, 24:00)."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)
