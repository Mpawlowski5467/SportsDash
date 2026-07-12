import type { GameSide } from "../types";

/** Up-to-three-char abbreviation fallback for sides without one. */
export function abbrev(name: string): string {
  return name.slice(0, 3).toUpperCase();
}

/** A side's display label: its abbreviation, else derived from the name. */
export function sideLabel(side: GameSide): string {
  return side.abbreviation ?? abbrev(side.name);
}
