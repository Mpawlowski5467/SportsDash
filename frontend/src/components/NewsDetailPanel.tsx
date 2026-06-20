import { useEffect, useState } from "react";

import type { NewsItem } from "../types";
import { formatDateTime } from "../lib/time";
import Portal from "./Portal";

/** The article's source chip: a followed team or a competition. */
export interface NewsSource {
  name: string;
  color: string | null;
}

/**
 * Slide-in reader panel for a news item. Clicking a headline no longer jumps
 * straight to the publisher; it opens this drawer with the article image,
 * its summary, and a prominent "Read full article" link out. Mirrors the map
 * panel: always mounted so it animates both ways, ESC/X to close, and it keeps
 * the last item rendered while sliding out.
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

  const [last, setLast] = useState<NewsItem | null>(item);
  useEffect(() => {
    if (item !== null) setLast(item);
  }, [item]);
  const shown = item ?? last;

  // Lock background scroll + ESC to close while open.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => {
    setImageFailed(false);
  }, [shown?.id]);

  return (
    <Portal>
      {/* Dim the dashboard behind the reader; click to dismiss. */}
      <div
        aria-hidden={!open}
        onClick={onClose}
        className={
          "fixed inset-0 z-30 bg-black/50 transition-opacity duration-300 " +
          (open ? "opacity-100" : "pointer-events-none opacity-0")
        }
      />
      <aside
        aria-hidden={!open}
        className={
          // top-12 clears the sticky 48px app header (it's portaled to body).
          "fixed bottom-0 right-0 top-12 z-40 flex w-[26rem] max-w-[92vw] flex-col " +
          "border-l border-zinc-800 bg-zinc-900 shadow-2xl transition-transform " +
          "duration-300 ease-out motion-reduce:transition-none " +
          (open ? "translate-x-0" : "pointer-events-none translate-x-full")
        }
      >
        {shown && (
          <>
            <header className="flex items-start gap-3 border-b border-zinc-800 px-4 py-3">
              <div className="flex min-w-0 flex-1 items-center gap-2 text-xs text-zinc-500">
                {source && (
                  <span className="flex items-center gap-1.5 font-medium text-zinc-300">
                    <span
                      aria-hidden
                      className="inline-block size-2 rounded-full"
                      style={{ backgroundColor: source.color ?? "#52525b" }}
                    />
                    {source.name}
                  </span>
                )}
                <span className="truncate">{shown.source}</span>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="-mr-1 -mt-1 shrink-0 rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
              >
                <svg
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  className="h-4 w-4"
                  aria-hidden="true"
                >
                  <path d="M5 5l10 10M15 5L5 15" />
                </svg>
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {shown.image_url && !imageFailed && (
                <img
                  src={shown.image_url}
                  alt=""
                  className="mb-4 h-44 w-full rounded-lg bg-zinc-800 object-cover"
                  onError={() => setImageFailed(true)}
                />
              )}

              <h2 className="text-lg font-semibold leading-snug text-zinc-100">
                {shown.title}
              </h2>
              {shown.published_at && (
                <p className="mt-1 text-xs text-zinc-500">
                  {formatDateTime(shown.published_at)}
                </p>
              )}

              {shown.summary ? (
                <p className="mt-4 whitespace-pre-line text-sm leading-relaxed text-zinc-300">
                  {shown.summary}
                </p>
              ) : (
                <p className="mt-4 text-sm text-zinc-500">
                  No summary available for this article — open it in full below.
                </p>
              )}
            </div>

            <div className="border-t border-zinc-800 px-4 py-3">
              <a
                href={shown.url}
                target="_blank"
                rel="noreferrer"
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-amber-400"
              >
                Read full article
                <svg
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-4 w-4"
                  aria-hidden="true"
                >
                  <path d="M7 13L13 7M8 7h5v5" />
                </svg>
              </a>
            </div>
          </>
        )}
      </aside>
    </Portal>
  );
}
