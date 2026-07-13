import { withAlpha } from "../lib/color";
import { useMemo, useState, type ReactNode } from "react";
import { useSetupLeagues, useStandings, useTeams } from "../hooks";
import { formatDateTime } from "../lib/time";
import type { Sport, StandingRow } from "../types";
import TeamLogo from "../components/TeamLogo";
import Select, { type SelectOption } from "../components/Select";
import { useOpenNation, useOpenTeam } from "../components/TeamDetailPanel";
import { useManageTeams } from "../components/ManageTeamsContext";
import { CURRENT_SEASON, seasonOptions, supportsArchives } from "../lib/seasons";

/** Per-team display metadata pulled from the /teams payload, keyed by id. */
interface TeamMeta {
  logo_url: string | null;
  abbreviation: string | null;
  color: string | null;
}

const FALLBACK_ACCENT = "#a1a1aa";

/** Convert a hex color to an rgba() string at the given alpha. */
/** ".667" style — three decimals, leading zero stripped. */
function formatPct(pct: number | null): string {
  if (pct === null) return "—";
  const fixed = pct.toFixed(3);
  return fixed.startsWith("0") ? fixed.slice(1) : fixed;
}

function formatGamesBack(gb: number | null): string {
  if (gb === null || gb === 0) return "—";
  return Number.isInteger(gb) ? gb.toFixed(0) : gb.toFixed(1);
}

function formatGoalDiff(gd: number | null): string {
  if (gd === null) return "—";
  return gd > 0 ? `+${gd}` : String(gd);
}

const HEADER_CELL =
  "sticky top-0 z-10 bg-zinc-950 px-2 py-2 text-xs font-medium uppercase tracking-wider text-zinc-500";
const NUM_CELL = "px-2 py-1.5 text-right tabular-nums text-zinc-300";

function StandingsRow({
  row,
  sport,
  teamMeta,
  leagueId,
}: {
  row: StandingRow;
  sport: Sport;
  teamMeta: Record<string, TeamMeta>;
  leagueId: string;
}) {
  const openTeam = useOpenTeam();
  const openNation = useOpenNation();
  // Tennis/MMA rankings are individuals — no team/nation page to open.
  const clickable = sport !== "tennis" && sport !== "mma";
  const followed = row.team_id !== null;
  const meta = followed ? teamMeta[row.team_id as string] : undefined;
  const accent = meta?.color ?? FALLBACK_ACCENT;
  // Tennis/MMA rankings list players/fighters by name — no crest to show.
  const showLogo = sport !== "tennis" && sport !== "mma";
  // A followed team (carries an internal id) opens its detail overlay.
  const teamCell = (
    <>
      {showLogo && (
        <TeamLogo
          // Prefer the per-row crest the API now sends for every team;
          // fall back to a followed team's /teams metadata.
          logoUrl={row.logo_url ?? meta?.logo_url}
          name={row.team_name}
          abbreviation={row.abbreviation ?? meta?.abbreviation}
          color={row.color ?? meta?.color}
          size="sm"
        />
      )}
      <span
        className={followed ? "font-semibold text-zinc-100" : "text-zinc-300"}
      >
        {row.team_name}
      </span>
    </>
  );
  return (
    <tr
      className="sd-stagger-item"
      style={
        followed
          ? {
              backgroundColor: withAlpha(accent, 0.08),
              borderLeft: `2px solid ${accent}`,
            }
          : { borderLeft: "2px solid transparent" }
      }
    >
      <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">
        {row.rank}
      </td>
      <td className="px-2 py-1.5">
        {clickable ? (
          <button
            type="button"
            onClick={() =>
              followed
                ? openTeam(row.team_id as string)
                : openNation(leagueId, row.team_name)
            }
            className="flex items-center gap-2 text-left transition-colors hover:text-amber-400"
            title={`View ${row.team_name}`}
          >
            {teamCell}
          </button>
        ) : (
          <span className="flex items-center gap-2">{teamCell}</span>
        )}
      </td>
      {sport === "tennis" ? (
        <td className={`${NUM_CELL} font-medium text-zinc-100`}>
          {row.points ?? "—"}
        </td>
      ) : (
        <>
          <td className={NUM_CELL}>{row.wins}</td>
          {sport === "soccer" ? (
        <>
          <td className={NUM_CELL}>{row.draws ?? "—"}</td>
          <td className={NUM_CELL}>{row.losses}</td>
          <td className={NUM_CELL}>{formatGoalDiff(row.goal_diff)}</td>
          <td className={`${NUM_CELL} font-medium text-zinc-100`}>
            {row.points ?? "—"}
          </td>
        </>
      ) : sport === "hockey" ? (
        <>
          <td className={NUM_CELL}>{row.losses}</td>
          <td className={NUM_CELL}>{row.ot_losses ?? "—"}</td>
          <td className={`${NUM_CELL} font-medium text-zinc-100`}>
            {row.points ?? "—"}
          </td>
        </>
      ) : sport === "football" ? (
        <>
          <td className={NUM_CELL}>{row.losses}</td>
          <td className={NUM_CELL}>{row.draws ?? "—"}</td>
          <td className={NUM_CELL}>{formatPct(row.win_pct)}</td>
        </>
      ) : sport === "volleyball" ? (
        <>
          <td className={NUM_CELL}>{row.losses}</td>
          <td className={`${NUM_CELL} font-medium text-zinc-100`}>
            {row.points ?? "—"}
          </td>
        </>
      ) : (
        <>
          <td className={NUM_CELL}>{row.losses}</td>
          <td className={NUM_CELL}>{formatPct(row.win_pct)}</td>
          <td className={NUM_CELL}>{formatGamesBack(row.games_back)}</td>
        </>
      )}
        </>
      )}
    </tr>
  );
}

