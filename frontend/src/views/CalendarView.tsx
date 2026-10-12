import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";

import {
  Button,
  Loading,
  Notice,
  RefreshIcon,
  SectionHead,
  Segmented,
  SpotChip,
} from "../components/bs";
import EventLeaderboardModal from "../components/EventLeaderboardModal";
import GameDetailModal from "../components/GameDetailModal";
import { useManageTeams } from "../components/ManageTeamsContext";
import { useIsActiveTab } from "../components/NavigateContext";
import Select, { type SelectOption } from "../components/Select";
import StaleBanner from "../components/StaleBanner";
import { useTheme } from "../components/ThemeProvider";
import {
  useEvents,
  useMeta,
  useSchedule,
  useScheduleWeather,
  useTeams,
  useToday,
} from "../hooks";
import { errorText } from "../lib/errorText";
import { localKeyFromDate } from "../lib/time";
import { useNow } from "../lib/useNow";
import { usePrefersReducedMotion } from "../lib/usePrefersReducedMotion";
import type { Game, League, SportEvent, Team } from "../types";
import type { LedgerDay } from "./calendar/Ledger";
import Legend from "./calendar/Legend";
import { Agenda, ComingUp, DayPanel, type RowContext } from "./calendar/Lists";
import MonthGrid from "./calendar/MonthGrid";
import SubscribeMenu from "./calendar/SubscribeMenu";
import WeekGrid, { dayHeadingId } from "./calendar/WeekGrid";
import { LEADERBOARD_SPORTS, appearsOnGameSide } from "./calendar/gameSideFollows";
import {
  DAY_LIST_BESIDE_MAX,
  anchorForUnit,
  busiestDayRows,
  comingUp,
  comingUpEvents,
  comingUpFoot,
  comingUpTitle,
  comingUpWindow,
  dayKeys,
  defaultDay,
  eventDays,
  eventsOn,
  fetchWindow,
  gameDay,
  gamesByDay,
  gamesInRange,
  hasLiveGame,
  holdsDay,
  legendRows,
  offNote,
  overlayLatest,
  overlayLatestEvents,
  playsIn,
  rangeCaption,
  rangeLabel,
  rangePhrase,
  rangeSpoken,
  rangeTitle,
  resolveRange,
  settleAnchor,
  stepAnchor,
  tally,
  weatherGameIds,
  weekFromMonth,
  wordsForRange,
  type CalendarUnit,
  type DayRange,
  type EventDays,
} from "./calendar/model";
import { Chevron, SideColorsContext } from "./calendar/parts";
import { NEXT_UP_DAYS, colorsByName } from "./results/model";
import { dayKeyOffset } from "./today/model";

type CalendarViewId = "week" | "month" | "agenda";

const VIEWS: { value: CalendarViewId; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "agenda", label: "Agenda" },
];

/**
 * How often the clock is read: what "today" is, and with it which column
 * is marked and which week "this week" means. The view stays mounted for
 * days on a kiosk, so the day is never held in state.
 */
const CLOCK_MS = 30_000;
/**
 * How often the schedule is read again while a game on screen is live and
 * the Calendar is the tab showing. The Today payload, laid over it, is
 * already polled twice as often; this catches a game Today does not carry
 * (one still going after midnight). Nothing is polled otherwise.
 */
const LIVE_REFRESH_MS = 60_000;
/**
 * Coming back to the tab reads the schedule again when it is older than
 * this. It is the app's own database, so the read is cheap; it is what
 * brings in a game added or called off since the tab was last looked at,
 * with no timer running while nobody is looking.
 */
const RETURN_REFRESH_MS = 5 * 60_000;
/**
 * A forecast older than this is read again, checked on the clock's beat
 * while the Calendar is the tab showing (so also on coming back to it, and
 * past midnight). It is the hook's own stale time, and the backend keeps
 * each forecast for 45 minutes, so the read is cheap. Without it a kiosk
 * showed Tuesday's forecast for Friday's game on Friday night.
 */
const WEATHER_REFRESH_MS = 30 * 60_000;

const NO_GAMES: readonly Game[] = [];
const NO_EVENTS: readonly SportEvent[] = [];
const NO_TEAMS: readonly Team[] = [];
const NO_LEAGUES: readonly League[] = [];

/**
 * One figure of the count strip beside the range. Every figure carries the
 * rule on its left; the strip is pulled left by that much and clipped, so
 * whichever figure starts a line shows none. That keeps the dividers right
 * when a month's three-figure counts wrap on a phone.
 */
