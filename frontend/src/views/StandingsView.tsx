import { useCallback, type ReactNode } from "react";

import { FollowMark, Loading, Notice, Segmented, Tag } from "../components/bs";
import StaleBanner from "../components/StaleBanner";
import { useTheme } from "../components/ThemeProvider";
import { useStandings, useTeams } from "../hooks";
import { errorText } from "../lib/errorText";
import { formatDateTime } from "../lib/time";
import type { League, StandingRow } from "../types";
import LeadersBlock from "./league/LeadersBlock";
import {
  expectsOneTable,
  hasTeamTable,
  isRankingTable,
  layoutStandings,
} from "./league/model";
import { Controls, useTeamLookup } from "./league/parts";
import StandingsTable from "./league/StandingsTable";
import TeamPanel from "./league/TeamPanel";

/**
 * The row of tables: equal columns, as many as fit at 352px or more each
 * (three across at 1440px, as mocked). A grid, so a table left alone on the
 * last line is as wide as the ones above it and their columns line up.
 */
const TABLES =
  "grid grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] items-stretch gap-7";
/** A table in that row: capped at 560px where a column is wider than that. */
const TABLE = "max-w-[35rem]";
/**
 * The one table of a single-table league, with the leaders and the team
 * panel beside it: it grows to its cap first, so its names have the room
 * before the wider column takes the rest.
 */
const TABLE_BESIDE = "max-w-[35rem] flex-[2_1_25.875rem]";

/**
 * The Standings sub-view, and the page the mockup draws: the league's
 * tables (one conference at a time where there are divisions), the top of
 * its leaders board, and the followed team's panel.
 *
 * A past season shows its tables only: the leaders board and the team's
 * fixtures and results exist for the current season alone.
 */
