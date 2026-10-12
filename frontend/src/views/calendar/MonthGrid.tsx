import { useRef, type CSSProperties, type KeyboardEvent } from "react";

import { FollowMark, SpotChip } from "../../components/bs";
import type { Game } from "../../types";
import { dayKeyOffset } from "../today/model";
import {
  MONTH_CELL_GAMES,
  WEEK_STARTS_ON,
  cellDots,
  dayCellLabel,
  dayOfMonth,
  eventsOn,
  gamesByDay,
  hasFollowedSide,
  holdsDay,
  layoutBars,
  monthWeeks,
  pairFigure,
  pickGames,
  rangeLabel,
  weekRange,
  type DayRange,
  type EventBar,
  type EventDays,
} from "./model";
import { SideBlock } from "./parts";

/**
 * A month as a grid of days, in the week's language at a smaller size: the
 * day's number, then a line per game (the two sides' blocks and the start
 * time or the score), a tournament as a bar running through its days, and
 * today under the spot bar.
 *
 * A day is one button. It does not open a game: it CHOOSES the day, and the
 * day's games are listed in full beside the grid (under it where there is
 * no room), where each row is the 44px button that opens the box score.
 * That is what lets a cell stay small enough for a phone and still stand
 * for a day with ten games: it lists the first few and says how many more
 * there are.
 *
 * Below 64rem of its own width a cell has no room for a pair of blocks and
 * a time, and shows the day's number over a square per game instead.
 *
 * Keyboard: the grid is one Tab stop (the chosen day). The arrow keys move
 * the choice by a day or a week, Home and End to the ends of the week; they
 * stop at the edges of the month, which the previous and next buttons turn.
 */

const WEEKDAYS: { short: string; long: string }[] = (() => {
  // Any week will do for the names: 2024-01-07 was a Sunday.
  const short = new Intl.DateTimeFormat("en-US", { weekday: "short" });
  const long = new Intl.DateTimeFormat("en-US", { weekday: "long" });
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(2024, 0, 7 + ((WEEK_STARTS_ON + index) % 7));
    return { short: short.format(day), long: long.format(day) };
  });
})();

/**
 * A tournament's bar across the days of one week. The bars of a week are
 * one layer laid over its cells (which keep a lane's height free for each),
 * so a bar runs unbroken through the hairlines between days and its name
 * has the whole run to be read in. They take no clicks: the day under a
 * bar is still the button, and names the tournament when chosen.
 *
 * Where a lane sits is fixed by the cell's own measure: the day number's
 * line (2.125rem down, 2.25rem in a wide cell), then a lane every 0.5rem
 * (1.375rem in a wide cell).
 */
function Bar({ bar, offset }: { bar: EventBar; offset: number }) {
  const start = offset + bar.column;
  const style = {
    "--lane": bar.lane,
    left: `${(start / 7) * 100}%`,
    right: `${((7 - start - bar.span) / 7) * 100}%`,
  } as CSSProperties;
  return (
    <span
      style={style}
      className={
        "absolute top-[calc(2.125rem+var(--lane)*0.5rem)] flex h-1.5 items-center border-y-2 border-ink bg-sheet @[64rem]/month:top-[calc(2.25rem+var(--lane)*1.375rem)] @[64rem]/month:h-5 " +
        // An end that is the tournament's own is closed and set in from
        // the day's edge; one cut by the week or the month runs off it.
        (bar.startsBefore ? "" : "ml-1 border-l-2 @[64rem]/month:ml-1.5 ") +
        (bar.endsAfter ? "" : "mr-1 border-r-2 @[64rem]/month:mr-1.5")
      }
    >
      <span className="hidden min-w-0 truncate px-1 font-condensed text-xs font-bold uppercase leading-none tracking-[0.04em] text-ink @[64rem]/month:block">
        {bar.entry.event.name}
      </span>
    </span>
  );
}

function PairLine({ game }: { game: Game }) {
  const figure = pairFigure(game);
  const off = game.phase === "postponed" || game.phase === "canceled";
  return (
    <span className="flex min-w-0 items-center gap-[0.1875rem]">
      <SideBlock side={game.away} className="h-5 w-[1.875rem] text-xs" />
      <SideBlock side={game.home} className="h-5 w-[1.875rem] text-xs" />
      {hasFollowedSide(game) && <FollowMark label={null} />}
      <span className="ml-auto flex min-w-0 justify-end">
        {figure.live ? (
          <SpotChip size="sm" className="min-w-0 truncate">
            {figure.text}
          </SpotChip>
        ) : (
          <span
            className={
              "min-w-0 truncate font-condensed text-[0.8125rem] font-bold uppercase leading-none tabular-nums " +
              (off ? "text-muted" : "text-ink")
            }
          >
            {figure.text}
          </span>
        )}
      </span>
    </span>
  );
}

