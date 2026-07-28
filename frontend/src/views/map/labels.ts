/**
 * Accessible names for the map's markers — pure string builders, extracted so
 * they are unit-testable without MapLibre or a DOM (as travel.ts is).
 *
 * MapLibre adopts every marker element as a `role="button"`, and unless the
 * element already carries an `aria-label` it stamps its own default on it. So
 * without this module a screen reader reads the whole map as twenty identical
 * buttons named "Map marker" — one club indistinguishable from another, and a
 * single venue indistinguishable from a cluster hiding four.
 *
 * Each kind therefore names itself from the data it already draws: what it is,
 * where it is, and the state the badge encodes visually (the today pulse, the
 * in-transit plane badge, a cluster's "your teams are in here" ring). A cluster
 * is deliberately NOT named like a place — it is a count and the action that
 * opens it. The fuller story (next match, scores, the fixture list) stays in
 * the hover label and the side panel: an accessible name is announced every
 * time the pin is reached, so it has to stay short.
 */
import type { MapTeam } from "../../types";
import type { MapVenueGroup } from "../../components/MapVenueGamesPanel";
import { abbreviateCount, type MapMode } from "./markers";

/** Spoken form of the `.sd-map-marker-today` pulse ring, shared by both pins. */
const TODAY = "playing today";

/**
 * Name a team/stadium pin — the club and the ground it stands on, since one
 * marker per team is exactly one stadium. Mirrors `createMarkerElement`'s
 * arguments so the badge and its name are always built from the same facts.
 */
export function teamMarkerLabel(
  team: MapTeam,
  highlightToday: boolean,
  inTransit = false,
): string {
  const parts = [team.venue ? `${team.name}, ${team.venue}` : team.name];
  if (highlightToday) parts.push(TODAY);
  // The corner plane badge — same wording as the badge's own title text.
  if (inTransit) parts.push("traveling to an away game");
  return parts.join(", ");
}

/**
 * Name a game-venue pin. The badge shows only a number, so the name has to
 * supply both the place and what that number counts.
 */
export function venueMarkerLabel(
  group: MapVenueGroup,
  highlightToday: boolean,
): string {
  const count = group.games.length;
  // Same fallback the hover label uses for a venue the upstream never named.
  const parts = [
    `${group.venue ?? "Venue"}, ${count} upcoming ${count === 1 ? "game" : "games"}`,
  ];
  if (highlightToday) parts.push(TODAY);
  return parts.join(", ");
}

/**
 * Name a cluster badge: how many pins it collapses, whether any of them are
 * the user's own (what the amber ring says), and what clicking does. The count
 * is abbreviated exactly as the badge prints it, so the visible label stays
 * inside the accessible name.
 */
export function clusterMarkerLabel(
  count: number,
  hasFollowed: boolean,
  mode: MapMode,
): string {
  const noun = mode === "games" ? "venues" : "stadiums";
  const parts = [`${abbreviateCount(count)} ${count === 1 ? noun.slice(0, -1) : noun}`];
  if (hasFollowed) parts.push("including teams you follow");
  parts.push("zoom in");
  return parts.join(", ");
}
