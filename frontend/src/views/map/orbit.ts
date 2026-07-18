/**
 * CameraOrbit — the slow cinematic orbit that begins after the camera flies
 * into a stadium (pin click, game click, or the flight cinematic's landing
 * dive). Only the bearing sweeps; center, zoom, and pitch stay wherever the
 * fly-in left them, so the stadium remains framed while the 3D buildings
 * parallax around it.
 *
 * Lifecycle rules (mirroring the flight cinematic's conventions):
 * - starts only once the in-flight camera move settles (the next `moveend`),
 *   with an ease-in ramp so there's no jolt;
 * - stops immediately on any USER camera move (drag/wheel/touch carry an
 *   `originalEvent` on `movestart`; programmatic moves don't) or on a canvas
 *   keydown (keyboard pan/zoom);
 * - callers stop it explicitly for everything else: another pin click, panel
 *   close, mode switch, tab hide, unmount;
 * - reduced-motion callers never schedule it (the `allow` gate is re-checked
 *   at start time so a mid-flight preference flip still wins).
 */
import type { Map as MaplibreMap, MapLibreEvent } from "maplibre-gl";

/** 5°/s ≈ one full lap every 72s — slow enough to read as a hover. */
const ORBIT_DEG_PER_SEC = 5;
/** Ease-in ramp: orbit speed scales 0→full over this long after starting. */
const ORBIT_RAMP_MS = 1200;
/**
 * Camera-update cadence. Every jumpTo is a FULL style render — with 3D
 * buildings visible that's the orbit's entire cost (measured ~13fps
 * unthrottled) — so we sweep at 15fps: still smooth for a slow 5°/s
 * hover, at roughly a quarter of the render load. The sweep angle
 * accumulates by true elapsed time, so the visual speed is
 * frame-rate independent.
 */
const ORBIT_UPDATE_MS = 1000 / 15;

type MoveStart = MapLibreEvent<MouseEvent | TouchEvent | WheelEvent | undefined>;

export class CameraOrbit {
  private raf = 0;
  /** Detaches the listeners of a scheduled (not yet started) orbit. */
  private detachPending: (() => void) | null = null;
  /** Detaches the user-interrupt listeners of a running orbit. */
  private detachUserStop: (() => void) | null = null;

  constructor(private readonly map: MaplibreMap) {}

  /** True while orbiting OR while a start is scheduled (post-fly moveend). */
  get active(): boolean {
    return this.raf !== 0 || this.detachPending !== null;
  }

  /** True only while the rAF loop drives the camera (used to skip reclustering
   *  on the orbit's own per-frame moveend, like the cinematic's guard). */
  get orbiting(): boolean {
    return this.raf !== 0;
  }

  /**
   * Schedule the orbit to begin once the current camera move settles (the
   * next `moveend` — the fly/ease that brought us here). `allow` is evaluated
   * at start time, not schedule time. A user move before settle cancels the
   * scheduled start; any previous orbit/schedule is replaced.
   */
  startAfterSettled(allow: () => boolean = () => true): void {
    this.stop();
    const onUserMove = (e: MoveStart) => {
      if (e.originalEvent !== undefined) this.stop();
    };
    const onSettled = () => {
      this.detachPending = null;
      this.map.off("movestart", onUserMove);
      if (allow()) this.begin();
    };
    this.map.once("moveend", onSettled);
    this.map.on("movestart", onUserMove);
    this.detachPending = () => {
      this.map.off("moveend", onSettled);
      this.map.off("movestart", onUserMove);
    };
  }

  /** Stop immediately and detach everything. Idempotent. */
  stop(): void {
    this.detachPending?.();
    this.detachPending = null;
    this.detachUserStop?.();
    this.detachUserStop = null;
    if (this.raf !== 0) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private begin(): void {
    // Orbit the point the fly-in framed, not wherever the center later drifts
    // to — the stadium stays centered for the whole sweep.
    const center = this.map.getCenter();
    let bearing = this.map.getBearing();
    const onUserMove = (e: MoveStart) => {
      if (e.originalEvent !== undefined) this.stop();
    };
    // Keyboard pan/zoom doesn't reliably carry an originalEvent on movestart,
    // so the canvas keydown stops the orbit too.
    const onKeyDown = () => this.stop();
    this.map.on("movestart", onUserMove);
    this.map.getCanvas().addEventListener("keydown", onKeyDown);
    this.detachUserStop = () => {
      this.map.off("movestart", onUserMove);
      this.map.getCanvas().removeEventListener("keydown", onKeyDown);
    };
    let start = 0;
    let lastJump = 0;
    const tick = (ts: number) => {
      if (start === 0) {
        start = ts;
        lastJump = ts;
      }
      // Camera updates are throttled (see ORBIT_UPDATE_MS); the sweep angle
      // accumulates by true elapsed time so 5°/s holds at any frame rate.
      const sinceJump = ts - lastJump;
      if (sinceJump >= ORBIT_UPDATE_MS) {
        // Clamp so a background-tab rAF gap doesn't jump the bearing.
        const step = Math.min(250, sinceJump);
        lastJump = ts;
        const t = Math.min(1, (ts - start) / ORBIT_RAMP_MS);
        const ease = t * t * (3 - 2 * t); // smoothstep 0→1
        bearing += ORBIT_DEG_PER_SEC * ease * (step / 1000);
        this.map.jumpTo({ center, bearing });
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
}
