import type { ReactNode } from "react";

/**
 * The followed-team marker: a small square of the spot colour. Announced as
 * "Followed team" unless a parent already says so.
 */
export function FollowMark({
  size = "sm",
  boxed = false,
  label = "Followed team",
  className,
}: {
  /** sm 10px (rows), lg 12px (beside a 40px team name). */
  size?: "sm" | "lg";
  /** Set in a 20px ink box: the legend form, for a light sheet. */
  boxed?: boolean;
  label?: string | null;
  className?: string;
}) {
  const a11y =
    label === null
      ? ({ "aria-hidden": true } as const)
      : ({ role: "img", "aria-label": label } as const);
  const mark = "bs-mark" + (size === "lg" ? " bs-mark--lg" : "");
  if (boxed) {
    return (
      <span
        className={"bs-mark-box" + (className ? ` ${className}` : "")}
        {...a11y}
      >
        <span className={mark} />
      </span>
    );
  }
  return (
    <span className={mark + (className ? ` ${className}` : "")} {...a11y} />
  );
}

/** A label on the spot fill ("Live", "Today"): ink on colour, never colour as text. */
export function SpotChip({
  children,
  size = "md",
  className,
}: {
  children: ReactNode;
  /** sm 13px over a date cell, md 15px (rows, the score line), lg 20px by a big score. */
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  return (
    <span
      className={
        "bs-spot" +
        (size === "md" ? "" : ` bs-spot--${size}`) +
        (className ? ` ${className}` : "")
      }
    >
      {children}
    </span>
  );
}

/** An outlined ink tag ("Leader"). */
export function Tag({
  children,
  title,
  className,
}: {
  children: ReactNode;
  /** A longer explanation, shown on hover. */
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={"bs-tag" + (className ? ` ${className}` : "")}
    >
      {children}
    </span>
  );
}
