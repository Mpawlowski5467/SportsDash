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

/**
 * Accent-text gate for the dark-based stadium theme: the focused team's
 * color drives --sd-accent, which lands on amber accent TEXT over the
 * near-black base — a dark club color (navy, maroon, black) would all but
 * vanish there. Colors already light enough pass through untouched; dark
 * ones are lifted toward white in steps — keeping the team's hue family —
 * until they clear the same luminance bar isLightColor uses.
 */
export function accentOnDark(hex: string): string {
  if (isLightColor(hex)) return hex;
  for (const amount of [0.35, 0.5, 0.65, 0.8]) {
    const lifted = mixColor(hex, "#ffffff", amount);
    if (isLightColor(lifted)) return lifted;
  }
  return mixColor(hex, "#ffffff", 0.85);
}
