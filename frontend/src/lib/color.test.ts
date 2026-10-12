import { describe, expect, it } from "vitest";

import {
  INK,
  PAPER,
  contrastRatio,
  fitFill,
  liftOn,
  luminance,
  mixColor,
  normalizeHex,
  readableOn,
} from "./color";

describe("mixColor", () => {
  it("keeps the color at amount 0 and reaches the target at amount 1", () => {
    expect(mixColor("#123456", "#abcdef", 0)).toBe("#123456");
    expect(mixColor("#123456", "#abcdef", 1)).toBe("#abcdef");
  });

  it("mixes channel-wise at the midpoint", () => {
    expect(mixColor("#000000", "#ffffff", 0.5)).toBe("#808080");
  });

  it("expands 3-digit hex and tolerates a missing #", () => {
    expect(mixColor("#000", "#fff", 0.5)).toBe("#808080");
    expect(mixColor("000000", "ffffff", 0.5)).toBe("#808080");
  });
});

describe("normalizeHex", () => {
  it("accepts the shapes providers send and rejects the rest", () => {
    expect(normalizeHex("#ABC")).toBe("#aabbcc");
    expect(normalizeHex("C8102E")).toBe("#c8102e");
    expect(normalizeHex(" #0c2340 ")).toBe("#0c2340");
    expect(normalizeHex("")).toBeNull();
    expect(normalizeHex(null)).toBeNull();
    expect(normalizeHex("#12345")).toBeNull();
    expect(normalizeHex("red")).toBeNull();
  });
});

describe("contrastRatio", () => {
  it("matches the WCAG reference points", () => {
    expect(luminance("#000000")).toBe(0);
    expect(luminance("#ffffff")).toBeCloseTo(1, 5);
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });

  it("is symmetric, and scores malformed input as no contrast", () => {
    expect(contrastRatio("#101418", "#e8643c")).toBeCloseTo(
      contrastRatio("#e8643c", "#101418"),
      10,
    );
    expect(contrastRatio("nope", "#ffffff")).toBe(1);
  });
});

/** Every 17th step of each channel: 4096 colors across the whole cube. */
function colorCube(): string[] {
  const out: string[] = [];
  for (let r = 0; r <= 255; r += 17) {
    for (let g = 0; g <= 255; g += 17) {
      for (let b = 0; b <= 255; b += 17) {
        out.push(
          "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join(""),
        );
      }
    }
  }
  return out;
}

describe("fitFill", () => {
  it("puts paper on a dark team color and ink on a pale one", () => {
    expect(fitFill("#0c2340")).toEqual({ fill: "#0c2340", on: PAPER }); // navy
    expect(fitFill("#ffc52f")).toEqual({ fill: "#ffc52f", on: INK }); // yellow
    expect(fitFill("#ffffff")).toEqual({ fill: "#ffffff", on: INK });
    expect(fitFill("#000000")).toEqual({ fill: "#000000", on: PAPER });
  });

  it("keeps the brand orange untouched, with ink on it", () => {
    expect(fitFill("#E8643C")).toEqual({ fill: "#e8643c", on: INK });
  });

  it("nudges the mid-luminance band where neither ink nor paper passes", () => {
    // #ee2222: paper 4.30:1, ink 4.26:1 — both short of 4.5.
    expect(contrastRatio(PAPER, "#ee2222")).toBeLessThan(4.5);
    expect(contrastRatio(INK, "#ee2222")).toBeLessThan(4.5);
    const fitted = fitFill("#ee2222");
    expect(contrastRatio(fitted.on, fitted.fill)).toBeGreaterThanOrEqual(4.5);
    // …by a small step, not a change of color.
    expect(contrastRatio(fitted.fill, "#ee2222")).toBeLessThan(1.15);
  });

  it("clears 4.5:1 for every color in the cube", () => {
    for (const hex of colorCube()) {
      const { fill, on } = fitFill(hex);
      expect(contrastRatio(on, fill), hex).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("falls back to ink for a malformed color", () => {
    expect(fitFill("not-a-color")).toEqual({ fill: INK, on: PAPER });
  });
});

describe("readableOn", () => {
  const LIGHT = ["#ffffff", "#f1f2f4", "#f6f7f8"];
  const DARK = ["#171c22", "#101418", "#13181e"];

  it("returns a color that already passes untouched", () => {
    expect(readableOn("#0c2340", LIGHT)).toBe("#0c2340");
    expect(readableOn("#ffc52f", DARK)).toBe("#ffc52f");
  });

  it("deepens a pale color on light surfaces and lifts a dark one on dark", () => {
    const deep = readableOn("#ffc52f", LIGHT);
    const lifted = readableOn("#0c2340", DARK);
    for (const surface of LIGHT) {
      expect(contrastRatio(deep, surface)).toBeGreaterThanOrEqual(4.5);
    }
    for (const surface of DARK) {
      expect(contrastRatio(lifted, surface)).toBeGreaterThanOrEqual(4.5);
    }
    // Hue family survives: navy stays blue-dominant.
    const b = parseInt(lifted.slice(5, 7), 16);
    const r = parseInt(lifted.slice(1, 3), 16);
    expect(b).toBeGreaterThan(r);
  });

  it("clears 4.5:1 on every surface for every color in the cube", () => {
    for (const hex of colorCube()) {
      for (const surfaces of [LIGHT, DARK]) {
        const text = readableOn(hex, surfaces);
        for (const surface of surfaces) {
          expect(contrastRatio(text, surface), hex).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });
});

describe("liftOn", () => {
  const DARK = ["#171c22", "#101418", "#13181e"];

  it("leaves a color that is already visible alone", () => {
    expect(liftOn("#ffc52f", DARK)).toBe("#ffc52f");
    expect(liftOn("#e8643c", DARK)).toBe("#e8643c");
  });

  it("raises a navy to a brighter blue of the same hue", () => {
    const lifted = liftOn("#0c2340", DARK);
    for (const surface of DARK) {
      expect(contrastRatio(lifted, surface)).toBeGreaterThanOrEqual(3);
    }
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(lifted.slice(i, i + 2), 16));
    // Still blue first, green second, red last, and still saturated: mixing
    // toward white would have pulled the three channels together.
    expect(b).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(r);
    expect(b - r).toBeGreaterThan(100);
  });

  it("turns black into a grey, having no hue to keep", () => {
    const lifted = liftOn("#000000", DARK);
    expect(lifted.slice(1, 3)).toBe(lifted.slice(3, 5));
    expect(lifted.slice(3, 5)).toBe(lifted.slice(5, 7));
    expect(contrastRatio(lifted, DARK[0])).toBeGreaterThanOrEqual(3);
  });

  it("clears 3:1 on every surface for every color in the cube", () => {
    for (const hex of colorCube()) {
      const lifted = liftOn(hex, DARK);
      for (const surface of DARK) {
        expect(contrastRatio(lifted, surface), hex).toBeGreaterThanOrEqual(3);
      }
    }
  });
});
