import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../api";
import { Button, Loading, Notice, TeamBlock } from "../components/bs";
import GameDetailModal from "../components/GameDetailModal";
import { useManageTeams } from "../components/ManageTeamsContext";
import { useNavigationRequest } from "../components/NavigateContext";
import Select, { type SelectOption } from "../components/Select";
import StaleBanner from "../components/StaleBanner";
import {
  useResults,
  useSchedule,
  useSeasonResults,
  useStandings,
  useTeams,
  useToday,
} from "../hooks";
import { errorText } from "../lib/errorText";
import { countOf, isIndividualSport, sportWords } from "../lib/labels";
import {
  CURRENT_SEASON,
  seasonOptions,
  seasonToken,
  supportsArchives,
} from "../lib/seasons";
import { localKeyFromDate, longDay } from "../lib/time";
import type { Game, League, Team } from "../types";
import NextUp from "./results/NextUp";
import ResultsTable, {
  NamedColorsContext,
  type TableShape,
} from "./results/ResultsTable";
import SeasonTiles from "./results/SeasonTiles";
import {
  FORM_GAMES,
  NEXT_UP_DAYS,
  RESULTS_LIMIT,
  colorsByName,
  currentSeason,
  deckSeason,
  hasSeasonRecord,
  hasUnwatchedLive,
  holdsNewestResult,
  isSeasonInPlay,
  mergeSeason,
  monthGroups,
  nextUp,
  seasonKey,
  seasonRow,
  summarize,
  teamSwitcherKind,
  unlistedFinals,
} from "./results/model";
import { dayKeyOffset } from "./today/model";
import { TeamColorsContext } from "./today/parts";

/**
 * How long the stored results are held back for the season's list. The
 * stored results are the app's own and answer at once; the list is a live
 * call to the provider, which takes a second when it is well and minutes
 * when it is not. Past this the stored results go up alone, and the list
 * is merged in when it lands.
 */
const SEASON_WAIT_MS = 2_000;
/**
 * How often the list of the season being played is asked for again after
 * it failed. Slow: the backend keeps no record of an empty answer, so every
 * try is a call to the provider.
 */
const SEASON_RETRY_MS = 10 * 60_000;
/** How often the schedule is read again while it alone holds a live game. */
const LIVE_RECHECK_MS = 60_000;

/** The page's name, and what is on it, beside the controls that choose it. */
function PageHead({ deck, children }: { deck?: string; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
        <h1 className="bs-title">Results</h1>
        {deck !== undefined && <p className="bs-deck m-0">{deck}</p>}
      </div>
      {children}
    </header>
  );
}

/** A control under its small-caps label. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="bs-label leading-none">{label}</span>
      {children}
    </div>
  );
}

/** The shapes of the page while a payload is on its way. */
function PageLoading({ label }: { label: string }) {
  return (
    <Loading label={label} className="flex flex-col gap-7">
      <div className="flex flex-wrap gap-0.5 border-t-[3px] border-rule">
        {["15rem", "21.875rem", "10rem", "16.25rem", "16.25rem"].map((basis, index) => (
          <div key={index} className="h-40 bg-tint" style={{ flex: `1 1 ${basis}` }} />
        ))}
      </div>
      <div className="h-80 border-t-[3px] border-rule bg-tint" />
    </Loading>
  );
}

/** "Athletics'" and "Chelsea's". */
const possessive = (name: string): string =>
  name.endsWith("s") ? `${name}’` : `${name}’s`;

/**
 * Everything about one followed team. A component of its own so that every
 * request it makes is for a team that exists: the view above it deals with
 * there being no teams at all.
 */
