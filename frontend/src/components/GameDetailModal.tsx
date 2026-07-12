import OddsSection from "./OddsSection";
import { oddsHasContent } from "../lib/odds";
import { abbrev } from "../lib/labels";
import { useEffect, useId } from "react";

import { useGameDetail } from "../hooks";
import { formatDateTime } from "../lib/time";
import type {
  Game,
  GameLineup,
  GameOdds,
  GameSummary,
  Performer,
  TeamStat,
  Weather,
} from "../types";
import LineupView from "./LineupView";
import PlayByPlayTimeline from "./PlayByPlayTimeline";
import Portal from "./Portal";
import StatusBadge from "./StatusBadge";
import WeatherInline from "./WeatherInline";
import WinProbChart from "./WinProbChart";

/**
 * On-demand box-score drill-down. Rendered as a full-screen dark overlay over
 * the dashboard; the line score and performers are fetched lazily (and kept
 * fresh while the game is live) via `useGameDetail`. Closes on ESC or a
 * backdrop click.
 *
 * The caller owns the open/closed state and simply mounts/unmounts this
 * component with the target `gameId`; mounting kicks off the fetch and
 * unmounting stops the polling. `fallbackGame` is the card's already-loaded
 * `Game` so the header renders instantly while the detail request is in
 * flight (the server echoes the same game back in `detail.game`).
 */
