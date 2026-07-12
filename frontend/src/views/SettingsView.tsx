/**
 * Notification preferences panel (Phase 3b).
 *
 * Full-screen dark kiosk overlay, mirroring the OnboardingWizard shell, that
 * lists each notifiable scope (global first, then followed teams, then
 * followed competitions) as a row with:
 *   - a mute toggle (a muted scope blocks all events and visually dims its
 *     per-event checkboxes), and
 *   - a checkbox per notifiable event type.
 *
 * Editing PUTs a single-scope NotificationPrefUpdate, applies the result
 * optimistically into the ["notification-prefs"] cache, then invalidates so
 * server truth wins moments later. The PUT returns the full prefs payload, so
 * a successful response simply replaces the cache.
 */

import { useMemo, useState } from "react";
import { useModalChrome } from "../components/modalChrome";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useNotificationPrefs } from "../hooks";
import { useTheme } from "../components/ThemeProvider";
import { useTicker } from "../lib/useTicker";
import { THEMES, type ThemeId } from "../lib/theme";
import type {
  NotificationPref,
  NotificationPrefsResponse,
  NotificationPrefUpdate,
} from "../types";

const PREFS_KEY = ["notification-prefs"] as const;

/** Human labels for the EventType strings, in the canonical ordering. */
const EVENT_LABELS: Record<string, string> = {
  starting_soon: "Starting soon",
  game_start: "Game start",
  period_start: "Period start",
  intermission: "Intermission",
  final: "Final",
};

