import { createContext, useContext } from "react";

/**
 * A request to focus the Map on a particular game's venue — raised from the
 * team profile's "Next match" card and consumed by MapView. `teamId`/`isHome`
 * let the map fall back to the followed team's home stadium when the game's
 * venue isn't on the map yet (e.g. not geocoded, or beyond the games window).
 */
export interface MapFocusTarget {
  gameId: string;
  venue: string | null;
  teamId: string | null;
  isHome: boolean;
}

interface MapFocusValue {
  target: MapFocusTarget | null;
  /** Set the focus target AND switch to the Map tab. */
  requestFocus: (target: MapFocusTarget) => void;
  /** Clear the target once the map has consumed it. */
  clear: () => void;
}

export const MapFocusContext = createContext<MapFocusValue>({
  target: null,
  requestFocus: () => {},
  clear: () => {},
});

export function useMapFocus(): MapFocusValue {
  return useContext(MapFocusContext);
}
