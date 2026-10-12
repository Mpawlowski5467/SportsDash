"""Unit tests for the keyless Wikipedia club-summary enrichment.

The search endpoint only proposes articles, in an order that is neither
relevant enough nor stable enough to trust; these tests pin what it takes
for one of them to be used as a team's "About" text.  All names are
fictional.  ``wiki_search.json`` and ``wiki_summary.json`` are the two
endpoints' real response shapes: the search is the one that used to file a
defunct neighbour's history under the club that was asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.models.domain import Sport
from app.services import wiki

FIXTURES = Path(__file__).parent / "fixtures"

TEAM = "Port Varen Lanterns"
HOCKEY = wiki._SPORT_TERMS["hockey"]
SOCCER = wiki._SPORT_TERMS["soccer"]


def _load(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _page(title: str = TEAM, **overrides: Any) -> dict[str, Any]:
    """The recorded REST summary, retitled and with fields replaced."""
    return {**_load("wiki_summary.json"), "title": title, **overrides}


class _Wikipedia:
    """A stand-in for both endpoints that records what was asked of it."""

    def __init__(self, search: dict[str, Any] | list[str], pages: dict[str, dict[str, Any]]):
        if isinstance(search, list):
            search = {"query": {"search": [{"ns": 0, "title": title} for title in search]}}
        self._search = search
        self._pages = pages
        self.queries: list[str] = []
        self.fetched: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/w/api.php":
            self.queries.append(request.url.params["srsearch"])
            return httpx.Response(200, json=self._search)
        title = request.url.path.rsplit("/", 1)[-1].replace("_", " ")
        self.fetched.append(title)
        page = self._pages.get(title)
        return httpx.Response(200, json=page) if page else httpx.Response(404, json={})


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        wiki.httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport, **k)
    )


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get(_key: str):
        return None

    async def _set(*_a, **_k):
        return None

    monkeypatch.setattr(wiki.cache, "cache_get_json", _get)
    monkeypatch.setattr(wiki.cache, "cache_set_json", _set)


# ---------------------------------------------------------------------------
# The article has to be the team's own
# ---------------------------------------------------------------------------


async def test_team_summary_ignores_a_neighbour_the_search_ranks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorded search puts a defunct club of the same city and sport first.

    Its lead matches every qualifier word, which is why it outranks the club
    that was asked for.  Only the article titled like the team is fetched.
    """
    wikipedia = _Wikipedia(
        _load("wiki_search.json"),
        {
            TEAM: _page(),
            "Port Varen Mariners": _page(
                "Port Varen Mariners",
                extract="The Port Varen Mariners were a professional ice hockey team.",
            ),
        },
    )
    _serve(monkeypatch, wikipedia)

    result = await wiki.team_summary(TEAM, sport="hockey")

    assert result is not None
    assert result.title == TEAM
    assert result.extract is not None
    assert result.extract.startswith("The Port Varen Lanterns are a professional ice hockey team")
    assert result.image_url == "https://upload.example.org/lanterns/Port_Varen_Lanterns.svg.png"
    assert wikipedia.queries == ["Port Varen Lanterns ice hockey team"]
    assert wikipedia.fetched == [TEAM]


