import type { ButtonHTMLAttributes, ReactNode } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * `outline` is a sheet with a 2px ink frame; `ghost` is the frame alone,
   * for a tinted block or the ground; `solid` is the ink block.
   */
  variant?: "outline" | "ghost" | "solid";
  /** `lg` is the 52px page-level picker. */
  size?: "md" | "lg";
  /** An icon alone: a 44px square. Give the button an `aria-label`. */
  icon?: boolean;
}

/**
 * The square Box Score button. A toggle passes `aria-pressed`, and the
 * pressed state is the solid block: the attribute is the single source of
 * both the semantics and the look.
 */
export default function Button({
  variant = "outline",
  size = "md",
  icon = false,
  className,
  type = "button",
  children,
  ...rest
}: Props) {
  const classes = ["bs-btn"];
  if (variant !== "outline") classes.push(`bs-btn--${variant}`);
  if (size === "lg") classes.push("bs-btn--lg");
  if (icon) classes.push("bs-btn--icon");
  if (className) classes.push(className);
  return (
    <button type={type} className={classes.join(" ")} {...rest}>
      {children}
    </button>
  );
}

/** Inline stroke icon at the size the mockups use beside button text. */
export function Icon({
  children,
  size = 18,
}: {
  children: ReactNode;
  size?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none", width: `${size / 16}rem`, height: `${size / 16}rem` }}
    >
      {children}
    </svg>
  );
}

export function ChevronDown({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M6 9l6 6 6-6" />
    </Icon>
  );
}

/**
 * The icon of a Refresh button: two arrows chasing each other, as the News
 * mockup draws it. `spinning` turns it while the read is under way, and
 * only where motion is welcome: with motion reduced the button has to show
 * its busy state some other way (its label, a set-back frame).
 */
export function RefreshIcon({
  size = 18,
  spinning = false,
}: {
  size?: number;
  spinning?: boolean;
}) {
  return (
    <span className={"inline-flex" + (spinning ? " motion-safe:animate-spin" : "")}>
      <Icon size={size}>
        <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
        <path d="M3 21v-5h5" />
      </Icon>
    </span>
  );
}
