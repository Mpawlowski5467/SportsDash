/**
 * Full-screen onboarding wizard.
 *
 * Flow: countries -> leagues -> teams -> review -> POST /setup/follow,
 * ending on a syncing screen that invalidates every cached query and polls
 * /today until games appear (or ~20s pass), then onComplete() fires.
 *
 * A first run starts empty. "manage" mode is the same wizard opened on the
 * followed set (`initial`, loaded by ManageTeamsWizard): the save
 * reconciles, keeping whatever is still selected and deleting the stored
 * data of whatever is not, so the editor has to start from what is followed
 * and the review step has to say what a save would unfollow.
 *
 * In "manage" mode a close button (or ESC) lets the user bail out without
 * touching anything — except while the save is in flight, when both are
 * inert; "first-run" mode has no escape hatch by design, though focus is
 * still trapped and the page scroll locked like any other overlay.
 */

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { CatalogLeague } from "../../types";
import { useSetupLeagues } from "../../hooks";
import { apiErrorMessage } from "./errors";
import { useModalChrome } from "../modalChrome";
import {
  EMPTY_SELECTION,
  clearLeagueTeams,
  followAllIds,
  followedLeagues,
  pickTeamLeagueIds,
  rememberedLeagueIds,
  removedFromSeed,
  toFollowRequest,
  toggleFollowAll,
  toggleLeague,
  toggleTeam,
  totalSelected,
  type SelectionState,
  type WizardSeed,
} from "./selection";
import CountryStep from "./CountryStep";
import LeagueStep from "./LeagueStep";
import TeamsStep from "./TeamsStep";
import ReviewStep from "./ReviewStep";
import SyncingStep from "./SyncingStep";
import WizardFrame from "./WizardFrame";

type Step = "countries" | "leagues" | "teams" | "review" | "syncing";

/** Every step that can exist; a given run uses a subset (see `steps`). */
const ALL_STEPS: Step[] = [
  "countries",
  "leagues",
  "teams",
  "review",
  "syncing",
];

/**
 * The country step is offered only when some sport is actually organised by
 * country — today that is soccer alone (40 of 55 leagues across 21
 * countries), while the NBA, the NFL and the tours have one country or none.
 * Deriving it rather than hardcoding "soccer" means a future league in a new
 * country turns the step on by itself, and a catalog trimmed back to one
 * country per sport turns it off again instead of showing a map with a
 * single pin on it.
 */
function countryStepApplies(leagues: CatalogLeague[]): boolean {
  const bySport = new Map<string, Set<string>>();
  for (const league of leagues) {
    if (league.country_code === null) continue;
    const seen = bySport.get(league.sport) ?? new Set<string>();
    seen.add(league.country_code);
    bySport.set(league.sport, seen);
  }
  return [...bySport.values()].some((codes) => codes.size > 1);
}

const STEP_TITLES: Record<Step, string> = {
  countries: "Countries",
  leagues: "Leagues",
  teams: "Teams",
  review: "Review",
  syncing: "Syncing",
};

export interface Props {
  mode: "first-run" | "manage";
  // What the wizard opens on. Read once, on mount: it seeds the wizard's
  // own state, so a new value later neither resets nor remounts anything.
  // Absent for a first run, which starts empty on the first step.
  initial?: WizardSeed;
  onComplete: () => void;
  onClose?: () => void;
}

const NO_CLOSE = () => {};

function StepIndicator({ current, steps }: { current: Step; steps: Step[] }) {
  const index = steps.indexOf(current);
  return (
    // "Step 2 of 4 · Teams" rather than a lit dot: the dots alone showed
    // progress without saying how much was left, which is the one thing a
    // first-run wizard has to answer.
    <div className="flex items-center gap-3">
      <span className="sd-micro font-semibold uppercase tracking-wider text-zinc-500">
        Step {index + 1} of {steps.length}
      </span>
      <span className="sd-meta font-medium text-zinc-200">
        {STEP_TITLES[current]}
      </span>
      <div className="flex items-center gap-1.5" aria-hidden="true">
        {steps.map((step, i) => (
          <span
            key={step}
            className={
              i === index
                ? "h-1.5 w-5 rounded-full bg-amber-400"
                : i < index
                  ? "h-1.5 w-1.5 rounded-full bg-amber-400/50"
                  : "h-1.5 w-1.5 rounded-full bg-zinc-700"
            }
          />
        ))}
      </div>
    </div>
  );
}

