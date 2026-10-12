/**
 * Theme system: the JS side of the Box Score tokens in `index.css`.
 *
 * Appearance is two independent choices:
 *
 *   - the BASE (`ThemeId`): light, dark, or high contrast. It swaps every
 *     semantic token (`--bs-ground`, `--bs-ink`, …) under `[data-theme]`.
 *   - the PALETTE (`PaletteId`): the brand orange, or the favourite team's
 *     colour as the spot. A colour choice on top of the light or dark base.
 *
 * This module owns reading and persisting both, migrating the ids older
 * builds stored, applying them to `<html>`, and measuring a team colour
 * against the base so the text on it always reads.
 *
 * Pure client state — no API, no `types.ts` changes. The no-FOUC bootstrap in
 * `index.html` mirrors the storage keys and the migration table here; a
 * test compares the two so they cannot drift.
 */

import {
  INK,
  PAPER,
  contrastRatio,
  fitFill,
  liftOn,
  normalizeHex,
  readableOn,
} from "./color";

export type ThemeId = "light" | "dark" | "contrast";
export type PaletteId = "brand" | "team";

export interface Appearance {
  theme: ThemeId;
  palette: PaletteId;
}

export interface ThemeOption {
  id: ThemeId;
  label: string;
  /** One-line description for the settings switcher. */
  description: string;
  /** Whether the theme renders on a dark base (affects `color-scheme`). */
  dark: boolean;
  /** Whether the Team palette may recolour this base. */
  teamPalette: boolean;
}

/** Canonical, ordered list of selectable bases. */
export const THEMES: readonly ThemeOption[] = [
  {
    id: "light",
    label: "Light",
    description: "White sheets and ink rules, like a printed scorecard.",
    dark: false,
    teamPalette: true,
  },
  {
    id: "dark",
    label: "Dark",
    description: "The same sheet inverted, for a dim room or a wall display.",
    dark: true,
    teamPalette: true,
  },
  {
    id: "contrast",
    label: "High contrast",
    description: "True black and white type, every text tone at WCAG AAA.",
    dark: true,
    teamPalette: false,
  },
];

export interface PaletteOption {
  id: PaletteId;
  label: string;
  description: string;
}

export const PALETTES: readonly PaletteOption[] = [
  {
    id: "brand",
    label: "Box Score orange",
    description: "The SportsDash orange as the one spot colour.",
  },
  {
    id: "team",
    label: "Team",
    description: "Your favourite team's colour as the spot colour.",
  },
];

export const DEFAULT_THEME: ThemeId = "light";
export const DEFAULT_PALETTE: PaletteId = "brand";

/** localStorage keys. Exported for the test that runs the bootstrap. */
export const THEME_KEY = "sportsdash.theme";
export const PALETTE_KEY = "sportsdash.palette";
export const FAVORITE_KEY = "sportsdash.favoriteTeam";
export const ACCENT_CACHE_KEY = "sportsdash.teamAccent";

/**
 * What every id a build has ever stored under `sportsdash.theme` means now.
 *
 * The id is persisted, so dropping one has to be handled rather than
 * ignored: without this a returning "custom" would fail validation and
 * silently reset to the default. Until Box Score the theme was a single
 * choice, so the old ids carry a palette too: "custom" (and "stadium"
 * before it) was the dark base tinted by a followed team, which is now
 * Dark + Team. The rest name a base and leave the palette alone.
 */
export const STORED_THEME_IDS: Readonly<
  Record<string, { theme: ThemeId; palette?: PaletteId }>
> = {
  light: { theme: "light" },
  dark: { theme: "dark" },
  contrast: { theme: "contrast" },
  custom: { theme: "dark", palette: "team" },
  stadium: { theme: "dark", palette: "team" },
  floodlight: { theme: "dark" },
  ember: { theme: "dark" },
  newsprint: { theme: "light" },
};

function isPaletteId(value: unknown): value is PaletteId {
  return value === "brand" || value === "team";
}

function themeOption(theme: ThemeId): ThemeOption {
  return THEMES.find((option) => option.id === theme) ?? THEMES[0];
}

