import { createContext, useContext, type ReactNode } from "react";

import {
  FollowMark,
  ScoreFigure,
  SpotChip,
  TeamBlock,
} from "../../components/bs";
import type { Game, GameSide } from "../../types";
import {
  dayOfMonth,
  monthShort,
  weekdayOf,
  type CardStatus,
  type SideValue,
} from "./model";

/**
 * Team colours for sides that carry none. A game reached through a followed
 * team's own schedule has no colour on either side, the followed one
 * included; the same team in a whole-competition game has one. So a side is
 * drawn in its own colour, else the colour its team was followed with, else
 * a colour seen under the same name on any other loaded game, else the
 * neutral block. Provided once by CalendarView.
 */
export const SideColorsContext = createContext<{
  byTeam: Record<string, string>;
  byName: Record<string, string>;
}>({ byTeam: {}, byName: {} });

export function useSideColor(side: GameSide): string | null {
  const colors = useContext(SideColorsContext);
  if (side.color) return side.color;
  if (side.team_id !== null && colors.byTeam[side.team_id]) {
    return colors.byTeam[side.team_id];
  }
  return colors.byName[side.name] ?? null;
}

/** A side's colour block, at the row size unless told otherwise. */
export function SideBlock({
  side,
  className,
}: {
  side: GameSide;
  className?: string;
}) {
  const color = useSideColor(side);
  return (
    <TeamBlock
      name={side.name}
      abbreviation={side.abbreviation}
      color={color}
      className={className}
    />
  );
}

/**
 * A date as the mockup sets it: the weekday over the month, small, and the
 * day of the month large beside them. Today carries the spot chip where the
 * month would be. Decoration: whatever holds it names the day in words; it
 * is a `<time>` so the date is there for a machine all the same.
 */
export function DateCell({
  dayKey,
  today,
  spread = false,
  className,
}: {
  dayKey: string;
  today: boolean;
  /** The numeral pushed to the far edge (a column header); otherwise beside the words. */
  spread?: boolean;
  className?: string;
}) {
  return (
    <time
      dateTime={dayKey}
      aria-hidden="true"
      className={
        "flex items-start font-condensed font-bold uppercase " +
        (spread ? "justify-between gap-2" : "gap-3") +
        (className ? ` ${className}` : "")
      }
    >
      <span
        className={
          "flex flex-col items-start pt-px " +
          (today ? "gap-1" : "gap-[0.3125rem]")
        }
      >
        <span className="text-base leading-none tracking-[0.08em]">
          {weekdayOf(dayKey)}
        </span>
        {today ? (
          <SpotChip size="sm">Today</SpotChip>
        ) : (
          <span className="text-[0.8125rem] font-semibold leading-none tracking-[0.08em] text-muted">
            {monthShort(dayKey)}
          </span>
        )}
      </span>
      <span className="text-[2.5rem] leading-[0.9] tabular-nums">
        {dayOfMonth(dayKey)}
      </span>
    </time>
  );
}

/**
 * Where a game stands, as the first line of its card or row: the start
 * time, "Final", the spot chip and the period, or that the game is off.
 * `size` is the start time's: 26px in a card, 30px in a table, smaller in
 * a compact card.
 */
export function StatusText({
  status,
  timeClass,
}: {
  status: CardStatus;
  timeClass: string;
}): ReactNode {
  switch (status.kind) {
    case "time":
      return (
        <time
          dateTime={status.iso}
          className={`whitespace-nowrap font-condensed font-bold leading-none text-ink tabular-nums ${timeClass}`}
        >
          {status.text}
        </time>
      );
    case "live":
      return (
        <span className="flex min-w-0 items-center gap-2">
          <SpotChip>Live</SpotChip>
          {status.text !== "" && (
            <span
              title={status.title}
              className="min-w-0 truncate font-condensed text-base font-bold uppercase leading-none tracking-[0.04em] text-ink tabular-nums"
            >
              {status.text}
            </span>
          )}
        </span>
      );
    case "final":
      return (
        <span className="font-condensed text-base font-semibold uppercase leading-none tracking-[0.08em] text-ink-2">
          Final
        </span>
      );
    default:
      return (
        <span className="min-w-0 truncate font-condensed text-base font-semibold uppercase leading-none tracking-[0.08em] text-muted">
          {status.text}
        </span>
      );
  }
}

/** The figure or the word at the end of a side's row. */
export function SideFigure({
  value,
  gameId,
  sizeClass,
}: {
  value: SideValue;
  /** Whose figure it is, so that only a change of ITS score pulses. */
  gameId: string;
  /** The numeral's size: 34px in a card, smaller where the row is. */
  sizeClass: string;
}): ReactNode {
  if (value === null) return null;
  if (value.kind === "tag") {
    return (
      <span className="ml-auto flex-none font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-muted">
        {value.text}
      </span>
    );
  }
  return (
    <span
      className={
        `ml-auto flex-none font-condensed leading-none tabular-nums ${sizeClass} ` +
        (value.lead ? "font-bold text-ink" : "font-medium text-muted")
      }
    >
      {/* Pulses when the score moves, and only then. */}
      <ScoreFigure of={gameId} value={value.value} />
    </span>
  );
}

/** The followed marker for a side, when it is one. */
export function SideMark({ game, side }: { game: Game; side: GameSide }) {
  return side.team_id !== null &&
    game.followed_team_ids.includes(side.team_id) ? (
    <FollowMark />
  ) : null;
}

/** A chevron: the icon on the previous and next buttons, and on a clipped bar. */
export function Chevron({
  direction,
  size = 20,
}: {
  direction: "left" | "right";
  size?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none", width: `${size / 16}rem`, height: `${size / 16}rem` }}
    >
      <path d={direction === "left" ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"} />
    </svg>
  );
}
