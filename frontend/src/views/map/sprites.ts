import type { Map as MaplibreMap, MapStyleImageMissingEvent } from "maplibre-gl";

// OpenFreeMap's tiles sometimes name POIs absent from its sprite atlas.
// Reuse a related atlas icon when available; otherwise show a neutral place
// marker. Labels still supply the exact place type/name.
const ALIASES: Record<string, string> = {
  sports_centre: "stadium",
  athletics: "stadium",
  multi: "pitch",
  disc_golf: "pitch",
  office: "commercial",
  atm: "bank",
  recycling: "waste_basket",
  gate: "entrance",
  lift_gate: "entrance",
};

function placeMarker() {
  const width = 24;
  const data = new Uint8Array(width * width * 4);
  for (let y = 0; y < width; y++) {
    for (let x = 0; x < width; x++) {
      const distance = Math.hypot(x - 11.5, y - 11.5);
      const i = (y * width + x) * 4;
      const halo = distance > 6;
      data[i] = halo ? 255 : 101;
      data[i + 1] = halo ? 255 : 100;
      data[i + 2] = halo ? 255 : 180;
      data[i + 3] = Math.round(Math.max(0, Math.min(1, 9 - distance)) * 255);
    }
  }
  return { width, height: width, data };
}

/** Install synchronously before tiles render; detach before removing the map. */
export function installMissingSprites(map: MaplibreMap): () => void {
  const fallback = placeMarker();
  const onMissing = ({ id }: MapStyleImageMissingEvent) => {
    if (!id || map.hasImage(id)) return;
    const alias = ALIASES[id];
    const source = alias && map.hasImage(alias) ? map.getImage(alias) : undefined;
    if (source?.data?.width && source.data.height) {
      map.addImage(id, source.data, { pixelRatio: source.pixelRatio, sdf: source.sdf });
    } else {
      map.addImage(id, fallback, { pixelRatio: 2 });
    }
  };
  map.on("styleimagemissing", onMissing);
  return () => { map.off("styleimagemissing", onMissing); };
}
