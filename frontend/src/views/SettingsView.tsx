/**
 * Settings panel.
 *
 * Full-screen dark kiosk overlay, mirroring the OnboardingWizard shell.
 * Holds the appearance (theme) switcher and the live-ticker toggle.
 *
 * The push-notification preference editor used to live here and was removed
 * on 2026-08-19: the notification path has never been exercised end to end,
 * so shipping a settings UI for it implied a working feature. The backend
 * scheduler and its `/api/notifications/prefs` endpoints are untouched and
 * still run — see the ROADMAP entry for what has to be verified before the
 * UI comes back.
 */

import { useModalChrome } from "../components/modalChrome";
import { useTheme } from "../components/ThemeProvider";
import { useTicker } from "../lib/useTicker";
import { THEMES, type ThemeId } from "../lib/theme";

/**
 * Tiny preview of a theme's palette. The wrapper carries the theme's
 * `data-theme` attribute, so the remapped `--color-*` vars from index.css
 * resolve *inside* this element only — the swatches show each theme's real
 * surface/accent without applying it to the whole app.
 */
function ThemeSwatch({ themeId }: { themeId: ThemeId }) {
  return (
    <span
      data-theme={themeId}
      aria-hidden
      className="flex h-6 w-10 flex-shrink-0 overflow-hidden rounded border border-black/20"
      style={{ backgroundColor: "var(--color-zinc-950)" }}
    >
      <span
        className="flex-1"
        style={{ backgroundColor: "var(--color-zinc-800)" }}
      />
      <span
        className="flex-1"
        style={{ backgroundColor: "var(--color-amber-400)" }}
      />
      <span
        className="flex-1"
        style={{ backgroundColor: "var(--color-zinc-100)" }}
      />
    </span>
  );
}

/**
 * Appearance / theme switcher. One mutually-exclusive option per entry in
 * `THEMES` — the list is rendered, never enumerated here, so adding a theme
 * needs no change in this file; selecting one persists to localStorage and
 * re-themes the whole app live via the ThemeProvider.
 */
function AppearanceSection() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6">
      <div className="mb-5">
        <h2 className="text-lg font-semibold text-zinc-100">Appearance</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Pick a theme for the dashboard. Custom keeps the dark base but
          tints its accent with your followed team&apos;s colors; High
          contrast holds every text tone at WCAG AAA.
        </p>
      </div>

      <div
        role="radiogroup"
        aria-label="Theme"
        className="grid grid-cols-1 gap-2 sm:grid-cols-2"
      >
        {THEMES.map((option) => {
          const selected = option.id === theme;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setTheme(option.id)}
              className={
                "flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors " +
                (selected
                  ? "border-amber-400/70 bg-amber-400/10 ring-1 ring-amber-400/40"
                  : "border-zinc-800 bg-zinc-900/60 hover:border-zinc-700 hover:bg-zinc-800/60")
              }
            >
              <ThemeSwatch themeId={option.id} />
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="font-medium text-zinc-100">
                    {option.label}
                  </span>
                  {selected && (
                    <span className="text-xs font-medium uppercase tracking-wide text-amber-400">
                      Active
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block text-xs text-zinc-400">
                  {option.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Live-ticker visibility toggle. Persists via `useTicker` (localStorage,
 * synced across the app) so the scoreboard bar in Layout shows/hides live.
 */
function TickerSection() {
  const [visible, toggle] = useTicker();

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-zinc-100">Live ticker</h2>
          <p className="mt-1 text-sm text-zinc-400">
            Show a scrolling scoreboard bar under the nav with today's games,
            on every tab. Hidden automatically when there's nothing on.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={visible}
          aria-label={visible ? "Hide live ticker" : "Show live ticker"}
          onClick={toggle}
          className={
            "relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors " +
            (visible ? "bg-amber-500/70" : "bg-zinc-700")
          }
        >
          <span
            aria-hidden
            className={
              "inline-block h-4 w-4 transform rounded-full bg-zinc-100 transition-transform " +
              (visible ? "translate-x-4" : "translate-x-0.5")
            }
          />
        </button>
      </div>
    </div>
  );
}

export interface Props {
  onClose: () => void;
}

export default function SettingsView({ onClose }: Props) {
  const dialogRef = useModalChrome(onClose);

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      className="fixed inset-0 z-50 overflow-y-auto bg-zinc-950 text-zinc-100 outline-none"
    >
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6">
        <header className="mb-6 flex items-center gap-4">
          <span className="select-none whitespace-nowrap text-base font-bold tracking-tight">
            Sports<span className="text-amber-400">Dash</span>
          </span>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-zinc-400">
              Settings
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close settings"
              title="Close"
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
          </div>
        </header>

        <div className="flex flex-1 flex-col gap-6">
          <AppearanceSection />
          <TickerSection />
        </div>
      </div>
    </div>
  );
}
