import { Button, SectionHead } from "../../components/bs";
import { countOf } from "../../lib/labels";
import type { Game, League, Weather } from "../../types";
import Ledger, { type LedgerDay } from "./Ledger";
import {
  daySpan,
  daySpoken,
  shortDay,
  weekdayOf,
  wordsFor,
  type ComingUpFoot,
  type DayRange,
} from "./model";

/** What every list of rows needs to draw a game and open it. */
export interface RowContext {
  todayKey: string;
  teamFilter: string | null;
  leaguesById: Record<string, League>;
  weather: Record<string, Weather> | undefined;
  onOpenGame: (game: Game) => void;
  onOpenEvent: (eventId: string) => void;
}

/**
 * "Coming up": the games still to start in the days after the range on
 * screen, as the mockup's table. At most a handful of rows; when the days
 * hold more, the foot says how many and opens the week they are in.
 *
 * Its days are a request of their own, so it has its own three states: a
 * list that has not arrived, or failed to, is not an empty one.
 */
export function ComingUp({
  days,
  window,
  note,
  foot,
  onOpenWeek,
  state,
  teamName,
  context,
}: {
  days: readonly LedgerDay[];
  window: DayRange;
  note: string;
  /** Set when the window holds more games than there are rows. */
  foot: ComingUpFoot | null;
  onOpenWeek: (week: DayRange) => void;
  state:
    | { kind: "loading" }
    | { kind: "failed"; detail: string; onRetry: () => void }
    | { kind: "ready" };
  /** The team the page is filtered to, for the sentence when nothing is on. */
  teamName: string | null;
  context: RowContext;
}) {
  return (
    <section
      aria-label="Coming up"
      className="flex min-w-0 flex-[999_1_35rem] flex-col"
    >
      <SectionHead title="Coming up" note={note} />
      <div className="bs-sheet flex flex-1 flex-col">
        {state.kind === "loading" ? (
          <p role="status" className="m-0 px-4 py-4 text-ink-2">
            Loading {daySpan(window)}…
          </p>
        ) : state.kind === "failed" ? (
          <p
            role="alert"
            className="m-0 flex flex-wrap items-center gap-x-3 px-4 py-2 font-medium text-alert"
          >
            Couldn&rsquo;t load {daySpan(window)}
            {state.detail}.
            <button
              type="button"
              onClick={state.onRetry}
              className="bs-link min-w-11 cursor-pointer justify-center"
            >
              Retry
            </button>
          </p>
        ) : days.length === 0 ? (
          // Only what is still to start is listed here, and only what the
          // backend has stored: the sentence claims no more than that.
          <p className="m-0 px-4 py-4 text-ink-2">
            {teamName === null
              ? `Nothing upcoming is stored for ${daySpan(window)}.`
              : `Nothing upcoming is stored for ${teamName}, ${daySpan(window)}.`}
          </p>
        ) : (
          <Ledger days={days} heads={{ when: "Time" }} {...context} />
        )}
        {state.kind === "ready" && foot !== null && (
          <div className="mt-auto flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-hair bg-tint px-4 py-2.5">
            <span className="text-sm font-medium text-ink-2">{foot.line}</span>
            <Button variant="ghost" onClick={() => onOpenWeek(foot.week)}>
              {foot.action}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}

/** The Agenda view: every day of the range that has something on, as rows. */
export function Agenda({
  days,
  label,
  note,
  empty,
  context,
}: {
  days: readonly LedgerDay[];
  /** The section's accessible name: "Agenda, week of July 27 to August 2". */
  label: string;
  note: string | undefined;
  empty: { headline: string; detail: string };
  context: RowContext;
}) {
  return (
    <section aria-label={label}>
      <SectionHead title="Agenda" note={note} />
      <div className="bs-sheet" data-calendar-grid="agenda">
        {days.length === 0 ? (
          <div className="bg-tint px-5 py-8">
            <p className="m-0 font-condensed text-3xl font-bold uppercase leading-none text-ink">
              {empty.headline}
            </p>
            <p className="m-0 mt-2 max-w-[44rem] text-ink-2">{empty.detail}</p>
          </div>
        ) : (
          <Ledger days={days} heads={{ when: "Time" }} {...context} />
        )}
      </div>
    </section>
  );
}

/**
 * Beside the month: the chosen day's games and tournaments in full. This is
 * where a game in a month is opened from; the grid's cells only choose the
 * day.
 */
export function DayPanel({
  day,
  under = false,
  context,
}: {
  day: LedgerDay;
  /**
   * The list takes the full width under the grid instead of the column
   * beside it: set when some day of the month has more rows than that
   * column holds (see `busiestDayRows`).
   */
  under?: boolean;
  context: RowContext;
}) {
  const count = day.games.length;
  const nothing = count === 0 && day.events.length === 0;
  return (
    <section
      aria-label={daySpoken(day.key, context.todayKey)}
      className={
        "flex min-w-0 flex-col " + (under ? "flex-[1_1_100%]" : "flex-[1_1_18rem]")
      }
    >
      <SectionHead
        title={`${weekdayOf(day.key)} ${shortDay(day.key)}`}
        note={
          day.key === context.todayKey
            ? "Today"
            : count > 0
              ? countOf(count, wordsFor(day.games))
              : undefined
        }
      />
      <div className="bs-sheet">
        {nothing ? (
          <p className="m-0 bg-tint px-4 py-4 text-ink-2">
            Nothing is stored for this day.
          </p>
        ) : (
          <Ledger days={[day]} dates={false} {...context} />
        )}
      </div>
    </section>
  );
}
