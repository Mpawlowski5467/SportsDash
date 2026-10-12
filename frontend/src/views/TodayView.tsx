import { useMemo, useState } from "react";

import { Loading, Notice, SectionHead, SpotChip } from "../components/bs";
import EventLeaderboardModal from "../components/EventLeaderboardModal";
import GameDetailModal from "../components/GameDetailModal";
import { useNavigateTab } from "../components/NavigateContext";
import StaleBanner from "../components/StaleBanner";
import {
  useEvents,
  useGameOdds,
  useOnThisDay,
  useSchedule,
  useTeams,
  useToday,
} from "../hooks";
import { errorText } from "../lib/errorText";
import { dateKeyInZone, localKeyFromDate, parseLocalDateKey } from "../lib/time";
import { useNow } from "../lib/useNow";
import type { Game, League, Team } from "../types";
import { ResultsList, UpcomingList } from "./today/GameLists";
import Leaderboards from "./today/Leaderboards";
import LiveHero from "./today/LiveHero";
import {
  dayKeyOffset,
  nextStartNote,
  pickHero,
  standingColumns,
  upcomingTitle,
  weekStrip,
  yesterdayTitle,
} from "./today/model";
import { isOnThisDayCurrent } from "./today/onThisDay";
import { TeamColorsContext } from "./today/parts";
import OnThisDaySection from "./today/OnThisDaySection";
import StandingsBox from "./today/StandingsBox";
import WeekStrip from "./today/WeekStrip";

/** How many leagues get a standings box beside the slate. */
const STANDINGS_LEAGUES = 3;
/**
 * The week's schedule is a plain database read, and Today stays on screen
 * across midnight: without a refresh, a game still being played when the
 * day rolls over would never reach "Last night".
 */
const SCHEDULE_REFRESH_MS = 5 * 60_000;

/** One figure of the count strip beside the date. */
function Count({
  value,
  label,
  live = false,
}: {
  value: number;
  label: string;
  live?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-1.5 border-l-2 border-ink px-2 first:border-l-0 first:pl-0 last:pr-0 sm:gap-2 sm:px-[1.375rem]">
      <span
        className={
          "text-[2.5rem] font-bold leading-[0.85] sm:text-[3.75rem] " +
          (value === 0 ? "text-muted" : "text-ink")
        }
      >
        {value}
      </span>
      {live && value > 0 ? (
        <SpotChip className="text-base">{label}</SpotChip>
      ) : (
        <span className="text-base font-semibold tracking-[0.08em] text-ink-2">
          {label}
        </span>
      )}
    </div>
  );
}

/** The shapes of the page while the first payload is on its way. */
function Skeleton() {
  return (
    <Loading label="Loading today’s games" className="flex flex-col gap-7">
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div className="h-[5.5rem] w-72 max-w-full bg-hair" />
        <div className="h-14 w-64 max-w-full bg-hair" />
      </div>
      <div className="flex flex-wrap items-start gap-7">
        <div className="flex min-w-0 flex-[999_1_35rem] flex-col gap-7">
          <div className="h-64 border-t-[3px] border-rule bg-tint" />
          <div className="h-40 border-t-[3px] border-rule bg-tint" />
        </div>
        <div className="flex min-w-0 flex-[1_1_22.5rem] flex-col gap-7">
          <div className="h-56 border-t-[3px] border-rule bg-tint" />
        </div>
      </div>
    </Loading>
  );
}

/**
 * Kiosk landing screen: the local calendar day for the followed teams, by
 * phase. The live game large, what is still to come, what has finished
 * today and last night; beside them where the followed teams stand and
 * what happened on this date; under them the week ahead.
 */