export default function MonthGrid({
  month,
  todayKey,
  games,
  events,
  selected,
  onSelect,
}: {
  month: DayRange;
  todayKey: string;
  /** The month's games: cut to it, filtered, with the live copies laid over. */
  games: readonly Game[];
  /** The tournaments to draw; none while a team filter is on. */
  events: readonly EventDays[];
  /** The chosen day: the one listed beside the grid, and the grid's Tab stop. */
  selected: string;
  onSelect: (dayKey: string) => void;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const byDay = gamesByDay(games);
  const weeks = monthWeeks(month);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const steps: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    let target: string | null = null;
    if (event.key in steps) target = dayKeyOffset(selected, steps[event.key]);
    else if (event.key === "Home") {
      const first = weekRange(selected).first;
      target = first < month.first ? month.first : first;
    } else if (event.key === "End") {
      const last = weekRange(selected).last;
      target = last > month.last ? month.last : last;
    }
    if (target === null) return;
    event.preventDefault();
    if (!holdsDay(month, target)) return;
    onSelect(target);
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-day="${target}"]`)
      ?.focus();
  };

  return (
    <div className="bs-sheet @container/month">
      <div
        ref={gridRef}
        role="grid"
        aria-label={rangeLabel("month", month)}
        data-calendar-grid="month"
        onKeyDown={onKeyDown}
      >
        <div role="row" className="grid grid-cols-7 border-b-2 border-ink">
          {WEEKDAYS.map((day) => (
            <span
              key={day.long}
              role="columnheader"
              aria-label={day.long}
              className="px-1.5 py-2 font-condensed text-sm font-bold uppercase leading-none tracking-[0.08em] text-ink @[64rem]/month:px-2 @[64rem]/month:text-base"
            >
              {day.short}
            </span>
          ))}
        </div>
        {weeks.map((week) => {
          // A tournament's bar is cut to the days of this week that are in
          // the month: the days outside it are drawn empty.
          const inside = week.filter((key) => holdsDay(month, key));
          const offset = week.indexOf(inside[0]);
          const { lanes, bars } = layoutBars(events, {
            first: inside[0],
            last: inside[inside.length - 1],
          });
          return (
            <div
              key={week[0]}
              role="row"
              className="relative grid grid-cols-7 gap-px border-b border-hair bg-hair last:border-b-0"
            >
              {week.map((key) => {
                if (!holdsDay(month, key)) {
                  return (
                    <div
                      key={key}
                      role="gridcell"
                      aria-disabled="true"
                      className="min-h-[3.25rem] bg-tint px-1.5 pt-2 @[64rem]/month:min-h-[7.25rem] @[64rem]/month:px-2 @[64rem]/month:pt-2.5"
                    >
                      <span
                        aria-hidden="true"
                        className="font-condensed text-[1.375rem] font-semibold leading-none text-muted tabular-nums"
                      >
                        {dayOfMonth(key)}
                      </span>
                    </div>
                  );
                }
                const today = key === todayKey;
                const chosen = key === selected;
                const dayGames = byDay.get(key) ?? [];
                const dayEvents = eventsOn(events, key);
                const { shown, more } = pickGames(dayGames, MONTH_CELL_GAMES);
                const dots = cellDots(dayGames);
                const quiet = dayGames.length === 0 && dayEvents.length === 0;
                return (
                  <div key={key} role="gridcell" className="flex min-w-0">
                    <button
                      type="button"
                      data-day={key}
                      tabIndex={chosen ? 0 : -1}
                      aria-pressed={chosen}
                      aria-label={dayCellLabel(key, todayKey, dayGames, dayEvents)}
                      onClick={() => onSelect(key)}
                      className={
                        "bs-focus-inset relative flex min-h-[3.25rem] w-full min-w-0 cursor-pointer flex-col text-left @[64rem]/month:min-h-[7.25rem] " +
                        (chosen ? "bs-inverse" : "bg-sheet hover:bg-tint")
                      }
                    >
                      {/* Laid over the cell's top padding, so the number
                          under it stands where every other day's does and
                          a bar runs level through today. */}
                      {today && (
                        <span className="absolute inset-x-0 top-0 h-1.5 bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]" />
                      )}
                      <span className="flex h-[2.125rem] flex-none items-end justify-between gap-1 px-1.5 pb-1 @[64rem]/month:h-9 @[64rem]/month:px-2">
                        <span
                          className={
                            "font-condensed text-[1.375rem] font-bold leading-none tabular-nums " +
                            (quiet && !today ? "text-muted" : "text-ink")
                          }
                        >
                          {dayOfMonth(key)}
                        </span>
                        {today && (
                          <SpotChip
                            size="sm"
                            className="mb-0.5 hidden @[64rem]/month:inline-block"
                          >
                            Today
                          </SpotChip>
                        )}
                      </span>

                      {/* Room for the bars that cross this week. */}
                      {lanes > 0 && (
                        <span
                          aria-hidden="true"
                          style={{ "--lanes": lanes } as CSSProperties}
                          className="block h-[calc(var(--lanes)*0.5rem)] flex-none @[64rem]/month:h-[calc(var(--lanes)*1.375rem)]"
                        />
                      )}

                      {/* Where there is room: a line per game. */}
                      <span className="hidden flex-col gap-1 px-2 pb-2.5 pt-1 @[64rem]/month:flex">
                        {shown.map((game) => (
                          <PairLine key={game.id} game={game} />
                        ))}
                        {more > 0 && (
                          <span className="font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-muted">
                            +{more} more
                          </span>
                        )}
                      </span>
                      {/* Where there is not: a square per game, up to three
                          (two and a "+" on a busier day, which is what
                          keeps them on one line in a phone's cell: see
                          `cellDots`), a followed team's game as the
                          followed marker. */}
                      <span className="flex flex-wrap items-center gap-[0.1875rem] px-1.5 pb-1.5 pt-0.5 @[64rem]/month:hidden">
                        {dots.shown.map((game) =>
                          hasFollowedSide(game) ? (
                            <FollowMark key={game.id} label={null} />
                          ) : (
                            <span key={game.id} className="size-1.5 flex-none bg-ink" />
                          ),
                        )}
                        {dots.more > 0 && (
                          <span className="font-condensed text-xs font-bold leading-none text-ink">
                            +
                          </span>
                        )}
                      </span>
                    </button>
                  </div>
                );
              })}
              {bars.length > 0 && (
                <div aria-hidden="true" className="pointer-events-none absolute inset-0">
                  {bars.map((bar) => (
                    <Bar key={bar.entry.event.id} bar={bar} offset={offset} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
