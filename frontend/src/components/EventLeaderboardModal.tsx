import { useId } from "react";

import { formatShortDate } from "../lib/time";
import type { LeaderRow, SportEvent } from "../types";
import Portal from "./Portal";
import { useModalChrome } from "./modalChrome";

/** Date span label: single day, or "Jun 12 – Jun 15" across a tournament. */
function spanLabel(event: SportEvent): string {
  const start = formatShortDate(event.start_time);
  if (event.end_time === null) return start;
  const end = formatShortDate(event.end_time);
  return start === end ? start : `${start} – ${end}`;
}

/** Phase-appropriate status pill for a leaderboard event. */
function EventStatus({ event }: { event: SportEvent }) {
  if (event.phase === "in_progress") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">
        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-red-500" />
        </span>
        <span className="uppercase tracking-wide">
          {event.round_label || "LIVE"}
        </span>
      </span>
    );
  }
  if (event.phase === "final") {
    return (
      <span className="inline-flex items-center rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-300">
        FINAL
      </span>
    );
  }
  if (event.phase === "scheduled") {
    return (
      <span className="inline-flex items-center rounded-full border border-zinc-700 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
        Upcoming
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-zinc-800/50 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-zinc-600">
      {event.phase === "postponed" ? "PPD" : "CANC"}
    </span>
  );
}

/** Score colored by sign: red under par, zinc at/over par. */
function ScoreText({ score }: { score: string }) {
  const trimmed = score.trim();
  const under = trimmed.startsWith("-");
  const over = trimmed.startsWith("+");
  return (
    <span
      className={
        "tabular-nums font-semibold " +
        (under ? "text-emerald-400" : over ? "text-zinc-300" : "text-zinc-200")
      }
    >
      {trimmed || "—"}
    </span>
  );
}

/** Full leaderboard table for an event. */
function LeaderboardTable({
  event,
  followedColor,
}: {
  event: SportEvent;
  // player_id -> team color of a followed golfer on this board.
  followedColor: Record<string, string>;
}) {
  if (event.leaderboard.length === 0) {
    return (
      <p className="px-3 py-4 text-sm text-zinc-500">
        Leaderboard not available yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
            <th className="w-12 px-3 py-1.5 font-semibold">Pos</th>
            <th className="px-3 py-1.5 font-semibold">Player</th>
            <th className="w-20 px-3 py-1.5 text-right font-semibold">Score</th>
            <th className="w-24 px-3 py-1.5 text-right font-semibold">Detail</th>
          </tr>
        </thead>
        <tbody>
          {event.leaderboard.map((row: LeaderRow, idx) => {
            const color =
              row.player_id !== null ? followedColor[row.player_id] : undefined;
            const followed = color !== undefined;
            return (
              <tr
                key={`${row.player_id ?? row.name}-${idx}`}
                className="border-b border-zinc-800/60 last:border-0"
                style={
                  followed
                    ? {
                        backgroundColor: color + "26", // ~15% alpha
                        boxShadow: `inset 3px 0 0 0 ${color}`,
                      }
                    : undefined
                }
              >
                <td className="px-3 py-1.5 tabular-nums text-zinc-400">
                  {row.position_label || row.position}
                </td>
                <td className="px-3 py-1.5">
                  <span className="flex items-center gap-2">
                    {followed && (
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ backgroundColor: color }}
                        aria-hidden="true"
                      />
                    )}
                    <span
                      className={followed ? "font-semibold text-zinc-100" : "text-zinc-200"}
                    >
                      {row.name}
                    </span>
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <ScoreText score={row.score} />
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums text-zinc-400">
                  {row.detail ?? ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * On-demand leaderboard drill-down for a single tournament event. Mirrors the
 * GameDetailModal pattern: a full-screen dark overlay with the event header
 * (name, date span, venue, status) and the full Pos / Player / Score / Detail
 * leaderboard as its body. The caller (Today's event grid) owns the open state
 * and mounts this with the already-loaded `event`, so no extra fetch is needed.
 * Followed golfers' rows are highlighted with their team color. Closes on ESC
 * or a backdrop click.
 */
export default function EventLeaderboardModal({
  event,
  followedColor,
  onClose,
}: {
  event: SportEvent;
  // player_id -> team color of a followed golfer on this board.
  followedColor: Record<string, string>;
  onClose: () => void;
}) {
  const titleId = useId();

  const dialogRef = useModalChrome(onClose);

  return (
    <Portal>
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b border-zinc-800 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2
              id={titleId}
              className="truncate text-base font-semibold text-zinc-100"
            >
              {event.name}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-500">
              <span>{spanLabel(event)}</span>
              {event.venue !== null && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{event.venue}</span>
                </>
              )}
            </div>
          </div>
          <EventStatus event={event} />
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

        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          <LeaderboardTable event={event} followedColor={followedColor} />
        </div>
      </div>
    </div>
    </Portal>
  );
}
