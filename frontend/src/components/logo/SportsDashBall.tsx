import { useId, type CSSProperties } from "react";

/**
 * The shared SportsDash ball mark — a circle split into six 60° pie wedges,
 * each carrying a different sport's pattern. Every loader (and the favicons)
 * is built on this one component.
 *
 *   variant="detailed"  the primary mark, with per-sport seam detail. Use at
 *                       ~28px and up.
 *   variant="flat"      solid wedges, no seams (invisible that small). Use
 *                       below ~28px, and as the favicon source.
 *
 * `onDark` dims the outer ring (#2B2D33) and centre hub (#15171C) so they read
 * against an ink background (Prompt 2 splash); the white separators stay white.
 *
 * The six wedge GROUPS carry stable classes `sd-w1`..`sd-w6` and the
 * separators/ring/hub group `sd-wsep`, so the splash loader can stagger their
 * assembly purely from CSS without this component needing to know about it.
 */

export type SportsDashBallVariant = "detailed" | "flat";

export interface SportsDashBallProps {
  /** Rendered pixel size (square). Defaults to 120. */
  size?: number;
  variant?: SportsDashBallVariant;
  /** Dim ring + hub for placement on a dark/ink surface. */
  onDark?: boolean;
  /** Accessible label. When omitted the mark is decorative (aria-hidden). */
  title?: string;
  className?: string;
  style?: CSSProperties;
}

/** The six wedge fill paths + their detailed/flat fills, clockwise from top. */
const WEDGES: ReadonlyArray<{
  key: string;
  d: string;
  detailed: string;
  flat: string;
}> = [
  { key: "basketball", d: "M60,60 L60,8 A52,52 0 0 1 105,34 Z", detailed: "#E8643C", flat: "#E8643C" }, // prettier-ignore
  { key: "soccer", d: "M60,60 L105,34 A52,52 0 0 1 105,86 Z", detailed: "#F4F1EC", flat: "#15171C" }, // prettier-ignore
  { key: "tennis", d: "M60,60 L105,86 A52,52 0 0 1 60,112 Z", detailed: "#C9D14A", flat: "#C9D14A" }, // prettier-ignore
  { key: "volleyball", d: "M60,60 L60,112 A52,52 0 0 1 15,86 Z", detailed: "#3B6FD4", flat: "#3B6FD4" }, // prettier-ignore
  { key: "baseball", d: "M60,60 L15,86 A52,52 0 0 1 15,34 Z", detailed: "#F4EFE6", flat: "#F1ECE2" }, // prettier-ignore
  { key: "football", d: "M60,60 L15,34 A52,52 0 0 1 60,8 Z", detailed: "#9A6A43", flat: "#9A6A43" }, // prettier-ignore
];

/** The six white radial separators (drawn over every variant). */
const SEPARATORS: readonly string[] = [
  "M60,60 L60,8",
  "M60,60 L105,34",
  "M60,60 L105,86",
  "M60,60 L60,112",
  "M60,60 L15,86",
  "M60,60 L15,34",
];

/** Per-wedge seam detail (detailed variant only), clipped to its wedge. */
type Seam = { d: string; stroke?: string; fill?: string; width?: number };
const SEAMS: ReadonlyArray<readonly Seam[]> = [
  // basketball — ink seam lines
  [{ d: "M60,8 V112 M8,60 H112 M60,8 Q18,60 60,112 M60,8 Q102,60 60,112", stroke: "#15171C", width: 2.3 }], // prettier-ignore
  // soccer — a single filled ink pentagon
  [{ d: "M82,49 L92.5,56.6 L88.5,68.9 L75.5,68.9 L71.5,56.6 Z", fill: "#15171C" }], // prettier-ignore
  // tennis — white curving seams
  [{ d: "M104,70 C84,72 80,86 64,104 M98,57 C86,66 88,82 78,99", stroke: "#FFFFFF", width: 2.6 }], // prettier-ignore
  // volleyball — white panel curves
  [{ d: "M16,84 Q40,74 60,60 M28,98 Q48,84 66,82 M10,68 Q34,72 52,64", stroke: "#FFFFFF", width: 2.2 }], // prettier-ignore
  // baseball — a red stitch curve
  [{ d: "M24,32 Q36,60 24,88", stroke: "#D8423B", width: 2.2 }], // prettier-ignore
  // football — white seam + laces
  [
    { d: "M37,16 L47,42", stroke: "#FFFFFF", width: 2.2 },
    { d: "M32,23 L42,20 M34,30 L44,27 M36,37 L46,34", stroke: "#FFFFFF", width: 1.5 },
  ],
];

function Seams({ seams }: { seams: readonly Seam[] }) {
  return (
    <>
      {seams.map((s, i) =>
        s.fill ? (
          <path key={i} d={s.d} fill={s.fill} />
        ) : (
          <path
            key={i}
            d={s.d}
            fill="none"
            stroke={s.stroke}
            strokeWidth={s.width}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ),
      )}
    </>
  );
}

export default function SportsDashBall({
  size = 120,
  variant = "detailed",
  onDark = false,
  title,
  className,
  style,
}: SportsDashBallProps) {
  // Unique, CSS-url-safe suffix so multiple instances on one page never share
  // clipPath ids (the wedge geometry is identical, but duplicate ids are still
  // invalid markup — scope them per instance).
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const clipId = (i: number) => `sd-wedge-${i}-${uid}`;

  const detailed = variant === "detailed";
  const ringStroke = onDark ? "#2B2D33" : "#FFFFFF";
  const hubFill = onDark ? "#15171C" : "#FFFFFF";

  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      className={className}
      style={{ display: "block", ...style }}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}

      {detailed ? (
        <defs>
          {WEDGES.map((w, i) => (
            <clipPath id={clipId(i)} key={w.key}>
              <path d={w.d} />
            </clipPath>
          ))}
        </defs>
      ) : null}

      {WEDGES.map((w, i) => (
        <g key={w.key} className={`sd-w${i + 1}`}>
          <path d={w.d} fill={detailed ? w.detailed : w.flat} />
          {detailed ? (
            <g clipPath={`url(#${clipId(i)})`}>
              <Seams seams={SEAMS[i]} />
            </g>
          ) : null}
        </g>
      ))}

      <g className="sd-wsep">
        {SEPARATORS.map((d, i) => (
          <path
            key={i}
            d={d}
            stroke="#FFFFFF"
            strokeWidth={detailed ? 2.6 : 3}
            strokeLinecap="round"
          />
        ))}
        {detailed ? (
          <>
            <circle cx="60" cy="60" r="52" fill="none" stroke={ringStroke} strokeWidth="2.6" />
            <circle cx="60" cy="60" r="4.5" fill={hubFill} />
          </>
        ) : null}
      </g>
    </svg>
  );
}
