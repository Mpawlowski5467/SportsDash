import type { ReactNode } from "react";

/**
 * The shape of what is on its way, pulsing, while a first payload loads.
 * By default a section: a tab over a sheet of rows. A page with a shape of
 * its own passes that shape as children.
 *
 * What is loading is said in words a screen reader is given, in a status
 * region, since the shapes themselves say nothing.
 */
export default function Loading({
  label,
  rows = 5,
  className,
  children,
}: {
  /** What is loading: "Loading standings". */
  label: string;
  /** How many rows the default section has. */
  rows?: number;
  className?: string;
  /** The page's own shapes, in place of the default section. */
  children?: ReactNode;
}) {
  return (
    <div
      aria-busy="true"
      className={"animate-pulse" + (className ? ` ${className}` : "")}
    >
      <p role="status" className="sr-only">
        {label}
      </p>
      {children ?? (
        <>
          <div className="border-t-[3px] border-rule">
            <div className="h-[2.125rem] w-32 bg-hair" />
          </div>
          <div className="border border-t-0 border-rule bg-tint">
            {Array.from({ length: rows }, (_, index) => (
              <div
                key={index}
                className="h-11 border-t border-hair first:border-t-0"
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