/**
 * Turn the two stored strings into an appearance. Pure, so the migration is
 * testable without a DOM; `index.html` runs the same logic before paint.
 *
 * An explicit stored palette always wins. The palette an old id implies
 * (custom → team) only applies while nothing newer has been stored.
 *
 * Builds before Box Score never stored the default, only a choice, so an
 * install that never picked a theme has nothing stored and opens Light.
 */
export function resolveAppearance(
  storedTheme: string | null,
  storedPalette: string | null,
): Appearance {
  const known =
    storedTheme !== null && Object.hasOwn(STORED_THEME_IDS, storedTheme)
      ? STORED_THEME_IDS[storedTheme]
      : undefined;
  const theme = known?.theme ?? DEFAULT_THEME;
  const palette = isPaletteId(storedPalette)
    ? storedPalette
    : (known?.palette ?? DEFAULT_PALETTE);
  return { theme, palette };
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    // Storage unavailable (private mode, disabled cookies).
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Persisting is best-effort; the caller still applies for this session.
  }
}

/**
 * Read the persisted appearance, falling back to the defaults. Safe to call
 * before React mounts and without `localStorage`.
 */
export function getAppearance(): Appearance {
  return resolveAppearance(readStorage(THEME_KEY), readStorage(PALETTE_KEY));
}

/**
 * Apply an appearance to `<html>`: the two data attributes the tokens key
 * on and the browser-chrome metas. The single source of the DOM effect,
 * idempotent and cheap.
 */
export function applyAppearance({ theme, palette }: Appearance): void {
  const root = document.documentElement;
  const option = themeOption(theme);
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-palette", option.teamPalette ? palette : "brand");
  document
    .querySelector('meta[name="color-scheme"]')
    ?.setAttribute("content", option.dark ? "dark" : "light");
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_SURFACES[theme].sheet);
}

/**
 * Persist and apply an appearance. Both values are written every time, so
 * the first change after an upgrade also rewrites a legacy id ("custom")
 * as the base + palette it stood for.
 */
export function setAppearance(appearance: Appearance): void {
  writeStorage(THEME_KEY, appearance.theme);
  writeStorage(PALETTE_KEY, appearance.palette);
  applyAppearance(appearance);
}

// ── Favourite team ──────────────────────────────────────────────────────

/** The team the Team palette is coloured by, as stored; null when unset. */
export function getFavoriteTeamId(): string | null {
  const stored = readStorage(FAVORITE_KEY);
  return stored === null || stored === "" ? null : stored;
}

export function setFavoriteTeamId(teamId: string | null): void {
  writeStorage(FAVORITE_KEY, teamId);
}

/**
 * Which followed team is the favourite: the stored choice while that team
 * is still followed, otherwise the first followed team that has a colour
 * (what the accent followed before the choice was explicit), otherwise the
 * first followed team.
 */
export function pickFavoriteTeam<T extends { id: string; color: string | null }>(
  teams: readonly T[],
  storedId: string | null,
): T | null {
  const stored =
    storedId === null ? undefined : teams.find((team) => team.id === storedId);
  return (
    stored ?? teams.find((team) => Boolean(team.color)) ?? teams[0] ?? null
  );
}

// ── Team palette ────────────────────────────────────────────────────────

/**
 * The surfaces a spot fill or spot-derived text can sit on, per base.
 * Mirrors the `--bs-*` values in `index.css` (a test reads the stylesheet
 * and fails if these drift), because contrast has to be measured in JS:
 * the team colour is data, not something CSS can gate.
 */
export const THEME_SURFACES: Readonly<
  Record<
    ThemeId,
    { sheet: string; ground: string; tint: string; block: string; band: string }
  >
> = {
  light: {
    sheet: "#ffffff",
    ground: "#f1f2f4",
    tint: "#f6f7f8",
    block: "#101418",
    band: "#101418",
  },
  dark: {
    sheet: "#171c22",
    ground: "#101418",
    tint: "#13181e",
    block: "#f1f2f4",
    band: "#0a0d10",
  },
  contrast: {
    sheet: "#000000",
    ground: "#000000",
    tint: "#141414",
    block: "#ffffff",
    band: "#000000",
  },
};

/** A fill under this ratio against its surface is lost without a keyline. */
export const EDGE_RATIO = 3;

