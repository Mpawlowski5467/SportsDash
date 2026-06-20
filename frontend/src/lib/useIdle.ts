/**
 * Idle-detection hook.
 *
 * Returns `true` once `ms` milliseconds have elapsed with no user
 * interaction, flipping back to `false` the instant the user does
 * anything. Activity is sampled from the usual pointer/keyboard/scroll/
 * touch events on `window`; the timer is debounced so a busy stream of
 * events (e.g. mousemove) doesn't reschedule on every tick.
 *
 * Passing `enabled: false` parks the hook: it tears down its listeners,
 * cancels any pending timer, and reports `false` (never idle). This lets
 * callers cheaply turn idle-watching on and off (e.g. only in kiosk mode)
 * without conditionally calling the hook.
 */

import { useEffect, useRef, useState } from "react";

const ACTIVITY_EVENTS: readonly string[] = [
  "mousemove",
  "mousedown",
  "keydown",
  "wheel",
  "touchstart",
  "scroll",
  "pointerdown",
];

export function useIdle(ms: number, enabled = true): boolean {
  const [idle, setIdle] = useState(false);
  // Hold the latest setter in a ref so the effect can depend only on
  // [ms, enabled] and never re-bind listeners just because React handed
  // us a new closure.
  const idleRef = useRef(idle);
  idleRef.current = idle;

  useEffect(() => {
    if (!enabled) {
      // Parked: make sure we're reported as active and bail before
      // wiring anything up.
      if (idleRef.current) setIdle(false);
      return;
    }

    let timer = 0;

    const goIdle = () => {
      if (!idleRef.current) setIdle(true);
    };

    const reset = () => {
      if (idleRef.current) setIdle(false);
      window.clearTimeout(timer);
      timer = window.setTimeout(goIdle, ms);
    };

    for (const event of ACTIVITY_EVENTS) {
      window.addEventListener(event, reset, { passive: true });
    }
    timer = window.setTimeout(goIdle, ms);

    return () => {
      window.clearTimeout(timer);
      for (const event of ACTIVITY_EVENTS) {
        window.removeEventListener(event, reset);
      }
    };
  }, [ms, enabled]);

  return enabled && idle;
}
