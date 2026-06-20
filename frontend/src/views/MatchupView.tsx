import { useMemo, useState, type ReactNode } from "react";

import LineupView from "../components/LineupView";
import PlayerAvatar from "../components/PlayerAvatar";
import Select, { type SelectOption } from "../components/Select";
import StatusBadge from "../components/StatusBadge";
import TeamLogo from "../components/TeamLogo";
import UpsetRadar from "../components/UpsetRadar";
import WeatherInline from "../components/WeatherInline";
import { localDayOffset, useMatchup, useSchedule, useToday } from "../hooks";
import { formatDateTime, formatShortDate } from "../lib/time";
import type {
  Game,
  GameOdds,
  GameSide,
  Matchup,
  Player,
} from "../types";

/** Phases that belong in a pre-game previews browser. */
const PREVIEW_PHASES = new Set<Game["phase"]>(["scheduled", "in_progress"]);

/** Up-to-three-char abbreviation fallback, mirroring the modal helpers. */
function abbrev(name: string): string {
  return name.slice(0, 3).toUpperCase();
}

function sideLabel(side: GameSide): string {
  return side.abbreviation ?? abbrev(side.name);
}

/** American odds with an explicit sign (+144 / -175); "—" when unpriced. */
function formatMoneyline(value: number | null): string {
  if (value === null) return "—";
  return value > 0 ? `+${value}` : `${value}`;
}

/** Home point spread with an explicit sign (-1.5 / +3); "PK" at zero. */
function formatSpread(value: number): string {
  if (value === 0) return "PK";
  return value > 0 ? `+${value}` : `${value}`;
}

/**
 * Pre-game PREVIEW browser. Gathers the upcoming games over the next two weeks
 * (scheduled + in-progress), lets you pick one, and renders a rich matchup
 * preview — win-probability / odds, recent form, head-to-head, projected
 * lineups, injuries, and the venue forecast — each section appearing only when
 * the matchup actually carries that data. An upset radar sits across the top,
 * flagging games where the model and the betting market disagree.
 */
export default function MatchupView() {
  // Schedule is the primary source (next 14 days); Today is a fallback for
  // when the schedule query is empty (e.g. nothing scheduled in the window
  // but games are live right now).
  const scheduleQuery = useSchedule(localDayOffset(0), localDayOffset(14));
  const todayQuery = useToday();

  const upcoming = useMemo(() => {
    const fromSchedule = (scheduleQuery.data ?? []).filter((game) =>
      PREVIEW_PHASES.has(game.phase),
    );
    const source =
      fromSchedule.length > 0
        ? fromSchedule
        : (todayQuery.data?.games ?? []).filter((game) =>
            PREVIEW_PHASES.has(game.phase),
          );
    return [...source].sort(
      (a, b) =>
        new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
    );
  }, [scheduleQuery.data, todayQuery.data]);

  const [selected, setSelected] = useState<string | undefined>(undefined);
  // Ignore a selection that's no longer in the window (the schedule shifts as
  // games finish); default to the first upcoming game.
  const selectedId = upcoming.some((game) => game.id === selected)
    ? selected
    : upcoming[0]?.id;

  const options: SelectOption[] = useMemo(
    () =>
      upcoming.map((game) => ({
        id: game.id,
        label: `${sideLabel(game.away)} @ ${sideLabel(game.home)}`,
        logoUrl: game.away.logo_url ?? game.home.logo_url,
        color: game.away.color ?? game.home.color,
      })),
    [upcoming],
  );

  const matchupQuery = useMatchup(selectedId);

  // Initial load: wait for the schedule (and its fallback) before deciding
  // whether there's anything to preview at all.
  if (scheduleQuery.isLoading && todayQuery.isLoading) {
    return <PageSkeleton />;
  }

  if (upcoming.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <UpsetRadar games={upcoming} />
        <EmptyState
          title="No upcoming games"
          message="Previews appear here as soon as your followed teams have games on the schedule."
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <UpsetRadar games={upcoming} />

      <Select
        options={options}
        value={selectedId}
        onChange={setSelected}
        ariaLabel="Choose a matchup to preview"
      />

      {matchupQuery.isError ? (
        <EmptyState
          title="Couldn't load this preview"
          message={matchupQuery.error?.message ?? "Please try again."}
        />
      ) : matchupQuery.data === undefined ? (
        <PreviewSkeleton />
      ) : (
        <Preview matchup={matchupQuery.data} />
      )}
    </div>
  );
}

