import { useEffect, useRef, useState } from "react";

import TeamLogo from "./TeamLogo";

export interface SelectOption {
  id: string;
  label: string;
  logoUrl?: string | null;
  color?: string | null;
}

/**
 * Compact dropdown selector — shows the current choice (icon + label) and
 * reveals the rest on click. Replaces wide wrapping pill rows when there are
 * many options (e.g. a dozen followed leagues). Closes on outside click / Esc.
 *
 * Keyboard: ArrowUp/ArrowDown open the list and move through options
 * (roving focus), Home/End jump, type-ahead jumps to the next option
 * starting with the typed letter, Enter/Space select, Escape closes and
 * returns focus to the trigger.
 */
export default function Select({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: SelectOption[];
  value: string | undefined;
  onChange: (id: string) => void;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = options.find((o) => o.id === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // On open, move focus to the selected option so arrows work immediately.
  useEffect(() => {
    if (!open) return;
    const selected = options.findIndex((o) => o.id === current?.id);
    optionRefs.current[selected === -1 ? 0 : selected]?.focus();
  }, [open, options, current?.id]);

  if (!current) return null;

  const focusedIndex = () =>
    optionRefs.current.findIndex((el) => el === document.activeElement);

  const focusOption = (index: number) => {
    const count = options.length;
    if (count === 0) return;
    const clamped = ((index % count) + count) % count;
    optionRefs.current[clamped]?.focus();
  };

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  const onTriggerKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
    }
  };

  const onListKeyDown = (event: React.KeyboardEvent) => {
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        return;
      case "ArrowDown":
        event.preventDefault();
        focusOption(focusedIndex() + 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        focusOption(focusedIndex() - 1);
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
      default: {
        // Type-ahead: jump to the next option starting with the letter.
        if (event.key.length === 1 && /\S/.test(event.key)) {
          const letter = event.key.toLowerCase();
          const from = focusedIndex();
          for (let step = 1; step <= options.length; step += 1) {
            const i = (from + step) % options.length;
            if (options[i].label.toLowerCase().startsWith(letter)) {
              focusOption(i);
              return;
            }
          }
        }
      }
    }
  };

  return (
    <div ref={ref} className="relative inline-block">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className="flex items-center gap-2 rounded-full border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-100 transition-colors hover:border-zinc-600"
      >
        <OptionIcon option={current} />
        <span className="max-w-[14rem] truncate">{current.label}</span>
        <svg
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={
            "h-3.5 w-3.5 text-zinc-400 transition-transform " +
            (open ? "rotate-180" : "")
          }
          aria-hidden="true"
        >
          <path d="M5 7.5l5 5 5-5" />
        </svg>
      </button>
      {open && (
        <div
          role="listbox"
          onKeyDown={onListKeyDown}
          className="absolute left-0 z-30 mt-1 max-h-80 w-64 overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 py-1 shadow-2xl"
        >
          {options.map((option, index) => {
            const active = option.id === current.id;
            return (
              <button
                key={option.id}
                ref={(el) => {
                  optionRefs.current[index] = el;
                }}
                type="button"
                role="option"
                aria-selected={active}
                tabIndex={-1}
                onClick={() => {
                  onChange(option.id);
                  close(true);
                }}
                className={
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors " +
                  (active
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-300 hover:bg-zinc-800/60")
                }
              >
                <OptionIcon option={option} />
                <span className="truncate">{option.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function OptionIcon({ option }: { option: SelectOption }) {
  if (option.logoUrl) {
    return (
      <TeamLogo
        logoUrl={option.logoUrl}
        name={option.label}
        color={option.color}
        size="sm"
      />
    );
  }
  if (option.color) {
    return (
      <span
        aria-hidden
        className="inline-block size-2.5 shrink-0 rounded-full"
        style={{ backgroundColor: option.color }}
      />
    );
  }
  return null;
}
