import type { ReactNode, RefObject } from "react";

export interface Props {
  // From the caller's own useModalChrome. The frame is only the markup: each
  // screen that fills it owns its chrome, so that ESC always answers to
  // whatever is on screen.
  dialogRef: RefObject<HTMLDivElement | null>;
  // Right of the wordmark — the wizard's step indicator.
  indicator?: ReactNode;
  // Renders the close button. Absent for a first run, which has no way out.
  onClose?: () => void;
  closeDisabled?: boolean;
  children: ReactNode;
}

/**
 * The setup overlay's full-screen shell: wordmark, close button, body.
 *
 * Shared by the wizard and by the panel that stands in for it while the
 * manage-mode selection loads, so the editor opens as one screen whose body
 * fills in rather than as two that swap.
 */
export default function WizardFrame({
  dialogRef,
  indicator,
  onClose,
  closeDisabled = false,
  children,
}: Props) {
  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label="SportsDash setup"
      className="fixed inset-0 z-50 overflow-y-auto bg-zinc-950 text-zinc-100 outline-none"
    >
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6">
        <header className="mb-6 flex items-center gap-4">
          <span className="select-none whitespace-nowrap text-base font-bold tracking-tight">
            Sports<span className="text-amber-400">Dash</span>
          </span>
          <div className="ml-auto flex items-center gap-3">
            {indicator}
            {onClose !== undefined && (
              <button
                type="button"
                onClick={onClose}
                disabled={closeDisabled}
                aria-label="Close setup"
                title="Close setup"
                className="rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
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

        <div className="flex-1 pt-2">{children}</div>
      </div>
    </div>
  );
}
