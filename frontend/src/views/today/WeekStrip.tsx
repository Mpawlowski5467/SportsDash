import { ScrollRegion, SectionHead } from "../../components/bs";
import { longDay, weekdayShort } from "../../lib/time";
import type { WeekDay } from "./model";

/**
 * The next seven days, today first: one cell per day with the followed
 * teams' games. Seven fixed columns in a sheet that scrolls sideways when
 * it must, so today and the days after it are always the visible end.
 */
export default function WeekStrip({
  days,
  loaded,
}: {
  days: readonly WeekDay[];
  /**
   * Whether the week's schedule has arrived. Until it has, a day with no
   * lines is a day we do not know about, not a day with no games.
   */
  loaded: boolean;
}) {
  return (
    <section aria-label="Next seven days">
      <SectionHead title="Next 7 days" note="Your teams' schedule" />
      <ScrollRegion label="Next seven days" className="bs-sheet">
        <ol className="m-0 grid min-w-[56rem] list-none grid-cols-7 p-0">
          {days.map((day) => {
            const empty = day.lines.length === 0;
            const unknown = empty && !loaded && !day.isToday;
            return (
              <li
                key={day.key}
                aria-label={longDay(day.date)}
                className={
                  "flex min-h-[7.375rem] flex-col gap-1.5 border-l border-hair px-4 pb-3.5 first:border-l-0 " +
                  (day.isToday ? "" : "pt-3 ") +
                  (empty && !unknown ? "bg-tint" : "")
                }
              >
                {day.isToday && (
                  <div className="-mx-4 h-1.5 flex-none bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]" />
                )}
                <div
                  className={
                    "flex items-baseline justify-between font-condensed uppercase" +
                    (day.isToday ? " pt-1.5" : "")
                  }
                >
                  <span
                    className={
                      "text-base tracking-[0.08em] " +
                      (day.isToday ? "font-bold text-ink" : "font-semibold text-muted")
                    }
                  >
                    {day.isToday ? "Today" : weekdayShort(day.date)}
                  </span>
                  <span
                    className={
                      "text-[2.5rem] font-bold leading-[0.9] " +
                      (empty ? "text-muted" : "text-ink")
                    }
                  >
                    {day.date.getDate()}
                  </span>
                </div>
                {unknown ? null : empty ? (
                  <span className="text-muted">No games</span>
                ) : (
                  day.lines.map((line, index) => (
                    <span
                      key={index}
                      className={
                        "line-clamp-2 [overflow-wrap:anywhere] " +
                        (day.isToday ? "font-semibold text-ink" : "text-ink-2")
                      }
                    >
                      {line}
                    </span>
                  ))
                )}
                {day.more > 0 && (
                  <span className="text-sm text-muted">+{day.more} more</span>
                )}
              </li>
            );
          })}
        </ol>
      </ScrollRegion>
    </section>
  );
}
