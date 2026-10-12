import type { CSSProperties } from "react";

import {
  FollowMark,
  MarkedName,
  ScrollRegion,
  SectionHead,
  Tag,
  TeamBlock,
} from "../../components/bs";
import { useOpenNation, useOpenTeam } from "../../components/TeamDetailPanel";
import type { Sport, StandingRow } from "../../types";
import {
  isRankingTable,
  leadNote,
  leagueColumns,
  type StandingTable as TableData,
  type TeamLookup,
} from "./model";

/**
 * The rank column in rem by the digits it holds: the mockup's 34px (14px of
 * margin and one digit with room to spare), wider for a twenty-team table
 * and for a ranking that runs into the hundreds.
 */
function rankRem(rows: readonly StandingRow[]): number {
  const last = rows.reduce((max, row) => Math.max(max, row.rank), 0);
  if (last > 99) return 3.125;
  return last > 9 ? 2.5 : 2.125;
}

/** The sheet's 14px inner margin, on the last column. */
const EDGE_REM = 0.875;
/** The least a team cell needs: the block and a name that can still wrap. */
const TEAM_MIN_REM = 8.75;
/**
 * What the rank column and each figure column give up, in rem, in a sheet
 * narrower than the table (soccer's five columns on a 360px phone). 5px:
 * the tightest pair of figures is then still 7px apart. Taking the room
 * from the team cell instead breaks names mid-word.
 */
const TRIM_REM = 0.3125;

/**
 * One table of the standings: a division, a group, or the whole league.
 * Columns follow the sport; the one the table is ranked by is set in bold.
 * A followed team's row is the ink block with the spot marker, and first
 * place wears the Leader tag.
 *
 * A row's team is a button that opens the team's page (the nation's, for a
 * team that is not followed), as the standings rows always have been.
 * Rankings list people and are not clickable.
 */
