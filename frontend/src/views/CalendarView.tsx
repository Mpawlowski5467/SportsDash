import { isLightColor } from "../lib/color";
import { localKeyFromDate } from "../lib/time";
import { useEffect, useMemo, useRef, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import type { DatesSetArg, EventClickArg, EventInput } from "@fullcalendar/core";
import { api } from "../api";
import { useSchedule, useScheduleWeather, useTeams } from "../hooks";
import type { Game, League, Sport, Team } from "../types";
import { leagueFallbackColor } from "../components/GameCard";
import GameDetailModal from "../components/GameDetailModal";
import { wmoEmoji } from "../components/WeatherInline";
import Select, { type SelectOption } from "../components/Select";
import "../calendar.css";

/**
 * Outdoor sports whose scheduled games can carry a venue forecast glyph.
 * Mirrors the backend's WEATHER_SPORTS (minus golf, which isn't a Game).
 */
const OUTDOOR_SPORTS: ReadonlySet<Sport> = new Set<Sport>([
  "soccer",
  "baseball",
  "football",
  "tennis",
]);

/**
 * `webcal://` subscription URL for the live calendar feed. `webcal://`
 * hands the URL to the OS calendar app (Apple/Google) which keeps polling
 * it, so subscribing stays live as games are added. Host comes from the
 * current page so it works behind the dev proxy and in production alike.
 */
function webcalUrl(teamId?: string): string {
  const base = `webcal://${window.location.host}/api/calendar.ics`;
  return teamId !== undefined
    ? `${base}?team_id=${encodeURIComponent(teamId)}`
    : base;
}

/**
 * "Subscribe" dropdown: a live `webcal://` feed for all followed teams,
 * plus one per followed team. Each row opens the feed in the OS calendar
 * app on click and offers a copy-to-clipboard button. Closes on outside
 * click or Escape.
 */
function SubscribeMenu({ teams }: { teams: Team[] }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const copy = (key: string, url: string) => {
    // Clipboard API can be unavailable on insecure origins; swallow and
    // just skip the "Copied" flash rather than throwing.
    navigator.clipboard
      ?.writeText(url)
      .then(() => {
        setCopied(key);
        window.setTimeout(
          () => setCopied((prev) => (prev === key ? null : prev)),
          1_500,
        );
      })
      .catch(() => undefined);
  };

  const rows: { key: string; label: string; teamId?: string }[] = [
    { key: "all", label: "All followed teams" },
    ...teams.map((team) => ({
      key: team.id,
      label: team.name,
      teamId: team.id,
    })),
  ];

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
      >
        Subscribe
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Subscribe to calendar"
          className="absolute right-0 top-full z-50 mt-1 w-72 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 py-1 shadow-xl shadow-black/40"
        >
          <p className="px-3 py-1.5 text-[11px] leading-snug text-zinc-500">
            Subscribe in Apple or Google Calendar to keep games updating
            automatically.
          </p>
          {rows.map((row) => {
            const url = webcalUrl(row.teamId);
            return (
              <div
                key={row.key}
                className="flex items-center gap-2 px-2 py-0.5"
              >
                <a
                  href={url}
                  className="min-w-0 flex-1 truncate rounded px-1.5 py-1.5 text-left text-sm text-zinc-200 transition-colors hover:bg-zinc-800"
                  title={`Subscribe: ${row.label}`}
                >
                  {row.label}
                </a>
                <button
                  type="button"
                  onClick={() => copy(row.key, url)}
                  title="Copy subscription link"
                  aria-label={`Copy subscription link for ${row.label}`}
                  className="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
                >
                  {copied === row.key ? "Copied" : "Copy"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * Local "YYYY-MM-DD" key from a Date. Built from the local date parts —
 * `toISOString()` would shift the day across the UTC boundary.
 */
function eventColor(game: Game, teamColors: Record<string, string>): string {
  for (const teamId of game.followed_team_ids) {
    const color = teamColors[teamId];
    if (color) return color;
  }
  // Whole-competition games (no followed team) fall back to a per-league
  // hue rather than a flat gray, matching the cards' accent.
  return leagueFallbackColor(game.sport);
}

/**
 * The backend interprets start/end as calendar days in ITS configured
 * timezone; these keys are computed in the BROWSER's. When the two
 * differ, an unpadded window can drop games on the visible range's edge
 * days — so always ask for one extra day on each side. FullCalendar
 * only renders events that fall inside the visible cells, so the
 * padding is invisible.
 */
function initialRange(): { start: string; end: string } {
  const now = new Date();
  return {
    start: localKeyFromDate(new Date(now.getFullYear(), now.getMonth(), 0)),
    end: localKeyFromDate(new Date(now.getFullYear(), now.getMonth() + 1, 1)),
  };
}

/**
 * Month/week calendar of all followed-team games over the visible range,
 * colored per team, with an iCalendar export.
 */
export default function CalendarView() {
  const [range, setRange] = useState(initialRange);
  const [teamFilter, setTeamFilter] = useState<string>("all");
  // Which game's box-score modal is open (its id), or null. Clicking a
  // calendar event opens the same GameDetailModal the cards use.
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null);
  const teamsQuery = useTeams();
  const scheduleQuery = useSchedule(range.start, range.end);

  // Resolve a clicked event back to its full Game so the modal header can
  // render instantly (fallbackGame) while the detail request is in flight.
  const gamesById = useMemo(() => {
    const map: Record<string, Game> = {};
    for (const game of scheduleQuery.data ?? []) map[game.id] = game;
    return map;
  }, [scheduleQuery.data]);

  // Game-day forecasts for the outdoor scheduled games in view, fetched as a
  // single batch and shown as a glyph on the event. Pre-filtered to outdoor
  // scheduled games so the request stays small (the backend filters again).
  const weatherIds = useMemo(
    () =>
      (scheduleQuery.data ?? [])
        .filter(
          (game) =>
            game.phase === "scheduled" && OUTDOOR_SPORTS.has(game.sport),
        )
        .map((game) => game.id),
    [scheduleQuery.data],
  );
  const weatherQuery = useScheduleWeather(weatherIds);

  const teamOptions: SelectOption[] = useMemo(() => {
    const teams = teamsQuery.data?.teams ?? [];
    return [
      { id: "all", label: "All teams" },
      ...teams.map((team) => ({
        id: team.id,
        label: team.name,
        logoUrl: team.logo_url,
        color: team.color,
      })),
    ];
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

  const events = useMemo<EventInput[]>(
    () =>
      (scheduleQuery.data ?? [])
        .filter(
          (game) =>
            teamFilter === "all" ||
            game.home.team_id === teamFilter ||
            game.away.team_id === teamFilter,
        )
        .map((game) => {
        const color = eventColor(game, teamColors);
        const matchup = `${game.away.abbreviation ?? game.away.name} @ ${game.home.abbreviation ?? game.home.name}`;
        // Whole-competition games have no followed team; prefix the league
        // name so the event still says which competition it belongs to.
        const isWholeComp = game.followed_team_ids.length === 0;
        const leagueName = leaguesById[game.league_id]?.name;
        const baseTitle =
          isWholeComp && leagueName !== undefined
            ? `${leagueName}: ${matchup}`
            : matchup;
        // Prepend a game-day forecast glyph for outdoor scheduled games once
        // the weather batch resolves (absent until then / when unavailable).
        const forecast = weatherQuery.data?.[game.id];
        const title =
          forecast !== undefined
            ? `${wmoEmoji(forecast.code)} ${baseTitle}`
            : baseTitle;
        return {
          id: game.id,
          title,
          start: game.start_time,
          backgroundColor: color,
          borderColor: color,
          textColor: isLightColor(color) ? "#18181b" : "#fafafa",
        };
      }),
    [scheduleQuery.data, teamColors, leaguesById, teamFilter, weatherQuery.data],
  );

  // Manual refresh: re-pull the stored games (and their forecast glyphs) on
  // demand, rather than waiting for the 30s staleness / a tab revisit. The
  // backend's own schedule sync still runs on its daily cron; this just
  // re-reads whatever it currently has. The button tracks the GAMES fetch
  // only — the weather batch is a slower, secondary enhancement, so the
  // button shouldn't stay spinning waiting on forecast glyphs.
  const refreshing = scheduleQuery.isFetching;
  const handleRefresh = () => {
    scheduleQuery.refetch();
    weatherQuery.refetch();
  };

  const handleEventClick = (arg: EventClickArg) => {
    // Events carry the game id; FullCalendar would otherwise treat the
    // event as a link and navigate. Open the box-score modal instead.
    arg.jsEvent.preventDefault();
    setSelectedGameId(arg.event.id);
  };

  const handleDatesSet = (arg: DatesSetArg) => {
    // arg.end is exclusive; /api/schedule takes inclusive local days.
    // Pad one day on each side for the server-vs-browser timezone gap
    // (see initialRange): -1 from the visible start, and the exclusive
    // end used as-is IS the +1 padded inclusive end.
    const start = localKeyFromDate(
      new Date(arg.start.getFullYear(), arg.start.getMonth(), arg.start.getDate() - 1),
    );
    const end = localKeyFromDate(arg.end);
    setRange((prev) =>
      prev.start === start && prev.end === end ? prev : { start, end },
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Select
          options={teamOptions}
          value={teamFilter}
          onChange={setTeamFilter}
          ariaLabel="Filter by team"
        />
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            title="Refresh games"
            aria-label="Refresh games"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
              aria-hidden="true"
            >
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <path d="M21 3v6h-6" />
            </svg>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <SubscribeMenu teams={teamsQuery.data?.teams ?? []} />
          <a
            href={api.calendarIcsUrl}
            download
            className="shrink-0 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
          >
            Export .ics
          </a>
        </div>
      </div>

      <FullCalendar
        plugins={[dayGridPlugin, timeGridPlugin]}
        initialView="dayGridMonth"
        headerToolbar={{
          left: "prev,next today",
          center: "title",
          right: "dayGridMonth,timeGridWeek",
        }}
        height="auto"
        events={events}
        eventClick={handleEventClick}
        datesSet={handleDatesSet}
        eventTimeFormat={{
          hour: "numeric",
          minute: "2-digit",
          meridiem: "short",
        }}
      />

      {selectedGameId !== null && (
        <GameDetailModal
          gameId={selectedGameId}
          fallbackGame={gamesById[selectedGameId]}
          onClose={() => setSelectedGameId(null)}
        />
      )}
    </div>
  );
}
