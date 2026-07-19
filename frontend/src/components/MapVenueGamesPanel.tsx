import { useEffect, useMemo, useState } from "react";

import type { GameOdds, GameSide, MapGame } from "../types";
import { useGameOdds } from "../hooks";
import { formatDateTime } from "../lib/time";
import TeamLogo from "./TeamLogo";
import Portal from "./Portal";
import GameDetailModal from "./GameDetailModal";

/** 3-letter fallback when a side carries no abbreviation (mirrors GameCard). */
function sideLabel(side: GameSide): string {
  return side.abbreviation || side.name.slice(0, 3).toUpperCase();
}

/**
 * A one-glance favorite chip: the favored side and its win probability,
 * falling back to the favored moneyline when only a line is priced. Replicates
 * GameCard's `favoriteChip` but operates on a MapGame's sides directly.
 * Returns null when there's nothing to show.
 */
function favoriteChip(
  game: MapGame,
  odds: GameOdds,
): { label: string; title: string } | null {
  const homeLabel = sideLabel(game.home);
  const awayLabel = sideLabel(game.away);
  if (odds.home_win_pct !== null && odds.away_win_pct !== null) {
    const homeFav = odds.home_win_pct >= odds.away_win_pct;
    const label = homeFav ? homeLabel : awayLabel;
    const pct = Math.round(homeFav ? odds.home_win_pct : odds.away_win_pct);
    return {
      label: `${label} ${pct}%`,
      title:
        `Win probability — ${awayLabel} ${Math.round(odds.away_win_pct)}% · ` +
        `${homeLabel} ${Math.round(odds.home_win_pct)}%`,
    };
  }
  const hm = odds.home_moneyline;
  const am = odds.away_moneyline;
  if (hm !== null || am !== null) {
    const homeFav = hm !== null && am !== null ? hm <= am : hm !== null;
    const label = homeFav ? homeLabel : awayLabel;
    const ml = homeFav ? hm : am;
    if (ml === null) return null;
    return {
      label: `${label} ${ml > 0 ? "+" : ""}${ml}`,
      title: odds.details ?? `${label} favored`,
    };
  }
  return null;
}

/**
 * A venue (one map pin) and the upcoming games taking place there, built by
 * MapView grouping the `/api/map` games by coordinates.
 */
export interface MapVenueGroup {
  key: string;
  venue: string | null;
  lat: number;
  lon: number;
  games: MapGame[]; // sorted earliest-first
  source: "followed" | "competition";
}

/**
 * Slide-in side panel for a clicked game-venue pin (map "Upcoming games"
 * mode). Lists every upcoming match at that stadium; clicking one fires
 * `onGameClick` so the map can replay that fixture's travel visuals (plane
 * for a followed away side, fans for a followed home side) — and opens the
 * full box-score modal UNLESS the map took over the whole screen (it
 * returns true when a flight cinematic starts, so the modal never covers
 * the flight). Mirrors MapTeamPanel's drawer chrome (always mounted so it
 * animates both ways; portaled to body so the view's transform doesn't
 * trap it; `top-12` clears the sticky header).
 */
