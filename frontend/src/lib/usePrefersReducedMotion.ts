import { useEffect, useState } from "react";

/**
 * Tracks the `prefers-reduced-motion: reduce` media query, reactively.
 *
 * Most of the app's motion is handled purely in CSS (every animation is gated
 * behind `prefers-reduced-motion: no-preference`), but a couple of loaders
 * need the value in JS — to stop a status-text interval, or to shorten a
 * splash's auto-advance when there's no animation to wait for.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
