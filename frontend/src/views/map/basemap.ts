/**
 * Basemap switcher (vector ↔ satellite) for the map view.
 *
 * Satellite mode underlays Esri World Imagery raster tiles — keyless, no
 * token, so it fits the project's no-API-keys rule — BELOW the vector
 * layers, so roads, labels, and the 3D buildings keep drawing on top. The
 * layer is added/removed imperatively on the live style instead of swapping
 * styles with map.setStyle(): the current init hands MapLibre a style URL
 * (it never fetches the style JSON itself), and a setStyle swap would tear
 * down the GeoJSON travel arcs and the 3D-building fix-ups for no benefit.
 * The choice persists to localStorage, same pattern as the theme/ticker
 * prefs (see lib/theme.ts, lib/useTicker.ts).
 */
import type { Map as MaplibreMap } from "maplibre-gl";

export type BasemapId = "vector" | "satellite";

const BASEMAP_KEY = "sportsdash.basemap";

/** Read the persisted basemap; defaults to vector (storage-safe). */
export function readBasemap(): BasemapId {
  try {
    return window.localStorage.getItem(BASEMAP_KEY) === "satellite"
      ? "satellite"
      : "vector";
  } catch {
    return "vector";
  }
}

/** Persist the choice (best-effort — blocked storage just skips it). */
export function writeBasemap(id: BasemapId): void {
  try {
    window.localStorage.setItem(BASEMAP_KEY, id);
  } catch {
    // Private mode / disabled storage — the in-memory state still applies.
  }
}

/** Source/layer ids for the Esri imagery underlay. */
export const SATELLITE_SOURCE = "sd-esri-imagery";
export const SATELLITE_LAYER = "sd-esri-imagery";

/**
 * Esri World Imagery (keyless). The imagery ends at z19; MapLibre oversamples
 * beyond it, so street-level stadium zooms still show (blurrier) ground.
 */
const ESRI_TILES =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTRIBUTION = "Esri, Maxar, Earthstar Geographics";

/**
 * Add or remove the Esri imagery layer on the live style. It is inserted
 * directly above the style's background layer (below every vector layer) so
 * roads/labels/extrusions keep drawing on top, and removed outright in vector
 * mode so no imagery requests run in the background and the Esri attribution
 * leaves the corner control. Returns false when the style isn't loaded yet
 * (the caller retries once it settles); no-ops (true) when the live state
 * already matches.
 */
export function setSatelliteImagery(map: MaplibreMap, on: boolean): boolean {
  if (!map.isStyleLoaded()) return false;
  const present = map.getLayer(SATELLITE_LAYER) !== undefined;
  if (on === present) return true;
  if (on) {
    if (map.getSource(SATELLITE_SOURCE) === undefined) {
      map.addSource(SATELLITE_SOURCE, {
        type: "raster",
        tiles: [ESRI_TILES],
        tileSize: 256,
        maxzoom: 19,
        attribution: ESRI_ATTRIBUTION,
      });
    }
    const firstVector = (map.getStyle().layers ?? []).find(
      (layer) => layer.type !== "background",
    );
    map.addLayer(
      {
        id: SATELLITE_LAYER,
        type: "raster",
        source: SATELLITE_SOURCE,
        paint: { "raster-opacity": 1 },
      },
      firstVector?.id,
    );
  } else {
    map.removeLayer(SATELLITE_LAYER);
    if (map.getSource(SATELLITE_SOURCE) !== undefined) {
      map.removeSource(SATELLITE_SOURCE);
    }
  }
  return true;
}
