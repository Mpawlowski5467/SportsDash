"""Racing: leaderboard Events (one race weekend, a field on a board).

Motorsport's sibling of ``golf.py`` — the second leaderboard sport to
reuse the Event pipeline end-to-end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Sequence


from app import timeutil
from app.models.domain import (
    Event,
    GamePhase,
    LeaderRow,
    League,
)

from app.providers.espn.common import (
    _CORE_BASE,
    _coerce_float,
    _coerce_int,
    _map_phase,
    _parse_espn_datetime,
)
from app.providers.espn.summary import _core_event_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Racing: a leaderboard Event (one race weekend, a field on a board) — NOT a
# two-sided Game.
#
# Verified live against the real F1 / NASCAR Cup / IndyCar feeds (July 2026):
#   * one ``events[]`` entry per race WEEKEND.  F1 spans the event across
#     ``date``/``endDate`` (Fri–Sun) with one ``competitions[]`` entry per
#     SESSION, keyed by ``type.abbreviation`` (FP1/FP2/FP3/Qual/Race; sprint
#     weekends swap in SS/SR).  NASCAR/IndyCar events are single-day: ONE
#     competition whose ``type`` is null and no event ``endDate``;
#   * competitors are THIN: ``{id, order, winner, athlete, ...}`` — the
#     competitor-level ``id`` IS the ESPN athlete id (the nested ``athlete``
#     object has NO id, only names + flag), ``order`` is the pre-sorted
#     finishing/classification position, ``winner: true`` marks P1 only.
#     No score, no laps, no gaps on the site scoreboard — those live on the
#     core API, which the enrichment further down reads to fill in
#     ``LeaderRow.score``/``detail`` (the board parses fine without it);
#   * a scheduled race often ships NO competitors at all (entry lists are
#     published near/after the race), so a pre-race Event may carry an
#     empty leaderboard;
#   * on a live Race session ``status.period`` counts laps run;
#   * every competitor carries a ``statistics`` slot, and it is EMPTY (``[]``)
#     on all 1606 competitors scanned across F1 / stock-car / IndyCar — but
#     every one of those sessions was FINAL or scheduled.  Whether the slot
#     populates MID-RACE is unverified (F1's summer break plus a stock-car
#     off-weekend meant no session anywhere was in progress to look at), so
#     :func:`_parse_racing_site_stats` reads it strictly additively: a
#     running gap when it is there, today's empty score column when it isn't.
#
# IMPORTANT — followed-driver tagging: same contract as golf.  The provider
# carries the ESPN athlete id transiently in ``LeaderRow.player_id``; the
# SCHEDULER rewrites that field after fetch, replacing each ESPN id with the
# internal followed-team id (or None) before the Event is persisted.
# ---------------------------------------------------------------------------

# The race session's ``type.abbreviation`` on multi-session (F1) weekends.
_RACE_ABBREVIATION = "Race"

# ``EventORM.round_label`` is String(48); keep parser output bounded.
_ROUND_LABEL_MAX = 48

# The lap-progress clause of a live Race label.  Shared by the builder and
# by :func:`_racing_lap_label`, which appends the race distance once the
# core API has told us what it is.
_LAP_MARKER = " · Lap "


def _sessions(event: dict[str, Any]) -> list[dict[str, Any]]:
    """The event's ``competitions[]`` (sessions), non-dicts dropped."""
    competitions = event.get("competitions")
    if not isinstance(competitions, list):
        return []
    return [c for c in competitions if isinstance(c, dict)]


def _session_abbreviation(session: dict[str, Any]) -> str:
    """``type.abbreviation`` ("FP1", "Qual", "Race"), "" when typeless."""
    raw_type = session.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    abbreviation = stype.get("abbreviation")
    return abbreviation.strip() if isinstance(abbreviation, str) else ""


def _session_status(session: dict[str, Any]) -> dict[str, Any]:
    status = session.get("status")
    return status if isinstance(status, dict) else {}


def _session_state(session: dict[str, Any]) -> str:
    """The session's ``status.type.state`` ("pre" | "in" | "post"), or ""."""
    raw_type = _session_status(session).get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    return str(stype.get("state") or "")


def _race_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The weekend's Race session, or the sole typeless competition.

    F1 keys its sessions by ``type.abbreviation``; NASCAR/IndyCar ship one
    competition whose ``type`` is null — that lone competition IS the race.
    """
    for session in sessions:
        if _session_abbreviation(session) == _RACE_ABBREVIATION:
            return session
    if len(sessions) == 1 and not _session_abbreviation(sessions[0]):
        return sessions[0]
    return None


def _competitor_items(session: dict[str, Any]) -> list[dict[str, Any]]:
    """The session's ``competitors[]`` entries, non-dicts dropped."""
    raw = session.get("competitors")
    if not isinstance(raw, list):
        return []
    return [competitor for competitor in raw if isinstance(competitor, dict)]


