import { createPortal } from "react-dom";

/**
 * Renders children into `document.body` so fixed-position overlays (modals,
 * slide-in panels) are positioned relative to the viewport — not whichever
 * ancestor happens to establish a containing block. The view wrapper
 * (`sd-view-enter`) animates `transform`, which would otherwise anchor a
 * `position: fixed` modal to the content column instead of the screen.
 */
export default function Portal({ children }: { children: React.ReactNode }) {
  return createPortal(children, document.body);
}
