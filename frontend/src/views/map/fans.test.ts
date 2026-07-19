/**
 * Pure crowd-planning math (clump layout, Manhattan routes, distance-along-
 * route lookups, facing) — tested without MapLibre or the DOM. Uses a seeded
 * RNG so the randomized plan is deterministic. All coordinates are fictional.
 */
import { describe, expect, it } from "vitest";

import {
  buildCrowdPlan,
  faceLeftForDir,
  legDir,
  pointAlong,
  toRouteM,
} from "./fans";

/** mulberry32 — tiny deterministic PRNG for reproducible plans. */
function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const LON = -71.1; // fictional venue
const LAT = 42.4;

describe("buildCrowdPlan", () => {
  it("lays out 3-4 clumps totalling 12-18 fans for many seeds", () => {
    for (let seed = 1; seed <= 40; seed += 1) {
      const plan = buildCrowdPlan(LON, LAT, { rand: seeded(seed) });
      expect(plan.clumps.length).toBeGreaterThanOrEqual(3);
      expect(plan.clumps.length).toBeLessThanOrEqual(4);
      expect(plan.fanCount).toBeGreaterThanOrEqual(12);
      expect(plan.fanCount).toBeLessThanOrEqual(18);
      for (const clump of plan.clumps) {
        expect(clump.fans.length).toBeGreaterThanOrEqual(2);
        expect(clump.route).toHaveLength(3); // start, corner, gate
      }
    }
  });

  it("produces axis-aligned Manhattan legs when the rotation is 0", () => {
    const plan = buildCrowdPlan(LON, LAT, {
      rand: seeded(7),
      rotationForClump: () => 0,
    });
    for (const clump of plan.clumps) {
      const r = toRouteM(clump.route, LON, LAT);
      // Corner shares one axis with the start and the other with the gate.
      const uFirst =
        Math.abs(r.ys[1] - r.ys[0]) < 1e-6 && Math.abs(r.xs[1] - r.xs[2]) < 1e-6;
      const vFirst =
        Math.abs(r.xs[1] - r.xs[0]) < 1e-6 && Math.abs(r.ys[1] - r.ys[2]) < 1e-6;
      expect(uFirst || vFirst).toBe(true);
    }
  });

  it("keeps consecutive legs perpendicular under a rotated frame", () => {
    const plan = buildCrowdPlan(LON, LAT, {
      rand: seeded(11),
      rotationForClump: () => 0.7,
    });
    for (const clump of plan.clumps) {
      const r = toRouteM(clump.route, LON, LAT);
      const d0 = legDir(r, 0);
      const d1 = legDir(r, 1);
      expect(d0.x * d1.x + d0.y * d1.y).toBeCloseTo(0, 6);
    }
  });

  it("ends routes at the stadium edge and starts them a walk away", () => {
    const plan = buildCrowdPlan(LON, LAT, { rand: seeded(3) });
    for (const clump of plan.clumps) {
      const r = toRouteM(clump.route, LON, LAT);
      const gateDist = Math.hypot(r.xs[2], r.ys[2]);
      expect(gateDist).toBeGreaterThanOrEqual(60);
      expect(gateDist).toBeLessThanOrEqual(130);
      const startDist = Math.hypot(r.xs[0], r.ys[0]);
      expect(startDist).toBeGreaterThanOrEqual(280);
      expect(startDist).toBeLessThanOrEqual(410);
      // ~15-30s trips at an effective 14-19 m/s walk.
      expect(r.total).toBeGreaterThanOrEqual(120);
      expect(r.total).toBeLessThanOrEqual(600);
    }
  });

  it("staggers clump starts so the flow is continuous", () => {
    const plan = buildCrowdPlan(LON, LAT, { rand: seeded(5) });
    for (let i = 1; i < plan.clumps.length; i += 1) {
      expect(plan.clumps[i].startDelayMs).toBeGreaterThan(
        plan.clumps[i - 1].startDelayMs,
      );
    }
  });
});

describe("pointAlong", () => {
  const route: [number, number][] = [
    [LON, LAT],
    [LON + 0.002, LAT],
    [LON + 0.002, LAT + 0.001],
  ];
  const r = toRouteM(route, LON, LAT);

  it("walks the full polyline by cumulative distance", () => {
    const leg1 = r.cum[1];
    const leg2 = r.cum[2] - r.cum[1];
    expect(r.total).toBeCloseTo(leg1 + leg2, 9);

    const start = pointAlong(r, 0);
    expect(start.x).toBeCloseTo(r.xs[0], 9);
    expect(start.y).toBeCloseTo(r.ys[0], 9);
    expect(start.leg).toBe(0);

    const end = pointAlong(r, r.total);
    expect(end.x).toBeCloseTo(r.xs[2], 9);
    expect(end.y).toBeCloseTo(r.ys[2], 9);

    const midFirst = pointAlong(r, leg1 / 2);
    expect(midFirst.leg).toBe(0);
    expect(midFirst.x).toBeCloseTo(r.xs[0] + (r.xs[1] - r.xs[0]) / 2, 9);

    const midSecond = pointAlong(r, leg1 + leg2 / 2);
    expect(midSecond.leg).toBe(1);
    expect(midSecond.y).toBeCloseTo(r.ys[1] + (r.ys[2] - r.ys[1]) / 2, 9);
  });

  it("clamps distances outside the route", () => {
    expect(pointAlong(r, -10).x).toBeCloseTo(r.xs[0], 9);
    expect(pointAlong(r, r.total + 10).x).toBeCloseTo(r.xs[2], 9);
  });
});

describe("faceLeftForDir", () => {
  it("flips on the dominant east/west component only", () => {
    expect(faceLeftForDir(-1)).toBe(true);
    expect(faceLeftForDir(1)).toBe(false);
    expect(faceLeftForDir(0)).toBeNull(); // north/south leg: keep facing
    expect(faceLeftForDir(0.2)).toBeNull();
    expect(faceLeftForDir(-0.9)).toBe(true);
  });
});
