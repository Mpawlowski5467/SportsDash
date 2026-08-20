/**
 * Country picker — the first drill-down for a sport organised by country.
 *
 * Soccer is 40 of the catalog's 55 leagues, and a flat list of all 40 is the
 * worst way to find the two you care about. ESPN's own league codes carry the
 * country (`soccer/eng.1`), so the wizard can ask "where?" before "which?" —
 * pick England, get its four leagues, then its teams.
 *
 * This was an OpenStreetMap map with a pin per country, and it is a grid now.
 * The map looked good and worked badly: nine of the twenty-one countries sit
 * within a few degrees of each other, so pins overlapped and names had to be
 * hidden below a zoom threshold — leaving a world map covered in anonymous
 * dots you had to hover to read. A grid answers "which countries can I pick?"
 * at a glance, needs no panning, works on a phone, and takes MapLibre
 * (~1.1MB) back out of the onboarding bundle. The Map view is still where a
 * map earns its place: there, position IS the information.
 *
 * Selecting is additive: pick as many countries as you like, and the league
 * step narrows to their leagues plus everything that belongs to no country at
 * all (confederation competitions, and every sport not organised this way).
 * Choosing none is a valid answer — the league step then shows everything,
 * exactly as it did before this step existed.
 */

import { useMemo } from "react";

import type { CatalogCountry, CatalogLeague } from "../../types";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";

export default function CountryStep({
  countries,
  leagues,
  selectedCodes,
  onToggle,
  onContinue,
  onSkip,
}: {
  countries: CatalogCountry[];
  leagues: CatalogLeague[];
  selectedCodes: string[];
  onToggle: (code: string) => void;
  onContinue: () => void;
  onSkip: () => void;
}) {
  const selected = useMemo(() => new Set(selectedCodes), [selectedCodes]);

  const chosenLeagueCount = useMemo(
    () =>
      leagues.filter(
        (league) =>
          league.country_code !== null && selected.has(league.country_code),
      ).length,
    [leagues, selected],
  );

  return (
    <div className="space-y-6">
      <header className="sd-rule pb-4">
        <h2 className="sd-title text-zinc-100">Where do you watch?</h2>
        <p className="sd-body mt-2 max-w-prose text-pretty text-zinc-400">
          Pick the countries whose leagues you follow and the next step narrows
          to them. Everything that belongs to no single country — the Champions
          League, the World Cup, the NBA, the tours — is offered either way.
        </p>
      </header>

      <div
        role="group"
        aria-label="Countries"
        className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4"
      >
        {countries.map((country) => {
          const active = selected.has(country.code);
          return (
            // The whole card is the button — there is nothing else inside it
            // to click, so it needs none of the overlay the league card uses.
            <button
              key={country.code}
              type="button"
              onClick={() => onToggle(country.code)}
              aria-pressed={active}
              className={
                "flex cursor-pointer items-center justify-between gap-2 rounded-lg border p-3 text-left transition-colors " +
                (active
                  ? "border-amber-400/60 bg-amber-500/10"
                  : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700")
              }
            >
              <span
                className={
                  "sd-body min-w-0 truncate font-medium " +
                  (active ? "text-amber-300" : "text-zinc-300")
                }
              >
                {country.name}
              </span>
              <span
                className={
                  "sd-micro shrink-0 rounded-full px-1.5 py-0.5 font-semibold tabular-nums " +
                  (active
                    ? "bg-amber-400/20 text-amber-200"
                    : "bg-zinc-800 text-zinc-400")
                }
                aria-label={`${country.league_count} ${
                  country.league_count === 1 ? "league" : "leagues"
                }`}
              >
                {country.league_count}
              </span>
            </button>
          );
        })}
      </div>

      <p className="sd-meta text-zinc-500" aria-live="polite">
        {selected.size === 0
          ? "No countries picked — the next step will show every league."
          : `${selected.size} ${selected.size === 1 ? "country" : "countries"} · ${chosenLeagueCount} ${chosenLeagueCount === 1 ? "league" : "leagues"} to choose from next`}
      </p>

      <footer className="flex items-center justify-between border-t border-zinc-800 pt-4">
        <button type="button" onClick={onSkip} className={SECONDARY_BUTTON}>
          Skip
        </button>
        <button type="button" onClick={onContinue} className={PRIMARY_BUTTON}>
          Continue
        </button>
      </footer>
    </div>
  );
}
