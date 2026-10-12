import type { ReactNode } from "react";

import { useOverflowing } from "../../lib/useOverflowing";

/**
 * A strip that scrolls sideways inside its sheet when it does not fit (a
 * wide table on a phone, a bracket), rather than pushing the page. While
 * it scrolls it is a named group and a keyboard stop, so the arrow keys
 * can reach what is off screen; when everything fits it is plain layout
 * and adds no stop.
 */
export default function ScrollRegion({
  label,
  className,
  children,
}: {
  /** What is in the strip: "Line score", "Next seven days". */
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const [ref, overflowing] = useOverflowing<HTMLDivElement>();
  return (
    <div
      ref={ref}
      // `relative`: the strip is the containing block of anything absolutely
      // positioned inside it (visually hidden text), so that is clipped with
      // the rest instead of widening the page from an off-screen column.
      className={
        "bs-focus-inset relative overflow-x-auto" +
        (className ? ` ${className}` : "")
      }
      {...(overflowing
        ? { role: "group", "aria-label": `${label}, scrolls sideways`, tabIndex: 0 }
        : {})}
    >
      {children}
    </div>
  );
}
