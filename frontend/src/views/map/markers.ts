/**
 * Imperative DOM element factories for the map's markers, tooltips,
 * clusters, planes, and fans — extracted from MapView.tsx. MapLibre DOM
 * markers take a plain element, so these are document.createElement
 * builders, not React components.
 */
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
  // Wrap so a corner ✈ badge can sit OUTSIDE the circle (which clips its own
  // overflow) — marks a team currently flying to an away game.
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:relative;line-height:0;";
  wrap.appendChild(el);
  const badge = document.createElement("div");
  badge.textContent = "✈";
  badge.title = "Traveling to an away game";
  badge.style.cssText =
    "position:absolute;top:-6px;right:-6px;font-size:11px;line-height:1;" +
    "color:var(--color-amber-400);text-shadow:0 0 3px rgba(0,0,0,0.75);" +
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

// A top-down airliner silhouette pointing NORTH (up); rotate by the compass
// bearing to aim it along the route. `fill="currentColor"` so CSS colors it.
export const PLANE_SVG =
  '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">' +
  '<path fill="currentColor" d="M12 2.2c-.55 0-1 .9-1 2v5.2L2.5 14c-.3.18-.5.5-.5.86v.5l9-2.1' +
  'v4.2l-2.2 1.6c-.2.14-.3.4-.3.64v.3l3.5-.9 3.5.9v-.3c0-.24-.1-.5-.3-.64L13 17.46v-4.2l9 2.1' +
  'v-.5c0-.36-.2-.68-.5-.86L13 9.4V4.2c0-1.1-.45-2-1-2z"/></svg>';

/** A plane marker element (div + rotatable inner SVG glyph). */
export function createPlaneElement(): { el: HTMLDivElement; glyph: HTMLElement } {
  const el = document.createElement("div");
  el.className = "sd-map-plane";
  el.style.pointerEvents = "none";
  el.innerHTML = PLANE_SVG;
  return { el, glyph: el.firstElementChild as HTMLElement };
}

// Several person silhouettes (all drawn facing RIGHT, `fill="currentColor"` so
// each fan is tinted its team's colour) — a mixed crowd instead of one repeated
// icon: walking, running, standing, and arms-up cheering.
const FAN_GLYPHS = [
  // walking
  '<path fill="currentColor" d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9 7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 13 16.8 14 19 14v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6z"/>',
  // running
  '<path fill="currentColor" d="M13.49 5.48c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm-3.6 13.9 1-4.4 2.1 2v6h2v-7.5l-2.1-2 .6-3c1.3 1.5 3.3 2.5 5.5 2.5v-2c-1.9 0-3.5-1-4.4-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1l-5.2 2.2v4.7h2v-3.4l1.8-.7-1.6 8.1-4.9-1-.4 2z"/>',
  // standing
  '<path fill="currentColor" d="M14 7h-4c-1.1 0-2 .9-2 2v6h2v7h4v-7h2V9c0-1.1-.9-2-2-2zm-2-.75c.97 0 1.75-.78 1.75-1.75S12.97 2.75 12 2.75 10.25 3.53 10.25 4.5 11.03 6.25 12 6.25z"/>',
  // cheering (arms up)
  '<g fill="currentColor"><circle cx="12" cy="4" r="2"/><path d="M10 7c-1.1 0-2 .9-2 2v5h2v7h4v-7h2V9c0-1.1-.9-2-2-2z"/><path d="M9.6 7.9 6.7 5.2c-.45-.42-1.15-.4-1.55.05s-.38 1.15.07 1.55l2.9 2.6c.4-.6.9-1.1 1.48-1.5z"/><path d="M14.4 7.9l2.9-2.7c.45-.42 1.15-.4 1.55.05s.38 1.15-.07 1.55l-2.9 2.6c-.4-.6-.9-1.1-1.48-1.5z"/></g>',
];

/** A "fan" marker element: a small person heading to the stadium. `variant`
 *  picks one of several poses (so the crowd is mixed), `color` tints it (their
 *  team), `faceLeft` flips it to face the stadium, `delaySec` desyncs the walk
 *  bob, and `size` (px) varies so the crowd has some depth. */
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
  const glyph =
    FAN_GLYPHS[((variant % FAN_GLYPHS.length) + FAN_GLYPHS.length) % FAN_GLYPHS.length];
  el.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${glyph}</svg>`;
  (el.firstElementChild as HTMLElement).style.animationDelay = `${delaySec}s`;
  return el;
}
