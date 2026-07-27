/**
 * The marker reconciler's "what changed?" decision, extracted from MapView.tsx
 * so it is unit-testable without MapLibre or a DOM.
 *
 * The map keeps its DOM markers alive across polls on purpose: recreating one
 * is what tears down clustering and restarts the today-pulse / orbit
 * animations. But marker ids are deliberately content-INDEPENDENT (a venue key
 * IS its rounded coordinates, a team's id is its id), so "same id" says nothing
 * about whether the payload behind the pin moved. Refreshing a kept marker is
 * therefore signature-driven, and that decision lives here.
 */

/**
 * The fields the reconciler compares. A full marker spec satisfies this, so
 * the view's `MapMarker` extends it rather than restating the contract.
 */
export interface MarkerSignature {
  lat: number;
  lon: number;
  /**
   * Content signature: everything this pin renders — badge, hover label, and
   * the payload its click hands to the side panel. This is what tells the
   * reconciler that a marker it is KEEPING has to be refreshed after a poll.
   */
  sig: string;
  /**
   * The subset `makeElement` draws. Only a change here rebuilds the badge DOM,
   * so a score change refreshes the tooltip/panel data without restarting the
   * today-pulse animation.
   */
  elementSig: string;
}

/**
 * What a KEPT marker needs. All three false means the incoming spec renders
 * exactly what is already on the map and the marker must be left completely
 * alone — that no-op is the whole reason the map stays smooth across the 30s
 * poll.
 */
export interface KeptMarkerUpdate {
  /** Rebuild the badge DOM inside the marker's stable host element. */
  rebuildElement: boolean;
  /** Push new coordinates to the MapLibre marker. */
  move: boolean;
  /**
   * Rebind the spec the once-bound hover/click handlers read through — what
   * makes the hover label, the side panel and the click's fly-to target show
   * current data. Implied by either of the other two: a marker whose badge or
   * position changed must never keep handlers pointing at the old payload.
   */
  rebindSpec: boolean;
}

/**
 * Decide how to refresh a marker the map is keeping (its id appears in both
 * the live set and the incoming one).
 */
export function diffKeptMarker(
  live: MarkerSignature,
  next: MarkerSignature,
): KeptMarkerUpdate {
  const rebuildElement = live.elementSig !== next.elementSig;
  const move = live.lon !== next.lon || live.lat !== next.lat;
  return {
    rebuildElement,
    move,
    rebindSpec: rebuildElement || move || live.sig !== next.sig,
  };
}
