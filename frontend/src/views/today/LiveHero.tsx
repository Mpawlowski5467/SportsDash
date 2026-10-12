import {
  Button,
  FollowMark,
  MarkedName,
  ScoreFigure,
  ScrollRegion,
  SectionHead,
  TeamBlock,
} from "../../components/bs";
import { useIsActiveTab } from "../../components/NavigateContext";
import { useGameDetail, useStandings } from "../../hooks";
import { shortSide, sideLabel } from "../../lib/labels";
import type { Game, League, Standings } from "../../types";
import {
  isFollowedSide,
  isIndividualSport,
  leadingSide,
  lineScoreOf,
  liveStatus,
  liveWinProbability,
  ordinal,
  recordText,
  showsScore,
  standingRowFor,
  tableName,
  type LineScore,
  type SideKey,
} from "./model";
import { SplitBar, boxScoreLabel, useSideColor } from "./parts";

/**
 * One side of the live game: block, name, where it stands, and the score.
 * The side in front is set in ink and bold; the side behind steps back.
 */
function TeamRow({
  game,
  sideKey,
  standings,
}: {
  game: Game;
  sideKey: SideKey;
  standings: Standings | undefined;
}) {
  const side = game[sideKey];
  const color = useSideColor(side);
  const scored = showsScore(game);
  const leader = leadingSide(game);
  // In front, or level: full weight. Only the side behind steps back.
  const strong = !scored || leader === null || leader === sideKey;
  const individual = isIndividualSport(game.sport);
  const followed = isFollowedSide(game, side);
  const wideScore = (game.home.score ?? 0) > 99 || (game.away.score ?? 0) > 99;

  const row = individual ? undefined : standingRowFor(standings, side);
  const facts = [
    individual ? null : sideKey === "home" ? "Home" : "Away",
    row === undefined ? null : recordText(row, game.sport),
    row === undefined || standings === undefined
      ? null
      : `${ordinal(row.rank)} in ${tableName(row, standings)}`,
  ].filter((part): part is string => part !== null);

  return (
    <div
      className={
        "flex items-center gap-3 px-4 sm:gap-4 sm:px-6 " +
        (sideKey === "away"
          ? "pb-[1.375rem] pt-6"
          : "border-t border-hair pb-6 pt-[1.375rem]")
      }
    >
      <TeamBlock
        name={side.name}
        abbreviation={side.abbreviation}
        color={color}
        size={64}
        className="max-sm:h-11 max-sm:w-11 max-sm:text-lg"
      />
      <div className="min-w-0 flex-1">
        <div
          className={
            "flex items-center gap-2.5 font-condensed text-[1.625rem] uppercase leading-none sm:text-[2.5rem] " +
            (strong ? "font-bold text-ink" : "font-semibold text-ink-2")
          }
        >
          {/* The marker is tied to the end of the name, so it stays with
              the name when the name wraps instead of drifting to the score. */}
          <span className="min-w-0">
            <MarkedName
              name={side.name}
              mark={
                followed && (
                  <FollowMark size="lg" className="ml-2.5 align-middle" />
                )
              }
            />
          </span>
        </div>
        {facts.length > 0 && (
          <div className="mt-1 text-sm text-muted">{facts.join(" · ")}</div>
        )}
      </div>
      {scored && (
        <div
          className={
            "flex-none font-condensed leading-[0.8] tabular-nums " +
            // Three figures (basketball) step down a size, or a long team
            // name beside them has nowhere to go.
            (wideScore ? "text-[3rem] sm:text-[5rem] " : "text-[4rem] sm:text-[6rem] ") +
            (strong ? "font-bold text-ink" : "font-semibold text-muted")
          }
        >
          {/* Pulses when the score moves, and only then. */}
          <ScoreFigure of={game.id} value={side.score} />
        </div>
      )}
    </div>
  );
}

