import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { ChevronDown, TeamBlock } from "./bs";

export interface SelectOption {
  id: string;
  label: string;
  /**
   * A team's code. An option that carries one is a team, and its mark is
   * the team's block in `color`; null means the team has no code, and the
   * block derives one from the label.
   */
  abbreviation?: string | null;
  /** A team's colour, for its block; on its own, a small colour mark. */
  color?: string | null;
  /**
   * Where the crest used to be. Crests are no longer drawn: an option that
   * has one is a team, and gets its block like one with a code.
   */
  logoUrl?: string | null;
}

/** Where the open list goes, measured from the trigger as it opens. */
interface Placement {
  /** The list hangs from the trigger's right edge and grows leftward. */
  fromRight: boolean;
  /**
   * How far it is, in px, from the edge the list hangs from to the far
   * edge of the window: the room the list has. Null before the first open.
   */
  reach: number | null;
}

/**
 * The Box Score dropdown: a square button showing the current choice (its
 * mark and label) over a chevron, which opens a list of the rest. For a
 * choice among too many options for a row of buttons. 44px by default;
 * `size="lg"` is the 52px page-level picker (a league, a season).
 *
 * The trigger is named by `ariaLabel` and described by the current choice,
 * so both are announced. Closes on an outside click or Escape.
 *
 * Keyboard: ArrowUp/ArrowDown on the trigger open the list; in the list
 * they move through the options (wrapping), Home/End jump, a letter jumps
 * to the next option starting with it, Enter/Space choose, Escape closes
 * and returns focus to the trigger, Tab closes.
 */
