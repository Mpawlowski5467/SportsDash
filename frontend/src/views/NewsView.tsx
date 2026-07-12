import { useMemo, useState, type ReactNode } from "react";
import { useNews, useRefreshNews, useTeams } from "../hooks";
import { relativeTime } from "../lib/time";
import type { League, NewsItem, NewsScope, Team } from "../types";
import NewsDetailPanel from "../components/NewsDetailPanel";

/** What a news card shows for its source: a followed team or a competition. */
interface NewsBadge {
  name: string; // full display name (team or competition)
  label: string; // short code for the corner chip
  color: string | null; // team color; null for competitions (no per-league color)
}

/** Short uppercase code for a competition badge, e.g. "FIFA World Cup" → "FWC". */
function leagueCode(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return (words[0] ?? "").slice(0, 3).toUpperCase();
  return words
    .map((w) => w[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

/** Resolve a news item to its badge (team takes precedence over competition). */
function badgeFor(
  item: NewsItem,
  teamsById: Record<string, Team>,
  leaguesById: Record<string, League>,
): NewsBadge {
  if (item.team_id) {
    const team = teamsById[item.team_id];
    if (team)
      return { name: team.name, label: team.abbreviation, color: team.color };
  }
  if (item.league_id) {
    const league = leaguesById[item.league_id];
    if (league)
      return { name: league.name, label: leagueCode(league.name), color: null };
  }
  const fallback = item.team_id ?? item.league_id ?? "News";
  return { name: fallback, label: fallback.slice(0, 3).toUpperCase(), color: null };
}

/** Convert a hex color to an rgba() string at the given alpha. */
function withAlpha(hex: string | null | undefined, alpha: number): string {
  const fallbackRgb = "161, 161, 170";
  if (!hex) return `rgba(${fallbackRgb}, ${alpha})`;
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return `rgba(${fallbackRgb}, ${alpha})`;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}


const PILL_ACTIVE =
  "flex items-center gap-1.5 rounded-full border border-zinc-600 bg-zinc-800 px-3 py-1 text-sm font-medium text-zinc-100";
const PILL_INACTIVE =
  "flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-sm text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-200";

function NewsPills({
  teams,
  competitions,
  scope,
  onSelect,
}: {
  teams: Team[];
  competitions: League[];
  scope: NewsScope | undefined;
  onSelect: (scope: NewsScope | undefined) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        onClick={() => onSelect(undefined)}
        className={scope === undefined ? PILL_ACTIVE : PILL_INACTIVE}
      >
        All
      </button>
      {teams.map((team) => {
        const active = scope?.teamId === team.id;
        return (
          <button
            key={`team:${team.id}`}
            type="button"
            onClick={() => onSelect({ teamId: team.id })}
            className={active ? PILL_ACTIVE : PILL_INACTIVE}
          >
            <span
              className="inline-block size-2 rounded-full"
              style={{ backgroundColor: team.color ?? "#52525b" }}
              aria-hidden
            />
            {team.name}
          </button>
        );
      })}
      {competitions.map((league) => {
        const active = scope?.leagueId === league.id;
        return (
          <button
            key={`league:${league.id}`}
            type="button"
            onClick={() => onSelect({ leagueId: league.id })}
            className={active ? PILL_ACTIVE : PILL_INACTIVE}
          >
            <span
              className="inline-block size-2 rounded-full border border-zinc-500"
              aria-hidden
            />
            {league.name}
          </button>
        );
      })}
    </div>
  );
}

function NewsCard({
  item,
  badge,
  onSelect,
}: {
  item: NewsItem;
  badge: NewsBadge;
  onSelect: (item: NewsItem) => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = item.image_url !== null && !imageFailed;
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      className="group flex flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/60 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-900"
    >
      {showImage ? (
        <img
          src={item.image_url ?? undefined}
          alt=""
          className="aspect-[16/9] w-full bg-zinc-800 object-cover"
          onError={() => setImageFailed(true)}
        />
      ) : (
        <div
          className="flex aspect-[16/9] w-full items-center justify-center bg-zinc-800/40"
          style={{
            backgroundColor: withAlpha(badge.color, 0.1),
          }}
        >
          <span
            className="text-2xl font-bold tracking-wide"
            style={{ color: withAlpha(badge.color, 0.5) }}
          >
            {badge.label}
          </span>
        </div>
      )}
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <span className="line-clamp-2 font-medium leading-snug text-zinc-100 group-hover:underline">
          {item.title}
        </span>
        {item.summary !== null && (
          <p className="line-clamp-2 text-sm text-zinc-400">{item.summary}</p>
        )}
        <div className="mt-auto flex items-center gap-x-2 pt-1 text-xs text-zinc-500">
          <span className="truncate">{item.source}</span>
          <span aria-hidden>·</span>
          <span className="whitespace-nowrap">
            {item.published_at !== null
              ? relativeTime(item.published_at)
              : "recently"}
          </span>
          <span
            className="ml-auto shrink-0 rounded px-1.5 py-0.5 font-semibold text-zinc-300"
            title={badge.name}
            style={{
              color: badge.color ?? undefined,
              backgroundColor: withAlpha(badge.color, 0.12),
            }}
          >
            {badge.label}
          </span>
        </div>
      </div>
    </button>
  );
}

function RefreshButton({
  busy,
  onClick,
}: {
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label="Refresh news"
      className="flex shrink-0 items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-sm text-zinc-300 transition-colors hover:border-zinc-600 hover:text-zinc-100 disabled:cursor-default disabled:opacity-60"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={`h-4 w-4 ${busy ? "animate-spin" : ""}`}
        aria-hidden="true"
      >
        <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
        <path d="M3 21v-5h5" />
      </svg>
      {busy ? "Refreshing…" : "Refresh"}
    </button>
  );
}

export default function NewsView() {
  const teamsQuery = useTeams();
  const [scope, setScope] = useState<NewsScope | undefined>(undefined);
  const [selected, setSelected] = useState<NewsItem | null>(null);
  const newsQuery = useNews(scope);
  const refresh = useRefreshNews(scope);

  const teams = teamsQuery.data?.teams ?? [];
  const leagues = teamsQuery.data?.leagues ?? [];
  // Whole-competition follows (e.g. World Cup) get their own pill — they have
  // no team rows, so their news is keyed by league instead.
  const competitions = useMemo(
    () => leagues.filter((league) => league.follow_all),
    [leagues],
  );
  const teamsById = useMemo(() => {
    const map: Record<string, Team> = {};
    for (const team of teams) map[team.id] = team;
    return map;
  }, [teams]);
  const leaguesById = useMemo(() => {
    const map: Record<string, League> = {};
    for (const league of leagues) map[league.id] = league;
    return map;
  }, [leagues]);

  if (teamsQuery.isError) {
    return (
      <p className="text-sm text-red-400">
        Failed to load teams: {teamsQuery.error?.message ?? "unknown error"}
      </p>
    );
  }
  if (!teamsQuery.data) {
    return <p className="text-sm text-zinc-500">Loading teams…</p>;
  }

  const items = newsQuery.data;
  const scopeName = scope?.teamId
    ? teamsById[scope.teamId]?.name
    : scope?.leagueId
      ? leaguesById[scope.leagueId]?.name
      : undefined;

  let body: ReactNode;
  if (newsQuery.isError) {
    body = (
      <p className="text-sm text-red-400">
        Failed to load news: {newsQuery.error?.message ?? "unknown error"}
      </p>
    );
  } else if (!items) {
    body = <p className="text-sm text-zinc-500">Loading news…</p>;
  } else if (items.length === 0) {
    body = (
      <p className="text-sm text-zinc-500">
        No news yet{scopeName ? ` for ${scopeName}` : ""}. Hit Refresh to pull
        the latest, or wait for the next scheduled refresh.
      </p>
    );
  } else {
    body = (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => (
          <NewsCard
            key={item.id}
            item={item}
            badge={badgeFor(item, teamsById, leaguesById)}
            onSelect={setSelected}
          />
        ))}
      </div>
    );
  }

  const selectedBadge = selected
    ? badgeFor(selected, teamsById, leaguesById)
    : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <NewsPills
          teams={teams}
          competitions={competitions}
          scope={scope}
          onSelect={setScope}
        />
        <RefreshButton busy={refresh.isPending} onClick={() => refresh.mutate()} />
      </div>
      {refresh.isError && (
        <p className="text-sm text-red-400">
          Refresh failed: {refresh.error?.message ?? "unknown error"}
        </p>
      )}
      {body}
      <NewsDetailPanel
        item={selected}
        source={selectedBadge}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
