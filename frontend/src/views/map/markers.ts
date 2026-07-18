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
  // Wrap so a corner plane badge can sit OUTSIDE the circle (which clips its own
  // overflow) — marks a team currently flying to an away game.
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:relative;line-height:0;";
  wrap.appendChild(el);
  const badge = document.createElement("div");
  // Same airliner as the map planes, shrunk to badge size (static markup).
  badge.innerHTML = PLANE_SVG.replace('width="22" height="27"', 'width="11" height="14"');
  badge.title = "Traveling to an away game";
  badge.style.cssText =
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

// A top-down airliner planform pointing NORTH (up); rotate by the compass
// bearing to aim it along the route. The airframe is vertically centered so
// the CSS rotate pivot (50% 50%) stays on the body, and the three fading
// dashes south of the tail are a contrail that trails behind it in flight.
// Two-tone wings + a dark outline keep it legible on any basemap.
export const PLANE_SVG =
  '<svg viewBox="0 0 32 40" width="22" height="27" aria-hidden="true">' +
  '<g stroke="rgba(9,9,11,0.72)" stroke-width="0.75">' +
  '<path fill="#e7e7ea" d="M17.4 12.3 27.9 20.7c.6.5.6 1.4-.2 1.6l-10.1-2.6z"/>' +
  '<path fill="#e7e7ea" d="M14.6 12.3 4.1 20.7c-.6.5-.6 1.4.2 1.6l10.1-2.6z"/>' +
  '<path fill="#e7e7ea" d="M17.3 23.1l5.3 4.2c.4.4.3 1.1-.3 1.2l-4.8-1z"/>' +
  '<path fill="#e7e7ea" d="M14.7 23.1l-5.3 4.2c-.4.4-.3 1.1.3 1.2l4.8-1z"/>' +
  '<rect fill="#d4d4d8" x="20.5" y="15.6" width="2.1" height="4.8" rx="1.05"/>' +
  '<rect fill="#d4d4d8" x="9.4" y="15.6" width="2.1" height="4.8" rx="1.05"/>' +
  '<path fill="#fafafa" d="M16 3.4c1.4 0 2 1.5 2 3.2l.1 17.2c0 2.5-.8 4.6-2.1 4.6s-2.1-2.1-2.1-4.6l.1-17.2c0-1.7.6-3.2 2-3.2z"/>' +
  "</g>" +
  '<g stroke="#fafafa" stroke-width="1.3" stroke-linecap="round">' +
  '<line x1="16" y1="30" x2="16" y2="31.8" opacity="0.5"/>' +
  '<line x1="16" y1="32.7" x2="16" y2="34.1" opacity="0.32"/>' +
  '<line x1="16" y1="35" x2="16" y2="36.1" opacity="0.18"/>' +
  "</g></svg>";

/** A plane marker element (div + rotatable inner SVG glyph). */
export function createPlaneElement(): { el: HTMLDivElement; glyph: HTMLElement } {
  const el = document.createElement("div");
  el.className = "sd-map-plane";
  el.style.pointerEvents = "none";
  el.innerHTML = PLANE_SVG;
  return { el, glyph: el.firstElementChild as HTMLElement };
}

// Side-profile walking figures facing RIGHT (viewBox 0 0 24 30). The jersey,
// cap, and scarf are `currentColor` so each fan wears its team's colors;
// head/hands are a warm neutral, pants dark. The `.sd-leg-*` / `.sd-arm`
// classes let CSS swing the limbs (see map.css) — a real walk cycle, not a
// sliding icon.
const FAN_SKIN = "#dfb28c";
const FAN_PANTS = "#3f3f46";

/** Cap + head shared by the cap-wearing variants. */
const FAN_HEAD_CAP =
  '<path fill="currentColor" d="M8.7 5.5c0-2.3 1.5-3.7 3.3-3.7s3.3 1.4 3.3 3.7v.6H8.7z"/>' +
  '<path fill="currentColor" d="M15.1 5.1l3.4.7c.5.1.5.9 0 1l-3.4-.2z"/>' +
  `<circle cx="12" cy="8.7" r="2.7" fill="${FAN_SKIN}"/>`;

/** Both legs; CSS alternates them around the hip. */
const FAN_LEGS =
  `<path class="sd-leg-b" d="M11.3 17.6 10.8 26.4" stroke="${FAN_PANTS}" stroke-width="2.4" stroke-linecap="round"/>` +
  `<path class="sd-leg-f" d="M12.7 17.6 13.2 26.4" stroke="${FAN_PANTS}" stroke-width="2.4" stroke-linecap="round"/>`;