def _board_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The session whose field IS the board, or None when nothing has one.

    The Race session's competitors are the finishing (or live running)
    order; before the race has a field, the latest session that DOES have
    competitors stands in (qualifying/practice classification), so a
    followed driver is visible on the board all weekend.  The core-API
    enrichment applies the same rule to the event root, so a Saturday board
    is enriched with Saturday's cars.
    """
    race = _race_session(sessions)
    if race is not None and _competitor_items(race):
        return race
    for session in reversed(sessions):
        if session is race:
            continue
        if _competitor_items(session):
            return session
    return None


def _racing_round_label(sessions: list[dict[str, Any]], phase: GamePhase) -> str:
    """The live session's abbreviation ("FP2", "Qual", "Race · Lap 44").

    Only an in-progress weekend shows a label; the first session in state
    "in" names it.  The Race session (or a typeless live competition,
    which is the race) appends lap progress from ``status.period`` when
    the feed carries it.  Scheduled and finished weekends show none —
    phase alone renders "Upcoming"/"FINAL" downstream.
    """
    if phase is not GamePhase.IN_PROGRESS:
        return ""
    for session in sessions:
        if _session_state(session) != "in":
            continue
        label = _session_abbreviation(session) or _RACE_ABBREVIATION
        if label == _RACE_ABBREVIATION:
            laps = _coerce_int(_session_status(session).get("period")) or 0
            if laps > 0:
                label = f"{label}{_LAP_MARKER}{laps}"
        return label[:_ROUND_LABEL_MAX]
    return ""


def _racing_lap_label(label: str, total_laps: int | None) -> str:
    """Append the race distance to a live lap label ("Lap 44" -> "Lap 44 of 70").

    The distance is a core-API fact (the site scoreboard only counts laps
    RUN), so it arrives after the label is built.  Anything else — a
    practice/qualifying label, a finished weekend's empty label, an unknown
    distance — is returned untouched.
    """
    if not label or not total_laps or _LAP_MARKER not in label:
        return label
    return f"{label} of {total_laps}"[:_ROUND_LABEL_MAX]


def _racing_leader_row(
    competitor: dict[str, Any], position: int, *, live: bool = False
) -> LeaderRow | None:
    """One :class:`LeaderRow` from a racing competitor; None if malformed.

    ``position`` is the caller-resolved board position (``order``, or the
    sequential fallback when the feed omits it).  Racing positions are
    unique — no tie labels — so the label is just the bare number.
    ``detail`` only flags the winner.

    ``score`` stays empty — the site scoreboard publishes no per-driver time
    on any session that has ever been observed — EXCEPT on a ``live`` board,
    where the competitor's ``statistics`` slot is read for a running gap at
    no extra call (see :func:`_parse_racing_site_stats`).

    ``player_id`` carries the ESPN athlete id transiently (the scheduler
    rewrites it to the internal followed-team id or None).
    """
    try:
        # The athlete id lives on the competitor itself (the nested athlete
        # object has no id on the racing scoreboard — verified live).
        athlete_id = str(competitor.get("id") or "").strip()
        raw_athlete = competitor.get("athlete")
        athlete: dict[str, Any] = raw_athlete if isinstance(raw_athlete, dict) else {}
        name = athlete.get("displayName") or athlete.get("fullName")
        if not (isinstance(name, str) and name):
            raise ValueError("missing driver name")
        return LeaderRow(
            position=position,
            position_label=str(position),
            name=name,
            score=_racing_live_row_score(competitor) if live else "",
            detail="Winner" if competitor.get("winner") is True else "",
            player_id=athlete_id or None,
        )
    except Exception:
        logger.warning("Skipping malformed ESPN racing competitor", exc_info=True)
        return None


def _racing_leaderboard(sessions: list[dict[str, Any]]) -> tuple[LeaderRow, ...]:
    """The board: race order first, else the latest session with entrants.

    See :func:`_board_session` for which session's field wins.  A scheduled
    event with no competitors anywhere yields an empty board.
    """
    session = _board_session(sessions)
    if session is None:
        return ()

    # A LIVE session is the only one whose per-driver ``statistics`` slot is
    # read: mid-race that slot is the one free running-gap source that exists
    # (see :func:`_parse_racing_site_stats`), while a finished board's score
    # column belongs to the core-API enrichment further down.
    live = _session_state(session) == "in"
    valid = _competitor_items(session)
    # A missing/zero/junk ``order`` sorts to the END, never the front —
    # otherwise the sequential fallback below would crown a malformed
    # entrant P1 (and the scheduler's best-placed-followed-row rule could
    # headline it as the winner).  An all-omitted field still comes out
    # 1..N in feed order: the sort is stable and every key ties.
    valid.sort(key=lambda c: _coerce_int(c.get("order")) or float("inf"))
    rows: list[LeaderRow] = []
    for competitor in valid:
        position = _coerce_int(competitor.get("order")) or (len(rows) + 1)
        row = _racing_leader_row(competitor, position, live=live)
        if row is not None:
            rows.append(row)
    return tuple(rows)


def _parse_racing_event(event: Any, league: League) -> Event | None:
    """Parse one race-weekend event into an :class:`Event`; None if bad."""
    try:
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise ValueError("missing event id")
        name = event.get("name") or event.get("shortName")
        if not (isinstance(name, str) and name):
            raise ValueError("missing event name")

        sessions = _sessions(event)

        start_time = _parse_espn_datetime(event.get("date"))
        if start_time is None:
            raise ValueError("missing or invalid start time")
        end_time = _parse_espn_datetime(event.get("endDate"))
        if end_time is None:
            # NASCAR/IndyCar events carry no endDate; the latest session
            # start (the Sunday race on an F1-style weekend) still bounds
            # the span.  A single-session event just ends the day it starts.
            session_dates = [
                parsed
                for session in sessions
                if (parsed := _parse_espn_datetime(session.get("date"))) is not None
            ]
            end_time = max(session_dates) if session_dates else None

        raw_status = event.get("status")
        status: dict[str, Any] = raw_status if isinstance(raw_status, dict) else {}
        raw_type = status.get("type")
        stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
        phase = _map_phase(str(stype.get("state") or ""), str(stype.get("name") or ""))

        # F1 carries the circuit on the event; NASCAR/IndyCar have no
        # circuit key at all, so the venue just stays None there.
        raw_circuit = event.get("circuit")
        circuit: dict[str, Any] = raw_circuit if isinstance(raw_circuit, dict) else {}
        venue = circuit.get("fullName")
        if not (isinstance(venue, str) and venue):
            venue = None

        return Event(
            id=f"espn:{event_id}",
            league_id=league.id,
            name=name,
            start_time=start_time,
            phase=phase,
            end_time=end_time,
            round_label=_racing_round_label(sessions, phase),
            venue=venue,
            leaderboard=_racing_leaderboard(sessions),
            last_update=timeutil.utcnow(),
        )
    except Exception:
        logger.warning(
            "Skipping malformed ESPN racing event for league %s", league.id, exc_info=True
        )
        return None


def _parse_racing_scoreboard(data: Any, league: League) -> list[Event]:
    """Parse a racing scoreboard payload into a list of race-weekend Events."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    return [event for raw in events if (event := _parse_racing_event(raw, league)) is not None]


