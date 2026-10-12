import type { CSSProperties, ReactNode } from "react";

import { FollowMark, OutcomeSquare, SpotChip } from "../../components/bs";
import { isIndividualSport } from "../../lib/labels";
import type { Game, League, Weather } from "../../types";
import {
  cardLabel,
  cardOutcome,
  cardSideName,
  cardStatus,
  competitionName,
  contextLine,
  dayKeys,
  daySpan,
  daySpoken,
  eventKind,
  eventLabel,
  eventState,
  fightLine,
  followedNoun,
  gamesByDay,
  isBusyDay,
  layoutBars,
  sideValue,
  weatherLine,
  type DayRange,
  type EventBar,
  type EventDays,
} from "./model";
import {
  Chevron,
  DateCell,
  SideBlock,
  SideFigure,
  SideMark,
  StatusText,
} from "./parts";

/**
 * The week as seven day columns of score cards, as mocked, with the
 * tournaments (which the mockup has none of) as a band of bars under the
 * day headers, each spanning its days.
 *
 * One set of markup, two layouts, chosen by the width of the SECTION (the
 * `week` container the view puts round this): seven columns from 60rem,
 * and below that a list of days down the page, so a phone never scrolls
 * the week sideways. In the list the same cards are as wide as the sheet
 * and so have room to spell the teams' names out.
 */

/** The DOM id of a day's heading: what "Jump to today" scrolls to. */
export function dayHeadingId(dayKey: string): string {
  return `sd-calendar-day-${dayKey}`;
}

function TournamentBar({
  bar,
  onOpen,
}: {
  bar: EventBar;
  onOpen: (eventId: string) => void;
}) {
  const { event } = bar.entry;
  const live = event.phase === "in_progress";
  const state = eventState(event);
  return (
    <li
      className="@container/bar min-w-0 p-1"
      style={{
        gridColumn: `${bar.column + 1} / span ${bar.span}`,
        gridRow: bar.lane + 2,
      }}
    >
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={eventLabel(bar.entry)}
        onClick={() => onOpen(event.id)}
        className={
          "bs-focus-inset flex min-h-11 w-full min-w-0 cursor-pointer items-center gap-2 border-2 border-ink bg-sheet px-2.5 text-left text-ink hover:bg-hover " +
          // A bar cut by the edge of the week is open on that side.
          (bar.startsBefore ? `@[60rem]/week:border-l-0 ` : "") +
          (bar.endsAfter ? `@[60rem]/week:border-r-0` : "")
        }
      >
        {bar.startsBefore && (
          <span className={`hidden flex-none @[60rem]/week:flex`}>
            <Chevron direction="left" size={14} />
          </span>
        )}
        {/* Not muted: the bar hovers to the hover fill, where muted text
            falls under the contrast floor. */}
        <span className="hidden flex-none font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-ink-2 @[13rem]/bar:inline">
          {eventKind(event.sport)}
        </span>
        <span className="min-w-0 truncate font-condensed text-[1.0625rem] font-bold uppercase leading-none tracking-[0.04em]">
          {event.name}
        </span>
        {/* In the list of days the bar no longer sits under its days, so
            it says which they are. */}
        <span className={`flex-none text-sm text-ink-2 @[60rem]/week:hidden`}>
          {daySpan({ first: bar.entry.first, last: bar.entry.last })}
        </span>
        <span className="ml-auto flex flex-none items-center gap-2">
          {live ? (
            <>
              <SpotChip size="sm">Live</SpotChip>
              {event.round_label !== "" && (
                <span className="hidden font-condensed text-[0.8125rem] font-bold uppercase leading-none tracking-[0.04em] @[22rem]/bar:inline">
                  {event.round_label}
                </span>
              )}
            </>
          ) : (
            state !== null && (
              <span className="font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-ink-2">
                {state}
              </span>
            )
          )}
        </span>
        {bar.endsAfter && (
          <span className={`hidden flex-none @[60rem]/week:flex`}>
            <Chevron direction="right" size={14} />
          </span>
        )}
      </button>
    </li>
  );
}

