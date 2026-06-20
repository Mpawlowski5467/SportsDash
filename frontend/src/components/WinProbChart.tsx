import { useId } from "react";

import { usePrefersReducedMotion } from "../lib/usePrefersReducedMotion";

/**
 * Win-probability sparkline: home win % (0–100) over the course of the game,
 * index → x, value → y. Drawn into a fixed `viewBox` so it scales fluidly to
 * the panel width. A dashed 50% reference line marks the coin-flip; the curve
 * itself (and its subtle area fill) leans sky when the latest reading favors
 * the home side and amber when it favors the away side — matching the
 * away=amber / home=sky convention used by the odds split bar.
 *
 * The caller gates on `series.length > 1`, but we stay defensive: a single
 * point (or none) renders just the reference line and the current label.
 */
export default function WinProbChart({
  series,
  homeLabel,
  awayLabel,
}: {
  series: number[];
  homeLabel: string;
  awayLabel: string;
}) {
  const reduced = usePrefersReducedMotion();
  const gradientId = useId();

  // Internal coordinate space; the SVG scales to its container via viewBox.
  const W = 300;
  const H = 80;

  const points = series.map((value, index) => {
    const clamped = Math.max(0, Math.min(100, value));
    const x =
      series.length > 1 ? (index / (series.length - 1)) * W : W / 2;
    // y is inverted: 100% (home) sits at the top, 0% at the bottom.
    const y = H - (clamped / 100) * H;
    return { x, y };
  });

  const last = series.length > 0 ? series[series.length - 1] : 50;
  const homeFavored = last >= 50;
  // Theme-safe accents only (sky / amber are mapped in every theme).
  const stroke = homeFavored ? "text-sky-400" : "text-amber-400";

  const linePath = points.map((p) => `${p.x},${p.y}`).join(" ");
  // Close the area down to the baseline for the soft fill underneath the line.
  const areaPath =
    points.length > 0
      ? `M ${points[0].x},${H} ` +
        points.map((p) => `L ${p.x},${p.y}`).join(" ") +
        ` L ${points[points.length - 1].x},${H} Z`
      : "";

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Win probability
        </h3>
        <span
          className={`text-xs font-semibold tabular-nums ${stroke}`}
        >
          {homeLabel} {Math.round(last)}%
        </span>
      </div>
      <div className="rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-3">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-20 w-full overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Home win probability, currently ${Math.round(last)} percent`}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor="currentColor"
                stopOpacity="0.25"
                className={stroke}
              />
              <stop
                offset="100%"
                stopColor="currentColor"
                stopOpacity="0"
                className={stroke}
              />
            </linearGradient>
          </defs>

          {/* 50% coin-flip reference line. */}
          <line
            x1="0"
            y1={H / 2}
            x2={W}
            y2={H / 2}
            className="stroke-zinc-700"
            strokeWidth="1"
            strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke"
          />

          {areaPath !== "" && (
            <path d={areaPath} fill={`url(#${gradientId})`} stroke="none" />
          )}

          {points.length > 1 && (
            <polyline
              points={linePath}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              // pathLength normalizes the dash units to 1 regardless of the
              // rendered size, so the draw reveal works even with a stretched
              // (preserveAspectRatio="none") non-scaling stroke.
              pathLength={1}
              className={`${stroke} ${reduced ? "" : "winprob-draw"}`}
            />
          )}

          {/* Marker on the latest reading. */}
          {points.length > 0 && (
            <circle
              cx={points[points.length - 1].x}
              cy={points[points.length - 1].y}
              r="2.5"
              className={`${stroke} fill-current`}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        <div className="mt-1.5 flex items-center justify-between text-[11px] tabular-nums text-zinc-500">
          <span>{awayLabel}</span>
          <span>{homeLabel}</span>
        </div>
      </div>

      {/* Subtle one-shot draw of the curve; static (drawn) is the base state. */}
      <style>{`
        @media (prefers-reduced-motion: no-preference) {
          .winprob-draw {
            stroke-dasharray: 1;
            stroke-dashoffset: 1;
            animation: winprob-draw 0.7s ease-out forwards;
          }
          @keyframes winprob-draw {
            to { stroke-dashoffset: 0; }
          }
        }
      `}</style>
    </div>
  );
}
