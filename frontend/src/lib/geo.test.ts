import { describe, expect, it } from "vitest";

import { greatCircle, hasCoords, haversineKm } from "./geo";

describe("hasCoords", () => {
  it("rejects NaN/null-ish coordinates from mid-geocode payloads", () => {
    expect(hasCoords({ lat: 40.7, lon: -74.0 })).toBe(true);
    expect(hasCoords({ lat: NaN, lon: -74.0 })).toBe(false);
    expect(hasCoords({ lat: 40.7, lon: Infinity })).toBe(false);
  });
});

describe("haversineKm", () => {
  it("matches the known NYC–LA great-circle distance (~3,940 km)", () => {
    const km = haversineKm(40.7128, -74.006, 34.0522, -118.2437);
    expect(km).toBeGreaterThan(3900);
    expect(km).toBeLessThan(3990);
  });

  it("is zero for coincident points", () => {
    expect(haversineKm(51.5, -0.1, 51.5, -0.1)).toBe(0);
  });
});

describe("greatCircle", () => {
  it("returns null for effectively coincident points", () => {
    expect(greatCircle(-0.1, 51.5, -0.1, 51.5)).toBeNull();
  });

  it("starts and ends at the given points", () => {
    const line = greatCircle(-74.006, 40.7128, -118.2437, 34.0522);
    expect(line).not.toBeNull();
    const [firstLon, firstLat] = line![0];
    const [lastLon, lastLat] = line![line!.length - 1];
    expect(firstLon).toBeCloseTo(-74.006, 3);
    expect(firstLat).toBeCloseTo(40.7128, 3);
    expect(lastLon).toBeCloseTo(-118.2437, 3);
    expect(lastLat).toBeCloseTo(34.0522, 3);
  });

  it("unwraps longitudes across the antimeridian (no ~360° jumps)", () => {
    // Tokyo → Los Angeles crosses the antimeridian.
    const line = greatCircle(139.65, 35.68, -118.2437, 34.0522);
    expect(line).not.toBeNull();
    for (let i = 1; i < line!.length; i += 1) {
      expect(Math.abs(line![i][0] - line![i - 1][0])).toBeLessThan(180);
    }
  });
});
