import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * Whether an element's content is wider than the element, kept current as
 * either changes size. For a strip that scrolls sideways only when it has
 * to: it is a keyboard stop and a named region while it scrolls, and plain
 * layout when everything fits.
 */
export function useOverflowing<T extends HTMLElement>(): [
  RefObject<T | null>,
  boolean,
] {
  const ref = useRef<T | null>(null);
  const [overflowing, setOverflowing] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const measure = () => {
      setOverflowing(element.scrollWidth > element.clientWidth + 1);
    };
    measure();
    // Measured once where there is no observer (an old browser, a test).
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    for (const child of element.children) observer.observe(child);
    return () => observer.disconnect();
  }, []);

  return [ref, overflowing];
}
