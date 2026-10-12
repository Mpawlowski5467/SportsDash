import { useEffect, useRef, type FocusEvent } from "react";

import { ScrollRegion, Segmented, TeamBlock } from "../../components/bs";
import Select, { type SelectOption } from "../../components/Select";
import { usePrefersReducedMotion } from "../../lib/usePrefersReducedMotion";
import {
  countSpoken,
  countText,
  filterKind,
  type FilterEntry,
  type ScopeCount,
} from "./model";

const GROUP_LABEL = "Show stories for";

/**
 * Brings a segment wholly inside its strip; one wider than the strip (a
 * long name on a small phone) is set from its start, where its block and
 * the beginning of its name are. Moved by hand along the strip only:
 * `scrollIntoView` would also move the page.
 */
function reveal(strip: HTMLElement, segment: HTMLElement, smooth: boolean) {
  const frame = strip.getBoundingClientRect();
  const box = segment.getBoundingClientRect();
  if (frame.width === 0) return; // the view is hidden
  const behavior = smooth ? "smooth" : "auto";
  const past = box.right > frame.right;
  if (box.left < frame.left || (past && box.width > frame.width)) {
    strip.scrollBy({ left: box.left - frame.left, behavior });
  } else if (past) {
    strip.scrollBy({ left: box.right - frame.right, behavior });
  }
}

/**
 * Which stories the page shows: everything, or one follow. For a handful
 * of follows it is the mockup's row of segments, each carrying the team's
 * block, its name and how many stories it lists. Below a wide window
 * (1024px) the row is one strip that scrolls inside itself, as the
 * mockup's does, and keeps the chosen segment and the one with keyboard
 * focus in view; from there up there is room, and a row that still does
 * not fit wraps onto a second. Past that many follows it is the shared
 * dropdown, with the counts in the options: a block of twenty segments is
 * not a control.
 *
 * A count is the number of stories that filter lists when chosen, "50+"
 * when it holds more than the page lists, and absent until the filter has
 * answered.
 */
export default function ScopeFilter({
  entries,
  counts,
  value,
  onChange,
}: {
  entries: readonly FilterEntry[];
  counts: ReadonlyMap<string, ScopeCount | null>;
  value: string;
  onChange: (key: string) => void;
}) {
  const reduced = usePrefersReducedMotion();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const kind = filterKind(entries);

  // Keep the chosen segment in view inside the strip.
  useEffect(() => {
    if (kind !== "segments") return;
    const strip = wrapRef.current?.firstElementChild;
    const chosen = strip?.querySelector('[aria-pressed="true"]');
    if (!(strip instanceof HTMLElement) || !(chosen instanceof HTMLElement)) return;
    reveal(strip, chosen, !reduced);
  }, [kind, value, reduced]);

  // And the segment the Tab key lands on: a browser scrolls a focused
  // control into view only when none of it shows, so one cut off by the
  // strip's edge would keep its name and its ring out of sight. Keyboard
  // focus only: a strip that moved under a press would take the segment
  // out from under the pointer before the click landed.
  const revealFocused = (event: FocusEvent<HTMLDivElement>) => {
    const strip = event.currentTarget.firstElementChild;
    const segment = event.target.closest("button");
    if (!(strip instanceof HTMLElement) || segment === null) return;
    if (!strip.contains(segment) || !segment.matches(":focus-visible")) return;
    reveal(strip, segment, !reduced);
  };

  if (kind === "select") {
    const options: SelectOption[] = entries.map((entry) => {
      const text = countText(counts.get(entry.key) ?? null);
      const name = entry.kind === "all" ? "All stories" : entry.name;
      const label = text === null ? name : `${name} · ${text}`;
      return entry.kind === "all"
        ? { id: entry.key, label }
        : {
            id: entry.key,
            label,
            abbreviation: entry.abbreviation,
            color: entry.color,
          };
    });
    return (
      <Select
        options={options}
        value={value}
        onChange={onChange}
        ariaLabel={GROUP_LABEL}
      />
    );
  }

  return (
    <div ref={wrapRef} onFocus={revealFocused} className="min-w-0 max-w-full">
      <ScrollRegion
        label="Story filters"
        // The strip's own ring goes round it, not inside: drawn inside in
        // the text colour it would vanish across the chosen segment, whose
        // fill is that colour.
        className="max-w-full focus-visible:outline-offset-2"
      >
        <Segmented
          label={GROUP_LABEL}
          value={value}
          onChange={onChange}
          // One row that scrolls until the window is wide. Wrapping any
          // sooner stacks six follows into five rows of stretched buttons
          // beside Refresh.
          className="max-lg:w-max max-lg:flex-nowrap [&>*]:gap-2.5 [&>*]:pl-3 [&>*]:pr-4 [&>*:first-child]:pl-[1.125rem] [&>*:first-child]:pr-3"
          options={entries.map((entry) => {
            const count = counts.get(entry.key) ?? null;
            const text = countText(count);
            const chosen = entry.key === value;
            return {
              value: entry.key,
              ariaLabel: countSpoken(entry.name, count),
              label: (
                <>
                  {entry.kind !== "all" && (
                    <TeamBlock
                      name={entry.name}
                      abbreviation={entry.abbreviation}
                      color={entry.color}
                    />
                  )}
                  <span className="max-w-[16rem] truncate">{entry.name}</span>
                  {text !== null &&
                    (chosen ? (
                      // The mockup's count chip: ink on the spot, in the
                      // chosen segment only.
                      <span className="inline-flex h-6 min-w-6 flex-none items-center justify-center bg-spot px-1.5 tracking-normal text-on-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]">
                        {text}
                      </span>
                    ) : (
                      // The label tone is too light on the hover fill, so a
                      // hovered segment's count steps up to the secondary.
                      <span
                        className={
                          "flex-none tracking-normal [button:hover_&]:text-ink-2 " +
                          (count !== null && count.count === 0
                            ? "text-muted"
                            : "text-ink-2")
                        }
                      >
                        {text}
                      </span>
                    ))}
                </>
              ),
            };
          })}
        />
      </ScrollRegion>
    </div>
  );
}
