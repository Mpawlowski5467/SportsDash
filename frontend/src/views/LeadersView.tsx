import type { ReactNode } from "react";

import {
  FollowMark,
  Loading,
  MarkedName,
  Notice,
  SectionHead,
} from "../components/bs";
import PlayerAvatar from "../components/PlayerAvatar";
import StaleBanner from "../components/StaleBanner";
import { useLeaders, useScorers } from "../hooks";
import { errorText } from "../lib/errorText";
import type { League } from "../types";
import { BoardTeamBlock, useTeamCodes } from "./league/LeadersBlock";
import { formatStat, leadersBoard } from "./league/model";
import { useTeamLookup } from "./league/parts";

/**
 * The Leaders sub-view: the league's whole board, one player a row. Real
 * tournament scorers (the Golden Boot) when the league has finished
 * matches with goals to count; otherwise the stat board, which is
 * league-wide where the provider ranks the league and is otherwise built
 * from the followed teams' rosters.
 *
 * A player you follow is the ink row; a player on a team you follow wears
 * the marker alone.
 */
export default function LeadersView({
  league,
  seasonYear,
  onCurrentSeason,
}: {
  league: League;
  /** The archive season picked, or undefined for the season in progress. */
  seasonYear: number | undefined;
  onCurrentSeason: () => void;
}) {
  const scorersQuery = useScorers(league.id);
  const leadersQuery = useLeaders(league.id);
  const lookup = useTeamLookup(league);
  const codeFor = useTeamCodes();

  // Neither request takes a season: the board is the current one only.
  if (seasonYear !== undefined) {
    return (
      <Notice
        title="Leaders"
        headline="Current season only"
        action={{ label: "Current season", onClick: onCurrentSeason }}
      >
        Leaders are kept for the season in progress. The {seasonYear} season
        has its standings, but no leaders board.
      </Notice>
    );
  }

  const data = leadersQuery.data;
  // Which board this is depends on the scorers request, so nothing is drawn
  // until it has answered: otherwise the stat board would show for a moment
  // and then be replaced by the Golden Boot.
  const board = scorersQuery.isPending
    ? null
    : leadersBoard(scorersQuery.data, data);
  // A failed refetch keeps the cached board on screen behind a stale banner
  // rather than blanking it; only an empty cache errors out.
  const stale =
    board?.kind !== "scorers" && leadersQuery.isError && data !== undefined;

  let body: ReactNode;
  if (board !== null) {
    body = (
      <section aria-label={board.title}>
        <SectionHead
          title={board.title}
          note={
            board.kind === "scorers"
              ? board.source
              : `${board.source} · ranked by ${board.unit}`
          }
        />
        <div className="bs-sheet tabular-nums">
          <div
            aria-hidden="true"
            className="bs-label flex items-center gap-3 border-b border-ink px-4 py-2"
          >
            <span className="w-8 flex-none">#</span>
            <span className="min-w-0 flex-1">Player</span>
            {/* A roster-built board can mix stats; each row then names
                its own beside the value, and the column has no one name. */}
            <span className="flex-none font-bold text-ink">
              {board.kind === "scorers" ||
              board.rows.every((row) => row.unit === board.unit)
                ? board.unit
                : "Stat"}
            </span>
          </div>
          <ol className="m-0 list-none p-0">
            {board.rows.map((row, index) => {
              const marked = row.followed || row.highlighted;
              return (
                // The row wraps: where the name would be squeezed under its
                // floor (a phone, a long stat line), the value drops to a
                // line of its own, at the right, and the name keeps the row.
                <li
                  key={row.key}
                  className={
                    "sd-stagger-item flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-hair px-4 py-2.5 first:border-t-0" +
                    (row.followed ? " bs-inverse" : "")
                  }
                >
                  <span
                    className={
                      "w-8 flex-none font-condensed text-[1.375rem] font-bold leading-none " +
                      (index < 3 || row.followed ? "text-ink" : "text-muted")
                    }
                  >
                    {row.rank}
                  </span>
                  <BoardTeamBlock row={row} lookup={lookup} codeFor={codeFor} />
                  {board.photos && (
                    <PlayerAvatar name={row.name} photoUrl={row.photoUrl} />
                  )}
                  <div className="min-w-[5.5rem] flex-1">
                    <p className="m-0 text-base font-bold leading-5 wrap-anywhere">
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
                    </p>
                    <p className="m-0 text-sm text-muted wrap-anywhere">
                      {[row.team, row.position].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                  <div className="ml-auto max-w-full flex-none text-right">
                    <span className="font-condensed text-3xl font-bold leading-none">
                      {formatStat(row.value)}
                    </span>{" "}
                    <span className="bs-label">{row.unit}</span>
                    {row.detail !== null && (
                      <p className="m-0 text-sm text-muted wrap-anywhere">
                        {row.detail}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      </section>
    );
  } else if (leadersQuery.isError && !data) {
    body = (
      <Notice
        title="Leaders"
        alert
        action={{ label: "Retry", onClick: () => void leadersQuery.refetch() }}
      >
        Couldn&rsquo;t load the leaders
        {errorText(leadersQuery.error)}
      </Notice>
    );
  } else if (!data || scorersQuery.isPending) {
    body = <Loading label="Loading leaders" rows={8} />;
  } else {
    body = (
      <Notice title="Leaders" headline="No player stats yet">
        No player stats yet for {data.league_name}. Leaders are built from
        your followed teams&rsquo; rosters — follow more teams in this league
        to broaden the board. (Rosters refresh on the daily update.)
      </Notice>
    );
  }

  return (
    <>
      {stale && (
        <StaleBanner
          message="Connection lost — showing the last loaded leaders."
          onRetry={() => void leadersQuery.refetch()}
        />
      )}
      {body}
    </>
  );
}
