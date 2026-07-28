"""Tiny additive schema migrations, plus one legacy data repair.

Single-user app with no migration framework: ``Base.metadata.create_all``
creates missing TABLES but never adds COLUMNS to existing ones.  Every
entry here is an idempotent "add column if missing" applied at startup,
so an existing homelab database survives upgrades without manual steps.

Append-only: never edit or remove entries.  Columns must be NULL-able
OR ``NOT NULL`` with a ``DEFAULT`` (both sqlite and postgres can add
either in place; a bare NOT NULL without a default would fail on
non-empty tables).

:func:`prune_orphaned_rows` is the data-level counterpart, run in the
same startup step — see its docstring.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Column, Connection, Table, func, inspect, select, text
from sqlalchemy.sql.elements import ColumnElement

from app.models.orm import Base

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
    # The stadium's own "About" prose (TheSportsDB venue record) and which
    # source supplied the club description ("thesportsdb" | "wikipedia") —
    # the profile page attributes its text to the upstream source.
    ("teams", "venue_description", "TEXT"),
    ("teams", "description_source", "VARCHAR(16)"),
    # MMA method of victory (how a finished bout ended).
    ("games", "fight_method", "VARCHAR(24)"),
    ("games", "fight_detail", "VARCHAR(64)"),
    ("games", "fight_round", "INTEGER"),
    ("games", "fight_clock", "VARCHAR(16)"),
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


# ---------------------------------------------------------------------------
# Legacy data repair: rows whose parent no longer exists
# ---------------------------------------------------------------------------


def _referenced_table_names() -> set[str]:
    """Names of tables that some other table's foreign key points at."""
    return {
        fk.column.table.name for table in Base.metadata.tables.values() for fk in table.foreign_keys
    }


def _orphan_filter(
    table: Table, child_col: Column[Any], parent_col: Column[Any]
) -> ColumnElement[bool]:
    """Rows of ``table`` whose (non-null) FK has no matching parent row."""
    parent_exists = select(parent_col).where(parent_col == child_col).correlate(table).exists()
    return child_col.is_not(None) & ~parent_exists


def prune_orphaned_rows(conn: Connection) -> None:
    """Delete child rows left behind by a parent that is already gone.

    SQLite parses ``REFERENCES`` but only enforces it when asked, and the
    PRAGMA that asks is new (see ``app.db._enable_sqlite_foreign_keys``),
    so a homelab/desktop database predating it can hold rows the schema
    says are impossible.  The known case: ``standings_archive`` rows whose
    league was dropped by a re-follow, from back when ``replace_followed``
    did not clear that table.  They are invisible junk today, but under
    enforcement any statement that touches such a row fails on a
    constraint the row never satisfied — so they are swept once at
    startup.  Idempotent, and a guaranteed no-op on postgres (which never
    allowed them); it still runs there so both dialects exercise the SQL.

    Only tables that nothing else references are swept.  Deleting a row
    that is itself a parent — ``teams`` is the sole case — could violate
    the very constraint this repairs, and no code path can orphan one
    anyway (``replace_followed`` deletes leagues and teams together), so
    those are reported and left alone.
    """
    tables = set(inspect(conn).get_table_names())
    referenced = _referenced_table_names()
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        for fk in sorted(table.foreign_keys, key=lambda fk: fk.parent.name):
            if len(fk.constraint.elements) != 1:
                continue  # no composite foreign keys in this schema
            parent_col = fk.column
            if parent_col.table.name not in tables:
                continue
            orphaned = _orphan_filter(table, fk.parent, parent_col)
            if table.name in referenced:
                stale = conn.execute(
                    select(func.count()).select_from(table).where(orphaned)
                ).scalar_one()
                if stale:
                    logger.warning(
                        "migrations: %d %s row(s) reference a missing %s — left in place",
                        stale,
                        table.name,
                        parent_col.table.name,
                    )
                continue
            deleted = conn.execute(table.delete().where(orphaned)).rowcount or 0
            if deleted:
                logger.warning(
                    "migrations: removed %d orphaned %s row(s) (no matching %s)",
                    deleted,
                    table.name,
                    parent_col.table.name,
                )
