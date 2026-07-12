import { useEffect, useRef, type RefObject } from "react";

/**
 * Shared overlay chrome. Overlays in this app stack (map panel → team
 * profile → game modal), and each previously hand-rolled its own ESC
 * listener and body scroll-lock save/restore — so one ESC closed every
 * layer at once, and closing stacked overlays out of order could unlock
 * the page behind a still-open dialog. The module-level state here
 * coordinates all of them:
 *
 * - an ESC *stack*: only the topmost overlay responds to Escape;
 * - a *ref-counted* body scroll lock: the original overflow value is
 *   saved once and restored only when the last overlay closes;
 * - focus management: focus moves into the dialog on open, Tab wraps
 *   inside it, and focus returns to the launcher on close.
 */

let scrollLocks = 0;
let savedOverflow = "";
const escStack: symbol[] = [];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), ' +
  '[tabindex]:not([tabindex="-1"])';

/**
 * ESC-to-close for a persistent overlay (e.g. a slide-over panel that
 * stays mounted and toggles `open`). Participates in the shared ESC
 * stack but adds no scroll lock or focus trap — right for non-modal
 * chrome where the page behind stays interactive.
 */
export function useTopmostEsc(onClose: () => void, enabled: boolean): void {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!enabled) return;
    const token = Symbol("overlay");
    escStack.push(token);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (escStack[escStack.length - 1] === token) {
        closeRef.current();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = escStack.indexOf(token);
      if (index !== -1) escStack.splice(index, 1);
    };
  }, [enabled]);
}

/**
 * Full modal chrome: topmost-only ESC, ref-counted body scroll lock,
 * initial focus, Tab trap, and focus restore. Attach the returned ref
 * (plus `tabIndex={-1}`) to the dialog container element.
 */
export function useModalChrome(
  onClose: () => void,
): RefObject<HTMLDivElement | null> {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const token = Symbol("modal");
    escStack.push(token);

    if (scrollLocks === 0) {
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    scrollLocks += 1;

    // Remember the launcher and move focus into the dialog.
    const launcher =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const container = containerRef.current;
    container?.focus({ preventScroll: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (escStack[escStack.length - 1] === token) {
          closeRef.current();
        }
        return;
      }
      if (event.key === "Tab" && container !== null) {
        const focusables = Array.from(
          container.querySelectorAll<HTMLElement>(FOCUSABLE),
        );
        if (focusables.length === 0) {
          event.preventDefault();
          return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || active === container)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = escStack.indexOf(token);
      if (index !== -1) escStack.splice(index, 1);
      scrollLocks -= 1;
      if (scrollLocks === 0) {
        document.body.style.overflow = savedOverflow;
      }
      launcher?.focus({ preventScroll: true });
    };
  }, []);

  return containerRef;
}