function StandingsTable({
  rows,
  sport,
  teamMeta,
  leagueId,
}: {
  rows: StandingRow[];
  sport: Sport;
  teamMeta: Record<string, TeamMeta>;
  leagueId: string;
}) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th className={`${HEADER_CELL} w-10 text-right`}>#</th>
          <th className={`${HEADER_CELL} text-left`}>
            {sport === "tennis" ? "Player" : "Team"}
          </th>
          {sport === "tennis" ? (
            <th className={`${HEADER_CELL} text-right`}>Pts</th>
          ) : (
            <>
              <th className={`${HEADER_CELL} text-right`}>W</th>
              {sport === "soccer" ? (
                <>
                  <th className={`${HEADER_CELL} text-right`}>D</th>
                  <th className={`${HEADER_CELL} text-right`}>L</th>
                  <th className={`${HEADER_CELL} text-right`}>GD</th>
                  <th className={`${HEADER_CELL} text-right`}>Pts</th>
                </>
              ) : sport === "hockey" ? (
                <>
                  <th className={`${HEADER_CELL} text-right`}>L</th>
                  <th className={`${HEADER_CELL} text-right`}>OTL</th>
                  <th className={`${HEADER_CELL} text-right`}>PTS</th>
                </>
              ) : sport === "football" ? (
                <>
                  <th className={`${HEADER_CELL} text-right`}>L</th>
                  <th className={`${HEADER_CELL} text-right`}>T</th>
                  <th className={`${HEADER_CELL} text-right`}>PCT</th>
                </>
              ) : sport === "volleyball" ? (
                <>
                  <th className={`${HEADER_CELL} text-right`}>L</th>
                  <th className={`${HEADER_CELL} text-right`}>Pts</th>
                </>
              ) : (
                <>
                  <th className={`${HEADER_CELL} text-right`}>L</th>
                  <th className={`${HEADER_CELL} text-right`}>PCT</th>
                  <th className={`${HEADER_CELL} text-right`}>GB</th>
                </>
              )}
            </>
          )}
        </tr>
      </thead>
      <tbody className="divide-y divide-zinc-800">
        {rows.map((row) => (
          <StandingsRow
            key={`${row.rank}-${row.team_name}`}
            row={row}
            sport={sport}
            teamMeta={teamMeta}
            leagueId={leagueId}
          />
        ))}
      </tbody>
    </table>
  );
}

interface Subgroup {
  name: string | null;
  rows: StandingRow[];
}
interface Group {
  name: string | null;
  // Whether ANY row in this group carries a division (subgroup). When false the
  // group renders as one flat table; when true it splits into per-division
  // mini-tables.
  nested: boolean;
  subgroups: Subgroup[];
}

/**
 * Bucket rows by `group`, then by `subgroup` within each group — preserving
 * first-seen order from the API at both levels. A group is `nested` only when
 * at least one of its rows has a non-null subgroup; otherwise its single
 * subgroup (keyed null) renders as one flat table (today's behavior).
 */
function groupStandings(rows: StandingRow[]): Group[] {
  const byGroup = new Map<string | null, Map<string | null, StandingRow[]>>();
  for (const row of rows) {
    let subs = byGroup.get(row.group);
    if (subs === undefined) {
      subs = new Map();
      byGroup.set(row.group, subs);
    }
    const bucket = subs.get(row.subgroup);
    if (bucket !== undefined) bucket.push(row);
    else subs.set(row.subgroup, [row]);
  }
  return [...byGroup.entries()].map(([name, subs]) => ({
    name,
    nested: [...subs.keys()].some((sub) => sub !== null),
    subgroups: [...subs.entries()].map(([subName, subRows]) => ({
      name: subName,
      rows: subRows,
    })),
  }));
}