async def test_team_summary_none_when_no_title_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hits that are all about the team, none of them the team: nothing is shown."""
    wikipedia = _Wikipedia(
        [
            "Port Varen Mariners",
            "Ilse Brandvold",
            "List of Port Varen Lanterns draft picks",
            "2026–27 Port Varen Lanterns season",
            "Port Varen Lanterns–Saltmarsh Gulls rivalry",
        ],
        {"Port Varen Mariners": _page("Port Varen Mariners")},
    )
    _serve(monkeypatch, wikipedia)

    assert await wiki.team_summary(TEAM, sport="hockey") is None
    assert wikipedia.fetched == []


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("Port Varen Lanterns", "Port Varen Lanterns"),
        ("Port Varen Lanterns (ice hockey)", "Port Varen Lanterns"),
        ("Port Varen Lanterns (Ice Hockey)", "Port Varen Lanterns"),
        ("port varen lanterns", "Port Varen Lanterns"),
        # Accents, punctuation and "&" are not differences.
        ("Saint-Orrin Étoile", "Saint Orrin Etoile"),
        ("St. Brannoch Blues", "St Brannoch Blues"),
        ("Brackwater & Hove Wanderers", "Brackwater and Hove Wanderers"),
        ("Queen's Reach Rovers", "Queens Reach Rovers"),
    ],
)
def test_title_is_the_teams(title: str, name: str) -> None:
    assert wiki._is_title_for(title, name, HOCKEY)


@pytest.mark.parametrize(
    "title",
    [
        "Port Varen Mariners",  # the neighbour
        "Port Varen",  # the city
        "Lanterns",
        "Port Varen Lanterns Women",
        "Port Varen Lanterns II",
        "List of Port Varen Lanterns draft picks",
        "2026–27 Port Varen Lanterns season",
        "History of the Port Varen Lanterns",
        # A "(…)" that is not this sport marks a different club of the name.
        "Port Varen Lanterns (1924–1941)",
        "Port Varen Lanterns (original)",
        "Port Varen Lanterns (Saltmarsh)",
        "Port Varen Lanterns (baseball)",
        "Port Varen Lanterns (ice hockey, born 2002)",
        "Port Varen Lanterns (disambiguation)",
    ],
)
def test_title_is_not_the_teams(title: str) -> None:
    assert not wiki._is_title_for(title, TEAM, HOCKEY)


def test_a_club_abbreviation_is_part_of_the_name() -> None:
    """A title is not stretched to fit a shorter name — nor a longer one cut.

    "Emberfall" could be "Emberfall F.C." or the unrelated "FC Emberfall";
    the name cannot say which, so it is neither.
    """
    assert not wiki._is_title_for("Emberfall F.C.", "Emberfall", SOCCER)
    assert not wiki._is_title_for("FC Emberfall", "Emberfall", SOCCER)
    assert not wiki._is_title_for("Emberfall", "Emberfall FC", SOCCER)
    assert not wiki._is_title_for("Emberfall S.C.", "Emberfall FC", SOCCER)
    # The same abbreviation, dotted or not, is the same name.
    assert wiki._is_title_for("Emberfall F.C.", "Emberfall FC", SOCCER)
    assert wiki._is_title_for("AFC Emberfall", "AFC Emberfall", SOCCER)


async def test_team_summary_prefers_the_sport_suffixed_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A "Name (sport)" title exists because the bare one is something else."""
    wikipedia = _Wikipedia(
        [TEAM, "Port Varen Lanterns (ice hockey)"],
        {
            TEAM: _page(description="Annual harbour festival", extract="A festival of lights."),
            "Port Varen Lanterns (ice hockey)": _page("Port Varen Lanterns (ice hockey)"),
        },
    )
    _serve(monkeypatch, wikipedia)

    result = await wiki.team_summary(TEAM, sport="hockey")

    assert result is not None and result.title == "Port Varen Lanterns (ice hockey)"
    assert wikipedia.fetched == ["Port Varen Lanterns (ice hockey)"]


# ---------------------------------------------------------------------------
# …and it has to say it is that sport's
# ---------------------------------------------------------------------------


async def test_team_summary_none_for_the_town_a_club_is_named_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare title is the town; its lead gets to the club only later."""
    town = _page(
        "Emberfall",
        description="Town in Saltmarsh",
        extract=(
            "Emberfall is a market town in the county of Saltmarsh, on the north bank of "
            "the River Orrin, eleven miles upstream of Port Varen. It grew up around a "
            "medieval bridge and a weekly cattle market, and later around its tanneries. "
            "The town is home to Emberfall Football Club."
        ),
    )
    assert "football club" in town["extract"].casefold()
    wikipedia = _Wikipedia(["Emberfall F.C.", "Emberfall"], {"Emberfall": town})
    _serve(monkeypatch, wikipedia)

    assert await wiki.team_summary("Emberfall", sport="soccer") is None
    assert wikipedia.fetched == ["Emberfall"]


async def test_team_summary_none_for_another_sports_team_of_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseball = _page(
        description="Coastal Baseball League franchise in Port Varen",
        extract="The Port Varen Lanterns are a professional baseball team based in Port Varen.",
    )
    wikipedia = _Wikipedia(["Port Varen Lanterns (1924–1941)", TEAM], {TEAM: baseball})
    _serve(monkeypatch, wikipedia)

    assert await wiki.team_summary(TEAM, sport="hockey") is None
    assert wikipedia.fetched == [TEAM]


async def test_team_summary_reads_the_sport_from_the_lead_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short description may name only the league, by its initials."""
    wikipedia = _Wikipedia([TEAM], {TEAM: _page(description="NHL team in Port Varen")})
    _serve(monkeypatch, wikipedia)

    result = await wiki.team_summary(TEAM, sport="hockey")
    assert result is not None and result.title == TEAM


