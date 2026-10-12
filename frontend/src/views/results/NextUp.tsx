import { SectionHead, SpotChip } from "../../components/bs";
import { formatTime, longDay } from "../../lib/time";
import type { Game, Sport, Team } from "../../types";
import {
  followedOpponent,
  nextUpDay,
  nextUpNote,
  perspective,
  venueWord,
} from "./model";
import { Opponent } from "./ResultsTable";

/**
 * The team's next few games, one cell each: the day, and who it is
 * against. A game being played now carries the spot bar and a "Live now"
 * chip. The cells share the row equally and wrap onto further rows on a
 * narrow sheet; the hairlines between them are the sheet's gaps, so they
 * hold however the cells wrap.
 */
export default function NextUp({
  games,
  team,
  teams,
  sport,
  todayKey,
  zone,
}: {
  games: readonly Game[];
  team: Team;
  teams: readonly Team[];
  sport: Sport | undefined;
  /**
   * The server's day, and the zone it is in, as on Today: they decide which
   * game is today's. The date a cell shows is the viewer's own.
   */
  todayKey: string;
  zone: string | undefined;
}) {
  if (games.length === 0) return null;
  return (
    <section aria-label="Next up">
      <SectionHead title="Next up" note={nextUpNote(games, team, sport)} />
      <div className="bs-sheet">
        <ol className="m-0 flex list-none flex-wrap gap-px bg-hair p-0">
          {games.map((game) => {
            const live = game.phase === "in_progress";
            const day = nextUpDay(game, todayKey, zone);
            const marked = live || day.isToday;
            const view = perspective(game, team);
            const when = `${longDay(game.start_time)}, ${formatTime(game.start_time)}`;
            return (
              <li
                key={game.id}
                className={
                  "flex min-h-[6.5rem] min-w-0 flex-[1_1_11.875rem] flex-col gap-1.5 bg-sheet px-5 pb-3.5 " +
                  (marked ? "" : "pt-3")
                }
              >
                {marked && (
                  <div className="-mx-5 h-1.5 flex-none bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]" />
                )}
                {/* Read out in full, start time included; the cell itself
                    shows only the day, with the time on hover. */}
                <span className="sr-only">
                  {live ? "Live now. " : ""}
                  {when}.
                </span>
                <div
                  aria-hidden="true"
                  title={when}
                  className={
                    "flex items-baseline justify-between gap-2.5 font-condensed uppercase " +
                    (marked ? "pt-1.5" : "")
                  }
                >
                  <span
                    className={
                      "flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-base tracking-[0.08em] " +
                      (marked ? "font-bold text-ink" : "font-semibold text-muted")
                    }
                  >
                    {day.label}
                    {live && <SpotChip>Live now</SpotChip>}
                  </span>
                  <span className="text-[2.5rem] font-bold leading-[0.9] text-ink">
                    {day.day}
                  </span>
                </div>
                {view !== null ? (
                  <Opponent
                    word={venueWord(game.sport, view.home)}
                    side={view.theirs}
                    followed={followedOpponent(view.theirs, teams, team)}
                    bold={marked}
                    tokenClass=""
                  />
                ) : (
                  <span className="font-semibold">
                    {game.away.name} at {game.home.name}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
