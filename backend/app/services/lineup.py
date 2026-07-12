"""Roster-derived, sport-specific projected lineups.

No free provider exposes confirmed gameday lineups — ESPN's box score only
names the top performers — so a game's "lineup" is built from each team's
stored roster: the real players (real names, positions, numbers, photos)
arranged into the sport's starting shape by their listed positions.  It is a
positional / depth-chart view, explicitly NOT a confirmed starting XI.

The arrangement is best-effort: positions are matched loosely (providers spell
them many ways), each unit fills up to a sport-typical count (active players
first), and anything left over becomes the bench.  Sparse or oddly-labeled
rosters still produce *a* lineup rather than nothing.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from app.models.domain import (
    LineupSlot,
    Player,
    PlayerStatus,
    Sport,
    TeamLineup,
)


class _PlayerLike(Protocol):
    """The subset of player attributes the arranger reads.

    Accepts both the domain ``Player`` and the ``PlayerORM`` row (the stored
    roster), so callers can pass whatever the repository hands back.
    """

    name: str
    position: str | None
    status: object


# Per sport: the ordered units and how many starters each holds.  A roster
# player joins the first unit its position maps to (see ``_unit_for``); the
# unit fills up to its count, the rest go to the bench.
_UNITS: dict[Sport, tuple[tuple[str, int], ...]] = {
    Sport.SOCCER: (("GK", 1), ("DEF", 4), ("MID", 3), ("FWD", 3)),
    Sport.BASKETBALL: (("G", 2), ("F", 2), ("C", 1)),
    Sport.BASEBALL: (("P", 1), ("C", 1), ("IF", 4), ("OF", 3), ("DH", 1)),
    Sport.HOCKEY: (("F", 3), ("D", 2), ("G", 1)),
    Sport.FOOTBALL: (("OFF", 6), ("DEF", 5), ("ST", 1)),
    Sport.VOLLEYBALL: (("OH", 2), ("MB", 2), ("OPP", 1), ("S", 1), ("L", 1)),
}

# Position token -> unit, per sport.  Matched against the upper-cased position
# string (exact token, then substring) so both terse provider codes ("CB") and
# spelled-out forms ("Centre-Back") land in the right unit.
_POSITION_UNITS: dict[Sport, tuple[tuple[str, frozenset[str]], ...]] = {
    Sport.SOCCER: (
        ("GK", frozenset({"GK", "G", "GOALKEEPER", "KEEPER"})),
        (
            "DEF",
            frozenset(
                {
                    "D",
                    "DF",
                    "CB",
                    "LB",
                    "RB",
                    "LWB",
                    "RWB",
                    "SW",
                    "WB",
                    "DEFENDER",
                    "DEFENCE",
                    "DEFENSE",
                    "FULLBACK",
                    "BACK",
                }
            ),
        ),
        (
            "MID",
            frozenset(
                {
                    "M",
                    "MF",
                    "CM",
                    "DM",
                    "AM",
                    "RM",
                    "LM",
                    "CDM",
                    "CAM",
                    "MIDFIELDER",
                    "MIDFIELD",
                }
            ),
        ),
        (
            "FWD",
            frozenset(
                {
                    "F",
                    "FW",
                    "ST",
                    "CF",
                    "W",
                    "LW",
                    "RW",
                    "SS",
                    "FORWARD",
                    "STRIKER",
                    "WINGER",
                    "ATTACKER",
                }
            ),
        ),
    ),
    Sport.BASKETBALL: (
        ("G", frozenset({"PG", "SG", "G", "GUARD", "POINT", "SHOOTING"})),
        ("F", frozenset({"SF", "PF", "F", "FORWARD"})),
        ("C", frozenset({"C", "CENTER", "CENTRE"})),
    ),
    Sport.BASEBALL: (
        ("P", frozenset({"P", "SP", "RP", "LHP", "RHP", "PITCHER"})),
        ("C", frozenset({"C", "CATCHER"})),
        (
            "IF",
            frozenset(
                {
                    "1B",
                    "2B",
                    "3B",
                    "SS",
                    "IF",
                    "INFIELD",
                    "INFIELDER",
                    "FIRST",
                    "SECOND",
                    "THIRD",
                    "SHORTSTOP",
                }
            ),
        ),
        (
            "OF",
            frozenset(
                {
                    "LF",
                    "CF",
                    "RF",
                    "OF",
                    "OUTFIELD",
                    "OUTFIELDER",
                }
            ),
        ),
        ("DH", frozenset({"DH", "DESIGNATED", "HITTER", "UTIL", "UT"})),
    ),
    Sport.HOCKEY: (
        ("F", frozenset({"C", "LW", "RW", "F", "W", "CENTER", "CENTRE", "WING", "FORWARD"})),
        ("D", frozenset({"D", "LD", "RD", "DEFENSE", "DEFENCE", "DEFENSEMAN", "DEFENCEMAN"})),
        ("G", frozenset({"G", "GOALIE", "GOALTENDER", "GK"})),
    ),
    Sport.FOOTBALL: (
        (
            "OFF",
            frozenset(
                {
                    "QB",
                    "RB",
                    "FB",
                    "HB",
                    "WR",
                    "TE",
                    "OL",
                    "C",
                    "G",
                    "T",
                    "OT",
                    "OG",
                    "LT",
                    "RT",
                    "LG",
                    "RG",
                }
            ),
        ),
        (
            "DEF",
            frozenset(
                {
                    "DL",
                    "DE",
                    "DT",
                    "NT",
                    "EDGE",
                    "LB",
                    "ILB",
                    "OLB",
                    "MLB",
                    "CB",
                    "S",
                    "SS",
                    "FS",
                    "DB",
                    "NB",
                }
            ),
        ),
        ("ST", frozenset({"K", "P", "LS", "PK", "KR", "PR", "KICKER", "PUNTER"})),
    ),
    Sport.VOLLEYBALL: (
        ("OH", frozenset({"OH", "OUTSIDE"})),
        ("MB", frozenset({"MB", "MIDDLE"})),
        ("OPP", frozenset({"OPP", "OPPOSITE", "RS"})),
        ("S", frozenset({"S", "SETTER"})),
        ("L", frozenset({"L", "LIBERO", "DS"})),
    ),
}


def _normalize(position: str | None) -> str:
    return (position or "").strip().upper()


def _unit_for(sport: Sport, position: str | None) -> str | None:
    """The unit a position belongs to for ``sport``, or None if unrecognized."""
    pos = _normalize(position)
    if not pos:
        return None
    matchers = _POSITION_UNITS.get(sport)
    if matchers is None:
        return None
    # Exact-token match wins (so "C" picks catcher in baseball, not "CF").
    for unit, tokens in matchers:
        if pos in tokens:
            return unit
    # Then a loose substring match for spelled-out / compound labels.
    for unit, tokens in matchers:
        if any(len(tok) > 1 and tok in pos for tok in tokens):
            return unit
    return None


def _is_active(player: _PlayerLike) -> bool:
    # PlayerStatus is a str-enum, so this works whether ``status`` is the enum
    # (domain Player) or the stored string (PlayerORM row).
    return player.status == PlayerStatus.ACTIVE


def build_team_lineup(sport: Sport, players: Iterable[_PlayerLike]) -> TeamLineup | None:
    """Arrange a roster into ``sport``'s starting shape, or None if not applicable.

    Returns None for sports without a roster-based lineup (individual /
    leaderboard sports) and for an empty roster.  Otherwise fills each unit up
    to its count from players whose position maps to it (active first), assigns
    a 1-based order across the chosen starters, and returns the rest as bench.
    """
    units = _UNITS.get(sport)
    roster = list(players)
    if units is None or not roster:
        return None

    buckets: dict[str, list[_PlayerLike]] = {key: [] for key, _ in units}
    leftover: list[_PlayerLike] = []
    for player in roster:
        unit = _unit_for(sport, player.position)
        if unit in buckets:
            buckets[unit].append(player)
        else:
            leftover.append(player)

    slots: list[LineupSlot] = []
    bench: list[Player] = []
    order = 0
    for key, count in units:
        # Active players first, preserving roster order within each group.
        chosen = sorted(buckets[key], key=lambda p: not _is_active(p))
        for player in chosen[:count]:
            order += 1
            slots.append(
                LineupSlot(
                    player=_as_player(player),
                    role=_normalize(player.position) or key,
                    unit=key,
                    order=order,
                )
            )
        bench.extend(_as_player(p) for p in chosen[count:])
    bench.extend(_as_player(p) for p in leftover)

    if not slots:
        return None

    formation: str | None = None
    if sport is Sport.SOCCER:
        outfield = [sum(1 for s in slots if s.unit == unit) for unit in ("DEF", "MID", "FWD")]
        if any(outfield):
            formation = "-".join(str(n) for n in outfield if n) or None

    return TeamLineup(formation=formation, slots=tuple(slots), bench=tuple(bench))


def _as_player(player: _PlayerLike) -> Player:
    """Coerce a stored roster row (or domain Player) into a domain ``Player``.

    The serializer only needs the read-only player fields; building a frozen
    ``Player`` here keeps the lineup model provider-agnostic regardless of
    whether the repository returned ORM rows or domain objects.
    """
    if isinstance(player, Player):
        return player
    return Player(
        id=getattr(player, "id", ""),
        team_id=getattr(player, "team_id", ""),
        name=player.name,
        position=getattr(player, "position", None),
        jersey_number=getattr(player, "jersey_number", None),
        status=_coerce_status(getattr(player, "status", PlayerStatus.ACTIVE)),
        status_detail=getattr(player, "status_detail", None),
        stat_line=getattr(player, "stat_line", None),
        career_stat_line=getattr(player, "career_stat_line", None),
        photo_url=getattr(player, "photo_url", None),
    )


def _coerce_status(status: object) -> PlayerStatus:
    if isinstance(status, PlayerStatus):
        return status
    try:
        return PlayerStatus(str(status))
    except ValueError:
        return PlayerStatus.ACTIVE
