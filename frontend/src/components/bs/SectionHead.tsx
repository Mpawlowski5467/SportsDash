import type { ReactNode } from "react";

interface Props {
  /** The label on the inverted tab. */
  title: ReactNode;
  /** A short plain-language note beside the tab. */
  note?: ReactNode;
  /** Trailing content, pushed to the far end: e.g. a "Full standings" link. */
  aside?: ReactNode;
  /**
   * Where the aside sits against the head: centred on the tab (a link), or
   * hung from the rule like the tab (a 44px control taller than the tab).
   */
  asideAlign?: "center" | "start";
  /** Heading level of the tab; sections sit under the page's h1. */
  level?: 2 | 3;
  className?: string;
}

/**
 * The head of every Box Score section: a 3px ink rule carrying an inverted
 * tab, with a note beside it. Put a `.bs-sheet` directly after it and the
 * rule becomes the sheet's top edge.
 */
export default function SectionHead({
  title,
  note,
  aside,
  asideAlign = "center",
  level = 2,
  className,
}: Props) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className={"bs-section-head" + (className ? ` ${className}` : "")}>
      <div className="bs-section-head__main">
        <Heading className="bs-tab">{title}</Heading>
        {note !== undefined && <span className="bs-note">{note}</span>}
      </div>
      {aside !== undefined && (
        <div
          className={
            "bs-section-head__aside" +
            (asideAlign === "start" ? " bs-section-head__aside--start" : "")
          }
        >
          {aside}
        </div>
      )}
    </div>
  );
}
