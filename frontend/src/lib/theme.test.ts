import { describe, expect, it } from "vitest";

import { contrastRatio } from "./color";
import {
  ACCENT_CACHE_KEY,
  ACCENT_VAR_NAMES,
  DEFAULT_PALETTE,
  DEFAULT_THEME,
  EDGE_RATIO,
  PALETTE_KEY,
  STORED_THEME_IDS,
  THEMES,
  THEME_KEY,
  THEME_SURFACES,
  pickFavoriteTeam,
  resolveAppearance,
  teamAccentVars,
  type ThemeId,
} from "./theme";

// The stylesheet and the inline bootstrap are read from disk: Vitest blanks
// CSS imports (even `?raw`), and the tsc program this file shares with the
// browser app has no Node types on purpose. So the one Node call is typed
// here, at module scope, and reached through a specifier tsc does not resolve.
const { readFileSync } = (await import(/* @vite-ignore */ "node:" + "fs")) as {
  readFileSync(path: URL, encoding: "utf8"): string;
};
const stylesheet = readFileSync(
  new URL("../index.css", import.meta.url),
  "utf8",
);
const bootstrapHtml = readFileSync(
  new URL("../../index.html", import.meta.url),
  "utf8",
);
const layoutSource = readFileSync(
  new URL("../components/Layout.tsx", import.meta.url),
  "utf8",
);

describe("resolveAppearance", () => {
  it("defaults to the light base and the brand palette", () => {
    expect(resolveAppearance(null, null)).toEqual({
      theme: "light",
      palette: "brand",
    });
    expect(DEFAULT_THEME).toBe("light");
    expect(DEFAULT_PALETTE).toBe("brand");
  });

  it("keeps a base a build has always had", () => {
    expect(resolveAppearance("dark", null).theme).toBe("dark");
    expect(resolveAppearance("light", null).theme).toBe("light");
    expect(resolveAppearance("contrast", null).theme).toBe("contrast");
  });

  it("migrates Custom, and Stadium before it, to Dark + Team", () => {
    expect(resolveAppearance("custom", null)).toEqual({
      theme: "dark",
      palette: "team",
    });
    expect(resolveAppearance("stadium", null)).toEqual({
      theme: "dark",
      palette: "team",
    });
  });

  it("lands the retired looks on the nearest base, brand palette", () => {
    expect(resolveAppearance("floodlight", null)).toEqual({
      theme: "dark",
      palette: "brand",
    });
    expect(resolveAppearance("ember", null)).toEqual({
      theme: "dark",
      palette: "brand",
    });
    expect(resolveAppearance("newsprint", null)).toEqual({
      theme: "light",
      palette: "brand",
    });
  });

  it("lets an explicit palette win over the one an old id implies", () => {
    expect(resolveAppearance("custom", "brand").palette).toBe("brand");
    expect(resolveAppearance("light", "team").palette).toBe("team");
  });

  it("rejects ids that only exist on Object.prototype, and junk", () => {
    for (const junk of ["constructor", "toString", "__proto__", "", "DARK"]) {
      expect(resolveAppearance(junk, junk)).toEqual({
        theme: "light",
        palette: "brand",
      });
    }
  });

  it("gives every stored id a base that exists", () => {
    const bases = new Set(THEMES.map((option) => option.id));
    for (const [id, target] of Object.entries(STORED_THEME_IDS)) {
      expect(bases.has(target.theme), id).toBe(true);
    }
  });
});

/**
 * Run the inline bootstrap from index.html against a stub page and report
 * what it did. `storage` is the localStorage contents, or null for storage
 * that throws on every access (private mode, blocked cookies).
 */
