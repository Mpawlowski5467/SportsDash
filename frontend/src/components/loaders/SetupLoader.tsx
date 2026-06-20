import { useEffect, useState, type CSSProperties } from "react";
import SportsDashBall from "../logo/SportsDashBall";
import { usePrefersReducedMotion } from "../../lib/usePrefersReducedMotion";

/**
 * Prompt 3 — the new-user "Setting up your dashboard" build loader. A breathing
 * mark with an orbiting accent arc on the left, status + progress on the right,
 * on a white card.
 *
 * Demo mode (no props): the status text cycles and the bar loops. In production
 * pass real `progress` (0–1) and `status` to drive them; the bar becomes
 * determinate and the cycling stops. Honours prefers-reduced-motion (no
 * spin/breathe via CSS gating; the status stops cycling and the bar shows a
 * calm static fill instead of looping).
 */

const DEMO_STATUSES = [
  "Pulling in your leagues…",
  "Syncing live scores…",
  "Arranging your widgets…",
  "Almost ready…",
] as const;

const MARK_PX = 132; // mark + orbit, centred in the 150px square

export interface SetupLoaderProps {
  /** Real progress 0–1. When provided the bar is determinate (controlled). */
  progress?: number;
  /** Real status line. Overrides the cycling demo copy. */
  status?: string;
  className?: string;
  style?: CSSProperties;
}

export default function SetupLoader({ progress, status, className, style }: SetupLoaderProps) {
  const reduced = usePrefersReducedMotion();
  const controlled = progress != null;
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (controlled || reduced) return;
    const t = window.setInterval(
      () => setIdx((i) => (i + 1) % DEMO_STATUSES.length),
      1600,
    );
    return () => window.clearInterval(t);
  }, [controlled, reduced]);

  const statusText = status ?? DEMO_STATUSES[idx];
  const pct = controlled ? Math.max(0, Math.min(1, progress)) * 100 : null;
  // Controlled → exact width; reduced demo → a calm static fill; animated demo
  // → width left to the looping `sd-bar` keyframe.
  const fillWidth = pct != null ? `${pct}%` : reduced ? "66%" : undefined;
  const fillClass = pct == null && !reduced ? "sd-setup-bar-fill--demo" : undefined;

  return (
    <div
      role="status"
      aria-label="Setting up your dashboard"
      className={className}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 28,
        maxWidth: 600,
        padding: "26px 30px",
        borderRadius: 18,
        backgroundColor: "#FFFFFF",
        boxShadow: "0 18px 48px -24px rgba(21, 23, 28, 0.45)",
        color: "#15171C",
        ...style,
      }}
    >
      {/* LEFT — 150px square: breathing mark + orbiting accent arc. */}
      <div
        style={{
          position: "relative",
          width: 150,
          height: 150,
          flexShrink: 0,
          display: "grid",
          placeItems: "center",
        }}
      >
        <span className="sd-breathe" style={{ display: "inline-flex", lineHeight: 0 }}>
          <SportsDashBall size={MARK_PX} variant="detailed" />
        </span>
        <svg
          className="sd-orbit"
          width={MARK_PX}
          height={MARK_PX}
          viewBox="0 0 120 120"
          aria-hidden="true"
          style={{ position: "absolute", inset: 0, margin: "auto" }}
        >
          <circle
            cx="60"
            cy="60"
            r="57"
            fill="none"
            stroke="#E8643C"
            strokeWidth="3.4"
            strokeLinecap="round"
            strokeDasharray="64 294"
          />
        </svg>
      </div>

      {/* RIGHT — eyebrow, heading, status row, progress bar. */}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontFamily: "var(--sd-font-mono)",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.16em",
            color: "#A9A69B",
          }}
        >
          NEW USER · SETTING UP
        </div>
        <h2
          style={{
            fontFamily: "var(--sd-font-display)",
            fontWeight: 700,
            fontSize: 24,
            letterSpacing: "-0.01em",
            color: "#15171C",
            margin: "6px 0 0",
          }}
        >
          Setting up your dashboard
        </h2>

        <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 14 }}>
          <span
            className="sd-setup-dot"
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              backgroundColor: "#E8643C",
              flexShrink: 0,
            }}
          />
          <span style={{ fontSize: 14, color: "#55534B" }}>{statusText}</span>
        </div>

        <div
          style={{
            marginTop: 14,
            height: 7,
            maxWidth: 420,
            borderRadius: 4,
            backgroundColor: "#ECEAE3",
            overflow: "hidden",
          }}
        >
          <div
            className={fillClass}
            style={{
              height: "100%",
              borderRadius: 4,
              backgroundColor: "#E8643C",
              width: fillWidth,
            }}
          />
        </div>
      </div>
    </div>
  );
}
