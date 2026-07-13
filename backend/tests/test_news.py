"""Tests for the three-source news merge service.

All teams, leagues, feeds, and articles are fictional.  Network access
is monkeypatched away; persistence runs against a throwaway in-memory
SQLite database built directly here (same pattern as
``test_repository.py``) so global settings and the app engine stay
untouched.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import feedparser
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import domain
from tests.db_engine import create_test_schema, make_test_engine
from app.services import news, repository

LEAGUE_ID = "pinnacle-basketball"
TEAM_ID = "ashport-comets"
RSS_FEED_URL = "https://news.example/ashport.xml"


def _news_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


COMP_ID = "worldcup"


def make_item(
    url: str,
    *,
    title: str = "Comets clinch a playoff berth",
    source: str = "Ashport Sports Wire",
    team_id: str | None = TEAM_ID,
    league_id: str | None = None,
) -> domain.NewsItem:
    return domain.NewsItem(
        id=_news_id(url),
        team_id=team_id,
        title=title,
        url=url,
        source=source,
        published_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        summary="Recap of the latest action.",
        league_id=league_id,
    )


@pytest.fixture
async def session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory DB + a ``session_scope`` patched into the news module."""
    engine = make_test_engine()
    await create_test_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(news, "session_scope", scope)
    yield factory
    await engine.dispose()


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    rss_feeds: tuple[str, ...] = (RSS_FEED_URL,),
) -> None:
    async with factory() as session:
        await repository.upsert_league(
            session,
            domain.League(
                id=LEAGUE_ID,
                sport=domain.Sport.BASKETBALL,
                name="Pinnacle Basketball League",
                provider="mock",
                provider_key="mock-basketball",
            ),
        )
        await repository.upsert_team(
            session,
            domain.Team(
                id=TEAM_ID,
                league_id=LEAGUE_ID,
                name="Ashport Comets",
                abbreviation="ASH",
                provider_key="ashport-comets",
                rss_feeds=rss_feeds,
            ),
        )
        await session.commit()