function runBootstrap(storage: Record<string, string> | null): {
  attributes: Record<string, string>;
  style: Record<string, string>;
  metas: Record<string, string>;
} {
  const script = /<script>\s*(\(function \(\) \{[\s\S]*?\}\)\(\);)\s*<\/script>/.exec(
    bootstrapHtml,
  );
  if (script === null) throw new Error("index.html has no inline bootstrap");
  const attributes: Record<string, string> = {};
  const style: Record<string, string> = {};
  const metas: Record<string, string> = {};
  const windowStub = {
    get localStorage() {
      if (storage === null) throw new Error("storage is blocked");
      return {
        getItem: (key: string) =>
          Object.hasOwn(storage, key) ? storage[key] : null,
      };
    },
  };
  const documentStub = {
    documentElement: {
      setAttribute: (name: string, value: string) => {
        attributes[name] = value;
      },
      style: {
        setProperty: (name: string, value: string) => {
          style[name] = value;
        },
      },
    },
    querySelector: (selector: string) => {
      const name = /meta\[name="([a-z-]+)"\]/.exec(selector)?.[1];
      if (name === undefined) return null;
      return {
        setAttribute: (_attribute: string, value: string) => {
          metas[name] = value;
        },
      };
    },
  };
  new Function("window", "document", script[1])(windowStub, documentStub);
  return { attributes, style, metas };
}

/** An object literal assigned to `var NAME = {…};` in the inline bootstrap. */
function bootstrapTable(name: string): Record<string, unknown[]> {
  const match = new RegExp(`var ${name} = (\\{[\\s\\S]*?\\});`).exec(
    bootstrapHtml,
  );
  if (match === null) throw new Error(`bootstrap has no ${name} table`);
  return new Function(`return ${match[1]};`)() as Record<string, unknown[]>;
}

