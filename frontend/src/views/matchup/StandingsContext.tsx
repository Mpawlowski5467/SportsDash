import { FollowMark, MarkedName, SectionHead } from "../../components/bs";
import { useNavigateTab } from "../../components/NavigateContext";
import { isIndividualSport } from "../../lib/labels";
import type { Game, Standings } from "../../types";
import {
  standingsNote,
  standingsPanels,
  type SideFigure,
  type StandingsPanel,
} from "./model";
import { SideBlock, SpareRoom } from "./parts";

/** A big figure over its small-caps label; the label comes first for a reader. */
function Figure({ figure, ruled }: { figure: SideFigure; ruled: boolean }) {
  return (
    // No `min-w-0`: a figure is never squeezed under its own width. Two
    // share a line while both fit (halves, as mocked), and the second
    // drops under the first when one is long ("11,830").
    <div
      className={
        "flex flex-[1_1_6.5rem] flex-col-reverse justify-end gap-1" +
        (ruled ? " border-l border-hair pl-5" : " pr-3")
      }
    >
      <dt className="bs-label">{figure.label}</dt>
      <dd className="m-0 whitespace-nowrap font-condensed text-[4rem] font-bold uppercase leading-[0.85]">
        {figure.value}
      </dd>
    </div>
  );
}

function Panel({
  game,
  panel,
  ruled,
}: {
  game: Game;
  panel: StandingsPanel;
  /** A hairline above: only between two panels of the same kind. */
  ruled: boolean;
}) {
  const side = game[panel.side];
  return (
    // At its own height: a panel is never stretched with its figures
    // centred in the space (see `SpareRoom`).
    <div
      className={
        "flex flex-col gap-3.5 px-5 pb-4 pt-[1.125rem]" +
        (panel.followed ? " bs-inverse" : "") +
        (ruled ? " border-t border-hair" : "")
      }
    >
      <h3
        className={
          "m-0 flex items-center gap-2.5 font-condensed text-[1.75rem] uppercase leading-none " +
          (panel.followed ? "font-bold" : "font-semibold text-ink-2")
        }
      >
        <SideBlock side={side} leagueId={game.league_id} />
        <span className="min-w-0 [overflow-wrap:anywhere]">
          <MarkedName
            name={side.name}
            mark={
              panel.followed && (
                <FollowMark
                  size="lg"
                  label={
                    isIndividualSport(game.sport)
                      ? "Followed player"
                      : "Followed team"
                  }
                  className="ml-2.5 align-middle"
                />
              )
            }
          />
        </span>
      </h3>
      <dl className="m-0 flex flex-wrap gap-y-2.5">
        <Figure figure={panel.place} ruled={false} />
        {panel.second !== null && <Figure figure={panel.second} ruled />}
      </dl>
      {panel.record !== null && (
        <dl className="m-0 flex items-baseline justify-between gap-3 border-t border-hair pt-2">
          <dt className="bs-label">Record</dt>
          <dd className="m-0 font-semibold">{panel.record}</dd>
        </dl>
      )}
    </div>
  );
}

/**
 * Where each side stands: its place and table, the figure its sport ranks
 * by, and its record. The followed side is the inverted block and comes
 * first. A side with no row in the table (a cup tie, a fighter) has no
 * panel, and with no panel at all the section is not drawn.
 */
export default function StandingsContext({
  game,
  standings,
}: {
  game: Game;
  standings: Standings | undefined;
}) {
  const navigate = useNavigateTab();
  const panels = standingsPanels(game, standings);
  if (panels.length === 0 || standings === undefined) return null;
  return (
    <section
      aria-label="Standings context"
      className="flex min-w-0 flex-[1_1_21.25rem] flex-col"
    >
      <SectionHead
        title="Standings context"
        note={standingsNote(standings.sport)}
      />
      <div className="bs-sheet flex flex-auto flex-col tabular-nums">
        {panels.map((panel, index) => (
          <Panel
            key={panel.side}
            game={game}
            panel={panel}
            ruled={index > 0 && panels[index - 1].followed === panel.followed}
          />
        ))}
        <div className="flex items-center justify-end border-t-2 border-ink px-5 py-1">
          <button
            type="button"
            onClick={() =>
              navigate("league", {
                leagueId: game.league_id,
                group: panels[0].group ?? undefined,
              })
            }
            className="bs-link my-0 cursor-pointer"
          >
            Full standings
          </button>
        </div>
        <SpareRoom />
      </div>
    </section>
  );
}
