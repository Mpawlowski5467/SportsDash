import { useMemo, useState, type ReactNode } from "react";
import { useRoster, useTeams } from "../hooks";
import { formatDateTime } from "../lib/time";
import type { Player, PlayerStatus, Sport } from "../types";
import TeamLogo from "../components/TeamLogo";
import PlayerAvatar from "../components/PlayerAvatar";
import Select, { type SelectOption } from "../components/Select";
import { useManageTeams } from "../components/ManageTeamsContext";

// Tennis players and UFC fighters are modeled as single-member "teams" with
// no roster — show a friendly note instead of an empty table.
const INDIVIDUAL_SPORTS: ReadonlySet<Sport> = new Set(["tennis", "mma"]);

const HEADER_CELL =
  "sticky top-0 z-10 bg-zinc-950 px-2 py-2 text-xs font-medium uppercase tracking-wider text-zinc-500";

const STATUS_PILLS: Record<
  Exclude<PlayerStatus, "active">,
  { label: string; className: string }
> = {
  day_to_day: {
    label: "DTD",
    className:
      "rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-semibold text-amber-400",
  },
  injured: {
    label: "INJ",
    className:
      "rounded bg-orange-500/15 px-1.5 py-0.5 text-xs font-semibold text-orange-400",
  },
  out: {
    label: "OUT",
    className:
      "rounded bg-red-500/15 px-1.5 py-0.5 text-xs font-semibold text-red-400",
  },
};

function isUnavailable(player: Player): boolean {
  return player.status === "injured" || player.status === "out";
}

function RosterRow({ player }: { player: Player }) {
  const unavailable = isUnavailable(player);
  return (
    <tr className="sd-stagger-item">
      <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">
        {player.jersey_number ?? "—"}
      </td>
      <td className="px-2 py-1.5">
        <div className="flex items-center gap-2.5">
          <PlayerAvatar photoUrl={player.photo_url} name={player.name} size="sm" />
          <div className="min-w-0">
            <div className={unavailable ? "text-zinc-400" : "text-zinc-100"}>
              {player.name}
            </div>
            {player.stat_line !== null && (
              <div className="text-xs tabular-nums text-zinc-500">
                {player.stat_line}
              </div>
            )}
            {player.career_stat_line !== null && (
              <div className="text-xs tabular-nums text-zinc-600">
                Career: {player.career_stat_line}
              </div>
            )}
            {player.status_detail !== null && (
              <div className="text-xs text-zinc-500">{player.status_detail}</div>
            )}
          </div>
        </div>
      </td>
      <td className="px-2 py-1.5 text-zinc-300">{player.position ?? "—"}</td>
      <td className="px-2 py-1.5">
        {player.status === "active" ? (
          <span className="text-zinc-600">—</span>
        ) : (
          <span className={STATUS_PILLS[player.status].className}>
            {STATUS_PILLS[player.status].label}
          </span>
        )}
      </td>
    </tr>
  );
}

export default function RosterView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const teams = teamsQuery.data?.teams ?? [];
  // Ignore a selection that no longer exists — re-running setup replaces
  // the followed teams, which would leave `selected` pointing at a 404.
  const teamId = teams.some((team) => team.id === selected)
    ? selected
    : teams[0]?.id;
  const activeTeam = teams.find((team) => team.id === teamId);
  const rosterQuery = useRoster(teamId);

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

  // Map each team to its league's sport so we can tell individual-sport
  // "teams" (a tennis player / UFC fighter) apart from real squads.
  const sportByTeamId = useMemo(() => {
    const sportByLeague: Record<string, Sport> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      sportByLeague[league.id] = league.sport;
    }
    const map: Record<string, Sport> = {};
    for (const team of teams) {
      const sport = sportByLeague[team.league_id];
      if (sport) map[team.id] = sport;
    }
    return map;
  }, [teamsQuery.data, teams]);

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
        <p className="text-sm text-zinc-500">No teams followed yet — follow a team to see its roster here.</p>
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

  const roster = rosterQuery.data;

  let body: ReactNode;
  if (rosterQuery.isError) {
    body = (
      <p className="text-sm text-red-400">
        Failed to load roster: {rosterQuery.error?.message ?? "unknown error"}
      </p>
    );
  } else if (!roster) {
    body = <p className="text-sm text-zinc-500">Loading roster…</p>;
  } else if (roster.players.length === 0) {
    const sport = teamId !== undefined ? sportByTeamId[teamId] : undefined;
    body =
      sport !== undefined && INDIVIDUAL_SPORTS.has(sport) ? (
        <p className="text-sm text-zinc-500">
          No roster for individual sports.
        </p>
      ) : (
        <p className="text-sm text-zinc-500">
          No roster yet for {roster.team_name}. Rosters are refreshed by the
          daily job, so check back after the next refresh.
        </p>
      );
  } else {
    const unavailableCount = roster.players.filter(isUnavailable).length;
    body = (
      <>
        <div className="flex items-center gap-3">
          <TeamLogo
            logoUrl={activeTeam?.logo_url}
            name={roster.team_name}
            abbreviation={activeTeam?.abbreviation}
            color={activeTeam?.color}
            size="lg"
          />
          <h2 className="text-lg font-semibold text-zinc-100">
            {roster.team_name}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-0.5 text-xs text-zinc-300">
            {roster.players.length} players
          </span>
          <span
            className={
              unavailableCount > 0
                ? "rounded-full border border-red-500/30 bg-red-500/10 px-2.5 py-0.5 text-xs text-red-400"
                : "rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-0.5 text-xs text-zinc-500"
            }
          >
            {unavailableCount} unavailable
          </span>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={`${HEADER_CELL} w-10 text-right`}>#</th>
              <th className={`${HEADER_CELL} text-left`}>Player</th>
              <th className={`${HEADER_CELL} text-left`}>Pos</th>
              <th className={`${HEADER_CELL} text-left`}>Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {roster.players.map((player) => (
              <RosterRow key={player.id} player={player} />
            ))}
          </tbody>
        </table>
        {roster.fetched_at !== null && (
          <p className="text-xs text-zinc-500">
            Updated {formatDateTime(roster.fetched_at)}
          </p>
        )}
      </>
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
