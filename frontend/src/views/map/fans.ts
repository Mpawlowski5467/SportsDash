/**
 * Fan crowds walking to the stadium — the shared crowd mechanic behind BOTH
 * the flight cinematic's arrival burst and the click-to-celebrate crowd
 * (extracted so the two call sites can never drift apart again; they used to
 * inline a copy of the same rAF each).
 *
 * Why this mechanic looks the way it does:
 * - CLUMPS, not individuals: real supporters arrive in small groups. Each
 *   clump shares one route; members get small lateral/along offsets
 *   (side-by-side / single-file), slightly varied speeds, and the clumps
 *   start staggered so the flow is continuous, never synchronized.
 * - STREET-LIKE routes: every clump walks a Manhattan two-leg path — along
 *   one axis, turn at a corner, along the other — ending at a GATE on the
 *   stadium's edge (a different gate per clump), never at the center. When
 *   the basemap can tell us the local road direction, the first leg aligns
 *   to it; otherwise the frame falls back to axis-aligned lng/lat.
 * - WALKING feel: movement is ground-speed-based — a real 1.2–1.6 m/s
 *   walking pace under TIME_SCALE compression — so trip time falls out of
 *   the route length instead of being a fixed duration. The old mechanic
 *   slid fans straight to the center over a hard-coded 4.2s, which read as
 *   "dragged in by a force".
 * - LINGER, then fade: at the gate each clump mills around (slow random-walk
 *   drift, staying clustered) for ~8–12s, THEN fades over ~2.5s. The fade is
 *   only at the very end of a fan's ~30s life — the old crowd was gone in 7s.
 *
 * Performance rules (the map keeps views mounted-but-hidden, and DOM markers
 * are expensive):
 * - ONE rAF loop per crowd, advanced at ~30fps even when rAF fires at 60+.
 * - DOM writes only on meaningful change: positions past an epsilon, opacity
 *   quantized to 1/20 steps, facing classes toggled only on flips.
 * - While the map tab is hidden the loop parks entirely (no rAF, no writes)
 *   and polls for visibility on a cheap timer — same IntersectionObserver
 *   signal the flyover orbit uses.
 * - stop() is idempotent and guarantees no orphan loop or markers; callers
 *   invoke it on unmount, mode switch, and every re-trigger.
 */
import maplibregl from "maplibre-gl";
import type { Map as MaplibreMap } from "maplibre-gl";

import { createFanElement } from "./markers";

/** Meters per degree of latitude. Routes span a few hundred meters, so the
 *  equirectangular error is far below a pixel at stadium zooms. */
const M_PER_DEG_LAT = 111_320;
const mPerDegLng = (lat: number): number =>
  M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);

/** Real-world walking pace (m/s) each fan's speed is drawn from. */
const WALK_MIN_MPS = 1.2;
const WALK_MAX_MPS = 1.6;
/**
 * Cinematic time compression. At a true 1.4 m/s a 300m approach would take
 * ~4 minutes — long after the camera has moved on. 12x keeps the motion
 * ground-speed-derived (the trip duration emerges from route length; it is
 * NOT a fixed constant) while reading as a brisk walk on screen.
 */
const TIME_SCALE = 12;

/** Gates sit on the stadium's edge — fans end AT the venue, never inside it. */
const GATE_MIN_M = 70;
const GATE_MAX_M = 120;
/** Clumps start this far out: ~250–470m routes → ~15–30s trips at walk pace. */
const START_MIN_M = 300;
const START_MAX_M = 390;
/** Hard crowd cap — never more figures than this on screen at once. */
const MAX_FANS = 18;

const FADE_IN_MS = 1000; // entering fans fade in instead of popping
const FADE_OUT_MS = 2500; // …and only fade out at the very end of their life
const LINGER_MIN_MS = 8000; // milling at the gate before dispersing
const LINGER_SPAN_MS = 3500;
const MILL_RADIUS_MIN_M = 30; // random-walk drift range around the gate
const MILL_RADIUS_SPAN_M = 30;
/** Stroll speed while milling (already on the time-compressed scale). */
const MILL_MPS = 0.55 * TIME_SCALE;

