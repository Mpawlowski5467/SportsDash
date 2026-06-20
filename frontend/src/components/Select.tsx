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
  const current = options.find((o) => o.id === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!current) return null;

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
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
          className="absolute left-0 z-30 mt-1 max-h-80 w-64 overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 py-1 shadow-2xl"
        >
          {options.map((option) => {
            const active = option.id === current.id;
            return (
              <button
                key={option.id}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => {
                  onChange(option.id);
                  setOpen(false);
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