/** Team jersey with a sleeve stripe. */
const FAN_JERSEY =
  '<path fill="currentColor" d="M9.2 12.6c0-1 .8-1.5 2.8-1.5s2.8.5 2.8 1.5l.3 4.5c0 .7-.5 1.2-1.2 1.2h-3.8c-.7 0-1.2-.5-1.2-1.2z"/>' +
  '<rect x="9.1" y="13.4" width="6.1" height="1.05" fill="rgba(255,255,255,0.85)"/>';

const FAN_SWING_ARM = `<path class="sd-arm" d="M13.3 12.6c1.4 1.2 2 3 1.9 5.3" stroke="${FAN_SKIN}" stroke-width="2.1" stroke-linecap="round" fill="none"/>`;

// A mixed crowd instead of one repeated figure: walker, scarf held
// overhead, flag waver, and a kid with a backpack.
const FAN_GLYPHS = [
  // walking to the gates
  FAN_HEAD_CAP + FAN_JERSEY + FAN_SWING_ARM + FAN_LEGS,
  // scarf up — the scarf owns the top, so this one wears no cap
  `<circle cx="12" cy="8.2" r="2.7" fill="${FAN_SKIN}"/>` +
    FAN_JERSEY +
    `<path d="M13.3 12.2c1.7-1.9 2.6-4 2.9-6.2" stroke="${FAN_SKIN}" stroke-width="1.9" stroke-linecap="round" fill="none"/>` +
    `<path d="M10.7 12.2C9 10.3 8.1 8.2 7.8 6" stroke="${FAN_SKIN}" stroke-width="1.9" stroke-linecap="round" fill="none"/>` +
    `<circle cx="7.7" cy="5.2" r="1" fill="${FAN_SKIN}"/>` +
    `<circle cx="16.3" cy="5.2" r="1" fill="${FAN_SKIN}"/>` +
    '<rect x="6.1" y="1.4" width="11.8" height="2.5" rx="1.1" fill="currentColor"/>' +
    '<rect x="9.2" y="1.4" width="1.1" height="2.5" fill="rgba(255,255,255,0.85)"/>' +
    '<rect x="12.9" y="1.4" width="1.1" height="2.5" fill="rgba(255,255,255,0.85)"/>' +
    FAN_LEGS,
  // waving a flag
  FAN_HEAD_CAP +
    FAN_JERSEY +
    `<path d="M10.8 12.6c-1.1 1.4-1.6 3.1-1.4 5.1" stroke="${FAN_SKIN}" stroke-width="1.9" stroke-linecap="round" fill="none"/>` +
    `<path d="M13.2 12.3c1.6-1.5 2.3-3 2.5-4.7" stroke="${FAN_SKIN}" stroke-width="1.9" stroke-linecap="round" fill="none"/>` +
    '<line x1="15.8" y1="7.6" x2="16.6" y2="1.6" stroke="#a1a1aa" stroke-width="0.9"/>' +
    '<path fill="currentColor" d="M16.6 1.6c1.7-.3 3.3.5 5.1.9l-.1 3.3c-1.8-.4-3.4-1.2-5.1-.9z"/>' +
    FAN_LEGS,
  // kid with a backpack — the pack juts out behind, lighter than the pants
  // so it reads against dark basemaps
  FAN_HEAD_CAP +
    '<rect x="6.1" y="11.6" width="3.1" height="5" rx="1.3" fill="#71717a"/>' +
    '<rect x="6.1" y="12.9" width="3.1" height="1" fill="#52525b"/>' +
    FAN_JERSEY +
    FAN_SWING_ARM +
    FAN_LEGS,
];

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
  const glyph =
    FAN_GLYPHS[((variant % FAN_GLYPHS.length) + FAN_GLYPHS.length) % FAN_GLYPHS.length];
  const width = Math.round(size * 0.8); // viewBox is 24x30
  el.innerHTML = `<svg viewBox="0 0 24 30" width="${width}" height="${size}" aria-hidden="true">${glyph}</svg>`;
  const svg = el.firstElementChild as HTMLElement;
  svg.style.animationDelay = `${delaySec}s`;
  // Desync the limb swing per fan, not just the whole-figure bob.
  for (const part of svg.querySelectorAll<HTMLElement>(".sd-leg-f, .sd-leg-b, .sd-arm")) {
    part.style.animationDelay = `${delaySec}s`;
  }
  return el;
}
