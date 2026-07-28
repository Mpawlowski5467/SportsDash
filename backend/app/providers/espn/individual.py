"""Individual sports (tennis / MMA): an athlete is a single-member 'team'.

Split out of the original single-file espn.py; see package __init__.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Iterable


from app import timeutil
from app.models.domain import (
    FightMethod,
    FightResult,
    Game,
    GamePhase,
    GameState,
    League,
    Sport,
    Team,
)

from app.providers.espn.common import (
    _coerce_int,
    _find_side,
    _map_phase,
    _normalize_period,
    _parse_espn_datetime,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual sports (tennis / MMA): an athlete is a single-member "team".
#
# These payloads differ from the team-sport scoreboard in three ways the
# generic ``_parse_event`` can't absorb:
#   * the entity lives in ``competitor.athlete`` (not ``competitor.team``),
#     and the id to match a followed athlete against is ``competitor.id``
#     (``competitor.athlete.id`` is null on the scoreboard — verified live);
#   * tennis nests matches as ``events -> groupings -> competitions`` and
#     ships the whole tournament draw in one payload;
#   * UFC bouts carry NO ``homeAway`` and NO per-fight start time — order
#     1 -> home, 2 -> away, and every bout inherits the card's start time.
# ---------------------------------------------------------------------------


def _athlete_name(competitor: dict[str, Any]) -> str | None:
    athlete = competitor.get("athlete")
    if isinstance(athlete, dict):
        for key in ("displayName", "shortName", "fullName"):
            value = athlete.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _athlete_abbreviation(competitor: dict[str, Any]) -> str | None:
    athlete = competitor.get("athlete")
    if isinstance(athlete, dict):
        value = athlete.get("shortName")
        if isinstance(value, str) and value:
            return value
    return None


def _competitor_id(competitor: dict[str, Any]) -> str:
    """The athlete id used to match a followed athlete (``competitor.id``)."""
    return str(competitor.get("id") or "").strip()


def _individual_sides(
    competitors: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(home, away)`` competitor dicts for a 1-v-1 individual event.

    Tennis competitors carry ``homeAway`` so that wins; UFC bouts don't,
    so fall back to ``order`` (1 -> home, 2 -> away, documented).  Either
    way exactly two sides are required.
    """
    sides = [c for c in competitors if isinstance(c, dict)]
    if len(sides) != 2:
        return None
    home = _find_side(sides, "home")
    away = _find_side(sides, "away")
    if home is not None and away is not None:
        return home, away
    # No homeAway (UFC): order 1 -> home, 2 -> away.  ``order`` is an int
    # in live payloads; sort defensively and treat the lower order as home.
    ordered = sorted(sides, key=lambda c: _coerce_int(c.get("order")) or 0)
    return ordered[0], ordered[1]


def _build_individual_state(
    game_id: str, league: League, competition: dict[str, Any]
) -> GameState | None:
    """GameState for a tennis match / MMA bout (athlete competitors).

    Scores are set/round counts: the number of sets/rounds each side has
    won, taken from per-period ``linescores`` (``winner: true`` entries)
    when present, else 0.  ``_build_state`` can't be reused because it
    reads ``competitor.score`` (absent here) and assumes ``homeAway``.
    """
    sides = _individual_sides(competition.get("competitors") or [])
    if sides is None:
        return None
    home, away = sides

    status = competition.get("status")
    if not isinstance(status, dict):
        status = {}
    raw_type = status.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    status_name = str(stype.get("name") or "")
    phase = _map_phase(str(stype.get("state") or ""), status_name)
    raw_period = _coerce_int(status.get("period")) or 0
    display_clock = status.get("displayClock")
    if not (isinstance(display_clock, str) and display_clock):
        display_clock = None
    detail_text = " ".join(
        str(stype.get(key) or "") for key in ("shortDetail", "detail", "altDetail")
    )
    period, label, clock, intermission = _normalize_period(
        league.sport, phase, raw_period, status_name, display_clock, detail_text
    )
    return GameState(
        game_id=game_id,
        phase=phase,
        home_score=_sets_won(home),
        away_score=_sets_won(away),
        period=period,
        period_label=label,
        clock=clock,
        is_intermission=intermission,
        last_update=timeutil.utcnow(),
    )