describe("index.html bootstrap", () => {
  it("maps every stored id exactly as theme.ts does", () => {
    const stored = bootstrapTable("STORED");
    expect(Object.keys(stored).sort()).toEqual(
      Object.keys(STORED_THEME_IDS).sort(),
    );
    for (const [id, target] of Object.entries(STORED_THEME_IDS)) {
      expect(stored[id][0], id).toBe(target.theme);
      expect(stored[id][1], id).toBe(target.palette);
    }
  });

  it("describes every base exactly as theme.ts does", () => {
    const bases = bootstrapTable("BASES");
    expect(Object.keys(bases).sort()).toEqual(
      THEMES.map((option) => option.id).sort(),
    );
    for (const option of THEMES) {
      const [dark, teamPalette, themeColor] = bases[option.id];
      expect(dark === 1, option.id).toBe(option.dark);
      expect(teamPalette === 1, option.id).toBe(option.teamPalette);
      expect(themeColor, option.id).toBe(THEME_SURFACES[option.id].sheet);
    }
  });

  it("falls back to the same default", () => {
    expect(bootstrapHtml).toContain(': ["light"];');
  });

  // The rest RUN the inline script against a stub page, so a renamed key,
  // an added variable or a value the script's filter rejects fails here
  // instead of silently costing the first frame.
  const allThemeIds = [...Object.keys(STORED_THEME_IDS), "nonsense"];

  it.each(allThemeIds)(
    "stored %s lands exactly where theme.ts puts it, for every palette",
    (storedTheme) => {
      for (const storedPalette of [null, "brand", "team", "nonsense"]) {
        const page = runBootstrap({
          [THEME_KEY]: storedTheme,
          ...(storedPalette === null ? {} : { [PALETTE_KEY]: storedPalette }),
        });
        const expected = resolveAppearance(storedTheme, storedPalette);
        const option = THEMES.find((t) => t.id === expected.theme);
        expect(page.attributes["data-theme"]).toBe(expected.theme);
        expect(page.attributes["data-palette"]).toBe(
          option?.teamPalette ? expected.palette : "brand",
        );
        expect(page.metas["color-scheme"]).toBe(option?.dark ? "dark" : "light");
        expect(page.metas["theme-color"]).toBe(
          THEME_SURFACES[expected.theme].sheet,
        );
      }
    },
  );

  it("paints nothing stored as Light, and survives storage that throws", () => {
    for (const page of [runBootstrap({}), runBootstrap(null)]) {
      // With storage blocked the script leaves the markup's own defaults.
      expect(page.attributes["data-theme"] ?? "light").toBe("light");
      expect(page.attributes["data-palette"] ?? "brand").toBe("brand");
      expect(page.style).toEqual({});
    }
  });

  it.each(["light", "dark"] as const)(
    "%s + team: paints the cached accent exactly as the app measured it",
    (theme) => {
      for (const color of ["#0c2340", "#ffc52f", "#144992", "#ee2222"]) {
        const vars = teamAccentVars(color, theme);
        const page = runBootstrap({
          [THEME_KEY]: theme,
          [PALETTE_KEY]: "team",
          [ACCENT_CACHE_KEY]: JSON.stringify({ theme, vars }),
        });
        expect(page.style, color).toEqual({ ...vars });
        expect(Object.keys(page.style).sort()).toEqual(
          [...ACCENT_VAR_NAMES].sort(),
        );
      }
    },
  );

  it("ignores a cache for another base, and anything that is not a colour", () => {
    const vars = teamAccentVars("#0c2340", "light");
    const wrongBase = runBootstrap({
      [THEME_KEY]: "dark",
      [PALETTE_KEY]: "team",
      [ACCENT_CACHE_KEY]: JSON.stringify({ theme: "light", vars }),
    });
    expect(wrongBase.style).toEqual({});

    const hostile = runBootstrap({
      [THEME_KEY]: "light",
      [PALETTE_KEY]: "team",
      [ACCENT_CACHE_KEY]: JSON.stringify({
        theme: "light",
        vars: {
          "--sd-accent": "url(https://example.test/x)",
          "--sd-on-accent": "#FFFFFF",
          "--sd-accent-text": "red; background: url(x)",
          "--sd-accent-edge": "transparent",
          "--bs-ink": "#ffffff",
          color: "#ffffff",
        },
      }),
    });
    expect(hostile.style).toEqual({ "--sd-accent-edge": "transparent" });

    const brand = runBootstrap({
      [THEME_KEY]: "light",
      [PALETTE_KEY]: "brand",
      [ACCENT_CACHE_KEY]: JSON.stringify({ theme: "light", vars }),
    });
    expect(brand.style).toEqual({});
  });

  it("keeps everything else when the accent cache is corrupt", () => {
    const page = runBootstrap({
      [THEME_KEY]: "custom",
      "sportsdash.kiosk": "1",
      [ACCENT_CACHE_KEY]: '{"theme":"dark","vars":{"--sd-acc',
    });
    expect(page.attributes["data-theme"]).toBe("dark");
    expect(page.attributes["data-palette"]).toBe("team");
    expect(page.attributes["data-kiosk"]).toBe("");
    expect(page.style).toEqual({});
  });

  it("sets the kiosk scale from the key Layout writes", () => {
    expect(layoutSource).toContain('const KIOSK_KEY = "sportsdash.kiosk";');
    expect(
      runBootstrap({ "sportsdash.kiosk": "1" }).attributes["data-kiosk"],
    ).toBe("");
    expect(
      "data-kiosk" in runBootstrap({ "sportsdash.kiosk": "0" }).attributes,
    ).toBe(false);
  });

  it("paints the light default before the script runs", () => {
    expect(bootstrapHtml).toContain('data-theme="light" data-palette="brand"');
    expect(bootstrapHtml).toContain(
      '<meta name="color-scheme" content="light" />',
    );
    expect(bootstrapHtml).toContain(
      `<meta name="theme-color" content="${THEME_SURFACES.light.sheet}" />`,
    );
  });
});

