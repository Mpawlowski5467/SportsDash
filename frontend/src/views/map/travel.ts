/**
 * Travel/animation data builders for the map view — pure functions joining
 * plotted teams to their games, extracted from MapView.tsx so they are
 * unit-testable without MapLibre.
 */
import type { Feature, FeatureCollection, LineString } from "geojson";

import type { GameSide, MapGame, MapTeam } from "../../types";
import type { TravelInfo } from "../../components/MapTeamPanel";
import type { MapVenueGroup } from "../../components/MapVenueGamesPanel";
import { greatCircle, hasCoords, haversineKm } from "../../lib/geo";
import { DEFAULT_COLOR } from "./markers";

/** True when an ISO timestamp falls on the viewer's local calendar today. */
export function isToday(iso: string | null): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

/** Group games into one entry per venue (keyed by rounded coordinates). */
export function groupGamesByVenue(games: MapGame[]): MapVenueGroup[] {
  const byKey = new Map<string, MapVenueGroup>();
  for (const game of games) {
    const key = `${game.lat.toFixed(4)},${game.lon.toFixed(4)}`;
    let group = byKey.get(key);
    if (group === undefined) {
      group = {
        key,
        venue: game.venue,
        lat: game.lat,
        lon: game.lon,
        games: [],
        source: "competition",
      };
      byKey.set(key, group);
    }
    group.games.push(game);
    if (game.followed) group.source = "followed";
  }
  for (const group of byKey.values()) {
    group.games.sort((a, b) => a.start_time.localeCompare(b.start_time));
  }
  return [...byKey.values()];
}

/** A followed team's next located game in the window, and which side it's on. */
export interface NextGame {
  game: MapGame;
  isHome: boolean;
}

/** Normalize a team/competitor name for joining games to plotted pins. */
export function teamNameKey(name: string): string {
  return name.trim().toLowerCase();
}

/**
 * Each plotted team's NEXT located game (earliest, home OR away) in the
 * window. The home/away split drives the travel visuals: an away next-game
 * gets the flight arc + plane, a home next-game gets the fans animation.
 * Every team on the map gets one (not just your followed teams), so a whole
 * competition / "follow all" still animates.
 *
 * Whole-competition games carry NO team_id on either side (only a name), so we
 * join a game side to a plotted pin by name, falling back through the rare
 * id-bearing case. Without the name join, "follow all" maps never animate.
 */
export function buildNextGames(
  teams: MapTeam[],
  games: MapGame[],
): Map<string, NextGame> {
  const byId = new Map(teams.map((t) => [t.team_id, t] as const));
  const byName = new Map<string, MapTeam>();
  for (const t of teams) byName.set(teamNameKey(t.name), t);
  const out = new Map<string, NextGame>();
  const consider = (side: GameSide, game: MapGame, isHome: boolean) => {
    if (!hasCoords(game)) return;
    const team =
      (side.team_id !== null ? byId.get(side.team_id) : undefined) ??
      byName.get(teamNameKey(side.name));
    if (team === undefined) return;
    const cur = out.get(team.team_id);
    if (cur === undefined || game.start_time < cur.game.start_time) {
      out.set(team.team_id, { game, isHome });
    }
  };
  for (const game of games) {
    consider(game.home, game, true);
    consider(game.away, game, false);
  }
  return out;
}

/**
 * Travel-arc FeatureCollection: a great-circle home→host line for every plotted
 * team whose NEXT game is AWAY (a home next-game gets fans, not an arc/plane).
 */