export default function TodayView() {
  const todayQuery = useToday();
  const teamsQuery = useTeams();
  // Supplementary "On this day" history; its section self-hides on error /
  // while pending, so it never gates the main slate. (Hooks run before the
  // early returns below — the view's standing rule.)
  const onThisDayQuery = useOnThisDay();
  const navigate = useNavigateTab();
  // "First pitch in 12 minutes" is a function of the clock, not of a payload.
  const now = useNow(30_000);

  const [openGame, setOpenGame] = useState<Game | null>(null);
  // The leaderboard-event id whose modal is open, or null. Golf has no
  // dedicated tab — its leaderboards are reached from the section here.
  const [openEventId, setOpenEventId] = useState<string | null>(null);

  // Days are the server's days: `/today` says which day it is and in which
  // zone, and `/schedule` takes days in the same terms. Until it answers,
  // the browser's own day is the best guess (and almost always the same).
  const todayKey = todayQuery.data?.date ?? localKeyFromDate(new Date());
  const zone = todayQuery.data?.timezone;
  const yesterdayKey = dayKeyOffset(todayKey, -1);
  const weekEndKey = dayKeyOffset(todayKey, 6);
  // One request covers last night and the week ahead; leaderboard events
  // (a tournament, a race weekend) are not games and come separately.
  const scheduleQuery = useSchedule(yesterdayKey, weekEndKey, undefined, {
    refetchInterval: SCHEDULE_REFRESH_MS,
  });
  const weekEventsQuery = useEvents(todayKey, weekEndKey);

  const teamsById = useMemo(() => {
    const map: Record<string, Team> = {};
    for (const team of teamsQuery.data?.teams ?? []) map[team.id] = team;
    return map;
  }, [teamsQuery.data]);

  const teamColors = useMemo(() => {
    const map: Record<string, string> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      if (team.color) map[team.id] = team.color;
    }
    return map;
  }, [teamsQuery.data]);

  const leaguesById = useMemo(() => {
    const map: Record<string, League> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      map[league.id] = league;
    }
    return map;
  }, [teamsQuery.data]);

  // Odds + win-probability for the scheduled / live games on screen, fetched
  // in one batch. (Empty until today's data loads.)
  const oddsGameIds = useMemo(
    () =>
      (todayQuery.data?.games ?? [])
        .filter((g) => g.phase === "scheduled" || g.phase === "in_progress")
        .map((g) => g.id),
    [todayQuery.data],
  );
  const oddsByGame = useGameOdds(oddsGameIds).data ?? {};

  if (todayQuery.isPending || teamsQuery.isPending) {
    return <Skeleton />;
  }

  // Full-screen error only when there is nothing to show. A failed
  // background refetch on a kiosk must NOT blank a perfectly good
  // dashboard — keep rendering the stale data with a slim warning.
  if (todayQuery.isError && !todayQuery.data) {
    return (
      <Notice
        title="Today"
        alert
        action={{ label: "Retry", onClick: () => void todayQuery.refetch() }}
      >
        Couldn&rsquo;t load today&rsquo;s games
        {errorText(todayQuery.error)}
      </Notice>
    );
  }
  if (!todayQuery.data) {
    return <Skeleton />;
  }

  const stale = todayQuery.isError;
  const { date, games } = todayQuery.data;
  // Defensive: an older backend (pre-Phase-5) may omit `events` entirely.
  const events = todayQuery.data.events ?? [];
  const localDate = parseLocalDateKey(date);
  const weekday = localDate.toLocaleDateString(undefined, { weekday: "long" });
  const longDate = localDate.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const monthDay = localDate.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
  });

  // The slate by phase; the server already sends it in start order.
  const live = games.filter((g) => g.phase === "in_progress");
  const hero = pickHero(live);
  const otherLive = live.filter((g) => g !== hero);
  const finals = games.filter((g) => g.phase === "final");
  // Postponed and canceled games stay with the games still to come, with
  // the status where the time would be: they were on today's card.
  const upcoming = games.filter(
    (g) => g.phase !== "in_progress" && g.phase !== "final",
  );
  const scheduled = upcoming.filter((g) => g.phase === "scheduled");

  const schedule = scheduleQuery.data ?? [];
  const lastNight = schedule.filter(
    (g) =>
      g.phase === "final" && dateKeyInZone(g.start_time, zone) === yesterdayKey,
  );
  const yesterday = parseLocalDateKey(yesterdayKey).toLocaleDateString(
    undefined,
    { weekday: "long", month: "long", day: "numeric" },
  );

  // Today's games come from the live `/today` poll; the rest of the week
  // from the schedule. Today's copy wins where both have a game.
  const todayIds = new Set(games.map((g) => g.id));
  const week = weekStrip(
    date,
    [...games, ...schedule.filter((g) => !todayIds.has(g.id))],
    weekEventsQuery.data ?? events,
    zone,
  );

  // A standings box for each league a followed team plays in, the leagues
  // with a game today first. Only leagues that HAVE a team table: a followed
  // tennis player or driver is a "team" too, and would otherwise take a slot
  // (and a request) for a box that can never be drawn.
  const playingToday = new Set(games.map((g) => g.league_id));
  const standingsLeagues = [
    ...new Set((teamsQuery.data?.teams ?? []).map((team) => team.league_id)),
  ]
    .filter((leagueId) => {
      const sport = leaguesById[leagueId]?.sport;
      return sport !== undefined && standingColumns(sport) !== null;
    })
    .sort(
      (a, b) => Number(playingToday.has(b)) - Number(playingToday.has(a)),
    )
    .slice(0, STANDINGS_LEAGUES);

  // "On this day" hides entirely unless it loaded with content FOR THIS
  // local day: an error, a pending fetch, an anniversary-free date, or a
  // payload stamped with a different day (a fetch straddling midnight, or
  // the query-cache GC window keeping yesterday's entry warm) must never
  // clutter the slate. Derived with the same helper the query key uses.
  const onThisDayTeams =
    !onThisDayQuery.isError &&
    onThisDayQuery.data !== undefined &&
    isOnThisDayCurrent(onThisDayQuery.data.date, localKeyFromDate(new Date()))
      ? onThisDayQuery.data.teams
      : [];

  // The open event resolved from live data, so a background refetch that drops
  // the tournament closes the modal instead of stranding stale rows.
  const openEvent =
    openEventId !== null
      ? (events.find((e) => e.id === openEventId) ?? null)
      : null;

  const startNote = nextStartNote(scheduled, now);

  return (
    <TeamColorsContext.Provider value={teamColors}>
    <div className="flex flex-col gap-7">
      {stale && (
        <StaleBanner
          message="Connection lost — showing the last loaded scores."
          onRetry={() => void todayQuery.refetch()}
        />
      )}

      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
          <h1 className="bs-title">{weekday}</h1>
          <p className="bs-deck m-0">{longDate}</p>
        </div>
        <div className="flex flex-wrap items-stretch gap-y-2 font-condensed uppercase">
          <Count value={live.length} label="Live" live />
          <Count value={scheduled.length} label="Upcoming" />
          <Count value={finals.length} label="Final" />
        </div>
      </header>

      <div className="flex flex-wrap items-start gap-7">
        <div className="flex min-w-0 flex-[999_1_35rem] flex-col gap-7">
          {hero !== null && (
            <div>
              <LiveHero
                game={hero}
                league={leaguesById[hero.league_id]}
                onOpen={setOpenGame}
              />
              {otherLive.length > 0 && (
                <ResultsList
                  games={otherLive}
                  leaguesById={leaguesById}
                  onOpen={setOpenGame}
                  oddsByGame={oddsByGame}
                  flushTop
                  label="Also live"
                />
              )}
            </div>
          )}

          {upcoming.length > 0 && (
            <section aria-label={upcomingTitle(scheduled)}>
              <SectionHead
                title={upcomingTitle(scheduled)}
                note={startNote ?? undefined}
              />
              <UpcomingList
                games={upcoming}
                oddsByGame={oddsByGame}
                leaguesById={leaguesById}
                onOpen={setOpenGame}
              />
            </section>
          )}

          {finals.length > 0 && (
            <section aria-label="Final today">
              <SectionHead
                title="Final today"
                note={`${finals.length} ${finals.length === 1 ? "game" : "games"} finished`}
              />
              <ResultsList
                games={finals}
                leaguesById={leaguesById}
                onOpen={setOpenGame}
              />
            </section>
          )}

          {games.length === 0 && events.length === 0 && (
            <Notice
              title="Today"
              headline="No games today"
              action={{
                label: "Open calendar",
                onClick: () => navigate("calendar"),
              }}
            >
              The week ahead is below, and the Calendar has the rest.
            </Notice>
          )}

          {lastNight.length > 0 && (
            <section aria-label={yesterdayTitle(lastNight)}>
              <SectionHead title={yesterdayTitle(lastNight)} note={yesterday} />
              <ResultsList
                games={lastNight}
                leaguesById={leaguesById}
                onOpen={setOpenGame}
              />
            </section>
          )}

          {events.length > 0 && (
            <Leaderboards events={events} onOpen={setOpenEventId} />
          )}
        </div>

        {/* `empty:hidden`: every child here can turn out to have nothing to
            show, and an empty column would still hold its 360px. */}
        <div className="flex min-w-0 flex-[1_1_22.5rem] flex-col gap-7 empty:hidden">
          {standingsLeagues.map((leagueId) => (
            <StandingsBox
              key={leagueId}
              leagueId={leagueId}
              limit={standingsLeagues.length === 1 ? 2 : 1}
            />
          ))}
          {/* Outside the games/empty-state logic, so the anniversaries
              render on quiet days too. */}
          {onThisDayTeams.length > 0 && (
            <OnThisDaySection
              teams={onThisDayTeams}
              teamsById={teamsById}
              dayLabel={monthDay}
            />
          )}
        </div>
      </div>

      <WeekStrip days={week} loaded={scheduleQuery.data !== undefined} />

      {openGame !== null && (
        <GameDetailModal
          gameId={openGame.id}
          fallbackGame={openGame}
          onClose={() => setOpenGame(null)}
        />
      )}
      {openEvent !== null && (
        <EventLeaderboardModal
          event={openEvent}
          followedColor={teamColors}
          onClose={() => setOpenEventId(null)}
        />
      )}
    </div>
    </TeamColorsContext.Provider>
  );
}
