import type { ReactNode } from "react";

import {
  OutcomeSquare,
  ScoreFigure,
  SpotChip,
  Tag,
} from "../../components/bs";
import {
  fightMethodLabel,
  fightResultOf,
  fightResultSummary,
  fightRoundLabel,
} from "../../lib/fight";
import { formatTime } from "../../lib/time";
import { watchability } from "../../lib/whyWatch";
import type { Game, GameOdds, League } from "../../types";
import {
  favoriteOf,
  followedOutcome,
  isIndividualSport,
  leadingSide,
  liveStatus,
  showsScore,
  type SideKey,
} from "./model";
import { SideName, SplitBar, boxScoreLabel } from "./parts";

/**
 * Today's lists of games. Each row is one button that opens the box score,
 * as every game row in the app always has been; the rows are laid out on a
 * grid rather than as a table so the same markup can restack instead of
 * scrolling sideways with its last column off screen.
 *
 * The columned layout is chosen from the width of the LIST (a container
 * query, `@4xl` = 56rem), not of the window: the lists sit in the page's
 * left column, which is far narrower than the window whenever the page is
 * two columns wide.
 */

const ROW =
  "bs-focus-inset grid w-full cursor-pointer items-center gap-x-3 gap-y-2 px-4 py-3.5 text-left hover:bg-tint @4xl:px-6";

/** Small caps over the columns, at widths where the columns exist. */
function ColumnHeads({ className, labels }: { className: string; labels: string[] }) {
  return (
    <div
      aria-hidden="true"
      className={`bs-label hidden gap-x-3 px-6 py-2 font-semibold @4xl:grid ${className}`}
    >
      {labels.map((label, index) => (
        <span key={index}>{label}</span>
      ))}
    </div>
  );
}