/** Period by period, away over home, with the running total at the end. */
function LineScoreTable({ game, line }: { game: Game; line: LineScore }) {
  const leader = leadingSide(game);
  const rows: { key: SideKey; total: number | null }[] = [
    { key: "away", total: line.awayTotal },
    { key: "home", total: line.homeTotal },
  ];
  return (
    <ScrollRegion label="Line score" className="@container border-t-2 border-ink">
      {/* As wide as its columns need and no wider: nine innings, or two
          halves. A fixed width sized for baseball would push a soccer
          score off a phone. */}
      <table
        className="w-full border-collapse text-center tabular-nums"
        style={{ minWidth: `${6 + line.columns.length * 2.25 + 3.5}rem` }}
      >
        <caption className="sr-only">
          Line score, {game.away.name} at {game.home.name}
        </caption>
        <thead>
          <tr className="bs-label">
            <th scope="col" className="px-4 py-2 text-left font-semibold sm:px-6">
              {game.sport === "baseball" ? "Inning" : "Period"}
            </th>
            {line.columns.map((column) => (
              <th
                key={column.label}
                scope="col"
                aria-current={column.current ? "true" : undefined}
                className={
                  "w-9 px-1 py-2 " +
                  (column.current
                    ? "bg-spot font-bold text-on-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]"
                    : "font-semibold")
                }
              >
                {column.label}
              </th>
            ))}
            <th
              scope="col"
              className="w-14 border-l border-rule py-2 pl-0 pr-3 font-bold text-ink"
            >
              {line.totalLabel}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, total }) => (
            <tr key={key} className="border-t border-hair">
              <th
                scope="row"
                className={
                  "px-4 py-[0.8125rem] text-left sm:px-6 " +
                  (leader === null || leader === key
                    ? "font-bold text-ink"
                    : "font-medium text-ink-2")
                }
              >
                {/* The code where the table is tight, the name where there
                    is room; the name is always what is read out. */}
                <span aria-hidden="true" className="@xl:hidden">
                  {shortSide(game, game[key])}
                </span>
                <span className="sr-only @xl:not-sr-only">{game[key].name}</span>
              </th>
              {line.columns.map((column) => (
                <td
                  key={column.label}
                  className={column.current ? "bg-ground" : undefined}
                >
                  {column[key] ?? ""}
                </td>
              ))}
              <td className="border-l border-rule pr-3 font-bold">{total ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRegion>
  );
}

/**
 * The live game, large: both sides and the score, the line score when the
 * provider has one, and a panel with where the game stands and where to
 * watch it. Fetches its own box score (polled while the game is live) and
 * its league's table for the record lines.
 */
export default function LiveHero({
  game,
  league,
  onOpen,
}: {
  game: Game;
  league: League | undefined;
  onOpen: (game: Game) => void;
}) {
  // Views stay mounted while hidden, and the box score costs the server
  // three uncached provider calls each time: poll it only while Today is
  // the tab on screen. Parked, it keeps the last line score it had.
  const showing = useIsActiveTab("today");
  const detail = useGameDetail(game.id, { enabled: showing }).data;
  const standings = useStandings(
    isIndividualSport(game.sport) ? undefined : game.league_id,
  ).data;

  const line = lineScoreOf(game, detail?.summary);
  const chance = liveWinProbability(detail?.summary);
  const status = liveStatus(game);
  const note = [league?.name ?? null, game.series]
    .filter((part): part is string => part !== null && part !== "")
    .join(" · ");
  const facts: [string, string][] = [];
  if (game.venue) facts.push(["Venue", game.venue]);
  if (game.broadcasts.length > 0) facts.push(["Watch", game.broadcasts.join(" · ")]);

  return (
    <section aria-label="Live now">
      <SectionHead title="Live now" note={note === "" ? undefined : note} />
      <div className="bs-sheet flex flex-wrap items-stretch">
        <div className="flex min-w-0 flex-[999_1_30rem] flex-col">
          <TeamRow game={game} sideKey="away" standings={standings} />
          <TeamRow game={game} sideKey="home" standings={standings} />
          {line !== null && <LineScoreTable game={game} line={line} />}
        </div>

        <div className="flex min-w-0 flex-[1_1_20rem] flex-col gap-[1.125rem] bg-tint px-4 py-5 sm:px-6">
          <div>
            <div className="font-condensed text-[1.75rem] font-bold uppercase leading-none">
              {status.headline}
            </div>
            {status.detail !== null && (
              <div className="mt-1 text-sm text-ink-2 tabular-nums">
                {status.detail}
              </div>
            )}
          </div>

          {chance !== null && (
            <div>
              <div className="flex items-baseline justify-between gap-3">
                <span className="bs-label">Win probability</span>
                <span className="whitespace-nowrap font-condensed text-[2.5rem] font-bold uppercase leading-[0.9] tabular-nums">
                  {sideLabel(chance.home >= chance.away ? game.home : game.away)}{" "}
                  {Math.round(Math.max(chance.home, chance.away))}%
                </span>
              </div>
              <SplitBar
                game={game}
                away={chance.away}
                home={chance.home}
                className="mt-2 h-3.5"
              />
            </div>
          )}

          {facts.length > 0 && (
            <table className="w-full border-collapse text-sm">
              <tbody>
                {facts.map(([key, value]) => (
                  <tr key={key} className="border-t border-rule">
                    <th scope="row" className="bs-label w-[5.25rem] py-[0.4375rem] text-left align-top">
                      {key}
                    </th>
                    <td className="py-[0.4375rem] font-medium">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="flex flex-wrap gap-2.5">
            <Button
              variant="solid"
              aria-haspopup="dialog"
              aria-label={boxScoreLabel(game)}
              onClick={() => onOpen(game)}
              className="px-3.5"
            >
              Box score
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
