/**
 * Imperative DOM element factories for the map's markers, tooltips,
 * clusters, planes, and fans — extracted from MapView.tsx. MapLibre DOM
 * markers take a plain element, so these are document.createElement
 * builders, not React components.
 */
import { PLANE_SVG, fanSvg } from "./figures";
export { PLANE_SVG } from "./figures";

import type { MapTeam } from "../../types";
import type { MapVenueGroup } from "../../components/MapVenueGamesPanel";
import { formatShortDate } from "../../lib/time";

export type MapMode = "games" | "stadiums";

/**
 * Marker badge diameter in px — the same for EVERY team plot (a followed
 * team or a whole-competition team) so all stadium chips read as one size.
 * Competition teams still stand out less than the user's own via a muted
 * opacity and a thin neutral ring (see `createMarkerElement`), not size.
 */
export const MARKER_SIZE = 32;

/** Fallback marker color for teams with no brand color. */
export const DEFAULT_COLOR = "#f59e0b"; // amber-500, matches the SportsDash accent

/** Neutral ring color for competition markers (zinc-400). */
export const COMPETITION_RING = "#a1a1aa";

/**
 * Build the team-marker DOM element: a rounded badge showing the team logo
 * with a colored ring and a subtle shadow. When `logo_url` is null — or the
 * image 404s / errors at runtime — we fall back to a solid colored pin so
 * every stadium still shows a marker. `source` drives the styling so the
 * user's own teams stand out among a competition's full field.
 */
export function createMarkerElement(
  team: MapTeam,
  highlightToday: boolean,
  inTransit = false,
): HTMLDivElement {
  const isCompetition = team.source === "competition";
  const ring = isCompetition ? COMPETITION_RING : team.color ?? DEFAULT_COLOR;
  const pinFill = team.color ?? DEFAULT_COLOR;
  const size = MARKER_SIZE;
  const el = document.createElement("div");
  el.className = highlightToday ? "sd-map-marker sd-map-marker-today" : "sd-map-marker";
  el.style.cssText =
    `width:${size}px;height:${size}px;border-radius:9999px;` +
    `box-sizing:border-box;` +
    `border:${isCompetition ? "1.5px" : "2px"} solid ${ring};` +
    `box-shadow:0 1px 4px rgba(0,0,0,0.45);cursor:pointer;` +
    `opacity:${isCompetition ? "0.78" : "1"};` +
    `display:flex;align-items:center;justify-content:center;overflow:hidden;` +
    `background:#f4f4f5;`;

  const showPin = () => {
    el.innerHTML = "";
    el.style.background = pinFill;
  };

  if (team.logo_url) {
    const img = document.createElement("img");
    img.src = team.logo_url;
    img.alt = team.name;
    img.style.cssText =
      "width:100%;height:100%;object-fit:contain;display:block;padding:3px;box-sizing:border-box;";
    img.addEventListener("error", showPin, { once: true });
    el.appendChild(img);
  } else {
    showPin();
  }

  if (!inTransit) return el;
  // Wrap so a corner plane badge can sit OUTSIDE the circle (which clips its own
  // overflow) — marks a team currently flying to an away game.
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:relative;line-height:0;";
  wrap.appendChild(el);
  const badge = document.createElement("div");
  // Same airliner as the map planes, shrunk to badge size (static markup).
  badge.innerHTML = PLANE_SVG.replace('width="30" height="35"', 'width="16" height="19"');
  badge.title = "Traveling to an away game";
  badge.style.cssText =
    `color:${pinFill};` +
    "position:absolute;top:-7px;right:-7px;line-height:0;" +
    "filter:drop-shadow(0 0 2px rgba(0,0,0,0.75));" +
    "pointer-events:none;";
  wrap.appendChild(badge);
  return wrap;
}