/** Advance the crowd at ~30fps even when rAF fires at the display rate. */
const FRAME_MS = 33;
/** Re-write a marker only after it moved at least this far (meters). */
const POS_EPSILON_M = 0.3;
/** Opacity writes are quantized to 1/20 steps to skip no-op style writes. */
const OPACITY_STEP = 0.05;
/** Fans are invisible below this zoom and ramp in up to +2 (unchanged gate). */
const ZOOM_FADE_MIN = 8;
const ZOOM_FADE_FULL = 10;
/** How often a parked (hidden-tab) crowd re-checks visibility. */
const PARK_POLL_MS = 500;

interface Point {
  x: number;
  y: number;
}

const toLngLat = (
  p: Point,
  centerLon: number,
  centerLat: number,
): [number, number] => [
  centerLon + p.x / mPerDegLng(centerLat),
  centerLat + p.y / M_PER_DEG_LAT,
];

// ---------------------------------------------------------------------------
// Pure route/plan math (unit-tested without MapLibre or the DOM)
// ---------------------------------------------------------------------------

export interface FanPlan {
  /** Side-by-side offset (m) from the clump's spine, perpendicular to the leg. */
  lateral: number;
  /** Single-file offset (m) along the route; negative trails behind the start. */
  along: number;
  /** Effective ground speed (walking pace × TIME_SCALE), ±15% per fan. */
  speedMps: number;
  sizePx: number;
  variant: number;
  animDelaySec: number;
}

export interface ClumpPlan {
  /** [start, corner, gate] in [lng, lat] — a Manhattan two-leg path. */
  route: [number, number][];
  startDelayMs: number;
  lingerMs: number;
  millRadiusM: number;
  fans: FanPlan[];
}

export interface CrowdPlan {
  clumps: ClumpPlan[];
  fanCount: number;
}

export interface CrowdPlanOptions {
  /** Injectable RNG so tests are deterministic. */
  rand?: () => number;
  /**
   * Per-clump rotation (radians from east, meter space) of the Manhattan
   * frame — the road-alignment hook. Return 0 (or omit) for axis-aligned.
   */
  rotationForClump?: (clumpIndex: number, start: Point) => number;
}

/**
 * A two-leg Manhattan path from `start` to `gate` inside the frame rotated by
 * `theta`: walk along the frame's u-axis, turn once at the corner, then along
 * its v-axis. Which axis comes first is a coin flip so clumps don't all trace
 * the same shape; gate/start angle jitter (in the caller) does the rest.
 */
function manhattanRoute(
  start: Point,
  gate: Point,
  theta: number,
  rand: () => number,
): Point[] {
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  const su = start.x * cos + start.y * sin;
  const sv = -start.x * sin + start.y * cos;
  const gu = gate.x * cos + gate.y * sin;
  const gv = -gate.x * sin + gate.y * cos;
  const corner = rand() < 0.5 ? { u: gu, v: sv } : { u: su, v: gv };
  return [
    start,
    { x: corner.u * cos - corner.v * sin, y: corner.u * sin + corner.v * cos },
    gate,
  ];
}

/**
 * Lay out the whole crowd: 3–4 clumps of 4–5 fans (12–18, hard-capped), each
 * clump with its own gate spread around the perimeter, its own start point
 * roughly outward of that gate, a staggered start, and a linger duration.
 */
