type LngLat = [number, number];
type Project = (point: LngLat) => { x: number; y: number };

/** The SVG nose follows the route as drawn, including map bearing and pitch. */
export function screenHeading(project: Project, from: LngLat, to: LngLat, at: LngLat = from): number {
  // Sample a local tangent at the aircraft. At stadium zoom a whole route
  // segment can sit behind the pitched camera's horizon and project backwards.
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const step = .00001 / (Math.hypot(dx, dy) || 1);
  const a = project(at);
  const b = project([at[0] + dx * step, at[1] + dy * step]);
  return Math.atan2(b.x - a.x, a.y - b.y) * 180 / Math.PI;
}

/** Single-file followers reach the gate later than their clump's leader. */
export function fanArrivalMs(distance: number, speedMps: number, along: number): number {
  return Math.max(0, distance - along) / speedMps * 1000;
}