/** Hover label content for a team marker (name, venue, next match / league). */
export function createTooltipElement(team: MapTeam): HTMLDivElement {
  const root = document.createElement("div");

  const name = document.createElement("div");
  name.textContent = team.name;
  name.style.cssText = "font-weight:600;color:var(--color-zinc-100);";
  root.appendChild(name);

  if (team.venue) {
    const venue = document.createElement("div");
    venue.textContent = team.venue;
    venue.style.cssText = "margin-top:1px;font-size:12px;color:var(--color-zinc-300);";
    root.appendChild(venue);
  }

  const thirdLine = team.next_opponent
    ? `Next: vs ${team.next_opponent}` +
      (team.next_match_time ? ` · ${formatShortDate(team.next_match_time)}` : "")
    : team.league_name ?? "";
  if (thirdLine) {
    const third = document.createElement("div");
    third.textContent = thirdLine;
    third.style.cssText = "margin-top:2px;font-size:11px;color:var(--color-zinc-400);";
    root.appendChild(third);
  }

  return root;
}

/**
 * Build a game-venue marker: a rounded-square badge showing how many games
 * are scheduled there in the window. Amber when a followed team is involved,
 * neutral zinc for a whole-competition venue — visually distinct from the
 * round team-logo chips of "Stadiums" mode.
 */
export function createVenueMarkerElement(
  group: MapVenueGroup,
  highlightToday: boolean,
): HTMLDivElement {
  const accent = group.source === "followed" ? DEFAULT_COLOR : COMPETITION_RING;
  const el = document.createElement("div");
  el.className = highlightToday
    ? "sd-map-marker sd-map-venue sd-map-marker-today"
    : "sd-map-marker sd-map-venue";
  el.style.cssText =
    "min-width:24px;height:24px;padding:0 6px;border-radius:7px;box-sizing:border-box;" +
    `border:2px solid ${accent};background:#18181b;color:#fafafa;` +
    "box-shadow:0 1px 4px rgba(0,0,0,0.45);cursor:pointer;" +
    "display:flex;align-items:center;justify-content:center;" +
    "font-family:inherit;font-size:12px;font-weight:600;line-height:1;";
  el.textContent = String(group.games.length);
  return el;
}

/** Hover label for a venue marker (venue, game count, next matchup). */
export function createVenueTooltipElement(group: MapVenueGroup): HTMLDivElement {
  const root = document.createElement("div");

  const name = document.createElement("div");
  name.textContent = group.venue ?? "Venue";
  name.style.cssText = "font-weight:600;color:var(--color-zinc-100);";
  root.appendChild(name);

  const count = document.createElement("div");
  count.textContent = `${group.games.length} upcoming ${
    group.games.length === 1 ? "game" : "games"
  }`;
  count.style.cssText = "margin-top:1px;font-size:12px;color:var(--color-zinc-300);";
  root.appendChild(count);

  const next = group.games[0];
  if (next) {
    const line = document.createElement("div");
    line.textContent =
      `${next.away.name} v ${next.home.name} · ${formatShortDate(next.start_time)}`;
    line.style.cssText = "margin-top:2px;font-size:11px;color:var(--color-zinc-400);";
    root.appendChild(line);
  }

  return root;
}