# ---------------------------------------------------------------------------
# Core-API enrichment: constructor / car number / grid / race time / gap
#
# The site scoreboard's competitors are thin, so the two display columns are
# filled from ESPN's core API in two tiers — verified live against the real
# F1 / stock-car / IndyCar core feeds (July 2026):
#
#   TIER A, every poll, ONE call per active race: the EVENT ROOT
#   ``…/events/{id}`` inlines EVERY session's full competitor array with
#   ``vehicle`` (constructor + car number) and ``startOrder`` (grid).  It
#   replaces one call per session, and sidesteps the 25-item paging the
#   ``…/competitions/{id}/competitors`` collection imposes on a 39-car
#   stock-car field.  One further call, once per event and then memoized,
#   reads the race distance for the "Lap 44 of 70" label.
#
#   TIER B, once when the race is FINAL: one
#   ``…/competitors/{id}/statistics`` per car for the race time / gap / laps
#   behind, plus ``…/competitors/{id}/status`` for classified-vs-retired.
#   ESPN offers no bulk expansion of those refs (every ``enable=``/
#   ``expand=`` variant returns the byte-identical payload), so this is a
#   real N+1: it is bounded (``_ENRICH_MAX_CARS`` / ``_ENRICH_CONCURRENCY``)
#   and the rendered result is memoized per event, so a re-poll of a FINAL
#   race never re-spends it.
#
# There is deliberately NO live equivalent of tier B.  A mid-race gap has
# exactly one route on this API — the same per-car statistics ref, at 22
# calls per poll on a Grand Prix and 39 on a stock-car race — and it is not
# established that anything in it even moves during a race: the competition
# aggregate publishes ``avgSpeed``/``victoryMargin``/``poleSpeed``/
# ``poleTime`` as ``.000`` on a COMPLETED Grand Prix, ``totalTime`` and
# ``behindTime`` are finishing values, and the ``gapToLeader`` category that
# stock-car/IndyCar payloads carry has an empty ``stats`` array on every car
# ever sampled.  Spending a per-poll N+1 to find out is not a trade worth
# making blind, so the only live gap read here is the FREE one: the site
# scoreboard's own per-competitor ``statistics`` slot, already in the payload
# the board is parsed from (:func:`_parse_racing_site_stats`, zero extra
# calls per poll).
#
# Shape traps the parsers below encode:
#   * the CONSTRUCTOR is ``vehicle.manufacturer`` on F1 — proven equal to
#     the athlete record's ``vehicles[0].team`` for every driver checked,
#     and the field resolves to 11 constructors x 2 cars sharing a
#     ``teamColor``.  Stock-car/IndyCar payloads add ``vehicle.team`` ("Joe
#     Gibbs Racing") and demote ``manufacturer`` to the OEM / engine
#     supplier, so ``team`` wins when present.  The ATHLETE record's
#     ``vehicles[]`` is never consulted: there ``manufacturer`` is the
#     engine, and on stock cars it is outright stale (a driver listed with
#     one make and number while racing another);
#   * ``behindLaps`` is POSITIVE on F1 and NEGATIVE on stock-car/IndyCar —
#     always ``abs()`` it;
#   * stock-car/IndyCar expose NO per-car race time (no ``totalTime``,
#     ``behindTime`` pinned at ``.000``) and no per-competitor ``status``
#     resource at all (it answers 400 —
#     "getCompetitorStatus() not supported"), so those boards degrade to laps
#     completed / laps down, and nothing about a car's outcome is knowable
#     there;
#   * THREE per-competitor status types exist, not two: ``STATUS_CLASSIFIED``
#     (id 12), ``STATUS_RETIRED`` (id 8) and ``STATUS_DISQUALIFIED`` (id 14,
#     ``displayValue`` "DQ") — 529 / 76 / 6 of the 611 F1 cars sampled.  A
#     lapped car is CLASSIFIED with ``behindLaps >= 1``; a non-starter is
#     simply ABSENT from the field, so there is no DNS status to read;
#   * a car's ``status`` is a bare ``$ref`` on the event root — never inlined,
#     under any ``enable=``/``expand=``/``view=`` variant — so there is no
#     bulk source for it either;
#   * a canceled or not-yet-started race has no field: the competitors
#     resources 404.  That is "no entry list yet", never an outage.
# ---------------------------------------------------------------------------