export default function GameDetailModal({
  gameId,
  fallbackGame,
  onClose,
}: {
  gameId: string;
  fallbackGame?: Game;
  onClose: () => void;
}) {
  const { data, isLoading, isError } = useGameDetail(gameId);
  const titleId = useId();

  // ESC to close, and lock background scroll while the overlay is open.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  const game = data?.game ?? fallbackGame;
  const summary = data?.summary ?? null;
  const weather = data?.weather ?? null;
  const lineup = data?.lineup ?? null;
  const odds = data?.odds ?? null;

  return (
    <Portal>
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b border-zinc-800 px-4 py-3">
          <div className="min-w-0 flex-1">
            {game !== undefined ? (
              <Matchup game={game} titleId={titleId} />
            ) : (
              <h2 id={titleId} className="text-base font-semibold text-zinc-100">
                Game details
              </h2>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 -mt-1 shrink-0 rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              className="h-4 w-4"
              aria-hidden="true"
            >
              <path d="M5 5l10 10M15 5L5 15" />
            </svg>
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {isLoading ? (
            <DetailSkeleton />
          ) : isError ? (
            <EmptyState message="Couldn't load this game." />
          ) : (
            <DetailBody
              summary={summary}
              lineup={lineup}
              weather={weather}
              odds={odds}
              game={game}
            />
          )}
        </div>
      </div>
    </div>
    </Portal>
  );
}

function hasSummaryContent(summary: GameSummary): boolean {
  return (
    summary.periods.length > 0 ||
    summary.performers.length > 0 ||
    summary.team_stats.length > 0 ||
    summary.home_total !== null ||
    summary.away_total !== null ||
    summary.win_probability.length > 1 ||
    summary.plays.length > 0
  );
}

function Matchup({ game, titleId }: { game: Game; titleId: string }) {
  return (
    <div className="flex flex-col gap-1">
      <h2
        id={titleId}
        className="truncate text-base font-semibold text-zinc-100"
      >
        {game.away.name} <span className="text-zinc-500">@</span>{" "}
        {game.home.name}
      </h2>
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <StatusBadge game={game} />
        <span className="truncate">{formatDateTime(game.start_time)}</span>
      </div>
    </div>
  );
}

/**
 * The modal body: shows whatever the game has — a box score (live/final), the
 * projected lineups (any phase, when rosters exist), and a venue forecast
 * (scheduled outdoor games), in that order. Falls back to a phase-appropriate
 * empty state when the game carries none of them.
 */
function DetailBody({
  summary,
  lineup,
  weather,
  odds,
  game,
}: {
  summary: GameSummary | null;
  lineup: GameLineup | null;
  weather: Weather | null;
  odds: GameOdds | null;
  game: Game | undefined;
}) {
  const hasSummary = summary !== null && hasSummaryContent(summary);
  const hasLineup =
    lineup !== null && (lineup.home !== null || lineup.away !== null);
  // Odds/win-prob are a pre-game and live artifact; never on a finished game.
  const hasOdds =
    odds !== null &&
    oddsHasContent(odds) &&
    game?.phase !== "final" &&
    game?.phase !== "postponed" &&
    game?.phase !== "canceled";

  if (!hasSummary && !hasLineup && !hasOdds && weather === null) {
    return (
      <EmptyState
        message={
          game?.phase === "scheduled"
            ? "Box score available after kickoff."
            : "Box score not available."
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      {hasOdds && game !== undefined && <OddsSection odds={odds} game={game} />}
      {hasSummary && <SummaryBody summary={summary} game={game} />}
      {hasLineup && game !== undefined && (
        <LineupView lineup={lineup} game={game} />
      )}
      {weather !== null && (
        <WeatherSection
          weather={weather}
          showNote={!hasSummary && !hasLineup}
        />
      )}
    </div>
  );
}

/**
 * Venue forecast block. The "appears once it kicks off" note only shows when
 * the forecast is the only content (no box score, no lineup yet).
 */
function WeatherSection({
  weather,
  showNote,
}: {
  weather: Weather;
  showNote: boolean;
}) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Venue forecast
      </h3>
      <div className="rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-3">
        <WeatherInline weather={weather} />
      </div>
      {showNote && (
        <p className="mt-2 text-xs text-zinc-500">
          Box score and stats appear once the game kicks off.
        </p>
      )}
    </div>
  );
}

function SummaryBody({
  summary,
  game,
}: {
  summary: GameSummary;
  game: Game | undefined;
}) {
  const awayName = game?.away.name ?? "Away";
  const homeName = game?.home.name ?? "Home";
  const awayLabel = game?.away.abbreviation ?? abbrev(awayName);
  const homeLabel = game?.home.abbreviation ?? abbrev(homeName);

  const homePerformers = summary.performers.filter((p) => p.side === "home");
  const awayPerformers = summary.performers.filter((p) => p.side === "away");

  return (
    <div className="space-y-5">
      {summary.win_probability.length > 1 && (
        <WinProbChart
          series={summary.win_probability}
          awayLabel={awayLabel}
          homeLabel={homeLabel}
        />
      )}

      {summary.periods.length > 0 && (
        <LineScore
          summary={summary}
          awayLabel={awayLabel}
          homeLabel={homeLabel}
        />
      )}

      {summary.team_stats.length > 0 && (
        <TeamStats
          stats={summary.team_stats}
          awayLabel={awayLabel}
          homeLabel={homeLabel}
        />
      )}

      {(awayPerformers.length > 0 || homePerformers.length > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <PerformerGroup title={awayName} performers={awayPerformers} />
          <PerformerGroup title={homeName} performers={homePerformers} />
        </div>
      )}

      {summary.plays.length > 0 && (
        <PlayByPlayTimeline plays={summary.plays} />
      )}
    </div>
  );
}

/**
 * Team-vs-team comparison: away value · label · home value, one row per
 * stat. Numeric percentage rows also draw a thin split bar so the balance
 * (e.g. possession) reads at a glance; non-numeric rows just show the values.
 */
function TeamStats({
  stats,
  awayLabel,
  homeLabel,
}: {
  stats: TeamStat[];
  awayLabel: string;
  homeLabel: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
        <span>{awayLabel}</span>
        <span>Team stats</span>
        <span>{homeLabel}</span>
      </div>
      <ul className="space-y-2">
        {stats.map((stat, index) => {
          const split = barSplit(stat.away, stat.home);
          return (
            <li key={`${stat.label}-${index}`} className="space-y-1">
              <div className="flex items-baseline justify-between gap-3 text-sm tabular-nums">
                <span className="font-semibold text-zinc-200">{stat.away}</span>
                <span className="text-xs text-zinc-500">{stat.label}</span>
                <span className="font-semibold text-zinc-200">{stat.home}</span>
              </div>
              {split !== null && (
                <div className="flex h-1 overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className="bg-amber-500/70"
                    style={{ width: `${split}%` }}
                  />
                  <div className="flex-1 bg-sky-500/60" />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Percentage of the bar that belongs to the AWAY side, when both values are
 * numeric and sum to something positive; null otherwise (so non-numeric
 * stats skip the bar).
 */
function barSplit(away: string, home: string): number | null {
  const a = parseFloat(away.replace(/[^0-9.]/g, ""));
  const h = parseFloat(home.replace(/[^0-9.]/g, ""));
  if (!Number.isFinite(a) || !Number.isFinite(h) || a + h <= 0) {
    return null;
  }
  return Math.round((a / (a + h)) * 100);
}

/**
 * Line-score grid: periods as columns, away/home as rows, with a totals
 * column. Horizontally scrollable so games with many periods (overtimes,
 * extra innings) don't overflow the panel.
 */
function LineScore({
  summary,
  awayLabel,
  homeLabel,
}: {
  summary: GameSummary;
  awayLabel: string;
  homeLabel: string;
}) {
  return (
    <div className="-mx-1 overflow-x-auto px-1">
      <table className="w-full border-collapse text-sm tabular-nums">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
            <th className="px-2 py-1.5 text-left font-medium">Team</th>
            {summary.periods.map((period, index) => (
              <th
                key={`${period.label}-${index}`}
                className="px-2 py-1.5 text-center font-medium"
              >
                {period.label}
              </th>
            ))}
            <th className="px-2 py-1.5 text-center font-semibold text-zinc-400">
              T
            </th>
          </tr>
        </thead>
        <tbody>
          <ScoreRow
            label={awayLabel}
            cells={summary.periods.map((p) => p.away)}
            total={summary.away_total}
          />
          <ScoreRow
            label={homeLabel}
            cells={summary.periods.map((p) => p.home)}
            total={summary.home_total}
          />
        </tbody>
      </table>
    </div>
  );
}

function ScoreRow({
  label,
  cells,
  total,
}: {
  label: string;
  cells: number[];
  total: number | null;
}) {
  return (
    <tr className="border-t border-zinc-800">
      <td className="px-2 py-1.5 text-left font-semibold text-zinc-200">
        {label}
      </td>
      {cells.map((value, index) => (
        <td key={index} className="px-2 py-1.5 text-center text-zinc-400">
          {value}
        </td>
      ))}
      <td className="px-2 py-1.5 text-center font-bold text-zinc-100">
        {total ?? "—"}
      </td>
    </tr>
  );
}

function PerformerGroup({
  title,
  performers,
}: {
  title: string;
  performers: Performer[];
}) {
  if (performers.length === 0) {
    return null;
  }
  return (
    <div>
      <h3 className="mb-2 truncate text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      <ul className="space-y-1.5">
        {performers.map((performer, index) => (
          <li
            key={`${performer.name}-${index}`}
            className="flex items-baseline justify-between gap-2 text-sm"
          >
            <span className="min-w-0 truncate text-zinc-200">
              {performer.name}
            </span>
            <span className="shrink-0 text-right text-xs tabular-nums text-zinc-400">
              {performer.detail}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-5" aria-hidden="true">
      <div className="space-y-2">
        <div className="h-4 w-24 rounded bg-zinc-800" />
        <div className="h-8 rounded bg-zinc-800" />
        <div className="h-8 rounded bg-zinc-800" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <div className="h-3 w-20 rounded bg-zinc-800" />
          <div className="h-4 rounded bg-zinc-800" />
          <div className="h-4 rounded bg-zinc-800" />
        </div>
        <div className="space-y-2">
          <div className="h-3 w-20 rounded bg-zinc-800" />
          <div className="h-4 rounded bg-zinc-800" />
          <div className="h-4 rounded bg-zinc-800" />
        </div>
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        className="h-8 w-8 text-zinc-600"
        aria-hidden="true"
      >
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 9h18M9 4v16" strokeLinecap="round" />
      </svg>
      <p className="text-sm text-zinc-400">{message}</p>
    </div>
  );
}

