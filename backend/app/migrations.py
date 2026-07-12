"""Tiny additive schema migrations.

Single-user app with no migration framework: ``Base.metadata.create_all``
creates missing TABLES but never adds COLUMNS to existing ones.  Every
entry here is an idempotent "add column if missing" applied at startup,
so an existing homelab database survives upgrades without manual steps.

Append-only: never edit or remove entries.  Columns must be NULL-able
OR ``NOT NULL`` with a ``DEFAULT`` (both sqlite and postgres can add
either in place; a bare NOT NULL without a default would fail on
non-empty tables).
"""
from __future__ import annotations

import logging

from sqlalchemy import Connection, inspect, text

logger = logging.getLogger(__name__)

# (table, column, DDL type) — append-only.
# NOTE: `BOOLEAN DEFAULT FALSE NOT NULL` is portable here — sqlite treats
# BOOLEAN as INTEGER and both dialects backfill from the DEFAULT when the
# column is added to a non-empty table.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("news_items", "image_url", "TEXT"),
    ("leagues", "follow_all", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("games", "series", "VARCHAR(128)"),
    ("players", "stat_line", "VARCHAR(256)"),
    ("teams", "home_venue", "VARCHAR(256)"),
    ("teams", "venue_lat", "DOUBLE PRECISION"),
    ("teams", "venue_lon", "DOUBLE PRECISION"),
    ("teams", "venue_capacity", "INTEGER"),
    ("teams", "venue_opened", "INTEGER"),
    ("teams", "venue_image_url", "TEXT"),
    ("teams", "venue_location", "VARCHAR(256)"),
    ("teams", "venue_surface", "VARCHAR(64)"),
    # Per-side crest/color so EVERY game card shows real logos (e.g. World
    # Cup nation flags), not just followed teams' — mirrors the standings fix.
    ("games", "home_logo_url", "TEXT"),
    ("games", "away_logo_url", "TEXT"),
    ("games", "home_color", "VARCHAR(16)"),
    ("games", "away_color", "VARCHAR(16)"),
    # Player photos + career stats (extracted from existing roster/overview
    # payloads) and club "About" (TheSportsDB description + founded year).
    ("players", "career_stat_line", "VARCHAR(256)"),
    ("players", "photo_url", "TEXT"),
    ("teams", "description", "TEXT"),
    ("teams", "founded_year", "INTEGER"),
    # Whole-competition (follow_all) news: keyed by league instead of team.
    ("news_items", "league_id", "VARCHAR(64)"),
)


def run_additive_migrations(conn: Connection) -> None:
    """Synchronous; call via ``conn.run_sync`` after ``create_all``."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table, column, ddl_type in _ADDITIVE_COLUMNS:
        if table not in tables:
            continue  # create_all just made it, with all columns present
        existing = {col["name"] for col in inspector.get_columns(table)}
        if column in existing:
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
        logger.info("migrations: added %s.%s", table, column)