def _sets_won(competitor: dict[str, Any]) -> int:
    """Count of won periods (sets/rounds) from a competitor's linescores."""
    linescores = competitor.get("linescores")
    if not isinstance(linescores, list):
        return 0
    return sum(1 for entry in linescores if isinstance(entry, dict) and entry.get("winner") is True)


def _round_label(competition: dict[str, Any]) -> str | None:
    """Tennis round name from ``competition.round`` (e.g. "Quarterfinal")."""
    raw_round = competition.get("round")
    if isinstance(raw_round, dict):
        value = raw_round.get("displayName") or raw_round.get("shortDisplayName")
        if isinstance(value, str) and value:
            return value
    return None


def _tennis_series(event: dict[str, Any], competition: dict[str, Any]) -> str | None:
    """``"Wimbledon · QF"``: tournament name + round when both are known."""
    tournament = event.get("name") or event.get("shortName")
    tournament = tournament if isinstance(tournament, str) and tournament else None
    round_name = _round_label(competition)
    if tournament and round_name:
        return f"{tournament} · {round_name}"
    return tournament or round_name


# ---------------------------------------------------------------------------
# MMA method of victory
#
# A finished bout's score is a round count ("1-0"), which for MMA is nearly
# meaningless — HOW it ended is the result.  ESPN spells that as free text
# in two places, both parsed by the same keyword classifier below:
#   * the scoreboard's own ``status.type`` detail — free, already fetched,
#     but usually just "Final";
#   * the core API's per-bout status feed — authoritative (it carries the
#     round and stoppage time), at the cost of ONE call per finished bout,
#     which ``EspnProvider._attach_fight_results`` bounds.
# Neither shape is contractual, so nothing here raises: an unrecognized or
# absent payload degrades to "no method known" and the bout is untouched.
# ---------------------------------------------------------------------------

# Ordered most-specific first: a "Technical Submission" must not read as a
# knockout, and a "Split Draw" is a draw rather than a decision.  Only the
# abbreviations ESPN actually publishes are matched, word-bounded, so "KO"
# can't hide inside "KNOCKED" and a stray "Dec" (a date) can't read as a
# decision.
_FIGHT_METHOD_PATTERNS: tuple[tuple[re.Pattern[str], FightMethod], ...] = (
    (re.compile(r"\bno[\s-]?contest\b", re.IGNORECASE), FightMethod.NO_CONTEST),
    (re.compile(r"\bdisqualifi\w*\b|\bdq\b", re.IGNORECASE), FightMethod.DISQUALIFICATION),
    (re.compile(r"\bsubmission\b|\bsub\b", re.IGNORECASE), FightMethod.SUBMISSION),
    (re.compile(r"\btko\b|\bko\b|\bknock[\s-]?out\b", re.IGNORECASE), FightMethod.KO),
    (re.compile(r"\bdraw\b", re.IGNORECASE), FightMethod.DRAW),
    (re.compile(r"\bdecision\b", re.IGNORECASE), FightMethod.DECISION),
)

# The scoreboard prefixes its status detail with the phase ("Final -
# Decision - Unanimous"); the phase is not part of the method.
_FINAL_PREFIX_RE = re.compile(r"^\s*final\b[\s\-/·:]*", re.IGNORECASE)
# A separator with whitespace on at least one side punctuates two words
# ("Technical - Guillotine"); a bare hyphen inside one belongs to it
# ("Rear-Naked Choke"), so only the former collapses to a space.
_DETAIL_SEPARATOR_RE = re.compile(r"\s+[-/·:]+\s*|\s*[-/·:]+\s+")
_DETAIL_SPACE_RE = re.compile(r"\s{2,}")
# Leading/trailing punctuation left behind once the method is cut out.
_DETAIL_TRIM = " \t-/·:,;()"