function Count({
  value,
  label,
  live = false,
}: {
  /** Null while the range is on its way: a dash stands in, so the strip keeps its place. */
  value: number | null;
  label: string;
  live?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-[0.4375rem] border-l-2 border-ink px-3 last:pr-0">
      <span
        className={
          "text-[2.25rem] font-bold leading-[0.85] tabular-nums sm:text-[2.75rem] " +
          (value === null || value === 0 ? "text-muted" : "text-ink")
        }
      >
        {value ?? "–"}
      </span>
      {live && value !== null && value > 0 ? (
        <SpotChip>{label}</SpotChip>
      ) : (
        <span className="text-[0.9375rem] font-semibold tracking-[0.08em] text-ink-2">
          {label}
        </span>
      )}
    </div>
  );
}

/** The shape of the page while a range is on its way. */
function Skeleton({ label }: { label: string }) {
  return (
    <Loading label={label} className="flex flex-col gap-7">
      <div>
        <div className="border-t-[3px] border-rule">
          <div className="h-[2.125rem] w-32 bg-hair" />
        </div>
        <div className="border border-t-0 border-rule">
          <div className="h-16 border-b-2 border-rule bg-tint" />
          <div className="h-[10.5rem] bg-tint" />
          <div className="h-[2.875rem] border-t-2 border-rule" />
        </div>
      </div>
      <div className="flex flex-wrap gap-7">
        <div className="h-52 flex-[999_1_35rem] border-t-[3px] border-rule bg-tint" />
        <div className="h-52 flex-[1_1_22.5rem] border-t-[3px] border-rule bg-tint" />
      </div>
    </Loading>
  );
}

/** The days with something on, as the rows of a list. */
function ledgerDays(
  range: DayRange,
  games: readonly Game[],
  events: readonly EventDays[],
  eventsOnFirstDayOnly = false,
): LedgerDay[] {
  const byDay = gamesByDay(games);
  const days: LedgerDay[] = [];
  for (const key of dayKeys(range)) {
    const dayGames = byDay.get(key) ?? [];
    const dayEvents = eventsOnFirstDayOnly
      ? events.filter((entry) => entry.first === key)
      : eventsOn(events, key);
    if (dayGames.length > 0 || dayEvents.length > 0) {
      days.push({ key, games: dayGames, events: dayEvents });
    }
  }
  return days;
}

/**
 * The followed teams' games on a calendar: a week of day columns holding
 * score cards (the default), a month as a grid of days, or the same range
 * as a plain list; under them what comes next and a legend of what is
 * followed. Golf tournaments and race weekends, which are not games, run
 * across their days as bars.
 */