# Enrichment bounds.  A FINAL race costs one core call per car, and a
# stock-car field is 40 cars — precisely the unbounded per-event fan-out
# that rate-limits ESPN into an open circuit breaker (the same lesson as the
# MMA method-of-victory backfill), so it is capped three ways: at most this
# many events per fetch, this many cars per event (the front of the field
# first), and this many calls in flight.
_ENRICH_MAX_EVENTS = 2
_ENRICH_MAX_CARS = 40
_ENRICH_CONCURRENCY = 8

# Sort key for a car whose ``order`` the feed omitted: behind every ranked
# one, so the cap above never spends a call on it while a classified car
# goes without.
_ORDER_LAST = 10_000

# How many events' rendered facts / race distances a provider remembers.
_ENRICH_MEMO_MAX = 64

# ``LeaderRow.score``/``detail`` are free display strings the UI renders
# verbatim; keep the persisted leaderboard JSON bounded anyway.
_SCORE_MAX = 24
_DETAIL_MAX = 80

# A ``$ref`` is a URL that arrives inside a remote payload — only follow it
# when it addresses the core API we asked in the first place.
_CORE_REF_PREFIX = f"{_CORE_BASE}/"

# How a car's race ended.  The first three are ESPN's per-competitor status
# types; the last two are ours, and the distinction between them is the whole
# point:
#   * ``_OUTCOME_NONE`` — nothing is known and nothing is claimed (a series
#     with no status resource, a car past the fan-out cap).  Renders exactly
#     as it did before any of this existed.
#   * ``_OUTCOME_AMBIGUOUS`` — the status is missing AND the statistics
#     provably cannot separate a retirement from a lapped finish, so the row
#     says so out loud instead of implying the car finished.
_OUTCOME_CLASSIFIED = "classified"
_OUTCOME_RETIRED = "retired"
_OUTCOME_DISQUALIFIED = "disqualified"
_OUTCOME_AMBIGUOUS = "ambiguous"
_OUTCOME_NONE = "none"

# ESPN's ``type.name`` -> outcome.  An exact lookup, NOT a substring test:
# ``"RETIRED" in name`` is precisely what let ``STATUS_DISQUALIFIED`` through
# as a finisher (six real cases in the sample — three of them showing the
# full race distance, one showing a 53-second finishing gap for a car that
# completed zero laps).  Anything unrecognized is _OUTCOME_NONE, never
# "classified": guessing "finished" is the one error mode that misinforms.
_STATUS_OUTCOMES = {
    "STATUS_CLASSIFIED": _OUTCOME_CLASSIFIED,  # id 12, displayValue "Classified"
    "STATUS_RETIRED": _OUTCOME_RETIRED,  # id 8,  displayValue "Retired"
    "STATUS_DISQUALIFIED": _OUTCOME_DISQUALIFIED,  # id 14, displayValue "DQ"
}

# The laps-behind threshold above which a car whose status we could not read
# is called a retirement.  See :func:`_racing_inferred_outcome` for the
# measured precision/recall and for why this must stay F1-only.
_DNF_LAPS_BEHIND = 4

# Appended as the LAST detail part when a car's outcome is unknowable (see
# _OUTCOME_AMBIGUOUS).  The score column keeps its arithmetically-true lap
# deficit — a question mark in a number column reads as a data glitch — and
# this is what stops that number implying the car was still running.
_UNKNOWN_STATUS_PART = "Status unknown"