def _strip_method_tokens(text: str) -> str:
    """Drop every method spelling from a leftover detail fragment.

    ESPN writes the method more than once ("KO/TKO", "Final - KO/TKO -
    Punches"), so cutting out only the token that matched leaves another
    spelling behind.  Those tokens carry no information the classified
    method doesn't already, but anything around them does — which is why
    only they are removed, not the whole remainder.
    """
    for pattern, _ in _FIGHT_METHOD_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _classify_fight_method(text: str) -> tuple[FightMethod, str | None] | None:
    """Split free-form result text into ``(method, flavour detail)``.

    ``"Submission - Rear-Naked Choke"`` -> ``(SUBMISSION, "Rear-Naked
    Choke")``; ``"KO/TKO - Punches"`` -> ``(KO, "Punches")``, the second
    spelling of the method dropped but the flavour kept; ``"KO/TKO"`` ->
    ``(KO, None)``, because nothing but method spellings is left.
    ``None`` when nothing in the text names a method at all.
    """
    cleaned = _FINAL_PREFIX_RE.sub("", text).strip()
    if not cleaned:
        return None
    for pattern, method in _FIGHT_METHOD_PATTERNS:
        match = pattern.search(cleaned)
        if match is None:
            continue
        remainder = _strip_method_tokens(f"{cleaned[: match.start()]} {cleaned[match.end() :]}")
        remainder = _DETAIL_SEPARATOR_RE.sub(" ", remainder)
        detail: str | None = _DETAIL_SPACE_RE.sub(" ", remainder).strip(_DETAIL_TRIM) or None
        return method, detail
    return None


def _fight_result_from_text(
    text: str,
    *,
    round_number: int | None = None,
    clock: str | None = None,
) -> FightResult | None:
    """A :class:`FightResult` from one piece of result text, or ``None``."""
    classified = _classify_fight_method(text)
    if classified is None:
        return None
    method, detail = classified
    return FightResult(
        method=method,
        detail=detail,
        round=round_number if round_number is not None and round_number > 0 else None,
        clock=clock or None,
    )


def _merge_fight_results(base: FightResult | None, better: FightResult) -> FightResult:
    """Overlay a richer result on a thinner one, field by field.

    ``better`` (the core-API feed) wins on the method; anything it leaves
    unknown falls back to what the scoreboard already told us, so a feed
    that names the method but omits the round doesn't lose the round.
    """
    if base is None:
        return better
    return FightResult(
        method=better.method,
        detail=better.detail if better.detail is not None else base.detail,
        round=better.round if better.round is not None else base.round,
        clock=better.clock if better.clock is not None else base.clock,
    )


def _scoreboard_fight_result(
    competition: dict[str, Any], state: GameState | None
) -> FightResult | None:
    """Method of victory from the scoreboard's own status text, if any.

    Costs nothing (the payload is already in hand) but is usually thin —
    ESPN often writes only "Final" — so this is the fallback the core-API
    enrichment upgrades.  Only finished bouts are considered; a scheduled
    bout's detail is a tee time, not a result.
    """
    if state is None or state.phase is not GamePhase.FINAL:
        return None
    status = competition.get("status")
    stype = status.get("type") if isinstance(status, dict) else None
    if not isinstance(stype, dict):
        return None
    # First field that actually names a method wins; a bare "Final"
    # classifies as nothing and falls through to the next.
    for key in ("detail", "shortDetail", "altDetail", "description"):
        value = stype.get(key)
        if not isinstance(value, str) or not value:
            continue
        result = _fight_result_from_text(value, round_number=state.period)
        if result is not None:
            return result
    return None


def _parse_bout_status(payload: Any) -> FightResult | None:
    """Method of victory from ESPN's core-API per-bout status payload.

    That endpoint is undocumented and its shape is NOT contractual, so
    every lookup is defensive: the result node is searched for under a
    couple of plausible keys (and one level of ``status`` nesting, since
    the same parse serves a bare status document and a competition that
    wraps one), the method is classified from whichever of its text fields
    names one, and the round/time come from the node or the status around
    it.  Returns ``None`` — "no method known" — for anything unrecognized.
    """
    if not isinstance(payload, dict):
        return None
    containers: list[dict[str, Any]] = [payload]
    nested = payload.get("status")
    if isinstance(nested, dict):
        containers.append(nested)
    for container in containers:
        for key in ("result", "outcome"):
            node = container.get(key)
            if not isinstance(node, dict):
                continue
            result = _fight_result_from_node(node, container)
            if result is not None:
                return result
    return None


