import { createContext, useContext, type ReactNode } from "react";

import { FollowMark, TeamBlock, teamFill, type TeamBlockSize } from "../../components/bs";
import { boxScoreLabel } from "../../lib/labels";
import type { Game, GameSide } from "../../types";
import { isFollowedSide } from "./model";

export { boxScoreLabel };

/**
 * Followed teams' colours by team id, from `/teams`. A game reached through
 * a followed team's own schedule carries no colour on either side, the
 * followed one included, so the block falls back to the colour the team was
 * followed with. Provided once by TodayView.
 */
export const TeamColorsContext = createContext<Record<string, string>>({});

/** The colour to draw a side in: its own, else its followed team's, else none. */
export function useSideColor(side: GameSide): string | null {
  const followed = useContext(TeamColorsContext);
  if (side.color) return side.color;
  return side.team_id === null ? null : (followed[side.team_id] ?? null);
}

/**
 * A team as it appears in a list row: its colour block, its name, and the
 * followed marker. `tone` is how much the name should carry: "lead" for the
 * followed side or the winner, "plain" for a side with nothing to say about
 * it, "quiet" for the other side of a row that has a lead.
 */
export function SideName({
  game,
  side,
  tone,
  prefix,
  blockSize = 34,
  className,
}: {
  game: Game;
  side: GameSide;
  tone: "lead" | "plain" | "quiet";
  /** "at", before the home side of a game still to be played. */
  prefix?: string;
  blockSize?: TeamBlockSize;
  className?: string;
}) {
  const color = useSideColor(side);
  const weight =
    tone === "lead"
      ? "font-bold text-ink"
      : tone === "quiet"
        ? "text-ink-2"
        : "font-medium text-ink";
  return (
    <span
      className={
        `flex min-w-0 items-center gap-2.5 ${weight}` +
        (className ? ` ${className}` : "")
      }
    >
      <TeamBlock
        name={side.name}
        abbreviation={side.abbreviation}
        color={color}
        size={blockSize}
      />
      <span className="min-w-0 truncate">
        {prefix !== undefined && <>{prefix} </>}
        {side.name}
      </span>
      {/* Beside the name, not inside it (as `MarkedName` would have it):
          the name is one line cut with an ellipsis, and a marker inside
          the cut text would go with it. */}
      {isFollowedSide(game, side) && <FollowMark />}
    </span>
  );
}

/**
 * Two shares as one bar, away first: a win probability. Drawn in the two
 * teams' colours when both are known; otherwise the larger share is ink and
 * the smaller the rule grey, so the bar still says who is ahead.
 */
export function SplitBar({
  game,
  away,
  home,
  className,
}: {
  game: Game;
  away: number;
  home: number;
  className?: string;
}) {
  const awayFill = teamFill(useSideColor(game.away));
  const homeFill = teamFill(useSideColor(game.home));
  const coloured = awayFill !== null && homeFill !== null;
  const total = away + home || 1;
  const segment = (
    share: number,
    fill: typeof awayFill,
    larger: boolean,
  ): ReactNode => (
    <span
      className={
        "block h-full " +
        (coloured
          ? fill?.edge
            ? "shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--bs-ink)_28%,transparent)]"
            : ""
          : larger
            ? "bg-ink"
            : "bg-rule")
      }
      style={{
        width: `${(share / total) * 100}%`,
        backgroundColor: coloured ? fill?.fill : undefined,
      }}
    />
  );
  return (
    <span
      role="img"
      aria-label={`${game.away.name} ${Math.round(away)} percent, ${game.home.name} ${Math.round(home)} percent`}
      className={"flex gap-0.5" + (className ? ` ${className}` : "")}
    >
      {segment(away, awayFill, away > home)}
      {segment(home, homeFill, home >= away)}
    </span>
  );
}
