/**
 * Theme system (Phase 8).
 *
 * Themes are implemented WITHOUT touching component markup: Tailwind v4
 * compiles utility classes like `bg-zinc-900` / `text-amber-400` to
 * `var(--color-zinc-900)` / `var(--color-amber-400)`. We re-theme by
 * remapping those palette vars under a `[data-theme]` attribute on
 * `<html>` (see `index.css`). This module owns the JS side: reading and
 * persisting the choice, applying the attribute to `document.documentElement`,
 * and the canonical theme list.
 *
 * Pure client state — no API, no `types.ts` changes.
 */

import { accentOnDark } from "./color";

export type ThemeId = "dark" | "light" | "custom" | "contrast";

/**
 * Themes that used to exist, and what a stored choice maps to now.
 *
 * The id is persisted in localStorage, so dropping one has to be handled
 * rather than ignored: without this a returning user's stored "stadium"
 * would fail validation and silently reset them to the default. "stadium"
 * was renamed (same behaviour, clearer name); the rest were retired and
 * land on the closest survivor.
 */
const LEGACY_THEME_IDS: Record<string, ThemeId> = {
  stadium: "custom",
  floodlight: "dark",
  ember: "dark",
  newsprint: "light",
};

export interface ThemeOption {
  id: ThemeId;
  label: string;
  /** One-line description for the settings switcher. */
  description: string;
  /** Whether the theme renders on a dark base (affects `color-scheme`). */
  dark: boolean;
}

/** Canonical, ordered list of selectable themes. */
export const THEMES: readonly ThemeOption[] = [
  {
    id: "dark",
    label: "Dark",
    description: "The default kiosk look — zinc on near-black with amber accents.",
    dark: true,
  },
  {
    id: "light",
    label: "Light",
    description: "Near-white surfaces with dark text, for bright rooms.",
    dark: false,
  },
  {
    id: "custom",
    label: "Custom",
    description: "The dark base, accented with your followed team's colors.",
    dark: true,
  },
  {
    id: "contrast",
    label: "High contrast",
    description: "True black and white type, every text tone at WCAG AAA.",
    dark: true,
  },
];

export const DEFAULT_THEME: ThemeId = "dark";

const STORAGE_KEY = "sportsdash.theme";

const THEME_IDS = new Set<ThemeId>(THEMES.map((theme) => theme.id));

function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && THEME_IDS.has(value as ThemeId);
}

/**
 * Read the persisted theme, falling back to the default. Safe to call
 * before React mounts and in environments without `localStorage` (the
 * read is wrapped — a throwing/blocked storage just yields the default).
 */
export function getTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isThemeId(stored)) {
      return stored;
    }
    if (stored !== null && Object.hasOwn(LEGACY_THEME_IDS, stored)) {
      return LEGACY_THEME_IDS[stored];
    }
  } catch {
    // Storage unavailable (private mode, disabled cookies) — use default.
  }
  return DEFAULT_THEME;
}

/**
 * Apply a theme to `<html>` by setting `data-theme`. The default/dark
 * theme leaves the attribute set to "dark" so a stale attribute from a
 * previous theme is always cleared. This is the single source of the DOM
 * effect and is called both from the no-FOUC bootstrap and `setTheme`.
 */
export function applyTheme(theme: ThemeId): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  // Keep the legacy `dark` class (set in index.html) in sync so any
  // `dark:`-prefixed Tailwind variants still resolve on dark-based themes.
  const option = THEMES.find((t) => t.id === theme);
  root.classList.toggle("dark", option?.dark ?? true);
}

/**
 * Persist and apply a theme. When switching away from "custom" the
 * dynamic `--sd-accent` override is cleared so the next dark/stadium
 * session starts from the default accent.
 */
export function setTheme(theme: ThemeId): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Persisting is best-effort; still apply for this session.
  }
  if (theme !== "custom") {
    clearCustomAccent();
  }
  applyTheme(theme);
}

/**
 * The Custom theme's dynamic accent. Sets the `--sd-accent` custom
 * property on `<html>`; `index.css` maps the amber ramp to it under
 * `[data-theme="custom"]`. A no-op (clears the override) when given a
 * falsy color so the CSS fallback (a vibrant amber) takes over.
 *
 * The accent lands on TEXT over the dark base, so a too-dark team color
 * (navy, maroon, black) would be near-invisible: it is lifted toward a
 * readable tone (hue preserved) before being applied.
 */
export function setCustomAccent(color: string | null | undefined): void {
  const root = document.documentElement;
  if (color) {
    root.style.setProperty("--sd-accent", accentOnDark(color));
  } else {
    clearCustomAccent();
  }
}

export function clearCustomAccent(): void {
  document.documentElement.style.removeProperty("--sd-accent");
}
