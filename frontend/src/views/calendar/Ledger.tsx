import type { CSSProperties } from "react";

import { FollowMark, OutcomeSquare, SpotChip } from "../../components/bs";
import type { Game, League, Weather } from "../../types";
import {
  cardLabel,
  cardOutcome,
  cardStatus,
  contextLine,
  daySpan,
  daySpoken,
  eventKind,
  eventRow,
  fightLine,
  rowSides,
  sideValue,
  weatherLine,
  type EventDays,
  type SideValue,
} from "./model";
import {
  DateCell,
  SideBlock,
  SideFigure,
  SideMark,
  StatusText,
} from "./parts";

/**
 * Days of games as rows, in the language of the mockup's "Coming up" table:
 * the date large on the left, then for each game its time (or where it
 * stands), the two sides and the venue. "Coming up", the Agenda and a
 * month's chosen day are all this one list.
 *
 * Each row is one button that opens the box score, as every game row in
 * the app is, laid out on a grid rather than as a table so it can restack:
 * the four columns exist from 48rem of the LIST's own width, and below
 * that a row is three lines (a phone, or the narrow column beside a month).
 */

export interface LedgerDay {
  key: string;
  games: readonly Game[];
  events: readonly EventDays[];
}

const ROW =
  "bs-focus-inset grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 px-4 py-3 text-left hover:bg-tint @3xl/ledger:grid-cols-[var(--ledger-columns)] @3xl/ledger:py-4 @3xl/ledger:pl-3 @3xl/ledger:pr-6";
/** A cell's place in the stacked row, released once the columns exist. */
const STATUS = "col-start-1 row-start-1 min-w-0 @3xl/ledger:col-auto @3xl/ledger:row-auto";
const MATCHUP =
  "col-span-2 row-start-2 flex min-w-0 flex-col gap-1.5 @3xl/ledger:col-span-1 @3xl/ledger:col-auto @3xl/ledger:row-auto";
const WHERE =
  "col-span-2 row-start-3 min-w-0 empty:hidden @3xl/ledger:col-span-1 @3xl/ledger:col-auto @3xl/ledger:row-auto @3xl/ledger:empty:block";
const RESULT =
  "col-start-2 row-start-1 flex justify-end @3xl/ledger:col-auto @3xl/ledger:row-auto";
const SMALL_CAPS =
  "font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-muted";

/** A row's figure: its score, or "Won"; where it plays is said in the venue cell. */
function rowValue(value: SideValue): SideValue {
  return value !== null && value.kind === "tag" && value.text !== "Won"
    ? null
    : value;
}

function GameRow({
  game,
  teamFilter,
  league,
  weather,
  onOpen,
}: {
  game: Game;
  teamFilter: string | null;
  league: League | undefined;
  weather: Weather | undefined;
  onOpen: (game: Game) => void;
}) {
  const sides = rowSides(game, teamFilter);
  const outcome = cardOutcome(game, teamFilter);
  const context = contextLine(game, league);
  const forecast = game.phase === "scheduled" ? weatherLine(weather) : null;
  const fight = fightLine(game);
  // With a point of view the followed side leads the row; with none, both
  // sides are set the same and the scores say who is ahead.
  const { viewed } = sides;
  return (
    <li className="border-t border-hair first:border-t-0">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={cardLabel(game, teamFilter, forecast, league)}
        onClick={() => onOpen(game)}
        className={ROW}
      >
        <span className={STATUS}>
          <StatusText
            status={cardStatus(game)}
            timeClass="text-[1.375rem] @3xl/ledger:text-[1.875rem]"
          />
        </span>
        <span className={MATCHUP}>
          <span
            className={
              "flex min-w-0 items-center gap-2.5 text-ink " +
              (viewed ? "font-bold" : "font-medium")
            }
          >
            <SideBlock side={sides.first} />
            <span className="min-w-0 truncate">{sides.first.name}</span>
            <SideMark game={game} side={sides.first} />
            <SideFigure
              value={rowValue(sideValue(game, sides.firstKey))}
              gameId={game.id}
              sizeClass="text-[1.375rem]"
            />
          </span>
          <span
            className={
              "flex min-w-0 items-center gap-2.5 " +
              (viewed ? "text-ink-2" : "font-medium text-ink")
            }
          >
            <SideBlock side={sides.second} />
            <span className="min-w-0 truncate">
              {sides.word} {sides.second.name}
            </span>
            <SideMark game={game} side={sides.second} />
            <SideFigure
              value={rowValue(sideValue(game, sides.secondKey))}
              gameId={game.id}
              sizeClass="text-[1.375rem]"
            />
          </span>
          {fight !== null && (
            // How a bout ended, where another row has its score.
            <span className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">
              {fight}
            </span>
          )}
        </span>
        <span className={WHERE}>
          {game.venue && (
            <span className="block text-sm font-medium text-ink-2 @3xl/ledger:text-[0.9375rem] @3xl/ledger:text-ink">
              {game.venue}
            </span>
          )}
          {(sides.place !== null || context !== null || forecast !== null) && (
            <span className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              {sides.place !== null && (
                <span className={SMALL_CAPS}>{sides.place}</span>
              )}
              {context !== null && (
                <span className="text-[0.8125rem] text-muted">{context}</span>
              )}
              {forecast !== null && (
                <span title={forecast.spoken} className="text-[0.8125rem] text-muted">
                  {forecast.text}
                </span>
              )}
            </span>
          )}
        </span>
        <span className={RESULT}>
          {outcome !== null && (
            <OutcomeSquare result={outcome.outcome} size={30} label={null} />
          )}
        </span>
      </button>
    </li>
  );
}

