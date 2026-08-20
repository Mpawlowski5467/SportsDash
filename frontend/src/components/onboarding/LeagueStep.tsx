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
  "racing",
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
  racing: "Racing",
};

/** Heading for a sport — capitalize unknown values so future sports still render. */
function sportLabel(sport: Sport): string {
  return SPORT_LABELS[sport] ?? sport.charAt(0).toUpperCase() + sport.slice(1);
}

export interface Props {
  // Country codes chosen on the map step. A league that belongs to one of
  // these is offered; a league that belongs to NO country is always offered
  // (confederation competitions and every sport not organised by country).
  // Empty means no narrowing at all — skipping the map shows everything.
  countryCodes: string[];
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
    // A CARD in a fixed grid, not a pill in a wrapping row. Pills sized
    // themselves by name length, so the rows came out ragged — one league on
    // one line, three on the next — and the nested "Follow all" pill read as
    // part of the same control. Here the league is the primary target and
    // "Follow all" is a separate line beneath it.
    <div
      className={
        "flex flex-col gap-2 rounded-lg border p-3 transition-colors " +
        (followAll
          ? "border-emerald-400/50 bg-emerald-500/10"
          : active
            ? "border-amber-400/60 bg-amber-500/10"
            : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700")
      }
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={active}
        className="flex min-w-0 cursor-pointer items-center gap-2 text-left disabled:cursor-default"
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
            className="h-5 w-5 shrink-0 object-contain"
            // No logo for this league, or it failed to load: drop the img and
            // fall back to showing just the league name.
            onError={(event) => {
              event.currentTarget.style.display = "none";
            }}
          />
        )}
        <span
          className={
            "sd-body min-w-0 truncate font-medium " +
            (followAll
              ? "text-emerald-300"
              : active
                ? "text-amber-300"
                : "text-zinc-300")
          }
        >
          {league.name}
        </span>
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
            "sd-micro self-start rounded-full px-2 py-0.5 font-semibold transition-colors " +
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

/** League multi-select: cards in a grid, grouped by sport. */
export default function LeagueStep({
  countryCodes,
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

  // Narrow to the chosen countries, keeping everything that has no country
  // of its own. A league already SELECTED always survives the filter — going
  // back and unpicking a country must not silently drop a league the user
  // chose while it was picked, which would be a change they never made and
  // would not see until the review step.
  const chosen = new Set(countryCodes);
  const leagues =
    chosen.size === 0
      ? leaguesQuery.data.leagues
      : leaguesQuery.data.leagues.filter(
          (league) =>
            league.country_code === null ||
            chosen.has(league.country_code) ||
            selectedIds.includes(league.id),
        );
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
      <header className="sd-rule pb-4">
        <h2 className="sd-title text-zinc-100">Choose your leagues</h2>
        <p className="sd-body mt-2 max-w-prose text-pretty text-zinc-400">
          Pick every league you want to follow — you&rsquo;ll choose teams next.
          Or hit &ldquo;Follow all&rdquo; on a league to follow it in full (every
          game), no team picks needed.
        </p>
      </header>

      {nationalLeagues.length > 0 && (
        <section>
          <h3 className="sd-eyebrow text-zinc-500">National teams</h3>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {nationalLeagues.map(renderChip)}
          </div>
        </section>
      )}

      {sportGroups.map(({ sport, leagues: sportLeagues }) => (
        <section key={sport}>
          <h3 className="sd-eyebrow text-zinc-500">{sportLabel(sport)}</h3>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
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
