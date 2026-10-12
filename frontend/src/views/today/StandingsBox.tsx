import {
  FollowMark,
  FormStrip,
  MarkedName,
  ScrollRegion,
  SectionHead,
} from "../../components/bs";
import { useNavigateTab } from "../../components/NavigateContext";
import { useResults, useStandings } from "../../hooks";
import type { StandingRow } from "../../types";
import {
  followedTables,
  outcomeForTeam,
  standingColumns,
  type Outcome,
  type StandingColumn,
  type StandingsSlice,
} from "./model";

/** How many recent results the strip under a table shows. */
const FORM_GAMES = 5;
/**
 * Today stays on screen all day, and a table changes when a game ends:
 * refresh on a beat rather than keep the numbers from page load.
 */
const REFRESH_MS = 5 * 60_000;

/** A followed team's last few results, newest first, under its table. */
function RecentForm({ row }: { row: StandingRow }) {
  const results =
    useResults(row.team_id ?? undefined, FORM_GAMES, {
      refetchInterval: REFRESH_MS,
    }).data ?? [];
  const outcomes = results
    .map((game) => (row.team_id === null ? null : outcomeForTeam(game, row.team_id)))
    .filter((outcome): outcome is Outcome => outcome !== null);
  if (outcomes.length === 0) return null;
  const label = `${row.team_name}, last ${outcomes.length === 1 ? "game" : outcomes.length}`;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t-2 border-ink px-4 py-3">
      <span className="bs-note">{label}</span>
      <FormStrip label={`${label}, most recent first`} results={outcomes} />
    </div>
  );
}

function Table({
  slice,
  columns,
  leagueId,
}: {
  slice: StandingsSlice;
  columns: StandingColumn[];
  leagueId: string;
}) {
  const cut = slice.rows.length < slice.total;
  return (
    <section aria-label={`${slice.title} standings`}>
      <SectionHead
        title={slice.title}
        note={cut ? `${slice.rows.length} of ${slice.total} places` : undefined}
        aside={
          <FullStandingsLink
            leagueId={leagueId}
            // Only a division sits inside a conference worth naming.
            group={
              slice.rows[0]?.subgroup ? (slice.rows[0].group ?? undefined) : undefined
            }
          />
        }
      />
      <div className="bs-sheet">
        {/* Scrolls inside the sheet on the narrowest screens rather than
            pushing the page sideways. */}
        <ScrollRegion label={`${slice.title} standings`}>
        <table className="w-full border-collapse text-right tabular-nums">
          <thead>
            <tr className="bs-label">
              <th scope="col" className="py-2 pl-4 pr-2 text-left font-semibold">
                Team
              </th>
              {columns.map((column, index) => (
                <th
                  key={column.key}
                  scope="col"
                  className={
                    "py-2 font-semibold normal-case " +
                    (index === columns.length - 1 ? "pl-2 pr-4" : "px-2")
                  }
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.rows.map((row, index) => {
              const followed = row.team_id !== null;
              // No hairline between a followed row and its neighbours: the
              // ink block is its own edge.
              const previous = index > 0 ? slice.rows[index - 1] : null;
              const afterFollowed = previous !== null && previous.team_id !== null;
              // Places were left out between the row above and this one (a
              // followed team outside the window): a heavy rule says so.
              const afterGap = previous !== null && row.rank - previous.rank > 1;
              return (
                <tr
                  key={`${row.rank}-${row.team_name}`}
                  className={
                    (followed
                      ? "bs-inverse"
                      : afterFollowed && !afterGap
                        ? ""
                        : "border-t border-hair") +
                    (afterGap ? " border-t-2 border-ink" : "")
                  }
                >
                  <th
                    scope="row"
                    className={
                      "py-2.5 pl-4 pr-2 text-left " +
                      (followed ? "font-bold" : "font-medium")
                    }
                  >
                    <span className="flex items-baseline">
                      <span
                        className={
                          "w-[1.625rem] flex-none font-condensed text-lg font-bold leading-[1.4] " +
                          (followed ? "text-ink" : "text-muted")
                        }
                      >
                        {row.rank}
                      </span>
                      <span className="min-w-0">
                        <MarkedName
                          name={row.team_name}
                          mark={followed && <FollowMark className="ml-2" />}
                        />
                      </span>
                    </span>
                  </th>
                  {columns.map((column, columnIndex) => (
                    <td
                      key={column.key}
                      className={
                        "py-2.5 " +
                        (columnIndex === columns.length - 1 ? "pl-2 pr-4" : "px-2") +
                        (column.ranked ? " font-bold" : "")
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
        {slice.rows
          .filter((row) => row.team_id !== null)
          .map((row) => (
            <RecentForm key={row.team_id} row={row} />
          ))}
      </div>
    </section>
  );
}

function FullStandingsLink({
  leagueId,
  group,
}: {
  leagueId: string;
  /** The conference this box's division sits in, so League opens on it. */
  group?: string;
}) {
  const navigate = useNavigateTab();
  return (
    <button
      type="button"
      onClick={() => navigate("league", { leagueId, group })}
      className="bs-link cursor-pointer"
    >
      Full standings
    </button>
  );
}

/**
 * The tables in one league that hold a followed team: a division where the
 * league has them, a window of the single table where it does not. Renders
 * nothing for a league with no team table, or none of your teams in it.
 */
export default function StandingsBox({
  leagueId,
  limit,
}: {
  leagueId: string;
  /** At most this many tables from the league. */
  limit: number;
}) {
  const standings = useStandings(leagueId, undefined, {
    refetchInterval: REFRESH_MS,
  }).data;
  if (standings === undefined) return null;
  const columns = standingColumns(standings.sport);
  if (columns === null) return null;
  return (
    <>
      {followedTables(standings)
        .slice(0, limit)
        .map((slice) => (
          <Table
            key={slice.title}
            slice={slice}
            columns={columns}
            leagueId={leagueId}
          />
        ))}
    </>
  );
}
