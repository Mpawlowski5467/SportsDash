import type { CSSProperties } from "react";

import { contrastRatio, fitFill, normalizeHex } from "../../lib/color";
import { THEME_SURFACES } from "../../lib/theme";

/** Width in px at the default scale. 34 and 40 are 26px tall; the rest are square. */
export type TeamBlockSize = 34 | 40 | 52 | 56 | 60 | 64;

interface Props {
  /** Full name: the accessible label, and the source of a missing code. */
  name: string;
  /** Short code shown in the block; derived from the name when absent. */
  abbreviation?: string | null;
  /** The team's colour. Without one the block is the neutral tint. */
  color?: string | null;
  /** 34 rows (default), 40 the Matchup picker, 52-60 panels, 64 the live game. */
  size?: TeamBlockSize;
  /**
   * Announce the team name. Off by default, because the block almost always
   * sits beside the name in text and would otherwise be read twice.
   */
  labelled?: boolean;
  className?: string;
}

/** The longest code a block can hold (set tighter past three). */
const MAX_CODE = 4;

/**
 * The code a block shows. A provider code is used when it fits. Athletes
 * arrive with a clipped short name instead ("J. Sinne" for Jannik Sinner),
 * which does not fit and is not a code, so those fall back to initials of
 * the full name, like a side with no code at all.
 */
export function teamCode(name: string, abbreviation?: string | null): string {
  const given = (abbreviation ?? "").replace(/[^\p{L}\p{N}]/gu, "");
  if (given.length > 0 && given.length <= MAX_CODE) return given.toUpperCase();
  const words = name
    .trim()
    .split(/\s+/)
    .filter((word) => /^[\p{L}\p{N}]/u.test(word));
  if (words.length >= 2) {
    return words
      .slice(0, 3)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
  }
  return (words[0] ?? "").slice(0, 3).toUpperCase();
}

/** Under this ratio against a sheet, a block's edge is lost without a keyline. */
const EDGE_RATIO = 1.5;
const SHEETS = Object.values(THEME_SURFACES).map((surface) => surface.sheet);

/**
 * A team colour as a fill: the colour, the text that reads on it, and
 * whether it needs a keyline to stand off a sheet. Null without a usable
 * colour. Shared with the bars that are drawn in two teams' colours.
 */
export function teamFill(
  color: string | null | undefined,
  /** 4.5 for the everyday bases; 7 for High contrast. */
  minRatio = 4.5,
): { fill: string; on: string; edge: boolean } | null {
  if (normalizeHex(color) === null) return null;
  const fitted = fitFill(color as string, minRatio);
  // A keyline only where the fill would dissolve into a sheet: next to
  // white (Light) or to the dark sheets (Dark, High contrast). The caller
  // cannot see the theme, so all are checked; a keyline on a fill that did
  // not need one in this theme is near-invisible.
  const edge = SHEETS.some(
    (sheet) => contrastRatio(fitted.fill, sheet) < EDGE_RATIO,
  );
  return { ...fitted, edge };
}

/**
 * The Box Score team mark: a square block of the team's colour carrying its
 * code. The text on it is ink or paper by measured contrast, never assumed
 * from the hue, so a navy block and a yellow one both read.
 */
export default function TeamBlock({
  name,
  abbreviation,
  color,
  size = 34,
  labelled = false,
  className,
}: Props) {
  const code = teamCode(name, abbreviation);
  const fitted = teamFill(color);
  // The same colour held to High contrast's 7:1: the fill is nudged further
  // until ink or paper reads on it at AAA. The component cannot see the
  // theme (the gallery renders it outside the provider), so it hands CSS
  // both pairs as custom properties and `.bs-team` picks by theme.
  const strict = teamFill(color, 7);
  // The neutral block (no colour) is always keylined.
  const edge = fitted === null || fitted.edge;

  const classes = ["bs-team"];
  if (size !== 34) classes.push(`bs-team--${size}`);
  if (code.length > 3) classes.push("bs-team--tight");
  if (edge) classes.push("bs-team--edge");
  if (className) classes.push(className);

  return (
    <span
      className={classes.join(" ")}
      style={
        fitted === null || strict === null
          ? undefined
          : ({
              "--bs-team-fill": fitted.fill,
              "--bs-team-on": fitted.on,
              "--bs-team-fill-aaa": strict.fill,
              "--bs-team-on-aaa": strict.on,
            } as CSSProperties)
      }
      {...(labelled
        ? { role: "img", "aria-label": name }
        : { "aria-hidden": true })}
    >
      {code}
    </span>
  );
}
