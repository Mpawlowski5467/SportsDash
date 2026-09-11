import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Map as MaplibreMap } from "maplibre-gl";
import { FlightCinematic } from "./cinematic";
import type { CameraOrbit } from "./orbit";

const state = vi.hoisted(() => ({
  planes: [] as { remove: ReturnType<typeof vi.fn>; setLngLat: ReturnType<typeof vi.fn> }[],
  crowds: [] as { stop: ReturnType<typeof vi.fn> }[],
}));
vi.mock("./markers", () => ({ createPlaneElement: () => ({ el: { classList: { add: vi.fn() } }, glyph: { style: {} } }) }));
vi.mock("./tilewarm", () => ({ warmTilesAlong: vi.fn(), warmTilesAround: vi.fn() }));
vi.mock("./fans", () => ({ FanCrowd: class {
  stop = vi.fn();
  constructor() { state.crowds.push(this); }
  start() { return this; }
} }));
vi.mock("maplibre-gl", () => ({ default: { Marker: class {
  remove = vi.fn();
  setLngLat = vi.fn().mockReturnThis();
  constructor() { state.planes.push(this); }
  addTo() { return this; }
} } }));

beforeEach(() => {
  state.planes.length = 0;
  state.crowds.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(() => fn(performance.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function setup() {
  const listeners = new Set<() => void>();
  const layers = new Set<string>();
  const sources = new Map();
  const map = {
    once: (_: string, callback: () => void) => listeners.add(callback),
    off: (_: string, callback: () => void) => listeners.delete(callback),
    stop: vi.fn(), flyTo: vi.fn(), jumpTo: vi.fn(), easeTo: vi.fn(),
    isStyleLoaded: () => true,
    project: ([x, y]: [number, number]) => ({ x, y: -y }),
    getLayer: (id: string) => layers.has(id),
    addLayer: ({ id }: { id: string }) => layers.add(id),
    removeLayer: (id: string) => layers.delete(id),
    getSource: (id: string) => sources.get(id),
    addSource: (id: string) => sources.set(id, { setData: vi.fn() }),
    removeSource: (id: string) => sources.delete(id),
  };
  const prefs = { visible: true, reduced: false };
  const orbit = { stop: vi.fn(), startAfterSettled: vi.fn() };
  const flight = new FlightCinematic({
    map: map as unknown as MaplibreMap, orbit: orbit as unknown as CameraOrbit,
    isVisible: () => prefs.visible, reducedMotion: () => prefs.reduced,
    stadiumZoom: 15.5, stadiumPitch: 55,
  });
  const settle = () => { const pending = [...listeners]; listeners.clear(); pending.forEach(fn => fn()); };
  const start = () => flight.start([-71, 42], [-72, 43], "#2563eb");
  return { flight, map, prefs, listeners, layers, sources, settle, start, orbit };
}

it("detaches cancelled intros before starting another flight", () => {
  const s = setup();
  s.start();
  expect(s.flight.flying).toBe(true);
  s.start();
  expect(s.listeners.size).toBe(1);
  expect(state.planes[0].remove).toHaveBeenCalledOnce();
  s.flight.stop();
  s.settle();
  expect(vi.getTimerCount()).toBe(0);
  expect(s.layers.size + s.sources.size).toBe(0);
});

it("lands exactly at the destination and cleans the plane before the crowd", () => {
  const s = setup();
  s.start(); s.settle();
  vi.advanceTimersByTime(14_000);
  expect(state.planes[0].setLngLat).toHaveBeenLastCalledWith([-72, 43]);
  expect(state.planes[0].remove).toHaveBeenCalledOnce();
  expect(state.crowds).toHaveLength(1);
  expect(state.crowds[0].stop).not.toHaveBeenCalled();
  expect(s.orbit.startAfterSettled).toHaveBeenCalledOnce();
  s.flight.stop(); s.flight.stop();
  expect(state.crowds[0].stop).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["visible", "reduced"] as const)("cancels an active flight when %s changes", preference => {
  const s = setup(); s.start(); s.settle();
  vi.advanceTimersByTime(100);
  s.prefs[preference] = preference === "reduced";
  vi.advanceTimersByTime(100);
  expect(s.flight.flying).toBe(false);
  expect(state.planes[0].remove).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it("skips aircraft and crowds when reduced motion is already enabled", () => {
  const s = setup(); s.prefs.reduced = true; s.start();
  expect(state.planes).toHaveLength(0);
  expect(s.map.flyTo).toHaveBeenCalledWith(expect.objectContaining({ center: [-72, 43], essential: false }));
  expect(s.listeners.size).toBe(0);
});
