import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { NewsItem } from "../types";
import {
  dateLine,
  dateParts,
  deckOf,
  headlineOf,
  inCapitals,
} from "../views/news/model";
import { HeadlineText, SourceArrow } from "../views/news/parts";
import { Button, Icon, TeamBlock } from "./bs";
import Portal from "./Portal";
import { usePanelChrome } from "./modalChrome";

/** Who the story is for: a followed team, an athlete or a competition. */
export interface NewsSource {
  name: string;
  /** The team's colour; null for a competition, and for most athletes. */
  color: string | null;
  /** The code for its block; derived from the name when absent. */
  abbreviation?: string | null;
  /**
   * Set after the name, for a person: the league, since a story filed
   * under a player may be the tour's rather than the player's own.
   */
  context?: string | null;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), ' +
  '[tabindex]:not([tabindex="-1"])';

/**
 * The in-app reader: a Box Score sheet that slides in from the right with
 * the story's picture, who it is for, the headline, the summary and when it
 * was published, over a solid "Read at source" button. A row on the News
 * screen opens it instead of leaving the app.
 *
 * It is a modal dialog, named by its headline. While open: focus is moved
 * into it and held there (Tab wraps), Escape and a click on the dimmed page
 * close it, the page behind does not scroll, and focus goes back to the row
 * it was opened from. It stays mounted so it can slide both ways, and is
 * `inert` while closed, so nothing in it can be tabbed to off screen. It
 * keeps the last story rendered while it slides out.
 *
 * Where the story is taller than the window, its text is a named stop
 * between Close and the link, so the arrow keys can scroll it: not every
 * browser lets a scrolling box take focus by itself. Where it fits, there
 * is no such stop.
 */