export function buildTravelArcs(
  teams: MapTeam[],
  nextByTeam: Map<string, NextGame>,
): FeatureCollection<LineString> {
  const features: Feature<LineString>[] = [];
  for (const team of teams) {
    if (!hasCoords(team)) continue;
    const next = nextByTeam.get(team.team_id);
    if (next === undefined || next.isHome) continue;
    const dest = next.game;
    const line = greatCircle(team.lon, team.lat, dest.lon, dest.lat);
    if (line === null) continue;
    features.push({
      type: "Feature",
      properties: { team_id: team.team_id, game_id: dest.game_id },
      geometry: { type: "LineString", coordinates: line },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * The followed plotted team a game side belongs to — id join first, then the
 * normalized-name join buildNextGames uses (whole-competition games carry no
 * team_id on either side). Returns undefined when the side isn't a team the
 * user follows, so the one-shot travel visuals (click any upcoming game in
 * the venue panel) stay reserved for the user's own teams.
 */
export function findFollowedTeamForSide(
  teams: MapTeam[],
  side: GameSide,
): MapTeam | undefined {
  const team =
    (side.team_id !== null
      ? teams.find((t) => t.team_id === side.team_id)
      : undefined) ??
    teams.find((t) => teamNameKey(t.name) === teamNameKey(side.name));
  return team !== undefined && team.source === "followed" ? team : undefined;
}

/**
 * Plotted teams that have a game HAPPENING — live (in_progress) or kicking off
 * today — mapped to the crowd colour for their stadium. Drives the
 * click-to-celebrate fan burst: clicking such a team's pin streams fans into
 * its stadium. Joined by name (whole-competition games carry no team_id on
 * either side) with the id-bearing case as a fallback, exactly like the other
 * animation builders, so a "follow all" field celebrates too.
 */
export function buildLiveTeams(
  teams: MapTeam[],
  games: MapGame[],
): Map<string, string> {
  const byId = new Map(teams.map((t) => [t.team_id, t] as const));
  const byName = new Map<string, MapTeam>();
  for (const t of teams) byName.set(teamNameKey(t.name), t);
  const out = new Map<string, string>();
  const consider = (side: GameSide) => {
    const team =
      (side.team_id !== null ? byId.get(side.team_id) : undefined) ??
      byName.get(teamNameKey(side.name));
    if (team === undefined) return;
    out.set(team.team_id, team.color ?? side.color ?? DEFAULT_COLOR);
  };
  for (const game of games) {
    const happening =
      game.phase === "in_progress" ||
      (game.phase === "scheduled" && isToday(game.start_time));
    if (!happening) continue;
    consider(game.home);
    consider(game.away);
  }
  return out;
}

// --- Travel facts (computed from coordinates; no provider call) -------------
const AVG_FLIGHT_KMH = 800; // effective cruise incl. climb/descent
const FLIGHT_OVERHEAD_MIN = 40; // taxi + climb + descent allowance
const IN_TRANSIT_HOURS = 48; // a team is "in transit" this close to an away game

/**
 * Travel facts per plotted team whose NEXT game is AWAY: great-circle
 * distance, an estimated flight time, the (approximate) time-zone change
 * (lon/15), and whether the trip is imminent ("in transit").
 */
export function buildTravelInfo(
  teams: MapTeam[],
  nextByTeam: Map<string, NextGame>,
  nowMs: number,
): Map<string, TravelInfo> {
  const out = new Map<string, TravelInfo>();
  for (const team of teams) {
    if (!hasCoords(team)) continue;
    const next = nextByTeam.get(team.team_id);
    if (next === undefined || next.isHome) continue;
    const dest = next.game;
    const km = haversineKm(team.lat, team.lon, dest.lat, dest.lon);
    if (km < 1) continue; // same venue — no real trip
    const startMs = new Date(dest.start_time).getTime();
    // Approximate tz from longitude (lon/15), wrapped to [-12,+12] so a
    // trans-antimeridian trip doesn't report an impossible ±23h jump.
    let tzDelta = Math.round(dest.lon / 15) - Math.round(team.lon / 15);
    if (tzDelta > 12) tzDelta -= 24;
    else if (tzDelta < -12) tzDelta += 24;
    out.set(team.team_id, {
      destVenue: dest.venue ?? "the away venue",
      opponent: dest.home.name, // they're the away side, so home is the host
      distanceKm: km,
      flightMinutes: FLIGHT_OVERHEAD_MIN + (km / AVG_FLIGHT_KMH) * 60,
      tzDelta,
      inTransit:
        Number.isFinite(startMs) &&
        startMs >= nowMs &&
        startMs - nowMs <= IN_TRANSIT_HOURS * 3600 * 1000,
    });
  }
  return out;
}
