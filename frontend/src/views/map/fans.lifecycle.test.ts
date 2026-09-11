import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Map as MaplibreMap } from "maplibre-gl";
import { FanCrowd } from "./fans";

type FigureElement = { style: { opacity: string }; firstElementChild: { style: { opacity: string } } };
const markers = vi.hoisted(() => [] as { el: FigureElement; remove: ReturnType<typeof vi.fn> }[]);
vi.mock("./markers", () => ({
  createFanElement: () => ({ style: {}, firstElementChild: { style: {} }, classList: { toggle: vi.fn() } }),
}));
vi.mock("maplibre-gl", () => ({
  default: {
    Marker: class {
      el;
      remove = vi.fn();
      constructor({ element }: { element: FigureElement }) {
        this.el = element;
        markers.push(this);
      }
      setLngLat() { this.el.style.opacity = "1"; return this; }
      addTo() { return this; }
    },
  },
}));

beforeEach(() => {
  markers.length = 0;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(() => fn(performance.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function crowd(isVisible: () => boolean) {
  return new FanCrowd({
    map: { getZoom: () => 16, getBearing: () => 0 } as MaplibreMap,
    lon: -71, lat: 42, color: "#2563eb", alignRoads: false,
    rand: () => .5, isVisible,
  });
}

it("starts promptly after being created in a hidden tab", () => {
  let visible = false;
  const fans = crowd(() => visible).start();
  vi.advanceTimersByTime(60_000);
  visible = true;
  vi.advanceTimersByTime(3_000);
  expect(markers.some(m => Number(m.el.firstElementChild.style.opacity) > 0)).toBe(true);
  fans.stop();
  expect(vi.getTimerCount()).toBe(0);
});

it("keeps fades on the artwork when MapLibre resets marker opacity", () => {
  const fans = crowd(() => true).start();
  vi.advanceTimersByTime(700);
  expect(markers.every(m => m.el.style.opacity === "1")).toBe(true);
  expect(markers.some(m => Number(m.el.firstElementChild.style.opacity) === 0)).toBe(true);
  expect(markers.some(m => Number(m.el.firstElementChild.style.opacity) > 0 && Number(m.el.firstElementChild.style.opacity) < .9)).toBe(true);
  fans.stop();
});

it("does not stack loops when restarted while parked, and removes every marker once", () => {
  const fans = crowd(() => false).start();
  vi.advanceTimersByTime(16);
  fans.start();
  expect(vi.getTimerCount()).toBe(1);
  fans.stop();
  fans.stop();
  fans.start();
  expect(vi.getTimerCount()).toBe(0);
  expect(markers.every(m => m.remove.mock.calls.length === 1)).toBe(true);
});