export function buildCrowdPlan(
  centerLon: number,
  centerLat: number,
  options: CrowdPlanOptions = {},
): CrowdPlan {
  const rand = options.rand ?? Math.random;
  const clumpCount = 3 + (rand() < 0.5 ? 1 : 0);
  const clumps: ClumpPlan[] = [];
  let fanCount = 0;
  for (let c = 0; c < clumpCount; c += 1) {
    // Gates spread evenly around the stadium (one per clump) with jitter so
    // re-triggers don't reuse identical geometry.
    const gateAngle = (c / clumpCount) * Math.PI * 2 + (rand() - 0.5) * 0.9;
    const gateR = GATE_MIN_M + rand() * (GATE_MAX_M - GATE_MIN_M);
    const gate = { x: Math.cos(gateAngle) * gateR, y: Math.sin(gateAngle) * gateR };
    // The clump starts roughly outward of its own gate — fans approach from
    // the same side of town they enter by.
    const startAngle = gateAngle + (rand() - 0.5) * 0.8;
    const startR = START_MIN_M + rand() * (START_MAX_M - START_MIN_M);
    const start = {
      x: Math.cos(startAngle) * startR,
      y: Math.sin(startAngle) * startR,
    };
    const theta = options.rotationForClump?.(c, start) ?? 0;
    const route = manhattanRoute(start, gate, theta, rand).map((p) =>
      toLngLat(p, centerLon, centerLat),
    ) as [number, number][];

    const memberCount = 4 + Math.floor(rand() * 2); // 4–5 → 12–18 with the cap
    const fans: FanPlan[] = [];
    for (let i = 0; i < memberCount; i += 1) {
      const walk =
        (WALK_MIN_MPS + rand() * (WALK_MAX_MPS - WALK_MIN_MPS)) *
        (0.85 + rand() * 0.3); // ±15% per fan
      fans.push({
        lateral: (i - (memberCount - 1) / 2) * 7 + (rand() - 0.5) * 4,
        along: -i * 9 - rand() * 3,
        speedMps: walk * TIME_SCALE,
        sizePx: 12.5 + rand() * 3.5,
        variant: Math.floor(rand() * 100),
        animDelaySec: rand() * 0.5,
      });
    }
    fanCount += memberCount;
    clumps.push({
      route,
      startDelayMs: c * (1600 + rand() * 900), // staggered, never synchronized
      lingerMs: LINGER_MIN_MS + rand() * LINGER_SPAN_MS,
      millRadiusM: MILL_RADIUS_MIN_M + rand() * MILL_RADIUS_SPAN_M,
      fans,
    });
  }
  // Hard cap: trim trailing fans (never below 2 per clump) if the draw
  // overshot MAX_FANS.
  while (fanCount > MAX_FANS) {
    const clump = [...clumps].reverse().find((cl) => cl.fans.length > 2);
    if (clump === undefined) break;
    clump.fans.pop();
    fanCount -= 1;
  }
  return { clumps, fanCount };
}

/** A route converted to flat meter space around the stadium center, with
 *  cumulative leg lengths for distance-along-route lookups. */
export interface RouteM {
  xs: number[];
  ys: number[];
  cum: number[];
  total: number;
}

export function toRouteM(
  route: [number, number][],
  centerLon: number,
  centerLat: number,
): RouteM {
  const kx = mPerDegLng(centerLat);
  const xs = route.map(([lng]) => (lng - centerLon) * kx);
  const ys = route.map(([, la]) => (la - centerLat) * M_PER_DEG_LAT);
  const cum = [0];
  for (let i = 1; i < xs.length; i += 1) {
    cum.push(cum[i - 1] + Math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]));
  }
  return { xs, ys, cum, total: cum[cum.length - 1] };
}

/** The point `d` meters along the route (clamped), and which leg that's on. */
export function pointAlong(
  route: RouteM,
  d: number,
): { x: number; y: number; leg: number } {
  const clamped = Math.max(0, Math.min(d, route.total));
  let leg = 0;
  while (leg < route.cum.length - 2 && clamped > route.cum[leg + 1]) leg += 1;
  const segLen = route.cum[leg + 1] - route.cum[leg];
  const f = segLen === 0 ? 0 : (clamped - route.cum[leg]) / segLen;
  return {
    x: route.xs[leg] + (route.xs[leg + 1] - route.xs[leg]) * f,
    y: route.ys[leg] + (route.ys[leg + 1] - route.ys[leg]) * f,
    leg,
  };
}

/** Unit direction of a route leg in meter space (x = east). */
export function legDir(route: RouteM, leg: number): Point {
  const last = Math.min(leg, route.xs.length - 2);
  const dx = route.xs[last + 1] - route.xs[last];
  const dy = route.ys[last + 1] - route.ys[last];
  const len = Math.hypot(dx, dy) || 1;
  return { x: dx / len, y: dy / len };
}