def _fight_result_from_node(node: dict[str, Any], container: dict[str, Any]) -> FightResult | None:
    """Classify one core-API result node against its enclosing status."""
    round_number = _coerce_int(node.get("round"))
    if round_number is None:
        round_number = _coerce_int(container.get("period"))
    raw_clock = node.get("displayClock") or container.get("displayClock")
    clock = raw_clock if isinstance(raw_clock, str) and raw_clock else None
    # Fields are tried richest-first only in the sense that the first one
    # carrying flavour wins; a bare "TKO" is kept as the fallback so a node
    # that only ever says "TKO" still produces a result.
    fallback: FightResult | None = None
    for key in ("displayName", "description", "name", "shortDisplayName", "text", "abbreviation"):
        value = node.get(key)
        if not isinstance(value, str) or not value:
            continue
        result = _fight_result_from_text(value, round_number=round_number, clock=clock)
        if result is None:
            continue
        if result.detail is not None:
            return result
        fallback = fallback or result
    return fallback


def _https_ref(value: Any) -> str | None:
    """A usable https URL from an ESPN ``$ref`` (they ship as bare http)."""
    if not isinstance(value, str):
        return None
    ref = value.strip()
    if ref.startswith("http://"):
        ref = f"https://{ref[len('http://') :]}"
    return ref if ref.startswith("https://") else None


def _mma_status_urls(data: Any, *, core_league_base: str) -> dict[str, str]:
    """``game id -> core-API bout-status URL`` for every bout in a card payload.

    Prefers the payload's own ``status.$ref`` (rewritten to https — ESPN
    publishes those links as bare http), and otherwise builds the core-API
    path from the card's event id and the bout's competition id.  UFC is
    the one place where the two ids differ, which is why the mapping has to
    be read off the payload rather than derived from the game id alone.
    ``core_league_base`` is passed in because the core API inserts a
    ``leagues`` segment the site API's ``provider_key`` doesn't have.
    """
    urls: dict[str, str] = {}
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return urls
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if not isinstance(competition, dict):
                continue
            competition_id = str(competition.get("id") or "").strip()
            if not competition_id:
                continue
            status = competition.get("status")
            ref = _https_ref(status.get("$ref")) if isinstance(status, dict) else None
            if ref is None and event_id:
                ref = f"{core_league_base}/events/{event_id}/competitions/{competition_id}/status"
            if ref is not None:
                urls[f"espn:{competition_id}"] = ref
    return urls


class _UnresolvedCompetition(Exception):
    """A competition that is not yet a real, resolved 1-v-1 match.

    Tennis tour scoreboards list every bracket slot, including future
    rounds whose competitors are still TBD placeholders (athlete id
    ``-3``/``-4``, no name) and non-singles draws.  These are expected
    empty slots, not malformed data, so they are skipped quietly (no
    warning traceback) rather than logged as parse failures.
    """


