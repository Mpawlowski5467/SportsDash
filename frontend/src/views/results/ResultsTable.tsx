import { createContext, useContext, type CSSProperties } from "react";

import {
  FollowMark,
  MarkedName,
  OutcomeSquare,
  SectionHead,
  TeamBlock,
} from "../../components/bs";
import {
  fightMethodLabel,
  fightResultOf,
  fightRoundLabel,
  fightResultSummary,
} from "../../lib/fight";
import { sportWords } from "../../lib/labels";
import { formatShortDate, localDateKey, weekdayShort } from "../../lib/time";
import type { Game, GameSide, Team } from "../../types";
import { resultFor } from "../today/model";
import { useSideColor } from "../today/parts";
import {
  detailLabel,
  followedOpponent,
  perspective,
  venueWord,
  type MonthGroup,
} from "./model";

/**
 * How a month's table is laid out, decided once for the whole list so the
 * columns line up from one month to the next.
 */
export interface TableShape {
  /** Two score columns, or one "Method" column for a sport with no tally. */
  scores: "tally" | "method";
  /** Room for a three-figure score (basketball). */
  wideScores: boolean;
  /** Whether any row has a box score to open. */
  detail: boolean;
}

/**
 * The columns at table width. The mockup is an auto-layout table, which
 * shares its spare width among every column but the two scores, so these
 * are the widths it actually renders at 1440px, as shares: each column
 * gives ground in proportion on a narrower sheet, down to the width the
 * mockup declares for it.
 */
function columnsOf(shape: TableShape): string {
  const score = shape.wideScores ? "4.5rem" : "3.5rem";
  return [
    "minmax(6.875rem, 1.46fr)", // date
    "minmax(0, 5.47fr)", // opponent
    "minmax(3.75rem, 0.84fr)", // result
    ...(shape.scores === "tally" ? [score, score] : ["minmax(6rem, 1.12fr)"]),
    "minmax(0, 3.21fr)", // venue
    ...(shape.detail ? ["minmax(5rem, 1.16fr)"] : []),
  ].join(" ");
}

/**
 * A row is a grid, not a table row: at table width (of the list itself, not
 * of the window) it is the mockup's seven columns; narrower, the same cells
 * restack into three lines (date and result, then the opponent, then the
 * venue) rather than scrolling the result off the side of a phone.
 */
const ROW =
  "grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-x-2 px-4 @sm:gap-x-3 @3xl:grid-cols-[var(--results-columns)] @3xl:gap-x-0 @3xl:px-6";
/** Back to plain source order once the columns exist. */
const AUTO = "@3xl:col-auto @3xl:row-auto";

/**
 * Colours known for teams by name, from every game the view has loaded
 * (see `colorsByName`). Provided once by ResultsView; the last resort after
 * a side's own colour and its followed team's.
 */
export const NamedColorsContext = createContext<Record<string, string>>({});

const TOKEN =
  "flex-none font-condensed text-base font-semibold uppercase tracking-[0.08em] text-muted";

/** The opponent as a row names it: "vs", its block, its name, a followed marker. */
export function Opponent({
  word,
  side,
  followed,
  bold = false,
  tokenClass = "w-[1.375rem]",
}: {
  word: string;
  side: GameSide;
  /** The followed team this opponent is, if it is one. */
  followed: Team | undefined;
  bold?: boolean;
  tokenClass?: string;
}) {
  const sideColor = useSideColor(side);
  const named = useContext(NamedColorsContext);
  return (
    <span
      className={
        "flex min-w-0 items-center gap-2.5 text-ink " +
        (bold ? "font-bold" : "font-semibold")
      }
    >
      <span className={`${TOKEN} ${tokenClass}`}>{word}</span>
      <TeamBlock
        name={side.name}
        abbreviation={side.abbreviation}
        color={sideColor ?? followed?.color ?? named[side.name]}
      />
      <span className="min-w-0 [overflow-wrap:anywhere]">
        <MarkedName
          name={side.name}
          mark={
            followed !== undefined && (
              <FollowMark className="relative -top-px ml-2.5 align-middle" />
            )
          }
        />
      </span>
    </span>
  );
}

