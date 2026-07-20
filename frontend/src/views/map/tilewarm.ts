/**
 * Destination tile pre-warming for the flight cinematic.
 *
 * The flight's remaining stutter isn't the camera math — it's NEW-CITY
 * vector tiles streaming in mid-flight: each fetch + parse spikes a frame
 * (measured p95 ~140ms during the dive into a dense stadium zoom). The
 * camera path is known up front, so while the plane cruises at low zoom we
 * fetch the DESTINATION's detail tiles into the browser HTTP cache; when
 * the dive later asks MapLibre for the same URLs they come back instantly
 * and the parse work spreads instead of bursting.
 *
 * No MapLibre API exists for prefetch (tile loading is viewport-driven),
 * so this warms the shared HTTP cache with plain `fetch`. Everything
 * degrades silently: no template, no cache headers, or a failed fetch just
 * means the flight behaves exactly as before.
 */
import type { Map as MaplibreMap } from "maplibre-gl";

/** Slippy-map tile coordinates for a lng/lat at zoom z. */
function tileX(lon: number, z: number): number {
  return Math.floor(((lon + 180) / 360) * 2 ** z);
}
function tileY(lat: number, z: number): number {
  const rad = (lat * Math.PI) / 180;
  return Math.floor(
    ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * 2 ** z,
  );
}

let cachedTemplate: string | null | undefined;

/**
 * Resolve the vector tiles' URL template ({z}/{x}/{y}) from the map style:
 * the style's vector source either lists `tiles` directly or points at a
 * TileJSON document that does. Resolved once per session.
 */
async function tileUrlTemplate(map: MaplibreMap): Promise<string | null> {
  if (cachedTemplate !== undefined) return cachedTemplate;
  cachedTemplate = null;
  try {
    const style = map.getStyle();
    const vectorSource = Object.values(style.sources).find(
      (s) => s.type === "vector",
    ) as { tiles?: string[]; url?: string } | undefined;
    if (vectorSource === undefined) return null;
    if (Array.isArray(vectorSource.tiles) && vectorSource.tiles.length > 0) {
      cachedTemplate = vectorSource.tiles[0];
    } else if (typeof vectorSource.url === "string") {
      const res = await fetch(vectorSource.url);
      if (res.ok) {
        const tilejson = (await res.json()) as { tiles?: string[] };
        if (Array.isArray(tilejson.tiles) && tilejson.tiles.length > 0) {
          cachedTemplate = tilejson.tiles[0];
        }
      }
    }
  } catch {
    cachedTemplate = null; // offline style fetch — no warming, no harm
  }
  return cachedTemplate;
}

/**
 * Kick off background fetches for the tiles covering `lon,lat` (plus a
 * one-tile margin) at the dive's detail zooms. Resolves the template, then
 * fire-and-forget — the flight must never wait on warming.
 */
export function warmTilesAround(
  map: MaplibreMap,
  lon: number,
  lat: number,
  zooms: number[] = [13, 14, 15],
  margin = 1,
): void {
  void tileUrlTemplate(map).then((template) => {
    if (template === null) return;
    for (const z of zooms) {
      const cx = tileX(lon, z);
      const cy = tileY(lat, z);
      for (let x = cx - margin; x <= cx + margin; x += 1) {
        for (let y = cy - margin; y <= cy + margin; y += 1) {
          warmFetch(template, z, x, y);
        }
      }
    }
  });
}

/**
 * Same warming along the flight's great-circle route at the cruise zooms.
 * The 12s flight crosses ~2,000km; at z5/z6 that's only a few dozen tiles,
 * all fetchable during the 1.4s intro sweep — without this, every second
 * of the cruise pulls fresh tiles over the wing (the mid-flight spikes
 * that survived destination-only warming).
 */
export function warmTilesAlong(
  map: MaplibreMap,
  route: [number, number][],
  zooms: number[] = [5, 6],
  margin = 0,
): void {
  void tileUrlTemplate(map).then((template) => {
    if (template === null) return;
    const seen = new Set<string>();
    for (const z of zooms) {
      for (const [lon, lat] of route) {
        const cx = tileX(lon, z);
        const cy = tileY(lat, z);
        for (let x = cx - margin; x <= cx + margin; x += 1) {
          for (let y = cy - margin; y <= cy + margin; y += 1) {
            const key = `${z}/${x}/${y}`;
            if (seen.has(key)) continue;
            seen.add(key);
            warmFetch(template, z, x, y);
          }
        }
      }
    }
  });
}

/** One fire-and-forget tile fetch into the shared HTTP cache. */
function warmFetch(template: string, z: number, x: number, y: number): void {
  const url = template
    .replace("{z}", String(z))
    .replace("{x}", String(x))
    .replace("{y}", String(y));
  // Errors are expected (offline, edge tiles) and ignored by design.
  fetch(url).catch(() => {});
}
