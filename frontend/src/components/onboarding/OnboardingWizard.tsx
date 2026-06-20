/**
 * Full-screen onboarding wizard.
 *
 * Flow: leagues -> teams -> review -> POST /setup/follow, ending on a
 * syncing screen that invalidates every cached query and polls /today
 * until games appear (or ~20s pass), then onComplete() fires. In "manage"
 * mode a close button lets the user bail out without touching anything;
 * "first-run" mode has no escape hatch by design.
 */

import { useState } from "react";
import { api } from "../../api";
import type { CatalogTeam, FollowSelection } from "../../types";
import { apiErrorMessage } from "./errors";
import LeagueStep from "./LeagueStep";
import TeamsStep from "./TeamsStep";
import ReviewStep from "./ReviewStep";
import SyncingStep from "./SyncingStep";

type Step = "leagues" | "teams" | "review" | "syncing";

const STEP_ORDER: Step[] = ["leagues", "teams", "review", "syncing"];

const STEP_TITLES: Record<Step, string> = {
  leagues: "Leagues",
  teams: "Teams",
  review: "Review",
  syncing: "Syncing",
};

export interface Props {
  mode: "first-run" | "manage";
  onComplete: () => void;
  onClose?: () => void;
}

function StepIndicator({ current }: { current: Step }) {
  const index = STEP_ORDER.indexOf(current);
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-400">
        {STEP_TITLES[current]}
      </span>
      <div className="flex items-center gap-1.5" aria-hidden="true">
        {STEP_ORDER.map((step, i) => (
          <span
            key={step}
            className={
              i === index
                ? "h-1.5 w-4 rounded-full bg-amber-400"
                : i < index
                  ? "h-1.5 w-1.5 rounded-full bg-amber-400/40"
                  : "h-1.5 w-1.5 rounded-full bg-zinc-700"
            }
          />
        ))}
      </div>
    </div>
  );
}

export default function OnboardingWizard({ mode, onComplete, onClose }: Props) {
  const [step, setStep] = useState<Step>("leagues");
  const [selectedLeagueIds, setSelectedLeagueIds] = useState<string[]>([]);
  // Subset of selectedLeagueIds in whole-competition mode: these follow the
  // entire competition (follow_all:true) and skip the team-picking step.
  const [followAllLeagueIds, setFollowAllLeagueIds] = useState<string[]>([]);
  const [teamsByLeague, setTeamsByLeague] = useState<
    Record<string, CatalogTeam[]>
  >({});
  const [followPending, setFollowPending] = useState(false);
  const [followError, setFollowError] = useState<string | null>(null);

  // Leagues that still need a team-picking grid (selected, not follow-all).
  const pickTeamLeagueIds = selectedLeagueIds.filter(
    (id) => !followAllLeagueIds.includes(id),
  );

  const totalSelected = pickTeamLeagueIds.reduce(
    (sum, id) => sum + (teamsByLeague[id] ?? []).length,
    0,
  );

  const dropLeagueTeams = (leagueId: string) => {
    setTeamsByLeague((prev) => {
      if (!(leagueId in prev)) return prev;
      const next = { ...prev };
      delete next[leagueId];
      return next;
    });
  };

  const toggleLeague = (leagueId: string) => {
    const deselecting = selectedLeagueIds.includes(leagueId);
    setSelectedLeagueIds(
      deselecting
        ? selectedLeagueIds.filter((id) => id !== leagueId)
        : [...selectedLeagueIds, leagueId],
    );
    if (deselecting) {
      dropLeagueTeams(leagueId);
      setFollowAllLeagueIds((prev) => prev.filter((id) => id !== leagueId));
    }
  };

  // Whole-competition toggle on a chip: marking follow-all selects the
  // league (if needed) and clears any teams picked for it; unmarking leaves
  // it selected in pick-teams mode.
  const toggleFollowAll = (leagueId: string) => {
    const enabling = !followAllLeagueIds.includes(leagueId);
    setFollowAllLeagueIds((prev) =>
      enabling ? [...prev, leagueId] : prev.filter((id) => id !== leagueId),
    );
    if (enabling) {
      if (!selectedLeagueIds.includes(leagueId)) {
        setSelectedLeagueIds((prev) => [...prev, leagueId]);
      }
      dropLeagueTeams(leagueId);
    }
  };

  const toggleTeam = (leagueId: string, team: CatalogTeam) => {
    setTeamsByLeague((prev) => {
      const current = prev[leagueId] ?? [];
      const exists = current.some(
        (chosen) => chosen.provider_key === team.provider_key,
      );
      return {
        ...prev,
        [leagueId]: exists
          ? current.filter(
              (chosen) => chosen.provider_key !== team.provider_key,
            )
          : [...current, team],
      };
    });
  };

  const confirmFollow = async () => {
    setFollowPending(true);
    setFollowError(null);
    try {
      const selections: FollowSelection[] = selectedLeagueIds
        .map((leagueId): FollowSelection | null => {
          if (followAllLeagueIds.includes(leagueId)) {
            return {
              league_id: leagueId,
              team_provider_keys: [],
              follow_all: true,
            };
          }
          const teams = teamsByLeague[leagueId] ?? [];
          if (teams.length === 0) return null;
          return {
            league_id: leagueId,
            team_provider_keys: teams.map((team) => team.provider_key),
          };
        })
        .filter((selection): selection is FollowSelection => selection !== null);
      await api.setupFollow({ selections });
      setStep("syncing");
    } catch (err) {
      setFollowError(apiErrorMessage(err));
    } finally {
      setFollowPending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-zinc-950 text-zinc-100">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6">
        <header className="mb-6 flex items-center gap-4">
          <span className="select-none whitespace-nowrap text-base font-bold tracking-tight">
            Sports<span className="text-amber-400">Dash</span>
          </span>
          <div className="ml-auto flex items-center gap-3">
            <StepIndicator current={step} />
            {mode === "manage" && onClose !== undefined && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close setup"
                title="Close setup"
                className="rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-4 w-4"
                  aria-hidden="true"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </button>
            )}
          </div>
        </header>

        <div className="flex-1 rounded-xl border border-zinc-800 bg-zinc-900/40 p-6">
          {step === "leagues" && (
            <LeagueStep
              selectedIds={selectedLeagueIds}
              followAllIds={followAllLeagueIds}
              onToggle={toggleLeague}
              onToggleFollowAll={toggleFollowAll}
              onContinue={() =>
                setStep(pickTeamLeagueIds.length > 0 ? "teams" : "review")
              }
            />
          )}
          {step === "teams" && (
            <TeamsStep
              leagueIds={pickTeamLeagueIds}
              teamsByLeague={teamsByLeague}
              onToggleTeam={toggleTeam}
              totalSelected={totalSelected}
              onBack={() => setStep("leagues")}
              onContinue={() => setStep("review")}
            />
          )}
          {step === "review" && (
            <ReviewStep
              leagueIds={selectedLeagueIds}
              followAllIds={followAllLeagueIds}
              teamsByLeague={teamsByLeague}
              pending={followPending}
              error={followError}
              onBack={() =>
                setStep(pickTeamLeagueIds.length > 0 ? "teams" : "leagues")
              }
              onConfirm={() => void confirmFollow()}
            />
          )}
          {step === "syncing" && <SyncingStep onDone={onComplete} />}
        </div>
      </div>
    </div>
  );
}