function Score({
  value,
  mine,
  wide,
}: {
  value: number | null;
  mine: boolean;
  /** Room for three figures, so stacked rows line up down the list. */
  wide: boolean;
}) {
  return (
    <span
      className={
        "block text-center font-condensed text-[2.125rem] leading-none tabular-nums " +
        (wide ? "min-w-[3.25rem] " : "min-w-9 ") +
        (mine ? "font-bold text-ink" : "font-medium text-muted")
      }
    >
      {value ?? "–"}
    </span>
  );
}

/** How a fight ended, where another sport has its two score columns. */
function Method({ game }: { game: Game }) {
  const fight = fightResultOf(game);
  if (fight === null) return null;
  const method = fightMethodLabel(fight.method);
  const round = fightRoundLabel(fight);
  return (
    <span
      title={fightResultSummary(fight)}
      className="flex flex-col font-condensed text-base font-semibold uppercase leading-tight tracking-[0.08em] text-muted"
    >
      {method !== "" && <span className="text-ink">{method}</span>}
      {round !== null && <span>{round}</span>}
    </span>
  );
}

function Row({
  game,
  team,
  teams,
  shape,
  leagueNames,
  canOpen,
  onOpen,
}: {
  game: Game;
  team: Team;
  teams: readonly Team[];
  shape: TableShape;
  leagueNames: Record<string, string>;
  canOpen: boolean;
  onOpen: (game: Game) => void;
}) {
  const view = perspective(game, team);
  const outcome = resultFor(game, team.id);
  // The round or card, and the competition when it is not the team's own
  // league (a cup tie, an international): the row's only way to say so.
  const context = [
    game.league_id !== team.league_id
      ? (leagueNames[game.league_id] ?? game.league_id)
      : null,
    game.series,
  ].filter((part): part is string => part !== null && part !== "");
  const hasVenue = (game.venue ?? "") !== "" || context.length > 0;

  return (
    <div
      role="row"
      // At table width every row is the mockup's 60px under its rule, with
      // or without the 44px link that otherwise sets the height.
      className={`sd-stagger-item border-t border-hair py-3 first:border-t-0 @3xl:min-h-[calc(3.75rem+1px)] @3xl:py-2 @3xl:first:border-t ${ROW}`}
    >
      <span
        role="rowheader"
        className={`col-start-1 row-start-1 whitespace-nowrap font-condensed text-[1.375rem] font-bold uppercase leading-none tracking-[0.02em] tabular-nums @3xl:pr-3 ${AUTO}`}
      >
        <time dateTime={localDateKey(game.start_time)}>
          {/* A column of its own at table width; stacked, just a word. */}
          <span className="mr-1.5 text-base font-semibold tracking-[0.08em] text-muted @3xl:mr-0 @3xl:inline-block @3xl:w-[2.875rem]">
            {weekdayShort(game.start_time)}{" "}
          </span>
          {formatShortDate(game.start_time)}
        </time>
      </span>

      <span role="cell" className={`col-span-full row-start-2 mt-2.5 min-w-0 @3xl:mt-0 @3xl:px-3 ${AUTO}`}>
        {view !== null ? (
          <Opponent
            word={venueWord(game.sport, view.home)}
            side={view.theirs}
            followed={followedOpponent(view.theirs, teams, team)}
          />
        ) : (
          // The team is on neither side by id or by name: say what the row
          // is rather than guess a point of view.
          <span className="font-semibold">
            {game.away.name} at {game.home.name}
          </span>
        )}
      </span>

      <span role="cell" className={`col-start-2 row-start-1 flex justify-center ${AUTO}`}>
        {outcome !== null && <OutcomeSquare result={outcome} size={30} />}
      </span>

      {shape.scores === "tally" ? (
        <>
          <span role="cell" className={`col-start-3 row-start-1 ${AUTO}`}>
            <Score
              value={(view?.mine ?? game.away).score}
              mine={view !== null}
              wide={shape.wideScores}
            />
          </span>
          <span role="cell" className={`col-start-4 row-start-1 ${AUTO}`}>
            <Score
              value={(view?.theirs ?? game.home).score}
              mine={false}
              wide={shape.wideScores}
            />
          </span>
        </>
      ) : (
        <span role="cell" className={`col-span-2 col-start-3 row-start-1 ${AUTO}`}>
          <Method game={game} />
        </span>
      )}

      <span
        role="cell"
        className={
          // A venue can be one long word ("RheinEnergieStadion"): it breaks
          // inside its column rather than run under the box score link.
          `row-start-3 min-w-0 text-ink-2 [overflow-wrap:anywhere] @3xl:mt-0 @3xl:pl-7 @3xl:pr-3 ${AUTO} ` +
          (hasVenue ? "mt-1.5 " : "") +
          // Stacked, the venue has the line to itself unless the row has
          // a box score, which sits under the two score columns.
          (shape.detail && canOpen ? "col-span-2 col-start-1" : "col-span-full")
        }
      >
        {game.venue}
        {context.length > 0 && (
          <span className="block text-sm text-muted">{context.join(" · ")}</span>
        )}
      </span>

      {shape.detail && (
        <span
          role="cell"
          className={
            `col-span-2 col-start-3 row-start-3 whitespace-nowrap text-right @3xl:mt-0 @3xl:pl-3 ${AUTO} ` +
            (canOpen ? "mt-1.5" : "")
          }
        >
          {canOpen && (
            <button
              type="button"
              aria-haspopup="dialog"
              aria-label={detailLabel(game, team)}
              onClick={() => onOpen(game)}
              // A 44px target at every width. Stacked, it reaches into the
              // row's own padding rather than adding a tall third line.
              className="bs-link -my-3 inline-flex min-h-11 cursor-pointer items-center py-0 @3xl:my-0"
            >
              Box score
            </button>
          )}
        </span>
      )}
    </div>
  );
}

