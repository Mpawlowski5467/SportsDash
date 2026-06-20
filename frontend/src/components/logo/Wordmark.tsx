import type { CSSProperties } from "react";
import SportsDashBall from "./SportsDashBall";

/**
 * The "SportsDash" wordmark and its lockups. Space Grotesk 700, -0.02em, with
 * "Sports" in ink and "Dash" in the brand orange #E8643C.
 *
 * "Sports" defaults to `currentColor` so the wordmark adapts to its surface
 * (ink on light cards, near-white on the ink splash) — pass `sportsColor` to
 * pin it. "Dash" stays brand orange across every theme.
 */

const DASH_ORANGE = "#E8643C";

export interface WordmarkProps {
  /** Font size in px. Defaults to 24. */
  size?: number;
  /** Colour of "Sports". Defaults to currentColor (inherits the surface). */
  sportsColor?: string;
  className?: string;
  style?: CSSProperties;
}

export function Wordmark({ size = 24, sportsColor, className, style }: WordmarkProps) {
  return (
    <span
      className={className}
      style={{
        fontFamily: "var(--sd-font-display)",
        fontWeight: 700,
        fontSize: size,
        letterSpacing: "-0.02em",
        lineHeight: 1,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      <span style={{ color: sportsColor ?? "currentColor" }}>Sports</span>
      <span style={{ color: DASH_ORANGE }}>Dash</span>
    </span>
  );
}

export interface LockupProps {
  /** Pixel size of the ball mark. Wordmark scales relative to it. */
  size?: number;
  sportsColor?: string;
  className?: string;
  style?: CSSProperties;
}

/** Detailed mark + wordmark, side by side (gap ~11px). */
export function HorizontalLockup({ size = 40, sportsColor, className, style }: LockupProps) {
  return (
    <span
      className={className}
      style={{ display: "inline-flex", alignItems: "center", gap: 11, ...style }}
    >
      <SportsDashBall size={size} variant="detailed" />
      <Wordmark size={Math.round(size * 0.55)} sportsColor={sportsColor} />
    </span>
  );
}

/** Detailed mark above the wordmark, centred. */
export function StackedLockup({ size = 88, sportsColor, className, style }: LockupProps) {
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: Math.round(size * 0.12),
        ...style,
      }}
    >
      <SportsDashBall size={size} variant="detailed" />
      <Wordmark size={Math.round(size * 0.3)} sportsColor={sportsColor} />
    </span>
  );
}
