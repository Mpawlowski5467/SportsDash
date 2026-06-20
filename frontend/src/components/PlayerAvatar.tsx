import { useEffect, useState } from "react";

/** Up-to-two-letter initials from a player's name (e.g. "Cole Palmer" → "CP"). */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const SIZE_PX: Record<NonNullable<Props["size"]>, number> = {
  sm: 28,
  md: 36,
  lg: 56,
};

interface Props {
  /** The player's headshot/cutout URL, or null when none is stored. */
  photoUrl?: string | null;
  /** Full player name — drives the alt text and the initials fallback. */
  name: string;
  /** Avatar box size. Defaults to "md". */
  size?: "sm" | "md" | "lg";
  className?: string;
}

/**
 * Round player headshot used in rosters and the team page. Renders the
 * player's `photo_url` as a cover-cropped circular image and falls back to a
 * neutral initials chip whenever there is no URL OR the image fails to load
 * (onError) — mirroring how {@link TeamLogo} degrades to its abbreviation
 * chip. The box is a fixed circle so it never shifts surrounding layout.
 */
export default function PlayerAvatar({
  photoUrl,
  name,
  size = "md",
  className,
}: Props) {
  const [failed, setFailed] = useState(false);
  const px = SIZE_PX[size];

  // Reset the error latch when the URL changes so a previously-broken image
  // doesn't suppress a new, valid one if the node is reused.
  useEffect(() => {
    setFailed(false);
  }, [photoUrl]);

  const box = { width: px, height: px } as const;
  const showImage = !!photoUrl && !failed;

  if (showImage) {
    return (
      <img
        src={photoUrl as string}
        alt=""
        aria-hidden="true"
        loading="lazy"
        width={px}
        height={px}
        onError={() => setFailed(true)}
        style={box}
        className={
          "shrink-0 rounded-full bg-zinc-800 object-cover object-top sd-avatar" +
          (className ? ` ${className}` : "")
        }
      />
    );
  }

  const fontSize = px <= 28 ? 11 : px <= 36 ? 13 : 18;
  return (
    <span
      aria-hidden="true"
      style={{ ...box, fontSize }}
      className={
        "inline-flex shrink-0 items-center justify-center rounded-full bg-zinc-700 font-semibold tracking-wide text-zinc-200" +
        (className ? ` ${className}` : "")
      }
    >
      {initials(name)}
    </span>
  );
}