function GameCard({
  game,
  compact,
  railed,
  teamFilter,
  league,
  weather,
  onOpen,
}: {
  game: Game;
  /** A busy day's card: no venue, shorter rows. */
  compact: boolean;
  /** In today's column, whose 2px rails take the place of 2px of padding. */
  railed: boolean;
  teamFilter: string | null;
  league: League | undefined;
  weather: Weather | undefined;
  onOpen: (game: Game) => void;
}) {
  const status = cardStatus(game);
  const outcome = cardOutcome(game, teamFilter);
  const individual = isIndividualSport(game.sport);
  const competition = competitionName(game, league);
  const context = contextLine(game, league);
  const forecast = game.phase === "scheduled" ? weatherLine(weather) : null;
  const fight = fightLine(game);
  const lines: ReactNode[] = [];
  if (fight !== null) {
    // A bout's result, where another card has its score: never cut short,
    // and kept on a busy day's card too.
    lines.push(
      <span key="fight" className="font-semibold text-ink [overflow-wrap:anywhere]">
        {fight}
      </span>,
    );
  }
  if (!compact && game.venue) {
    lines.push(
      <span key="venue" className="truncate font-medium text-ink-2">
        {game.venue}
      </span>,
    );
  }
  if (!compact && context !== null) {
    lines.push(
      <span key="context" className="truncate text-muted">
        {context}
      </span>,
    );
  }
  if (forecast !== null) {
    lines.push(
      <span key="weather" title={forecast.spoken} className="truncate text-muted">
        {forecast.text}
      </span>,
    );
  }
  return (
    <li className="@container/card border-b border-hair bg-sheet">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={cardLabel(game, teamFilter, forecast, league)}
        onClick={() => onOpen(game)}
        className={
          "bs-focus-inset flex w-full cursor-pointer flex-col text-left hover:bg-tint " +
          (compact
            ? "px-3.5 pb-2.5 pt-2 "
            : `px-3.5 pb-4 pt-3.5 @[60rem]/week:min-h-[10.5rem] `) +
          (railed ? `@[60rem]/week:px-3` : "")
        }
      >
        <span
          className={
            "flex items-center justify-between gap-2 " +
            (compact ? "h-6" : "h-7")
          }
        >
          <StatusText
            status={status}
            timeClass={compact ? "text-xl" : "text-[1.625rem]"}
          />
          {outcome !== null ? (
            <OutcomeSquare
              result={outcome.outcome}
              size={compact ? 24 : 26}
              label={null}
            />
          ) : (
            // A busy day's card has no line under the sides, so the
            // competition a game belongs to is named up here. In a narrow
            // column it is the one that gives way (shrink-[999]): a live
            // game's minute is the fact, and must not be cut to fit it.
            compact &&
            competition !== null && (
              <span className="min-w-0 shrink-[999] truncate font-condensed text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.08em] text-muted">
                {competition}
              </span>
            )
          )}
        </span>
        <span
          className={"flex flex-col gap-0.5 " + (compact ? "mt-1" : "mt-2.5")}
        >
          {(["away", "home"] as const).map((key) => {
            const side = game[key];
            const value = sideValue(game, key);
            return (
              <span
                key={key}
                className={
                  "flex items-center gap-2 " +
                  (compact ? "h-[1.625rem]" : "h-[2.125rem]")
                }
              >
                <SideBlock side={side} />
                {individual ? (
                  // A person's block is only initials: the surname goes
                  // with it even in a column, the full name where there
                  // is room.
                  <span className="min-w-0 truncate text-sm font-semibold text-ink">
                    <span className="@[17rem]/card:hidden">
                      {cardSideName(game, side)}
                    </span>
                    <span className="hidden @[17rem]/card:inline">{side.name}</span>
                  </span>
                ) : (
                  <span className="hidden min-w-0 truncate text-sm font-semibold text-ink @[17rem]/card:block">
                    {side.name}
                  </span>
                )}
                <SideMark game={game} side={side} />
                <SideFigure
                  value={value}
                  gameId={game.id}
                  sizeClass={compact ? "text-2xl" : "text-[2.125rem]"}
                />
              </span>
            );
          })}
        </span>
        {lines.length > 0 && (
          <span
            className={
              "flex flex-col text-[0.8125rem] leading-[1.125rem] " +
              (compact ? "mt-1" : "mt-2")
            }
          >
            {lines}
          </span>
        )}
      </button>
    </li>
  );
}

