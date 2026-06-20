import { useMemo, useState } from "react";

import type { Game, GameOdds, GameSide, League, Sport } from "../types";
import { useTeams } from "../hooks";
import GameDetailModal from "./GameDetailModal";
import StatusBadge from "./StatusBadge";
import TeamLogo from "./TeamLogo";

/**
 * Fallback accent colors for whole-competition games (no followed team),
 * keyed by sport so they read as a consistent league hue rather than a flat
 * gray. Shared with the calendar so a competition looks the same in both.
 */
const SPORT_FALLBACK_COLORS: Record<Sport, string> = {
  basketball: "#f97316",
  baseball: "#3b82f6",
  soccer: "#22c55e",
  hockey: "#38bdf8",
  football: "#a855f7",
  tennis: "#84cc16",
  mma: "#ef4444",
  golf: "#14b8a6",
  volleyball: "#eab308",
};

export const NEUTRAL_FALLBACK_COLOR = "#52525b";

/** Stable accent color for a game that has no followed team to color it. */
export function leagueFallbackColor(sport: Sport): string {
  return SPORT_FALLBACK_COLORS[sport] ?? NEUTRAL_FALLBACK_COLOR;
}

/** 3-letter fallback when a side carries no abbreviation. */
function sideLabel(side: GameSide): string {
  return side.abbreviation || side.name.slice(0, 3).toUpperCase();
}

/**
 * A one-glance favorite chip for the card footer: the favored side and its
 * win probability (the "prediction"), falling back to the favored money­line
 * when only a line is priced. Returns null when there's nothing to show.
 */
function favoriteChip(
  game: Game,
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

/** Color of the first followed team that has an entry in the color map. */
function firstFollowedColor(
  game: Game,
  teamColors?: Record<string, string>,
): string | undefined {
  if (!teamColors) return undefined;
  for (const teamId of game.followed_team_ids) {
    const color = teamColors[teamId];
    if (color) return color;
  }
  return undefined;
}

function TeamRow({
  side,
  opponent,
  game,
  teamColors,
  teamLogos,
}: {
  side: GameSide;
  opponent: GameSide;
  game: Game;
  teamColors?: Record<string, string>;
  teamLogos?: Record<string, string>;
}) {
  const followed =
    side.team_id !== null && game.followed_team_ids.includes(side.team_id);
  // Prefer the real crest/flag the API now sends for EVERY side; fall back to
  // a followed team's cached /teams logo.
  const logoUrl =
    side.logo_url ??
    (side.team_id !== null && teamLogos ? teamLogos[side.team_id] : undefined);
  // Color the fallback abbreviation chip: a followed team's brand color, else
  // the side's own provider color (e.g. a nation's flag color). The chip only
  // shows when there's no logo / it 404s.
  const followedColor =
    followed && side.team_id !== null && teamColors
      ? teamColors[side.team_id]
      : undefined;
  const chipColor = followedColor ?? side.color ?? undefined;

  const started = game.phase === "in_progress" || game.phase === "final";
  const showScore = game.phase !== "scheduled" && side.score !== null;
  const isLeader =
    started &&
    side.score !== null &&
    opponent.score !== null &&
    side.score > opponent.score;

  return (
    <div className="flex items-center gap-2">
      <TeamLogo
        logoUrl={logoUrl}
        name={side.name}
        abbreviation={side.abbreviation}
        color={chipColor}
        size="sm"
      />
      <span
        className={
          "min-w-0 flex-1 truncate text-sm " +
          (followed ? "text-zinc-100" : "text-zinc-300")
        }
      >
        {side.name}
      </span>
      {showScore && (
        <span
          key={side.score ?? "none"}
          className={
            "sd-score shrink-0 text-right text-sm tabular-nums " +
            (isLeader ? "font-bold text-zinc-100" : "text-zinc-400")
          }
        >
          {side.score}
        </span>
      )}
    </div>
  );
}

/**
 * Dense, glanceable game card: away on top, home below, status + venue
 * footer. Followed teams get their color on the abbreviation chip and the
 * first followed team's color as a thin left accent border.
 *
 * Whole-competition games have no followed team, so neither side is
 * colored; the card falls back to a per-league accent and labels itself
 * with the competition name so it still reads at a glance.
 *
 * The whole card is a button: clicking (or Enter/Space when focused) opens a
 * full-screen box-score drill-down for this game. The modal state lives here
 * so every existing call site keeps working with the same props.
 */
export default function GameCard({
  game,
  teamColors,
  leaguesById,
  odds,
}: {
  game: Game;
  teamColors?: Record<string, string>; // team_id -> hex
  leaguesById?: Record<string, League>; // league_id -> league (for fallback label)
  odds?: GameOdds | null; // pre-game lines + win-prob, when priced
}) {
  const [open, setOpen] = useState(false);
  // Logos live on the cached /teams payload (Team.logo_url), keyed by the
  // internal team id; GameSide only carries team_id, so we resolve here
  // rather than threading a prop through every call site (keeps TodayView /
  // the kiosk untouched). useTeams is staleTime:Infinity — already in cache.
  const teamsQuery = useTeams();
  const teamLogos = useMemo(() => {
    const map: Record<string, string> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      if (team.logo_url) map[team.id] = team.logo_url;
    }
    return map;
  }, [teamsQuery.data]);

  const followedColor = firstFollowedColor(game, teamColors);
  const isWholeComp = game.followed_team_ids.length === 0;
  const accent = followedColor ?? leagueFallbackColor(game.sport);
  const leagueName = leaguesById?.[game.league_id]?.name;
  // Win-prob / line chip only makes sense before / during a game.
  const chip =
    odds != null && game.phase !== "final" ? favoriteChip(game, odds) : null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Box score: ${game.away.name} at ${game.home.name}`}
        className="sd-card sd-stagger-item block w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-left transition hover:border-zinc-700 hover:bg-zinc-800/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
        style={{ borderLeftWidth: "3px", borderLeftColor: accent }}
      >
        {game.series !== null && (
          <div className="mb-1.5 truncate text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            {game.series}
          </div>
        )}
        <div className="space-y-1.5">
          <TeamRow
            side={game.away}
            opponent={game.home}
            game={game}
            teamColors={teamColors}
            teamLogos={teamLogos}
          />
          <TeamRow
            side={game.home}
            opponent={game.away}
            game={game}
            teamColors={teamColors}
            teamLogos={teamLogos}
          />
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-zinc-800/70 pt-2">
          <span className="flex shrink-0 items-center gap-2">
            <StatusBadge game={game} />
            {chip !== null && (
              <span
                className="inline-flex items-center rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-amber-400"
                title={chip.title}
              >
                {chip.label}
              </span>
            )}
          </span>
          <span className="flex min-w-0 items-center gap-2">
            {isWholeComp && leagueName !== undefined && (
              <span
                className="inline-flex max-w-[10rem] items-center gap-1 truncate rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                style={{ color: accent }}
                title={leagueName}
              >
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: accent }}
                  aria-hidden="true"
                />
                <span className="truncate">{leagueName}</span>
              </span>
            )}
            {game.venue !== null && (
              <span className="min-w-0 truncate text-xs text-zinc-500">
                {game.venue}
              </span>
            )}
          </span>
        </div>
      </button>
      {open && (
        <GameDetailModal
          gameId={game.id}
          fallbackGame={game}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
