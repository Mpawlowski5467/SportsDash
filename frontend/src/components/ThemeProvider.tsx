/**
 * Theme context: the base, the palette, and the favourite team.
 *
 * Holds the appearance as React state (initialised from localStorage via
 * `getAppearance()`), exposes a `useTheme()` hook so the SettingsView
 * controls update the whole app live, and — only under the Team palette —
 * drives `--sd-accent` and its companions on <html> from the favourite
 * team's colour.
 *
 * The favourite team is an explicit, persisted choice among the followed
 * teams. Until one is made it is the first followed team with a colour,
 * which is what the accent followed before the choice existed. No API or
 * `types.ts` changes — pure client state over the cached `/teams` payload.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useTeams } from "../hooks";
import {
  THEMES,
  applyAppearance,
  clearTeamAccent,
  getAppearance,
  getFavoriteTeamId,
  pickFavoriteTeam,
  setAppearance,
  setFavoriteTeamId,
  setTeamAccent,
  type Appearance,
  type PaletteId,
  type ThemeId,
} from "../lib/theme";
import type { Team } from "../types";

interface ThemeContextValue {
  theme: ThemeId;
  palette: PaletteId;
  setTheme: (theme: ThemeId) => void;
  setPalette: (palette: PaletteId) => void;
  /** Whether the current base can be recoloured by the Team palette. */
  teamPaletteAvailable: boolean;
  /** The team the Team palette is coloured by; null when none is followed. */
  favoriteTeam: Team | null;
  setFavoriteTeam: (teamId: string) => void;
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
  const [appearance, setAppearanceState] = useState<Appearance>(() =>
    getAppearance(),
  );
  const [favoriteId, setFavoriteId] = useState<string | null>(() =>
    getFavoriteTeamId(),
  );
  const teams = useTeams().data?.teams;

  const update = useCallback((next: Appearance) => {
    setAppearance(next); // localStorage + the DOM attributes
    setAppearanceState(next);
  }, []);

  const { theme, palette } = appearance;
  const setTheme = useCallback(
    (next: ThemeId) => update({ theme: next, palette }),
    [update, palette],
  );
  const setPalette = useCallback(
    (next: PaletteId) => update({ theme, palette: next }),
    [update, theme],
  );

  const setFavoriteTeam = useCallback((teamId: string) => {
    setFavoriteTeamId(teamId);
    setFavoriteId(teamId);
  }, []);

  // Keep the DOM in sync if the appearance ever changes without going
  // through `update` (defensive; `applyAppearance` is idempotent and cheap).
  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);

  // Another tab changed a choice: take it. `update` writes both keys from
  // this tab's state, so a tab holding a stale palette would otherwise write
  // it back over the other tab's choice the next time only the base changed.
  // Reads only, and only when something differs, so two tabs cannot ping-pong.
  useEffect(() => {
    const sync = () => {
      const stored = getAppearance();
      setAppearanceState((current) =>
        current.theme === stored.theme && current.palette === stored.palette
          ? current
          : stored,
      );
      setFavoriteId(getFavoriteTeamId());
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  const favoriteTeam = useMemo(
    () => (teams === undefined ? null : pickFavoriteTeam(teams, favoriteId)),
    [teams, favoriteId],
  );

  const teamPaletteAvailable =
    THEMES.find((option) => option.id === theme)?.teamPalette ?? false;
  const teamPaletteActive = palette === "team" && teamPaletteAvailable;
  const favoriteColor = favoriteTeam?.color ?? null;
  const teamsLoaded = teams !== undefined;

  // The base the accent variables on <html> were measured for. They start
  // out as whatever the bootstrap painted, which is for the boot base.
  const accentBaseRef = useRef<ThemeId>(theme);

  useEffect(() => {
    if (!teamPaletteActive) {
      clearTeamAccent();
      return;
    }
    if (!teamsLoaded) {
      // Until `/teams` answers, leave what the index.html bootstrap painted
      // from its cache: clearing here would flash the brand orange on every
      // load, and would throw the cache away with it. Unless the base has
      // changed since: a lift or a keyline measured for the other base can
      // be unreadable on this one, and the base's own spot never is.
      if (accentBaseRef.current !== theme) {
        clearTeamAccent();
        accentBaseRef.current = theme;
      }
      return;
    }
    setTeamAccent(favoriteColor, theme);
    accentBaseRef.current = theme;
  }, [teamPaletteActive, teamsLoaded, favoriteColor, theme]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      palette,
      setTheme,
      setPalette,
      teamPaletteAvailable,
      favoriteTeam,
      setFavoriteTeam,
    }),
    [
      theme,
      palette,
      setTheme,
      setPalette,
      teamPaletteAvailable,
      favoriteTeam,
      setFavoriteTeam,
    ],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
