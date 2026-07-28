/**
 * diffKeptMarker: how the map refreshes a pin it is KEEPING across a poll.
 *
 * The reconciler used to skip every id it already had on the map, so a poll
 * that scored a game or moved a fixture updated nothing behind an existing
 * pin. Every "changed" case below fails against that behaviour — it decided
 * nothing at all — while the unchanged case pins the property that made
 * skipping tempting in the first place: an identical payload must still touch
 * nothing, or clustering and the today-pulse animation restart every 30s.
 */
import { describe, expect, it } from "vitest";

import { diffKeptMarker, type MarkerSignature } from "./reconcile";

function sig(overrides: Partial<MarkerSignature> = {}): MarkerSignature {
  return {
    lat: 51.5,
    lon: -0.12,
    sig: "1|{\"name\":\"Harbor Herons\",\"score\":0}",
    elementSig: "1|followed|#123456",
    ...overrides,
  };
}

describe("diffKeptMarker", () => {
  it("leaves a byte-identical marker completely alone", () => {
    expect(diffKeptMarker(sig(), sig())).toEqual({
      rebuildElement: false,
      move: false,
      rebindSpec: false,
    });
  });

  it("rebinds the spec when only the panel/tooltip payload moved", () => {
    // A goal: the hover label and the open side panel must show it, but the
    // badge is drawing the same thing, so its DOM (and its pulse animation)
    // must survive untouched.
    const update = diffKeptMarker(
      sig(),
      sig({ sig: "1|{\"name\":\"Harbor Herons\",\"score\":1}" }),
    );
    expect(update.rebindSpec).toBe(true);
    expect(update.rebuildElement).toBe(false);
    expect(update.move).toBe(false);
  });

  it("rebuilds the badge when what it draws changed", () => {
    // The team stopped playing today: the pulse ring comes off the pin.
    const update = diffKeptMarker(
      sig(),
      sig({ sig: "0|{}", elementSig: "0|followed|#123456" }),
    );
    expect(update.rebuildElement).toBe(true);
    expect(update.rebindSpec).toBe(true);
  });

  it("moves the marker when the venue's coordinates shift", () => {
    const update = diffKeptMarker(sig(), sig({ lat: 40.7, lon: -74.0 }));
    expect(update.move).toBe(true);
    // The click's fly-to target and the hover popup's anchor are read off the
    // bound spec, so a move has to rebind it or they drift from the pin.
    expect(update.rebindSpec).toBe(true);
  });

  it("moves on a longitude-only change", () => {
    expect(diffKeptMarker(sig(), sig({ lon: -0.13 })).move).toBe(true);
  });
});