export default function MapVenueGamesPanel({
  venue,
  leagueNames,
  onClose,
  onGameClick,
}: {
  venue: MapVenueGroup | null;
  leagueNames: Record<string, string>;
  onClose: () => void;
  /** Per-game click affordance — the map flies/celebrates for that fixture.
   *  Return true to take over the map and suppress the box-score modal.
   *  Optional so the panel still works standalone. */
  onGameClick?: (game: MapGame) => boolean | void;
}) {
  const open = venue !== null;

  // Retain the last venue so its content stays rendered through the slide-out.
  const [last, setLast] = useState<MapVenueGroup | null>(venue);
  useEffect(() => {
    if (venue !== null) setLast(venue);
  }, [venue]);
  const shown = venue ?? last;

  // The game whose box score is open (drill-down), if any.
  const [openGameId, setOpenGameId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const games = shown?.games ?? [];

  // Pre-game / live lines for the listed games (favorite chip per row). Only
  // scheduled & in-progress games carry a meaningful line, so we skip finals.
  const oddsIds = useMemo(
    () =>
      games
        .filter(
          (game) =>
            game.phase === "scheduled" || game.phase === "in_progress",
        )
        .map((game) => game.game_id),
    [games],
  );
  const oddsQuery = useGameOdds(oddsIds);
  const oddsById = oddsQuery.data;

  return (
    <>
      <Portal>
        <aside
          aria-hidden={!open}
          className={
            "fixed bottom-0 right-0 top-12 z-40 flex w-[20rem] max-w-[88vw] flex-col " +
            "border-l border-zinc-800 bg-zinc-900 shadow-2xl transition-transform " +
            "duration-300 ease-out motion-reduce:transition-none sm:w-[22rem] " +
            (open ? "translate-x-0" : "pointer-events-none translate-x-full")
          }
        >
          {shown && (
            <>
              <div
                className="h-1 w-full shrink-0"
                style={{
                  backgroundColor:
                    shown.source === "followed" ? "#f59e0b" : "#a1a1aa",
                }}
              />
              <header className="flex items-start gap-3 border-b border-zinc-800 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-base font-semibold text-zinc-100">
                    {shown.venue ?? "Venue"}
                  </h2>
                  <p className="truncate text-xs uppercase tracking-wide text-zinc-500">
                    {games.length} upcoming {games.length === 1 ? "game" : "games"}
                  </p>
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

              <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
                <ul className="flex flex-col gap-1.5">
                  {games.map((game) => {
                    const odds = oddsById?.[game.game_id];
                    const chip =
                      odds !== undefined ? favoriteChip(game, odds) : null;
                    return (
                      <li key={game.game_id}>
                        <GameVenueRow
                          game={game}
                          leagueLabel={
                            game.league_name ??
                            leagueNames[game.league_id] ??
                            game.league_id
                          }
                          chip={chip}
                          onOpen={() => {
                            // A travel cinematic takes over the whole map —
                            // the modal must never cover the flight.
                            if (onGameClick?.(game) !== true) {
                              setOpenGameId(game.game_id);
                            }
                          }}
                        />
                      </li>
                    );
                  })}
                </ul>
              </div>
            </>
          )}
        </aside>
      </Portal>

      {openGameId && (
        <GameDetailModal gameId={openGameId} onClose={() => setOpenGameId(null)} />
      )}
    </>
  );
}

/** One clickable game at the venue: kickoff, both sides (crest + name), score. */
function GameVenueRow({
  game,
  leagueLabel,
  chip,
  onOpen,
}: {
  game: MapGame;
  leagueLabel: string;
  chip: { label: string; title: string } | null;
  onOpen: () => void;
}) {
  const live = game.phase === "in_progress";
  const hasScore = game.home.score !== null && game.away.score !== null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-2 text-left transition hover:border-zinc-700 hover:bg-zinc-800/70"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[11px] uppercase tracking-wide text-zinc-500">
            {leagueLabel}
          </span>
          {chip !== null && (
            <span
              className="inline-flex shrink-0 items-center rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-400"
              title={chip.title}
            >
              {chip.label}
            </span>
          )}
        </span>
        <span
          className={
            "shrink-0 text-[11px] " +
            (live ? "font-semibold text-amber-400" : "text-zinc-400")
          }
        >
          {live ? game.period_label || "Live" : formatDateTime(game.start_time)}
        </span>
      </div>
      <div className="mt-1.5 flex flex-col gap-1">
        <GameVenueSide
          name={game.away.name}
          logoUrl={game.away.logo_url}
          abbreviation={game.away.abbreviation}
          color={game.away.color}
          score={game.away.score}
          showScore={hasScore}
        />
        <GameVenueSide
          name={game.home.name}
          logoUrl={game.home.logo_url}
          abbreviation={game.home.abbreviation}
          color={game.home.color}
          score={game.home.score}
          showScore={hasScore}
        />
      </div>
    </button>
  );
}

function GameVenueSide({
  name,
  logoUrl,
  abbreviation,
  color,
  score,
  showScore,
}: {
  name: string;
  logoUrl: string | null;
  abbreviation: string | null;
  color: string | null;
  score: number | null;
  showScore: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <TeamLogo
        logoUrl={logoUrl}
        name={name}
        abbreviation={abbreviation}
        color={color}
        size="sm"
      />
      <span className="min-w-0 flex-1 truncate text-sm text-zinc-200">{name}</span>
      {showScore && (
        <span className="shrink-0 text-sm font-semibold tabular-nums text-zinc-100">
          {score}
        </span>
      )}
    </div>
  );
}
