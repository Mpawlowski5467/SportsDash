import { useMemo, useState, type ReactNode } from "react";
import { useResults, useTeams } from "../hooks";
import { formatShortDate } from "../lib/time";
import type { Game, GameSide, Sport } from "../types";
import TeamLogo from "../components/TeamLogo";
import Select, { type SelectOption } from "../components/Select";
import { useManageTeams } from "../components/ManageTeamsContext";

/** Per-side display metadata for logos, keyed by internal team id. */
interface SideMeta {
  logo_url: string | null;
  abbreviation: string | null;
  color: string | null;
}

const RESULTS_LIMIT = 50;

type Outcome = "W" | "L" | "D" | null;

const OUTCOME_CHIP: Record<Exclude<Outcome, null>, string> = {
  W: "bg-emerald-500/15 text-emerald-400",
  L: "bg-red-500/15 text-red-400",
  D: "bg-zinc-700/40 text-zinc-300",
};

function sideLabel(side: GameSide): string {
  return side.abbreviation ?? side.name;
}

/** Win/loss/draw from the selected team's perspective; null when unknown. */
function outcomeFor(game: Game, teamId: string | undefined): Outcome {
  if (teamId === undefined) return null;
  const mine =
    game.home.team_id === teamId
      ? game.home
      : game.away.team_id === teamId
        ? game.away
        : null;
  if (mine === null) return null;
  const theirs = mine === game.home ? game.away : game.home;
  if (mine.score === null || theirs.score === null) return null;
  if (mine.score > theirs.score) return "W";
  if (mine.score < theirs.score) return "L";
  return "D";
}

/** Month heading like "June 2026", computed from the LOCAL date. */
function monthLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
}

/** Sports whose games can legitimately end level (a draw/tie counts). */
const DRAW_SPORTS: ReadonlySet<Sport> = new Set<Sport>(["soccer", "football"]);

interface ResultsSummary {
  /** Current streak from the newest decided result, or null if none. */
  streak: { outcome: "W" | "L" | "D"; count: number } | null;
  wins: number;
  losses: number;
  draws: number;
  /** Whether to surface the draw column in the record chip. */
  showDraws: boolean;
}

/**
 * Compute the current W/L(/D) streak and last-10 record from the selected
 * team's results. `games` is newest-first (API order); games we can't decide
 * an outcome for (e.g. a missing score, or not the selected team's game) are
 * skipped so they neither extend nor break the streak.
 */
function summarize(
  games: Game[],
  teamId: string | undefined,
): ResultsSummary {
  let streak: ResultsSummary["streak"] = null;
  let streakClosed = false;
  let wins = 0;
  let losses = 0;
  let draws = 0;
  let drawCapable = false;
  let decided = 0;

  for (const game of games) {
    const outcome = outcomeFor(game, teamId);
    if (outcome === null) continue;

    // Streak runs newest-first over decided results and stops at the first
    // result whose outcome differs from the leading one.
    if (!streakClosed) {
      if (streak === null) {
        streak = { outcome, count: 1 };
      } else if (streak.outcome === outcome) {
        streak.count += 1;
      } else {
        streakClosed = true;
      }
    }

    if (DRAW_SPORTS.has(game.sport) || outcome === "D") drawCapable = true;

    // Last-10 record over the most recent decided results.
    if (decided < 10) {
      if (outcome === "W") wins += 1;
      else if (outcome === "L") losses += 1;
      else draws += 1;
      decided += 1;
    }
  }

  return { streak, wins, losses, draws, showDraws: drawCapable };
}