@dataclass(frozen=True)
class _RacingCar:
    """Tier-A facts for one car, free from the core event root."""

    team: str | None  # constructor / entrant, per _racing_team's rule
    number: str | None  # car number
    grid: int | None  # ``startOrder`` — the grid slot, NOT the finish place
    order: int | None  # finishing / running position, for fan-out ordering
    winner: bool
    statistics_url: str | None  # tier-B ref; None when the feed omits it
    status_url: str | None  # F1 only: stock-car/IndyCar have no status


@dataclass(frozen=True)
class _RacingCarStats:
    """The tier-B per-car statistics that reach a display column."""

    laps_completed: int | None
    laps_behind: int | None  # always positive here (see the sign trap above)
    total_time: str | None  # lead-lap finishers only
    behind_time: str | None  # every lead-lap finisher EXCEPT the leader
    fastest_lap: str | None  # exactly one car in the field
    # Whether ``pitsTaken`` was PUBLISHED at all (its value can legitimately
    # be 0).  Never displayed — it is a retirement signal: present on 529 of
    # 529 classified cars, absent on 26 of 76 retired ones.
    pits_reported: bool = False


@dataclass(frozen=True)
class _RacingCarFacts:
    """One car's rendered ``LeaderRow.score`` / ``detail`` strings."""

    score: str
    detail: str


