import type { ReactNode } from "react";

import {
  FollowMark,
  FormStrip,
  OutcomeSquare,
  SectionHead,
  Segmented,
  SpotChip,
  TeamBlock,
} from "../../components/bs";
import { useResults, useSchedule } from "../../hooks";
import { localDayOffset } from "../../lib/time";
import { useNow } from "../../lib/useNow";
import type { Game, League, StandingRow, Standings, Team } from "../../types";
import { outcomeForTeam, recordText, tableName } from "../today/model";
import {
  fixtureDay,
  formLabel,
  heroStats,
  inTable,
  outcomeSummary,
  recordRem,
  scoreFor,
  teamOutcomes,
  upcomingFixtures,
  venueSide,
  venueSummary,
  type TeamLookup,
} from "./model";

/** How many fixtures and results the panel lists, as mocked. */
const NEXT_UP = 6;
const RECENT = 5;
/** The schedule window, the same one the team page asks for (and so shares). */
const AHEAD_DAYS = 45;
/**
 * How often the fixtures and the results are asked for again, on screen or
 * not: League stays mounted once visited, and nothing else refetches them,
 * so without a beat "Live now" and Next up would stand as first loaded
 * until midnight. Both lists share it, so they cannot tell two stories
 * about one game for longer than this. Both requests are plain reads of
 * what the server has already stored.
 */
const REFRESH_MS = 60_000;

/** The tinted band over a list: its name left, a summary right. */
function SubHead({ title, summary }: { title: string; summary?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t-2 border-ink bg-tint px-4 py-2">
      <h3 className="m-0 font-condensed text-base font-bold uppercase leading-5 tracking-[0.08em]">
        {title}
      </h3>
      {summary !== undefined && summary !== "" && (
        <span className="text-sm leading-5 text-ink-2">{summary}</span>
      )}
    </div>
  );
}

/** A line in place of a list: loading, empty, or a failed request. */
function ListNote({
  children,
  onRetry,
  status = false,
}: {
  children: ReactNode;
  onRetry?: () => void;
  status?: boolean;
}) {
  return (
    <p
      role={onRetry !== undefined ? "alert" : status ? "status" : undefined}
      className="m-0 flex min-h-[2.3125rem] flex-wrap items-center justify-between gap-x-3 border-t border-hair px-4 py-2 text-sm text-muted"
    >
      {children}
      {onRetry !== undefined && (
        <button
          type="button"
          onClick={onRetry}
          // The word alone is 34px wide; the target is 44px either way.
          className="bs-link min-w-11 cursor-pointer justify-center"
        >
          Retry
        </button>
      )}
    </p>
  );
}

/**
 * One fixture or result: when, home or away, the opponent's block, and
 * what there is to say about it. The opponent is named by its block alone,
 * as mocked; the block announces the full name.
 */
function FixtureRow({
  game,
  teamId,
  when,
  lookup,
  children,
}: {
  game: Game;
  teamId: string;
  when: string;
  lookup: TeamLookup;
  children: ReactNode;
}) {
  const home = venueSide(game, teamId) === "home";
  const opponent = home ? game.away : game.home;
  const identity = lookup(opponent.name, opponent.abbreviation);
  return (
    <li className="flex min-h-[2.3125rem] items-center gap-2.5 border-t border-hair px-4">
      <span className="w-[4.625rem] flex-none font-condensed text-lg font-bold uppercase leading-none tracking-[0.02em]">
        {when}
      </span>
      <span className="w-5 flex-none text-ink-2">
        <span aria-hidden="true">{home ? "vs" : "@"}</span>
        <span className="sr-only">{home ? "at home to" : "away at"}</span>
      </span>
      <span title={opponent.name} className="inline-flex flex-none">
        <TeamBlock
          name={opponent.name}
          abbreviation={opponent.abbreviation ?? identity?.abbreviation}
          color={opponent.color ?? identity?.color}
          labelled
        />
      </span>
      <span className="ml-auto flex items-center gap-2.5">{children}</span>
    </li>
  );
}

/**
 * The followed team in the selected league: its record and place, its last
 * few results, what is next and what just happened. With several followed
 * teams in the league a switcher picks which one; the page draws no panel
 * for a league followed as a whole, or for a sport with no team table.
 */
