"""Application settings, sourced from environment variables / .env.

All variables use the ``SPORTSDASH_`` prefix, e.g.
``SPORTSDASH_DATABASE_URL``, ``SPORTSDASH_TIMEZONE``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SPORTSDASH_", extra="ignore")

    # Infrastructure.  The sqlite default keeps local dev / tests zero-setup;
    # docker-compose overrides it with the postgres URL.
    database_url: str = "sqlite+aiosqlite:///./sportsdash.db"
    redis_url: str | None = None

    # Notifications (self-hosted ntfy).
    ntfy_url: str = "http://localhost:8090"
    ntfy_topic: str = "sportsdash"
    ntfy_token: str | None = None
    notifications_enabled: bool = True

    # Display timezone; all storage stays UTC.
    timezone: str = "America/New_York"

    # Followed teams/leagues definition.
    teams_config_path: str = "config/teams.yaml"

    # Polling cadence.
    live_poll_seconds: int = 45
    live_lead_minutes: int = 20  # begin fast polling this long before tip-off
    starting_soon_minutes: int = 15
    # A FINAL notification whose first send failed is re-attempted by the next
    # live/events tick, but only while the game/event is this recent — so a
    # fresh deploy or a long outage never floods the user with finals for
    # games that ended hours ago.
    resend_final_lookback_hours: int = 6
    news_refresh_minutes: int = 60
    # Locale for the auto-generated Google News feed per team, as
    # "lang-COUNTRY" (e.g. "pl-PL" for Polish-language coverage).
    news_locale: str = "en-US"
    daily_refresh_hour: int = 5  # local hour for schedule/standings/roster refresh

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Venue weather (Open-Meteo, keyless).  Current conditions on map pins and
    # a short forecast on outdoor scheduled games; best-effort and cached.
    weather_enabled: bool = True
    weather_units: Literal["metric", "imperial"] = "metric"
    weather_cache_minutes: int = 45  # weather changes slowly; cache generously

    # Club "About" enrichment via Wikipedia (keyless).  Fallback prose for the
    # team page when TheSportsDB has no description; best-effort and cached
    # with a long TTL (a club's history changes rarely).
    wiki_enabled: bool = True
    wiki_lang: str = "en"
    wiki_cache_minutes: int = 10080  # 7 days

    # Provider HTTP resilience (retry/backoff around upstream calls).
    provider_timeout_seconds: float = 15.0
    provider_max_retries: int = 3  # attempts beyond the first, on transient errors
    provider_backoff_base: float = 0.5  # seconds; doubled each retry (+ jitter)
    # Read data whose last refresh is older than this is flagged ``is_stale``
    # so the UI can warn (e.g. when the provider has been down for a while).
    data_stale_after_minutes: int = 1440  # 24h

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