def _clean_str(value: Any) -> str | None:
    """A non-blank stripped string, else None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _positive_int(value: Any) -> int | None:
    """A strictly positive int from an ESPN scalar/stat wrapper, else None."""
    number = _coerce_int(value)
    return number if number is not None and number > 0 else None


def _racing_ref(node: Any) -> str | None:
    """The ``$ref`` on ``node``, when it addresses the core API; else None."""
    if not isinstance(node, dict):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_CORE_REF_PREFIX):
        return ref
    return None


def _racing_team(vehicle: dict[str, Any]) -> str | None:
    """The entrant to display: ``vehicle.team``, else ``vehicle.manufacturer``.

    F1 payloads carry no ``team`` and put the CONSTRUCTOR in
    ``manufacturer``; stock-car/IndyCar payloads name the race team in
    ``team`` (null for a fraction of the field) and use ``manufacturer``
    for the OEM / engine supplier, which is the right fallback there.
    """
    return _clean_str(vehicle.get("team")) or _clean_str(vehicle.get("manufacturer"))


def _racing_core_event_url(provider_key: str, event_key: str) -> str:
    """The core-API event root for one race weekend (tier A)."""
    return f"{_CORE_BASE}/{_core_event_path(provider_key)}/events/{event_key}"


def _racing_competition_statistics_url(
    provider_key: str, event_key: str, competition_id: str
) -> str:
    """The core-API competition-level statistics aggregate (race distance)."""
    return (
        f"{_CORE_BASE}/{_core_event_path(provider_key)}/events/{event_key}"
        f"/competitions/{competition_id}/statistics"
    )


def _racing_field(event_root: Any) -> tuple[str | None, dict[str, _RacingCar]]:
    """``(board competition id, tier-A car facts by ESPN athlete id)``.

    The board session is picked with the same rule as the site board (see
    :func:`_board_session`).  A payload with no field at all — a canceled or
    not-yet-run weekend — yields ``(None, {})`` and no enrichment.
    """
    if not isinstance(event_root, dict):
        return None, {}
    session = _board_session(_sessions(event_root))
    if session is None:
        return None, {}
    cars: dict[str, _RacingCar] = {}
    for competitor in _competitor_items(session):
        # The competitor-level id IS the ESPN athlete id, exactly as on the
        # site scoreboard — that is what joins these facts to a LeaderRow.
        athlete_id = str(competitor.get("id") or "").strip()
        if not athlete_id:
            continue
        raw_vehicle = competitor.get("vehicle")
        vehicle: dict[str, Any] = raw_vehicle if isinstance(raw_vehicle, dict) else {}
        cars[athlete_id] = _RacingCar(
            team=_racing_team(vehicle),
            number=_clean_str(vehicle.get("number")),
            grid=_positive_int(competitor.get("startOrder")),
            order=_positive_int(competitor.get("order")),
            winner=competitor.get("winner") is True,
            statistics_url=_racing_ref(competitor.get("statistics")),
            status_url=_racing_ref(competitor.get("status")),
        )
    return str(session.get("id") or "").strip() or None, cars


def _racing_stat_map(payload: Any) -> dict[str, dict[str, Any]]:
    """``{stat name: stat object}`` from a core racing statistics payload.

    Per-car statistics nest under ``splits.categories[].stats[]`` while the
    competition-level aggregate puts ``categories[]`` at the top level; both
    flatten the same way.  Everything read here lives in the "general"
    category — a stock-car per-car payload adds an always-empty
    "gapToLeader" category, which flattening simply skips.
    """
    if not isinstance(payload, dict):
        return {}
    splits = payload.get("splits")
    categories = splits.get("categories") if isinstance(splits, dict) else payload.get("categories")
    if not isinstance(categories, list):
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for category in categories:
        if not isinstance(category, dict):
            continue
        entries = category.get("stats")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name and name not in stats:
                stats[name] = entry
    return stats


def _racing_display(stat: dict[str, Any] | None) -> str | None:
    """A stat's ``displayValue``, dropped when its numeric value is zero.

    Zeroed placeholders are everywhere in these payloads — a stock-car
    ``behindTime`` pinned at ``.000``, a zeroed ``fastestLap`` for the whole
    field, the qualifying splits on a Race competition — and none of them
    may be rendered as a real time.
    """
    if stat is None:
        return None
    if _coerce_float(stat.get("value")) == 0.0:
        return None
    return _clean_str(stat.get("displayValue"))


def _racing_stats_from_map(stats: dict[str, dict[str, Any]]) -> _RacingCarStats | None:
    """Per-car statistics from a flattened stat map; None when it is empty."""
    if not stats:
        return None
    laps_behind = _coerce_int(stats.get("behindLaps", {}).get("value"))
    return _RacingCarStats(
        laps_completed=_positive_int(stats.get("lapsCompleted", {}).get("value")),
        # F1 reports laps behind positive, stock-car/IndyCar negative.
        laps_behind=abs(laps_behind) if laps_behind is not None else None,
        total_time=_racing_display(stats.get("totalTime")),
        behind_time=_racing_display(stats.get("behindTime")),
        fastest_lap=_racing_display(stats.get("fastestLap")),
        pits_reported="pitsTaken" in stats,
    )


def _parse_racing_stats(payload: Any) -> _RacingCarStats | None:
    """Tier-B per-car statistics; None when the payload carries none."""
    return _racing_stats_from_map(_racing_stat_map(payload))


def _racing_site_stat_map(node: Any) -> dict[str, dict[str, Any]]:
    """``{stat name: stat object}`` from a SITE-scoreboard ``statistics`` slot.

    UNVERIFIED SHAPE, and deliberately liberal about it.  The slot exists on
    every racing competitor the site scoreboard ships and is an empty LIST on
    every session ever observed — all of them finished or scheduled, because
    no racing session anywhere was in progress during the probe.  So the
    shape read here is the one the site API uses wherever it does populate a
    ``statistics`` array: a flat list of ``{name, value, displayValue}``
    objects.  A dict is handed to the core-API flattener instead, in case a
    live payload nests them the way the core API does.  Anything else — a
    string, a number, a list of junk — yields nothing, which is how the board
    goes on rendering exactly as it does today.
    """
    if isinstance(node, dict):
        return _racing_stat_map(node)
    if not isinstance(node, list):
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for entry in node:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("abbreviation")
        if isinstance(name, str) and name and name not in stats:
            stats[name] = entry
    return stats


def _parse_racing_site_stats(node: Any) -> _RacingCarStats | None:
    """A live board's per-driver statistics, or None when there are none.

    Costs ZERO extra calls: this reads the ``statistics`` slot inside the
    site-scoreboard payload the board itself is parsed from.  That is the
    only running-gap source on this API that is not a per-car N+1 — and it
    has never been seen populated, so everything downstream of it treats
    "absent" as the normal case.
    """
    return _racing_stats_from_map(_racing_site_stat_map(node))


def _racing_live_row_score(competitor: dict[str, Any]) -> str:
    """One live competitor's score column; "" on anything unexpected.

    Isolated from the row parse on purpose.  The populated shape of the site
    ``statistics`` slot has never been observed, so a surprise in there must
    cost at most this one column — never the driver's row, and never the
    board.
    """
    try:
        return _racing_live_score(_parse_racing_site_stats(competitor.get("statistics")))
    except Exception:
        logger.warning("Ignoring unparseable ESPN racing live statistics", exc_info=True)
        return ""


def _racing_live_score(stats: _RacingCarStats | None) -> str:
    """The ``score`` column for a car on a LIVE board: the running gap, or "".

    Only the two genuinely running numbers are rendered — the interval to the
    leader and the lap deficit.  ``totalTime`` is a FINISHING time and
    ``lapsCompleted`` merely restates the lap counter already in the round
    label ("Lap 44 of 70"), so neither may stand in for a gap here: on a live
    board a stale or borrowed time is worse than an empty column.
    """
    if stats is None:
        return ""
    if stats.behind_time:
        return _signed(stats.behind_time)[:_SCORE_MAX]
    if stats.laps_behind:
        return _laps_behind(stats.laps_behind)
    return ""


def _parse_racing_car_status(payload: Any) -> tuple[str, int | None]:
    """``(outcome, laps completed)`` from a per-competitor status payload.

    The ``type.name`` is the only signal trusted: inferring retirement from
    ``completed: false`` would brand every car on a live board a DNF, and
    ``completed`` is false for a disqualification too.  An unrecognized name
    yields ``_OUTCOME_NONE`` — an honest "we did not read this" that lets the
    statistics fallback have its say — never a silent "classified".
    ``period`` is that car's laps completed.
    """
    if not isinstance(payload, dict):
        return _OUTCOME_NONE, None
    raw_type = payload.get("type")
    stype: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    name = str(stype.get("name") or "").strip().upper()
    return _STATUS_OUTCOMES.get(name, _OUTCOME_NONE), _positive_int(payload.get("period"))


def _racing_inferred_outcome(stats: _RacingCarStats) -> str:
    """A car's outcome guessed from its statistics alone — F1 only.

    The FALLBACK for when the per-competitor status resource was never
    fetched or its call failed; the status stays authoritative whenever it
    answers.  Callers must gate this on the car HAVING a status ref, which
    only F1 competitors do: the threshold below is series-specific, and at a
    0.4-mile short track a stock car 8 laps down is still circulating (0.982
    of race distance), so the same rule fires on 5-7 CLASSIFIED cars per
    stock-car race.

    Measured over 611 F1 cars from 30 races (529 classified / 76 retired /
    6 disqualified), each clause with ZERO false positives on the 529:
      * ``abs(behindLaps) >= 4``      precision 1.000, recall 0.974 (74/76);
      * ``lapsCompleted`` absent or 0 17 of 17 such cars were retired;
      * ``pitsTaken`` absent          26 of 26 (and 3 of the 6 DSQs);
      * a NONZERO ``totalTime`` implies a finisher, 398 of 398.
    Bottom-six-of-the-field accuracy: 178 of 180.

    What is left over is genuinely undecidable, not merely unmeasured: at the
    2025 Qatar GP two cars RETIRED on lap 55 of 57 sit directly beneath
    CLASSIFIED cars on lap 56, with the same ``behindLaps`` of 2 and the same
    pit count.  Those cars come back ``_OUTCOME_AMBIGUOUS`` (4.5 cars per
    race on average) rather than being called either way.
    """
    if stats.laps_completed is None:
        return _OUTCOME_RETIRED
    if not stats.pits_reported:
        return _OUTCOME_RETIRED
    if stats.laps_behind is not None and stats.laps_behind >= _DNF_LAPS_BEHIND:
        return _OUTCOME_RETIRED
    if stats.total_time:
        return _OUTCOME_CLASSIFIED
    return _OUTCOME_AMBIGUOUS


def _racing_render_outcome(
    car: _RacingCar, stats: _RacingCarStats | None, status: tuple[str, int | None]
) -> tuple[str, int | None]:
    """``(outcome, retirement lap)`` to render for one car.

    ESPN's status wins whenever it said anything.  Failing that, an F1 car
    with statistics in hand gets the inference above; a car with neither —
    a stock-car/IndyCar entrant (no status resource exists) or one past the
    tier-B fan-out cap — gets ``_OUTCOME_NONE`` and renders exactly as it did
    before any of this: whatever the laps/times alone support.
    """
    outcome, lap = status
    if outcome != _OUTCOME_NONE:
        return outcome, lap
    if car.status_url is None or stats is None:
        return _OUTCOME_NONE, None
    return _racing_inferred_outcome(stats), None


def _signed(text: str) -> str:
    """ESPN already prefixes a gap with "+"; add one when it doesn't."""
    return text if text[0] in "+-" else f"+{text}"