export default function TeamPanel({
  league,
  teams,
  teamId,
  onTeam,
  standings,
  lookup,
  className,
}: {
  league: League;
  /** The followed teams in this league; at least one. */
  teams: readonly Team[];
  /** The team shown; one of `teams`. */
  teamId: string;
  onTeam: (teamId: string) => void;
  /** The live table, once it has loaded: the record and place come from it. */
  standings: Standings | undefined;
  lookup: TeamLookup;
  className?: string;
}) {
  const team = teams.find((entry) => entry.id === teamId) ?? teams[0];
  const now = useNow(60_000);
  const scheduleQuery = useSchedule(
    localDayOffset(-1),
    localDayOffset(AHEAD_DAYS),
    team.id,
    { refetchInterval: REFRESH_MS },
  );
  const resultsQuery = useResults(team.id, RECENT, {
    refetchInterval: REFRESH_MS,
  });

  const row: StandingRow | undefined = standings?.rows.find(
    (entry) => entry.team_id === team.id,
  );
  const table =
    row === undefined || standings === undefined
      ? []
      : standings.rows
          .filter(
            (entry) =>
              entry.group === row.group && entry.subgroup === row.subgroup,
          )
          .sort((a, b) => a.rank - b.rank);
  const tableTitle =
    row === undefined || standings === undefined
      ? league.name
      : tableName(row, standings);
  const record = row === undefined ? null : recordText(row, league.sport);
  const stats =
    row === undefined ? [] : heroStats(row, table, league.sport, tableTitle);

  const fixtures = upcomingFixtures(scheduleQuery.data ?? [], team.id, NEXT_UP);
  const results = (resultsQuery.data ?? []).slice(0, RECENT);
  const outcomes = teamOutcomes(results, team.id);

  return (
    <section
      aria-label={team.name}
      className={"flex min-w-0 flex-col" + (className ? ` ${className}` : "")}
    >
      <SectionHead
        title={team.name}
        note={
          // The marker and the words on one centre line.
          <span className="flex items-center gap-2">
            <FollowMark boxed label={null} />
            Your team {inTable(tableTitle)}
          </span>
        }
      />
      <div className="bs-sheet @container flex-auto tabular-nums">
        {teams.length > 1 && (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b-2 border-ink bg-tint px-4 py-3">
            <span className="bs-note">Your teams in {league.name}</span>
            <Segmented
              label={`Your teams in ${league.name}`}
              value={team.id}
              onChange={onTeam}
              options={teams.map((entry) => ({
                value: entry.id,
                label: entry.abbreviation || entry.name,
                ariaLabel: entry.name,
              }))}
              className="[&>*]:px-3.5"
            />
          </div>
        )}

        <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3.5 gap-y-3 p-4 @[26rem]:grid-cols-[auto_auto_minmax(0,1fr)]">
          <TeamBlock
            name={team.name}
            abbreviation={team.abbreviation}
            color={team.color}
            size={56}
          />
          {record !== null ? (
            <div
              className="font-condensed font-bold leading-[4rem]"
              style={{ fontSize: `min(${recordRem(record)}rem, 21cqw)` }}
            >
              <span className="sr-only">Record </span>
              {record}
            </div>
          ) : (
            <p className="m-0 text-ink-2">
              {standings === undefined
                ? "Record not loaded yet."
                : `Not in the ${league.name} table yet.`}
            </p>
          )}
          {stats.length > 0 && (
            <div className="col-span-2 flex flex-wrap gap-x-6 gap-y-1 border-t border-hair pt-3 font-condensed uppercase @[26rem]:col-span-1 @[26rem]:flex-col @[26rem]:flex-nowrap @[26rem]:border-l-2 @[26rem]:border-t-0 @[26rem]:border-ink @[26rem]:pl-3.5 @[26rem]:pt-0">
              {stats.map((stat) => (
                <div
                  key={stat.label}
                  className="flex flex-wrap items-baseline gap-x-1.5"
                >
                  <span className="text-3xl font-bold leading-[1.875rem]">
                    {stat.figure}
                  </span>
                  <span className="text-sm font-semibold leading-[1.4] tracking-[0.08em] text-ink-2">
                    {stat.label}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {outcomes.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t-2 border-ink px-4 py-3">
            <span className="bs-note">{formLabel(outcomes.length)}</span>
            <FormStrip
              label={`${team.name}, ${formLabel(outcomes.length).toLowerCase()}`}
              results={outcomes}
            />
          </div>
        )}

        <SubHead
          title="Next up"
          summary={venueSummary(fixtures, team.id)}
        />
        {fixtures.length > 0 ? (
          <ul className="m-0 list-none p-0">
            {fixtures.map((game) => (
              <FixtureRow
                key={game.id}
                game={game}
                teamId={team.id}
                when={fixtureDay(game.start_time, now)}
                lookup={lookup}
              >
                {game.phase === "in_progress" ? (
                  <SpotChip>Live now</SpotChip>
                ) : (
                  <span className="text-sm text-muted">
                    {venueSide(game, team.id) === "home" ? "Home" : "Away"}
                  </span>
                )}
              </FixtureRow>
            ))}
          </ul>
        ) : scheduleQuery.isPending ? (
          <ListNote status>Loading fixtures…</ListNote>
        ) : scheduleQuery.isError ? (
          <ListNote onRetry={() => void scheduleQuery.refetch()}>
            Couldn&rsquo;t load the fixtures.
          </ListNote>
        ) : (
          <ListNote>No fixtures in the next {AHEAD_DAYS} days.</ListNote>
        )}

        <SubHead title="Recent" summary={outcomeSummary(outcomes)} />
        {results.length > 0 ? (
          <ul className="m-0 list-none p-0">
            {results.map((game) => {
              const outcome = outcomeForTeam(game, team.id);
              return (
                <FixtureRow
                  key={game.id}
                  game={game}
                  teamId={team.id}
                  when={fixtureDay(game.start_time, now)}
                  lookup={lookup}
                >
                  <span className="font-condensed text-2xl font-bold leading-none">
                    {scoreFor(game, team.id) ?? ""}
                  </span>
                  {outcome !== null && <OutcomeSquare result={outcome} size={24} />}
                </FixtureRow>
              );
            })}
          </ul>
        ) : resultsQuery.isPending ? (
          <ListNote status>Loading results…</ListNote>
        ) : resultsQuery.isError ? (
          <ListNote onRetry={() => void resultsQuery.refetch()}>
            Couldn&rsquo;t load the results.
          </ListNote>
        ) : (
          <ListNote>No results yet.</ListNote>
        )}
      </div>
    </section>
  );
}
