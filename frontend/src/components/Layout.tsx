import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Icon } from "./bs";
import KioskClock from "./KioskClock";
import { MastheadWordmark, SportsDashBall } from "./logo";
import ScoreTicker from "./ScoreTicker";
import { useIdle } from "../lib/useIdle";
import { useTicker } from "../lib/useTicker";

/** localStorage key for the persisted kiosk on/off toggle. */
const KIOSK_KEY = "sportsdash.kiosk";
/** Auto-advance the active tab this often while kiosk mode is running. */
const ROTATE_MS = 12_000;
/** Pause auto-rotation this long after any user interaction, then resume. */
const PAUSE_MS = 15_000;
/** Drive the visible "next in {n}s" countdown (and rotation check) on this beat. */
const TICK_MS = 1_000;
/** Show the idle clock overlay after this long with no interaction. */
const IDLE_MS = 90_000;

export type TabId =
  | "today"
  | "calendar"
  | "matchup"
  | "league"
  | "results"
  | "news"
  | "map";

export const TABS: { id: TabId; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "calendar", label: "Calendar" },
  { id: "matchup", label: "Matchup" },
  { id: "league", label: "League" },
  { id: "results", label: "Results" },
  { id: "news", label: "News" },
  { id: "map", label: "Map" },
];

export interface Props {
  active: TabId;
  onChange: (tab: TabId) => void;
  onManageTeams?: () => void;
  onOpenSettings?: () => void;
  children: ReactNode;
}

/**
 * Persisted kiosk on/off toggle, backed by localStorage. Reads the stored
 * value once on mount (SSR/private-mode safe) and mirrors changes back.
 */
function useKiosk(): [boolean, () => void] {
  const [kiosk, setKiosk] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(KIOSK_KEY) === "1";
    } catch {
      return false;
    }
  });

  // Kiosk scales the whole sheet from the root font size (`html[data-kiosk]`
  // in index.css). The index.html bootstrap sets the same attribute before
  // first paint, so a wall display does not start small and jump.
  useEffect(() => {
    document.documentElement.toggleAttribute("data-kiosk", kiosk);
  }, [kiosk]);

  const toggle = useCallback(() => {
    setKiosk((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(KIOSK_KEY, next ? "1" : "0");
      } catch {
        // Private mode / storage disabled — keep the in-memory state.
      }
      return next;
    });
  }, []);

  return [kiosk, toggle];
}

/** Local wall-clock time, refreshed every 30 seconds. */
function useClock(): string {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(interval);
  }, []);

  return now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const MENU_ITEM =
  "bs-focus-inset flex min-h-11 w-full cursor-pointer items-center px-4 text-left font-condensed text-[1.0625rem] font-bold uppercase tracking-[0.06em] text-ink hover:bg-hover";

/**
 * Settings button with a tiny dropdown: "Manage teams" (the onboarding wizard in
 * manage mode) and "Settings" (appearance + ticker). Closes on
 * outside click, Escape, or after a selection.
 */
