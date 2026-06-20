import { useEffect, useState } from "react";

import { useToday } from "../hooks";

/** Local wall-clock, ticking every second for the big idle display. */
function useTick(): Date {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(interval);
  }, []);
  return now;
}

/**
 * Full-screen idle clock overlay for kiosk mode.
 *
 * Shown by Layout after ~90s of no interaction (kiosk only). Renders a
 * large local time + date plus a compact "N live / N upcoming today" line
 * from `useToday`. It is a passive screensaver: any interaction flips the
 * parent's idle state back to active and unmounts this overlay, so there
 * is no dismiss button here — the whole surface just absorbs nothing and
 * lets the document-level activity listeners do the work.
 */
export default function KioskClock() {
  const now = useTick();
  const todayQuery = useToday();

  const time = now.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
  const date = now.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  const games = todayQuery.data?.games ?? [];
  const liveCount = games.filter((g) => g.phase === "in_progress").length;
  const upcomingCount = games.filter((g) => g.phase === "scheduled").length;

  return (
    <div
      className="fixed inset-0 z-[100] flex flex-col items-center justify-center bg-zinc-950 text-zinc-100 select-none"
      aria-hidden="true"
    >
      <div className="text-[clamp(4rem,18vw,16rem)] font-bold leading-none tracking-tight tabular-nums">
        {time}
      </div>
      <div className="mt-4 text-[clamp(1rem,3vw,2rem)] font-medium text-zinc-400">
        {date}
      </div>
      <div className="mt-8 flex items-center gap-3 text-[clamp(0.875rem,2vw,1.25rem)]">
        <span
          className={
            liveCount > 0 ? "font-semibold text-red-400" : "text-zinc-500"
          }
        >
          {liveCount} live
        </span>
        <span className="text-zinc-700" aria-hidden="true">
          ·
        </span>
        <span className="text-zinc-400">{upcomingCount} upcoming today</span>
      </div>
      <div className="absolute bottom-8 text-xs uppercase tracking-widest text-zinc-700">
        Sports<span className="text-amber-400/70">Dash</span> kiosk
      </div>
    </div>
  );
}
