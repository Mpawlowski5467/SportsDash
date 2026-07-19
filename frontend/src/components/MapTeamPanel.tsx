import { useEffect, useState } from "react";

import type { Game, MapTeam } from "../types";
import { formatDateTime, formatShortDate } from "../lib/time";
import TeamLogo from "./TeamLogo";
import Portal from "./Portal";
import { useTopmostEsc } from "./modalChrome";
import WeatherInline from "./WeatherInline";
import { useOpenNation, useOpenTeam } from "./TeamDetailPanel";
import { PLANE_SVG } from "../views/map/markers";

/** A followed team's flight to its next away game (computed from coordinates). */
export interface TravelInfo {
  destVenue: string;
  opponent: string;
  distanceKm: number;
  flightMinutes: number;
  tzDelta: number; // signed time-zone change, hours
  inTransit: boolean; // the away game is within the in-transit window
}

/** "2h 40m" / "55m" from a minute count (round first, then split, so the
 * remainder can never render an impossible "1h 60m"). */
function formatFlight(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/**
 * Slide-in side panel for a map marker. Replaces the old MapLibre popup
 * (whose `closeOnClick` swallowed the very click that opened it) with a
 * proper right-hand drawer showing the team, the stadium it plays at, and
 * — for a World Cup-style host-country tournament — its next match.
 *
 * Always mounted so the open/close transform animates both ways; it keeps
 * the last-selected team's content visible while sliding out. Closed, it is
 * pushed off-screen and made non-interactive so it never blocks the map.
 */
export default function MapTeamPanel({
  team,
  leagueNames,
  matchesByVenue,
  travel,
  onClose,
}: {
  team: MapTeam | null;
  leagueNames: Record<string, string>;
  matchesByVenue: Record<string, Game[]>;
  travel?: TravelInfo | null;
  onClose: () => void;
}) {
  const open = team !== null;
  const openTeam = useOpenTeam();
  const openNation = useOpenNation();

  // Retain the last team so its content stays rendered through the slide-out.
  const [last, setLast] = useState<MapTeam | null>(team);
  useEffect(() => {
    if (team !== null) setLast(team);
  }, [team]);
  const shown = team ?? last;

  // ESC closes while open — via the shared stack, so a modal opened on
  // top of this panel takes ESC priority.
  useTopmostEsc(onClose, open);

  const leagueLabel = shown
    ? shown.league_name ?? leagueNames[shown.league_id] ?? shown.league_id
    : "";

  const facts: { label: string; value: string }[] = [];
  if (shown?.location) facts.push({ label: "Location", value: shown.location });
  if (shown?.capacity != null) {
    facts.push({
      label: "Capacity",
      value: shown.capacity.toLocaleString("en-US"),
    });
  }
  if (shown?.opened != null) {
    facts.push({ label: "Opened", value: String(shown.opened) });
  }
  if (shown?.surface) facts.push({ label: "Surface", value: shown.surface });

  const venueMatches = shown ? matchesByVenue[shown.venue ?? ""] ?? [] : [];

  return (
    <Portal>
    <aside
      aria-hidden={!open}
      className={
        // top-12 clears the sticky 48px app header so the panel's own header
        // (crest + name + close) isn't hidden behind the nav.
        "fixed bottom-0 right-0 top-12 z-40 flex w-[20rem] max-w-[88vw] flex-col " +
        "border-l border-zinc-800 bg-zinc-900 shadow-2xl transition-transform " +
        "duration-300 ease-out motion-reduce:transition-none sm:w-[22rem] " +
        (open ? "translate-x-0" : "pointer-events-none translate-x-full")
      }
    >
      {shown && (
        <>
          {/* Accent strip in the team color. */}
          <div
            className="h-1 w-full shrink-0"
            style={{ backgroundColor: shown.color ?? "#f59e0b" }}
          />
          <header className="flex items-start gap-3 border-b border-zinc-800 px-4 py-3">
            <TeamLogo
              logoUrl={shown.logo_url}
              name={shown.name}
              abbreviation={shown.abbreviation}
              color={shown.color}
              size="lg"
            />
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-base font-semibold text-zinc-100">
                {shown.name}
              </h2>
              <p className="truncate text-xs uppercase tracking-wide text-zinc-500">
                {leagueLabel}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="-mr-1 -mt-1 shrink-0 rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
            >
              <svg
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                className="h-4 w-4"
                aria-hidden="true"
              >
                <path d="M5 5l10 10M15 5L5 15" />
              </svg>
            </button>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            {shown.image_url && (
              <img
                src={shown.image_url}
                alt=""
                className="mb-4 h-36 w-full rounded-lg object-cover"
                onError={(e) => {
                  e.currentTarget.style.display = "none";
                }}
              />
            )}

            {shown.next_opponent && (
              <section className="mb-4 rounded-lg border border-zinc-800 bg-zinc-800/40 px-3 py-2.5">
                <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                  Next match
                </p>
                <p className="mt-0.5 text-sm font-semibold text-zinc-100">
                  vs {shown.next_opponent}
                </p>
                {shown.next_match_time && (
                  <p className="text-xs text-zinc-400">
                    {formatDateTime(shown.next_match_time)}
                  </p>
                )}
              </section>
            )}

            {travel && (
              <section className="mb-4 rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                    Travel · next away game
                  </p>
                  {travel.inTransit && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-400">
                      {/* The map's airliner, rotated to point along the
                          chip's reading direction (static markup). */}
                      <span
                        aria-hidden="true"
                        className="inline-flex rotate-90"
                        dangerouslySetInnerHTML={{
                          __html: PLANE_SVG.replace(
                            'width="22" height="27"',
                            'width="9" height="11"',
                          ),
                        }}
                      />
                      In transit
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-sm text-zinc-200">
                  to {travel.destVenue}
                </p>
                <dl className="mt-2 grid grid-cols-3 gap-2 text-center">
                  <div>
                    <dt className="text-[10px] uppercase tracking-wide text-zinc-500">
                      Distance
                    </dt>
                    <dd className="text-sm font-semibold tabular-nums text-zinc-100">
                      {Math.round(travel.distanceKm).toLocaleString("en-US")}
                      <span className="text-[10px] text-zinc-500"> km</span>
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] uppercase tracking-wide text-zinc-500">
                      Flight
                    </dt>
                    <dd className="text-sm font-semibold tabular-nums text-zinc-100">
                      {formatFlight(travel.flightMinutes)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] uppercase tracking-wide text-zinc-500">
                      Time zones
                    </dt>
                    <dd className="text-sm font-semibold tabular-nums text-zinc-100">
                      {travel.tzDelta === 0
                        ? "same"
                        : `${travel.tzDelta > 0 ? "+" : ""}${travel.tzDelta}h`}
                    </dd>
                  </div>
                </dl>
              </section>
            )}

            <section>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                {shown.next_opponent ? "Plays at" : "Home venue"}
              </p>
              <p className="mt-0.5 text-sm font-semibold text-zinc-100">
                {shown.venue ?? "Unknown venue"}
              </p>
              {facts.length > 0 && (
                <dl className="mt-3 space-y-2">
                  {facts.map((fact) => (
                    <div
                      key={fact.label}
                      className="flex items-baseline justify-between gap-3 border-t border-zinc-800/70 pt-2 text-sm first:border-0 first:pt-0"
                    >
                      <dt className="shrink-0 text-zinc-500">{fact.label}</dt>
                      <dd className="text-right text-zinc-200">{fact.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>

            {shown.weather && (
              <section className="mt-4 border-t border-zinc-800/70 pt-4">
                <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                  Weather
                </p>
                <div className="mt-2">
                  <WeatherInline weather={shown.weather} />
                </div>
              </section>
            )}

            {shown.source === "followed" ? (
              <button
                type="button"
                onClick={() => {
                  const id = shown.team_id;
                  onClose();
                  openTeam(id);
                }}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-100 transition hover:bg-zinc-700"
              >
                View team page →
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  const { league_id, name } = shown;
                  onClose();
                  openNation(league_id, name);
                }}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-100 transition hover:bg-zinc-700"
              >
                View {shown.name} →
              </button>
            )}

            {venueMatches.length > 0 && shown && (
              <section className="mt-5">
                <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                  Matches at this stadium
                </p>
                <ul className="mt-2 divide-y divide-zinc-800/70">
                  {venueMatches.map((game) => (
                    <MatchRow
                      key={game.id}
                      game={game}
                      highlightName={shown.name}
                    />
                  ))}
                </ul>
              </section>
            )}
          </div>
        </>
      )}
    </aside>
    </Portal>
  );
}

/** One compact fixture row: date · away v home, with the score once it exists. */
function MatchRow({
  game,
  highlightName,
}: {
  game: Game;
  highlightName: string;
}) {
  const involves =
    game.home.name === highlightName || game.away.name === highlightName;
  const hasScore = game.home.score !== null && game.away.score !== null;
  return (
    <li className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="w-12 shrink-0 text-xs text-zinc-500">
          {formatShortDate(game.start_time)}
        </span>
        <span
          className={
            "truncate " + (involves ? "text-zinc-100" : "text-zinc-300")
          }
        >
          {game.away.name} <span className="text-zinc-600">v</span>{" "}
          {game.home.name}
        </span>
      </div>
      <span className="shrink-0 text-xs tabular-nums text-zinc-400">
        {hasScore ? `${game.away.score}–${game.home.score}` : "—"}
      </span>
    </li>
  );
}