function eventLabel(eventType: string): string {
  return (
    EVENT_LABELS[eventType] ??
    eventType
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

/** Whether a scope row is global / a team / a competition (for grouping). */
type ScopeKind = "global" | "team" | "league";

function scopeKind(scope: string): ScopeKind {
  if (scope === "global") return "global";
  if (scope.startsWith("team:")) return "team";
  return "league";
}

/** Apply a single-scope update to a prefs payload (used optimistically). */
function applyUpdate(
  prev: NotificationPrefsResponse,
  update: NotificationPrefUpdate,
): NotificationPrefsResponse {
  return {
    ...prev,
    prefs: prev.prefs.map((pref) =>
      pref.scope === update.scope
        ? {
            ...pref,
            muted: update.muted ?? pref.muted,
            events: { ...pref.events, ...(update.events ?? {}) },
          }
        : pref,
    ),
  };
}

function MuteToggle({
  muted,
  onToggle,
  busy,
}: {
  muted: boolean;
  onToggle: () => void;
  busy: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={muted}
      aria-label={muted ? "Unmute scope" : "Mute scope"}
      disabled={busy}
      onClick={onToggle}
      className={
        "relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors disabled:opacity-50 " +
        (muted ? "bg-red-500/70" : "bg-zinc-700")
      }
    >
      <span
        aria-hidden
        className={
          "inline-block h-4 w-4 transform rounded-full bg-zinc-100 transition-transform " +
          (muted ? "translate-x-4" : "translate-x-0.5")
        }
      />
    </button>
  );
}

function EventCheckbox({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <label
      className={
        "flex items-center gap-2 text-sm transition-opacity " +
        (disabled
          ? "cursor-not-allowed opacity-40"
          : "cursor-pointer text-zinc-200")
      }
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className="h-4 w-4 rounded border-zinc-600 bg-zinc-800 text-amber-400 accent-amber-400 focus:ring-amber-400/40 disabled:cursor-not-allowed"
      />
      <span className="whitespace-nowrap">{label}</span>
    </label>
  );
}

function ScopeRow({
  pref,
  eventTypes,
  onUpdate,
}: {
  pref: NotificationPref;
  eventTypes: string[];
  onUpdate: (update: NotificationPrefUpdate) => void;
}) {
  // Track in-flight edits per scope so we can disable controls while a PUT is
  // outstanding; the cache is updated optimistically by the parent.
  const [busy, setBusy] = useState(false);

  const send = (update: Omit<NotificationPrefUpdate, "scope">) => {
    setBusy(true);
    void Promise.resolve(onUpdate({ scope: pref.scope, ...update })).finally(
      () => setBusy(false),
    );
  };

  return (
    <li className="flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 md:flex-row md:items-center md:justify-between md:gap-6">
      <div className="flex items-center gap-3">
        <MuteToggle
          muted={pref.muted}
          busy={busy}
          onToggle={() => send({ muted: !pref.muted })}
        />
        <div className="min-w-0">
          <div className="truncate font-medium text-zinc-100">
            {pref.label}
          </div>
          {pref.muted && (
            <div className="text-xs font-medium uppercase tracking-wide text-red-400">
              Muted
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-2 md:justify-end">
        {eventTypes.map((eventType) => {
          // Default (key absent) is ENABLED, per the resolution contract.
          const enabled = pref.events[eventType] ?? true;
          return (
            <EventCheckbox
              key={eventType}
              label={eventLabel(eventType)}
              checked={enabled}
              disabled={pref.muted || busy}
              onChange={() => send({ events: { [eventType]: !enabled } })}
            />
          );
        })}
      </div>
    </li>
  );
}

function ScopeGroup({
  title,
  prefs,
  eventTypes,
  onUpdate,
}: {
  title: string;
  prefs: NotificationPref[];
  eventTypes: string[];
  onUpdate: (update: NotificationPrefUpdate) => void;
}) {
  if (prefs.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      <ul className="flex flex-col gap-2">
        {prefs.map((pref) => (
          <ScopeRow
            key={pref.scope}
            pref={pref}
            eventTypes={eventTypes}
            onUpdate={onUpdate}
          />
        ))}
      </ul>
    </section>
  );
}

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
 * Appearance / theme switcher. Four mutually-exclusive options
 * (dark/light/newsprint/stadium); selecting one persists to localStorage
 * and re-themes the whole app live via the ThemeProvider.
 */
function AppearanceSection() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6">
      <div className="mb-5">
        <h2 className="text-lg font-semibold text-zinc-100">Appearance</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Pick a theme for the dashboard. Stadium keeps the dark base but
          tints its accent with your followed team's color.
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
  const queryClient = useQueryClient();
  const prefsQuery = useNotificationPrefs();
  const [error, setError] = useState<string | null>(null);

  const data = prefsQuery.data;
  const eventTypes = data?.event_types ?? [];

  const groups = useMemo(() => {
    const prefs = data?.prefs ?? [];
    return {
      global: prefs.filter((p) => scopeKind(p.scope) === "global"),
      teams: prefs.filter((p) => scopeKind(p.scope) === "team"),
      leagues: prefs.filter((p) => scopeKind(p.scope) === "league"),
    };
  }, [data]);

  /**
   * Optimistically apply a single-scope update, then PUT. On success the
   * server payload replaces the cache; on failure the optimistic write is
   * rolled back and the error surfaced. Always invalidate so server truth
   * reconciles regardless.
   */
  const handleUpdate = async (update: NotificationPrefUpdate) => {
    setError(null);
    const previous =
      queryClient.getQueryData<NotificationPrefsResponse>(PREFS_KEY);
    if (previous) {
      queryClient.setQueryData<NotificationPrefsResponse>(
        PREFS_KEY,
        applyUpdate(previous, update),
      );
    }
    try {
      const updated = await api.updateNotificationPref(update);
      queryClient.setQueryData<NotificationPrefsResponse>(PREFS_KEY, updated);
    } catch (err) {
      if (previous) {
        queryClient.setQueryData<NotificationPrefsResponse>(
          PREFS_KEY,
          previous,
        );
      }
      setError(err instanceof Error ? err.message : "Failed to save change.");
    } finally {
      void queryClient.invalidateQueries({ queryKey: PREFS_KEY });
    }
  };

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

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6">
          <div className="mb-5">
            <h2 className="text-lg font-semibold text-zinc-100">
              Push notifications
            </h2>
            <p className="mt-1 text-sm text-zinc-400">
              Choose which game events send a push, per team and competition.
              The most specific scope wins — a team setting overrides its
              league, which overrides the global default. Mute a scope to
              silence it entirely.
            </p>
          </div>

          {error !== null && (
            <p className="mb-4 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          {prefsQuery.isError ? (
            <div className="flex items-center gap-3">
              <p className="text-sm text-red-400">
                Failed to load notification preferences:{" "}
                {prefsQuery.error?.message ?? "unknown error"}
              </p>
              <button
                type="button"
                onClick={() => void prefsQuery.refetch()}
                className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
              >
                Retry
              </button>
            </div>
          ) : !data ? (
            <p className="text-sm text-zinc-500">Loading preferences…</p>
          ) : (
            <div className="flex flex-col gap-6">
              <ScopeGroup
                title="Global default"
                prefs={groups.global}
                eventTypes={eventTypes}
                onUpdate={handleUpdate}
              />
              <ScopeGroup
                title="Teams"
                prefs={groups.teams}
                eventTypes={eventTypes}
                onUpdate={handleUpdate}
              />
              <ScopeGroup
                title="Competitions"
                prefs={groups.leagues}
                eventTypes={eventTypes}
                onUpdate={handleUpdate}
              />
            </div>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