export default function OnboardingWizard({
  mode,
  initial,
  onComplete,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const [followPending, setFollowPending] = useState(false);
  const [followError, setFollowError] = useState<string | null>(null);
  // Closing is off while the save is in flight. The POST cannot be recalled,
  // so closing then would leave a save that lands with nobody to act on it:
  // no syncing step if it succeeds, and no error if it fails.
  const close = followPending ? undefined : onClose;
  // Shared modal chrome: ESC stack, scroll lock, focus trap, focus restore.
  // First-run mode deliberately stays non-dismissible (ESC hits a no-op)
  // but still traps focus and locks scroll like every other overlay.
  const dialogRef = useModalChrome(close ?? NO_CLOSE);
  const leaguesQuery = useSetupLeagues();
  const catalog = useMemo(
    () => leaguesQuery.data?.leagues ?? [],
    [leaguesQuery.data],
  );
  const countries = useMemo(
    () => leaguesQuery.data?.countries ?? [],
    [leaguesQuery.data],
  );
  const hasCountryStep = useMemo(
    () => countryStepApplies(catalog),
    [catalog],
  );
  const steps = useMemo(
    () =>
      hasCountryStep
        ? ALL_STEPS
        : ALL_STEPS.filter((entry) => entry !== "countries"),
    [hasCountryStep],
  );

  const [step, setStep] = useState<Step>(initial?.step ?? "countries");

  // The catalog arrives after mount, so the opening step is a guess until it
  // does. If this run has no country step, step past it — but only from the
  // country step itself, so a user who has already moved on is never yanked
  // backwards by a late refetch.
  useEffect(() => {
    if (!hasCountryStep && step === "countries" && leaguesQuery.isSuccess) {
      setStep("leagues");
    }
  }, [hasCountryStep, step, leaguesQuery.isSuccess]);
  const [selectedCountryCodes, setSelectedCountryCodes] = useState<string[]>(
    initial?.selectedCountryCodes ?? [],
  );
  // Leagues, whole-league flags and team picks live in one value because
  // every change to them is a pure transition in selection.ts. It remembers
  // more than it follows — a deselected league keeps its picks — so the
  // steps are handed that module's effective view, never the raw fields.
  const [selection, setSelection] = useState<SelectionState>(
    initial ?? EMPTY_SELECTION,
  );

  const pickLeagueIds = pickTeamLeagueIds(selection);

  const toggleCountry = (code: string) => {
    setSelectedCountryCodes((current) =>
      current.includes(code)
        ? current.filter((existing) => existing !== code)
        : [...current, code],
    );
  };

  const confirmFollow = async () => {
    setFollowPending(true);
    setFollowError(null);
    try {
      await api.setupFollow(toFollowRequest(selection));
      // Here rather than left to the syncing step, because this line runs
      // whether or not the wizard is still mounted and that step only if it
      // is. A save that landed unseen would leave the dashboard on the old
      // followed set until a reload: `/teams` never goes stale by itself.
      void queryClient.invalidateQueries();
      setStep("syncing");
    } catch (err) {
      setFollowError(apiErrorMessage(err));
    } finally {
      setFollowPending(false);
    }
  };

  return (
    <WizardFrame
      dialogRef={dialogRef}
      indicator={<StepIndicator current={step} steps={steps} />}
      onClose={mode === "manage" ? onClose : undefined}
      closeDisabled={followPending}
    >
      {step === "countries" && (
        <CountryStep
          countries={countries}
          leagues={catalog}
          selectedCodes={selectedCountryCodes}
          onToggle={toggleCountry}
          onContinue={() => setStep("leagues")}
          onSkip={() => {
            setSelectedCountryCodes([]);
            setStep("leagues");
          }}
        />
      )}
      {step === "leagues" && (
        <LeagueStep
          countryCodes={selectedCountryCodes}
          rememberedIds={rememberedLeagueIds(selection)}
          selectedIds={selection.selectedLeagueIds}
          followAllIds={followAllIds(selection)}
          onToggle={(leagueId) =>
            setSelection((current) => toggleLeague(current, leagueId))
          }
          onToggleFollowAll={(leagueId) =>
            setSelection((current) => toggleFollowAll(current, leagueId))
          }
          onBack={hasCountryStep ? () => setStep("countries") : undefined}
          onContinue={() =>
            setStep(pickLeagueIds.length > 0 ? "teams" : "review")
          }
        />
      )}
      {step === "teams" && (
        <TeamsStep
          leagueIds={pickLeagueIds}
          teamsByLeague={selection.teamsByLeague}
          seededTeamsByLeague={initial?.teamsByLeague}
          onToggleTeam={(leagueId, team) =>
            setSelection((current) => toggleTeam(current, leagueId, team))
          }
          onClearLeague={(leagueId) =>
            setSelection((current) => clearLeagueTeams(current, leagueId))
          }
          totalSelected={totalSelected(selection)}
          onBack={() => setStep("leagues")}
          onContinue={() => setStep("review")}
        />
      )}
      {step === "review" && (
        <ReviewStep
          mode={mode}
          follows={followedLeagues(selection)}
          removed={
            initial === undefined ? [] : removedFromSeed(initial, selection)
          }
          pending={followPending}
          error={followError}
          onBack={() => setStep(pickLeagueIds.length > 0 ? "teams" : "leagues")}
          onConfirm={() => void confirmFollow()}
        />
      )}
      {step === "syncing" && <SyncingStep onDone={onComplete} />}
    </WizardFrame>
  );
}
