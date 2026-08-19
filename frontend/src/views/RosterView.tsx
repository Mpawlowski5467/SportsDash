import { useMemo, useState, type ReactNode } from "react";
import {
  useFollowPlayer,
  usePlayerFollows,
  useRoster,
  useTeams,
  useUnfollowPlayer,
} from "../hooks";
import { formatDateTime } from "../lib/time";
import type { League, Player, PlayerStatus, Sport } from "../types";
import TeamLogo from "../components/TeamLogo";
import PlayerAvatar from "../components/PlayerAvatar";
import Select, { type SelectOption } from "../components/Select";
import { useManageTeams } from "../components/ManageTeamsContext";
import {
  bareAthleteId,
  FollowStarButton,
  NO_PLAYER_FOLLOW_SPORTS,
} from "../components/TeamProfileView";

// Tennis players, UFC fighters, and racing drivers are modeled as
// single-member "teams" with no roster — show a friendly note instead of an
// empty table.
const INDIVIDUAL_SPORTS: ReadonlySet<Sport> = new Set(["tennis", "mma", "racing"]);

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

/** Per-row follow-star state; null hides the star column entirely. */
interface FollowCell {
  followed: boolean;
  pending: boolean;
  onToggle: () => void;
}

function RosterRow({
  player,
  follow,
}: {
  player: Player;
  follow: FollowCell | null;
}) {
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
              <div className="text-xs tabular-nums text-zinc-500">
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
          <span className="text-zinc-500">—</span>
        ) : (
          <span className={STATUS_PILLS[player.status].className}>
            {STATUS_PILLS[player.status].label}
          </span>
        )}
      </td>
      {follow !== null && (
        <td className="px-2 py-1.5 text-right">
          <FollowStarButton
            name={player.name}
            followed={follow.followed}
            pending={follow.pending}
            onToggle={follow.onToggle}
          />
        </td>
      )}
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
  const followsQuery = usePlayerFollows();
  const followPlayer = useFollowPlayer();
  const unfollowPlayer = useUnfollowPlayer();

  // Scoped to the ACTIVE team: bare ESPN athlete ids are only unique per
  // sport, so matching on the id alone could fill a stranger's star on
  // another followed team's roster — and clicking that star would delete
  // the real follow. A follow stores its parent team; require it to match.
  const followedIds = useMemo(
    () =>
      new Set(
        (followsQuery.data ?? [])
          .filter((f) => f.team_id === activeTeam?.id)
          .map((f) => f.athlete_id),
      ),
    [followsQuery.data, activeTeam?.id],
  );
  // Only the stars whose mutations are in flight disable (mutations carry
  // their athlete id in `variables`). Follow and unfollow are independent
  // mutations that can BOTH be pending (star one player, unstar another
  // before the first settles), so each contributes its id — an either/or
  // here left the second row enabled for a duplicate fire.
  const pendingAthleteIds = new Set<string>();
  if (followPlayer.isPending && followPlayer.variables !== undefined) {
    pendingAthleteIds.add(followPlayer.variables.athleteId);
  }
  if (unfollowPlayer.isPending && unfollowPlayer.variables !== undefined) {
    pendingAthleteIds.add(unfollowPlayer.variables);
  }

  const toggleFollow = (player: Player) => {
    if (activeTeam === undefined) return;
    const athleteId = bareAthleteId(player.id);
    if (followedIds.has(athleteId)) {
      unfollowPlayer.mutate(athleteId);
    } else {
      followPlayer.mutate({
        athleteId,
        body: {
          name: player.name,
          team_id: activeTeam.id,
          league_id: activeTeam.league_id,
          position: player.position,
          photo_url: player.photo_url,
        },
      });
    }
  };

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

  // Map each team to its league so we can tell individual-sport "teams"
  // (a tennis player / UFC fighter) apart from real squads, and gate the
  // follow stars on the league's provider.
  const leagueByTeamId = useMemo(() => {
    const leaguesById: Record<string, League> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      leaguesById[league.id] = league;
    }
    const map: Record<string, League> = {};
    for (const team of teams) {
      const league = leaguesById[team.league_id];
      if (league) map[team.id] = league;
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
  const league = teamId !== undefined ? leagueByTeamId[teamId] : undefined;
  const sport = league?.sport;
  // Follow stars are a supplementary affordance: ESPN team-sport squads
  // only. Individual sports have no roster players to follow, and the
  // follow endpoint validates against ESPN-keyed roster rows, so a
  // TheSportsDB roster (volleyball) would render stars whose PUT can only
  // 404 — silently. The whole column also hides if the follows list
  // hasn't loaded / failed.
  const showFollowStars =
    activeTeam !== undefined &&
    league !== undefined &&
    league.provider === "espn" &&
    !NO_PLAYER_FOLLOW_SPORTS.has(league.sport) &&
    followsQuery.data !== undefined;

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
              {showFollowStars && (
                <th className={`${HEADER_CELL} w-10 text-right`}>
                  <span className="sr-only">Follow</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {roster.players.map((player) => (
              <RosterRow
                key={player.id}
                player={player}
                follow={
                  showFollowStars
                    ? {
                        followed: followedIds.has(bareAthleteId(player.id)),
                        pending: pendingAthleteIds.has(bareAthleteId(player.id)),
                        onToggle: () => toggleFollow(player),
                      }
                    : null
                }
              />
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