async def test_team_summary_none_for_disambiguation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wikipedia = _Wikipedia(
        [TEAM], {TEAM: _page(type="disambiguation", extract="May refer to a hockey team.")}
    )
    _serve(monkeypatch, wikipedia)
    assert await wiki.team_summary(TEAM, sport="hockey") is None


async def test_team_summary_none_when_the_title_redirects_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary endpoint follows redirects; the page that answers is checked."""
    wikipedia = _Wikipedia([TEAM], {TEAM: _page("Port Varen Mariners")})
    _serve(monkeypatch, wikipedia)
    assert await wiki.team_summary(TEAM, sport="hockey") is None


async def test_team_summary_none_without_a_recognised_sport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No sport, nothing to check an article against — so nothing is asked."""
    wikipedia = _Wikipedia([TEAM], {TEAM: _page()})
    _serve(monkeypatch, wikipedia)

    assert await wiki.team_summary(TEAM) is None
    assert await wiki.team_summary(TEAM, sport="curling") is None
    assert wikipedia.queries == []


def test_every_sport_can_be_checked() -> None:
    """A sport missing here would silently never get a Wikipedia description."""
    assert set(wiki._SPORT_TERMS) == {sport.value for sport in Sport}
    for terms in wiki._SPORT_TERMS.values():
        assert terms.evidence
        assert all(suffix == wiki._fold(suffix) for suffix in terms.suffixes)


# ---------------------------------------------------------------------------
# Cache, failures, switches
# ---------------------------------------------------------------------------


async def test_team_summary_cache_key_carries_the_match_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries resolved under an older rule sit under another key, unread."""
    stored: dict[str, tuple[Any, int]] = {}
    asked: list[str] = []

    async def _get(key: str):
        asked.append(key)
        entry = stored.get(key)
        return entry[0] if entry else None

    async def _set(key: str, value: Any, ttl: int):
        stored[key] = (value, ttl)

    monkeypatch.setattr(wiki.cache, "cache_get_json", _get)
    monkeypatch.setattr(wiki.cache, "cache_set_json", _set)
    wikipedia = _Wikipedia(_load("wiki_search.json"), {TEAM: _page()})
    _serve(monkeypatch, wikipedia)

    key = f"wiki:v{wiki.MATCH_VERSION}:en:port varen lanterns|hockey"
    first = await wiki.team_summary(TEAM, sport="hockey")
    assert asked == [key]
    assert stored[key][1] == get_settings().wiki_cache_minutes * 60

    # The second call is answered from the cache: no further request.
    second = await wiki.team_summary(TEAM, sport="hockey")
    assert second == first
    assert wikipedia.queries == ["Port Varen Lanterns ice hockey team"]
    assert wikipedia.fetched == [TEAM]


async def test_team_summary_does_not_cache_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[str] = []

    async def _set(key: str, *_a, **_k):
        stored.append(key)

    monkeypatch.setattr(wiki.cache, "cache_set_json", _set)
    _serve(monkeypatch, _Wikipedia(["Port Varen Mariners"], {}))

    assert await wiki.team_summary(TEAM, sport="hockey") is None
    assert stored == []


async def test_team_summary_never_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _serve(monkeypatch, handler)
    assert await wiki.team_summary(TEAM, sport="hockey") is None


async def test_team_summary_none_on_an_unexpected_search_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for body in ({"error": {"code": "ratelimited"}}, {"query": []}, {"query": {"search": {}}}):
        _serve(monkeypatch, _Wikipedia(body, {TEAM: _page()}))
        assert await wiki.team_summary(TEAM, sport="hockey") is None


async def test_team_summary_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "wiki_enabled", False)
    assert await wiki.team_summary(TEAM, sport="hockey") is None


async def test_team_summary_empty_name_is_none() -> None:
    assert await wiki.team_summary("   ", sport="hockey") is None
