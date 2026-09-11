import { expect, it, vi } from "vitest";
import type { Map as MaplibreMap, MapStyleImageMissingEvent } from "maplibre-gl";
import { installMissingSprites } from "./sprites";

it("fills missing icons without replacing existing sprites and detaches cleanly", () => {
  const original = { data: { width: 2, height: 2, data: new Uint8Array(16) }, pixelRatio: 1, sdf: true };
  const images = new Map<string, unknown>([["stadium", original]]);
  let onMissing: (event: MapStyleImageMissingEvent) => void;
  const map = {
    on: (_: string, callback: typeof onMissing) => { onMissing = callback; }, off: vi.fn(),
    hasImage: (id: string) => images.has(id), getImage: (id: string) => images.get(id),
    addImage: vi.fn((id: string, data: unknown) => { images.set(id, data); }),
  };
  const detach = installMissingSprites(map as unknown as MaplibreMap);
  const missing = (id: string) => onMissing({ id } as MapStyleImageMissingEvent);
  missing("sports_centre");
  expect(map.addImage).toHaveBeenLastCalledWith("sports_centre", original.data, { pixelRatio: 1, sdf: true });
  missing("unknown_place");
  const fallback = images.get("unknown_place") as { data: Uint8Array; width: number };
  expect(fallback.width).toBe(24);
  expect(fallback.data.some((value, index) => index % 4 === 3 && value > 0)).toBe(true);
  missing("stadium"); missing("unknown_place"); missing("");
  expect(map.addImage).toHaveBeenCalledTimes(2);
  expect(images.get("stadium")).toBe(original);
  detach();
  expect(map.off).toHaveBeenCalledWith("styleimagemissing", expect.any(Function));
});
