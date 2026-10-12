import { describe, expect, it } from "vitest";

import { SCORE_FLASH_MS, scoreMoved } from "./scoreFlash";

// Read from disk as `theme.test.ts` does: Vitest blanks CSS imports, and the
// tsc program has no Node types on purpose.
const { readFileSync } = (await import(/* @vite-ignore */ "node:" + "fs")) as {
  readFileSync(path: URL, encoding: "utf8"): string;
};
const read = (path: string) =>
  readFileSync(new URL(path, import.meta.url), "utf8");
const stylesheet = read("../index.css");
/** Every place a score is drawn. */
const callers = {
  "components/ScoreTicker.tsx": read("../components/ScoreTicker.tsx"),
  "components/GameRow.tsx": read("../components/GameRow.tsx"),
  "views/today/GameLists.tsx": read("../views/today/GameLists.tsx"),
  "views/today/LiveHero.tsx": read("../views/today/LiveHero.tsx"),
  "views/calendar/parts.tsx": read("../views/calendar/parts.tsx"),
  "views/matchup/GameSheet.tsx": read("../views/matchup/GameSheet.tsx"),
};

describe("scoreMoved", () => {
  it("is true when the same game's score changes", () => {
    expect(scoreMoved({ of: "g1", value: 3 }, { of: "g1", value: 4 })).toBe(true);
    // A score can go down too (a goal ruled out).
    expect(scoreMoved({ of: "g1", value: 2 }, { of: "g1", value: 1 })).toBe(true);
    expect(scoreMoved({ of: "g1", value: 0 }, { of: "g1", value: 1 })).toBe(true);
  });

  it("is false when nothing changed, which is every re-render and every re-show", () => {
    expect(scoreMoved({ of: "g1", value: 3 }, { of: "g1", value: 3 })).toBe(false);
    expect(scoreMoved({ of: "g1", value: 0 }, { of: "g1", value: 0 })).toBe(false);
  });

  it("is false when the place on the page is given another game", () => {
    // Matchup's picker, or Today's hero moving on to the next live game.
    expect(scoreMoved({ of: "g1", value: 3 }, { of: "g2", value: 5 })).toBe(false);
    expect(scoreMoved({ of: "g1", value: 3 }, { of: "g2", value: 3 })).toBe(false);
  });

  it("is false when a figure appears or goes away", () => {
    // A game starting: there was no score, now there is 0.
    expect(scoreMoved({ of: "g1", value: null }, { of: "g1", value: 0 })).toBe(false);
    expect(scoreMoved({ of: "g1", value: 3 }, { of: "g1", value: null })).toBe(false);
    expect(scoreMoved({ of: "g1", value: null }, { of: "g1", value: null })).toBe(false);
  });
});

describe("the stylesheet's half of the rule", () => {
  it("animates only the armed class, for the length the component waits", () => {
    const armed = /\.sd-score--flash\s*\{\s*animation:\s*sd-score-flash\s+(\d+)ms/.exec(
      stylesheet,
    );
    expect(armed).not.toBeNull();
    expect(Number(armed?.[1])).toBe(SCORE_FLASH_MS);
  });

  it("leaves the figure itself still: a class that stays on replays on every re-show", () => {
    // No rule may give `.sd-score` (the figure, always present) an animation.
    expect(stylesheet).not.toMatch(/\.sd-score\s*\{/);
    expect(stylesheet).not.toMatch(/\.sd-score\s*,/);
  });

  it("keeps the pulse behind the reduced-motion gate", () => {
    const rule = stylesheet.indexOf(".sd-score--flash");
    const gate = stylesheet.lastIndexOf(
      "@media (prefers-reduced-motion: no-preference)",
      rule,
    );
    const reduce = stylesheet.lastIndexOf(
      "@media (prefers-reduced-motion: reduce)",
      rule,
    );
    expect(rule).toBeGreaterThan(-1);
    // The nearest motion query above the rule is the one that allows motion.
    expect(gate).toBeGreaterThan(reduce);
  });

  it("is applied by one component: no view keys a score to restart it", () => {
    for (const [file, source] of Object.entries(callers)) {
      expect(source, file).toContain("<ScoreFigure");
      expect(source, file).not.toMatch(/className="[^"]*\bsd-score\b/);
    }
  });
});