export default function StandingsView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();
  const setupLeaguesQuery = useSetupLeagues();
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const leagues = teamsQuery.data?.leagues ?? [];
  // Ignore a selection that no longer exists — re-running setup replaces
  // the followed leagues, which would leave `selected` pointing at a 404.
  const leagueId = leagues.some((league) => league.id === selected)
    ? selected
    : leagues[0]?.id;
  const league = leagues.find((entry) => entry.id === leagueId);
  // Past-season archive picker (espn team sports only).
  const [seasonSel, setSeasonSel] = useState<string>(CURRENT_SEASON);
  const archives = supportsArchives(league);
  const seasonYear =
    archives && seasonSel !== CURRENT_SEASON ? Number(seasonSel) : undefined;
  const standingsQuery = useStandings(leagueId, seasonYear);

  // League logos for the selector (the /teams rows don't carry one).
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

  const teamMeta = useMemo(() => {
    const map: Record<string, TeamMeta> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      map[team.id] = {
        logo_url: team.logo_url,
        abbreviation: team.abbreviation,
        color: team.color,
      };
    }
    return map;
  }, [teamsQuery.data]);

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
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-zinc-500">No leagues followed yet — follow a league to see its standings here.</p>
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

  const standings = standingsQuery.data;

  let body: ReactNode;
  if (standingsQuery.isError) {
    body =
      seasonYear !== undefined ? (
        <p className="text-sm text-zinc-500">
          The {seasonSel} season isn&apos;t available from the provider for
          this league.
        </p>
      ) : (
        <p className="text-sm text-red-400">
          Failed to load standings:{" "}
          {standingsQuery.error?.message ?? "unknown error"}
        </p>
      );
  } else if (!standings) {
    body = <p className="text-sm text-zinc-500">Loading standings…</p>;
  } else if (standings.rows.length === 0) {
    body =
      standings.sport === "mma" ? (
        <p className="text-sm text-zinc-500">
          Rankings not available for this league.
        </p>
      ) : (
        <p className="text-sm text-zinc-500">
          No standings yet for {standings.league_name}. Standings refresh once a
          day, so they will appear after the next daily refresh.
        </p>
      );
  } else {
    const groups = groupStandings(standings.rows);
    // Flat single table only when every row is both group- and subgroup-less.
    const grouped =
      groups.some((group) => group.name !== null) ||
      groups.some((group) => group.nested);
    body = (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-zinc-200">
              {standings.league_name}
            </h2>
            {standings.is_stale && (
              <span
                title="The source hasn't updated recently — this may be out of date."
                className="inline-flex items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400"
              >
                Possibly out of date
              </span>
            )}
          </div>
          <span className="text-xs text-zinc-500">{standings.season}</span>
        </div>
        {grouped ? (
          groups.map((group) => (
            <div
              key={group.name ?? "ungrouped"}
              className="flex flex-col gap-2"
            >
              {group.name !== null && (
                <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
                  {group.name}
                </h3>
              )}
              {group.nested ? (
                group.subgroups.map((sub) => (
                  <div
                    key={sub.name ?? "undivided"}
                    className="flex flex-col gap-1"
                  >
                    {sub.name !== null && (
                      <h4 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                        {sub.name}
                      </h4>
                    )}
                    <StandingsTable
                      rows={sub.rows}
                      sport={standings.sport}
                      teamMeta={teamMeta}
                      leagueId={standings.league_id}
                    />
                  </div>
                ))
              ) : (
                <StandingsTable
                  rows={group.subgroups[0]?.rows ?? []}
                  sport={standings.sport}
                  teamMeta={teamMeta}
                  leagueId={standings.league_id}
                />
              )}
            </div>
          ))
        ) : (
          <StandingsTable
            rows={standings.rows}
            sport={standings.sport}
            teamMeta={teamMeta}
            leagueId={standings.league_id}
          />
        )}
        {standings.fetched_at !== null && (
          <p className="text-xs text-zinc-500">
            Updated {formatDateTime(standings.fetched_at)}
          </p>
        )}
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          options={leagueOptions}
          value={leagueId}
          onChange={(id) => {
            setSelected(id);
            setSeasonSel(CURRENT_SEASON); // a new league starts on its live table
          }}
          ariaLabel="Choose league"
        />
        {archives && (
          <Select
            options={seasonOptions()}
            value={seasonSel}
            onChange={setSeasonSel}
            ariaLabel="Choose season"
          />
        )}
      </div>
      {body}
    </div>
  );
}
