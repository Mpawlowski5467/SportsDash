import maplibregl from "maplibre-gl";
import type { FeatureCollection, LineString } from "geojson";
import { greatCircle } from "../../lib/geo";
import { FanCrowd } from "./fans";
import { createPlaneElement } from "./markers";
import { screenHeading } from "./motion";
import type { CameraOrbit } from "./orbit";
import { warmTilesAlong, warmTilesAround } from "./tilewarm";

type LngLat = [number, number];
const ARC = "sd-cine-arc";
const DURATION = 12_000;
const CAMERA_UPDATE_MS = 1000 / 24;

interface Options {
  map: maplibregl.Map;
  orbit: CameraOrbit;
  isVisible: () => boolean;
  reducedMotion: () => boolean;
  stadiumZoom: number;
  stadiumPitch: number;
}

/** Owns one flight: intro listener, camera frames, route, aircraft and crowd.
 * All resources leave together on stop, including a cancelled intro sweep.
 * React only supplies the latest visibility/motion preferences and target.
 */
export class FlightCinematic {
  private raf = 0;
  private plane: maplibregl.Marker | null = null;
  private crowd: FanCrowd | null = null;
  private cleanupTimer = 0;
  private detachIntro: (() => void) | null = null;

  constructor(private readonly options: Options) {}

  get flying(): boolean { return this.raf !== 0 || this.detachIntro !== null; }

  stop(): void {
    this.detachIntro?.();
    this.detachIntro = null;
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.cleanupTimer) window.clearTimeout(this.cleanupTimer);
    this.raf = 0;
    this.cleanupTimer = 0;
    this.plane?.remove();
    this.plane = null;
    this.crowd?.stop();
    this.crowd = null;
    this.setArc(null);
    this.options.orbit.stop();
  }

  start(home: LngLat, dest: LngLat, color: string): void {
    this.stop();
    const { map, stadiumZoom, stadiumPitch, reducedMotion, isVisible, orbit } = this.options;
    const route = greatCircle(home[0], home[1], dest[0], dest[1]);
    if (route === null || reducedMotion()) {
      map.flyTo({ center: dest, zoom: stadiumZoom, pitch: stadiumPitch, essential: false });
      return;
    }
    // Finish the previous camera move before listening for this intro's end.
    map.stop();
    const { el, glyph } = createPlaneElement(color);
    el.classList.add("sd-map-plane-cine");
    const plane = new maplibregl.Marker({ element: el }).setLngLat(route[0]).addTo(map);
    this.plane = plane;
    this.setArc(route);
    warmTilesAround(map, dest[0], dest[1]);
    warmTilesAlong(map, route);
    let start = 0;
    let lastCam = 0;
    let fansStarted = false;
    const tick = (ts: number) => {
      if (this.plane !== plane) return;
      if (!isVisible() || reducedMotion()) { this.stop(); return; }
      if (start === 0) start = ts;
      const p = Math.min(1, (ts - start) / DURATION);
      const e = p * p * (3 - 2 * p);
      const fi = e * (route.length - 1);
      const idx = Math.min(Math.floor(fi), route.length - 2);
      const frac = fi - idx;
      const a = route[idx];
      const b = route[idx + 1];
      const pos: LngLat = [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac];
      // Cruise uses a shallow pitch and z5 tiles. Detail tiles are warmed
      // before the late dive; camera work is throttled while the marker
      // continues at display cadence.
      const dive = Math.max(0, (p - 0.8) / 0.2);
      if (ts - lastCam >= CAMERA_UPDATE_MS || p === 1) {
        lastCam = ts;
        map.jumpTo({
          center: pos, zoom: 5.5 + (stadiumZoom - 5.5) * dive ** 1.4,
          pitch: 30 + (stadiumPitch - 30) * dive, bearing: -10,
        });
      }
      plane.setLngLat(pos);
      glyph.style.transform = `rotate(${screenHeading(point => map.project(point), a, b, pos)}deg)`;
      if (!fansStarted && p > 0.62) {
        fansStarted = true;
        this.crowd = new FanCrowd({ map, lon: dest[0], lat: dest[1], color, isVisible }).start();
      }
      if (p < 1) {
        this.raf = requestAnimationFrame(tick);
      } else {
        this.raf = 0;
        map.easeTo({ center: dest, zoom: stadiumZoom, pitch: stadiumPitch, duration: 700 });
        orbit.startAfterSettled(() => !reducedMotion() && isVisible());
        this.cleanupTimer = window.setTimeout(() => {
          this.cleanupTimer = 0;
          this.plane?.remove();
          this.plane = null;
          this.setArc(null);
        }, 1600);
      }
    };
    const begin = () => {
      this.detachIntro = null;
      if (this.plane === plane) this.raf = requestAnimationFrame(tick);
    };
    this.detachIntro = () => { map.off("moveend", begin); };
    map.once("moveend", begin);
    // Attach first: a no-distance/reduced-duration move may end synchronously.
    map.flyTo({ center: home, zoom: 5.5, pitch: 30, bearing: -10, duration: 1400 });
  }

  private setArc(route: LngLat[] | null): void {
    const { map } = this.options;
    if (!map.isStyleLoaded()) return;
    if (route === null) {
      if (map.getLayer(ARC)) map.removeLayer(ARC);
      if (map.getSource(ARC)) map.removeSource(ARC);
      return;
    }
    const data: FeatureCollection<LineString> = {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: route } }],
    };
    const source = map.getSource(ARC) as maplibregl.GeoJSONSource | undefined;
    if (source) { source.setData(data); return; }
    map.addSource(ARC, { type: "geojson", data });
    map.addLayer({
      id: ARC, type: "line", source: ARC,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#f59e0b", "line-width": 2.5, "line-opacity": 0.9 },
    });
  }
}
