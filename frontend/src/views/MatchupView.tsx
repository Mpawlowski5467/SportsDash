import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError } from "../api";
import { Loading, Notice } from "../components/bs";
import GameDetailModal from "../components/GameDetailModal";
import { useManageTeams } from "../components/ManageTeamsContext";
import { useIsActiveTab } from "../components/NavigateContext";
import StaleBanner from "../components/StaleBanner";
import { useTheme } from "../components/ThemeProvider";
import UpsetRadar from "../components/UpsetRadar";
import {
  useGameDetail,
  useMatchup,
  useSchedule,
  useSetupTeamsFor,
  useStandings,
  useTeams,
  useToday,
} from "../hooks";
import { errorText } from "../lib/errorText";
import { localKeyFromDate } from "../lib/time";
import { useNow } from "../lib/useNow";
import type { Game } from "../types";
import { teamLookup, type TeamLookup } from "./league/model";
import GamePicker from "./matchup/GamePicker";
import GameSheet from "./matchup/GameSheet";
import LastGames from "./matchup/LastGames";
import Meetings from "./matchup/Meetings";
import {
  PREVIEW_DAYS,
  bettingLines,
  catalogLeagues,
  chooseGame,
  freshest,
  mergeMeetings,
  pickerSplit,
  previewGames,
  probabilityRows,
  viewerDay,
} from "./matchup/model";
import { IdentityContext, type Identify } from "./matchup/parts";
import { Injuries, Lineups } from "./matchup/Squads";
import StandingsContext from "./matchup/StandingsContext";
import WinProbability from "./matchup/WinProbability";
import { colorsByName, hasUnwatchedLive } from "./results/model";
import { dayKeyOffset } from "./today/model";

/**
 * The view stays mounted once visited, so anything fetched once would stand
 * until a reload: the schedule and the standings are read again on a beat.
 * Both are plain reads of what the server has stored.
 */
const SCHEDULE_REFRESH_MS = 5 * 60_000;
const STANDINGS_REFRESH_MS = 5 * 60_000;
/** How often the schedule is read again while it alone holds a live game. */
const LIVE_RECHECK_MS = 60_000;
/**
 * How old the preview on screen may get before it is asked for again. The
 * preview is assembled from provider calls (lines, forecast), so it is not
 * polled like the rest: it is refreshed this often, and only while Matchup
 * is the tab on screen and the game is still to be played or under way.
 */
const PREVIEW_REFRESH_MS = 10 * 60_000;

/** The page's name, beside the control that chooses the game. */
function PageHead({ children }: { children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
      <h1 className="bs-title">Matchup</h1>
      {children}
    </header>
  );
}

const SHAPE = "border-t-[3px] border-rule bg-tint";

/** The shape of the page while the list of games is on its way. */
function PageLoading() {
  return (
    <Loading label="Loading games" className="flex flex-col gap-7">
      <div className={`h-60 ${SHAPE}`} />
      <div className="flex flex-wrap gap-7">
        <div className={`h-60 min-w-0 flex-[999_1_35rem] ${SHAPE}`} />
        <div className={`h-60 min-w-0 flex-[1_1_23.75rem] ${SHAPE}`} />
      </div>
    </Loading>
  );
}

/**
 * One game, previewed or followed live: the two sides with their records
 * and the score, each side's chance to win, the games running against
 * expectation, the two sides' meetings, the followed side's last few
 * results, where both stand in the table, and (kept from the old view)
 * the betting lines, projected lineups and injuries. Every section hides
 * when the game carries no data for it.
 */
