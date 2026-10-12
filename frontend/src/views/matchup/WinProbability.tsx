import { SectionHead, SpotChip, teamFill } from "../../components/bs";
import { useTheme } from "../../components/ThemeProvider";
import { THEME_SURFACES } from "../../lib/theme";
import type { Game } from "../../types";
import {
  barFills,
  barLabel,
  favoredLine,
  linesNote,
  probabilityNote,
  type KeyValue,
  type ProbabilityRow,
} from "./model";
import { Fact, SideBlock, useIdentity } from "./parts";

/**
 * Two shares as one bar, away first, as wide as the shares themselves. In
 * the two teams' colours when those can be told apart on this sheet;
 * otherwise without colour, the favoured share solid and the other an
 * outline, the way a win and a loss are told apart everywhere else.
 */
function Bar({ game, row }: { game: Game; row: ProbabilityRow }) {
  const { theme } = useTheme();
  const away = useIdentity(game.away, game.league_id).color;
  const home = useIdentity(game.home, game.league_id).color;
  // The fills the blocks beside the bar are drawn in, so the two agree.
  const minRatio = theme === "contrast" ? 7 : 4.5;
  const fills = barFills(
    teamFill(away, minRatio)?.fill,
    teamFill(home, minRatio)?.fill,
    THEME_SURFACES[theme].sheet,
  );
  const segment = (share: number, fill: string | undefined, solid: boolean) => (
    <span
      className={
        "block h-full min-w-0 " +
        (fill !== undefined ? "" : solid ? "bg-ink" : "border-2 border-ink")
      }
      style={{ flex: `${share} 1 0%`, backgroundColor: fill }}
    />
  );
  return (
    <div
      role="img"
      aria-label={barLabel(game, row)}
      className="mt-2.5 flex h-3.5 gap-0.5"
    >
      {segment(row.shares.away, fills?.away, row.favored !== "home")}
      {segment(row.shares.home, fills?.home, row.favored !== "away")}
    </div>
  );
}

function Row({ game, row }: { game: Game; row: ProbabilityRow }) {
  // A step down in a phone-wide sheet, so both figures and their blocks
  // keep one line even at "100%".
  const figure = (side: "away" | "home") =>
    "text-[2.375rem] leading-[0.8] @[20rem]:text-[2.75rem] @sm:text-[3.5rem] " +
    (row.favored === null || row.favored === side
      ? "font-bold text-ink"
      : "font-semibold text-muted");
  return (
    <div className="flex flex-1 flex-wrap items-center gap-x-6 gap-y-3 border-t border-hair px-4 pb-5 pt-[1.125rem] first:border-t-0 sm:px-6">
      <div className="min-w-0 flex-[1_1_15rem]">
        <h3 className="m-0 font-condensed text-[1.375rem] font-bold uppercase leading-none tracking-[0.02em]">
          {row.title}
        </h3>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-2">
          {row.live && <SpotChip size="sm">Live</SpotChip>}
          {favoredLine(game, row)}
        </div>
      </div>
      <div className="min-w-0 flex-[999_1_22.5rem]">
        {/* The bar under them says the same figures aloud, once. */}
        <div
          aria-hidden="true"
          className="flex flex-wrap items-center justify-between gap-x-2 gap-y-4 font-condensed @sm:gap-x-4"
        >
          <span className="flex items-center gap-2 @sm:gap-2.5">
            <SideBlock side={game.away} leagueId={game.league_id} />
            <span className={figure("away")}>{row.percent.away}%</span>
          </span>
          <span className="ml-auto flex items-center gap-2 @sm:gap-2.5">
            <span className={figure("home")}>{row.percent.home}%</span>
            <SideBlock side={game.home} leagueId={game.league_id} />
          </span>
        </div>
        <Bar game={game} row={row} />
      </div>
    </div>
  );
}

/**
 * Each side's chance to win: the model's projection from before the start,
 * and while the game is on, where it stands now. Under them the betting
 * lines, which the mockup has no place for and the view always showed.
 * With lines and no projection (most soccer) the section is the lines
 * alone, and is named for them.
 */
export default function WinProbability({
  game,
  rows,
  lines,
}: {
  game: Game;
  rows: readonly ProbabilityRow[];
  lines: readonly KeyValue[];
}) {
  if (rows.length === 0 && lines.length === 0) return null;
  const title = rows.length > 0 ? "Win probability" : "Betting lines";
  return (
    <section
      aria-label={title}
      className={
        "flex min-w-0 flex-[999_1_35rem] flex-col" +
        // A strip of lines alone keeps its own height: stretched to the
        // radar beside it, it would be four tall empty cells.
        (rows.length === 0 ? " self-start" : "")
      }
    >
      <SectionHead
        title={title}
        note={
          rows.length > 0
            ? probabilityNote(rows, game.sport)
            : linesNote(game)
        }
      />
      <div className="bs-sheet @container flex flex-auto flex-col tabular-nums">
        {rows.map((row) => (
          <Row key={row.key} game={game} row={row} />
        ))}
        {lines.length > 0 && (
          <dl
            aria-label="Betting lines"
            className={
              "m-0 flex flex-wrap gap-px bg-hair" +
              (rows.length > 0 ? " border-t-2 border-ink" : "")
            }
          >
            {lines.map((line) => (
              <Fact
                key={line.label}
                label={line.label}
                className="flex-[1_1_12.5rem]"
              >
                {line.value}
              </Fact>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}
