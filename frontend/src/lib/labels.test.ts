import { describe, expect, it } from "vitest";

import { countOf, countWord, splitLastGlyph, sportWords } from "./labels";

describe("splitLastGlyph", () => {
  it("splits a name before its last character, so a marker can stay with it", () => {
    expect(splitLastGlyph("Arizona Diamondbacks")).toEqual({
      head: "Arizona Diamondback",
      last: "s",
    });
    expect(splitLastGlyph("  Real Sociedad ")).toEqual({ head: "Real Socieda", last: "d" });
    expect(splitLastGlyph("A")).toEqual({ head: "", last: "A" });
    expect(splitLastGlyph("")).toEqual({ head: "", last: "" });
  });

  it("keeps a combining accent with its letter", () => {
    // "é" written as e + U+0301: one character to a reader, two code points.
    const { head, last } = splitLastGlyph("Kylian Mbappe\u0301");
    expect(head).toBe("Kylian Mbapp");
    expect(last).toBe("e\u0301");
  });
});

describe("sportWords", () => {
  it("uses each sport's own word for what is counted", () => {
    expect(sportWords("baseball").tally?.noun).toBe("Runs");
    expect(sportWords("basketball").tally?.noun).toBe("Points");
    expect(sportWords("football").tally?.noun).toBe("Points");
    expect(sportWords("soccer").tally?.noun).toBe("Goals");
    expect(sportWords("hockey").tally?.noun).toBe("Goals");
    expect(sportWords("tennis").tally?.noun).toBe("Sets");
    expect(sportWords("volleyball").tally?.noun).toBe("Sets");
  });

  it("has no tally for a sport whose score is not one", () => {
    expect(sportWords("mma").tally).toBeNull();
    expect(sportWords("golf").tally).toBeNull();
    expect(sportWords(undefined).tally).toBeNull();
  });

  it("counts games, matches and bouts", () => {
    expect(countOf(8, sportWords("baseball"))).toBe("8 games");
    expect(countOf(1, sportWords("soccer"))).toBe("1 match");
    expect(countOf(2, sportWords("mma"))).toBe("2 bouts");
  });
});

describe("countWord", () => {
  it("spells counts up to ten and leaves the rest as digits", () => {
    expect(countWord(0)).toBe("zero");
    expect(countWord(5)).toBe("five");
    expect(countWord(10)).toBe("ten");
    expect(countWord(11)).toBe("11");
    expect(countWord(2.5)).toBe("2.5");
  });
});
