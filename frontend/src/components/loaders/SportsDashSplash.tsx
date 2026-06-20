import { useEffect, type ReactNode } from "react";
import SportsDashBall from "../logo/SportsDashBall";
import { Wordmark } from "../logo/Wordmark";
import { usePrefersReducedMotion } from "../../lib/usePrefersReducedMotion";

/**
 * Prompt 2 — the app-starting splash. Full screen on ink, the detailed mark
 * assembling wedge-by-wedge (the stagger lives in index.css, keyed off the
 * `.sd-splash-ball` wrapper and the ball's `sd-w*` groups) with the wordmark
 * resolving underneath.
 *
 * `loop` (default true) keeps it cycling — that's the looping demo. In
 * production set `loop={false}` and pass `onComplete`: the splash hands off
 * once the mark has assembled (≈2s, or ≈0.4s under reduced motion) so the app
 * can transition in. Honours prefers-reduced-motion (assembled mark + wordmark,
 * no motion).
 */

export interface SportsDashSplashProps {
  loop?: boolean;
  onComplete?: () => void;
  /** Mark size in px. Defaults to 132. */
  size?: number;
  /** Extra content under the wordmark (e.g. an error / retry affordance). */
  children?: ReactNode;
}

export default function SportsDashSplash({
  loop = true,
  onComplete,
  size = 132,
  children,
}: SportsDashSplashProps) {
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (loop || !onComplete) return;
    // Fire while the mark sits on its assembled plateau (pop holds scale 1 and
    // the wordmark is up from ~42%–88% of the 2.8s cycle) so the hand-off never
    // shows the collapsing tail. Under reduced motion there's nothing to wait
    // for, so advance promptly.
    const t = window.setTimeout(onComplete, reduced ? 400 : 2000);
    return () => window.clearTimeout(t);
  }, [loop, onComplete, reduced]);

  return (
    <div
      role="status"
      aria-label="Starting up"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 26,
        backgroundColor: "#15171C",
        color: "#F4F1EC",
        textAlign: "center",
        padding: 24,
      }}
    >
      <span className="sd-splash-ball" style={{ display: "inline-flex", lineHeight: 0 }}>
        <SportsDashBall size={size} variant="detailed" onDark />
      </span>

      <div
        className="sd-splash-word"
        style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 7 }}
      >
        <Wordmark size={23} sportsColor="#F4F1EC" />
        <span
          style={{
            fontFamily: "var(--sd-font-mono)",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.14em",
            color: "#7D7A70",
          }}
        >
          Starting up
        </span>
      </div>

      {children}
    </div>
  );
}
