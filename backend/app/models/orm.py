"""SQLAlchemy ORM models (the persistence cache of normalized domain data).

Datetime columns are ``DateTime(timezone=True)`` and always hold UTC.
Note: SQLite hands back naive datetimes on read — normalize anything
read from these columns with ``app.timeutil.ensure_utc`` before use.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


class LeagueORM(Base):
    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sport: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    provider_key: Mapped[str] = mapped_column(String(128))
    # Whole-competition follow: sync every fixture in this league, not
    # just a followed team's. True for competitions followed in full
    # (e.g. an entire World Cup); False when the league exists only as a
    # context for followed teams / standings.
    follow_all: Mapped[bool] = mapped_column(Boolean, default=False)


class TeamCompetitionORM(Base):
    """Extra leagues a followed team's schedule is pulled from.

    A national team plays across several competitions (World Cup,
    Nations League, friendlies); its ``TeamORM.league_id`` is the
    primary one (used for standings/rosters/display) and each extra
    competition it appears in gets a row here so the scheduler fans the
    team's schedule fetch across all of them.  ``provider_key`` is the
    team's id in that competition's context (global for ESPN nations).
    """

    __tablename__ = "team_competitions"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(128))


class TeamORM(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"))
    name: Mapped[str] = mapped_column(String(128))
    abbreviation: Mapped[str] = mapped_column(String(8))
    provider_key: Mapped[str] = mapped_column(String(128))
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rss_feeds: Mapped[list] = mapped_column(JSON, default=list)
    roster_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Home-venue location + facts for the map view (geocoded/enriched + cached).
    home_venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    venue_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    venue_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    venue_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue_opened: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    venue_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    venue_surface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Club "About": a history/description paragraph and the founding year,
    # enriched by name from TheSportsDB (Wikipedia fallback) — null until resolved.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The stadium's own "About" prose (TheSportsDB ``strStadiumDescription``)
    # and the club description's upstream source ("thesportsdb" | "wikipedia"),
    # so the profile page can attribute its text — null until resolved.
    venue_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


class GameORM(Base):
    """A game involving at least one followed team, plus its last-known state.

    The state columns double as the scheduler's "last seen" snapshot for
    transition diffing across polls.
    """

    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<provider>:<key>"
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"))
    home_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    home_name: Mapped[str] = mapped_column(String(128))
    away_name: Mapped[str] = mapped_column(String(128))
    home_abbreviation: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_abbreviation: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Per-side crest/color (e.g. nation flags) so every card shows a real logo.
    home_logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    away_logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    home_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    away_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Tournament/round or fight-card context for individual sports
    # (e.g. "Wimbledon · QF", "UFC 320"); None for team sports.
    series: Mapped[str | None] = mapped_column(String(128), nullable=True)

    phase: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    home_score: Mapped[int] = mapped_column(Integer, default=0)
    away_score: Mapped[int] = mapped_column(Integer, default=0)
    period: Mapped[int] = mapped_column(Integer, default=0)
    period_label: Mapped[str] = mapped_column(String(32), default="")
    clock: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_intermission: Mapped[bool] = mapped_column(Boolean, default=False)
    state_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EventORM(Base):
    """A leaderboard competition (golf tournament, etc.).

    The Event counterpart to :class:`GameORM`.  ``leaderboard`` is a JSON
    list of dicts shaped exactly like ``schemas.LeaderRowOut``.  The
    state columns double as the scheduler's last-seen snapshot.
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<provider>:<key>"
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)

    phase: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    round_label: Mapped[str] = mapped_column(String(48), default="")
    leaderboard: Mapped[list] = mapped_column(JSON, default=list)
    state_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StandingsORM(Base):
    __tablename__ = "standings"

    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"), primary_key=True)
    season: Mapped[str] = mapped_column(String(32))
    # JSON list of dicts shaped exactly like schemas.StandingRowOut.
    rows: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StandingsArchiveORM(Base):
    """One final (or fetched-on-demand) standings table per league season.

    ``StandingsORM`` stays the rolling current snapshot; this table is
    append-per-season history.  ``season`` is the numeric season key used
    in API queries (the ENDING year for cross-year seasons: "2025-26" ->
    "2026"); ``season_label`` keeps the provider's display form.
    """

    __tablename__ = "standings_archive"

    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"), primary_key=True)
    season: Mapped[str] = mapped_column(String(16), primary_key=True)
    season_label: Mapped[str] = mapped_column(String(32))
    # JSON list of dicts shaped exactly like schemas.StandingRowOut.
    rows: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerORM(Base):
    __tablename__ = "players"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    position: Mapped[str | None] = mapped_column(String(32), nullable=True)
    jersey_number: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    status_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    stat_line: Mapped[str | None] = mapped_column(String(256), nullable=True)
    career_stat_line: Mapped[str | None] = mapped_column(String(256), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsORM(Base):
    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # hash of url
    # team_id for a followed-team article; league_id for a whole-competition
    # (follow_all) article, which has no TeamORM row. Exactly one is set.
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    league_id: Mapped[str | None] = mapped_column(
        ForeignKey("leagues.id"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationSentORM(Base):
    """Dedupe ledger so each logical event notifies exactly once."""

    __tablename__ = "notifications_sent"

    dedupe_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppMetaORM(Base):
    """Tiny key/value store for app-level flags (e.g. the ``onboarded`` flag)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class StadiumORM(Base):
    """Resolved stadium location + facts, cached independently of teams.

    Keyed by ``"{provider}:{provider_key}"`` so a venue can be resolved
    once and reused — both for a followed team and for the many teams of
    a whole-competition follow (e.g. all World Cup nations) that have no
    ``TeamORM`` row of their own.  Populated by the location refresh /
    map assembly via the stadium-enrichment + geocode pipeline.
    """

    __tablename__ = "stadiums"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)  # "{provider}:{key}"
    team_name: Mapped[str] = mapped_column(String(128))
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    surface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # None until a lookup runs; lets us cache "no result" and not retry hot.
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationPrefORM(Base):
    """Per-scope notification preferences.

    ``scope`` is ``"global"``, ``"team:{id}"``, or ``"league:{id}"``.
    Resolution is most-specific-wins (team → league → global default).
    ``muted`` silences the scope entirely; ``events`` is a JSON map of
    ``{event_type: bool}`` overriding which transition types notify.
    """

    __tablename__ = "notification_prefs"

    scope: Mapped[str] = mapped_column(String(96), primary_key=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    events: Mapped[dict] = mapped_column(JSON, default=dict)