/**
 * One month of a team's results as a table: when, against whom, how it
 * went, the score with the team's own figure first, where, and the way in
 * to the box score. Table semantics are spelled out with roles, because the
 * rows are grids that restack on a narrow sheet.
 */
export default function ResultsTable({
  group,
  team,
  teams,
  shape,
  note,
  leagueNames,
  openable,
  onOpen,
}: {
  group: MonthGroup;
  team: Team;
  teams: readonly Team[];
  shape: TableShape;
  note: string;
  leagueNames: Record<string, string>;
  /** Ids of the games the API can open a box score for. */
  openable: ReadonlySet<string>;
  onOpen: (game: Game) => void;
}) {
  const tally = sportWords(group.games[0]?.sport).tally;
  const scoreWord = tally === null ? "score" : tally.noun.toLowerCase();
  const head = "py-2 font-semibold";
  return (
    <section aria-label={`${group.label} results`}>
      <SectionHead title={group.label} note={note} />
      <div className="bs-sheet @container">
        <div
          role="table"
          aria-label={`${team.name} results, ${group.label}`}
          style={{ "--results-columns": columnsOf(shape) } as CSSProperties}
        >
          {/* Column heads exist at every width for a screen reader; they are
              only drawn where there are columns under them. */}
          <div role="rowgroup" className="sr-only @3xl:not-sr-only">
            <div role="row" className={`bs-label ${ROW}`}>
              <span role="columnheader" className={`${head} @3xl:pr-3`}>
                Date
              </span>
              <span role="columnheader" className={`${head} @3xl:px-3`}>
                Opponent
              </span>
              <span role="columnheader" className={`${head} text-center`}>
                Result
              </span>
              {shape.scores === "tally" ? (
                <>
                  <span
                    role="columnheader"
                    aria-label={`${team.name} ${scoreWord}`}
                    className="flex justify-center py-2"
                  >
                    <TeamBlock
                      name={team.name}
                      abbreviation={team.abbreviation}
                      color={team.color}
                    />
                  </span>
                  <span
                    role="columnheader"
                    aria-label={`Opponent ${scoreWord}`}
                    className={`${head} text-center`}
                  >
                    Opp
                  </span>
                </>
              ) : (
                <span role="columnheader" className={head}>
                  Method
                </span>
              )}
              <span role="columnheader" className={`${head} @3xl:pl-7 @3xl:pr-3`}>
                Venue
              </span>
              {shape.detail && (
                <span role="columnheader" className={`${head} text-right @3xl:pl-3`}>
                  Detail
                </span>
              )}
            </div>
          </div>
          <div role="rowgroup">
            {group.games.map((game) => (
              <Row
                key={game.id}
                game={game}
                team={team}
                teams={teams}
                shape={shape}
                leagueNames={leagueNames}
                canOpen={openable.has(game.id)}
                onOpen={onOpen}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
