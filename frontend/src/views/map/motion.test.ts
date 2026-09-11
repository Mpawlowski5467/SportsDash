import { describe, expect, it } from "vitest";
import { fanArrivalMs, screenHeading } from "./motion";

describe("route orientation", () => {
  it("follows screen projection when the camera rotates", () => {
    const northUp = ([lng, lat]: [number, number]) => ({ x: lng, y: -lat });
    const eastUp = ([lng, lat]: [number, number]) => ({ x: -lat, y: -lng });
    expect(screenHeading(northUp, [0, 0], [1, 0])).toBe(90);
    expect(screenHeading(eastUp, [0, 0], [1, 0])).toBe(0);
    expect(screenHeading(eastUp, [0, 0], [0, 1])).toBe(-90);
  });

  it("uses projected latitude and pitch instead of raw degrees", () => {
    const project = ([x, y]: [number, number]) => ({ x, y: -y * 2 });
    expect(screenHeading(project, [0, 0], [1, 1])).toBeCloseTo(26.565);
  });

  it("does not reverse on landing when a distant sample is behind the camera", () => {
    const pitched = ([x, y]: [number, number]) => ({ x: x / (1 + x), y });
    expect(screenHeading(pitched, [-2, 0], [2, 0], [0, 0])).toBe(90);
  });
});

describe("fan arrival", () => {
  it("lets trailing fans finish walking before entering the gate", () => {
    const leader = fanArrivalMs(200, 20, 0);
    const follower = fanArrivalMs(200, 20, -30);
    expect(leader).toBe(10_000);
    expect(follower).toBe(11_500);
    expect(20 * follower / 1000 - 30).toBe(200);
  });
});
