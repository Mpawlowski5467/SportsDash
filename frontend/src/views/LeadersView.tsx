import { useMemo, useState, type ReactNode } from "react";

import { useLeaders, useScorers, useSetupLeagues, useTeams } from "../hooks";
import TeamLogo from "../components/TeamLogo";
import Select, { type SelectOption } from "../components/Select";

export default function LeadersView() {
  const teamsQuery = useTeams();
  const setupLeaguesQuery = useSetupLeagues();
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const leagues = teamsQuery.data?.leagues ?? [];
  const leagueId = leagues.some((league) => league.id === selected)
    ? selected
    : leagues[0]?.id;

  // League logos for the selector come from the setup catalog (the /teams
  // league rows don't carry one); a plain name shows when none is found.
  const leagueOptions: SelectOption[] = useMemo(() => {
    const logoById: Record<string, string | null> = {};
    for (const cat of setupLeaguesQuery.data?.leagues ?? []) {
      logoById[cat.id] = cat.logo_url;
    }
    return leagues.map((league) => ({
      id: league.id,
      label: league.name,
      logoUrl: logoById[league.id],
    }));
  }, [leagues, setupLeaguesQuery.data]);
  // Real tournament scorers (the Golden Boot) when the league is an active
  // competition with finished matches; otherwise the roster-derived board.
  const scorersQuery = useScorers(leagueId);
  const leadersQuery = useLeaders(leagueId);
  const scorers = scorersQuery.data;
  const showGoldenBoot = (scorers?.rows.length ?? 0) > 0;

  if (teamsQuery.isError) {
    return (
      <p className="text-sm text-red-400">
        Failed to load leagues: {teamsQuery.error?.message ?? "unknown error"}
      </p>
    );
  }
  if (!teamsQuery.data) {
    return <p className="text-sm text-zinc-500">Loading leagues…</p>;
  }
  if (leagues.length === 0) {
    return (
      <p className="text-sm text-zinc-500">No leagues configured.</p>
    );
  }

  const data = leadersQuery.data;

  let body: ReactNode;
  if (showGoldenBoot && scorers) {
    // Golden Boot — real goals tallied from finished matches.
    body = (
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-zinc-200">Golden Boot</h2>
          <span className="text-xs uppercase tracking-wide text-zinc-500">
            {scorers.games_counted}{" "}
            {scorers.games_counted === 1 ? "match" : "matches"}
          </span>
        </div>
        <ol className="flex flex-col divide-y divide-zinc-800">
          {scorers.rows.map((row) => (
            <li
              key={`${row.team}-${row.player}`}
              className={
                "sd-stagger-item flex items-center gap-3 border-l-2 py-2 pl-2 " +
                (row.highlighted
                  ? "border-amber-500 bg-amber-500/10"
                  : "border-transparent")
              }
            >
              <span className="w-6 shrink-0 text-right text-sm tabular-nums text-zinc-500">
                {row.rank}
              </span>
              <TeamLogo logoUrl={row.team_logo_url} name={row.team} size="sm" />
              <div className="min-w-0 flex-1">
                <p
                  className={
                    "truncate text-sm font-medium " +
                    (row.highlighted ? "text-amber-200" : "text-zinc-100")
                  }
                >
                  {row.player}
                </p>
                <p className="truncate text-xs text-zinc-500">{row.team}</p>
              </div>
              <span className="shrink-0 text-right">
                <span className="text-base font-semibold tabular-nums text-zinc-100">
                  {row.goals}
                </span>{" "}
                <span className="text-xs text-zinc-500">
                  {row.goals === 1 ? "goal" : "goals"}
                </span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    );
  } else if (leadersQuery.isError) {
    body = (
      <p className="text-sm text-red-400">
        Failed to load leaders: {leadersQuery.error?.message ?? "unknown error"}
      </p>
    );
  } else if (!data) {
    body = <p className="text-sm text-zinc-500">Loading leaders…</p>;
  } else if (data.rows.length === 0) {
    body = (
      <p className="max-w-md text-sm text-zinc-500">
        No player stats yet for {data.league_name}. Leaders are built from your
        followed teams' rosters — follow more teams in this league to broaden
        the board. (Rosters refresh on the daily update.)
      </p>
    );
  } else {
    body = (
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-zinc-200">
            {data.league_name}
          </h2>
          <span className="text-xs uppercase tracking-wide text-zinc-500">
            Ranked by {data.stat_label}
          </span>
        </div>
        <ol className="flex flex-col divide-y divide-zinc-800">
          {data.rows.map((row) => (
            <li
              key={`${row.team_id}-${row.player_id}`}
              className={
                "sd-stagger-item flex items-center gap-3 border-l-2 py-2 pl-2 " +
                (row.highlighted
                  ? "border-amber-500 bg-amber-500/10"
                  : "border-transparent")
              }
            >
              <span className="w-6 shrink-0 text-right text-sm tabular-nums text-zinc-500">
                {row.rank}
              </span>
              <TeamLogo
                logoUrl={row.team_logo_url}
                name={row.team_name}
                color={row.team_color}
                size="sm"
              />
              <div className="min-w-0 flex-1">
                <p
                  className={
                    "truncate text-sm font-medium " +
                    (row.highlighted ? "text-amber-200" : "text-zinc-100")
                  }
                >
                  {row.name}
                </p>
                <p className="truncate text-xs text-zinc-500">
                  {row.team_name}
                  {row.position ? ` · ${row.position}` : ""}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <span className="text-base font-semibold tabular-nums text-zinc-100">
                  {formatValue(row.value)}
                </span>{" "}
                <span className="text-xs text-zinc-500">{row.stat_label}</span>
                {row.detail && (
                  <p className="text-[11px] text-zinc-600">{row.detail}</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Select
        options={leagueOptions}
        value={leagueId}
        onChange={setSelected}
        ariaLabel="Choose league"
      />
      {body}
    </div>
  );
}

/** Drop a trailing ".0" so integer stats read "3" not "3.0". */
function formatValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
