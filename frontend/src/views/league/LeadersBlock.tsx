import type { CSSProperties } from "react";

import {
  FollowMark,
  Loading,
  MarkedName,
  Notice,
  SectionHead,
  TeamBlock,
} from "../../components/bs";
import { useLeaders, useScorers, useTeams } from "../../hooks";
import { errorText } from "../../lib/errorText";
import type { League } from "../../types";
import {
  boardNote,
  formatStat,
  leadersBoard,
  shareOfLeader,
  statColumnRem,
  topOfBoard,
  type BoardRow,
  type TeamLookup,
} from "./model";

/** How many of the board's rows the Standings page shows. */
const TOP = 10;

/**
 * Wide: rank, player, value and bar side by side, as mocked. Narrow (a
 * phone, or a column too tight for all four): the bar drops under the
 * player so the number is never pushed off screen. The switch is on the
 * sheet's own width, not the window's.
 */
const ROW =
  "grid grid-cols-[2.75rem_minmax(0,1fr)_auto] items-center @[33rem]:grid-cols-[3.125rem_15rem_var(--stat-w)_minmax(0,1fr)]";

/** The team a board row plays for, as a block: code and colour by lookup. */
export function BoardTeamBlock({
  row,
  lookup,
  codeFor,
}: {
  row: BoardRow;
  lookup: TeamLookup;
  /** A followed team's code by its id (a followed row names the team in full). */
  codeFor: Record<string, string>;
}) {
  const identity = lookup(row.team);
  return (
    <TeamBlock
      name={identity?.name ?? row.team}
      abbreviation={
        (row.teamId !== null ? codeFor[row.teamId] : undefined) ??
        identity?.abbreviation ??
        // A team that is not followed arrives as its code already.
        (row.team.length <= 4 ? row.team : null)
      }
      color={row.teamColor ?? identity?.color}
    />
  );
}

/** Followed teams' codes by id, from `/teams`. */
export function useTeamCodes(): Record<string, string> {
  const teams = useTeams().data?.teams;
  const codes: Record<string, string> = {};
  for (const team of teams ?? []) codes[team.id] = team.abbreviation;
  return codes;
}

/**
 * The top of the league's leaders board on the Standings page: the Golden
 * Boot where finished matches have goals to count, else the stat board.
 * Each bar is the row's value as a share of the leader's; the first three
 * rows are drawn in the spot colour. Renders nothing for a league with no
 * board (the Leaders sub-view says why).
 */
export default function LeadersBlock({
  league,
  lookup,
  className,
}: {
  league: League;
  lookup: TeamLookup;
  className?: string;
}) {
  const scorersQuery = useScorers(league.id);
  const leadersQuery = useLeaders(league.id);
  const codeFor = useTeamCodes();
  // Which board this is depends on the scorers request, so nothing is
  // drawn until it has answered: otherwise the stat board would show for a
  // moment and then be replaced by the Golden Boot.
  const board = scorersQuery.isPending
    ? null
    : leadersBoard(scorersQuery.data, leadersQuery.data);
  const wrapper = "flex min-w-0 flex-col" + (className ? ` ${className}` : "");

  if (board === null) {
    if (scorersQuery.isPending || leadersQuery.isPending) {
      return <Loading label="Loading leaders" rows={6} className={wrapper} />;
    }
    if (leadersQuery.isError && leadersQuery.data === undefined) {
      return (
        <Notice
          title="Leaders"
          alert
          dense
          action={{ label: "Retry", onClick: () => void leadersQuery.refetch() }}
          className={wrapper}
        >
          Couldn&rsquo;t load the leaders
          {errorText(leadersQuery.error)}
        </Notice>
      );
    }
    return null;
  }

  const rows = topOfBoard(board, TOP);
  const top = rows[0]?.value ?? 0;
  const values = rows.map((row) => formatStat(row.value));
  return (
    <section aria-label={board.title} className={wrapper}>
      <SectionHead
        title={board.title}
        note={boardNote(board, rows.length)}
      />
      <div
        role="table"
        aria-label={board.title}
        className="bs-sheet @container flex-auto tabular-nums"
        // The value column is as wide as the longest value shown, plus the
        // mockup's 20px to the bar.
        style={
          { "--stat-w": `${statColumnRem(values) + 1.25}rem` } as CSSProperties
        }
      >
        <div role="rowgroup">
          <div role="row" className={`${ROW} bs-label border-b border-ink`}>
            <span role="columnheader" className="py-2 pl-4">
              #
            </span>
            <span role="columnheader" className="py-2">
              Player
            </span>
            <span
              role="columnheader"
              className="py-2 pr-4 text-right font-bold text-ink @[33rem]:pr-5"
            >
              {board.unit}
            </span>
            <span
              role="columnheader"
              className="hidden py-2 pr-4 @[33rem]:block"
            >
              Share of the leader
            </span>
          </div>
        </div>
        <div role="rowgroup">
          {rows.map((row, index) => {
            const share = shareOfLeader(row.value, top);
            const marked = row.followed || row.highlighted;
            return (
              <div
                key={row.key}
                role="row"
                className={`${ROW} border-t border-hair first:border-t-0`}
              >
                <span
                  role="cell"
                  className={
                    "pl-4 font-condensed text-[1.375rem] font-bold leading-none " +
                    (index < 3 ? "text-ink" : "text-muted")
                  }
                >
                  {row.rank}
                </span>
                <span
                  role="rowheader"
                  className="flex min-h-[3.625rem] min-w-0 items-center gap-3 py-2 pr-3"
                >
                  <BoardTeamBlock row={row} lookup={lookup} codeFor={codeFor} />
                  <span className="flex min-w-0 flex-col">
                    <span className="text-base font-bold leading-5 wrap-anywhere">
                      <MarkedName
                        name={row.name}
                        mark={
                          marked && (
                            <FollowMark
                              className="ml-2"
                              label={
                                row.followed
                                  ? "Followed player"
                                  : "On a team you follow"
                              }
                            />
                          )
                        }
                      />
                    </span>
                    <span className="text-sm leading-[1.1875rem] text-muted wrap-anywhere">
                      {[row.team, row.position].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </span>
                <span
                  role="cell"
                  className="pr-4 text-right font-condensed text-[2.5rem] font-bold leading-none @[33rem]:pr-5"
                >
                  {values[index]}
                </span>
                <span
                  role="cell"
                  className="col-span-2 col-start-2 pb-3 pr-4 @[33rem]:col-span-1 @[33rem]:col-start-auto @[33rem]:pb-0"
                >
                  <span
                    role="img"
                    aria-label={`${share} percent of the leader`}
                    className={
                      "block h-3 @[33rem]:h-5 " +
                      (index < 3
                        ? "bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]"
                        : "bg-ink")
                    }
                    style={{ width: `${share}%` }}
                  />
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
