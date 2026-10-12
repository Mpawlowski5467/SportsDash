import { useEffect, useState } from "react";

/**
 * The current time, refreshed every `intervalMs`. For text that is a
 * function of the clock ("First pitch in 12 minutes"): a query that polls
 * does not re-render its consumers when the payload comes back unchanged,
 * so the countdown needs its own beat.
 */
export function useNow(intervalMs: number): Date {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(interval);
  }, [intervalMs]);

  return now;
}