function StreakChips({ summary }: { summary: ResultsSummary }) {
  const { streak, wins, losses, draws, showDraws } = summary;
  if (streak === null) return null;
  const record = showDraws ? `${wins}-${losses}-${draws}` : `${wins}-${losses}`;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span
        className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-bold tabular-nums ${
          OUTCOME_CHIP[streak.outcome]
        }`}
        title={`Current ${
          streak.outcome === "W"
            ? "win"
            : streak.outcome === "L"
              ? "loss"
              : "draw"
        } streak`}
      >
        {streak.outcome}
        {streak.count}
      </span>
      <span
        className="inline-flex items-center rounded-md bg-zinc-800 px-2 py-0.5 text-xs font-medium tabular-nums text-zinc-300"
        title="Last 10 results"
      >
        L10 {record}
      </span>
    </div>
  );
}

function ResultRow({
  game,
  teamId,
  leagueName,
  teamMeta,
}: {
  game: Game;
  teamId: string | undefined;
  leagueName: string;
  teamMeta: Record<string, SideMeta>;
}) {
  const outcome = outcomeFor(game, teamId);
  const awayIsMine = teamId !== undefined && game.away.team_id === teamId;
  const homeIsMine = teamId !== undefined && game.home.team_id === teamId;
  const mineClass = "font-semibold text-zinc-100";
  const otherClass = "text-zinc-300";
  const awayMeta = game.away.team_id ? teamMeta[game.away.team_id] : undefined;
  const homeMeta = game.home.team_id ? teamMeta[game.home.team_id] : undefined;
  return (
    <li className="sd-stagger-item flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm">
      <span className="w-14 shrink-0 text-xs tabular-nums text-zinc-500">
        {formatShortDate(game.start_time)}
      </span>
      <span
        className={`flex size-5 shrink-0 items-center justify-center rounded text-xs font-bold ${
          outcome === null ? OUTCOME_CHIP.D : OUTCOME_CHIP[outcome]
        }`}
      >
        {outcome ?? "–"}
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate tabular-nums">
        <TeamLogo
          logoUrl={game.away.logo_url ?? awayMeta?.logo_url}
          name={game.away.name}
          abbreviation={game.away.abbreviation}
          color={game.away.color ?? awayMeta?.color}
          size="xs"
        />
        <span className={awayIsMine ? mineClass : otherClass}>
          {sideLabel(game.away)} {game.away.score ?? "—"}
        </span>
        <span className="text-zinc-600">@</span>
        <TeamLogo
          logoUrl={game.home.logo_url ?? homeMeta?.logo_url}
          name={game.home.name}
          abbreviation={game.home.abbreviation}
          color={game.home.color ?? homeMeta?.color}
          size="xs"
        />
        <span className={homeIsMine ? mineClass : otherClass}>
          {sideLabel(game.home)} {game.home.score ?? "—"}
        </span>
      </span>
      <span className="ml-auto shrink-0 text-right text-xs text-zinc-500">
        {leagueName}
      </span>
    </li>
  );
}

export default function ResultsView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const teams = teamsQuery.data?.teams ?? [];
  // Ignore a selection that no longer exists — re-running setup replaces
  // the followed teams, which would leave `selected` pointing at a 404.
  const teamId = teams.some((team) => team.id === selected)
    ? selected
    : teams[0]?.id;
  const resultsQuery = useResults(teamId, RESULTS_LIMIT);

  const teamOptions: SelectOption[] = useMemo(
    () =>
      teams.map((team) => ({
        id: team.id,
        label: team.name,
        logoUrl: team.logo_url,
        color: team.color,
      })),
    [teams],
  );

  const leagueNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      map[league.id] = league.name;
    }
    return map;
  }, [teamsQuery.data]);

  // Logos for followed teams (the opponent side is usually not followed, so
  // TeamLogo falls back to its abbreviation chip there — by design).
  const teamMeta = useMemo(() => {
    const map: Record<string, SideMeta> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      map[team.id] = {
        logo_url: team.logo_url,
        abbreviation: team.abbreviation,
        color: team.color,
      };
    }
    return map;
  }, [teamsQuery.data]);

  const games = resultsQuery.data;

  const summary = useMemo(
    () => summarize(games ?? [], teamId),
    [games, teamId],
  );

  // Group in API order (newest first) under local-month headings.
  const groups = useMemo(() => {
    const out: { label: string; games: Game[] }[] = [];
    for (const game of games ?? []) {
      const label = monthLabel(game.start_time);
      const last = out[out.length - 1];
      if (last !== undefined && last.label === label) {
        last.games.push(game);
      } else {
        out.push({ label, games: [game] });
      }
    }
    return out;
  }, [games]);

  if (teamsQuery.isError) {
    return (
      <p className="text-sm text-red-400">
        Failed to load teams: {teamsQuery.error?.message ?? "unknown error"}
      </p>
    );
  }
  if (!teamsQuery.data) {
    return <p className="text-sm text-zinc-500">Loading teams…</p>;
  }
  if (teams.length === 0) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-zinc-500">No teams followed yet — follow a team to see its results here.</p>
        <button
          type="button"
          onClick={openManageTeams}
          className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
        >
          Manage teams
        </button>
      </div>
    );
  }

  let body: ReactNode;
  if (resultsQuery.isError) {
    body = (
      <p className="text-sm text-red-400">
        Failed to load results: {resultsQuery.error?.message ?? "unknown error"}
      </p>
    );
  } else if (!games) {
    body = <p className="text-sm text-zinc-500">Loading results…</p>;
  } else if (games.length === 0) {
    body = (
      <p className="text-sm text-zinc-500">
        No results yet for this team. Final scores accumulate here as games
        finish while the app runs.
      </p>
    );
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <StreakChips summary={summary} />
        {groups.map((group) => (
          <section key={group.label}>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              {group.label}
            </h2>
            <ul className="flex flex-col gap-1.5">
              {group.games.map((game) => (
                <ResultRow
                  key={game.id}
                  game={game}
                  teamId={teamId}
                  leagueName={leagueNames[game.league_id] ?? game.league_id}
                  teamMeta={teamMeta}
                />
              ))}
            </ul>
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Select
        options={teamOptions}
        value={teamId}
        onChange={setSelected}
        ariaLabel="Choose team"
      />
      {body}
    </div>
  );
}