export default function CalendarView() {
  const [view, setView] = useState<CalendarViewId>("week");
  // What previous and next move by. Week and Month set it; the Agenda lists
  // whichever range was on screen when it was opened.
  const [unit, setUnit] = useState<CalendarUnit>("week");
  // A day inside the range the user went to, or null while the view follows
  // today (see `resolveRange`): a kiosk left on "this week" moves to the
  // new week by itself, and a week somebody navigated to stays put.
  const [anchor, setAnchor] = useState<string | null>(null);
  const [teamFilter, setTeamFilter] = useState<string>("all");
  // The day chosen in the month grid; null until one is, and then the
  // month opens on today (or its first day with anything on).
  const [pickedDay, setPickedDay] = useState<string | null>(null);
  const [openGame, setOpenGame] = useState<Game | null>(null);
  const [openEventId, setOpenEventId] = useState<string | null>(null);
  const [livePoll, setLivePoll] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // Set by the Today button: once the week is drawn, bring today into view
  // where the week is a list down the page.
  const [jumpPending, setJumpPending] = useState(false);
  const [topPending, setTopPending] = useState(false);

  const showing = useIsActiveTab("calendar");
  const openManageTeams = useManageTeams();
  const reducedMotion = usePrefersReducedMotion();
  const { favoriteTeam } = useTheme();
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);

  // Days are the viewer's: a column is a local day, the same zone the
  // start times on its cards are written in.
  const now = useNow(CLOCK_MS);
  const todayKey = localKeyFromDate(now);
  const range = resolveRange(unit, anchor, todayKey);
  const fetched = fetchWindow(range);
  const upWindow = comingUpWindow(range, todayKey);
  const upFetched = fetchWindow(upWindow);

  const teamsQuery = useTeams();
  // The payload the score line already polls: reading it costs no request,
  // and it is what turns a card live.
  const todayQuery = useToday();
  const scheduleQuery = useSchedule(
    fetched.start,
    fetched.end,
    undefined,
    livePoll && showing ? { refetchInterval: LIVE_REFRESH_MS } : undefined,
  );
  const eventsQuery = useEvents(fetched.start, fetched.end);
  // The days after the range, for "Coming up". For the week on screen this
  // is next week's own request, so pressing Next finds it already loaded.
  const upQuery = useSchedule(upFetched.start, upFetched.end);
  const upEventsQuery = useEvents(upFetched.start, upFetched.end);
  // A tournament's days are fixed by the server (and already written into
  // the .ics feed) in ITS timezone, so the bars are dated against that
  // rather than the viewer's.
  const serverZone = useMeta().data?.timezone;

  const teams = teamsQuery.data?.teams ?? NO_TEAMS;
  const leagues = teamsQuery.data?.leagues ?? NO_LEAGUES;
  const filter = teamFilter === "all" ? null : teamFilter;

  const leaguesById = useMemo(() => {
    const map: Record<string, League> = {};
    for (const league of leagues) map[league.id] = league;
    return map;
  }, [leagues]);

  const teamColors = useMemo(() => {
    const map: Record<string, string> = {};
    for (const team of teams) {
      if (team.color) map[team.id] = team.color;
    }
    return map;
  }, [teams]);

  // The follows the two controls below can actually serve. Both are about
  // GAMES, and a leaderboard-sport follow (a golfer, a driver) has none:
  // its tournaments are Events, which belong to no team. A per-team `.ics`
  // feed for one comes back empty forever, and picking one in the team
  // filter matches neither side of any game while hiding the bars as well.
  // So the row is dropped once, here, and each control notes the absence.
  // See `calendar/gameSideFollows.ts`.
  const gameSideTeams = useMemo(
    () => teams.filter((team) => appearsOnGameSide(team, leaguesById)),
    [teams, leaguesById],
  );
  const omitsLeaderboardFollows = gameSideTeams.length < teams.length;
  const followsLeaderboards = leagues.some((league) =>
    LEADERBOARD_SPORTS.has(league.sport),
  );
  // Whether anything followed plays games at all. Somebody who follows only
  // golfers or drivers has none, ever: the game counters would be three
  // zeros for good, so they are not drawn.
  const followsGames =
    gameSideTeams.length > 0 ||
    leagues.some(
      (league) => league.follow_all && !LEADERBOARD_SPORTS.has(league.sport),
    );

  const teamOptions: SelectOption[] = useMemo(
    () => [
      { id: "all", label: "All teams" },
      ...gameSideTeams.map((team) => ({
        id: team.id,
        label: team.name,
        abbreviation: team.abbreviation,
        color: team.color,
      })),
    ],
    [gameSideTeams],
  );

  // The schedule with the live copies laid over it, cut to the range.
  const freshGames = todayQuery.data?.games ?? NO_GAMES;
  const freshEvents = todayQuery.data?.events ?? NO_EVENTS;
  const schedule = scheduleQuery.data;
  const allGames = useMemo(
    () => overlayLatest(schedule ?? NO_GAMES, freshGames),
    [schedule, freshGames],
  );
  const rangeFirst = range.first;
  const rangeLast = range.last;
  const rangeGames = useMemo(
    () => gamesInRange(allGames, { first: rangeFirst, last: rangeLast }),
    [allGames, rangeFirst, rangeLast],
  );
  const games = useMemo(
    () =>
      filter === null
        ? rangeGames
        : rangeGames.filter((game) => playsIn(game, filter)),
    [rangeGames, filter],
  );

  // Tournaments across their days. Every one that touches the range, for
  // the legend; none on the grid while filtering to one team: an event has
  // no team side, so no team's calendar owns it (the rule the per-team .ics
  // feed follows too).
  const rangeEvents = useMemo(
    () =>
      eventDays(
        overlayLatestEvents(eventsQuery.data ?? NO_EVENTS, freshEvents),
        serverZone,
      ).filter((entry) => entry.last >= rangeFirst && entry.first <= rangeLast),
    [eventsQuery.data, freshEvents, serverZone, rangeFirst, rangeLast],
  );
  const events = filter === null ? rangeEvents : [];

  const upGames = useMemo(
    () => overlayLatest(upQuery.data ?? NO_GAMES, freshGames),
    [upQuery.data, freshGames],
  );
  const upFirst = upWindow.first;
  const upLast = upWindow.last;
  const up = useMemo(
    () => comingUp(upGames, { first: upFirst, last: upLast }, filter),
    [upGames, upFirst, upLast, filter],
  );
  const upEvents = useMemo(
    () =>
      filter === null
        ? comingUpEvents(
            eventDays(
              overlayLatestEvents(upEventsQuery.data ?? NO_EVENTS, freshEvents),
              serverZone,
            ),
            { first: upFirst, last: upLast },
          )
        : [],
    [upEventsQuery.data, freshEvents, serverZone, upFirst, upLast, filter],
  );

  // The month's chosen day: the one picked while it is in the month on
  // screen, otherwise where the month opens.
  const chosenDay =
    pickedDay !== null && holdsDay(range, pickedDay)
      ? pickedDay
      : defaultDay(range, todayKey, games, events);

  // Forecasts for the games drawn in full: the range's (a month's grid has
  // no room for one, so there it is the chosen day's) and the rows of
  // "Coming up". Two batches, as the two lists are two requests: the first
  // list's forecasts are not asked for again when the second arrives.
  const weatherIds = useMemo(
    () =>
      weatherGameIds(
        view === "month"
          ? games.filter((game) => gameDay(game) === chosenDay)
          : games,
      ),
    [view, games, chosenDay],
  );
  const upWeatherIds = useMemo(() => weatherGameIds(up.shown), [up.shown]);
  const weatherQuery = useScheduleWeather(weatherIds);
  const upWeatherQuery = useScheduleWeather(upWeatherIds);
  // When each batch was last asked for, answered or not: a batch that keeps
  // failing is tried again on the same beat, not on every tick.
  const weatherAskedAt = Math.max(
    weatherQuery.dataUpdatedAt,
    weatherQuery.errorUpdatedAt,
  );
  const upWeatherAskedAt = Math.max(
    upWeatherQuery.dataUpdatedAt,
    upWeatherQuery.errorUpdatedAt,
  );
  const weather = useMemo(
    () => ({ ...upWeatherQuery.data, ...weatherQuery.data }),
    [weatherQuery.data, upWeatherQuery.data],
  );

  const sideColors = useMemo(
    () => ({ byTeam: teamColors, byName: colorsByName([...allGames, ...upGames]) }),
    [teamColors, allGames, upGames],
  );

  // The leaderboard modal renders straight from the already-loaded event,
  // resolved from live data each render, so a refetch that drops the
  // tournament closes the modal instead of stranding stale rows.
  const openEvent =
    openEventId === null
      ? undefined
      : [...rangeEvents, ...upEvents].find(
          (entry) => entry.event.id === openEventId,
        )?.event;

  // ── Keeping it true while it stays on screen ──────────────────────────

  // A range that has come to hold today is "this week": from then on it
  // follows today, like a view that was never moved.
  useEffect(() => {
    setAnchor((prev) => settleAnchor(unit, prev, todayKey));
  }, [unit, todayKey]);

  // While a game on screen is in progress the schedule is read on a beat.
  const live = hasLiveGame(games);
  useEffect(() => {
    setLivePoll(live);
  }, [live]);

  const refetchSchedule = scheduleQuery.refetch;
  const refetchEvents = eventsQuery.refetch;
  const refetchUp = upQuery.refetch;
  const refetchUpEvents = upEventsQuery.refetch;

  // A game that was live a moment ago and is not any more has just ended.
  // The news came from the Today payload; read the schedule once so its
  // own copy holds the final score too, and still does after midnight when
  // Today has moved on to another day.
  const liveIds = games
    .filter((game) => game.phase === "in_progress")
    .map((game) => game.id)
    .join(",");
  const lastLiveIds = useRef("");
  useEffect(() => {
    const before = lastLiveIds.current.split(",").filter(Boolean);
    lastLiveIds.current = liveIds;
    const current = new Set(liveIds.split(","));
    if (before.some((id) => !current.has(id))) void refetchSchedule();
  }, [liveIds, refetchSchedule]);

  // Midnight: yesterday's games are no longer in the Today payload, so
  // what the schedule says about them is all there is. Read it afresh.
  const lastDay = useRef(todayKey);
  useEffect(() => {
    if (lastDay.current === todayKey) return;
    lastDay.current = todayKey;
    void refetchSchedule();
    void refetchEvents();
    void refetchUp();
    void refetchUpEvents();
  }, [todayKey, refetchSchedule, refetchEvents, refetchUp, refetchUpEvents]);

  // Coming back to the tab (the kiosk rotation does, every minute or so):
  // read again when what is held has grown old. See RETURN_REFRESH_MS.
  const wasShowing = useRef(showing);
  const scheduleReadAt = scheduleQuery.dataUpdatedAt;
  useEffect(() => {
    const returned = showing && !wasShowing.current;
    wasShowing.current = showing;
    if (!returned || Date.now() - scheduleReadAt < RETURN_REFRESH_MS) return;
    void refetchSchedule();
    void refetchEvents();
    void refetchUp();
    void refetchUpEvents();
  }, [showing, scheduleReadAt, refetchSchedule, refetchEvents, refetchUp, refetchUpEvents]);

  // A forecast changes through the day and the view stays up for days:
  // read a batch again once it is old. See WEATHER_REFRESH_MS.
  const refetchWeather = weatherQuery.refetch;
  const refetchUpWeather = upWeatherQuery.refetch;
  const weatherBusy = weatherQuery.isFetching;
  const upWeatherBusy = upWeatherQuery.isFetching;
  const hasWeatherIds = weatherIds.length > 0;
  const hasUpWeatherIds = upWeatherIds.length > 0;
  const clock = now.getTime();
  useEffect(() => {
    if (!showing) return;
    const old = (askedAt: number) =>
      askedAt > 0 && clock - askedAt > WEATHER_REFRESH_MS;
    if (hasWeatherIds && !weatherBusy && old(weatherAskedAt)) {
      void refetchWeather();
    }
    if (hasUpWeatherIds && !upWeatherBusy && old(upWeatherAskedAt)) {
      void refetchUpWeather();
    }
  }, [
    showing,
    clock,
    hasWeatherIds,
    hasUpWeatherIds,
    weatherBusy,
    upWeatherBusy,
    weatherAskedAt,
    upWeatherAskedAt,
    refetchWeather,
    refetchUpWeather,
  ]);

  // Moving the range re-queries /api/events for the new window, so a
  // tournament picked in the old one is no longer on the grid: drop the
  // selection with the range that produced it. Without this the id lingers
  // and the modal pops back open by itself the moment the user navigates
  // to a window that happens to contain that tournament again.
  useEffect(() => {
    setOpenEventId(null);
  }, [rangeFirst, rangeLast]);

  // A filtered-to team can leave the options after it was picked:
  // unfollowed elsewhere, or recognized as a leaderboard follow only once
  // its league arrives. The Select falls back to displaying "All teams"
  // when its value matches nothing, so leaving the id set would show an
  // unfiltered control over a grid still filtered to a team that isn't
  // listed. Fall back to "all" so the two agree.
  useEffect(() => {
    setTeamFilter((prev) =>
      prev === "all" || teamOptions.some((option) => option.id === prev)
        ? prev
        : "all",
    );
  }, [teamOptions]);

  const jumpToToday = () => {
    const heading = document.getElementById(dayHeadingId(todayKey));
    if (heading === null) return;
    heading.scrollIntoView({
      block: "start",
      behavior: reducedMotion ? "auto" : "smooth",
    });
    heading.focus({ preventScroll: true });
  };
  // After the Today button: where the week is a list down the page, today
  // may be several days below the fold. This week may have to be fetched
  // first (a skeleton stands in for the grid), so the jump waits for the
  // grid; it is dropped when the view or the range moves on meanwhile.
  const weekHoldsToday = unit === "week" && holdsDay(range, todayKey);
  const scheduleLoaded = scheduleQuery.data !== undefined;
  useEffect(() => {
    if (!jumpPending) return;
    if (view !== "week" || !weekHoldsToday) {
      setJumpPending(false);
      return;
    }
    const grid = rootRef.current?.querySelector('[data-calendar-grid="week"]');
    if (!grid) return;
    setJumpPending(false);
    if (getComputedStyle(grid).display === "grid") return;
    jumpToToday();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpPending, scheduleLoaded, view, weekHoldsToday]);

  // ── Controls ──────────────────────────────────────────────────────────

  const step = (steps: number) =>
    setAnchor(stepAnchor(unit, anchor, todayKey, steps));
  const goToday = () => {
    setAnchor(null);
    setPickedDay(null);
    if (view === "week") setJumpPending(true);
  };
  // The week on screen when the month was opened: Week, Month and back
  // returns to it (see `weekFromMonth`).
  const weekLeft = useRef<string | null>(null);
  const changeView = (next: CalendarViewId) => {
    if (next !== "agenda" && next !== unit) {
      if (next === "month") {
        weekLeft.current = range.first;
        setAnchor(anchorForUnit("month", "week", anchor, todayKey));
      } else {
        setAnchor(weekFromMonth(range, pickedDay, weekLeft.current, todayKey));
      }
      setUnit(next);
    }
    setView(next);
  };
  const openWeekOf = (dayKey: string) => {
    setUnit("week");
    setView("week");
    setAnchor(settleAnchor("week", dayKey, todayKey));
    setTopPending(true);
  };
  // After "Show next week" under Coming up: the week it opened is at the
  // top of the page, a screen or more above the button that was pressed
  // (which may itself be gone). Go there, and hand the keyboard to the view.
  useEffect(() => {
    if (!topPending) return;
    setTopPending(false);
    rootRef.current?.scrollIntoView({
      block: "start",
      behavior: reducedMotion ? "auto" : "smooth",
    });
    document.getElementById(panelId)?.focus({ preventScroll: true });
  }, [topPending, reducedMotion, panelId]);

  // Manual refresh: re-read the stored games and tournaments (and the
  // games' forecasts) on demand. The backend's own schedule sync still runs
  // on its daily cron; this re-reads whatever it currently has. The button
  // tracks the content fetches; the weather batch is a slower, secondary
  // enhancement, so it shouldn't keep the button busy.
  const refresh = () => {
    if (refreshing) return;
    setRefreshing(true);
    if (weatherIds.length > 0) void weatherQuery.refetch();
    if (upWeatherIds.length > 0) void upWeatherQuery.refetch();
    void Promise.allSettled([
      refetchSchedule(),
      refetchEvents(),
      refetchUp(),
      refetchUpEvents(),
    ]).then(() => setRefreshing(false));
  };

  // ── What the page says ────────────────────────────────────────────────

  const phrase = rangePhrase(unit, range, todayKey);
  const counts = tally(games);
  const off = offNote(counts);
  // "Games", or "Matches" / "Bouts" when that is all the range holds (or,
  // in an empty range, all that is followed). One word for the whole page:
  // the counters' name, an empty day, the empty week's sentence.
  const counted = wordsForRange(games, leagues).games;
  const countedAs = counted.charAt(0).toUpperCase() + counted.slice(1);
  const label = rangeLabel(unit, range);
  const loaded = schedule !== undefined;
  const filterTeam = filter === null ? undefined : teams.find((team) => team.id === filter);

  const head = (controls: boolean): ReactNode => (
    <header className="flex flex-wrap items-end gap-x-6 gap-y-4">
      <h1 className="bs-title">Calendar</h1>
      {controls && (
        <div className="flex min-w-0 flex-[1_1_35rem] flex-wrap items-center gap-x-3 gap-y-2">
          <div className="flex min-w-0 flex-[1_1_100%] flex-wrap items-center gap-x-2.5 gap-y-2">
            {/* One line, whatever the width: previous, the range, next.
                Where it stands must not depend on how long the range's
                name is ("May 2026", "Mar 30 – Apr 5, 2026"), or the
                buttons after it and the rows under it move from under
                the finger at every step and every change of view. On a
                phone the line is the full width, the name centred between
                the two buttons; from there up the name has a slot as wide
                as the longest week of a year. */}
            <div className="flex min-w-0 items-center gap-2.5 max-sm:flex-[1_1_100%]">
              <Button icon aria-label={`Previous ${unit}`} onClick={() => step(-1)}>
                <Chevron direction="left" />
              </Button>
              <p
                aria-live="polite"
                className="m-0 min-w-0 px-1 text-center font-condensed text-[clamp(1.125rem,5.6vw,1.875rem)] font-bold uppercase leading-none tracking-[0.02em] max-sm:flex-1 sm:min-w-[calc(8em+0.5rem)]"
              >
                {label}
              </p>
              <Button icon aria-label={`Next ${unit}`} onClick={() => step(1)}>
                <Chevron direction="right" />
              </Button>
            </div>
            <Button onClick={goToday}>Today</Button>
            {/* Drawn before the range has arrived too, with dashes for
                its figures: the strip is a row of its own on a phone, and
                everything under it would jump up and back at each step. */}
            {followsGames && (
              <div
                role="group"
                aria-label={`${countedAs} ${phrase}`}
                aria-busy={!loaded && !scheduleQuery.isError}
                className="ml-auto flex min-w-0 max-w-full flex-col items-end gap-1.5 max-sm:ml-0 max-sm:flex-[1_1_100%] max-sm:items-start"
              >
                <div className="max-w-full overflow-x-clip font-condensed uppercase">
                  <div className="-ml-[calc(0.75rem+2px)] flex flex-wrap items-stretch gap-y-2">
                    <Count value={loaded ? counts.final : null} label="Final" />
                    <Count value={loaded ? counts.live : null} label="Live" live />
                    <Count value={loaded ? counts.upcoming : null} label="Upcoming" />
                  </div>
                </div>
                {/* In none of the three; said only when there are any. */}
                {loaded && off !== null && (
                  <p className="m-0 text-sm font-medium leading-none text-ink-2">
                    {off}
                  </p>
                )}
              </div>
            )}
          </div>

          <Segmented
            kind="tabs"
            joined
            label="Calendar view"
            value={view}
            onChange={changeView}
            options={VIEWS.map((entry) => ({ ...entry, controls: panelId }))}
          />
          {teamOptions.length > 1 && (
            <Select
              options={teamOptions}
              value={teamFilter}
              onChange={setTeamFilter}
              ariaLabel="Filter by team"
            />
          )}
          {/* On the second line, so the first is the mockup's own (which
              has no Refresh) and still fits beside the title at 1280px. */}
          <Button
            icon
            onClick={refresh}
            aria-disabled={refreshing}
            aria-label={refreshing ? "Refreshing games" : "Refresh games"}
            title="Refresh games"
            // Busy is drawn as the set-back frame a disabled button
            // has, so it shows with motion turned off too; the button
            // stays focusable, which a disabled one would not.
            className={refreshing ? "cursor-progress border-rule text-muted" : undefined}
          >
            <RefreshIcon size={20} spinning={refreshing} />
          </Button>
          <div className="ml-auto flex max-w-full">
            <SubscribeMenu
              teams={gameSideTeams}
              omitsLeaderboardFollows={omitsLeaderboardFollows}
            />
          </div>
          {omitsLeaderboardFollows && teamOptions.length > 1 && (
            <p className="m-0 flex-[1_1_100%] text-sm text-ink-2">
              A tournament belongs to no team, so those follows aren&rsquo;t
              listed — All teams is the view that shows them.
            </p>
          )}
        </div>
      )}
    </header>
  );

  // ── States ────────────────────────────────────────────────────────────

  if (teamsQuery.isError && !teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        {head(false)}
        <Notice
          title="Calendar"
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
        {head(false)}
        <Skeleton label="Loading the calendar" />
      </div>
    );
  }
  if (teams.length === 0 && !leagues.some((league) => league.follow_all)) {
    return (
      <div className="flex flex-col gap-7">
        {head(false)}
        <Notice
          title="Calendar"
          headline="Nothing followed yet"
          action={{ label: "Manage teams", onClick: openManageTeams }}
        >
          Follow a team or a competition to see its games here.
        </Notice>
      </div>
    );
  }

  const context: RowContext = {
    todayKey,
    teamFilter: filter,
    leaguesById,
    weather,
    onOpenGame: setOpenGame,
    onOpenEvent: setOpenEventId,
  };
  // An empty range is "nothing stored", which is all the data can say: the
  // backend keeps about a week back and six weeks ahead.
  const beyond = range.first > dayKeyOffset(todayKey, NEXT_UP_DAYS);
  const empty = {
    headline: "Nothing on the calendar",
    detail:
      (filterTeam !== undefined
        ? `Nothing is stored for ${filterTeam.name} ${phrase}.`
        : `No ${counted} are stored for ${phrase}.`) +
      (beyond
        ? " The schedule reaches about six weeks ahead; later dates fill in as they come into range."
        : ""),
  };
  const caption =
    rangeCaption(
      teams,
      leagues,
      games,
      filter,
      favoriteTeam?.id ?? null,
      unit,
    ) ?? undefined;
  const upCaption = rangeCaption(
    teams,
    leagues,
    up.all,
    filter,
    favoriteTeam?.id ?? null,
    "week",
  );
  const title = rangeTitle(unit, range, todayKey);
  const holdsToday = holdsDay(range, todayKey);

  // "Coming up" is two requests. Its games decide its state; but a list
  // with no game in it is only "nothing upcoming" once the tournaments are
  // known too: somebody who follows only a golfer would otherwise read
  // that nothing is stored while the request for it failed or is still out.
  const awaited =
    upQuery.data === undefined
      ? upQuery
      : up.shown.length === 0 &&
          followsLeaderboards &&
          filter === null &&
          upEventsQuery.data === undefined
        ? upEventsQuery
        : null;
  const upState: ComponentProps<typeof ComingUp>["state"] =
    awaited === null
      ? { kind: "ready" }
      : awaited.isError
        ? {
            kind: "failed",
            detail: errorText(awaited.error),
            onRetry: () => void awaited.refetch(),
          }
        : { kind: "loading" };

  let body: ReactNode;
  if (scheduleQuery.isError && !loaded) {
    body = (
      <Notice
        title={title}
        alert
        headline="Couldn’t load the calendar"
        action={{ label: "Retry", onClick: () => void scheduleQuery.refetch() }}
      >
        {`The request for ${label} failed${errorText(scheduleQuery.error)}.`}
      </Notice>
    );
  } else if (!loaded) {
    body = <Skeleton label={`Loading ${label}`} />;
  } else {
    const lower = (
      <div className="flex flex-wrap items-stretch gap-7">
        <ComingUp
          days={ledgerDays(upWindow, up.shown, upEvents, true)}
          window={upWindow}
          note={[comingUpTitle(upWindow, todayKey), upCaption]
            .filter((part): part is string => part !== null)
            .join(" · ")}
          foot={comingUpFoot(up, upWindow, todayKey)}
          onOpenWeek={(week) => openWeekOf(week.first)}
          state={upState}
          teamName={filterTeam?.name ?? null}
          context={context}
        />
        <Legend
          rows={legendRows(
            teams,
            leagues,
            rangeGames,
            // A list that has not arrived, or failed to, is not an empty
            // one: the athletes' rows say which instead of "Not on a
            // leaderboard".
            eventsQuery.data !== undefined
              ? rangeEvents.map((entry) => entry.event)
              : eventsQuery.isError
                ? "failed"
                : "loading",
            phrase,
          )}
          resultsLabel={
            holdsToday ? `Results so far ${phrase}` : `Results ${phrase}`
          }
        />
      </div>
    );
    if (view === "week") {
      body = (
        <>
          <section aria-label={rangeSpoken(unit, range)} className="@container/week">
            <SectionHead
              title={title}
              note={caption}
              aside={
                holdsToday && (games.length > 0 || events.length > 0) ? (
                  <button
                    type="button"
                    onClick={jumpToToday}
                    className="bs-link cursor-pointer @[60rem]/week:hidden"
                  >
                    Jump to today
                  </button>
                ) : undefined
              }
            />
            <WeekGrid
              range={range}
              todayKey={todayKey}
              games={games}
              events={events}
              teamFilter={filter}
              leaguesById={leaguesById}
              weather={weather}
              onOpenGame={setOpenGame}
              onOpenEvent={setOpenEventId}
              empty={empty}
              gamesWord={counted}
              showResultKey={gameSideTeams.length > 0}
            />
          </section>
          {lower}
        </>
      );
    } else if (view === "month") {
      body = (
        <>
          <div className="flex flex-wrap items-start gap-7">
            <section
              aria-label={rangeSpoken(unit, range)}
              // 64rem is where a day's cell can hold a pair of blocks and
              // a time; with less than that beside the day's list, the
              // list goes under the grid instead.
              className="min-w-0 flex-[999_1_64rem]"
            >
              <SectionHead title={title} note={caption} />
              <MonthGrid
                month={range}
                todayKey={todayKey}
                games={games}
                events={events}
                selected={chosenDay}
                onSelect={setPickedDay}
              />
            </section>
            <DayPanel
              day={{
                key: chosenDay,
                games: gamesByDay(games).get(chosenDay) ?? [],
                events: eventsOn(events, chosenDay),
              }}
              // A busy month's list goes under the grid at full width:
              // beside it, ten games are a column twice the grid's height
              // with blank page under the grid.
              under={busiestDayRows(range, games, events) > DAY_LIST_BESIDE_MAX}
              context={context}
            />
          </div>
          {lower}
        </>
      );
    } else {
      body = (
        <>
          <Agenda
            days={ledgerDays(range, games, events)}
            label={`Agenda, ${rangeSpoken(unit, range)}`}
            note={[title, caption]
              .filter((part): part is string => part !== undefined)
              .join(" · ")}
            empty={empty}
            context={context}
          />
          {lower}
        </>
      );
    }
  }

  return (
    <SideColorsContext.Provider value={sideColors}>
      <div ref={rootRef} className="flex flex-col gap-7">
        {scheduleQuery.isError && loaded && (
          <StaleBanner
            message="Connection lost — showing the last loaded schedule."
            onRetry={() => void scheduleQuery.refetch()}
          />
        )}

        {head(true)}

        {eventsQuery.isError &&
          !eventsQuery.data &&
          followsLeaderboards &&
          filter === null && (
            <p role="alert" className="m-0 flex flex-wrap items-center gap-x-3 font-medium text-alert">
              Couldn&rsquo;t load tournaments{errorText(eventsQuery.error)}.
              <button
                type="button"
                onClick={() => void eventsQuery.refetch()}
                className="bs-link min-w-11 cursor-pointer justify-center"
              >
                Retry
              </button>
            </p>
          )}

        <div
          role="tabpanel"
          id={panelId}
          // Focusable by script only (see `openWeekOf`), and like <main>
          // it draws no ring: it is the whole page under the head.
          tabIndex={-1}
          aria-label={`${VIEWS.find((entry) => entry.value === view)?.label ?? ""} view`}
          className="flex flex-col gap-7 outline-none"
        >
          {body}
        </div>

        {openGame !== null && (
          <GameDetailModal
            gameId={openGame.id}
            fallbackGame={openGame}
            onClose={() => setOpenGame(null)}
          />
        )}
        {openEvent !== undefined && (
          <EventLeaderboardModal
            event={openEvent}
            followedColor={teamColors}
            onClose={() => setOpenEventId(null)}
          />
        )}
      </div>
    </SideColorsContext.Provider>
  );
}
