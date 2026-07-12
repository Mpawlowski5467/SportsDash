/**
 * Pure spherical-geometry helpers for the map view — extracted from
 * MapView.tsx so the math is unit-testable without MapLibre or the DOM.
 */

export const toRad = (deg: number): number => (deg * Math.PI) / 180;
export const toDeg = (rad: number): number => (rad * 180) / Math.PI;

/** Samples along each great-circle arc (more = smoother long-haul curves). */
export const GREAT_CIRCLE_SAMPLES = 48;

/** Finite-coordinate guard (the payload can carry nulls/NaN during geocoding). */
export function hasCoords(p: { lat: number; lon: number }): boolean {
  return Number.isFinite(p.lat) && Number.isFinite(p.lon);
}

/**
 * Sample a great-circle path from (lon1,lat1) to (lon2,lat2) via spherical
 * linear interpolation (slerp), returning a list of [lon, lat] positions.
 * Returns null when the two points are effectively coincident.
 */
export function greatCircle(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number,
  samples: number = GREAT_CIRCLE_SAMPLES,
): [number, number][] | null {
  const phi1 = toRad(lat1);
  const phi2 = toRad(lat2);
  const lam1 = toRad(lon1);
  const lam2 = toRad(lon2);

  // Angular distance (haversine), then slerp between the two endpoints.
  const dPhi = phi2 - phi1;
  const dLam = lam2 - lam1;
  const h =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLam / 2) ** 2;
  const d = 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  if (d < 1e-6) return null;
  const sinD = Math.sin(d);

  const points: [number, number][] = [];
  let prevLon: number | null = null;
  for (let i = 0; i <= samples; i += 1) {
    const f = i / samples;
    const A = Math.sin((1 - f) * d) / sinD;
    const B = Math.sin(f * d) / sinD;
    const x = A * Math.cos(phi1) * Math.cos(lam1) + B * Math.cos(phi2) * Math.cos(lam2);
    const y = A * Math.cos(phi1) * Math.sin(lam1) + B * Math.cos(phi2) * Math.sin(lam2);
    const z = A * Math.sin(phi1) + B * Math.sin(phi2);
    const phi = Math.atan2(z, Math.hypot(x, y));
    const lam = Math.atan2(y, x);
    let lonDeg = toDeg(lam);
    // Unwrap longitude so consecutive samples never jump ~360° across the
    // antimeridian — otherwise a trans-Pacific arc renders as a flat streak.
    // MapLibre renders out-of-[-180,180] longitudes wrapped correctly.
    if (prevLon !== null) {
      while (lonDeg - prevLon > 180) lonDeg -= 360;
      while (lonDeg - prevLon < -180) lonDeg += 360;
    }
    prevLon = lonDeg;
    points.push([lonDeg, toDeg(phi)]);
  }
  return points;
}

/** Great-circle distance in km between two lat/lon points (haversine). */
export function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}
