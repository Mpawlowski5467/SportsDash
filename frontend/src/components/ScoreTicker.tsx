import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useToday } from "../hooks";
import { boxScoreLabel, shortMatchup, shortSide } from "../lib/labels";
import { periodLabel } from "../lib/periodLabel";
import { formatTime } from "../lib/time";
import { usePrefersReducedMotion } from "../lib/usePrefersReducedMotion";
import type { Game, GameSide } from "../types";
import { ScoreFigure, SpotChip } from "./bs";
import GameDetailModal from "./GameDetailModal";
import { fightMethodLabel, fightResultOf, fightResultSummary } from "./StatusBadge";

/**
 * The score line: a full-width ink band under the masthead with today's
 * games, on every tab — built for the kiosk wall display. Reuses the
 * already-polled `/today` data (no new endpoint) and opens the shared
 * `GameDetailModal`.
 *
 * Renders nothing when there are no games today. When every game fits on one
 * row the line is static, as drawn. When they do not fit it scrolls (a
 * duplicated track translated -50% for a seamless loop, paused on hover and
 * on keyboard focus); under `prefers-reduced-motion` it is a single,
 * manually scrollable strip instead.
 */

/** Phase ordering for the line: live first, then upcoming, then finals. */
const PHASE_RANK: Record<Game["phase"], number> = {
  in_progress: 0,
  scheduled: 1,
  final: 2,
  postponed: 3,
  canceled: 4,
};

/** The muted lead-in of a cell that is not live: a time, or how it ended. */
function statusLabel(game: Game): { text: string; title?: string } {
  if (game.phase === "scheduled") {
    return { text: formatTime(game.start_time) };
  }
  if (game.phase === "final") {
    // A finished fight's score is a round count, so the label carries how
    // it actually ended; every other sport is a plain "Final".
    const fight = fightResultOf(game);
    const method = fight === null ? "" : fightMethodLabel(fight.method);
    return {
      text: method !== "" ? `Final · ${method}` : "Final",
      title: fight === null ? undefined : fightResultSummary(fight),
    };
  }
  return { text: game.phase === "postponed" ? "PPD" : "Canc" };
}

/** "Bot 5th", "2nd Half 71'", or "HT" during a break with no label. */
function liveDetail(game: Game): string {
  const period = periodLabel(game, "short") || (game.is_intermission ? "HT" : "");
  return [period, game.clock ?? ""].filter(Boolean).join(" ");
}

function Score({ game, side }: { game: Game; side: GameSide }) {
  return (
    <>
      {shortSide(game, side)}{" "}
      {/* Pulses when the score moves, and only then. */}
      <ScoreFigure of={game.id} value={side.score} className="tabular-nums" />
    </>
  );
}

function ScoreCell({
  game,
  onOpen,
  flushFirst,
  duplicate = false,
}: {
  game: Game;
  onOpen: (game: Game) => void;
  /** Static line: the first cell sits flush, with no rule before it. */
  flushFirst: boolean;
  duplicate?: boolean;
}) {
  const live = game.phase === "in_progress";
  const started = live || game.phase === "final";
  const showScore =
    started && game.away.score !== null && game.home.score !== null;

  let lead: ReactNode;
  if (live) {
    lead = <SpotChip>Live</SpotChip>;
  } else {
    const status = statusLabel(game);
    lead = (
      <span className="text-ink-2" title={status.title}>
        {status.text}
      </span>
    );
  }
  const detail = live ? liveDetail(game) : "";

  return (
    <button
      type="button"
      onClick={() => onOpen(game)}
      // The duplicated copy exists only to make the loop seamless; keep it out
      // of the a11y tree and tab order so each game is announced/focused once.
      aria-hidden={duplicate || undefined}
      tabIndex={duplicate ? -1 : undefined}
      aria-label={boxScoreLabel(game)}
      className={
        // `uppercase` is set here, not on the line: browsers reset
        // text-transform on buttons, so it does not inherit.
        "bs-focus-inset flex min-h-11 shrink-0 cursor-pointer items-center gap-2.5 whitespace-nowrap border-l border-rule px-5 uppercase text-ink hover:bg-[color-mix(in_srgb,var(--bs-ink)_12%,transparent)]" +
        (flushFirst ? " first:border-l-0 first:pl-0" : "")
      }
    >
      {lead}
      <span>
        {showScore ? (
          <>
            <Score game={game} side={game.away} /> ·{" "}
            <Score game={game} side={game.home} />
          </>
        ) : (
          shortMatchup(game)
        )}
      </span>
      {detail !== "" && <span className="text-ink-2">{detail}</span>}
    </button>
  );
}

export default function ScoreTicker() {
  const { data } = useToday();
  const [openGame, setOpenGame] = useState<Game | null>(null);
  const reducedMotion = usePrefersReducedMotion();

  const games = useMemo(() => {
    const all = data?.games ?? [];
    return [...all].sort((a, b) => {
      const rank = PHASE_RANK[a.phase] - PHASE_RANK[b.phase];
      if (rank !== 0) return rank;
      return a.start_time.localeCompare(b.start_time);
    });
  }, [data]);

  // Whether one copy of the cells is wider than the line. Measured rather
  // than guessed from the game count: it depends on the window, the kiosk
  // scale and how long today's labels are.
  const lineRef = useRef<HTMLDivElement>(null);
  const cellsRef = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  // While the keyboard is inside the line it stops sliding and becomes a
  // strip the browser can scroll: a cell that has slid out of view could
  // otherwise take focus where nobody can see it.
  const [focused, setFocused] = useState(false);
  const hasGames = games.length > 0;

  useEffect(() => {
    const line = lineRef.current;
    const cells = cellsRef.current;
    if (!hasGames || line === null || cells === null) return;
    const measure = () => {
      setOverflowing(cells.scrollWidth > line.clientWidth + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(line);
    observer.observe(cells);
    return () => observer.disconnect();
  }, [hasGames]);

  if (!hasGames) {
    return null;
  }

  const animate = overflowing && !reducedMotion && !focused;
  // Tie the loop duration to the cell count so a long line doesn't whip past
  // and a short one doesn't crawl (~6s of travel per game).
  const durationStyle = animate
    ? { animationDuration: `${Math.max(30, games.length * 6)}s` }
    : undefined;

  return (
    <div className="bs-band z-40 lg:sticky lg:top-[var(--bs-masthead-h)]">
      <div className="mx-auto max-w-[90rem] px-4 sm:px-8">
        <div
          ref={lineRef}
          role="region"
          aria-label="Live scoreboard"
          onFocus={(event) => {
            // Pointer focus (a click on a cell) must not stop the line.
            if (event.target.matches(":focus-visible")) setFocused(true);
          }}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              setFocused(false);
            }
          }}
          className={
            "sd-ticker font-condensed text-lg font-semibold tracking-[0.04em] " +
            (overflowing && !animate ? "overflow-x-auto" : "overflow-hidden")
          }
        >
          <div
            className={"flex w-max" + (animate ? " sd-ticker-animate" : "")}
            style={durationStyle}
          >
            <div ref={cellsRef} className="flex">
              {games.map((game) => (
                <ScoreCell
                  key={game.id}
                  game={game}
                  onOpen={setOpenGame}
                  flushFirst={!animate}
                />
              ))}
            </div>
            {animate && (
              <div className="flex">
                {games.map((game) => (
                  <ScoreCell
                    key={`dup-${game.id}`}
                    game={game}
                    onOpen={setOpenGame}
                    flushFirst={false}
                    duplicate
                  />
                ))}
              </div>
            )}
          </div>
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