/** Compact count label for a cluster badge (e.g. 1298 -> "1.3k"). */
export function abbreviateCount(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

/**
 * Build a cluster badge: a round dark chip showing how many pins it collapses.
 * It wears an amber ring when any of the user's own (followed) teams are inside
 * so their region stays findable even when zoomed out; otherwise a neutral
 * zinc ring. Size grows a little with the count so dense regions read heavier.
 */
export function createClusterElement(
  count: number,
  hasFollowed: boolean,
): HTMLDivElement {
  const ring = hasFollowed ? DEFAULT_COLOR : COMPETITION_RING;
  const size = count >= 100 ? 46 : count >= 25 ? 40 : 34;
  const el = document.createElement("div");
  el.className = "sd-map-marker sd-map-cluster";
  el.style.cssText =
    `width:${size}px;height:${size}px;border-radius:9999px;box-sizing:border-box;` +
    `border:2px solid ${ring};background:#18181b;color:#fafafa;` +
    `box-shadow:0 0 0 4px ${hasFollowed ? "rgba(245,158,11,0.15)" : "rgba(161,161,170,0.15)"},` +
    `0 1px 6px rgba(0,0,0,0.5);cursor:pointer;` +
    `display:flex;align-items:center;justify-content:center;` +
    `font-family:inherit;font-weight:700;line-height:1;` +
    `font-size:${count >= 100 ? "13px" : "12px"};`;
  el.textContent = abbreviateCount(count);
  return el;
}

/** Hover label for a cluster badge (count + zoom-in hint). */
export function createClusterTooltipElement(
  count: number,
  hasFollowed: boolean,
  mode: MapMode,
): HTMLDivElement {
  const root = document.createElement("div");
  const noun = mode === "games" ? "venues" : "stadiums";

  const title = document.createElement("div");
  title.textContent = `${count} ${count === 1 ? noun.slice(0, -1) : noun}`;
  title.style.cssText = "font-weight:600;color:var(--color-zinc-100);";
  root.appendChild(title);

  if (hasFollowed) {
    const followed = document.createElement("div");
    followed.textContent = "Includes teams you follow";
    followed.style.cssText =
      "margin-top:1px;font-size:11px;color:var(--color-amber-400);";
    root.appendChild(followed);
  }

  const hint = document.createElement("div");
  hint.textContent = "Click to zoom in";
  hint.style.cssText = "margin-top:2px;font-size:11px;color:var(--color-zinc-400);";
  root.appendChild(hint);

  return root;
}

/** A plane marker element (div + rotatable inner SVG glyph). */
export function createPlaneElement(color = DEFAULT_COLOR): { el: HTMLDivElement; glyph: HTMLElement } {
  const el = document.createElement("div");
  el.className = "sd-map-plane";
  el.style.color = color;
  el.style.pointerEvents = "none";
  // Pure decoration, and unclickable — but MapLibre files every marker it
  // adopts as a role="button", so hide it or a screen reader meets one
  // nameless button per plane in flight.
  el.setAttribute("aria-hidden", "true");
  el.setAttribute("role", "presentation");
  el.innerHTML = PLANE_SVG;
  return { el, glyph: el.firstElementChild as HTMLElement };
}

/** A "fan" marker element: a small walking figure heading to the stadium.
 *  `variant` picks one of several figures (so the crowd is mixed), `color`
 *  dresses it in its team's colors, `faceLeft` flips it to face the
 *  stadium, `delaySec` desyncs the walk cycle, and `size` (px height)
 *  varies so the crowd has some depth. */
export function createFanElement(
  color: string,
  faceLeft: boolean,
  delaySec: number,
  variant: number,
  size: number,
): HTMLDivElement {
  const el = document.createElement("div");
  el.className = faceLeft ? "sd-map-fan sd-map-fan-l" : "sd-map-fan sd-map-fan-r";
  el.style.color = color;
  el.style.pointerEvents = "none";
  // Decoration, like the planes: a crowd of dozens must not become a crowd of
  // buttons in the accessibility tree (see createPlaneElement).
  el.setAttribute("aria-hidden", "true");
  el.setAttribute("role", "presentation");
  el.innerHTML = fanSvg(variant, size);
  const svg = el.firstElementChild as HTMLElement;
  svg.style.animationDelay = `${-Math.abs(delaySec)}s`;
  // Desync the limb swing per fan, not just the whole-figure bob.
  for (const part of svg.querySelectorAll<HTMLElement>(".sd-leg-f, .sd-leg-b, .sd-arm")) {
    part.style.animationDelay = `${-Math.abs(delaySec)}s`;
  }
  return el;
}
