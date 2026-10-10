import { useMemo } from "react";
import { useSetupLeagues } from "../../hooks";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";
import type { LeagueFollow, RemovedFollow } from "./selection";
import MiniSpinner from "../loaders/MiniSpinner";

export interface Props {
  mode: "first-run" | "manage";
  // What the save will follow: each league in whole or by its picks, with
  // the leagues that ended up with neither already left out.
  follows: LeagueFollow[];
  // What the save will unfollow, compared with the set the wizard opened on.
  // Always empty for a first run, which opened on nothing.
  removed: RemovedFollow[];
  pending: boolean;
  error: string | null;
  onBack: () => void;
  onConfirm: () => void;
}

/** Confirmation screen: chips of chosen teams + whole-competition follows. */
export default function ReviewStep({
  mode,
  follows,
  removed,
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

  const hasSelections = follows.length > 0;

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-zinc-100">
          Review your picks
        </h2>
        <p className="mt-1 text-sm text-zinc-400">
          {mode === "manage"
            ? "SportsDash will follow exactly these. Everything you kept stays as it is."
            : "SportsDash will follow these."}
        </p>
      </header>

      {hasSelections ? (
        <div className="space-y-4">
          {follows.map((follow) => (
            <section key={follow.leagueId}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                {nameById[follow.leagueId] ?? follow.leagueId}
              </h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {follow.followAll ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/60 bg-emerald-500/15 px-3 py-1 text-sm text-emerald-200">
                    <span aria-hidden="true">✓</span>
                    <span className="font-medium">Entire league — every game</span>
                  </span>
                ) : (
                  follow.teams.map((team) => (
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

      {/* The save deletes what it unfollows, and nothing above shows an
          absence: a league toggled off by a stray click simply is not
          listed. So what is going is spelled out, right above Confirm. */}
      {removed.length > 0 && (
        <div className="space-y-4 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3">
          <p className="text-sm font-medium text-red-300">
            Will be unfollowed — their stored games, rosters and alert
            settings are deleted
          </p>
          {removed.map((entry) => (
            <section key={entry.leagueId}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                {nameById[entry.leagueId] ?? entry.leagueId}
                {entry.leagueRemoved && " — the whole league"}
              </h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {entry.followAllRemoved && (
                  <span className="inline-flex items-center rounded-full border border-red-900/60 bg-zinc-900 px-3 py-1 text-sm font-medium text-zinc-200">
                    Entire league — every game
                  </span>
                )}
                {entry.teams.map((team) => (
                  <span
                    key={team.provider_key}
                    className="inline-flex items-center gap-1.5 rounded-full border border-red-900/60 bg-zinc-900 px-3 py-1 text-sm text-zinc-200"
                  >
                    <span className="font-medium">{team.name}</span>
                    <span className="text-xs text-zinc-500">
                      {team.abbreviation}
                    </span>
                  </span>
                ))}
              </div>
            </section>
          ))}
        </div>
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
