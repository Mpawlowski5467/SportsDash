import type { GamePlay } from "../types";

/**
 * Condensed key-moment timeline. Plays arrive chronologically and render as a
 * vertical rail: scoring plays get an emphasized (emerald) dot and bolder
 * text; routine moments a muted zinc dot. Each row shows the period + clock
 * (muted), the play text, and — when present — the running "away-home" score
 * as a right-aligned tabular chip. The list scrolls within the modal so a long
 * game's worth of moments doesn't blow out the panel.
 */
export default function PlayByPlayTimeline({ plays }: { plays: GamePlay[] }) {
  if (plays.length === 0) {
    return null;
  }

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Key moments
      </h3>
      <ul className="max-h-72 space-y-0 overflow-y-auto pr-1">
        {plays.map((play, index) => {
          const hasScore =
            play.home_score !== null && play.away_score !== null;
          return (
            <li
              // Stable-ish key: live polling appends moments, so combining the
              // play's coordinates with its index avoids DOM reuse glitches if
              // the feed ever reorders.
              key={`${play.period_label}-${play.clock ?? ""}-${index}`}
              className="relative flex gap-3 pb-3 pl-1 last:pb-0"
            >
              {/* Left rail: connecting line + the moment's dot. */}
              <div className="relative flex w-2 shrink-0 justify-center">
                {index !== plays.length - 1 && (
                  <span
                    className="absolute top-1.5 bottom-0 w-px bg-zinc-800"
                    aria-hidden="true"
                  />
                )}
                <span
                  className={`relative mt-1 h-2 w-2 shrink-0 rounded-full ${
                    play.scoring ? "bg-emerald-400" : "bg-zinc-600"
                  }`}
                  aria-hidden="true"
                />
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 text-[11px] uppercase tracking-wide text-zinc-500 tabular-nums">
                    {play.period_label}
                    {play.clock !== null && (
                      <span className="text-zinc-600"> · {play.clock}</span>
                    )}
                  </span>
                  {hasScore && (
                    <span className="ml-auto shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[11px] font-semibold text-zinc-300 tabular-nums">
                      {play.away_score}-{play.home_score}
                    </span>
                  )}
                </div>
                <p
                  className={`mt-0.5 text-sm ${
                    play.scoring
                      ? "font-semibold text-zinc-100"
                      : "text-zinc-300"
                  }`}
                >
                  {play.text}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