def _laps_behind(laps: int) -> str:
    """A lap deficit as the score column spells it ("+1 lap", "+2 laps")."""
    return f"+{laps} lap" + ("" if laps == 1 else "s")


def _racing_score(stats: _RacingCarStats | None, *, outcome: str) -> str:
    """The ``score`` column: "DNF"/"DSQ", gap, laps down, race time, laps run.

    Order matters.  A retired or disqualified car must never borrow a time it
    did not set — a disqualified car in particular can carry the full race
    distance AND a finishing gap (six real cases, one of them showing
    "+53.472" for a car that completed zero laps) — hence the early exits.
    Then: a lead-lap finisher carries BOTH ``totalTime`` and ``behindTime``
    and the gap is the useful number, so only the leader — which has no
    ``behindTime`` — falls through to the total time.  A lapped car has
    neither, just ``behindLaps``.  The final ``lapsCompleted`` step is what
    stock-car/IndyCar boards land on: those series publish no per-car time or
    gap whatsoever.

    ``_OUTCOME_AMBIGUOUS`` renders like any other car on purpose: its lap
    deficit is arithmetically true whether it retired or was lapped, and the
    caveat belongs in the detail column, not as a "?" in a number column.
    """
    if outcome == _OUTCOME_RETIRED:
        return "DNF"
    if outcome == _OUTCOME_DISQUALIFIED:
        return "DSQ"
    if stats is None:
        return ""
    if stats.behind_time:
        return _signed(stats.behind_time)[:_SCORE_MAX]
    if stats.laps_behind:
        return _laps_behind(stats.laps_behind)
    if stats.total_time:
        return stats.total_time[:_SCORE_MAX]
    if stats.laps_completed:
        return f"{stats.laps_completed} laps"
    return ""