export default function Select({
  options,
  value,
  onChange,
  ariaLabel,
  size = "md",
  placeholder,
}: {
  options: readonly SelectOption[];
  value: string | undefined;
  onChange: (id: string) => void;
  ariaLabel?: string;
  size?: "md" | "lg";
  /**
   * What the trigger says while `value` is none of the options ("11 more
   * games"), for a list that holds only part of a choice. Without it the
   * first option stands as the current one, as it always has.
   */
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement>({
    fromRight: false,
    reach: null,
  });
  const ref = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const valueId = useId();
  const current = options.find((option) => option.id === value) ?? options[0];
  const currentId = current?.id;
  // Nothing in the list is chosen, and the caller gave words for that.
  const unchosen =
    placeholder !== undefined && !options.some((option) => option.id === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // On open, move focus to the chosen option so the arrows work at once.
  useEffect(() => {
    if (!open) return;
    const chosen = options.findIndex((option) => option.id === currentId);
    optionRefs.current[chosen === -1 ? 0 : chosen]?.focus();
  }, [open, options, currentId]);

  if (current === undefined) return null;

  // The list hangs from whichever edge of the trigger keeps it on screen:
  // the edge on the trigger's own side of the window, which is the one with
  // more room beyond it. The room is kept too, as the most the list may
  // take, so an open list never reaches past the window and widens the page.
  const show = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect !== undefined) {
      const viewport = document.documentElement.clientWidth;
      const fromRight = rect.left + rect.width / 2 > viewport / 2;
      setPlacement({
        fromRight,
        reach: fromRight ? rect.right : viewport - rect.left,
      });
    }
    setOpen(true);
  };

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  const focused = () =>
    optionRefs.current.findIndex((element) => element === document.activeElement);

  const focusOption = (index: number) => {
    const count = options.length;
    if (count === 0) return;
    optionRefs.current[((index % count) + count) % count]?.focus();
  };

  const onTriggerKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show();
    }
  };

  const onListKeyDown = (event: KeyboardEvent) => {
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        return;
      case "ArrowDown":
        event.preventDefault();
        focusOption(focused() + 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        focusOption(focused() - 1);
        return;
      case "Home":
        event.preventDefault();
        focusOption(0);
        return;
      case "End":
        event.preventDefault();
        focusOption(options.length - 1);
        return;
      case "Tab":
        // Tabbing away closes rather than leaving a stranded open list.
        close(false);
        return;
      default:
        // Type-ahead: jump to the next option starting with the letter.
        if (event.key.length === 1 && /\S/.test(event.key)) {
          const letter = event.key.toLowerCase();
          const from = focused();
          for (let step = 1; step <= options.length; step += 1) {
            const index = (from + step) % options.length;
            if (options[index].label.toLowerCase().startsWith(letter)) {
              focusOption(index);
              return;
            }
          }
        }
    }
  };

  const large = size === "lg";
  return (
    // Two boxes. The outer one is what a layout sizes, and a column stretches
    // it to its own width; the inner one hugs the trigger, so the list is
    // measured from the trigger and not from however wide the column is.
    <div ref={ref} className="inline-block max-w-full">
      <div className="relative inline-block max-w-full align-top">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => (open ? setOpen(false) : show())}
          onKeyDown={onTriggerKeyDown}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label={ariaLabel}
          aria-describedby={ariaLabel === undefined ? undefined : valueId}
          className={
            "bs-btn max-w-full " +
            (large
              ? "bs-btn--lg"
              : // The mockup's 44px picker: tighter than a plain button, and
                // tighter still on the side a team block sits.
                !unchosen && isTeam(current)
                ? "gap-2.5 pl-2 pr-2.5"
                : "gap-2 pl-3.5 pr-2.5")
          }
        >
          {!unchosen && <OptionMark option={current} />}
          <span id={valueId} className="min-w-0 max-w-[16rem] truncate">
            {unchosen ? placeholder : current.label}
          </span>
          <ChevronDown size={large ? 20 : 16} />
        </button>
        {open && (
          <div
            role="listbox"
            aria-label={ariaLabel}
            onKeyDown={onListKeyDown}
            // As wide as its longest option, up to 22rem and never past the
            // window's own 1rem gutter.
            style={
              placement.reach === null
                ? undefined
                : { maxWidth: `min(22rem, calc(${placement.reach}px - 1rem))` }
            }
            className={
              "absolute z-30 mt-1 max-h-80 w-max min-w-full max-w-[min(22rem,calc(100vw-2rem))] overflow-y-auto border-2 border-ink bg-sheet " +
              (placement.fromRight ? "right-0" : "left-0")
            }
          >
            {options.map((option, index) => {
              const chosen = !unchosen && option.id === current.id;
              return (
                <button
                  key={option.id}
                  ref={(element) => {
                    optionRefs.current[index] = element;
                  }}
                  type="button"
                  role="option"
                  aria-selected={chosen}
                  tabIndex={-1}
                  onClick={() => {
                    onChange(option.id);
                    close(true);
                  }}
                  className={
                    "bs-focus-inset flex min-h-11 w-full cursor-pointer items-center gap-2.5 border-t border-hair text-left font-condensed text-[1.0625rem] font-bold uppercase tracking-[0.06em] first:border-t-0 " +
                    (isTeam(option) ? "pl-2 pr-4 " : "px-4 ") +
                    (chosen ? "bs-inverse" : "text-ink hover:bg-hover")
                  }
                >
                  <OptionMark option={option} />
                  <span className="min-w-0 truncate">{option.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** Whether an option stands for a team, and so is marked with its block. */
function isTeam(option: SelectOption): boolean {
  return option.abbreviation !== undefined || Boolean(option.logoUrl);
}

/**
 * What stands before an option's label: a team's block, or a square of the
 * colour for an option that only has one. Decoration either way; the label
 * beside it is what is read out.
 */
function OptionMark({ option }: { option: SelectOption }) {
  if (isTeam(option)) {
    return (
      <TeamBlock
        name={option.label}
        abbreviation={option.abbreviation}
        color={option.color}
      />
    );
  }
  if (option.color) {
    return (
      <span
        aria-hidden="true"
        className="size-3 flex-none shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--bs-ink)_28%,transparent)]"
        style={{ backgroundColor: option.color }}
      />
    );
  }
  return null;
}