/** The line under a matchup: the competition, or the round or card. */
function contextLine(game: Game, league: League | undefined): string | null {
  const parts = [
    // A whole-competition game has no followed side to explain why it is
    // here, so it says which competition it belongs to.
    game.followed_team_ids.length === 0 ? (league?.name ?? null) : null,
    game.series,
  ].filter((part): part is string => part !== null && part !== "");
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** How much each side's name carries in a row with no result yet. */
function upcomingTone(game: Game, key: SideKey): "lead" | "plain" | "quiet" {
  if (game.followed_team_ids.length === 0) return "plain";
  const side = game[key];
  return side.team_id !== null && game.followed_team_ids.includes(side.team_id)
    ? "lead"
    : "quiet";
}

const UPCOMING_COLUMNS =
  "@4xl:grid-cols-[8rem_minmax(0,1.9fr)_minmax(0,1fr)_minmax(0,0.8fr)_9.5rem]";

/** Games still to start: when, who, where, how to watch, and who is favoured. */
export function UpcomingList({
  games,
  oddsByGame,
  leaguesById,
  onOpen,
}: {
  games: readonly Game[];
  oddsByGame: Record<string, GameOdds>;
  leaguesById: Record<string, League>;
  onOpen: (game: Game) => void;
}) {
  return (
    <div className="bs-sheet @container">
      <ColumnHeads
        className={UPCOMING_COLUMNS}
        labels={["Time", "Matchup", "Venue", "Watch", "Favorite"]}
      />
      <ul className="m-0 list-none p-0">
        {games.map((game) => {
          const odds = oddsByGame[game.id];
          const favorite = favoriteOf(game, odds);
          const watch = watchability(game, odds);
          const context = contextLine(game, leaguesById[game.league_id]);
          const off = game.phase === "postponed" || game.phase === "canceled";
          const where = [game.venue, game.broadcasts[0] ?? null].filter(
            (part): part is string => part !== null && part !== "",
          );
          return (
            <li key={game.id} className="border-t border-hair">
              <button
                type="button"
                aria-haspopup="dialog"
                aria-label={boxScoreLabel(game)}
                onClick={() => onOpen(game)}
                className={`${ROW} grid-cols-[minmax(0,1fr)_auto] ${UPCOMING_COLUMNS}`}
              >
                <span
                  className={
                    "order-1 whitespace-nowrap font-condensed text-[1.375rem] font-bold uppercase leading-none @4xl:order-none @4xl:text-[1.875rem] " +
                    (off ? "text-muted" : "text-ink")
                  }
                >
                  {game.phase === "postponed"
                    ? "PPD"
                    : game.phase === "canceled"
                      ? "Canc"
                      : formatTime(game.start_time)}
                </span>

                <span className="order-3 col-span-2 flex min-w-0 flex-col gap-1.5 @4xl:order-none @4xl:col-span-1">
                  <SideName game={game} side={game.away} tone={upcomingTone(game, "away")} />
                  <SideName
                    game={game}
                    side={game.home}
                    tone={upcomingTone(game, "home")}
                    prefix={isIndividualSport(game.sport) ? "vs" : "at"}
                  />
                  {context !== null && (
                    <span className="text-sm text-muted">{context}</span>
                  )}
                </span>

                <span className="hidden min-w-0 text-ink-2 @4xl:block">
                  {game.venue ?? ""}
                </span>
                <span className="hidden min-w-0 text-ink-2 @4xl:block">
                  {game.broadcasts[0] ?? ""}
                </span>

                <span className="order-2 flex flex-col items-end gap-1.5 @4xl:order-none @4xl:items-start">
                  {favorite !== null && (
                    <>
                      <span
                        title={favorite.title}
                        className="whitespace-nowrap font-condensed text-[1.375rem] font-bold uppercase leading-none tabular-nums"
                      >
                        {favorite.label}
                      </span>
                      {favorite.split !== null && (
                        <SplitBar
                          game={game}
                          away={favorite.split.away}
                          home={favorite.split.home}
                          className="h-2 w-[7.5rem]"
                        />
                      )}
                    </>
                  )}
                  {watch !== null && <Tag>{watch.reason}</Tag>}
                </span>

                {where.length > 0 && (
                  <span className="order-4 col-span-2 text-sm text-ink-2 @4xl:hidden">
                    {where.join(" · ")}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// The names get the flexible space: a truncated team beside a half-empty
// venue column is the wrong trade.
const RESULT_COLUMNS =
  "@4xl:grid-cols-[5.5rem_minmax(0,1.3fr)_2.75rem_2.75rem_minmax(0,1.3fr)_minmax(0,0.7fr)_2.25rem]";
/** The same, with room for basketball's three figures. */
const RESULT_COLUMNS_WIDE =
  "@4xl:grid-cols-[5.5rem_minmax(0,1.3fr)_4rem_4rem_minmax(0,1.3fr)_minmax(0,0.7fr)_2.25rem]";

/** A score numeral: the side in front in ink, the side behind stepped back. */
function SideScore({
  game,
  sideKey,
  className,
}: {
  game: Game;
  sideKey: SideKey;
  className: string;
}) {
  const leader = leadingSide(game);
  return (
    <span
      className={
        `text-center font-condensed text-[2.125rem] leading-none tabular-nums ${className} ` +
        (leader === null || leader === sideKey
          ? "font-bold text-ink"
          : "font-medium text-muted")
      }
    >
      {showsScore(game) && (
        <ScoreFigure of={game.id} value={game[sideKey].score} />
      )}
    </span>
  );
}

/** How a side's name reads once there is a score: winner forward, loser back. */
function resultTone(game: Game, key: SideKey): "lead" | "plain" | "quiet" {
  const leader = leadingSide(game);
  if (leader === null) return "plain";
  return leader === key ? "lead" : "quiet";
}

/** The first cell of a result row: "Final", how a fight ended, or the live period. */
function ResultLead({ game }: { game: Game }): ReactNode {
  if (game.phase === "in_progress") {
    const status = liveStatus(game);
    return (
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1 @4xl:flex-col @4xl:items-start">
        <SpotChip>Live</SpotChip>
        <span className="font-condensed text-base font-bold uppercase leading-none tracking-[0.04em] text-ink">
          {[status.headline === "Live" ? null : status.headline, status.detail]
            .filter(Boolean)
            .join(" ")}
        </span>
      </span>
    );
  }
  const fight = fightResultOf(game);
  const method = fight === null ? "" : fightMethodLabel(fight.method);
  const round = fight === null ? null : fightRoundLabel(fight);
  return (
    <span
      title={fight === null ? undefined : fightResultSummary(fight)}
      className="flex flex-wrap items-baseline gap-x-2 font-condensed text-base font-semibold uppercase leading-tight tracking-[0.08em] text-muted @4xl:flex-col"
    >
      <span>Final</span>
      {method !== "" && <span className="text-ink">{method}</span>}
      {round !== null && <span>{round}</span>}
    </span>
  );
}

/**
 * Games with a score: finished ones (with the result as the followed team
 * saw it) and, under the live hero, the other games in progress. Away side
 * first, as everywhere else in the app.
 *
 * A live row also says where to watch and, when the game has earned it, why
 * ("Tight late"), as a live row always has; pass `oddsByGame` for that.
 */
export function ResultsList({
  games,
  leaguesById,
  onOpen,
  oddsByGame,
  flushTop = false,
  label,
}: {
  games: readonly Game[];
  leaguesById: Record<string, League>;
  onOpen: (game: Game) => void;
  oddsByGame?: Record<string, GameOdds>;
  /** Sits under another sheet rather than a section head: no top edge. */
  flushTop?: boolean;
  /** A small-caps line over the rows ("Also live"). */
  label?: string;
}) {
  // One set of columns for the whole list, so the scores line up down it.
  const columns = games.some(
    (game) => (game.home.score ?? 0) > 99 || (game.away.score ?? 0) > 99,
  )
    ? RESULT_COLUMNS_WIDE
    : RESULT_COLUMNS;
  return (
    <div className={"bs-sheet @container" + (flushTop ? " border-t-0" : "")}>
      {label !== undefined && (
        <div className="bs-label border-b border-hair bg-tint px-4 py-2 @4xl:px-6">
          {label}
        </div>
      )}
      <ul className="m-0 list-none p-0">
        {games.map((game) => {
          const live = game.phase === "in_progress";
          const outcome = game.phase === "final" ? followedOutcome(game) : null;
          const watch = live ? watchability(game, oddsByGame?.[game.id]) : null;
          const where = [
            game.venue,
            live ? (game.broadcasts[0] ?? null) : null,
            contextLine(game, leaguesById[game.league_id]),
          ]
            .filter((part): part is string => part !== null && part !== "")
            .join(" · ");
          return (
            <li key={game.id} className="border-t border-hair first:border-t-0">
              <button
                type="button"
                aria-haspopup="dialog"
                aria-label={boxScoreLabel(game)}
                onClick={() => onOpen(game)}
                className={`${ROW} grid-cols-[minmax(0,1fr)_auto_auto] ${columns}`}
              >
                <span className="col-span-3 @4xl:col-span-1">
                  <ResultLead game={game} />
                </span>
                <SideName
                  game={game}
                  side={game.away}
                  tone={resultTone(game, "away")}
                  className="col-start-1 row-start-2 @4xl:col-start-auto @4xl:row-start-auto"
                />
                <SideScore
                  game={game}
                  sideKey="away"
                  className="col-start-2 row-start-2 min-w-9 @4xl:col-start-auto @4xl:row-start-auto"
                />
                <SideScore
                  game={game}
                  sideKey="home"
                  className="col-start-2 row-start-3 min-w-9 @4xl:col-start-auto @4xl:row-start-auto"
                />
                <SideName
                  game={game}
                  side={game.home}
                  tone={resultTone(game, "home")}
                  className="col-start-1 row-start-3 @4xl:col-start-auto @4xl:row-start-auto"
                />
                {/* One line of where and why. A cell of its own in the
                    columned layout, a line under the sides when stacked. */}
                <span className="col-span-3 row-start-4 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-2 empty:hidden @4xl:col-span-1 @4xl:row-start-auto @4xl:text-[0.9375rem]">
                  {where !== "" && <span className="min-w-0">{where}</span>}
                  {watch !== null && <Tag>{watch.reason}</Tag>}
                </span>
                <span className="col-start-3 row-span-2 row-start-2 flex justify-end @4xl:col-start-auto @4xl:row-span-1 @4xl:row-start-auto">
                  {outcome !== null && <OutcomeSquare result={outcome} size={30} />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
