import SportsDashBall from "../logo/SportsDashBall";

/**
 * Prompt 4 — the tiny inline spinner (≤20px) for nav bars, buttons and toasts.
 * Uses the FLAT mark (seams are invisible this small) rotating 360° at a flat
 * 1s linear spin. Sits inline next to text; respects prefers-reduced-motion by
 * freezing on the static flat mark.
 */

export interface MiniSpinnerProps {
  /** Diameter in px. Defaults to 18. */
  size?: number;
  /** Accessible label. Defaults to "Loading". */
  label?: string;
  className?: string;
}

export default function MiniSpinner({ size = 18, label, className }: MiniSpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label ?? "Loading"}
      className={className}
      style={{ display: "inline-flex", lineHeight: 0, verticalAlign: "middle" }}
    >
      <span className="sd-mini-spin" style={{ display: "inline-flex", lineHeight: 0 }}>
        <SportsDashBall size={size} variant="flat" />
      </span>
    </span>
  );
}