export default function WeekGrid({
  range,
  todayKey,
  games,
  events,
  teamFilter,
  leaguesById,
  weather,
  onOpenGame,
  onOpenEvent,
  empty,
  gamesWord,
  showResultKey,
}: {
  range: DayRange;
  todayKey: string;
  /** The range's games: cut to it, filtered, with the live copies laid over. */
  games: readonly Game[];
  /** The tournaments to draw; none while a team filter is on. */
  events: readonly EventDays[];
  teamFilter: string | null;
  leaguesById: Record<string, League>;
  weather: Record<string, Weather> | undefined;
  onOpenGame: (game: Game) => void;
  onOpenEvent: (eventId: string) => void;
  /** What to say when the week holds nothing at all. */
  empty: { headline: string; detail: string };
  /** "games", "matches" or "bouts": the word the rest of the page uses, for an empty day. */
  gamesWord: string;
  /** Whether W / L squares can appear at all (a team is followed). */
  showResultKey: boolean;
}) {
  const days = dayKeys(range);
  const byDay = gamesByDay(games);
  const { lanes, bars } = layoutBars(events, range);
  const todayIndex = days.indexOf(todayKey);
  const nothing = games.length === 0 && bars.length === 0;
  const bandRows = lanes > 0 ? `repeat(${lanes}, auto) ` : "";
  const gridStyle: CSSProperties = {
    gridTemplateRows: `auto ${bandRows}1fr`,
  };
  const bandStyle = (column: string): CSSProperties => ({
    gridColumn: column,
    gridRow: `2 / span ${lanes}`,
  });
  const drew = games.some((game) => cardOutcome(game, teamFilter)?.outcome === "D");
  const teamGame = games.some((game) => !isIndividualSport(game.sport));
  const noun = followedNoun(games);

  return (
    <div className="bs-sheet">
      <div
        data-calendar-grid="week"
        // -mb-px: the tallest column's last card tucks its hairline under
        // the key's ink rule, as drawn.
        className={`-mb-px @[60rem]/week:grid @[60rem]/week:grid-cols-7`}
        style={gridStyle}
      >
        {lanes > 0 && (
          <>
            <div
              aria-hidden="true"
              className={`hidden border-b border-hair bg-tint @[60rem]/week:block`}
              style={bandStyle("1 / -1")}
            />
            {todayIndex !== -1 && (
              // Today's rails run on through the band; a bar that crosses
              // today is drawn over them.
              <div
                aria-hidden="true"
                className={`hidden border-x-2 border-ink @[60rem]/week:block`}
                style={bandStyle(String(todayIndex + 1))}
              />
            )}
          </>
        )}
        {bars.length > 0 && (
          <ul
            aria-label="Tournaments"
            className={`m-0 list-none border-b border-hair bg-tint p-0 @[60rem]/week:contents`}
          >
            {bars.map((bar) => (
              <TournamentBar
                key={bar.entry.event.id}
                bar={bar}
                onOpen={onOpenEvent}
              />
            ))}
          </ul>
        )}

        {days.map((key, index) => {
          const today = key === todayKey;
          const dayGames = byDay.get(key) ?? [];
          const compact = isBusyDay(dayGames.length);
          // A hairline on the left of every column but the first, today's
          // (it has its own rails) and the one after today.
          const ruled = index !== 0 && !today && index !== todayIndex + 1;
          const rule = ruled ? `@[60rem]/week:border-l @[60rem]/week:border-hair ` : "";
          return (
            <div key={key} className={`@[60rem]/week:contents`}>
              <h3
                id={dayHeadingId(key)}
                tabIndex={-1}
                aria-label={daySpoken(key, todayKey)}
                className={
                  "bs-focus-inset m-0 flex-col font-normal " +
                  // With nothing on, the list of days is just the message.
                  (nothing ? "hidden @[60rem]/week:flex " : "flex ") +
                  // Today's heading is an inverted block, and the ring is
                  // drawn inside it: in the block's text colour, not the
                  // page's ink (ink on ink).
                  (today ? "focus-visible:outline-on-block" : rule)
                }
                style={{ gridColumn: index + 1, gridRow: 1 }}
              >
                {today && (
                  <span className="h-1.5 flex-none bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]" />
                )}
                <DateCell
                  dayKey={key}
                  today={today}
                  spread
                  className={
                    "box-border border-b-2 px-3.5 " +
                    (today
                      ? "bs-inverse h-[3.625rem] border-block pt-1.5"
                      : "h-16 border-ink pt-3")
                  }
                />
              </h3>
              {!nothing && (
                <ul
                  className={
                    "m-0 list-none bg-tint p-0 " +
                    (today ? "border-x-2 border-ink " : rule)
                  }
                  style={{ gridColumn: index + 1, gridRow: lanes + 2 }}
                >
                  {dayGames.length === 0 ? (
                    <li className={`px-3.5 py-3 text-muted @[60rem]/week:sr-only`}>
                      No {gamesWord}
                    </li>
                  ) : (
                    dayGames.map((game) => (
                      <GameCard
                        key={game.id}
                        game={game}
                        compact={compact}
                        railed={today}
                        teamFilter={teamFilter}
                        league={leaguesById[game.league_id]}
                        weather={weather?.[game.id]}
                        onOpen={onOpenGame}
                      />
                    ))
                  )}
                </ul>
              )}
            </div>
          );
        })}

        {nothing && (
          <div
            className={`bg-tint px-5 py-8 @[60rem]/week:flex @[60rem]/week:min-h-[10.5rem] @[60rem]/week:flex-col @[60rem]/week:items-center @[60rem]/week:justify-center @[60rem]/week:text-center`}
            style={{ gridColumn: "1 / -1", gridRow: 2 }}
          >
            <p className="m-0 font-condensed text-3xl font-bold uppercase leading-none text-ink">
              {empty.headline}
            </p>
            <p className="m-0 mt-2 max-w-[44rem] text-ink-2">{empty.detail}</p>
          </div>
        )}
      </div>

      {/* Nothing to explain in a week with no games, or to somebody who
          follows only golfers or drivers: no squares can appear, and no
          game has a home side. */}
      {games.length > 0 && (showResultKey || teamGame) && (
        <ul
          aria-label="Key"
          className="relative m-0 flex list-none flex-wrap items-center gap-x-7 gap-y-2 border-t-2 border-ink bg-sheet px-3.5 py-2.5 text-sm font-medium text-ink-2"
        >
          <li className="bs-label">Key</li>
          {showResultKey && (
            <>
              <li className="flex items-center gap-2">
                <OutcomeSquare result="W" label={null} />
                W: your {noun} won
              </li>
              <li className="flex items-center gap-2">
                <OutcomeSquare result="L" label={null} />
                L: your {noun} lost
              </li>
              {drew && (
                <li className="flex items-center gap-2">
                  <OutcomeSquare result="D" label={null} />
                  D: a draw
                </li>
              )}
              <li className="flex items-center gap-2">
                <FollowMark label={null} />
                Square marks {noun === "athlete" ? "an" : "a"} {noun} you follow
              </li>
            </>
          )}
          {teamGame && <li>Away team is listed first, home team second</li>}
        </ul>
      )}
    </div>
  );
}