export default function MatchupView() {
  const showing = useIsActiveTab("matchup");
  const now = useNow(30_000);
  const { favoriteTeam } = useTheme();
  const openManageTeams = useManageTeams();

  // Which games are on offer goes by the server's day, as on Today. The
  // Today payload is the one the score line already polls, so reading it
  // here costs no request, and it is what turns the game on screen live.
  const todayQuery = useToday();
  const todayKey = todayQuery.data?.date ?? localKeyFromDate(now);
  const zone = todayQuery.data?.timezone;
  const todayGames = todayQuery.data?.games;
  // The day words ("Tonight", a bare start time) go by the viewer's own
  // day, the one the clock times are printed in.
  const today = viewerDay(now);

  // From yesterday, so a game still going past midnight is in the range.
  // Today no longer carries such a game, so while the schedule holds a live
  // game that Today does not, it is read again on a shorter beat.
  const [recheckLive, setRecheckLive] = useState(false);
  const scheduleQuery = useSchedule(
    dayKeyOffset(todayKey, -1),
    dayKeyOffset(todayKey, PREVIEW_DAYS),
    undefined,
    { refetchInterval: recheckLive ? LIVE_RECHECK_MS : SCHEDULE_REFRESH_MS },
  );
  // The range moves on when the server's day does, and a new range is a
  // new query with nothing loaded yet. The last answer stands in until the
  // new one lands, or the whole page would drop to its skeleton (or jump to
  // another game) once a night.
  const loadedSchedule = scheduleQuery.data;
  const [heldSchedule, setHeldSchedule] = useState<Game[] | undefined>(
    undefined,
  );
  useEffect(() => {
    if (loadedSchedule !== undefined) setHeldSchedule(loadedSchedule);
  }, [loadedSchedule]);
  const schedule = loadedSchedule ?? heldSchedule;
  const unwatchedLive = useMemo(
    () => hasUnwatchedLive(schedule ?? [], todayGames ?? []),
    [schedule, todayGames],
  );
  useEffect(() => {
    setRecheckLive(unwatchedLive);
  }, [unwatchedLive]);

  const teamsQuery = useTeams();
  const leagues = teamsQuery.data?.leagues;
  const teams = teamsQuery.data?.teams;

  // The games on offer, and the one on screen: the reader's choice, else
  // the first (see `chooseGame` for what happens when a chosen game ends).
  const list = useMemo(
    () => previewGames(schedule ?? [], todayGames ?? [], todayKey, zone),
    [schedule, todayGames, todayKey, zone],
  );
  const [chosenId, setChosenId] = useState<string | undefined>(undefined);
  const listed = useMemo(
    () => chooseGame(list, schedule ?? [], todayGames ?? [], chosenId, now),
    [list, schedule, todayGames, chosenId, now],
  );
  const selectedId = listed?.id;

  const matchupQuery = useMatchup(selectedId);
  const matchup = matchupQuery.data;
  // The box score is what carries the in-game win probability. Each poll of
  // it costs the server uncached provider calls, so it runs only while the
  // game is live and Matchup is the tab on screen.
  const detailQuery = useGameDetail(selectedId, {
    enabled: showing && listed?.phase === "in_progress",
  });
  const detail = detailQuery.data;

  // The preview does not refresh, so its copy of the game stands still. The
  // game on screen is the newest copy among the payloads that carry it.
  const listedFromToday =
    listed !== null && (todayGames ?? []).some((entry) => entry.id === listed.id);
  const game: Game | null =
    listed === null
      ? null
      : (freshest<Game>([
          {
            value: listed,
            at: listedFromToday
              ? todayQuery.dataUpdatedAt
              : scheduleQuery.dataUpdatedAt,
          },
          { value: detail?.game, at: detailQuery.dataUpdatedAt },
          { value: matchup?.game, at: matchupQuery.dataUpdatedAt },
        ]) ?? listed);

  const leagueId = game?.league_id;
  const league = leagues?.find((entry) => entry.id === leagueId);
  // A fighter has no table; every other sport has one or a ranking.
  const standingsQuery = useStandings(
    game !== null && game.sport !== "mma" ? game.league_id : undefined,
    undefined,
    { refetchInterval: STANDINGS_REFRESH_MS },
  );
  const standings = standingsQuery.data;

  // ── Team colours and codes ──────────────────────────────────────────
  // A game reached through a followed team's schedule carries no colour on
  // either side. Joined here, per league: its standings rows (codes), the
  // followed teams and the setup catalog (every team's colour).
  const catalogKey = catalogLeagues(
    game === null ? list : [game, ...list],
    leagues ?? [],
  ).join("\n");
  const catalogIds = useMemo(
    () => (catalogKey === "" ? [] : catalogKey.split("\n")),
    [catalogKey],
  );
  const catalogs = useSetupTeamsFor(catalogIds);
  const lookups = useMemo(() => {
    const byLeague: Record<string, TeamLookup> = {};
    const ids = new Set(catalogIds);
    if (leagueId !== undefined) ids.add(leagueId);
    for (const id of ids) {
      const at = catalogIds.indexOf(id);
      byLeague[id] = teamLookup([
        id === leagueId
          ? (standings?.rows ?? []).map((row) => ({
              name: row.team_name,
              abbreviation: row.abbreviation,
              color: row.color,
            }))
          : [],
        (teams ?? []).filter((team) => team.league_id === id),
        at === -1 ? [] : (catalogs[at]?.teams ?? []),
      ]);
    }
    return byLeague;
  }, [catalogIds, catalogs, leagueId, standings, teams]);
  const followedColors = useMemo(() => {
    const colors: Record<string, string> = {};
    for (const team of teams ?? []) {
      if (team.color) colors[team.id] = team.color;
    }
    return colors;
  }, [teams]);
  const namedColors = useMemo(
    () => colorsByName([...(schedule ?? []), ...(todayGames ?? [])]),
    [schedule, todayGames],
  );
  const identify: Identify = useCallback(
    (side, sideLeague) => {
      const known = lookups[sideLeague]?.(side.name, side.abbreviation) ?? null;
      return {
        color:
          side.color ||
          (side.team_id === null ? undefined : followedColors[side.team_id]) ||
          known?.color ||
          namedColors[side.name] ||
          null,
        abbreviation: side.abbreviation || known?.abbreviation || null,
      };
    },
    [lookups, followedColors, namedColors],
  );

  // ── Keeping the preview from going stale ────────────────────────────
  const previewAt = Math.max(
    matchupQuery.dataUpdatedAt,
    matchupQuery.errorUpdatedAt,
  );
  const refetchPreview = matchupQuery.refetch;
  const toPlay = game?.phase === "scheduled" || game?.phase === "in_progress";
  useEffect(() => {
    if (!showing || !toPlay || previewAt === 0) return;
    const timer = window.setTimeout(
      () => void refetchPreview(),
      Math.max(0, PREVIEW_REFRESH_MS - (Date.now() - previewAt)),
    );
    return () => window.clearTimeout(timer);
  }, [showing, toPlay, previewAt, refetchPreview]);

  const [openGame, setOpenGame] = useState<Game | null>(null);

  const meetings = useMemo(
    () =>
      matchup === undefined
        ? []
        : mergeMeetings(matchup.head_to_head, matchup.head_to_head_record),
    [matchup],
  );
  // The games the API can open a box score for are the stored ones; the
  // meetings that came only with the cross-season record are not.
  const openable = useMemo(
    () => new Set((matchup?.head_to_head ?? []).map((entry) => entry.id)),
    [matchup],
  );

  const radar = (
    <UpsetRadar
      games={list}
      currentId={selectedId}
      today={today}
      showing={showing}
      onSelect={setChosenId}
    />
  );

  // ── Nothing to preview ──────────────────────────────────────────────
  if (game === null) {
    const scheduleFailed = scheduleQuery.isError && schedule === undefined;
    return (
      <IdentityContext.Provider value={identify}>
        <div className="flex flex-col gap-7">
          <PageHead />
          {scheduleFailed ? (
            <Notice
              title="Upcoming"
              alert
              headline="Couldn’t load the schedule"
              action={{
                label: "Retry",
                onClick: () => {
                  void scheduleQuery.refetch();
                  void todayQuery.refetch();
                },
              }}
            >
              {`The request failed${errorText(scheduleQuery.error)}.`}
            </Notice>
          ) : schedule === undefined ? (
            // Nothing answered yet. Once a range has answered, its games
            // stand until the next range does (see `heldSchedule`).
            <PageLoading />
          ) : (
            <>
              {teamsQuery.data !== undefined &&
              teamsQuery.data.teams.length === 0 &&
              teamsQuery.data.leagues.length === 0 ? (
                <Notice
                  title="Upcoming"
                  headline="No upcoming games"
                  action={{ label: "Manage teams", onClick: openManageTeams }}
                >
                  Nothing is followed yet. Follow a team or a competition to
                  see its games previewed here.
                </Notice>
              ) : (
                <Notice title="Upcoming" headline="No upcoming games">
                  Previews appear here as soon as your followed teams have
                  games on the schedule.
                </Notice>
              )}
              {/* In a row of its own: the radar sizes itself as a column of
                  the row it shares with the win probability. */}
              <div className="flex">{radar}</div>
            </>
          )}
        </div>
      </IdentityContext.Provider>
    );
  }

  const split = pickerSplit(list, listed);
  const odds = matchup?.odds ?? detail?.odds ?? null;
  const rows =
    matchup === undefined ? [] : probabilityRows(game, odds, detail?.summary);
  const lines = matchup === undefined ? [] : bettingLines(game, odds);
  const hasLineup =
    matchup !== undefined &&
    matchup.lineup !== null &&
    (matchup.lineup.home !== null || matchup.lineup.away !== null);

  // A background refetch that failed keeps what was loaded behind a banner;
  // only a request with nothing cached replaces its part of the page.
  const stale =
    scheduleQuery.isError && schedule !== undefined
      ? {
          message: "Connection lost — showing the last loaded games.",
          retry: () => void scheduleQuery.refetch(),
        }
      : scheduleQuery.isError
        ? {
            message: "Couldn’t load the schedule — showing today’s games only.",
            retry: () => void scheduleQuery.refetch(),
          }
        : matchupQuery.isError && matchup !== undefined
          ? {
              message: "Connection lost — showing the last loaded preview.",
              retry: () => void matchupQuery.refetch(),
            }
          : null;

  const previewMissing =
    matchupQuery.error instanceof ApiError && matchupQuery.error.status === 404;

  return (
    <IdentityContext.Provider value={identify}>
      <div className="flex flex-col gap-7">
        {stale !== null && (
          <StaleBanner message={stale.message} onRetry={stale.retry} />
        )}

        <PageHead>
          <GamePicker
            split={split}
            currentId={game.id}
            today={today}
            onPick={setChosenId}
          />
        </PageHead>

        <GameSheet
          game={game}
          leagueName={league?.name}
          standings={standings}
          // A forecast is for a game still to start. The server sends none
          // for any other, but the copy fetched before the start is still
          // held once the game is under way or over.
          weather={
            game.phase === "scheduled" ? (matchup?.weather ?? null) : null
          }
          now={now}
          onOpen={setOpenGame}
        />

        <div className="flex flex-wrap items-stretch gap-7">
          {matchup !== undefined ? (
            <WinProbability game={game} rows={rows} lines={lines} />
          ) : matchupQuery.isError ? (
            previewMissing ? (
              <Notice
                title="Preview"
                headline="No preview for this game"
                className="min-w-0 flex-[999_1_35rem]"
              >
                It is no longer on the stored schedule.
              </Notice>
            ) : (
              <Notice
                title="Preview"
                alert
                headline="Couldn’t load this preview"
                action={{
                  label: "Retry",
                  onClick: () => void matchupQuery.refetch(),
                }}
                className="min-w-0 flex-[999_1_35rem]"
              >
                {`The request failed${errorText(matchupQuery.error)}.`}
              </Notice>
            )
          ) : (
            <Loading
              label="Loading preview"
              className="min-w-0 flex-[999_1_35rem]"
            >
              <div className={`h-full min-h-56 ${SHAPE}`} />
            </Loading>
          )}
          {radar}
        </div>

        {matchup !== undefined ? (
          <>
            <div className="flex flex-wrap items-stretch gap-7 empty:hidden">
              <Meetings
                game={game}
                meetings={meetings}
                record={matchup.head_to_head_record}
                openable={openable}
                onOpen={setOpenGame}
                now={now}
              />
              <LastGames
                // A new game starts on its own default side.
                key={game.id}
                game={game}
                league={league}
                teams={teams ?? []}
                favoriteId={favoriteTeam?.id ?? null}
                awayForm={matchup.away_form}
                homeForm={matchup.home_form}
                seasonLabel={standings?.season}
                labelKnown={standingsQuery.isFetched}
                now={now}
              />
              <StandingsContext game={game} standings={standings} />
            </div>
            {hasLineup && matchup.lineup !== null && (
              <Lineups game={game} lineup={matchup.lineup} />
            )}
            <Injuries
              game={game}
              away={matchup.away_injuries}
              home={matchup.home_injuries}
            />
          </>
        ) : (
          !matchupQuery.isError && (
            <div
              aria-hidden="true"
              className="flex animate-pulse flex-wrap gap-7"
            >
              {[0, 1, 2].map((index) => (
                <div
                  key={index}
                  className={`h-72 min-w-0 flex-[1_1_21.25rem] ${SHAPE}`}
                />
              ))}
            </div>
          )
        )}

        {openGame !== null && (
          <GameDetailModal
            gameId={openGame.id}
            fallbackGame={openGame}
            onClose={() => setOpenGame(null)}
          />
        )}
      </div>
    </IdentityContext.Provider>
  );
}