/**
 * Facing for a leg. The figures are side-profile, so only the east/west
 * component flips them; a mostly-north/south leg keeps the previous facing
 * (null) rather than picking an arbitrary side.
 */
export function faceLeftForDir(dirX: number): boolean | null {
  if (dirX < -0.3) return true;
  if (dirX > 0.3) return false;
  return null;
}

// ---------------------------------------------------------------------------
// Road alignment (best-effort; axis-aligned fallback)
// ---------------------------------------------------------------------------

/**
 * The dominant road bearing near a point, in radians from east (meter space),
 * or null when the basemap can't answer — style mid-load, area off-screen,
 * no road layers rendered, or a headless map. Every failure path just means
 * "use the axis-aligned fallback"; alignment is a nicety, never a dependency.
 */
function queryRoadBearingRad(
  map: MaplibreMap,
  at: [number, number],
  lat: number,
): number | null {
  try {
    if (!map.isStyleLoaded()) return null;
    const pt = map.project(at);
    const features = map.queryRenderedFeatures([
      [pt.x - 90, pt.y - 90],
      [pt.x + 90, pt.y + 90],
    ]);
    const cosLat = Math.cos((lat * Math.PI) / 180);
    let best: number | null = null;
    let bestLen = 0;
    for (const feature of features) {
      if (feature.layer.type !== "line") continue;
      if (!/road|street|path/i.test(feature.layer.id)) continue;
      if (feature.geometry.type !== "LineString") continue;
      const coords = feature.geometry.coordinates;
      for (let i = 0; i + 1 < coords.length; i += 1) {
        // Weight by true (meter) segment length so the LONGEST rendered road
        // segment wins — tile stubs and driveways lose to the through-road.
        const mx = (coords[i + 1][0] - coords[i][0]) * cosLat;
        const my = coords[i + 1][1] - coords[i][1];
        const len = Math.hypot(mx, my);
        if (len > bestLen) {
          bestLen = len;
          best = Math.atan2(my, mx);
        }
      }
    }
    return best;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// The live crowd
// ---------------------------------------------------------------------------

interface FanRuntime {
  marker: maplibregl.Marker;
  el: HTMLDivElement;
  plan: FanPlan;
  clump: ClumpPlan;
  routeM: RouteM;
  // DOM-write caches: only touch the element on meaningful change.
  lastOpacity: number;
  lastFaceLeft: boolean;
  lastX: number;
  lastY: number;
  posInit: boolean;
  // Mill state (initialized on arrival at the gate).
  millX: number;
  millY: number;
  millTX: number;
  millTY: number;
  millNextAt: number;
}

export interface FanCrowdOptions {
  map: MaplibreMap;
  lon: number;
  lat: number;
  color: string;
  /** The IntersectionObserver signal — the loop parks while this is false. */
  isVisible?: () => boolean;
  rand?: () => number;
  /** Align first legs to nearby roads when possible (default true). */
  alignRoads?: boolean;
}

export class FanCrowd {
  private readonly map: MaplibreMap;
  private readonly lon: number;
  private readonly lat: number;
  private readonly isVisible?: () => boolean;
  private readonly rand: () => number;
  private readonly fans: FanRuntime[] = [];
  private raf = 0;
  private pauseTimer = 0;
  private parkedAt = 0;
  private pausedMs = 0;
  private startTs = 0;
  private lastWrite = 0;
  private prevWrite = 0;
  private stopped = false;

  constructor(options: FanCrowdOptions) {
    const { map, lon, lat, color } = options;
    this.map = map;
    this.lon = lon;
    this.lat = lat;
    this.isVisible = options.isVisible;
    this.rand = options.rand ?? Math.random;
    const plan = buildCrowdPlan(lon, lat, {
      rand: this.rand,
      rotationForClump:
        options.alignRoads === false
          ? undefined
          : (_c, start) =>
              queryRoadBearingRad(map, toLngLat(start, lon, lat), lat) ?? 0,
    });
    for (const clump of plan.clumps) {
      const routeM = toRouteM(clump.route, lon, lat);
      for (const fan of clump.fans) {
        const el = createFanElement(
          color,
          true, // initial facing; corrected per leg on the first update
          fan.animDelaySec,
          fan.variant,
          Math.round(fan.sizePx * 2) / 2,
        );
        el.style.opacity = "0"; // fade in over the first walked meters
        const marker = new maplibregl.Marker({ element: el })
          .setLngLat(clump.route[0])
          .addTo(map);
        this.fans.push({
          marker,
          el,
          plan: fan,
          clump,
          routeM,
          lastOpacity: 0,
          lastFaceLeft: true,
          lastX: 0,
          lastY: 0,
          posInit: false,
          millX: 0,
          millY: 0,
          millTX: 0,
          millTY: 0,
          millNextAt: 0,
        });
      }
    }
  }

  get fanCount(): number {
    return this.fans.length;
  }

  /** Begin (idempotent). Returns `this` so callers can assign inline. */
  start(): this {
    if (this.stopped || this.raf !== 0) return this;
    this.raf = requestAnimationFrame(this.tick);
    return this;
  }

  /** Tear down the loop and remove every marker. Idempotent. */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    if (this.raf !== 0) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.pauseTimer !== 0) window.clearTimeout(this.pauseTimer);
    this.pauseTimer = 0;
    for (const fan of this.fans) fan.marker.remove();
    this.fans.length = 0;
  }

  private tick = (ts: number): void => {
    if (this.stopped) return;
    if (this.isVisible !== undefined && !this.isVisible()) {
      // The app keeps views mounted-but-hidden: park the loop ENTIRELY (no
      // rAF, no DOM writes) and poll for visibility on a cheap timer. The
      // parked span is subtracted from the clock so fans resume where they
      // were instead of teleporting forward.
      this.raf = 0;
      this.parkedAt = performance.now();
      this.pauseTimer = window.setTimeout(() => {
        this.pauseTimer = 0;
        if (this.stopped) return;
        this.pausedMs += performance.now() - this.parkedAt;
        this.raf = requestAnimationFrame(this.tick);
      }, PARK_POLL_MS);
      return;
    }
    if (this.startTs === 0) this.startTs = ts;
    if (ts - this.lastWrite < FRAME_MS) {
      this.raf = requestAnimationFrame(this.tick);
      return;
    }
    this.prevWrite = this.lastWrite;
    this.lastWrite = ts;
    const elapsed = ts - this.startTs - this.pausedMs;
    const zoom = this.map.getZoom();
    const zoomFade =
      zoom < ZOOM_FADE_MIN
        ? 0
        : zoom < ZOOM_FADE_FULL
          ? (zoom - ZOOM_FADE_MIN) / (ZOOM_FADE_FULL - ZOOM_FADE_MIN)
          : 1;
    let alive = false;
    for (const fan of this.fans) {
      if (this.updateFan(fan, elapsed, zoomFade)) alive = true;
    }
    if (!alive) {
      this.stop(); // everyone faded — terminal, do NOT reschedule
      return;
    }
    this.raf = requestAnimationFrame(this.tick);
  };

  /** Advance one fan; returns true while it's still part of the show. */
  private updateFan(fan: FanRuntime, elapsed: number, zoomFade: number): boolean {
    const t = elapsed - fan.clump.startDelayMs;
    if (t < 0) return true; // waiting for its clump's staggered start
    const walkMs = (fan.routeM.total / fan.plan.speedMps) * 1000;

    if (t < walkMs) {
      const d = fan.plan.speedMps * (t / 1000) + fan.plan.along;
      if (d < 0) return true; // trailing single-file members enter in sequence
      const p = pointAlong(fan.routeM, d);
      const dir = legDir(fan.routeM, p.leg);
      this.writePosition(
        fan,
        p.x - dir.y * fan.plan.lateral,
        p.y + dir.x * fan.plan.lateral,
      );
      this.writeFacing(fan, faceLeftForDir(dir.x));
      // Fade in over the first walked meters so spawns never pop.
      const fadeIn = Math.min(
        1,
        d / (fan.plan.speedMps * (FADE_IN_MS / 1000)),
      );
      this.writeOpacity(fan, 0.9 * fadeIn * zoomFade);
      return true;
    }

    const millT = t - walkMs;
    if (millT < fan.clump.lingerMs) {
      this.updateMill(fan, millT);
      this.writeOpacity(fan, 0.9 * zoomFade);
      return true;
    }

    const fadeT = millT - fan.clump.lingerMs;
    if (fadeT < FADE_OUT_MS) {
      // Keep drifting while fading — freezing mid-fade reads as a glitch.
      this.updateMill(fan, millT);
      this.writeOpacity(fan, 0.9 * (1 - fadeT / FADE_OUT_MS) * zoomFade);
      return true;
    }
    this.writeOpacity(fan, 0);
    return false;
  }

  /** Slow random-walk drift near the gate; targets keep the clump clustered. */
  private updateMill(fan: FanRuntime, millT: number): void {
    const lastLeg = fan.routeM.xs.length - 2;
    const dir = legDir(fan.routeM, lastLeg);
    if (fan.millNextAt === 0) {
      const gate = pointAlong(fan.routeM, fan.routeM.total);
      fan.millX = gate.x - dir.y * fan.plan.lateral;
      fan.millY = gate.y + dir.x * fan.plan.lateral;
      fan.millTX = fan.millX;
      fan.millTY = fan.millY;
      fan.millNextAt = 1; // retarget on the next line
    }
    if (millT >= fan.millNextAt) {
      const gate = pointAlong(fan.routeM, fan.routeM.total);
      const r = fan.clump.millRadiusM;
      // Wander near the gate, biased toward the fan's own side of the clump
      // so the group mills as a CLUSTER rather than collapsing to a point.
      fan.millTX =
        gate.x + (this.rand() - 0.5) * 2 * r - dir.y * fan.plan.lateral * 0.6;
      fan.millTY =
        gate.y + (this.rand() - 0.5) * 2 * r + dir.x * fan.plan.lateral * 0.6;
      fan.millNextAt = millT + 1400 + this.rand() * 1400;
    }
    const dx = fan.millTX - fan.millX;
    const dy = fan.millTY - fan.millY;
    const dist = Math.hypot(dx, dy);
    if (dist > 0.1) {
      const dtMs = this.prevWrite === 0 ? FRAME_MS : this.lastWrite - this.prevWrite;
      const step = Math.min(dist, MILL_MPS * (Math.min(100, dtMs) / 1000));
      fan.millX += (dx / dist) * step;
      fan.millY += (dy / dist) * step;
      this.writePosition(fan, fan.millX, fan.millY);
      this.writeFacing(fan, faceLeftForDir(dx / dist));
    }
  }

  private writePosition(fan: FanRuntime, x: number, y: number): void {
    if (
      fan.posInit &&
      Math.hypot(x - fan.lastX, y - fan.lastY) < POS_EPSILON_M
    ) {
      return;
    }
    fan.marker.setLngLat(toLngLat({ x, y }, this.lon, this.lat));
    fan.lastX = x;
    fan.lastY = y;
    fan.posInit = true;
  }

  private writeFacing(fan: FanRuntime, faceLeft: boolean | null): void {
    if (faceLeft === null || faceLeft === fan.lastFaceLeft) return;
    fan.lastFaceLeft = faceLeft;
    // The walk-cycle CSS keys off these classes (bob + flip), so swapping the
    // class changes direction without touching the SVG.
    fan.el.classList.toggle("sd-map-fan-l", faceLeft);
    fan.el.classList.toggle("sd-map-fan-r", !faceLeft);
  }

  private writeOpacity(fan: FanRuntime, opacity: number): void {
    const q = Math.round(opacity / OPACITY_STEP) * OPACITY_STEP;
    if (q === fan.lastOpacity) return;
    fan.lastOpacity = q;
    fan.el.style.opacity = String(q);
  }
}