def _racing_detail_text(parts: list[str], *, keep_last: bool = False) -> str:
    """Join detail parts with " · ", bounded by ``_DETAIL_MAX``.

    ``keep_last`` protects the final part from the truncation — used for the
    unknown-status caveat, which is the one part whose loss would change what
    the row MEANS rather than just how much of it is shown.
    """
    text = " · ".join(parts)
    if len(text) <= _DETAIL_MAX or not keep_last or len(parts) < 2:
        return text[:_DETAIL_MAX]
    last = parts[-1]
    head = " · ".join(parts[:-1])[: max(0, _DETAIL_MAX - len(last) - 3)].rstrip(" ·")
    return f"{head} · {last}" if head else last[:_DETAIL_MAX]


def _racing_detail(
    car: _RacingCar,
    stats: _RacingCarStats | None,
    *,
    outcome: str,
    retired_lap: int | None,
) -> str:
    """The ``detail`` column: "Winner · Team · #7 · Grid 2", bounded.

    A retired car names the lap it stopped on (its ``status.period``, or its
    ``lapsCompleted`` when the status call was skipped or failed) so the
    "DNF" in the score column reads as a fact rather than a shrug, and a
    disqualified one says "Disqualified" — DSQ is NOT inferable from
    statistics (3 of the 6 sampled carry the full race distance and look
    exactly like finishers), so that word only ever comes from the status
    resource.  A car whose outcome is undecidable gets
    ``_UNKNOWN_STATUS_PART`` last.  The one car in the field with a
    ``fastestLap`` gets it instead.
    """
    parts: list[str] = []
    if car.winner:
        parts.append("Winner")
    if car.team:
        parts.append(car.team)
    if car.number:
        parts.append(f"#{car.number}")
    if car.grid:
        parts.append(f"Grid {car.grid}")
    if outcome == _OUTCOME_RETIRED:
        lap = retired_lap or (stats.laps_completed if stats is not None else None)
        parts.append(f"Retired lap {lap}" if lap else "Retired")
    elif outcome == _OUTCOME_DISQUALIFIED:
        parts.append("Disqualified")
    else:
        if stats is not None and stats.fastest_lap:
            parts.append(f"Fastest lap {stats.fastest_lap}")
        if outcome == _OUTCOME_AMBIGUOUS:
            parts.append(_UNKNOWN_STATUS_PART)
    return _racing_detail_text(parts, keep_last=outcome == _OUTCOME_AMBIGUOUS)


def _racing_facts(
    cars: dict[str, _RacingCar],
    stats: dict[str, _RacingCarStats],
    statuses: dict[str, tuple[str, int | None]],
) -> dict[str, _RacingCarFacts]:
    """Rendered ``(score, detail)`` per athlete id; all-empty cars dropped.

    ``stats``/``statuses`` are empty on a tier-A poll, which is exactly how
    a live board ends up with a constructor and a grid slot but no time.
    """
    facts: dict[str, _RacingCarFacts] = {}
    for athlete_id, car in cars.items():
        car_stats = stats.get(athlete_id)
        outcome, retired_lap = _racing_render_outcome(
            car, car_stats, statuses.get(athlete_id, (_OUTCOME_NONE, None))
        )
        score = _racing_score(car_stats, outcome=outcome)
        detail = _racing_detail(car, car_stats, outcome=outcome, retired_lap=retired_lap)
        if score or detail:
            facts[athlete_id] = _RacingCarFacts(score=score, detail=detail)
    return facts


def _apply_racing_facts(
    board: tuple[LeaderRow, ...], facts: dict[str, _RacingCarFacts]
) -> tuple[LeaderRow, ...]:
    """Merge rendered facts into a parsed board, row by row.

    The join key is ``LeaderRow.player_id`` — still the bare ESPN athlete id
    at provider level (the scheduler rewrites it to the internal
    followed-team id afterwards), and the same id the core feed keys a
    competitor by.  Enrichment only ever ADDS: a row with no facts, or an
    empty rendered column, keeps whatever the site scoreboard gave us.
    """
    if not facts:
        return board
    return tuple(
        replace(row, score=car.score or row.score, detail=car.detail or row.detail)
        if (car := facts.get(row.player_id or "")) is not None
        else row
        for row in board
    )


def _racing_enrich_targets(events: Sequence[Event]) -> list[Event]:
    """The events worth a core-API enrichment, live races first.

    Only a weekend that already HAS a board can be enriched, and only a
    live or finished one is worth the call: a scheduled race's entry list is
    not published, so its competitors resources just 404.  The slice is
    capped so a season-wide window fetch can never fan out over two dozen
    finished races; the most recently started ones win, because those are
    the boards anyone is still looking at.
    """
    candidates = [
        event
        for event in events
        if event.leaderboard and event.phase in (GamePhase.IN_PROGRESS, GamePhase.FINAL)
    ]
    candidates.sort(
        key=lambda event: (event.phase is not GamePhase.IN_PROGRESS, -event.start_time.timestamp())
    )
    return candidates[:_ENRICH_MAX_EVENTS]


def _racing_race_distance(payload: Any) -> int | None:
    """The scheduled race distance in laps from the competition aggregate."""
    return _positive_int(_racing_stat_map(payload).get("laps", {}).get("value"))
