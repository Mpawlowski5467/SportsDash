/**
 * Persisted visibility toggle for the live scoreboard ticker.
 *
 * The toggle is flipped in SettingsView but the ticker mounts in Layout, so
 * the two components live in different subtrees and can't share React state
 * directly. We back the flag with localStorage and broadcast a custom
 * `"sportsdash:ticker"` event on change so every `useTicker()` consumer
 * re-reads and stays in sync (the native `storage` event only fires in OTHER
 * tabs, so we dispatch our own for same-tab updates).
 */

import { useCallback, useEffect, useState } from "react";

const TICKER_KEY = "sportsdash.ticker";
const TICKER_EVENT = "sportsdash:ticker";

/** Read the persisted flag; defaults to ON (only an explicit "0" hides it). */
export function readTicker(): boolean {
  try {
    return window.localStorage.getItem(TICKER_KEY) !== "0";
  } catch {
    return true;
  }
}

/**
 * `[visible, toggle]`, mirrored to localStorage and synced across every
 * consumer in the tab. Reads once on mount (SSR/private-mode safe).
 */
export function useTicker(): [boolean, () => void] {
  const [visible, setVisible] = useState<boolean>(readTicker);

  useEffect(() => {
    const sync = () => setVisible(readTicker());
    window.addEventListener(TICKER_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TICKER_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const toggle = useCallback(() => {
    setVisible((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(TICKER_KEY, next ? "1" : "0");
      } catch {
        // Private mode / storage disabled — keep the in-memory state.
      }
      window.dispatchEvent(new Event(TICKER_EVENT));
      return next;
    });
  }, []);

  return [visible, toggle];
}