/** A single matchup preview, top to bottom. Sections self-hide when empty. */
function Preview({ matchup }: { matchup: Matchup }) {
  const { game, odds, weather, lineup } = matchup;

  const hasForm = matchup.home_form.length > 0 || matchup.away_form.length > 0;
  const hasH2H = matchup.head_to_head.length > 0;
  const hasLineup =
    lineup !== null && (lineup.home !== null || lineup.away !== null);
  const hasInjuries =
    matchup.home_injuries.length > 0 || matchup.away_injuries.length > 0;
  const hasOdds = odds !== null && oddsHasContent(odds);

  return (
    <article className="flex flex-col gap-6">
      <PreviewHeader game={game} />

      {hasOdds && <OddsSection odds={odds} game={game} />}

      {hasForm && (
        <Section title="Form">
          <div className="space-y-2">
            <FormRow label={sideLabel(game.away)} form={matchup.away_form} />
            <FormRow label={sideLabel(game.home)} form={matchup.home_form} />
          </div>
        </Section>
      )}

      {hasH2H && (
        <Section title="Head to head">
          <ul className="flex flex-col gap-1.5">
            {matchup.head_to_head.map((meeting) => (
              <HeadToHeadRow key={meeting.id} game={meeting} />
            ))}
          </ul>
        </Section>
      )}

      {hasLineup && <LineupView lineup={lineup} game={game} />}

      {hasInjuries && (
        <Section title="Injuries">
          <div className="grid gap-4 sm:grid-cols-2">
            <InjuryGroup
              title={game.away.name}
              players={matchup.away_injuries}
            />
            <InjuryGroup
              title={game.home.name}
              players={matchup.home_injuries}
            />
          </div>
        </Section>
      )}

      {weather !== null && (
        <Section title="Weather">
          <div className="rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-3">
            <WeatherInline weather={weather} />
          </div>
        </Section>
      )}
    </article>
  );
}

/** Away / home logos + names, the status badge, and the formatted start time. */
function PreviewHeader({ game }: { game: Game }) {
  return (
    <header className="flex flex-col gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-4">
      <div className="flex items-center justify-between gap-3">
        <SideHeader side={game.away} align="start" />
        <span className="shrink-0 text-xs font-semibold uppercase tracking-wide text-zinc-600">
          vs
        </span>
        <SideHeader side={game.home} align="end" />
      </div>
      <div className="flex items-center justify-center gap-2 text-xs text-zinc-500">
        <StatusBadge game={game} />
        <span className="text-zinc-600">·</span>
        <span className="tabular-nums">{formatDateTime(game.start_time)}</span>
      </div>
    </header>
  );
}

function SideHeader({
  side,
  align,
}: {
  side: GameSide;
  align: "start" | "end";
}) {
  return (
    <div
      className={
        "flex min-w-0 flex-1 items-center gap-2 " +
        (align === "end" ? "flex-row-reverse text-right" : "")
      }
    >
      <TeamLogo
        logoUrl={side.logo_url}
        name={side.name}
        abbreviation={side.abbreviation}
        color={side.color}
        size="md"
      />
      <span className="min-w-0 truncate text-sm font-semibold text-zinc-100">
        {side.name}
      </span>
    </div>
  );
}

function oddsHasContent(odds: GameOdds): boolean {
  return (
    odds.home_win_pct !== null ||
    odds.away_win_pct !== null ||
    odds.home_moneyline !== null ||
    odds.away_moneyline !== null ||
    odds.spread !== null ||
    odds.over_under !== null
  );
}

/**
 * Win-probability + betting line for the matchup — a copy of the detail
 * modal's OddsSection (which isn't exported), kept visually identical: the
 * win-probability split bar reads away (amber, left) / home (sky, right), and
 * the moneyline / spread / total rows show only what the provider priced.
 */
function OddsSection({ odds, game }: { odds: GameOdds; game: Game }) {
  const awayLabel = sideLabel(game.away);
  const homeLabel = sideLabel(game.home);
  const awayPct = odds.away_win_pct;
  const homePct = odds.home_win_pct;
  const hasProb = awayPct !== null && homePct !== null;
  const hasLine =
    odds.home_moneyline !== null ||
    odds.away_moneyline !== null ||
    odds.spread !== null ||
    odds.over_under !== null;

  return (
    <Section title="Win probability & odds">
      <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-3">
        {hasProb && (
          <div className="space-y-1">
            <div className="flex items-baseline justify-between text-sm tabular-nums">
              <span className="font-semibold text-amber-400">
                {awayLabel} {Math.round(awayPct!)}%
              </span>
              <span className="text-[11px] uppercase tracking-wide text-zinc-500">
                Win probability
              </span>
              <span className="font-semibold text-sky-400">
                {Math.round(homePct!)}% {homeLabel}
              </span>
            </div>
            <div className="flex h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="bg-amber-500/70"
                style={{ width: `${awayPct}%` }}
              />
              <div className="flex-1 bg-sky-500/60" />
            </div>
          </div>
        )}

        {hasLine && (
          <dl className="space-y-1.5 text-sm">
            {(odds.away_moneyline !== null || odds.home_moneyline !== null) && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Moneyline
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  {awayLabel} {formatMoneyline(odds.away_moneyline)}
                  <span className="px-1.5 text-zinc-600">·</span>
                  {homeLabel} {formatMoneyline(odds.home_moneyline)}
                </dd>
              </div>
            )}
            {odds.spread !== null && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Spread
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  {homeLabel} {formatSpread(odds.spread)}
                </dd>
              </div>
            )}
            {odds.over_under !== null && (
              <div className="flex items-center justify-between gap-3">
                <dt className="text-xs uppercase tracking-wide text-zinc-500">
                  Total
                </dt>
                <dd className="tabular-nums text-zinc-200">
                  O/U {odds.over_under}
                </dd>
              </div>
            )}
          </dl>
        )}

        {odds.provider !== null && (
          <p className="text-[11px] text-zinc-500">via {odds.provider}</p>
        )}
      </div>
    </Section>
  );
}

