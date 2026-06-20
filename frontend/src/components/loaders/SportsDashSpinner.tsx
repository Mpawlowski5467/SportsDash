import SportsDashBall from "../logo/SportsDashBall";

/**
 * Prompt 1 — the everyday spinner. The detailed ball mark rolling 360°
 * continuously. The non-linear easing (cubic-bezier(.5,.15,.5,.85), in
 * index.css `.sd-roll`) gives it a real "roll" rather than a flat mechanical
 * spin. Respects prefers-reduced-motion by freezing on the static mark.
 */

export interface SportsDashSpinnerProps {
  /** Diameter of the mark in px. Defaults to 120. */
  size?: number;
  /** Optional caption underneath (Space Mono, uppercase). e.g. "LOADING". */
  label?: string;
  className?: string;
}

export default function SportsDashSpinner({
  size = 120,
  label,
  className,
}: SportsDashSpinnerProps) {
  return (
    <div
      role="status"
      aria-label={label ?? "Loading"}
      className={className}
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 14,
      }}
    >
      <span className="sd-roll" style={{ display: "inline-flex", lineHeight: 0 }}>
        <SportsDashBall size={size} variant="detailed" />
      </span>
      {label != null ? (
        <span
          style={{
            fontFamily: "var(--sd-font-mono)",
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: "0.14em",
            color: "#8A877C",
          }}
        >
          {label}
        </span>
      ) : null}
    </div>
  );
}