export default function NewsDetailPanel({
  item,
  source,
  onClose,
}: {
  item: NewsItem | null;
  source: NewsSource | undefined;
  onClose: () => void;
}) {
  const open = item !== null;
  const titleId = useId();

  // The story and who it is for, kept after `item` goes back to null so the
  // sheet does not empty as it slides away.
  const [last, setLast] = useState<{
    item: NewsItem;
    source: NewsSource | undefined;
  } | null>(item === null ? null : { item, source });
  useEffect(() => {
    if (item !== null) setLast({ item, source });
  }, [item, source]);
  const shown = item ?? last?.item ?? null;
  const shownSource = item !== null ? source : last?.source;

  // Shared chrome while open: topmost-only Escape (a modal stacked above
  // this keeps Escape priority), the ref-counted scroll lock, and focus
  // moved into the sheet and returned to the launcher on close.
  const panelRef = usePanelChrome(onClose, open);

  // Tab stays inside the sheet. `usePanelChrome` is for panels that leave
  // the page usable; this one is modal, so it holds the Tab key as well.
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const panel = event.currentTarget;
    const stops = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (stops.length === 0) {
      event.preventDefault();
      return;
    }
    const first = stops[0];
    const final = stops[stops.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      final.focus();
    } else if (!event.shiftKey && active === final) {
      event.preventDefault();
      first.focus();
    }
  };

  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => {
    setImageFailed(false);
  }, [shown?.id]);

  // Whether the story is taller than the room the sheet gives it, kept
  // current as the window, the picture or the story changes.
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [scrolls, setScrolls] = useState(false);
  const shownId = shown?.id;
  useEffect(() => {
    const body = bodyRef.current;
    if (body === null) return;
    const measure = () => {
      setScrolls(body.scrollHeight > body.clientHeight + 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(body);
    for (const child of body.children) observer.observe(child);
    return () => observer.disconnect();
  }, [shownId, imageFailed]);

  const headline = shown === null ? "" : headlineOf(shown);
  const summary = shown === null ? null : deckOf(shown);
  const date = shown === null ? null : dateParts(shown.published_at);

  return (
    <Portal>
      <div
        className={
          "fixed inset-0 z-50 overflow-hidden " +
          (open ? "" : "pointer-events-none")
        }
      >
        {/* The page behind, dimmed; a click on it closes the reader. */}
        <div
          aria-hidden="true"
          onClick={onClose}
          className={
            "absolute inset-0 bg-black/60 transition-opacity duration-300 motion-reduce:transition-none " +
            (open ? "opacity-100" : "opacity-0")
          }
        />
        <aside
          ref={panelRef}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          // Mounted while closed: `inert` takes the whole sheet out of the
          // tab order and the accessibility tree until it opens, and
          // `aria-hidden` says the same to whatever does not know `inert`.
          inert={!open}
          aria-hidden={open ? undefined : true}
          onKeyDown={onKeyDown}
          className={
            "absolute inset-y-0 right-0 flex w-[30rem] max-w-[92vw] flex-col border-l-[3px] border-ink bg-sheet text-ink outline-none " +
            "transition-transform duration-300 ease-out motion-reduce:transition-none " +
            (open ? "translate-x-0" : "translate-x-full")
          }
        >
          {shown && (
            <>
              <header className="bs-section-head flex-none flex-nowrap border-b border-b-rule">
                <div className="bs-section-head__main min-w-0 flex-nowrap">
                  <p className="bs-tab flex-none self-start">Story</p>
                  {/* Wraps rather than cuts: this is the only place the
                      reader names the outlet. */}
                  <span className="bs-note min-w-0 leading-tight [overflow-wrap:anywhere]">
                    {shown.source}
                  </span>
                </div>
                <div className="bs-section-head__aside bs-section-head__aside--start flex-none p-2.5">
                  <Button icon onClick={onClose} aria-label="Close story">
                    <Icon size={20}>
                      <path d="M6 6l12 12M18 6L6 18" />
                    </Icon>
                  </Button>
                </div>
              </header>

              <div
                ref={bodyRef}
                className="bs-focus-inset min-h-0 flex-1 overflow-y-auto"
                {...(scrolls
                  ? { role: "group", "aria-label": "Story text, scrolls", tabIndex: 0 }
                  : {})}
              >
                {shown.image_url && !imageFailed && (
                  <img
                    src={shown.image_url}
                    alt=""
                    className="aspect-video w-full border-b border-rule bg-tint object-cover"
                    onError={() => setImageFailed(true)}
                  />
                )}

                <div className="flex flex-col gap-3.5 px-5 pb-6 pt-5">
                  {shownSource !== undefined && shownSource.name !== "" && (
                    <p className="m-0 flex min-w-0 items-center gap-2.5 font-condensed text-lg font-semibold uppercase leading-none tracking-[0.04em] text-ink-2">
                      <TeamBlock
                        name={shownSource.name}
                        abbreviation={shownSource.abbreviation}
                        color={shownSource.color}
                      />
                      <span className="min-w-0 [overflow-wrap:anywhere]">
                        {shownSource.context
                          ? `${shownSource.name} · ${shownSource.context}`
                          : shownSource.name}
                      </span>
                    </p>
                  )}
                  <h2
                    id={titleId}
                    dir="auto"
                    className={
                      "m-0 font-condensed text-[1.875rem] font-bold leading-none text-balance [overflow-wrap:anywhere]" +
                      (inCapitals(headline) ? " uppercase" : "")
                    }
                  >
                    <HeadlineText text={headline} />
                  </h2>
                  {date !== null && (
                    <p className="bs-label m-0">
                      <time dateTime={date.key}>{dateLine(date)}</time>
                    </p>
                  )}
                  {summary !== null ? (
                    <p
                      dir="auto"
                      className="m-0 whitespace-pre-line text-[1.0625rem] leading-relaxed text-ink-2 [overflow-wrap:anywhere]"
                    >
                      {summary}
                    </p>
                  ) : (
                    <p className="m-0 text-muted">
                      This story came without a summary. The full story is at
                      its source.
                    </p>
                  )}
                </div>
              </div>

              <div className="flex-none border-t-2 border-ink p-4">
                <a
                  href={shown.url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Read at source, in a new tab"
                  className="bs-btn bs-btn--solid w-full gap-2"
                >
                  Read at source
                  <SourceArrow />
                </a>
              </div>
            </>
          )}
        </aside>
      </div>
    </Portal>
  );
}
