import type { KeyboardEvent, ReactNode } from "react";

interface Option<T extends string> {
  value: T;
  label: ReactNode;
  /** Accessible name, when the visible label is not enough on its own. */
  ariaLabel?: string;
  /** For `kind="tabs"`: the id of the panel this tab shows. */
  controls?: string;
}

interface Props<T extends string> {
  /** Accessible name of the group. */
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: readonly Option<T>[];
  /**
   * `toggle` is a group of pressed/unpressed buttons (a filter). `tabs` is a
   * tablist whose items select a view below it.
   */
  kind?: "toggle" | "tabs";
  /** Single row, sized to sit level with a 44px button beside it. */
  joined?: boolean;
  className?: string;
}

/**
 * Buttons sharing one 2px ink frame; the chosen one is the ink block. The
 * frame is the control's own background showing through the gaps, so the
 * items can wrap at phone width and still read as one control.
 *
 * As tabs, the arrow keys, Home and End move between items and select as
 * they go. Every tab also stays a Tab stop, as the League sub-tabs always
 * were, so nothing a keyboard user already does stops working.
 */
export default function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
  kind = "toggle",
  joined = false,
  className,
}: Props<T>) {
  const classes = ["bs-seg"];
  if (joined) classes.push("bs-seg--joined");
  if (className) classes.push(className);
  const tabs = kind === "tabs";

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // From the tab that has focus, which Tab can move off the selected one
    // (every tab is a stop); the selected tab only when focus is elsewhere.
    const tabStops = [
      ...event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'),
    ];
    const focused = tabStops.indexOf(document.activeElement as HTMLElement);
    const current =
      focused === -1
        ? options.findIndex((option) => option.value === value)
        : focused;
    const step: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: options.length - 1,
    };
    if (!(event.key in step) || options.length === 0) return;
    event.preventDefault();
    const next = (step[event.key] + options.length) % options.length;
    onChange(options[next].value);
    tabStops[next]?.focus();
  };

  return (
    <div
      role={tabs ? "tablist" : "group"}
      aria-label={label}
      className={classes.join(" ")}
      onKeyDown={tabs ? onKeyDown : undefined}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-label={option.ariaLabel}
            {...(tabs
              ? {
                  role: "tab",
                  "aria-selected": selected,
                  "aria-controls": option.controls,
                }
              : { "aria-pressed": selected })}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
