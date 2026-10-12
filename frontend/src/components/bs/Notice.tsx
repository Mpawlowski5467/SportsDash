import type { ReactNode } from "react";

import SectionHead from "./SectionHead";

/**
 * A section holding one message instead of its content: nothing to show
 * yet, nothing to show at all, or a request that failed with nothing cached.
 * A failure is an alert, set in the alert colour, and its action (Retry) is
 * the solid button.
 *
 * The first line carries the weight: the headline when there is one, and
 * otherwise the message itself.
 */
export default function Notice({
  title,
  headline,
  children,
  action,
  alert = false,
  dense = false,
  className,
}: {
  /** The section tab. */
  title: string;
  /** A few words, large. */
  headline?: string;
  /** The message: a sentence under the headline, or the whole of it. */
  children?: ReactNode;
  action?: { label: string; onClick: () => void };
  alert?: boolean;
  /**
   * The sheet's margin drops from 20px to 16px: for a notice standing in
   * for a block whose own rows are set 16px in.
   */
  dense?: boolean;
  className?: string;
}) {
  return (
    <section aria-label={title} className={className}>
      <SectionHead title={title} />
      <div
        role={alert ? "alert" : undefined}
        className={
          "bs-sheet flex flex-wrap items-center justify-between gap-y-3 " +
          (dense ? "gap-x-4 p-4" : "gap-x-6 p-5")
        }
      >
        <div className="min-w-0 max-w-[44rem]">
          {headline !== undefined && (
            <p
              className={
                "m-0 font-condensed text-3xl font-bold uppercase leading-none" +
                (alert ? " text-alert" : "")
              }
            >
              {headline}
            </p>
          )}
          {children !== undefined && (
            <p
              className={
                "m-0 " +
                (headline !== undefined
                  ? "mt-2 text-ink-2"
                  : alert
                    ? "font-medium text-alert"
                    : "text-ink-2")
              }
            >
              {children}
            </p>
          )}
        </div>
        {action !== undefined && (
          <button
            type="button"
            onClick={action.onClick}
            className={"bs-btn" + (alert ? " bs-btn--solid" : "")}
          >
            {action.label}
          </button>
        )}
      </div>
    </section>
  );
}
