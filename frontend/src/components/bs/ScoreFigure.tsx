import { useEffect, useRef, useState } from "react";

import { SCORE_FLASH_MS, scoreMoved } from "../../lib/scoreFlash";

/**
 * A score figure that pulses once when its value changes in front of the
 * viewer, and at no other time.
 *
 * The pulse says "this score has just moved", so it must not play when
 * nothing moved. A figure that carried the animation all the time played
 * it on its first paint, and again every time its view was shown: a view
 * that is not the current tab is hidden with `display: none`, and a CSS
 * animation starts over when its element is displayed again. On a kiosk
 * that was every score on the page, every rotation.
 *
 * So the animation class is put on for the length of one pulse, and only
 * when all of these hold:
 *
 * - the figure was already drawn with another value (not its first paint,
 *   and not a figure appearing where there was none);
 * - it is still the same game's figure: when `of` changes, the new value
 *   is another game's score, not a change;
 * - it is on screen at that moment. A score that moves while its view is
 *   hidden is simply the new figure when the view comes back.
 *
 * The first two are `scoreMoved` (lib/scoreFlash, unit-tested); the third
 * is asked of the page here.
 *
 * Reduced motion is the stylesheet's affair, like every other animation
 * here: the class does nothing outside `prefers-reduced-motion:
 * no-preference`.
 */
export default function ScoreFigure({
  value,
  of,
  className = "",
}: {
  /** The figure as drawn; null draws nothing. */
  value: number | string | null;
  /** What the figure counts for: the game's id. */
  of: string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const drawn = useRef({ of, value });
  // `turn` counts the pulses, so that a second change while one is still
  // playing starts a new one; `on` is the class.
  const [pulse, setPulse] = useState({ turn: 0, on: false });

  useEffect(() => {
    const before = drawn.current;
    drawn.current = { of, value };
    if (!scoreMoved(before, drawn.current)) return;
    const element = ref.current;
    // No boxes: the figure, or a view around it, is not displayed.
    if (element === null || element.getClientRects().length === 0) return;
    setPulse((last) => ({ turn: last.turn + 1, on: true }));
  }, [of, value]);

  useEffect(() => {
    if (!pulse.on) return;
    const timer = window.setTimeout(
      () => setPulse((last) => ({ ...last, on: false })),
      SCORE_FLASH_MS + 50,
    );
    return () => window.clearTimeout(timer);
  }, [pulse]);

  return (
    <span
      // A new turn is a new element, which is what starts the animation
      // again from its first frame.
      key={pulse.turn}
      ref={ref}
      className={
        "sd-score inline-block" +
        (pulse.on ? " sd-score--flash" : "") +
        (className === "" ? "" : ` ${className}`)
      }
    >
      {value}
    </span>
  );
}