function EventRow({
  entry,
  dayKey,
  todayKey,
  onOpen,
}: {
  entry: EventDays;
  /** The day this row stands on: a list shows a tournament on each of its days. */
  dayKey: string;
  todayKey: string;
  onOpen: (eventId: string) => void;
}) {
  const { event } = entry;
  const { live, state, dayOf, followed, label } = eventRow(entry, dayKey, todayKey);
  return (
    <li className="border-t border-hair first:border-t-0">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={label}
        onClick={() => onOpen(event.id)}
        className={ROW}
      >
        <span className={STATUS}>
          {live ? (
            <span className="flex min-w-0 items-center gap-2">
              <SpotChip>Live</SpotChip>
              {event.round_label !== "" && (
                <span className="min-w-0 truncate font-condensed text-base font-bold uppercase leading-none tracking-[0.04em] text-ink">
                  {event.round_label}
                </span>
              )}
            </span>
          ) : (
            <span className="font-condensed text-base font-semibold uppercase leading-none tracking-[0.08em] text-ink-2">
              {state ?? dayOf ?? "All day"}
            </span>
          )}
        </span>
        <span className={MATCHUP}>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-1 font-bold text-ink">
            <span className={SMALL_CAPS}>{eventKind(event.sport)}</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">{event.name}</span>
          </span>
          {followed.map((row, index) => (
            <span
              key={index}
              className="flex min-w-0 items-center gap-2 text-ink-2 tabular-nums"
            >
              {/* A place and a score only once there are standings: before
                  the first round the board is the entry list. */}
              {row.place !== null && (
                <span className="flex-none font-condensed text-base font-bold leading-none text-muted">
                  {row.place}
                </span>
              )}
              <span className="min-w-0 truncate font-semibold text-ink">
                {row.name}
              </span>
              <FollowMark label={null} />
              {row.place !== null && (
                <span className="ml-auto flex-none font-condensed text-[1.375rem] font-bold leading-none text-ink">
                  {row.score ?? "—"}
                </span>
              )}
            </span>
          ))}
        </span>
        <span className={WHERE}>
          {event.venue && (
            <span className="block text-sm font-medium text-ink-2 @3xl/ledger:text-[0.9375rem] @3xl/ledger:text-ink">
              {event.venue}
            </span>
          )}
          <span className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            {/* Which day of it this is, unless the status cell says so. */}
            {(live || state !== null) && dayOf !== null && (
              <span className={SMALL_CAPS}>{dayOf}</span>
            )}
            <span className="text-[0.8125rem] text-muted">
              {daySpan({ first: entry.first, last: entry.last })}
            </span>
          </span>
        </span>
        <span className={RESULT} />
      </button>
    </li>
  );
}

export default function Ledger({
  days,
  todayKey,
  teamFilter,
  leaguesById,
  weather,
  onOpenGame,
  onOpenEvent,
  heads,
  dates = true,
}: {
  /** The days to list, in order; a day with nothing on is not passed. */
  days: readonly LedgerDay[];
  todayKey: string;
  teamFilter: string | null;
  leaguesById: Record<string, League>;
  weather: Record<string, Weather> | undefined;
  onOpenGame: (game: Game) => void;
  onOpenEvent: (eventId: string) => void;
  /** The small caps over the columns, where the columns exist. */
  heads?: { when: string };
  /** The date beside each day's rows; off for a list that is one day. */
  dates?: boolean;
}) {
  // One set of columns for the whole list. The last holds the result
  // square, and has no width in a list that shows none.
  const squares = days.some((day) =>
    day.games.some((game) => cardOutcome(game, teamFilter) !== null),
  );
  const columns = `9.5rem minmax(0, 1.15fr) minmax(0, 1fr) ${squares ? "1.875rem" : "0rem"}`;
  const style = { "--ledger-columns": columns } as CSSProperties;
  const dayGrid = dates
    ? "@3xl/ledger:grid @3xl/ledger:grid-cols-[9.875rem_minmax(0,1fr)]"
    : "";
  return (
    <div className="@container/ledger" style={style}>
      {heads !== undefined && (
        <div
          aria-hidden="true"
          className={
            "bs-label hidden border-b border-hair font-semibold " +
            (dates ? dayGrid : "@3xl/ledger:block")
          }
        >
          {dates && <span className="px-6 py-2">Date</span>}
          <span className="hidden gap-x-3 py-2 pl-3 pr-6 @3xl/ledger:grid @3xl/ledger:grid-cols-[var(--ledger-columns)]">
            <span>{heads.when}</span>
            <span>Matchup</span>
            <span>Venue</span>
            <span />
          </span>
        </div>
      )}
      <ol className="m-0 list-none p-0">
        {days.map((day) => {
          const today = day.key === todayKey;
          return (
            <li
              key={day.key}
              className={`border-t border-hair first:border-t-0 ${dayGrid}`}
            >
              {dates && (
                <h3
                  aria-label={daySpoken(day.key, todayKey)}
                  className="m-0 border-b border-hair bg-tint px-4 py-2.5 font-normal @3xl/ledger:border-b-0 @3xl/ledger:bg-transparent @3xl/ledger:px-6 @3xl/ledger:py-4"
                >
                  <DateCell dayKey={day.key} today={today} />
                </h3>
              )}
              <ul className="m-0 min-w-0 list-none p-0">
                {day.events.map((entry) => (
                  <EventRow
                    key={entry.event.id}
                    entry={entry}
                    dayKey={day.key}
                    todayKey={todayKey}
                    onOpen={onOpenEvent}
                  />
                ))}
                {day.games.map((game) => (
                  <GameRow
                    key={game.id}
                    game={game}
                    teamFilter={teamFilter}
                    league={leaguesById[game.league_id]}
                    weather={weather?.[game.id]}
                    onOpen={onOpenGame}
                  />
                ))}
              </ul>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