export default function StandingsTable({
  table,
  sport,
  leagueId,
  followedId,
  lookup,
  className,
}: {
  table: TableData;
  sport: Sport;
  leagueId: string;
  /** The followed team a row is, by id on a live table and by name on a past one. */
  followedId: (row: StandingRow) => string | null;
  lookup: TeamLookup;
  className?: string;
}) {
  const openTeam = useOpenTeam();
  const openNation = useOpenNation();
  const columns = leagueColumns(sport);
  const ranking = isRankingTable(sport);
  const isFollowed = (row: StandingRow) => followedId(row) !== null;
  const note = leadNote(table.rows, sport, isFollowed);

  const rankWidth = rankRem(table.rows);
  // What the table needs with every column at its full width.
  const fullRem =
    rankWidth +
    columns.reduce((sum, column) => sum + column.width, 0) +
    EDGE_REM +
    TEAM_MIN_REM;
  // The sheet is a size container, so `100cqw` is the room the table has.
  // A table that fits is left as it is. One that does not has its rank and
  // figure columns trimmed, all or nothing (the clamp is a switch: any
  // shortfall times 999 is past the cap), and the team cell gets whatever
  // that frees. A sheet narrower still scrolls, as it did.
  const trimmed = columns.length + 1;
  const sizing = {
    "--col-trim": `clamp(0rem, (${fullRem}rem - 100cqw) * 999, ${TRIM_REM}rem)`,
    minWidth: `calc(${fullRem}rem - ${trimmed} * var(--col-trim))`,
  } as CSSProperties;
  const colWidth = (rem: number) => `calc(${rem}rem - var(--col-trim))`;

  return (
    <section
      aria-label={`${table.title} standings`}
      className={"flex min-w-0 flex-col" + (className ? ` ${className}` : "")}
    >
      <SectionHead
        title={table.title}
        note={note ?? undefined}
      />
      <ScrollRegion
        label={`${table.title} standings`}
        className="bs-sheet @container flex-auto"
      >
        <table
          className="w-full table-fixed border-collapse text-right tabular-nums"
          style={sizing}
        >
          <colgroup>
            <col style={{ width: colWidth(rankWidth) }} />
            <col />
            {columns.map((column, index) => (
              <col
                key={column.key}
                style={{
                  width: colWidth(
                    column.width +
                      (index === columns.length - 1 ? EDGE_REM : 0),
                  ),
                }}
              />
            ))}
          </colgroup>
          <thead>
            <tr className="bs-label border-b border-ink">
              <th scope="col" className="py-2 pl-3.5 text-left font-semibold">
                #
              </th>
              <th scope="col" className="py-2 text-left font-semibold">
                {ranking ? "Player" : "Team"}
              </th>
              {columns.map((column, index) => (
                <th
                  key={column.key}
                  scope="col"
                  className={
                    "py-2 text-right " +
                    (column.ranked ? "font-bold text-ink" : "font-semibold") +
                    (index === columns.length - 1 ? " pr-3.5" : "")
                  }
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => {
              const teamId = followedId(row);
              const followed = teamId !== null;
              const leader = index === 0 && !ranking && table.rows.length > 1;
              // No hairline between a followed row and a plain neighbour:
              // the ink block is its own edge. Two followed rows in a row
              // keep one between them, drawn in the block's own rule tone.
              const afterFollowed =
                index > 0 && isFollowed(table.rows[index - 1]);
              const identity = lookup(row.team_name, row.abbreviation);
              const name = (
                <>
                  {!ranking && (
                    <TeamBlock
                      name={row.team_name}
                      abbreviation={row.abbreviation ?? identity?.abbreviation}
                      color={row.color ?? identity?.color}
                    />
                  )}
                  {/* The tag sits beside the name where there is room and
                      drops under it where there is not, rather than
                      squeezing the name into a column of single words. */}
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
                    <span className="min-w-0 wrap-anywhere">
                      <MarkedName
                        name={row.team_name}
                        mark={
                          followed && (
                            <FollowMark
                              className="ml-2"
                              label={ranking ? "Followed player" : undefined}
                            />
                          )
                        }
                      />
                    </span>
                    {leader && <Tag>Leader</Tag>}
                  </span>
                </>
              );
              return (
                <tr
                  key={`${row.rank}-${row.team_name}`}
                  className={
                    "sd-stagger-item" +
                    (followed ? " bs-inverse" : "") +
                    (index > 0 && followed === afterFollowed
                      ? " border-t border-hair"
                      : "")
                  }
                >
                  <td
                    className={
                      "pl-3.5 text-left font-condensed text-lg font-bold " +
                      (leader || followed ? "text-ink" : "text-muted")
                    }
                  >
                    {row.rank}
                  </td>
                  <th
                    scope="row"
                    className={
                      "p-0 text-left " +
                      (followed
                        ? "font-bold"
                        : leader
                          ? "font-semibold"
                          : "font-medium")
                    }
                  >
                    {ranking ? (
                      <span className="flex min-h-11 items-center gap-2.5 py-1.5 pr-2">
                        {name}
                      </span>
                    ) : (
                      // The button reaches 8px into the rank column's spare
                      // room, so its inset focus ring clears the team block.
                      <button
                        type="button"
                        aria-haspopup="dialog"
                        title={`View ${row.team_name}`}
                        onClick={() =>
                          teamId !== null
                            ? openTeam(teamId)
                            : openNation(leagueId, row.team_name)
                        }
                        className="bs-focus-inset -ml-2 flex min-h-11 w-[calc(100%+0.5rem)] min-w-0 cursor-pointer items-center gap-2.5 py-1.5 pl-2 pr-2 text-left hover:text-spot-text"
                      >
                        {name}
                      </button>
                    )}
                  </th>
                  {columns.map((column, columnIndex) => (
                    <td
                      key={column.key}
                      className={
                        "p-0" +
                        (column.ranked ? " font-bold" : "") +
                        (columnIndex === columns.length - 1 ? " pr-3.5" : "")
                      }
                    >
                      {column.value(row)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollRegion>
    </section>
  );
}
