/**
 * Settings panel.
 *
 * A full-screen sheet over the app, mirroring the OnboardingWizard shell.
 * Holds the appearance controls (base, colours, favourite team) and the
 * score-line toggle.
 *
 * The push-notification preference editor used to live here and was removed
 * on 2026-08-19: the notification path has never been exercised end to end,
 * so shipping a settings UI for it implied a working feature. The backend
 * scheduler and its `/api/notifications/prefs` endpoints are untouched and
 * still run — see the ROADMAP entry for what has to be verified before the
 * UI comes back.
 */

import type { CSSProperties, ReactNode } from "react";

import { Icon, SectionHead, TeamBlock } from "../components/bs";
import { MastheadWordmark, SportsDashBall } from "../components/logo";
import { useModalChrome } from "../components/modalChrome";
import { useTheme } from "../components/ThemeProvider";
import { useTeams } from "../hooks";
import { useTicker } from "../lib/useTicker";
import {
  PALETTES,
  THEMES,
  teamAccentVars,
  type PaletteId,
  type ThemeId,
} from "../lib/theme";

/**
 * A thumbnail of a sheet in one base and palette. The wrapper carries its
 * own `data-theme` / `data-palette`, so the tokens resolve *inside* this
 * element only — the swatch shows each choice's real ground, sheet, tab and
 * spot without applying it to the whole app.
 */
function Swatch({
  theme,
  palette,
  teamColor,
}: {
  theme: ThemeId;
  palette: PaletteId;
  teamColor: string | null;
}) {
  const accent =
    palette === "team" ? teamAccentVars(teamColor, theme) : null;
  return (
    <span
      data-theme={theme}
      data-palette={palette}
      style={(accent ?? undefined) as CSSProperties | undefined}
      aria-hidden="true"
      className="relative block h-10 w-16 flex-none border border-rule bg-ground"
    >
      <span className="absolute inset-x-1.5 bottom-1.5 top-2 border border-t-[3px] border-rule border-t-ink bg-sheet" />
      <span className="absolute left-1.5 top-2 h-2.5 w-6 bg-tab" />
      <span className="absolute bottom-3 right-3 h-2.5 w-2.5 bg-spot shadow-[inset_0_0_0_1px_var(--bs-spot-edge)]" />
    </span>
  );
}

/** One choice in a radiogroup: a square button, the ink block when chosen. */
function Choice({
  checked,
  disabled = false,
  onChoose,
  label,
  description,
  children,
}: {
  checked: boolean;
  disabled?: boolean;
  onChoose: () => void;
  label: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      disabled={disabled}
      onClick={onChoose}
      className={
        "flex min-h-[4.5rem] cursor-pointer items-center gap-3.5 border-2 px-3.5 py-3 text-left disabled:cursor-not-allowed " +
        (checked
          ? "bs-inverse border-block"
          : "border-ink bg-sheet text-ink hover:bg-hover disabled:border-rule disabled:hover:bg-sheet")
      }
    >
      {children}
      <span className="min-w-0">
        <span
          className={
            "block font-condensed text-[1.0625rem] font-bold uppercase leading-tight tracking-[0.06em]" +
            (disabled ? " text-muted" : "")
          }
        >
          {label}
        </span>
        {description !== undefined && (
          <span className="mt-0.5 block text-sm text-ink-2">{description}</span>
        )}
      </span>
    </button>
  );
}

/** A labelled row inside the appearance sheet. */
function Row({
  label,
  note,
  children,
}: {
  label: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-t border-hair p-5 first:border-t-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="bs-label">{label}</span>
        {note !== undefined && <span className="bs-note">{note}</span>}
      </div>
      {children}
    </div>
  );
}

/**
 * Appearance: two independent choices and the team the second one uses.
 * Every option is rendered from the lists in `lib/theme.ts`, never
 * enumerated here; choosing one persists to localStorage and re-themes the
 * whole app live via the ThemeProvider.
 */
