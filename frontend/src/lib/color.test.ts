import { describe, expect, it } from "vitest";

import { accentOnDark, isLightColor, mixColor } from "./color";

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

describe("accentOnDark", () => {
  it("passes an already-light accent through untouched", () => {
    // The stock amber accent is light enough as-is.
    expect(accentOnDark("#f59e0b")).toBe("#f59e0b");
  });

  it("lifts a dark club color into readable territory", () => {
    const lifted = accentOnDark("#041e42"); // deep navy
    expect(lifted).not.toBe("#041e42");
    expect(isLightColor(lifted)).toBe(true);
  });

  it("keeps the team's hue family when lifting", () => {
    const lifted = accentOnDark("#041e42"); // navy: blue channel dominant
    const r = parseInt(lifted.slice(1, 3), 16);
    const g = parseInt(lifted.slice(3, 5), 16);
    const b = parseInt(lifted.slice(5, 7), 16);
    expect(b).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(r);
  });

  it("lifts even a near-black color to a readable tone", () => {
    expect(isLightColor(accentOnDark("#111111"))).toBe(true);
  });
});
