/** Hex → rgba() with the given alpha; a neutral zinc when hex is missing/bad. */
export function withAlpha(hex: string | null | undefined, alpha: number): string {
  const fallbackRgb = "161, 161, 170";
  if (!hex) return `rgba(${fallbackRgb}, ${alpha})`;
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return `rgba(${fallbackRgb}, ${alpha})`;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Simple perceived-luminance check for readable text over a color. */
export function isLightColor(hex: string): boolean {
  const raw = hex.replace("#", "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return false;
  return 0.299 * r + 0.587 * g + 0.114 * b > 150;
}

/** Parse #rgb/#rrggbb to [r, g, b] channels, or null when malformed. */
function parseHex(hex: string): [number, number, number] | null {
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

/**
 * Channel-wise sRGB mix of two hex colors; `amount` is the share of
 * `target` (0 keeps `hex`, 1 yields `target`). Malformed input falls back
 * to a neutral zinc, matching withAlpha's posture.
 */
export function mixColor(hex: string, target: string, amount: number): string {
  const from = parseHex(hex);
  const to = parseHex(target);
  if (from === null || to === null) return "#a1a1aa";
  const t = Math.min(1, Math.max(0, amount));
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t);
  return (
    "#" +
    [mix(from[0], to[0]), mix(from[1], to[1]), mix(from[2], to[2])]
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}

/** Box Score ink and paper: the only two colors text is ever set in on a fill. */
export const INK = "#101418";
export const PAPER = "#ffffff";

/** `#rrggbb`, lower-case, from #rgb / rrggbb / #RRGGBB; null when malformed. */
export function normalizeHex(hex: string | null | undefined): string | null {
  if (!hex) return null;
  const rgb = parseHex(hex);
  if (rgb === null) return null;
  return "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
}

/** WCAG 2 relative luminance (0 black .. 1 white); null when malformed. */
export function luminance(hex: string): number | null {
  const rgb = parseHex(hex);
  if (rgb === null) return null;
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * WCAG 2 contrast ratio between two colors, 1 (none) .. 21 (black on white).
 * Malformed input scores 1 so a bad color can never pass a contrast gate.
 */
export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  if (la === null || lb === null) return 1;
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * A fill and the text color that reads on it. Text is ink or paper, whichever
 * contrasts more: never assumed from the hue, so a navy block and a yellow
 * one both read.
 *
 * A narrow band of mid-luminance colors (some strong reds and blues) clears
 * `minRatio` with neither: ink and paper both land near 4.3:1. Those fills
 * are nudged a few percent toward the far end from the text until the text
 * passes, which is not a visible change of color.
 */
export function fitFill(
  hex: string,
  minRatio = 4.5,
): { fill: string; on: string } {
  const fill = normalizeHex(hex);
  if (fill === null) return { fill: INK, on: PAPER };
  const withInk = contrastRatio(INK, fill);
  const withPaper = contrastRatio(PAPER, fill);
  const on = withInk >= withPaper ? INK : PAPER;
  if (Math.max(withInk, withPaper) >= minRatio) return { fill, on };
  const away = on === INK ? PAPER : "#000000";
  for (let step = 1; step <= 50; step += 1) {
    const nudged = mixColor(fill, away, step * 0.02);
    if (contrastRatio(on, nudged) >= minRatio) return { fill: nudged, on };
  }
  return { fill: away, on };
}

/**
 * A color made readable as TEXT (or a thin rule) on every one of `surfaces`:
 * it is moved toward ink on light surfaces, toward paper on dark ones, in
 * small steps that keep its hue family, until each surface clears
 * `minRatio`. Colors that already pass are returned untouched. (It replaces
 * the Custom theme's `accentOnDark`, which lifted toward white past a fixed
 * luma bar and only knew dark bases.)
 */
export function readableOn(
  hex: string,
  surfaces: readonly string[],
  minRatio = 4.5,
): string {
  const color = normalizeHex(hex);
  const first = surfaces[0] === undefined ? null : luminance(surfaces[0]);
  if (first === null) return color ?? INK;
  const target = first > 0.4 ? INK : PAPER;
  if (color === null) return target;
  const passes = (candidate: string) =>
    surfaces.every((surface) => contrastRatio(candidate, surface) >= minRatio);
  if (passes(color)) return color;
  for (let step = 1; step <= 50; step += 1) {
    const moved = mixColor(color, target, step * 0.02);
    if (passes(moved)) return moved;
  }
  return target;
}

/** [hue 0..360, saturation 0..1, lightness 0..1] of a parsed color. */
function toHsl([r, g, b]: [number, number, number]): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const lightness = (max + min) / 2;
  const delta = max - min;
  if (delta === 0) return [0, 0, lightness];
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  let hue: number;
  if (max === rn) hue = ((gn - bn) / delta) % 6;
  else if (max === gn) hue = (bn - rn) / delta + 2;
  else hue = (rn - gn) / delta + 4;
  return [(hue * 60 + 360) % 360, saturation, lightness];
}

function fromHsl(hue: number, saturation: number, lightness: number): string {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const x = chroma * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = lightness - chroma / 2;
  const sector = Math.floor(hue / 60) % 6;
  const [r, g, b] = [
    [chroma, x, 0],
    [x, chroma, 0],
    [0, chroma, x],
    [0, x, chroma],
    [x, 0, chroma],
    [chroma, 0, x],
  ][sector];
  return (
    "#" +
    [r, g, b]
      .map((v) =>
        Math.round((v + m) * 255)
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

/**
 * A dark color made visible as a FILL on dark `surfaces`: its lightness is
 * raised, hue and saturation kept, one percent at a time until every
 * surface clears `minRatio`. A navy becomes a brighter blue of the same hue
 * rather than the grey-blue that mixing toward white gives, so the fill
 * still reads as the team's color. Colors that already pass are untouched.
 */
export function liftOn(
  hex: string,
  surfaces: readonly string[],
  minRatio = 3,
): string {
  const color = normalizeHex(hex);
  const rgb = color === null ? null : parseHex(color);
  if (color === null || rgb === null) return PAPER;
  const passes = (candidate: string) =>
    surfaces.every((surface) => contrastRatio(candidate, surface) >= minRatio);
  if (passes(color)) return color;
  const [hue, saturation, lightness] = toHsl(rgb);
  for (let step = 1; lightness + step * 0.01 < 1; step += 1) {
    const lifted = fromHsl(hue, saturation, lightness + step * 0.01);
    if (passes(lifted)) return lifted;
  }
  return PAPER;
}