export default function StandingsView({
  league,
  seasonYear,
  onCurrentSeason,
  group,
  onGroup,
  teamId,
  onTeam,
}: {
  league: League;
  /** The archive season picked, or undefined for the live table. */
  seasonYear: number | undefined;
  onCurrentSeason: () => void;
  /** The conference picked, when the league has them. */
  group: string | null;
  onGroup: (group: string) => void;
  /** The followed team picked for the panel, when there are several. */
  teamId: string | null;
  onTeam: (teamId: string) => void;
}) {
  const teamsQuery = useTeams();
  const standingsQuery = useStandings(league.id, seasonYear);
  // The panel's record and place are the live table's, whatever is shown.
  const liveStandings = useStandings(league.id).data;
  const lookup = useTeamLookup(league);
  const { favoriteTeam } = useTheme();

  const teams = (teamsQuery.data?.teams ?? []).filter(
    (team) => team.league_id === league.id,
  );
  // A past season's rows carry no followed-team id, so a row is also
  // matched to a followed team by name.
  const followedId = useCallback(
    (row: StandingRow): string | null =>
      row.team_id ??
      (teamsQuery.data?.teams ?? []).find(
        (team) => team.league_id === league.id && team.name === row.team_name,
      )?.id ??
      null,
    [teamsQuery.data, league.id],
  );

  const standings = standingsQuery.data;
  const live = seasonYear === undefined;
  const ranking = isRankingTable(league.sport);

  const shownTeam =
    teams.find((team) => team.id === teamId) ??
    teams.find((team) => team.id === favoriteTeam?.id) ??
    teams[0];

  // The two blocks beside (or under) the tables. `sized` gives them the
  // mocked widths in a row; stacked in a column they take its width.
  const leaders = (sized: boolean) =>
    live ? (
      <LeadersBlock
        league={league}
        lookup={lookup}
        className={sized ? "flex-[999_1_35rem]" : undefined}
      />
    ) : null;
  const panel = (sized: boolean) =>
    live && shownTeam !== undefined && hasTeamTable(league.sport) ? (
      <TeamPanel
        league={league}
        teams={teams}
        teamId={shownTeam.id}
        onTeam={onTeam}
        standings={liveStandings}
        lookup={lookup}
        className={sized ? "flex-[1_1_27.5rem]" : undefined}
      />
    ) : null;
  /** The mocked second row: the leaders wide, the team's panel beside them. */
  const below = (
    <div className="flex flex-wrap items-stretch gap-7 empty:hidden">
      {leaders(true)}
      {panel(true)}
    </div>
  );
  /**
   * One table (a single-table league): the leaders and the team's panel
   * stand beside it instead of under it, so a twenty-row table does not
   * leave the rest of the row empty.
   */
  const beside = (table: ReactNode) => (
    <div className="flex flex-wrap items-start gap-7">
      {table}
      {/* The basis is the least the two blocks read well at (the team
          panel's mocked 440px); a wider one would drop the column under
          the table on a tablet and leave the table beside nothing. */}
      <div className="flex min-w-0 flex-[3_1_27.5rem] flex-col gap-7 empty:hidden">
        {leaders(false)}
        {panel(false)}
      </div>
    </div>
  );

  let controls: ReactNode = null;
  let body: ReactNode;

  // Only replace the tables when there is nothing cached to show; a failed
  // refetch keeps the last table up behind a stale banner.
  if (standingsQuery.isError && !standings) {
    body = (
      <>
        {seasonYear !== undefined ? (
          <Notice
            title="Standings"
            headline={`No ${seasonYear} table`}
            action={{ label: "Current season", onClick: onCurrentSeason }}
          >
            The {seasonYear} season isn&rsquo;t available from the provider
            for this league.
          </Notice>
        ) : (
          <Notice
            title="Standings"
            alert
            action={{
              label: "Retry",
              onClick: () => void standingsQuery.refetch(),
            }}
          >
            Couldn&rsquo;t load the standings
            {errorText(standingsQuery.error)}
          </Notice>
        )}
        {below}
      </>
    );
  } else if (!standings) {
    // The placeholder stands where the table most likely will, so the
    // blocks around it stay put when the table arrives.
    body = expectsOneTable(league.sport) ? (
      beside(
        <Loading label="Loading standings" rows={10} className={TABLE_BESIDE} />,
      )
    ) : (
      <>
        <div className={TABLES}>
          <Loading label="Loading standings" rows={6} className={TABLE} />
        </div>
        {below}
      </>
    );
  } else if (standings.rows.length === 0) {
    body = (
      <>
        <Notice
          title="Standings"
          headline={
            standings.sport === "mma" ? "No rankings" : "No standings yet"
          }
        >
          {standings.sport === "mma"
            ? "Rankings not available for this league."
            : `No standings yet for ${standings.league_name}. Standings refresh once a day, so they will appear after the next daily refresh.`}
        </Notice>
        {below}
      </>
    );
  } else {
    const layout = layoutStandings(
      standings,
      group,
      (row) => followedId(row) !== null,
    );
    const anyFollowed = standings.rows.some((row) => followedId(row) !== null);
    const tagged = !ranking && layout.tables.some((table) => table.rows.length > 1);

    controls = (
      <>
        {layout.groups.length > 1 && layout.active !== null && (
          <Segmented
            label="Conference"
            value={layout.active}
            onChange={onGroup}
            options={layout.groups.map((name) => ({ value: name, label: name }))}
            className="max-sm:w-full"
          />
        )}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm font-medium text-ink-2 sm:ml-auto">
          {tagged && (
            <span className="inline-flex items-center gap-2">
              <Tag>Leader</Tag>
              {layout.leaderWord}
            </span>
          )}
          {anyFollowed && (
            <span className="inline-flex items-center gap-2">
              <FollowMark boxed label={null} />
              {ranking ? "Player you follow" : "Team you follow"}
            </span>
          )}
          {standings.is_stale && (
            <Tag title="The source hasn't updated recently — this may be out of date.">
              Possibly out of date
            </Tag>
          )}
          {standings.fetched_at !== null && (
            <span>Updated {formatDateTime(standings.fetched_at)}</span>
          )}
        </div>
      </>
    );

    const table = (
      data: (typeof layout.tables)[number],
      className: string = TABLE,
    ) => (
      <StandingsTable
        key={data.key}
        table={data}
        sport={standings.sport}
        leagueId={standings.league_id}
        followedId={followedId}
        lookup={lookup}
        className={className}
      />
    );

    body =
      layout.tables.length === 1 ? (
        beside(table(layout.tables[0], TABLE_BESIDE))
      ) : (
        <>
          <div className={TABLES}>
            {layout.tables.map((data) => table(data))}
          </div>
          {below}
        </>
      );
  }

  return (
    <>
      <Controls>{controls}</Controls>
      {standingsQuery.isError && standings && (
        <StaleBanner
          message="Connection lost — showing the last loaded table."
          onRetry={() => void standingsQuery.refetch()}
        />
      )}
      {body}
    </>
  );
}
