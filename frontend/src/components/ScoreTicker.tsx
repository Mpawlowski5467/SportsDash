import { useMemo, useState } from "react";

import { useToday } from "../hooks";
import type { Game, GameSide } from "../types";
import GameDetailModal from "./GameDetailModal";
import StatusBadge from "./StatusBadge";
import TeamLogo from "./TeamLogo";

/**
 * Always-visible scrolling scoreboard bar. Mounted globally under the nav so
 * today's scores are glanceable on every tab — built for the kiosk wall
 * display. Reuses the already-polled `/today` data (no new endpoint) and the
 * shared `TeamLogo` / `StatusBadge` / `GameDetailModal` building blocks.
 *
 * Renders nothing when there are no games today. With enough games the chip
 * row auto-scrolls (a duplicated track translated -50% for a seamless loop,
 * paused on hover); with only a handful it's a static, manually-scrollable
 * strip. All motion is gated on `prefers-reduced-motion` in index.css.
 */

/** Phase ordering for the ticker: live first, then upcoming, then finals. */
const PHASE_RANK: Record<Game["phase"], number> = {
  in_progress: 0,
  scheduled: 1,
  final: 2,
  postponed: 3,
  canceled: 4,
};

/** Animate the marquee only once there are enough chips to fill the bar. */
const ANIMATE_THRESHOLD = 5;

function sideLabel(side: GameSide): string {
  return side.abbreviation ?? side.name.slice(0, 3).toUpperCase();
}

function TickerChip({
  game,
  onOpen,
  duplicate = false,
}: {
  game: Game;
  onOpen: (game: Game) => void;
  duplicate?: boolean;
}) {
  const started = game.phase === "in_progress" || game.phase === "final";
  const showScore =
    started && game.away.score !== null && game.home.score !== null;
  const awayLeads =
    showScore && (game.away.score ?? 0) > (game.home.score ?? 0);
  const homeLeads =
    showScore && (game.home.score ?? 0) > (game.away.score ?? 0);

  return (
    <button
      type="button"
      onClick={() => onOpen(game)}
      // The duplicated copy exists only to make the loop seamless; keep it out
      // of the a11y tree and tab order so each game is announced/focused once.
      aria-hidden={duplicate || undefined}
      tabIndex={duplicate ? -1 : undefined}
      aria-label={`${game.away.name} at ${game.home.name} — open box score`}
      className="flex shrink-0 items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900/70 px-2.5 py-1 text-xs transition-colors hover:border-zinc-700 hover:bg-zinc-800/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
    >
      <span className="flex items-center gap-1.5">
        <TeamLogo
          logoUrl={game.away.logo_url}
          name={game.away.name}
          abbreviation={game.away.abbreviation}
          color={game.away.color}
          size="xs"
        />
        <span className={awayLeads ? "font-semibold text-zinc-100" : "text-zinc-300"}>
          {sideLabel(game.away)}
        </span>
        {showScore && (
          <span
            key={game.away.score ?? "none"}
            className={
              "sd-score tabular-nums " +
              (awayLeads ? "font-bold text-zinc-100" : "text-zinc-400")
            }
          >
            {game.away.score}
          </span>
        )}
      </span>

      <span className="text-zinc-600">{showScore ? "·" : "@"}</span>

      <span className="flex items-center gap-1.5">
        <TeamLogo
          logoUrl={game.home.logo_url}
          name={game.home.name}
          abbreviation={game.home.abbreviation}
          color={game.home.color}
          size="xs"
        />
        <span className={homeLeads ? "font-semibold text-zinc-100" : "text-zinc-300"}>
          {sideLabel(game.home)}
        </span>
        {showScore && (
          <span
            key={game.home.score ?? "none"}
            className={
              "sd-score tabular-nums " +
              (homeLeads ? "font-bold text-zinc-100" : "text-zinc-400")
            }
          >
            {game.home.score}
          </span>
        )}
      </span>

      <StatusBadge game={game} />
    </button>
  );
}

export default function ScoreTicker() {
  const { data } = useToday();
  const [openGame, setOpenGame] = useState<Game | null>(null);

  const games = useMemo(() => {
    const all = data?.games ?? [];
    return [...all].sort((a, b) => {
      const rank = PHASE_RANK[a.phase] - PHASE_RANK[b.phase];
      if (rank !== 0) return rank;
      return a.start_time.localeCompare(b.start_time);
    });
  }, [data]);

  if (games.length === 0) {
    return null;
  }

  const animate = games.length >= ANIMATE_THRESHOLD;
  // Tie the loop duration to the chip count so a long bar doesn't whip past
  // and a short one doesn't crawl (~6s of travel per game).
  const durationStyle = animate
    ? { animationDuration: `${Math.max(30, games.length * 6)}s` }
    : undefined;

  const chips = games.map((game) => (
    <TickerChip key={game.id} game={game} onOpen={setOpenGame} />
  ));

  return (
    <div className="sticky top-12 z-40 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur">
      <div
        className={
          "sd-ticker mx-auto max-w-[1600px] " +
          (animate ? "overflow-hidden" : "overflow-x-auto")
        }
        aria-label="Live scoreboard"
      >
        <div
          className={
            "sd-ticker-track flex items-center gap-2 px-4 py-1.5" +
            (animate ? " sd-ticker-animate" : "")
          }
          style={durationStyle}
        >
          {chips}
          {animate &&
            games.map((game) => (
              <TickerChip
                key={`dup-${game.id}`}
                game={game}
                onOpen={setOpenGame}
                duplicate
              />
            ))}
        </div>
      </div>
      {openGame && (
        <GameDetailModal
          gameId={openGame.id}
          fallbackGame={openGame}
          onClose={() => setOpenGame(null)}
        />
      )}
    </div>
  );
}
