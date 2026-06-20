import { useMemo } from "react";
import { useSetupLeagues } from "../../hooks";
import type { CatalogTeam } from "../../types";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";
import MiniSpinner from "../loaders/MiniSpinner";

export interface Props {
  leagueIds: string[];
  followAllIds: string[];
  teamsByLeague: Record<string, CatalogTeam[]>;
  pending: boolean;
  error: string | null;
  onBack: () => void;
  onConfirm: () => void;
}

/** Confirmation screen: chips of chosen teams + whole-competition follows. */
export default function ReviewStep({
  leagueIds,
  followAllIds,
  teamsByLeague,
  pending,
  error,
  onBack,
  onConfirm,
}: Props) {
  const leaguesQuery = useSetupLeagues();

  const nameById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const league of leaguesQuery.data?.leagues ?? []) {
      map[league.id] = league.name;
    }
    return map;
  }, [leaguesQuery.data]);

  // A selected league shows up either as a whole-competition follow or as a
  // group of picked teams (dropped if it ended up with none).
  const groups = leagueIds
    .map((leagueId) => ({
      id: leagueId,
      name: nameById[leagueId] ?? leagueId,
      followAll: followAllIds.includes(leagueId),
      teams: teamsByLeague[leagueId] ?? [],
    }))
    .filter((group) => group.followAll || group.teams.length > 0);

  const hasSelections = groups.length > 0;

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-zinc-100">
          Review your picks
        </h2>
        <p className="mt-1 text-sm text-zinc-400">
          SportsDash will follow these. Confirming replaces anything you
          followed before.
        </p>
      </header>

      {hasSelections ? (
        <div className="space-y-4">
          {groups.map((group) => (
            <section key={group.id}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                {group.name}
              </h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {group.followAll ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/60 bg-emerald-500/15 px-3 py-1 text-sm text-emerald-200">
                    <span aria-hidden="true">✓</span>
                    <span className="font-medium">Entire league — every game</span>
                  </span>
                ) : (
                  group.teams.map((team) => (
                    <span
                      key={team.provider_key}
                      className="inline-flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-sm text-zinc-200"
                    >
                      <span className="font-medium">{team.name}</span>
                      <span className="text-xs text-zinc-500">
                        {team.abbreviation}
                      </span>
                    </span>
                  ))
                )}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-sm text-zinc-400">
          Nothing selected yet — go back and pick a team or follow a whole
          league.
        </p>
      )}

      {error !== null && (
        <div className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2">
          <p className="text-sm text-red-300">
            Couldn&rsquo;t save your picks — {error}
          </p>
        </div>
      )}

      <footer className="flex items-center justify-between border-t border-zinc-800 pt-4">
        <button
          type="button"
          onClick={onBack}
          disabled={pending}
          className={SECONDARY_BUTTON}
        >
          Back
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending || !hasSelections}
          className={PRIMARY_BUTTON}
        >
          {pending && <MiniSpinner size={16} />}
          {pending ? "Saving…" : "Confirm"}
        </button>
      </footer>
    </div>
  );
}