async def _seed_follow_all(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A whole-competition (``follow_all``) league with NO team rows."""
    async with factory() as session:
        await repository.upsert_league(
            session,
            domain.League(
                id=COMP_ID,
                sport=domain.Sport.SOCCER,
                name="World Cup",
                provider="mock",
                provider_key="soccer/fifa.world",
                follow_all=True,
            ),
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Google News URL builder
# ---------------------------------------------------------------------------


def test_google_news_url_en_us() -> None:
    url = news.build_google_news_url("Ashport Comets", "en-US")
    assert url == (
        "https://news.google.com/rss/search?q=%22Ashport%20Comets%22&hl=en-US&gl=US&ceid=US:en"
    )


def test_google_news_url_pl_pl() -> None:
    url = news.build_google_news_url("Rivermont Stags", "pl-PL")
    assert url == (
        "https://news.google.com/rss/search?q=%22Rivermont%20Stags%22&hl=pl-PL&gl=PL&ceid=PL:pl"
    )


def test_google_news_url_normalizes_case_and_falls_back() -> None:
    assert news.build_google_news_url("Ashport Comets", "PL-pl").endswith(
        "&hl=pl-PL&gl=PL&ceid=PL:pl"
    )
    # A malformed locale must never break the refresh; fall back to en-US.
    assert news.build_google_news_url("Ashport Comets", "nonsense").endswith(
        "&hl=en-US&gl=US&ceid=US:en"
    )


# ---------------------------------------------------------------------------
# Feed entry parsing: images + per-entry source
# ---------------------------------------------------------------------------

_FEED_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Ashport Sports Wire</title>
    <item>
      <title>Comets clinch a playoff berth</title>
      <link>https://news.example/ashport/clinch</link>
      <description>Recap of the clincher.</description>
      <media:thumbnail url="https://img.example/clinch-thumb.jpg"/>
    </item>
    <item>
      <title>Stags edge Comets in overtime</title>
      <link>https://news.example/ashport/ot-loss</link>
      <media:content url="https://img.example/ot-loss.jpg" medium="image"/>
    </item>
    <item>
      <title>Comets sign Jory Vance</title>
      <link>https://news.example/ashport/signing</link>
      <enclosure url="https://img.example/signing.jpg" type="image/jpeg" length="1234"/>
    </item>
    <item>
      <title>Practice report</title>
      <link>https://news.example/ashport/practice</link>
      <enclosure url="https://media.example/practice.mp3" type="audio/mpeg" length="99"/>
      <source url="https://gazette.example/feed">Harborline Gazette</source>
    </item>
  </channel>
</rss>
"""


def test_entry_parsing_extracts_images_and_entry_source() -> None:
    parsed = feedparser.parse(_FEED_XML)
    items = [
        news._entry_to_news_item(entry, team_id=TEAM_ID, source="Ashport Sports Wire")
        for entry in parsed.entries
    ]
    assert all(item is not None for item in items)
    assert len(items) == 4

    by_url = {item.url: item for item in items if item is not None}
    assert (
        by_url["https://news.example/ashport/clinch"].image_url
        == "https://img.example/clinch-thumb.jpg"
    )
    assert (
        by_url["https://news.example/ashport/ot-loss"].image_url
        == "https://img.example/ot-loss.jpg"
    )
    assert (
        by_url["https://news.example/ashport/signing"].image_url
        == "https://img.example/signing.jpg"
    )
    # An audio enclosure is not an image.
    assert by_url["https://news.example/ashport/practice"].image_url is None

    # Source defaults to the feed title; a per-entry <source> (as Google
    # News emits) wins because the entry link is a redirect URL.
    assert by_url["https://news.example/ashport/clinch"].source == "Ashport Sports Wire"
    assert by_url["https://news.example/ashport/practice"].source == "Harborline Gazette"


# ---------------------------------------------------------------------------
# refresh_all_news: three-source merge
# ---------------------------------------------------------------------------


async def test_refresh_dedupes_identical_urls_across_sources(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(session_factory)

    shared_url = "https://news.example/ashport/clinch"
    provider_url = "https://news.example/ashport/provider-exclusive"

    class FakeProvider:
        provider_id = "mock"

        async def get_news(self, league: domain.League, team: domain.Team) -> list[domain.NewsItem]:
            assert league.id == LEAGUE_ID
            assert team.id == TEAM_ID
            return [
                make_item(shared_url, source="Provider Wire"),
                make_item(provider_url, title="Provider exclusive"),
            ]

    monkeypatch.setattr(news.registry, "get_provider", lambda provider_id: FakeProvider())

    async def fake_rss(team) -> list[domain.NewsItem]:
        return [make_item(shared_url, source="Ashport Sports Wire")]

    async def fake_google(team) -> list[domain.NewsItem]:
        return [make_item(shared_url, source="Harborline Gazette")]

    monkeypatch.setattr(news, "fetch_team_news", fake_rss)
    monkeypatch.setattr(news, "fetch_google_news", fake_google)

    assert await news.refresh_all_news() == 2

    async with session_factory() as session:
        rows = await repository.list_news(session)
    assert {row.url for row in rows} == {shared_url, provider_url}
    by_url = {row.url: row for row in rows}
    # The first copy (source order: provider, rss, google) is kept.
    assert by_url[shared_url].source == "Provider Wire"

    # A second refresh inserts nothing new.
    assert await news.refresh_all_news() == 0


async def test_provider_failure_does_not_block_other_sources(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(session_factory)

    class ExplodingProvider:
        provider_id = "mock"

        async def get_news(self, league: domain.League, team: domain.Team) -> list[domain.NewsItem]:
            raise RuntimeError("provider outage")

    monkeypatch.setattr(news.registry, "get_provider", lambda provider_id: ExplodingProvider())

    rss_url = "https://news.example/ashport/roster-notes"

    async def fake_rss(team) -> list[domain.NewsItem]:
        return [make_item(rss_url, title="Roster notes")]

    async def fake_google(team) -> list[domain.NewsItem]:
        raise RuntimeError("google outage")

    monkeypatch.setattr(news, "fetch_team_news", fake_rss)
    monkeypatch.setattr(news, "fetch_google_news", fake_google)

    # Provider and Google both fail; the RSS item is still stored.
    assert await news.refresh_all_news() == 1

    async with session_factory() as session:
        rows = await repository.list_news(session)
    assert [row.url for row in rows] == [rss_url]
    assert rows[0].team_id == TEAM_ID


# ---------------------------------------------------------------------------
# Whole-competition (follow_all) league news
# ---------------------------------------------------------------------------


async def test_refresh_ingests_follow_all_league_news(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Following a whole competition (no team) still pulls league-wide news."""
    await _seed_follow_all(session_factory)

    comp_url = "https://news.example/worldcup/final-preview"

    class FakeProvider:
        provider_id = "mock"

        async def get_news(
            self, league: domain.League, team: domain.Team
        ) -> list[domain.NewsItem]:  # pragma: no cover - no team rows seeded
            raise AssertionError("get_news must not run for a teamless league")

        async def get_league_news(self, league: domain.League) -> list[domain.NewsItem]:
            assert league.id == COMP_ID
            assert league.follow_all is True
            return [
                make_item(
                    comp_url,
                    title="World Cup final preview",
                    team_id=None,
                    league_id=COMP_ID,
                )
            ]

    monkeypatch.setattr(news.registry, "get_provider", lambda provider_id: FakeProvider())

    assert await news.refresh_all_news() == 1

    async with session_factory() as session:
        rows = await repository.list_news(session, league_id=COMP_ID)
    assert [row.url for row in rows] == [comp_url]
    assert rows[0].team_id is None
    assert rows[0].league_id == COMP_ID

    # A second refresh inserts nothing new.
    assert await news.refresh_all_news() == 0


async def test_list_news_scopes_by_team_or_league(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _seed_follow_all(session_factory)

    team_url = "https://news.example/ashport/team-only"
    comp_url = "https://news.example/worldcup/comp-only"
    async with session_factory() as session:
        await repository.upsert_news(
            session,
            [
                make_item(team_url, title="Team note", team_id=TEAM_ID),
                make_item(comp_url, title="Comp note", team_id=None, league_id=COMP_ID),
            ],
        )
        await session.commit()

    async with session_factory() as session:
        all_rows = await repository.list_news(session)
        team_rows = await repository.list_news(session, team_id=TEAM_ID)
        comp_rows = await repository.list_news(session, league_id=COMP_ID)

    assert {row.url for row in all_rows} == {team_url, comp_url}
    assert [row.url for row in team_rows] == [team_url]
    assert [row.url for row in comp_rows] == [comp_url]


async def test_refresh_news_for_league_noop_when_not_follow_all(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A normally-followed (partial) league + an unknown id are both no-ops:
    # league news only applies to whole-competition follows.
    await _seed(session_factory)

    monkeypatch.setattr(
        news.registry,
        "get_provider",
        lambda provider_id: (_ for _ in ()).throw(AssertionError("provider must not be consulted")),
    )

    assert await news.refresh_news_for_league(LEAGUE_ID) == 0
    assert await news.refresh_news_for_league("no-such-league") == 0


def test_google_news_url_includes_league_context() -> None:
    url = news.build_google_news_url("Athletics", "en-US", context="Coastal Baseball Circuit")
    assert "q=%22Athletics%22%20Coastal%20Baseball%20Circuit" in url
    # Without context the bare quoted name is preserved.
    assert "q=%22Athletics%22&" in news.build_google_news_url("Athletics", "en-US")


def test_strip_html_decodes_entities() -> None:
    assert news._strip_html("Comets&nbsp;&nbsp;win &amp; advance") == "Comets win & advance"
    # Entity-encoded brackets must not survive as markup or text tags.
    assert "<script>" not in news._strip_html("&lt;script&gt;alert(1)&lt;/script&gt;safe")