function SettingsMenu({
  onManageTeams,
  onOpenSettings,
}: {
  onManageTeams?: () => void;
  onOpenSettings?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const choose = (action?: () => void) => {
    setOpen(false);
    // The menu item is about to unmount. Park focus on the trigger first, so
    // the overlay this opens records a launcher that still exists and can
    // hand focus back to it when it closes.
    triggerRef.current?.focus();
    action?.();
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label="Settings"
        title="Settings"
        aria-haspopup="menu"
        aria-expanded={open}
        className="bs-btn bs-btn--icon"
      >
        <Icon size={20}>
          <path d="M4 6h8M18 6h2M4 12h2M12 12h8M4 18h10M20 18h0" />
          <circle cx="15" cy="6" r="2.5" />
          <circle cx="9" cy="12" r="2.5" />
          <circle cx="17" cy="18" r="2.5" />
        </Icon>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Settings"
          className="absolute right-0 top-full z-50 mt-1 w-48 border-2 border-ink bg-sheet"
        >
          {onManageTeams && (
            <button
              type="button"
              role="menuitem"
              onClick={() => choose(onManageTeams)}
              className={MENU_ITEM}
            >
              Manage teams
            </button>
          )}
          {onOpenSettings && (
            <button
              type="button"
              role="menuitem"
              onClick={() => choose(onOpenSettings)}
              className={MENU_ITEM + " border-t border-hair"}
            >
              Settings
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Monitor-icon toggle that turns kiosk auto-rotation on and off. */
function KioskToggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label="Kiosk mode"
      aria-pressed={on}
      title={on ? "Kiosk mode on — click to stop" : "Kiosk mode"}
      className="bs-btn bs-btn--icon"
    >
      <Icon size={20}>
        <rect x="2" y="3" width="20" height="14" rx="1" />
        <path d="M8 21h8M12 17v4" />
      </Icon>
    </button>
  );
}

export default function Layout({
  active,
  onChange,
  onManageTeams,
  onOpenSettings,
  children,
}: Props) {
  const clock = useClock();
  const [kiosk, toggleKiosk] = useKiosk();
  const [ticker] = useTicker();

  // Idle watcher only runs in kiosk mode (parked otherwise).
  const idle = useIdle(IDLE_MS, kiosk);

  // Seconds remaining until the next tab rotation, surfaced in the header
  // pill so the user can see kiosk mode is live and ticking. null when
  // kiosk is off (no pill). Paused interactions hold it at full.
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  // Auto-rotation: while kiosk is on, advance to the next tab every
  // ROTATE_MS — but pause for PAUSE_MS after any user interaction, then
  // resume. A single 1s tick drives the visible countdown AND the rotation,
  // so the pill provably ticks down before each advance. We track the last
  // interaction timestamp and re-check it each tick rather than tearing the
  // interval down on every event.
  const lastInteractionRef = useRef<number>(0);
  // Keep the latest active tab in a ref so the rotation tick can read it
  // without being re-created (which would reset its phase) each change.
  const activeRef = useRef<TabId>(active);
  activeRef.current = active;

  useEffect(() => {
    if (!kiosk) {
      setSecondsLeft(null);
      return;
    }

    const markInteraction = () => {
      lastInteractionRef.current = Date.now();
    };
    const events = ["mousedown", "keydown", "wheel", "touchstart"] as const;
    for (const event of events) {
      window.addEventListener(event, markInteraction, { passive: true });
    }

    // Anchor the first rotation a full cycle out, then count down to it.
    let dueAt = Date.now() + ROTATE_MS;
    setSecondsLeft(Math.ceil(ROTATE_MS / 1000));

    const interval = window.setInterval(() => {
      const now = Date.now();
      // Within the post-interaction pause: hold the countdown at full and
      // keep pushing the rotation out so it fires a full cycle after the
      // user stops touching things.
      if (now - lastInteractionRef.current < PAUSE_MS) {
        dueAt = now + ROTATE_MS;
        setSecondsLeft(Math.ceil(ROTATE_MS / 1000));
        return;
      }

      if (now >= dueAt) {
        const index = TABS.findIndex((tab) => tab.id === activeRef.current);
        const next = TABS[(index + 1) % TABS.length];
        onChange(next.id);
        dueAt = now + ROTATE_MS;
        setSecondsLeft(Math.ceil(ROTATE_MS / 1000));
        return;
      }

      setSecondsLeft(Math.max(0, Math.ceil((dueAt - now) / 1000)));
    }, TICK_MS);

    return () => {
      window.clearInterval(interval);
      for (const event of events) {
        window.removeEventListener(event, markInteraction);
      }
    };
  }, [kiosk, onChange]);

  return (
    <div className="min-h-screen bg-ground text-ink">
      {/* Masthead: a white sheet over a 3px ink rule. One 68px row from
          1280px; below that the nav drops to its own scrolling row. Keep
          --bs-masthead-h in index.css in step with the row heights here. */}
      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-sheet">
        <div className="mx-auto flex max-w-[90rem] flex-wrap items-center gap-x-3 px-4 sm:gap-x-7 sm:px-8">
          <button
            type="button"
            onClick={() => onChange("today")}
            aria-label="SportsDash home"
            className="flex min-h-14 shrink-0 cursor-pointer select-none items-center gap-2 text-ink sm:min-h-[4.25rem] sm:gap-2.5"
          >
            <SportsDashBall
              variant="ink"
              className="h-7 w-7 sm:h-[2.125rem] sm:w-[2.125rem]"
            />
            <MastheadWordmark className="text-[1.375rem] sm:text-3xl" />
          </button>

          <nav
            aria-label="Sections"
            className="order-last flex w-full min-w-0 items-stretch overflow-x-auto font-condensed text-[1.1875rem] tracking-[0.06em] xl:order-none xl:w-auto xl:overflow-visible"
          >
            {TABS.map((tab) => {
              const isActive = tab.id === active;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onChange(tab.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={
                    // `uppercase` is on the button, not the nav: browsers
                    // reset text-transform on buttons, so it does not inherit.
                    "bs-focus-inset grid min-h-11 shrink-0 cursor-pointer place-items-center whitespace-nowrap px-[1.125rem] uppercase xl:min-h-[4.25rem] " +
                    (isActive
                      ? "bg-tab font-bold text-on-tab"
                      : "font-semibold text-ink hover:bg-hover")
                  }
                >
                  <span className="col-start-1 row-start-1">{tab.label}</span>
                  {/* Holds the bold width, so the tabs to the right do not
                      shift when this one becomes current. */}
                  <span
                    aria-hidden="true"
                    className="invisible col-start-1 row-start-1 font-bold"
                  >
                    {tab.label}
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="ml-auto flex min-h-14 items-center gap-2 sm:min-h-[4.25rem] sm:gap-3">
            <time className="whitespace-nowrap font-condensed text-[1.375rem] font-bold leading-none sm:text-3xl">
              {clock}
            </time>

            {kiosk && (
              <span
                title="Kiosk mode — auto-rotating tabs"
                className="bs-spot select-none"
              >
                Kiosk
                {secondsLeft !== null && (
                  <span className="hidden tabular-nums sm:inline">
                    {" "}
                    · next in {secondsLeft}s
                  </span>
                )}
              </span>
            )}

            <KioskToggle on={kiosk} onToggle={toggleKiosk} />

            {(onManageTeams || onOpenSettings) && (
              <SettingsMenu
                onManageTeams={onManageTeams}
                onOpenSettings={onOpenSettings}
              />
            )}
          </div>
        </div>
      </header>

      {/* The score line, under the masthead, on every tab. Hidden while the
          kiosk idle clock is up (idle is only ever true in kiosk mode). */}
      {ticker && !idle && <ScoreTicker />}

      {/* Focusable by script only: a jump from another view lands here
          (see `navigate` in App), so the next Tab starts in the page. */}
      <main
        id="sd-main"
        tabIndex={-1}
        className="mx-auto max-w-[90rem] px-4 pb-10 pt-7 outline-none sm:px-8"
      >
        {children}
      </main>

      {kiosk && idle && <KioskClock />}
    </div>
  );
}