export type TeamAccentVars = Readonly<{
  "--sd-accent": string;
  "--sd-on-accent": string;
  "--sd-accent-text": string;
  "--sd-accent-edge": string;
  "--sd-accent-edge-block": string;
}>;

/**
 * The Team palette's variables for one team colour on one base, or null
 * when the colour is missing or malformed (the CSS then falls back to the
 * base's own spot).
 *
 * The rule is to keep the team's colour the team's colour, and to make it
 * read by measurement rather than by assumption:
 *
 *   - Text ON the fill is ink or paper, whichever contrasts more.
 *   - A dark colour on the dark base (navy, maroon, black) is all but
 *     invisible, and a keyline around nothing is not a team colour. Its
 *     lightness is raised, hue and saturation kept, only as far as 3:1
 *     against the sheet. This is the job the old Custom theme's
 *     `accentOnDark` did, measured by contrast instead of a fixed luma bar
 *     (so it stops much sooner) and without washing the colour toward white.
 *   - A pale colour on the light base (yellow, white) is left alone: it
 *     still reads as itself, and deepening it would turn a yellow olive.
 *     Where a fill is under 3:1 against what it sits on, it gets a keyline
 *     in the surrounding ink, the way a spot colour is trapped in print.
 *   - Spot-derived TEXT has no edge to draw, so it is moved by `readableOn`
 *     until it clears 4.5:1.
 */
export function teamAccentVars(
  color: string | null | undefined,
  theme: ThemeId,
): TeamAccentVars | null {
  const teamColor = normalizeHex(color);
  if (teamColor === null || !themeOption(theme).teamPalette) return null;
  const surface = THEME_SURFACES[theme];
  const sheets = [surface.sheet, surface.ground, surface.tint];
  const visible =
    theme === "dark" ? liftOn(teamColor, sheets, EDGE_RATIO) : teamColor;
  const { fill, on } = fitFill(visible);
  // Sheet and tint, not the ground: the same surfaces the brand spot is
  // held to in index.css, so an orange team colour is treated exactly like
  // the brand orange (2.97:1 on the Light ground, and unkeyed, as mocked).
  const onSheet = Math.min(
    contrastRatio(fill, surface.sheet),
    contrastRatio(fill, surface.tint),
  );
  const onBlock = Math.min(
    contrastRatio(fill, surface.block),
    contrastRatio(fill, surface.band),
  );
  // The keyline is the ink of whatever the fill sits on: the page's text
  // colour on a sheet, the block's text colour on a block.
  const sheetInk = theme === "light" ? INK : "#f1f2f4";
  const blockInk = theme === "light" ? PAPER : INK;
  return {
    "--sd-accent": fill,
    "--sd-on-accent": on,
    "--sd-accent-text": readableOn(fill, sheets),
    "--sd-accent-edge": onSheet < EDGE_RATIO ? sheetInk : "transparent",
    "--sd-accent-edge-block": onBlock < EDGE_RATIO ? blockInk : "transparent",
  };
}

export const ACCENT_VAR_NAMES = [
  "--sd-accent",
  "--sd-on-accent",
  "--sd-accent-text",
  "--sd-accent-edge",
  "--sd-accent-edge-block",
] as const;

/**
 * Drive the Team palette from a team colour: sets the `--sd-accent*`
 * variables on `<html>` (which `index.css` maps the spot tokens to under
 * `[data-palette="team"]`) and caches them, keyed by base, so the bootstrap
 * can paint the team colour on the very first frame of the next visit
 * instead of flashing the brand orange until `/teams` loads.
 *
 * A missing or malformed colour clears the override, and the CSS fallback
 * (the base's own spot) takes over.
 */
export function setTeamAccent(
  color: string | null | undefined,
  theme: ThemeId,
): void {
  const vars = teamAccentVars(color, theme);
  if (vars === null) {
    clearTeamAccent();
    return;
  }
  const root = document.documentElement;
  for (const name of ACCENT_VAR_NAMES) {
    root.style.setProperty(name, vars[name]);
  }
  writeStorage(ACCENT_CACHE_KEY, JSON.stringify({ theme, vars }));
}

export function clearTeamAccent(): void {
  const root = document.documentElement;
  for (const name of ACCENT_VAR_NAMES) {
    root.style.removeProperty(name);
  }
  writeStorage(ACCENT_CACHE_KEY, null);
}