function TeamResults({
  teams,
  leagues,
}: {
  teams: readonly Team[];
  leagues: readonly League[];
}) {
  const [selected, setSelected] = useState<string | undefined>(undefined);
  // Season picker (ESPN team sports only). "Current season" is the season
  // being played; a year fetches that season on demand.
  const [seasonSel, setSeasonSel] = useState<string>(CURRENT_SEASON);
  const [openGame, setOpenGame] = useState<Game | null>(null);

  // Another view can send the user here for a team ("Results" beside a
  // followed team). Applied once per request, so a later choice made here
  // is never overridden by the same request again.
  const request = useNavigationRequest("results");
  const requestNonce = request?.nonce;
  const requestedTeam = request?.teamId;
  const appliedNonce = useRef<number | null>(null);
  useEffect(() => {
    if (requestNonce === undefined || appliedNonce.current === requestNonce) {
      return;
    }
    appliedNonce.current = requestNonce;
    if (
      requestedTeam !== undefined &&
      teams.some((entry) => entry.id === requestedTeam)
    ) {
      setSelected(requestedTeam);
      setSeasonSel(CURRENT_SEASON);
    }
  }, [requestNonce, requestedTeam, teams]);

  // Ignore a selection that no longer exists: re-running setup replaces the
  // followed teams, which would leave `selected` pointing at a 404.
  const team = teams.find((entry) => entry.id === selected) ?? teams[0];
  const league = leagues.find((entry) => entry.id === team.league_id);
  const archives = supportsArchives(league);
  const seasonYear =
    archives && seasonSel !== CURRENT_SEASON ? Number(seasonSel) : undefined;

  // Two sources of finished games. The stored results are current but only
  // hold what finished while the app was running; the provider's list of a
  // season is complete but can be hours old (ESPN team sports only).
  const resultsQuery = useResults(team.id, RESULTS_LIMIT);
  const stored = resultsQuery.data;
  const liveStandings = useStandings(team.league_id);
  const thisYear = new Date().getFullYear();

  // "Current season" is the season being played: its list, with the stored
  // results laid over it. The label of the live table says which year the
  // provider files that season under (see `seasonKey`).
  const yearMode = seasonYear !== undefined;
  const seasonLabel = liveStandings.data?.season;
  // `isFetched`, here and for the list: answered once, with a payload or an
  // error, and still answered while the same thing is being asked again.
  // (A query with nothing to show goes back to "pending" on every refetch,
  // which would take the page down to its skeleton each time.)
  const labelKnown = liveStandings.isFetched;
  const liveKey =
    archives && labelKnown
      ? seasonKey(seasonLabel, league?.sport, thisYear)
      : undefined;
  const seasonQuery = useSeasonResults(team.id, seasonYear ?? liveKey, {
    live: isSeasonInPlay(seasonYear, liveKey),
  });
  const seasonList = seasonQuery.data;
  // Whether the season on screen is the one being played: "Current season",
  // or the year that is the live table's own key.
  const liveSeason = isSeasonInPlay(seasonYear, liveKey);

  // The list of a season is asked for once and kept, as suits a past
  // season. For the one being played that would make a single failed
  // request final (the backend answers a provider failure and "no such
  // season" alike), leaving the stored results alone, holes and all, for as
  // long as the page stays open. So that one is asked for again, slowly.
  const seasonFailed = !yearMode && seasonQuery.isError;
  const reaskSeason = seasonQuery.refetch;
  useEffect(() => {
    if (!seasonFailed) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void reaskSeason();
    }, SEASON_RETRY_MS);
    return () => window.clearInterval(timer);
  }, [seasonFailed, reaskSeason]);

  // The stored results wait for the season's list, so that a streak which
  // the list is about to correct is not flashed first; but not for long
  // (see SEASON_WAIT_MS), timed from the moment the team is chosen, whatever
  // the requests behind it are doing. A team that has been waited for is
  // not waited for again while the same answer is still outstanding:
  // switching away and back must not take down what was already showing.
  const seasonAnswered =
    yearMode ||
    !archives ||
    (labelKnown && (liveKey === undefined || seasonQuery.isFetched));
  const [waitedOut, setWaitedOut] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const teamId = team.id;
  useEffect(() => {
    if (seasonAnswered) {
      // Answered: the next wait for this team (its answer can be dropped
      // from the cache while another team is on screen) starts afresh.
      setWaitedOut((prev) => {
        if (!prev.has(teamId)) return prev;
        const next = new Set(prev);
        next.delete(teamId);
        return next;
      });
      return;
    }
    const timer = window.setTimeout(
      () => setWaitedOut((prev) => new Set(prev).add(teamId)),
      SEASON_WAIT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [teamId, seasonAnswered]);
  const ready =
    stored !== undefined && (seasonAnswered || waitedOut.has(teamId));

  // The season being played, when its list is here, and that list with the
  // stored results laid over it.
  const playing = liveSeason ? currentSeason(seasonList) : undefined;
  const merged = useMemo(
    () =>
      playing !== undefined && stored !== undefined
        ? mergeSeason(playing, stored, team.league_id)
        : undefined,
    [playing, stored, team.league_id],
  );

  const activeQuery = yearMode ? seasonQuery : resultsQuery;
  // A picked year is the provider's list of it (merged, when it is the
  // season being played). "Current season" is the merged list, or simply
  // every stored result, as it always was, when there is no list to merge.
  const games = yearMode
    ? (merged ?? seasonList)
    : ready
      ? (merged ?? stored)
      : undefined;
  // Whether the list is one season's games; every stored result is not, and
  // is not called a season.
  const oneSeason = yearMode || playing !== undefined;

  // The standings are the live table, shown when the season on screen is
  // the one it is for: the season being played, or any other year only on
  // proof (see `seasonRow` for why a past season's table is never asked for).
  const seasonIsLive =
    liveSeason || holdsNewestResult(games, stored, team.league_id);
  const standings = seasonIsLive ? liveStandings.data : undefined;
  const row = seasonRow(
    liveStandings.data,
    team,
    seasonIsLive,
    (games ?? []).length > 0,
  );

  // Days are the server's, as on Today. The Today payload is the one the
  // score line already polls, so reading it here costs no request, and it
  // is what turns a "Next up" cell live.
  const todayQuery = useToday();
  const todayKey = todayQuery.data?.date ?? localKeyFromDate(new Date());
  const zone = todayQuery.data?.timezone;
  const todayGames = todayQuery.data?.games;
  // From yesterday, so a game still going past midnight is in the range.
  // Today no longer carries such a game (it holds the games that start on
  // its day), so the schedule is the only place its ending shows: while it
  // holds a live game that Today does not, it is read again on a beat.
  const [recheckLive, setRecheckLive] = useState(false);
  const scheduleQuery = useSchedule(
    dayKeyOffset(todayKey, -1),
    dayKeyOffset(todayKey, NEXT_UP_DAYS),
    team.id,
    recheckLive ? { refetchInterval: LIVE_RECHECK_MS } : undefined,
  );
  const schedule = scheduleQuery.data;
  const unwatchedLive = useMemo(
    () => hasUnwatchedLive(schedule ?? [], todayGames ?? []),
    [schedule, todayGames],
  );
  useEffect(() => {
    setRecheckLive(unwatchedLive);
  }, [unwatchedLive]);
  const upcoming = useMemo(
    () =>
      seasonYear === undefined
        ? nextUp(schedule ?? [], todayGames ?? [], todayKey, zone)
        : [],
    [seasonYear, schedule, todayGames, todayKey, zone],
  );

  // The games the API can open a box score for are the stored ones. A
  // season's list is fetched live and never stored, so only its rows that
  // are also among the stored results get the link.
  const openable = useMemo(
    () => new Set((stored ?? []).map((game) => game.id)),
    [stored],
  );

  // A game that has finished and is not among the stored results yet has
  // just left "Next up": fetch the list it now belongs in, once per game.
  const finished = useMemo(
    () =>
      stored === undefined
        ? []
        : unlistedFinals(schedule ?? [], todayGames ?? [], team.id, openable),
    [stored, schedule, todayGames, team.id, openable],
  );
  const refetchResults = resultsQuery.refetch;
  const askedFor = useRef(new Set<string>());
  useEffect(() => {
    const fresh = finished.filter((id) => !askedFor.current.has(id));
    if (fresh.length === 0) return;
    for (const id of fresh) askedFor.current.add(id);
    void refetchResults();
  }, [finished, refetchResults]);

  const sport = league?.sport ?? games?.[0]?.sport;
  const words = sportWords(sport);
  const summary = useMemo(
    () => summarize(games ?? [], team, sport, FORM_GAMES),
    [games, team, sport],
  );
  const groups = useMemo(() => monthGroups(games ?? []), [games]);
  const shape: TableShape = useMemo(
    () => ({
      scores: sport === "mma" ? "method" : "tally",
      wideScores: (games ?? []).some(
        (game) => (game.home.score ?? 0) > 99 || (game.away.score ?? 0) > 99,
      ),
      detail: (games ?? []).some((game) => openable.has(game.id)),
    }),
    [sport, games, openable],
  );

  const teamColors = useMemo(() => {
    const map: Record<string, string> = {};
    for (const entry of teams) {
      if (entry.color) map[entry.id] = entry.color;
    }
    return map;
  }, [teams]);
  const namedColors = useMemo(
    () => colorsByName([...(stored ?? []), ...(games ?? []), ...(schedule ?? [])]),
    [stored, games, schedule],
  );
  const leagueNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const entry of leagues) map[entry.id] = entry.name;
    return map;
  }, [leagues]);
  // The dropdown marks each team as the toggle buttons do: with its block.
  const teamOptions: SelectOption[] = useMemo(
    () =>
      teams.map((entry) => ({
        id: entry.id,
        label: entry.name,
        abbreviation: entry.abbreviation,
        color: entry.color,
      })),
    [teams],
  );
  // The live option is named by its season once the table has said which
  // it is ("2026-27"), as the mockup's control and League's picker are;
  // until then, and for a league whose label carries no year, it is
  // "Current season". `thisYear` is a dependency because the list starts
  // from the current year.
  const liveToken = seasonToken(seasonLabel);
  const seasons = useMemo(
    () =>
      seasonOptions().map((option) =>
        option.id === CURRENT_SEASON && liveToken !== null
          ? { ...option, label: liveToken }
          : option,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [thisYear, liveToken],
  );

  const pickTeam = (id: string) => {
    setSelected(id);
    setSeasonSel(CURRENT_SEASON); // a new team starts on the current season
  };

  const seasonText = deckSeason(
    seasonYear,
    seasonLabel,
    archives,
    playing !== undefined,
  );
  const hasRecord =
    row !== undefined && standings !== undefined && hasSeasonRecord(standings.sport);
  const tiles = (
    <SeasonTiles
      team={team}
      sport={sport}
      title={
        yearMode
          ? `${seasonYear} season`
          : oneSeason || hasRecord
            ? "Season so far"
            : "Recent form"
      }
      note={[
        league?.name ?? null,
        games !== undefined && games.length > 0
          ? `through ${longDay(games[0].start_time)}`
          : null,
      ]
        .filter((part): part is string => part !== null)
        .join(" · ")}
      summary={summary}
      standings={standings}
      row={row}
    />
  );
  const next = (
    <NextUp
      games={upcoming}
      team={team}
      teams={teams}
      sport={sport}
      todayKey={todayKey}
      zone={zone}
    />
  );

  // Only replace the list when there is nothing cached to show; a failed
  // refetch keeps the last results up behind a stale banner (see TodayView).
  let body: ReactNode;
  if (activeQuery.isError && !games) {
    const noSuchSeason =
      seasonYear !== undefined &&
      activeQuery.error instanceof ApiError &&
      activeQuery.error.status === 404;
    body = noSuchSeason ? (
      <Notice title="Results" headline={`No ${seasonYear} season`}>
        The {seasonYear} season isn&rsquo;t available from the provider for
        this team.
      </Notice>
    ) : (
      <Notice
        title="Results"
        alert
        headline="Couldn’t load results"
        action={{ label: "Retry", onClick: () => void activeQuery.refetch() }}
      >
        {`The request failed${errorText(activeQuery.error)}.`}
      </Notice>
    );
  } else if (!games) {
    body = <PageLoading label="Loading results" />;
  } else if (games.length === 0) {
    body = (
      <>
        {tiles}
        <Notice
          title="Results"
          headline={seasonYear !== undefined ? "No final scores" : "No results yet"}
        >
          {seasonYear !== undefined
            ? "No final scores for that season."
            : "Final scores accumulate here as games finish while the app runs."}
        </Notice>
        {next}
      </>
    );
  } else {
    // What the columns mean, said once over the newest month; the months
    // under it say how many games they hold.
    const legend = [
      "Newest first",
      sport !== undefined && !isIndividualSport(sport)
        ? "vs is a home game, at is on the road"
        : null,
      words.tally !== null
        ? `${possessive(team.name)} ${words.tally.noun.toLowerCase()} come first`
        : null,
    ]
      .filter((part): part is string => part !== null)
      .join(" · ");
    body = (
      <>
        {tiles}
        {groups.map((group, index) => (
          <ResultsTable
            key={group.key}
            group={group}
            team={team}
            teams={teams}
            shape={shape}
            note={index === 0 ? legend : countOf(group.games.length, words)}
            leagueNames={leagueNames}
            openable={openable}
            onOpen={setOpenGame}
          />
        ))}
        {next}
      </>
    );
  }

  return (
    <TeamColorsContext.Provider value={teamColors}>
    <NamedColorsContext.Provider value={namedColors}>
      <div className="flex flex-col gap-7">
        {activeQuery.isError && games && (
          <StaleBanner
            message="Connection lost — showing the last loaded results."
            onRetry={() => void activeQuery.refetch()}
          />
        )}

        <PageHead
          deck={seasonText === null ? team.name : `${team.name} · ${seasonText}`}
        >
          <div className="flex min-w-0 flex-wrap items-end gap-x-4 gap-y-3">
            <Field label="Team">
              {teamSwitcherKind(teams.length) === "buttons" ? (
                <div role="group" aria-label="Team" className="flex flex-wrap gap-1.5">
                  {teams.map((entry) => (
                    <Button
                      key={entry.id}
                      aria-pressed={entry.id === team.id}
                      onClick={() => pickTeam(entry.id)}
                      className="min-w-0 max-w-full gap-2.5 py-1.5 pl-2 pr-3.5 text-left"
                    >
                      <TeamBlock
                        name={entry.name}
                        abbreviation={entry.abbreviation}
                        color={entry.color}
                      />
                      {entry.name}
                    </Button>
                  ))}
                </div>
              ) : (
                <Select
                  options={teamOptions}
                  value={team.id}
                  onChange={pickTeam}
                  ariaLabel="Choose team"
                />
              )}
            </Field>
            {archives && (
              <Field label="Season">
                <Select
                  options={seasons}
                  value={seasonSel}
                  onChange={setSeasonSel}
                  ariaLabel="Choose season"
                />
              </Field>
            )}
          </div>
        </PageHead>

        {body}

        {openGame !== null && (
          <GameDetailModal
            gameId={openGame.id}
            fallbackGame={openGame}
            onClose={() => setOpenGame(null)}
          />
        )}
      </div>
    </NamedColorsContext.Provider>
    </TeamColorsContext.Provider>
  );
}

/**
 * One followed team's finished games, newest first by month, under the
 * figures that sum them up (the season record, the form over the last
 * eight, the streak, the scoring, the place in the table) and over the
 * games that come next.
 */
export default function ResultsView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();

  if (teamsQuery.isError && !teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <Notice
          title="Results"
          alert
          headline="Couldn’t load teams"
          action={{ label: "Retry", onClick: () => void teamsQuery.refetch() }}
        >
          {`The request failed${errorText(teamsQuery.error)}.`}
        </Notice>
      </div>
    );
  }
  if (!teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <PageLoading label="Loading teams" />
      </div>
    );
  }
  if (teamsQuery.data.teams.length === 0) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <Notice
          title="Results"
          headline="No teams followed yet"
          action={{ label: "Manage teams", onClick: openManageTeams }}
        >
          Follow a team to see its results here.
        </Notice>
      </div>
    );
  }
  return (
    <TeamResults
      teams={teamsQuery.data.teams}
      leagues={teamsQuery.data.leagues}
    />
  );
}
