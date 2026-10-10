import { useMemo, useState } from "react";

import type { Game, GameOdds, GameSide, League, Sport } from "../types";
import { useTeams } from "../hooks";
import { watchability } from "../lib/whyWatch";
import GameDetailModal from "./GameDetailModal";
import StatusBadge, {
  fightResultOf,
  fightResultSummary,
  fightRoundLabel,
} from "./StatusBadge";
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
  racing: "#64748b", // checkered-flag slate
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

/**
 * When a finished fight ended, for the footer chip: the badge already names
 * the method, so this adds only the round and stoppage time. Null for every
 * sport but MMA, and whenever the provider didn't report the round.
 */
function fightRoundChip(game: Game): { label: string; title: string } | null {
  const fight = fightResultOf(game);
  if (fight === null) return null;
  const label = fightRoundLabel(fight);
  return label === null ? null : { label, title: fightResultSummary(fight) };
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

/**
 * One side of a game inside a row: crest, name, and the score once there is
 * one. The leader's score is the only bold figure on the line, so a glance
 * down a column of rows finds the winners without reading them.
 */
function Side({
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
  const chipColor =
    side.team_id !== null ? teamColors?.[side.team_id] : undefined;
  const logoUrl = side.team_id !== null ? teamLogos?.[side.team_id] : undefined;
  const showScore = game.phase !== "scheduled" && side.score !== null;
  const isLeader =
    side.score !== null &&
    opponent.score !== null &&
    side.score > opponent.score;

  return (
    <span className="flex min-w-0 items-center gap-2">
      <TeamLogo
        logoUrl={logoUrl}
        name={side.name}
        abbreviation={side.abbreviation}
        color={chipColor}
        size="sm"
      />
      <span
        className={
          "truncate " + (followed ? "font-semibold text-zinc-100" : "text-zinc-300")
        }
      >
        {side.name}
      </span>
      {showScore && (
        <span
          key={side.score ?? "none"}
          className={
            "sd-score sd-figure shrink-0 " +
            (isLeader ? "font-bold text-zinc-100" : "text-zinc-400")
          }
        >
          {side.score}
        </span>
      )}
    </span>
  );
}

/**
 * A game as a row in a listing, not a tile: kickoff time in a fixed gutter,
 * the matchup on the primary line, venue and broadcast demoted beneath it,
 * and the odds / why-watch chips trailing right.
 *
 * The gutter is the point — a column of rows sharing one time column is
 * scannable in a way a grid of cards is not, and it is what lets the
 * typography carry the hierarchy instead of borders and fills. A followed
 * team is marked by a rule on the leading edge rather than a tinted card.
 *
 * Whole-competition games have no followed team, so neither side is
 * colored; the row falls back to a per-league accent and labels itself with
 * the competition name so it still reads at a glance.
 *
 * The whole row is a button: clicking (or Enter/Space when focused) opens a
 * full-screen box-score drill-down. The modal state lives here so every
 * call site keeps working with the same props.
 */
export default function GameRow({
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
  // rather than threading a prop through every call site. useTeams is
  // staleTime:Infinity — already in cache.
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
  // ...and once it's over, a finished fight puts the round/time in its place.
  const fightChip = fightRoundChip(game);
  const preOrLive = game.phase === "scheduled" || game.phase === "in_progress";
  // A rare "why watch" pill scored from what the row already holds (the game
  // plus the batched odds prop — no extra fetches). Pre/live only, and it
  // yields whenever the fight chip occupies the slot.
  const watch =
    preOrLive && fightChip === null ? watchability(game, odds) : null;
  // Where to watch: the first (national-market-first) network, pre/live only.
  const broadcast =
    preOrLive && game.broadcasts.length > 0 ? game.broadcasts[0] : null;

  // The gutter reads as a time before the game and as a status after it —
  // "10:10 PM" while it is still a plan, "Final" once it is a fact.
  const meta = [game.venue, broadcast].filter(
    (part): part is string => part !== null && part !== undefined,
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Box score: ${game.away.name} at ${game.home.name}`}
        className="sd-row sd-hair sd-stagger-item w-full py-4 text-left transition-colors hover:bg-zinc-900/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
      >
        <span className="sd-figure flex shrink-0 items-center gap-2 self-start pl-3 text-[15px] font-semibold text-zinc-100">
          <span
            aria-hidden="true"
            className="absolute-none -ml-3 h-5 w-0.5 shrink-0 rounded-full"
            style={{ backgroundColor: accent }}
          />
          <StatusBadge game={game} />
        </span>

        <span className="flex min-w-0 flex-col gap-1.5">
          <span className="sd-lede flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <Side
              side={game.away}
              opponent={game.home}
              game={game}
              teamColors={teamColors}
              teamLogos={teamLogos}
            />
            <span className="sd-meta shrink-0 text-zinc-500">at</span>
            <Side
              side={game.home}
              opponent={game.away}
              game={game}
              teamColors={teamColors}
              teamLogos={teamLogos}
            />
          </span>
          {(meta.length > 0 || game.series !== null || isWholeComp) && (
            <span className="sd-meta flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-zinc-500">
              {game.series !== null && (
                <>
                  <span className="truncate">{game.series}</span>
                  <span aria-hidden="true" className="text-zinc-500">
                    ·
                  </span>
                </>
              )}
              {isWholeComp && leagueName !== undefined && (
                <>
                  <span className="truncate" style={{ color: accent }}>
                    {leagueName}
                  </span>
                  {meta.length > 0 && (
                    <span aria-hidden="true" className="text-zinc-500">
                      ·
                    </span>
                  )}
                </>
              )}
              {meta.map((part, index) => (
                <span key={part} className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{part}</span>
                  {index < meta.length - 1 && (
                    <span aria-hidden="true" className="text-zinc-500">
                      ·
                    </span>
                  )}
                </span>
              ))}
            </span>
          )}
        </span>

        <span className="flex shrink-0 items-center gap-2 self-start">
          {chip !== null && (
            <span
              className="sd-figure text-[13px] font-semibold text-amber-400"
              title={chip.title}
            >
              {chip.label}
            </span>
          )}
          {fightChip !== null && (
            <span
              className="sd-figure text-[13px] font-semibold text-zinc-400"
              title={fightChip.title}
            >
              {fightChip.label}
            </span>
          )}
          {watch !== null && (
            <span
              className="text-[13px] font-semibold text-rose-400"
              title={`Why watch: ${watch.reason}`}
            >
              {watch.reason}
            </span>
          )}
        </span>
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