function AppearanceSection() {
  const {
    theme,
    palette,
    setTheme,
    setPalette,
    teamPaletteAvailable,
    favoriteTeam,
    setFavoriteTeam,
  } = useTheme();
  // undefined while `/teams` is loading or has failed: not the same thing as
  // following nobody, and not reported as such.
  const teams = useTeams().data?.teams;
  const favoriteColor = favoriteTeam?.color ?? null;

  return (
    <section aria-label="Appearance">
      <SectionHead title="Appearance" note="Base and colours are separate choices" />
      <div className="bs-sheet">
        <Row label="Base">
          <div
            role="radiogroup"
            aria-label="Base"
            className="grid grid-cols-1 gap-2 sm:grid-cols-3"
          >
            {THEMES.map((option) => (
              <Choice
                key={option.id}
                checked={option.id === theme}
                onChoose={() => setTheme(option.id)}
                label={option.label}
                description={option.description}
              >
                <Swatch
                  theme={option.id}
                  palette={option.teamPalette ? palette : "brand"}
                  teamColor={favoriteColor}
                />
              </Choice>
            ))}
          </div>
        </Row>

        <Row
          label="Colours"
          note={
            teamPaletteAvailable
              ? undefined
              : "High contrast keeps its own colours, so every tone stays at AAA"
          }
        >
          <div
            role="radiogroup"
            aria-label="Colours"
            className="grid grid-cols-1 gap-2 sm:grid-cols-2"
          >
            {PALETTES.map((option) => (
              <Choice
                key={option.id}
                checked={
                  teamPaletteAvailable
                    ? option.id === palette
                    : option.id === "brand"
                }
                disabled={!teamPaletteAvailable && option.id === "team"}
                onChoose={() => setPalette(option.id)}
                label={option.label}
                description={
                  option.id === "team" && favoriteTeam !== null
                    ? `${favoriteTeam.name}'s colour as the spot colour.`
                    : option.description
                }
              >
                <Swatch
                  theme={teamPaletteAvailable ? theme : "light"}
                  palette={option.id}
                  teamColor={favoriteColor}
                />
              </Choice>
            ))}
          </div>
        </Row>

        <Row
          label="Favourite team"
          note="The team the Team colours come from"
        >
          {teams === undefined ? (
            <p className="m-0 text-ink-2">
              Your teams have not loaded, so the choice cannot be shown yet.
            </p>
          ) : teams.length === 0 ? (
            <p className="m-0 text-ink-2">
              Follow a team to use its colour. Until then Team looks the same
              as Box Score orange.
            </p>
          ) : (
            <>
              <div
                role="radiogroup"
                aria-label="Favourite team"
                className="flex flex-wrap gap-1.5"
              >
                {teams.map((team) => (
                  <button
                    key={team.id}
                    type="button"
                    role="radio"
                    aria-checked={team.id === favoriteTeam?.id}
                    onClick={() => setFavoriteTeam(team.id)}
                    className="bs-btn justify-start gap-2.5 pl-2 pr-3.5"
                  >
                    <TeamBlock
                      name={team.name}
                      abbreviation={team.abbreviation}
                      color={team.color}
                    />
                    {team.name}
                  </button>
                ))}
              </div>
              {favoriteTeam !== null && favoriteColor === null && (
                <p className="m-0 text-sm text-ink-2">
                  {favoriteTeam.name} has no colour on record, so Team falls
                  back to Box Score orange.
                </p>
              )}
            </>
          )}
        </Row>
      </div>
    </section>
  );
}

/**
 * Score-line visibility toggle. Persists via `useTicker` (localStorage,
 * synced across the app) so the band in Layout shows/hides live.
 */
function TickerSection() {
  const [visible, toggle] = useTicker();

  return (
    <section aria-label="Score line">
      <SectionHead title="Score line" />
      <div className="bs-sheet flex items-center justify-between gap-6 p-5">
        <p className="m-0 max-w-prose text-ink-2">
          Show the score line under the masthead with today's games, on every
          tab. It scrolls when the games do not fit on one row, and hides
          when there is nothing on.
        </p>
        <button
          type="button"
          role="switch"
          aria-checked={visible}
          aria-label={visible ? "Hide live ticker" : "Show live ticker"}
          onClick={toggle}
          className={
            "relative h-11 w-[4.75rem] flex-none cursor-pointer border-2 border-ink " +
            (visible ? "bg-block" : "bg-sheet")
          }
        >
          <span
            aria-hidden="true"
            className={
              "absolute top-1 h-[calc(100%-0.5rem)] w-8 " +
              (visible ? "right-1 bg-on-block" : "left-1 bg-ink")
            }
          />
          <span
            aria-hidden="true"
            className={
              "absolute top-1/2 -translate-y-1/2 font-condensed text-sm font-bold uppercase tracking-[0.06em] " +
              (visible ? "left-2 text-on-block" : "right-1.5 text-ink")
            }
          >
            {visible ? "On" : "Off"}
          </span>
        </button>
      </div>
    </section>
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
      className="fixed inset-0 z-50 overflow-y-auto bg-ground text-ink outline-none"
    >
      <div className="border-b-[3px] border-ink bg-sheet">
        <div className="mx-auto flex max-w-[60rem] items-center gap-3 px-4 sm:px-8">
          <span className="flex min-h-14 select-none items-center gap-2 sm:min-h-[4.25rem] sm:gap-2.5">
            <SportsDashBall
              variant="ink"
              className="h-7 w-7 sm:h-[2.125rem] sm:w-[2.125rem]"
            />
            <MastheadWordmark className="text-[1.375rem] sm:text-3xl" />
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            title="Close"
            className="bs-btn bs-btn--icon ml-auto"
          >
            <Icon size={20}>
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </Icon>
          </button>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-[60rem] flex-col gap-7 px-4 pb-10 pt-7 sm:px-8">
        <h1 className="bs-title">Settings</h1>
        <AppearanceSection />
        <TickerSection />
      </div>
    </div>
  );
}
