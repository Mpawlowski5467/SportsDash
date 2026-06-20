import { useMemo, useState } from "react";
import { useSetupLeagues, useSetupTeams } from "../../hooks";
import type { CatalogTeam } from "../../types";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";
import { apiErrorMessage } from "./errors";

export interface Props {
  leagueIds: string[];
  teamsByLeague: Record<string, CatalogTeam[]>;
  onToggleTeam: (leagueId: string, team: CatalogTeam) => void;
  totalSelected: number;
  onBack: () => void;
  onContinue: () => void;
}

/** Naive plural for the entity noun ("team" -> "teams"). */
function pluralize(noun: string): string {
  return noun.endsWith("s") ? noun : `${noun}s`;
}

/** Team logo with graceful fallback to an abbreviation chip. */
function TeamLogo({ team }: { team: CatalogTeam }) {
  const [failed, setFailed] = useState(false);

  if (team.logo_url === null || failed) {
    return (
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-[11px] font-bold text-zinc-300">
        {team.abbreviation}
      </span>
    );
  }
  return (
    <img
      src={team.logo_url}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-10 w-10 shrink-0 object-contain"
    />
  );
}

function SectionSkeleton() {
  return (
    <div className="grid animate-pulse grid-cols-2 gap-2 sm:grid-cols-3">
      {Array.from({ length: 6 }, (_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2"
        >
          <div className="h-10 w-10 shrink-0 rounded-full bg-zinc-800" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="h-4 w-3/4 rounded bg-zinc-800" />
            <div className="h-3 w-1/3 rounded bg-zinc-800/70" />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * One selected league's team grid: search filter, multi-select cards.
 * Each section owns its useSetupTeams query, so all selected leagues
 * fetch in parallel as soon as the step mounts.
 */
function LeagueSection({
  leagueId,
  leagueName,
  entityNoun,
  selected,
  onToggle,
}: {
  leagueId: string;
  leagueName: string;
  entityNoun: string;
  selected: CatalogTeam[];
  onToggle: (team: CatalogTeam) => void;
}) {
  const teamsQuery = useSetupTeams(leagueId);
  const [search, setSearch] = useState("");
  const nounPlural = pluralize(entityNoun);

  const visible = useMemo(() => {
    const teams = teamsQuery.data?.teams ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return teams;
    }
    return teams.filter(
      (team) =>
        team.name.toLowerCase().includes(needle) ||
        team.abbreviation.toLowerCase().includes(needle),
    );
  }, [teamsQuery.data, search]);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-200">
          {leagueName}
          {selected.length > 0 && (
            <span className="ml-2 text-xs font-medium text-amber-400">
              {selected.length} selected
            </span>
          )}
        </h3>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={`Search ${nounPlural}…`}
          aria-label={`Search ${leagueName} ${nounPlural}`}
          className="w-48 rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-amber-400/60 focus:outline-none"
        />
      </div>

      {teamsQuery.isPending ? (
        <SectionSkeleton />
      ) : teamsQuery.isError ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3">
          <p className="min-w-0 truncate text-sm text-red-300">
            Couldn&rsquo;t load {nounPlural} — {apiErrorMessage(teamsQuery.error)}
          </p>
          <button
            type="button"
            onClick={() => void teamsQuery.refetch()}
            className={`shrink-0 ${SECONDARY_BUTTON}`}
          >
            Retry
          </button>
        </div>
      ) : visible.length === 0 ? (
        <p className="py-4 text-sm text-zinc-500">
          No {nounPlural} match &ldquo;{search}&rdquo;.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {visible.map((team) => {
            const isSelected = selected.some(
              (chosen) => chosen.provider_key === team.provider_key,
            );
            return (
              <button
                key={team.provider_key}
                type="button"
                onClick={() => onToggle(team)}
                aria-pressed={isSelected}
                className={
                  isSelected
                    ? "flex items-center gap-3 rounded-lg border border-amber-400/60 bg-amber-500/10 px-3 py-2 text-left"
                    : "flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-left transition-colors hover:border-zinc-600"
                }
              >
                <TeamLogo team={team} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-zinc-100">
                    {team.name}
                  </span>
                  <span className="block text-xs text-zinc-500">
                    {team.abbreviation}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** Per-league team grids with a running total and Continue gate. */
export default function TeamsStep({
  leagueIds,
  teamsByLeague,
  onToggleTeam,
  totalSelected,
  onBack,
  onContinue,
}: Props) {
  const leaguesQuery = useSetupLeagues();

  const leagueById = useMemo(() => {
    const map: Record<string, { name: string; noun: string }> = {};
    for (const league of leaguesQuery.data?.leagues ?? []) {
      map[league.id] = { name: league.name, noun: league.entity_noun };
    }
    return map;
  }, [leaguesQuery.data]);

  // Heading/footer noun: shared noun when all picked leagues agree
  // ("players"/"fighters"), else the generic "teams".
  const nouns = new Set(
    leagueIds.map((id) => leagueById[id]?.noun ?? "team"),
  );
  const headingNoun = pluralize(nouns.size === 1 ? [...nouns][0] : "team");

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-zinc-100">
          Pick your {headingNoun}
        </h2>
        <p className="mt-1 text-sm text-zinc-400">
          Select at least one {headingNoun === "teams" ? "team" : headingNoun.slice(0, -1)}{" "}
          to follow across your leagues.
        </p>
      </header>

      <div className="space-y-8">
        {leagueIds.map((leagueId) => (
          <LeagueSection
            key={leagueId}
            leagueId={leagueId}
            leagueName={leagueById[leagueId]?.name ?? leagueId}
            entityNoun={leagueById[leagueId]?.noun ?? "team"}
            selected={teamsByLeague[leagueId] ?? []}
            onToggle={(team) => onToggleTeam(leagueId, team)}
          />
        ))}
      </div>

      <footer className="flex items-center justify-between border-t border-zinc-800 pt-4">
        <button type="button" onClick={onBack} className={SECONDARY_BUTTON}>
          Back
        </button>
        <div className="flex items-center gap-3">
          <span className="text-sm tabular-nums text-zinc-400">
            {totalSelected === 1
              ? `1 ${headingNoun === "teams" ? "team" : headingNoun.slice(0, -1)} selected`
              : `${totalSelected} ${headingNoun} selected`}
          </span>
          <button
            type="button"
            onClick={onContinue}
            disabled={totalSelected === 0}
            className={PRIMARY_BUTTON}
          >
            Continue
          </button>
        </div>
      </footer>
    </div>
  );
}