/** The `--bs-*` declarations of one theme block in index.css. */
function themeTokens(theme: ThemeId): Record<string, string> {
  const selector =
    theme === "light"
      ? ':root,\n[data-theme="light"] {'
      : `[data-theme="${theme}"] {`;
  const start = stylesheet.indexOf(selector);
  if (start === -1) throw new Error(`index.css has no block for ${theme}`);
  const body = stylesheet.slice(start, stylesheet.indexOf("\n}", start));
  const tokens: Record<string, string> = {};
  for (const match of body.matchAll(/(--bs-[a-z0-9-]+):\s*([^;]+);/g)) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

const THEME_IDS = THEMES.map((option) => option.id);

describe("index.css tokens", () => {
  it.each(THEME_IDS)("%s: THEME_SURFACES mirrors the stylesheet", (theme) => {
    const tokens = themeTokens(theme);
    const surface = THEME_SURFACES[theme];
    expect(tokens["--bs-sheet"]).toBe(surface.sheet);
    expect(tokens["--bs-ground"]).toBe(surface.ground);
    expect(tokens["--bs-tint"]).toBe(surface.tint);
    expect(tokens["--bs-block"]).toBe(surface.block);
    expect(tokens["--bs-band"]).toBe(surface.band);
  });

  // AA for the two everyday bases; High contrast promises AAA.
  it.each(THEME_IDS)("%s: every text token clears its floor", (theme) => {
    const tokens = themeTokens(theme);
    const floor = theme === "contrast" ? 7 : 4.5;
    const pairs: [string, string][] = [];
    for (const surface of ["sheet", "ground", "tint"]) {
      for (const text of ["ink", "ink-2", "muted", "spot-text", "alert"]) {
        pairs.push([text, surface]);
      }
    }
    pairs.push(
      ["on-spot", "spot"],
      ["on-block", "block"],
      ["on-block-2", "block"],
      ["on-band", "band"],
      ["on-band-2", "band"],
      ["on-draw", "draw"],
    );
    for (const [text, surface] of pairs) {
      const ratio = contrastRatio(
        tokens[`--bs-${text}`],
        tokens[`--bs-${surface}`],
      );
      expect(ratio, `${text} on ${surface}`).toBeGreaterThanOrEqual(floor);
    }
  });

  it.each(THEME_IDS)(
    "%s: a spot under 3:1 against a block has its keyline",
    (theme) => {
      const tokens = themeTokens(theme);
      for (const [edge, surfaces] of [
        ["--bs-spot-edge", ["sheet", "tint"]],
        ["--bs-spot-edge-block", ["block", "band"]],
      ] as const) {
        const lowest = Math.min(
          ...surfaces.map((surface) =>
            contrastRatio(tokens["--bs-spot"], tokens[`--bs-${surface}`]),
          ),
        );
        if (lowest < EDGE_RATIO) {
          expect(tokens[edge], edge).not.toBe("transparent");
        }
      }
    },
  );
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

describe("teamAccentVars", () => {
  it("has nothing to offer without a usable colour", () => {
    expect(teamAccentVars(null, "light")).toBeNull();
    expect(teamAccentVars("", "dark")).toBeNull();
    expect(teamAccentVars("teal", "dark")).toBeNull();
  });

  it("never recolours High contrast", () => {
    expect(teamAccentVars("#0c2340", "contrast")).toBeNull();
  });

  it("keeps a navy navy on the light base, with paper on it", () => {
    const vars = teamAccentVars("#0c2340", "light");
    expect(vars?.["--sd-accent"]).toBe("#0c2340");
    expect(vars?.["--sd-on-accent"]).toBe("#ffffff");
    expect(vars?.["--sd-accent-edge"]).toBe("transparent");
    // Navy on the ink block is 1.2:1: it needs the block's keyline.
    expect(vars?.["--sd-accent-edge-block"]).toBe("#ffffff");
  });

  it("keeps a yellow yellow on the light base, with ink and a keyline", () => {
    const vars = teamAccentVars("#ffc52f", "light");
    expect(vars?.["--sd-accent"]).toBe("#ffc52f");
    expect(vars?.["--sd-on-accent"]).toBe("#101418");
    expect(vars?.["--sd-accent-edge"]).toBe("#101418");
    expect(vars?.["--sd-accent-edge-block"]).toBe("transparent");
  });

  it("lifts a navy on the dark base only as far as it has to", () => {
    const vars = teamAccentVars("#0c2340", "dark");
    const fill = vars?.["--sd-accent"] ?? "";
    expect(fill).not.toBe("#0c2340");
    expect(contrastRatio(fill, THEME_SURFACES.dark.sheet)).toBeGreaterThanOrEqual(3);
    // Far short of a pastel, and still blue-dominant.
    expect(contrastRatio(fill, THEME_SURFACES.dark.sheet)).toBeLessThan(3.6);
    expect(parseInt(fill.slice(5, 7), 16)).toBeGreaterThan(
      parseInt(fill.slice(1, 3), 16),
    );
  });

  it.each(["light", "dark"] as const)(
    "%s: every colour in the cube reads, as a fill and as text",
    (theme) => {
      const surface = THEME_SURFACES[theme];
      const sheets = [surface.sheet, surface.ground, surface.tint];
      for (const hex of colorCube()) {
        const vars = teamAccentVars(hex, theme);
        if (vars === null) throw new Error(`no vars for ${hex}`);
        const fill = vars["--sd-accent"];
        expect(
          contrastRatio(vars["--sd-on-accent"], fill),
          `${hex} text on fill`,
        ).toBeGreaterThanOrEqual(4.5);
        for (const sheet of sheets) {
          expect(
            contrastRatio(vars["--sd-accent-text"], sheet),
            `${hex} as text`,
          ).toBeGreaterThanOrEqual(4.5);
        }
        // A fill is either visible against the sheet and the tint, or
        // keylined (the ground is not held to it: see teamAccentVars).
        if (vars["--sd-accent-edge"] === "transparent") {
          for (const sheet of [surface.sheet, surface.tint]) {
            expect(
              contrastRatio(fill, sheet),
              `${hex} fill on sheet`,
            ).toBeGreaterThanOrEqual(EDGE_RATIO);
          }
        }
        if (vars["--sd-accent-edge-block"] === "transparent") {
          expect(
            Math.min(
              contrastRatio(fill, surface.block),
              contrastRatio(fill, surface.band),
            ),
            `${hex} fill on block`,
          ).toBeGreaterThanOrEqual(EDGE_RATIO);
        }
        if (theme === "dark") {
          // The dark base lifts instead of keylining on its sheets.
          expect(vars["--sd-accent-edge"], hex).toBe("transparent");
        }
      }
    },
  );
});

describe("pickFavoriteTeam", () => {
  const teams = [
    { id: "a", color: null },
    { id: "b", color: "#0c2340" },
    { id: "c", color: "#ffc52f" },
  ];

  it("defaults to the first followed team that has a colour", () => {
    expect(pickFavoriteTeam(teams, null)?.id).toBe("b");
  });

  it("honours the stored choice, even one without a colour", () => {
    expect(pickFavoriteTeam(teams, "c")?.id).toBe("c");
    expect(pickFavoriteTeam(teams, "a")?.id).toBe("a");
  });

  it("falls back when the stored team is no longer followed", () => {
    expect(pickFavoriteTeam(teams, "gone")?.id).toBe("b");
  });

  it("takes the first team when none has a colour, and null when none", () => {
    expect(pickFavoriteTeam([{ id: "x", color: null }], null)?.id).toBe("x");
    expect(pickFavoriteTeam([], "a")).toBeNull();
  });
});

describe("teamAccentVars and the brand orange", () => {
  it("treats an orange team exactly like the brand palette", () => {
    // 2.97:1 on the Light ground, 3.3:1 on a sheet: unkeyed in both paths.
    expect(teamAccentVars("#e8643c", "light")?.["--sd-accent-edge"]).toBe(
      "transparent",
    );
  });
});
