import { parseLocalDateKey } from "../lib/time";
import { useMemo, useState } from "react";
import { useGameOdds, useTeams, useToday } from "../hooks";
import GameCard from "../components/GameCard";
import EventLeaderboardModal from "../components/EventLeaderboardModal";
import type { League, SportEvent } from "../types";

/**
 * Parse a "YYYY-MM-DD" key as a LOCAL calendar date. `new Date("YYYY-MM-DD")`
 * parses as UTC midnight, which shifts the day for anyone west of Greenwich —
 * so we split and use the local-time constructor instead.
 */
/** Score colored by sign: under par emerald, at/over par zinc. */
function eventScoreClass(score: string): string {
  const t = score.trim();
  if (t.startsWith("-")) return "text-emerald-400";
  if (t.startsWith("+")) return "text-zinc-300";
  return "text-zinc-200";
}

/**
 * Compact leaderboard-event card for the Today landing screen: tournament
 * name, current round, the top three, plus any followed golfer's line when
 * they're outside the top three. Followed golfers are accented by their
 * team color (a followed golfer is a single-member team).
 */
function EventCard({
  event,
  teamColors,
  onOpen,
}: {
  event: SportEvent;
  teamColors: Record<string, string>;
  onOpen: () => void;
}) {
  const top3 = event.leaderboard.slice(0, 3);
  const top3Ids = new Set(top3.map((row) => row.player_id));
  // Followed golfers not already shown in the top three.
  const followedExtra = event.leaderboard.filter(
    (row) =>
      row.player_id !== null &&
      teamColors[row.player_id] &&
      !top3Ids.has(row.player_id),
  );

  // First followed golfer's color accents the card border.
  const accent =
    event.leaderboard.find(
      (row) => row.player_id !== null && teamColors[row.player_id],
    )?.player_id ?? null;
  const accentColor = accent !== null ? teamColors[accent] : undefined;

  const live = event.phase === "in_progress";
  const roundText =
    event.round_label || (event.phase === "final" ? "Final" : "Upcoming");

  const renderRow = (
    row: SportEvent["leaderboard"][number],
    key: string | number,
  ) => {
    const color = row.player_id !== null ? teamColors[row.player_id] : undefined;
    const followed = color !== undefined;
    return (
      <div key={key} className="flex items-center gap-2 text-sm">
        <span className="w-7 shrink-0 tabular-nums text-xs text-zinc-500">
          {row.position_label || row.position}
        </span>
        {followed && (
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: color }}
            aria-hidden="true"
          />
        )}
        <span
          className={
            "min-w-0 flex-1 truncate " +
            (followed ? "font-semibold text-zinc-100" : "text-zinc-300")
          }
        >
          {row.name}
        </span>
        <span
          className={"shrink-0 tabular-nums font-semibold " + eventScoreClass(row.score)}
        >
          {row.score.trim() || "—"}
        </span>
        {row.detail !== null && (
          <span className="w-12 shrink-0 text-right text-xs tabular-nums text-zinc-500">
            {row.detail}
          </span>
        )}
      </div>
    );
  };

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open ${event.name} leaderboard`}
      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-800/40"
      style={
        accentColor !== undefined
          ? { borderLeftWidth: "3px", borderLeftColor: accentColor }
          : undefined
      }
    >
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-semibold text-zinc-100">
          {event.name}
        </span>
        {live ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">
            <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-red-500" />
            </span>
            <span className="uppercase tracking-wide">{roundText}</span>
          </span>
        ) : (
          <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
            {roundText}
          </span>
        )}
      </div>
      <div className="mt-2 space-y-1">
        {top3.length === 0 ? (
          <p className="text-xs text-zinc-500">Leaderboard not available yet.</p>
        ) : (
          top3.map((row, i) => renderRow(row, `top-${i}`))
        )}
        {followedExtra.length > 0 && (
          <div className="mt-1 space-y-1 border-t border-zinc-800/70 pt-1">
            {followedExtra.map((row, i) => renderRow(row, `followed-${i}`))}
          </div>
        )}
      </div>
    </button>
  );
}

function SkeletonGrid() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="flex items-end justify-between">
        <div className="space-y-2">
          <div className="h-6 w-40 rounded bg-zinc-800" />
          <div className="h-4 w-28 rounded bg-zinc-800/70" />
        </div>
        <div className="h-6 w-44 rounded-full bg-zinc-800/70" />
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <div
            key={i}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5"
          >
            <div className="space-y-2">
              <div className="h-5 w-3/4 rounded bg-zinc-800" />
              <div className="h-5 w-2/3 rounded bg-zinc-800" />
              <div className="mt-1 h-4 w-1/2 rounded bg-zinc-800/70" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Kiosk landing screen: every game on the local calendar day for followed
 * teams, dense and glanceable.
 */
export default function TodayView() {
  const todayQuery = useToday();
  const teamsQuery = useTeams();

  // The leaderboard-event id whose modal is open, or null. Golf has no
  // dedicated tab anymore — its leaderboards are reached by clicking an
  // event card here.
  const [openEventId, setOpenEventId] = useState<string | null>(null);

  const teamColors = useMemo(() => {
    const map: Record<string, string> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      if (team.color) map[team.id] = team.color;
    }
    return map;
  }, [teamsQuery.data]);

  // For whole-competition games (no followed team) GameCard labels the card
  // with the league name; supply the league lookup it needs.
  const leaguesById = useMemo(() => {
    const map: Record<string, League> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      map[league.id] = league;
    }
    return map;
  }, [teamsQuery.data]);

  // Odds + win-probability for the scheduled / live games on screen, fetched
  // in one batch and threaded into each card. (Hooks run before the early
  // returns below, so the ids are empty until today's data loads.)
  const oddsGameIds = useMemo(
    () =>
      (todayQuery.data?.games ?? [])
        .filter((g) => g.phase === "scheduled" || g.phase === "in_progress")
        .map((g) => g.id),
    [todayQuery.data],
  );
  const oddsByGame = useGameOdds(oddsGameIds).data ?? {};

  if (todayQuery.isPending || teamsQuery.isPending) {
    return <SkeletonGrid />;
  }

  // Full-screen error only when there is nothing to show. A failed
  // background refetch on a kiosk must NOT blank a perfectly good
  // dashboard — keep rendering the stale data with a slim warning.
  if (todayQuery.isError && !todayQuery.data) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3">
        <p className="min-w-0 truncate text-sm text-red-300">
          Couldn&rsquo;t load today&rsquo;s games
          {todayQuery.error instanceof Error && todayQuery.error.message
            ? ` — ${todayQuery.error.message}`
            : ""}
        </p>
        <button
          type="button"
          onClick={() => void todayQuery.refetch()}
          className="shrink-0 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!todayQuery.data) {
    return <SkeletonGrid />;
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

  const liveCount = games.filter((g) => g.phase === "in_progress").length;
  const upcomingCount = games.filter((g) => g.phase === "scheduled").length;
  const finalCount = games.filter((g) => g.phase === "final").length;

  // The open event resolved from live data, so a background refetch that drops
  // the tournament closes the modal instead of stranding stale rows.
  const openEvent =
    openEventId !== null
      ? (events.find((e) => e.id === openEventId) ?? null)
      : null;

  return (
    <div className="space-y-4">
      {stale && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-1.5">
          <p className="text-xs text-amber-400">
            Connection lost — showing the last loaded scores.
          </p>
          <button
            type="button"
            onClick={() => void todayQuery.refetch()}
            className="shrink-0 text-xs font-medium text-amber-300 underline-offset-2 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">{weekday}</h1>
          <p className="text-sm text-zinc-400">{longDate}</p>
        </div>
        {games.length > 0 && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-xs text-zinc-400">
            <span className={liveCount > 0 ? "font-medium text-red-400" : ""}>
              {liveCount} live
            </span>
            <span aria-hidden="true">·</span>
            <span>{upcomingCount} upcoming</span>
            <span aria-hidden="true">·</span>
            <span>{finalCount} final</span>
          </span>
        )}
      </header>

      {events.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Leaderboards
          </h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {events.map((event) => (
              <EventCard
                key={event.id}
                event={event}
                teamColors={teamColors}
                onOpen={() => setOpenEventId(event.id)}
              />
            ))}
          </div>
        </section>
      )}

      {games.length === 0 && events.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <p className="text-base font-medium text-zinc-300">No games today</p>
          <p className="mt-1 text-sm text-zinc-500">
            Check the Calendar tab for upcoming games.
          </p>
        </div>
      ) : (
        games.length > 0 && (
          <section className="space-y-2">
            {events.length > 0 && (
              <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Games
              </h2>
            )}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {games.map((game) => (
                <GameCard
                  key={game.id}
                  game={game}
                  teamColors={teamColors}
                  leaguesById={leaguesById}
                  odds={oddsByGame[game.id]}
                />
              ))}
            </div>
          </section>
        )
      )}

      {openEvent !== null && (
        <EventLeaderboardModal
          event={openEvent}
          followedColor={teamColors}
          onClose={() => setOpenEventId(null)}
        />
      )}
    </div>
  );
}
