import { useSetupLeagues } from "../../hooks";
import type { CatalogLeague, Sport } from "../../types";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";
import { apiErrorMessage } from "./errors";
import MiniSpinner from "../loaders/MiniSpinner";

const SPORT_ORDER: Sport[] = [
  "basketball",
  "baseball",
  "soccer",
  "hockey",
  "football",
  "tennis",
  "mma",
  "golf",
  "volleyball",
];

const SPORT_LABELS: Record<Sport, string> = {
  basketball: "Basketball",
  baseball: "Baseball",
  soccer: "Soccer",
  hockey: "Hockey",
  football: "Football",
  tennis: "Tennis",
  mma: "MMA",
  golf: "Golf",
  volleyball: "Volleyball",
};

/** Heading for a sport — capitalize unknown values so future sports still render. */
function sportLabel(sport: Sport): string {
  return SPORT_LABELS[sport] ?? sport.charAt(0).toUpperCase() + sport.slice(1);
}

export interface Props {
  selectedIds: string[];
  followAllIds: string[];
  onToggle: (leagueId: string) => void;
  onToggleFollowAll: (leagueId: string) => void;
  // Leagues is the first wizard step, so there's usually nowhere to go back
  // to; the Back button only renders when a handler is provided.
  onBack?: () => void;
  onContinue: () => void;
}

/** A single league pill, with an optional "follow whole competition" toggle. */
function LeagueChip({
  league,
  selected,
  followAll,
  onToggle,
  onToggleFollowAll,
}: {
  league: CatalogLeague;
  selected: boolean;
  followAll: boolean;
  onToggle: () => void;
  onToggleFollowAll: () => void;
}) {
  // In follow-all mode the chip is "active" by virtue of the whole-comp
  // follow; picking teams is mutually exclusive with it.
  const active = selected && !followAll;
  return (
    <div
      className={
        "inline-flex items-center gap-2 rounded-full border py-1 pl-4 pr-1.5 text-sm font-medium transition-colors " +
        (followAll
          ? "border-emerald-400/60 bg-emerald-500/15 text-emerald-300"
          : active
            ? "border-amber-400/60 bg-amber-500/15 text-amber-300"
            : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100")
      }
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={active}
        className="flex cursor-pointer items-center gap-1.5"
        disabled={followAll}
        title={
          followAll
            ? `Following all of ${league.name} — individual picks are off`
            : undefined
        }
      >
        {league.logo_url && (
          <img
            src={league.logo_url}
            alt=""
            className="h-4 w-4 shrink-0 object-contain"
            // No logo for this league, or it failed to load: drop the img and
            // fall back to showing just the league name.
            onError={(event) => {
              event.currentTarget.style.display = "none";
            }}
          />
        )}
        {league.name}
      </button>
      {league.supports_follow_all && (
        <button
          type="button"
          onClick={onToggleFollowAll}
          aria-pressed={followAll}
          title={
            followAll
              ? `Following all of ${league.name}`
              : `Follow all of ${league.name} (every game, no team picks)`
          }
          className={
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold transition-colors " +
            (followAll
              ? "bg-emerald-500/25 text-emerald-200 hover:bg-emerald-500/35"
              : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200")
          }
        >
          {followAll ? "✓ Whole league" : "Follow all"}
        </button>
      )}
    </div>
  );
}

/** League multi-select: pill-cards grouped under sport / national headings. */
export default function LeagueStep({
  selectedIds,
  followAllIds,
  onToggle,
  onToggleFollowAll,
  onBack,
  onContinue,
}: Props) {
  const leaguesQuery = useSetupLeagues();

  if (leaguesQuery.isPending) {
    return (
      <div className="flex justify-center py-16">
        <MiniSpinner size={20} />
      </div>
    );
  }

  if (leaguesQuery.isError) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3">
        <p className="min-w-0 truncate text-sm text-red-300">
          Couldn&rsquo;t load the league catalog —{" "}
          {apiErrorMessage(leaguesQuery.error)}
        </p>
        <button
          type="button"
          onClick={() => void leaguesQuery.refetch()}
          className={`shrink-0 ${SECONDARY_BUTTON}`}
        >
          Retry
        </button>
      </div>
    );
  }

  const leagues = leaguesQuery.data.leagues;
  // National-team competitions get their own group, separate from club
  // leagues; club leagues stay grouped by sport.
  const nationalLeagues = leagues.filter((league) => league.national);
  const clubLeagues = leagues.filter((league) => !league.national);
  // Known sports first in display order, then any the catalog has that we
  // don't know about yet (first-seen order) — new sports must never vanish.
  const sports = [
    ...new Set([...SPORT_ORDER, ...clubLeagues.map((league) => league.sport)]),
  ];
  const sportGroups: { sport: Sport; leagues: CatalogLeague[] }[] = sports
    .map((sport) => ({
      sport,
      leagues: clubLeagues.filter((league) => league.sport === sport),
    }))
    .filter((group) => group.leagues.length > 0);

  const renderChip = (league: CatalogLeague) => (
    <LeagueChip
      key={league.id}
      league={league}
      selected={selectedIds.includes(league.id)}
      followAll={followAllIds.includes(league.id)}
      onToggle={() => onToggle(league.id)}
      onToggleFollowAll={() => onToggleFollowAll(league.id)}
    />
  );

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-zinc-100">
          Choose your leagues
        </h2>
        <p className="mt-1 text-sm text-zinc-400">
          Pick every league you want to follow — you&rsquo;ll choose teams next.
          Or hit &ldquo;Follow all&rdquo; on a league to follow it in full (every
          game), no team picks needed.
        </p>
      </header>

      {nationalLeagues.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            National Teams
          </h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {nationalLeagues.map(renderChip)}
          </div>
        </section>
      )}

      {sportGroups.map(({ sport, leagues: sportLeagues }) => (
        <section key={sport}>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {sportLabel(sport)}
          </h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {sportLeagues.map(renderChip)}
          </div>
        </section>
      ))}

      <footer className="flex items-center justify-between border-t border-zinc-800 pt-4">
        {onBack ? (
          <button type="button" onClick={onBack} className={SECONDARY_BUTTON}>
            Back
          </button>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={onContinue}
          disabled={selectedIds.length === 0}
          className={PRIMARY_BUTTON}
        >
          Continue
        </button>
      </footer>
    </div>
  );
}
