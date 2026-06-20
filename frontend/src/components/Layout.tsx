import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import KioskClock from "./KioskClock";
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
  onOpenNotifications?: () => void;
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

/**
 * Gear button with a tiny dropdown: "Manage teams" (the onboarding wizard in
 * manage mode) and "Notifications" (the notification-prefs panel). Closes on
 * outside click, Escape, or after a selection.
 */
function SettingsMenu({
  onManageTeams,
  onOpenNotifications,
}: {
  onManageTeams?: () => void;
  onOpenNotifications?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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
    action?.();
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label="Settings"
        title="Settings"
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4 w-4"
          aria-hidden="true"
        >
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Settings"
          className="absolute right-0 top-full z-50 mt-1 w-44 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 py-1 shadow-xl shadow-black/40"
        >
          {onManageTeams && (
            <button
              type="button"
              role="menuitem"
              onClick={() => choose(onManageTeams)}
              className="block w-full px-3 py-2 text-left text-sm text-zinc-200 transition-colors hover:bg-zinc-800"
            >
              Manage teams
            </button>
          )}
          {onOpenNotifications && (
            <button
              type="button"
              role="menuitem"
              onClick={() => choose(onOpenNotifications)}
              className="block w-full px-3 py-2 text-left text-sm text-zinc-200 transition-colors hover:bg-zinc-800"
            >
              Notifications
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
      className={
        on
          ? "rounded-md p-1 text-amber-400 transition-colors hover:bg-zinc-800"
          : "rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
      }
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-4 w-4"
        aria-hidden="true"
      >
        <rect x="2" y="3" width="20" height="14" rx="2" />
        <path d="M8 21h8" />
        <path d="M12 17v4" />
      </svg>
    </button>
  );
}

export default function Layout({
  active,
  onChange,
  onManageTeams,
  onOpenNotifications,
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
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="sticky top-0 z-50 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-4 px-4">
          <span className="select-none whitespace-nowrap text-base font-bold tracking-tight">
            Sports<span className="text-amber-400">Dash</span>
          </span>

          <nav
            aria-label="Sections"
            className="flex min-w-0 items-center gap-1 overflow-x-auto"
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
                    isActive
                      ? "whitespace-nowrap rounded-full bg-zinc-800 px-3 py-1 text-sm font-medium text-zinc-100 transition-colors"
                      : "whitespace-nowrap rounded-full px-3 py-1 text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                  }
                >
                  {tab.label}
                </button>
              );
            })}
          </nav>

          <time className="ml-auto whitespace-nowrap text-sm tabular-nums text-zinc-400">
            {clock}
          </time>

          {kiosk && (
            <span
              aria-live="polite"
              title="Kiosk mode — auto-rotating tabs"
              className="flex select-none items-center gap-1.5 whitespace-nowrap rounded-full border border-amber-400/40 bg-amber-400/10 px-2.5 py-0.5 text-xs font-medium text-amber-300"
            >
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
              </span>
              Kiosk
              {secondsLeft !== null && (
                <span className="tabular-nums text-amber-400/70">
                  next in {secondsLeft}s
                </span>
              )}
            </span>
          )}

          <KioskToggle on={kiosk} onToggle={toggleKiosk} />

          {(onManageTeams || onOpenNotifications) && (
            <SettingsMenu
              onManageTeams={onManageTeams}
              onOpenNotifications={onOpenNotifications}
            />
          )}
        </div>
      </header>

      {/* Live scoreboard, under the nav, on every tab. Hidden while the kiosk
          idle clock is up (idle is only ever true in kiosk mode). */}
      {ticker && !idle && <ScoreTicker />}

      <main className="mx-auto max-w-[1600px] px-4 py-4">{children}</main>

      {kiosk && idle && <KioskClock />}
    </div>
  );
}
