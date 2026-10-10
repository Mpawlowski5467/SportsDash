/**
 * The wizard as an editor: "Manage teams".
 *
 * A save keeps what is still selected and deletes the stored data of what
 * is not, so the editor must open on the followed set — opened empty, it
 * would have the user rebuild their follows from memory and unfollow
 * whatever they forgot. This component fetches that set
 * (`GET /setup/selection`) and hands it to the wizard as its seed.
 *
 * The seed is latched: the wizard copies it into its own state on mount, so
 * it has to be complete before the wizard exists and must not change under
 * it afterwards. Until then a loading panel stands in — with its OWN modal
 * chrome, and never mounted beside the wizard. The Tab trap is not
 * stack-aware the way ESC is: every mounted chrome handles the key against
 * its own container, so one left up beside the wizard could swallow it.
 */

import { useState } from "react";
import { useSetupSelection } from "../../hooks";
import { SECONDARY_BUTTON } from "./buttons";
import { apiErrorMessage } from "./errors";
import { useModalChrome } from "../modalChrome";
import { seedFromSelection, type WizardSeed } from "./selection";
import MiniSpinner from "../loaders/MiniSpinner";
import OnboardingWizard from "./OnboardingWizard";
import WizardFrame from "./WizardFrame";

export interface Props {
  onComplete: () => void;
  onClose: () => void;
}

/** Stand-in for the wizard while the followed set loads, or fails to. */
function SelectionLoadingPanel({
  error,
  onRetry,
  onClose,
}: {
  error: string | null;
  onRetry: () => void;
  onClose: () => void;
}) {
  const dialogRef = useModalChrome(onClose);
  return (
    <WizardFrame dialogRef={dialogRef} onClose={onClose}>
      {error === null ? (
        <div className="flex justify-center py-16">
          <MiniSpinner size={20} label="Loading your teams" />
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3">
          <p className="min-w-0 truncate text-sm text-red-300">
            Couldn&rsquo;t load what you follow — {error}
          </p>
          <button
            type="button"
            onClick={onRetry}
            className={`shrink-0 ${SECONDARY_BUTTON}`}
          >
            Retry
          </button>
        </div>
      )}
    </WizardFrame>
  );
}

export default function ManageTeamsWizard({ onComplete, onClose }: Props) {
  const selectionQuery = useSetupSelection();
  const [seed, setSeed] = useState<WizardSeed | null>(null);

  // Latch the seed from the first response fetched for THIS open, and only
  // from a response. A failed request must never fall through to an empty
  // seed: the wizard would open as if nothing were followed, and saving one
  // pick from there would unfollow everything else. An error stays on the
  // panel until Retry succeeds or the user closes.
  //
  // Adjusting state during render (as App does for visited tabs): React
  // re-renders before committing, so the panel and the wizard never share a
  // frame. Once set the seed is never replaced — the query refetches
  // whenever the app invalidates its cache, the save itself included, and a
  // new seed must neither reset nor remount a wizard the user is editing.
  if (
    seed === null &&
    selectionQuery.isSuccess &&
    selectionQuery.isFetchedAfterMount
  ) {
    setSeed(seedFromSelection(selectionQuery.data));
  }

  if (seed === null) {
    return (
      <SelectionLoadingPanel
        error={
          selectionQuery.isError ? apiErrorMessage(selectionQuery.error) : null
        }
        onRetry={() => void selectionQuery.refetch()}
        onClose={onClose}
      />
    );
  }

  return (
    <OnboardingWizard
      mode="manage"
      initial={seed}
      onComplete={onComplete}
      onClose={onClose}
    />
  );
}