def _parse_individual_competition(
    competition: Any,
    event: dict[str, Any],
    league: League,
    *,
    series: str | None,
    fallback_start: datetime | None,
    team: Team | None,
) -> Game | None:
    """Parse one tennis/MMA competition into a :class:`Game`.

    ``fallback_start`` (the card/event date) covers UFC bouts, which carry
    no per-fight start time; tennis competitions carry their own date.
    ``team`` (when given) tags the matching side's internal id.
    """
    try:
        if not isinstance(competition, dict):
            raise ValueError("competition is not an object")
        competition_id = str(competition.get("id") or "").strip()
        if not competition_id:
            raise ValueError("missing competition id")

        start_time = _parse_espn_datetime(competition.get("date")) or fallback_start
        if start_time is None:
            raise ValueError("missing or invalid start time")

        sides = _individual_sides(competition.get("competitors") or [])
        if sides is None:
            raise _UnresolvedCompetition("expected two athlete competitors")
        home, away = sides
        home_name = _athlete_name(home)
        away_name = _athlete_name(away)
        if not home_name or not away_name:
            raise _UnresolvedCompetition("competitors not yet determined")

        game_id = f"espn:{competition_id}"
        state = _build_individual_state(game_id, league, competition)
        # MMA only: a finished bout's round count is not a result, so read
        # the method of victory the scoreboard happens to carry.  Tennis
        # (and any future individual sport) is simply unaffected.
        fight_result = (
            _scoreboard_fight_result(competition, state) if league.sport is Sport.MMA else None
        )

        raw_venue = competition.get("venue")
        venue = raw_venue.get("fullName") if isinstance(raw_venue, dict) else None
        if not (isinstance(venue, str) and venue):
            venue = None

        home_team_id: str | None = None
        away_team_id: str | None = None
        if team is not None:
            key = str(team.provider_key)
            if _competitor_id(home) == key:
                home_team_id = team.id
            elif _competitor_id(away) == key:
                away_team_id = team.id

        return Game(
            id=game_id,
            league_id=league.id,
            home_name=home_name,
            away_name=away_name,
            start_time=start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_abbreviation=_athlete_abbreviation(home),
            away_abbreviation=_athlete_abbreviation(away),
            venue=venue,
            series=series,
            fight_result=fight_result,
            state=state,
        )
    except _UnresolvedCompetition as exc:
        # Expected empty bracket slot (TBD competitors / non-singles draw),
        # not malformed data: skip without a noisy warning traceback.
        logger.debug(
            "Skipping unresolved ESPN %s competition for league %s: %s",
            league.sport.value,
            league.id,
            exc,
        )
        return None
    except Exception:
        logger.warning(
            "Skipping malformed ESPN %s competition for league %s",
            league.sport.value,
            league.id,
            exc_info=True,
        )
        return None


def _iter_tennis_competitions(
    event: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], str | None]]:
    """Yield ``(competition, series)`` from a tennis event's groupings.

    Tennis nests matches as ``event -> groupings[] -> competitions[]``;
    each grouping is a draw (Men's/Women's Singles, Doubles, ...).
    """
    groupings = event.get("groupings")
    if not isinstance(groupings, list):
        return
    for grouping in groupings:
        if not isinstance(grouping, dict):
            continue
        competitions = grouping.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if isinstance(competition, dict):
                yield competition, _tennis_series(event, competition)


def _parse_tennis_events(
    data: Any,
    league: League,
    team: Team | None = None,
) -> list[Game]:
    """Parse a tennis scoreboard (whole-draw payload) into Games."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    games: list[Game] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for competition, series in _iter_tennis_competitions(event):
            game = _parse_individual_competition(
                competition,
                event,
                league,
                series=series,
                fallback_start=None,
                team=team,
            )
            if game is not None:
                games.append(game)
    return games


def _parse_mma_events(
    data: Any,
    league: League,
    team: Team | None = None,
) -> list[Game]:
    """Parse a UFC scoreboard into Games (one per bout).

    Each event is a fight card with ``competitions[]`` bouts; bouts have
    no per-fight start time, so the card date is used for every bout and
    the card name becomes the series label ("UFC 320").
    """
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    games: list[Game] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        card_name = event.get("name") or event.get("shortName")
        series = card_name if isinstance(card_name, str) and card_name else None
        card_start = _parse_espn_datetime(event.get("date"))
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            game = _parse_individual_competition(
                competition,
                event,
                league,
                series=series,
                fallback_start=card_start,
                team=team,
            )
            if game is not None:
                games.append(game)
    return games


def _parse_individual_scoreboard(data: Any, league: League, team: Team | None = None) -> list[Game]:
    """Dispatch a tennis/MMA scoreboard payload to its sport parser."""
    if league.sport is Sport.TENNIS:
        return _parse_tennis_events(data, league, team)
    return _parse_mma_events(data, league, team)


def _games_for_athlete(games: Iterable[Game], team: Team) -> list[Game]:
    """Keep only the games where ``team`` (an athlete) is a competitor."""
    return [game for game in games if game.home_team_id == team.id or game.away_team_id == team.id]
