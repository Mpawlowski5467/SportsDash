import { useEffect, useState } from "react";

/**
 * Perceived-luminance check so the abbreviation-chip fallback picks readable
 * text on top of an arbitrary team color. Mirrors the helper GameCard uses.
 */
function isLightColor(hex: string): boolean {
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

/** Up-to-three-char abbreviation chip label. */
function abbrev(name: string, abbreviation?: string | null): string {
  if (abbreviation) return abbreviation;
  return name.slice(0, 3).toUpperCase();
}

const SIZE_PX: Record<NonNullable<Props["size"]>, number> = {
  xs: 16,
  sm: 20,
  md: 28,
  lg: 40,
};

interface Props {
  /** The team's logo image URL, or null/undefined when none is stored. */
  logoUrl?: string | null;
  /** Full team name — drives the alt text and the fallback chip label. */
  name: string;
  /** Short code shown on the fallback chip (defaults to first 3 of name). */
  abbreviation?: string | null;
  /** Brand color for the fallback chip; neutral zinc when absent. */
  color?: string | null;
  /** Logo box size. Defaults to "md". */
  size?: "xs" | "sm" | "md" | "lg";
  className?: string;
}

/**
 * Shared team badge used throughout the app. Renders the team's `logo_url`
 * as a contained image and gracefully falls back to the existing
 * abbreviation chip whenever there is no URL OR the image fails to load
 * (onError). The chip uses the team's brand color when supplied (readable
 * text picked by luminance), otherwise a neutral zinc chip.
 *
 * The box is a fixed square so logos never shift surrounding layout; the
 * image is `object-contain` so non-square crests aren't distorted.
 */
export default function TeamLogo({
  logoUrl,
  name,
  abbreviation,
  color,
  size = "md",
  className,
}: Props) {
  const [failed, setFailed] = useState(false);
  const px = SIZE_PX[size];

  // Reset the error latch if the URL changes (e.g. team switch re-uses the
  // same TeamLogo node), so a previously-broken URL doesn't suppress a new,
  // valid one.
  useEffect(() => {
    setFailed(false);
  }, [logoUrl]);

  const box = {
    width: px,
    height: px,
  } as const;

  const showImage = !!logoUrl && !failed;

  if (showImage) {
    return (
      <img
        src={logoUrl as string}
        alt=""
        aria-hidden="true"
        loading="lazy"
        width={px}
        height={px}
        onError={() => setFailed(true)}
        style={box}
        className={
          "shrink-0 rounded object-contain sd-logo" +
          (className ? ` ${className}` : "")
        }
      />
    );
  }

  const fontSize = px <= 16 ? 8 : px <= 20 ? 9 : px <= 28 ? 10 : 12;
  return (
    <span
      aria-hidden="true"
      style={
        color
          ? {
              ...box,
              fontSize,
              backgroundColor: color,
              color: isLightColor(color) ? "#18181b" : "#fafafa",
            }
          : { ...box, fontSize }
      }
      className={
        "inline-flex shrink-0 items-center justify-center rounded font-bold tracking-wide" +
        (color ? "" : " bg-zinc-700 text-zinc-200") +
        (className ? ` ${className}` : "")
      }
    >
      {abbrev(name, abbreviation)}
    </span>
  );
}
