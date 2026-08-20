/**
 * Country picker — the first drill-down for a sport organised by country.
 *
 * Soccer is 40 of the catalog's 55 leagues, and a flat list of all 40 is the
 * worst way to find the two you care about. ESPN's own league codes carry the
 * country (`soccer/eng.1`), so the wizard can ask "where?" before "which?" —
 * pick England, get its four leagues, then its teams.
 *
 * The map is an OpenStreetMap basemap (the OpenFreeMap tiles the Map view
 * already uses) with one pin per country that HAS leagues. Selecting is
 * additive: pick as many countries as you like, and the league step narrows
 * to their leagues plus everything that belongs to no country at all
 * (confederation competitions, and every sport that is not organised this
 * way). Choosing none is a valid answer — the league step then shows
 * everything, exactly as it did before this step existed.
 *
 * Pins are real buttons in the DOM, so they tab and take Enter/Space. That is
 * only affordable because there are ~21 of them: the Map view's 700+ markers
 * are why it does not do this (see the keyboard loose end in ROADMAP).
 */

import { useEffect, useMemo, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { CatalogCountry, CatalogLeague } from "../../types";
import { PRIMARY_BUTTON, SECONDARY_BUTTON } from "./buttons";

/** Same style the Map view uses — keyless OSM vector tiles. */
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

/**
 * Zoom at which a pin earns its country name.
 *
 * Europe holds nine of the twenty-one countries inside a few degrees, so at
 * world zoom a name label on each one is a stack of overlapping rectangles
 * with Italy hidden behind Switzerland. Below this zoom every pin is a bare
 * count; above it there is room for the name. Hover and keyboard focus show
 * the name at any zoom, so nothing is undiscoverable while zoomed out.
 */
const NAME_ZOOM = 3.2;

interface Pin {
  marker: maplibregl.Marker;
  name: HTMLElement;
}

function pinElement(
  country: CatalogCountry,
  selected: boolean,
): { el: HTMLElement; name: HTMLElement } {
  const el = document.createElement("button");
  el.type = "button";
  el.setAttribute(
    "aria-label",
    `${country.name} — ${country.league_count} ${
      country.league_count === 1 ? "league" : "leagues"
    }`,
  );
  el.setAttribute("aria-pressed", String(selected));
  el.className =
    "group flex cursor-pointer items-center gap-1.5 rounded-full border px-1.5 py-1 text-xs font-semibold shadow-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 " +
    (selected
      ? "border-amber-300 bg-amber-400 text-zinc-950"
      : "border-zinc-600 bg-zinc-900/95 text-zinc-200 hover:border-amber-400 hover:text-amber-300");

  const name = document.createElement("span");
  name.textContent = country.name;
  // Hidden by zoom, but revealed on hover/focus at ANY zoom — the group-*
  // variants key off the button above.
  name.className =
    "hidden pl-1 whitespace-nowrap group-hover:inline group-focus-visible:inline";

  const count = document.createElement("span");
  count.textContent = String(country.league_count);
  count.className =
    "min-w-5 rounded-full px-1 text-center tabular-nums " +
    (selected ? "bg-zinc-950/25" : "bg-zinc-800 text-zinc-400");

  el.append(name, count);
  return { el, name };
}

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
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const pinsRef = useRef<Pin[]>([]);
  const fittedRef = useRef(false);

  // Handlers change every render (they close over `selectedCodes`), so the
  // marker effect below must not depend on them or it would tear every pin
  // down and rebuild it on each keystroke. A ref keeps the latest without
  // re-running: the same pattern the Map view uses for its poll-safe markers.
  const onToggleRef = useRef(onToggle);
  useEffect(() => {
    onToggleRef.current = onToggle;
  }, [onToggle]);

  const selected = useMemo(() => new Set(selectedCodes), [selectedCodes]);

  // Mount the map ONCE. A remount on every selection would refetch tiles and
  // throw away the user's pan/zoom, which is the whole point of a map.
  useEffect(() => {
    if (containerRef.current === null || mapRef.current !== null) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      // Overridden by fitBounds below once the pins are known; these are
      // only what paints for the first frame.
      center: [-20, 30],
      zoom: 1.7,
      attributionControl: { compact: true },
    });
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Rebuild pins when the selection changes — 21 markers, so a full rebuild
  // is cheaper than reconciling, and there is no animation to interrupt.
  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;
    for (const pin of pinsRef.current) pin.marker.remove();
    pinsRef.current = countries.map((country) => {
      const { el, name } = pinElement(country, selected.has(country.code));
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        onToggleRef.current(country.code);
      });
      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([country.lon, country.lat])
        .addTo(map);
      return { marker, name };
    });

    const applyZoom = () => {
      const show = map.getZoom() >= NAME_ZOOM;
      for (const pin of pinsRef.current) {
        pin.name.classList.toggle("hidden", !show);
        pin.name.classList.toggle("inline", show);
      }
    };
    applyZoom();
    map.on("zoom", applyZoom);

    // Frame the pins rather than trusting a hardcoded center: the catalog
    // decides which countries exist, so the right view is whatever holds
    // them. Only on the FIRST build — refitting on every selection would
    // yank the camera out from under someone mid-pick.
    if (!fittedRef.current && countries.length > 0) {
      const bounds = new maplibregl.LngLatBounds();
      for (const country of countries) {
        bounds.extend([country.lon, country.lat]);
      }
      map.fitBounds(bounds, { padding: 56, animate: false, maxZoom: 4 });
      fittedRef.current = true;
    }

    return () => {
      map.off("zoom", applyZoom);
      for (const pin of pinsRef.current) pin.marker.remove();
      pinsRef.current = [];
    };
  }, [countries, selected]);

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
        ref={containerRef}
        className="h-[26rem] w-full overflow-hidden rounded-lg border border-zinc-800"
      />

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