/** One side's recent form as W/L/D chips, newest-first, with a side label. */
function FormRow({ label, form }: { label: string; form: string[] }) {
  if (form.length === 0) return null;
  return (
    <div className="flex items-center gap-2">
      <span className="w-12 shrink-0 truncate text-xs font-semibold uppercase tracking-wide text-zinc-400">
        {label}
      </span>
      <div className="flex flex-wrap gap-1">
        {form.map((result, index) => (
          <FormChip key={index} result={result} />
        ))}
      </div>
    </div>
  );
}

const FORM_CHIP: Record<string, string> = {
  W: "bg-emerald-500/15 text-emerald-400",
  L: "bg-red-500/15 text-red-400",
  D: "bg-zinc-700/40 text-zinc-300",
};

function FormChip({ result }: { result: string }) {
  const key = result.toUpperCase();
  return (
    <span
      className={
        "flex size-5 items-center justify-center rounded text-xs font-bold " +
        (FORM_CHIP[key] ?? "bg-zinc-700/40 text-zinc-300")
      }
    >
      {key}
    </span>
  );
}

/** Past meeting: date · away v home · score. */
function HeadToHeadRow({ game }: { game: Game }) {
  return (
    <li className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm">
      <span className="w-14 shrink-0 text-xs tabular-nums text-zinc-500">
        {formatShortDate(game.start_time)}
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate tabular-nums">
        <TeamLogo
          logoUrl={game.away.logo_url}
          name={game.away.name}
          abbreviation={game.away.abbreviation}
          color={game.away.color}
          size="xs"
        />
        <span className="text-zinc-300">{sideLabel(game.away)}</span>
        <span className="text-zinc-600">v</span>
        <TeamLogo
          logoUrl={game.home.logo_url}
          name={game.home.name}
          abbreviation={game.home.abbreviation}
          color={game.home.color}
          size="xs"
        />
        <span className="text-zinc-300">{sideLabel(game.home)}</span>
      </span>
      <span className="shrink-0 text-right text-xs font-semibold tabular-nums text-zinc-200">
        {game.away.score ?? "—"}–{game.home.score ?? "—"}
      </span>
    </li>
  );
}

/** One side's unavailable players: avatar + name + position + status detail. */
function InjuryGroup({
  title,
  players,
}: {
  title: string;
  players: Player[];
}) {
  if (players.length === 0) return null;
  return (
    <div>
      <h4 className="mb-2 truncate text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h4>
      <ul className="space-y-1.5">
        {players.map((player) => (
          <li key={player.id} className="flex items-center gap-2.5">
            <PlayerAvatar
              photoUrl={player.photo_url}
              name={player.name}
              size="sm"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-1.5">
                <span className="min-w-0 truncate text-sm text-zinc-200">
                  {player.name}
                </span>
                {player.position !== null && (
                  <span className="shrink-0 text-[11px] uppercase tracking-wide text-zinc-500">
                    {player.position}
                  </span>
                )}
              </div>
              {player.status_detail !== null && (
                <p className="truncate text-xs text-rose-400">
                  {player.status_detail}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Section wrapper: an uppercase label over its content. */
function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-zinc-800 py-12 text-center">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        className="h-8 w-8 text-zinc-600"
        aria-hidden="true"
      >
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 9h18M9 4v16" strokeLinecap="round" />
      </svg>
      <p className="text-sm font-medium text-zinc-300">{title}</p>
      <p className="max-w-sm text-xs text-zinc-500">{message}</p>
    </div>
  );
}

/** Full-page skeleton while the upcoming-games list is still resolving. */
function PageSkeleton() {
  return (
    <div className="flex animate-pulse flex-col gap-4" aria-hidden="true">
      <div className="h-10 rounded-lg bg-zinc-800/60" />
      <div className="h-9 w-48 rounded-full bg-zinc-800/60" />
      <PreviewSkeleton />
    </div>
  );
}

/** Skeleton for the preview body while a matchup loads. */
function PreviewSkeleton() {
  return (
    <div className="flex animate-pulse flex-col gap-6" aria-hidden="true">
      <div className="h-20 rounded-xl bg-zinc-800/60" />
      <div className="space-y-2">
        <div className="h-3 w-28 rounded bg-zinc-800/60" />
        <div className="h-16 rounded-lg bg-zinc-800/60" />
      </div>
      <div className="space-y-2">
        <div className="h-3 w-20 rounded bg-zinc-800/60" />
        <div className="h-10 rounded-lg bg-zinc-800/60" />
      </div>
    </div>
  );
}
