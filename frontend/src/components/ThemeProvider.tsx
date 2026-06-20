/**
 * Theme context + stadium dynamic-accent effect (Phase 8).
 *
 * Holds the active theme as React state (initialised from localStorage via
 * `getTheme()`), exposes a `useTheme()` hook so the SettingsView switcher
 * updates the whole app live, and — only when the theme is "stadium" — sets
 * `--sd-accent` on <html> from the color of the team currently in focus.
 *
 * "In focus" is kept deliberately simple and safe: the first followed team
 * that has a color (from the cached `/teams` payload). When none is
 * available the override is cleared so the CSS fallback (a vibrant amber)
 * takes over. No API or `types.ts` changes — pure client state.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useTeams } from "../hooks";
import {
  applyTheme,
  clearStadiumAccent,
  getTheme,
  setStadiumAccent,
  setTheme as persistTheme,
  type ThemeId,
} from "../lib/theme";

interface ThemeContextValue {
  theme: ThemeId;
  setTheme: (theme: ThemeId) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (ctx === null) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}

export default function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => getTheme());
  const teamsQuery = useTeams();

  const setTheme = useCallback((next: ThemeId) => {
    persistTheme(next); // localStorage + applyTheme + clears accent if leaving stadium
    setThemeState(next);
  }, []);

  // Keep the DOM attribute in sync if `theme` ever changes without going
  // through `setTheme` (defensive; `applyTheme` is idempotent and cheap).
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Stadium dynamic accent: drive --sd-accent from the focused team's color.
  // Only active under the stadium theme; otherwise the override is cleared.
  const focusColor = useMemo(() => {
    const teams = teamsQuery.data?.teams ?? [];
    return teams.find((team) => Boolean(team.color))?.color ?? null;
  }, [teamsQuery.data]);

  useEffect(() => {
    if (theme === "stadium") {
      setStadiumAccent(focusColor);
    } else {
      clearStadiumAccent();
    }
    return () => {
      // Tidy up on unmount so a leftover override can't bleed into a
      // non-stadium theme on a future mount.
      clearStadiumAccent();
    };
  }, [theme, focusColor]);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme }),
    [theme, setTheme],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
