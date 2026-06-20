import { useEffect } from "react";

import type { Game, NewsItem, Player } from "../types";
import { formatShortDate, relativeTime } from "../lib/time";
import Portal from "./Portal";
import TeamLogo from "./TeamLogo";
import PlayerAvatar from "./PlayerAvatar";
import { useMapFocus } from "./MapFocusContext";

export type Outcome = "W" | "L" | "D";

/**
 * Normalized, presentation-ready profile for ONE team — built by either the
 * followed-team loader or the competition-nation loader, so clubs and nations
 * share the exact same rich view.
 */
export interface TeamProfile {
  name: string;
  abbreviation: string | null;
  logoUrl: string | null;
  color: string | null;
  subtitle: string; // league name, or group ("Group C")
  rank: number | null;
  record: string | null; // "W-D-L"
  points: number | null;
  form: Outcome[]; // most-recent first, up to 5
  nextMatch: Game | null;
  fixtures: Game[]; // upcoming
  results: Game[]; // recent, newest-first
  roster: Player[]; // [] for nations
  news: NewsItem[]; // [] for nations
  stadium: {
    venue: string | null;
    location: string | null;
    capacity: number | null;
    imageUrl: string | null;
  } | null;
  description: string | null; // club "About" history paragraph
  founded: number | null; // founding year
}

const FORM_CHIP: Record<Outcome, string> = {
  W: "bg-emerald-500/20 text-emerald-300",
  L: "bg-red-500/20 text-red-300",
  D: "bg-zinc-600/40 text-zinc-300",
};

/**
 * The unified team/nation dashboard. A hero header (crest, record, recent
 * form, next match) over an optional stadium photo, then fixtures, recent
 * results, roster, and news — each section shown only when it has data, so
 * the same component serves a fully-followed club and a by-name World Cup
 * nation alike.
 */
export default function TeamProfileView({
  profile,
  isLoading,
  isError,
  onClose,
}: {
  profile: TeamProfile | null;
  isLoading: boolean;
  isError: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const { requestFocus } = useMapFocus();
  const accent = profile?.color ?? "#f59e0b";

  return (
    <Portal>
      <div
        className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 sm:p-8"
        onClick={onClose}
      >
        <div
          role="dialog"
          aria-modal="true"
          className="my-auto flex w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl"
          onClick={(event) => event.stopPropagation()}
        >
          {/* Hero — optional stadium photo behind the crest + identity. */}
          <div className="relative">
            {profile?.stadium?.imageUrl && (
              <div className="absolute inset-0">
                <img
                  src={profile.stadium.imageUrl}
                  alt=""
                  className="h-full w-full object-cover opacity-30"
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
                <div className="absolute inset-0 bg-gradient-to-t from-zinc-900 via-zinc-900/70 to-zinc-900/40" />
              </div>
            )}
            <div
              className="absolute inset-x-0 top-0 h-1"
              style={{ backgroundColor: accent }}
            />
            <div className="relative flex items-start gap-3 px-4 pb-3 pt-4">
              <TeamLogo
                logoUrl={profile?.logoUrl}
                name={profile?.name ?? "Team"}
                abbreviation={profile?.abbreviation}
                color={profile?.color}
                size="lg"
              />
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-xl font-semibold text-zinc-100">
                  {profile?.name ?? "Team"}
                </h2>
                <p className="truncate text-xs uppercase tracking-wide text-zinc-400">
                  {profile?.subtitle ?? ""}
                </p>
                {profile && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {profile.rank !== null && (
                      <Chip>#{profile.rank}</Chip>
                    )}
                    {profile.record && <Chip>{profile.record}</Chip>}
                    {profile.points !== null && (
                      <Chip>{profile.points} pts</Chip>
                    )}
                    {profile.form.length > 0 && (
                      <span className="flex items-center gap-1">
                        {profile.form.map((o, i) => (
                          <span
                            key={i}
                            className={`flex size-5 items-center justify-center rounded text-[11px] font-bold ${FORM_CHIP[o]}`}
                          >
                            {o}
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="shrink-0 rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
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
            </div>
          </div>

          <div className="max-h-[72vh] overflow-y-auto border-t border-zinc-800 px-4 py-4">
            {isLoading ? (
              <p className="text-sm text-zinc-500">Loading…</p>
            ) : isError || !profile ? (
              <p className="text-sm text-red-400">Couldn't load this team.</p>
            ) : (
              <div className="space-y-5">
                {(profile.description || profile.founded !== null) && (
                  <Section
                    title={
                      profile.founded !== null
                        ? `About · Founded ${profile.founded}`
                        : "About"
                    }
                  >
                    {profile.description && (
                      <p className="text-sm leading-relaxed text-zinc-300">
                        {profile.description}
                      </p>
                    )}
                  </Section>
                )}

                {profile.nextMatch && (
                  <button
                    type="button"
                    onClick={() => {
                      const game = profile.nextMatch!;
                      // Which side is this team? Prefer a name match; fall back
                      // to whichever side is the followed team if the
                      // denormalized names don't line up.
                      let homeMine = game.home.name === profile.name;
                      if (!homeMine && game.away.name !== profile.name) {
                        homeMine =
                          game.home.team_id !== null &&
                          game.followed_team_ids.includes(game.home.team_id);
                      }
                      const side = homeMine ? game.home : game.away;
                      requestFocus({
                        gameId: game.id,
                        venue: game.venue,
                        teamId: side.team_id,
                        isHome: homeMine,
                      });
                      onClose();
                    }}
                    className="group w-full rounded-lg border border-zinc-800 bg-zinc-800/40 px-3 py-2.5 text-left transition hover:border-zinc-700 hover:bg-zinc-800/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
                  >
                    <p className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                      <span>Next match</span>
                      <span className="text-amber-400 opacity-0 transition group-hover:opacity-100">
                        View on map →
                      </span>
                    </p>
                    <NextMatch game={profile.nextMatch} self={profile.name} />
                  </button>
                )}

                <div className="grid gap-5 sm:grid-cols-2">
                  {profile.fixtures.length > 0 && (
                    <Section title="Fixtures">
                      <FixtureList
                        games={profile.fixtures}
                        self={profile.name}
                      />
                    </Section>
                  )}
                  {profile.results.length > 0 && (
                    <Section title="Recent results">
                      <FixtureList
                        games={profile.results}
                        self={profile.name}
                        showScore
                      />
                    </Section>
                  )}
                  {profile.roster.length > 0 && (
                    <Section title={`Roster · ${profile.roster.length}`}>
                      <ul className="max-h-60 divide-y divide-zinc-800/70 overflow-y-auto">
                        {profile.roster.map((p) => (
                          <li
                            key={p.id}
                            className="flex items-center justify-between gap-2 py-1.5 text-sm"
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              <PlayerAvatar
                                photoUrl={p.photo_url}
                                name={p.name}
                                size="sm"
                              />
                              <span className="min-w-0">
                                <span className="block truncate text-zinc-200">
                                  {p.name}
                                  {p.position && (
                                    <span className="ml-1.5 text-xs text-zinc-500">
                                      {p.position}
                                    </span>
                                  )}
                                </span>
                                {p.career_stat_line && (
                                  <span className="block truncate text-xs tabular-nums text-zinc-600">
                                    Career: {p.career_stat_line}
                                  </span>
                                )}
                              </span>
                            </span>
                            {p.stat_line && (
                              <span className="shrink-0 text-xs tabular-nums text-zinc-400">
                                {p.stat_line}
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </Section>
                  )}
                  {profile.news.length > 0 && (
                    <Section title="Latest news">
                      <ul className="flex flex-col gap-2">
                        {profile.news.map((item) => (
                          <li key={item.id}>
                            <a
                              href={item.url}
                              target="_blank"
                              rel="noreferrer"
                              className="block text-sm text-zinc-200 hover:underline"
                            >
                              {item.title}
                            </a>
                            <p className="text-xs text-zinc-500">
                              {item.source}
                              {item.published_at
                                ? ` · ${relativeTime(item.published_at)}`
                                : ""}
                            </p>
                          </li>
                        ))}
                      </ul>
                    </Section>
                  )}
                </div>

                {profile.stadium?.venue && (
                  <Section title="Stadium">
                    <p className="text-sm text-zinc-200">
                      {profile.stadium.venue}
                    </p>
                    <p className="text-xs text-zinc-500">
                      {[
                        profile.stadium.location,
                        profile.stadium.capacity != null
                          ? `Capacity ${profile.stadium.capacity.toLocaleString("en-US")}`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </Section>
                )}

                {profile.fixtures.length === 0 &&
                  profile.results.length === 0 &&
                  profile.roster.length === 0 &&
                  profile.news.length === 0 &&
                  !profile.stadium?.venue && (
                    <p className="text-sm text-zinc-500">
                      No games, roster, or news available right now.
                    </p>
                  )}
              </div>
            )}
          </div>
        </div>
      </div>
    </Portal>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md bg-zinc-800 px-2 py-0.5 text-xs font-medium tabular-nums text-zinc-200">
      {children}
    </span>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

function NextMatch({ game, self }: { game: Game; self: string }) {
  const homeMine = game.home.name === self;
  const opp = homeMine ? game.away : game.home;
  return (
    <div className="mt-0.5 flex items-center gap-2">
      <span className="text-sm text-zinc-500">{homeMine ? "vs" : "@"}</span>
      <TeamLogo
        logoUrl={opp.logo_url}
        name={opp.name}
        abbreviation={opp.abbreviation}
        color={opp.color}
        size="sm"
      />
      <span className="flex-1 truncate text-sm font-semibold text-zinc-100">
        {opp.name}
      </span>
      <span className="shrink-0 text-right text-xs text-zinc-400">
        {formatShortDate(game.start_time)}
        {game.venue ? ` · ${game.venue}` : ""}
      </span>
    </div>
  );
}

function FixtureList({
  games,
  self,
  showScore,
}: {
  games: Game[];
  self: string;
  showScore?: boolean;
}) {
  return (
    <ul className="flex flex-col gap-1.5">
      {games.map((game) => {
        const homeMine = game.home.name === self;
        const opp = homeMine ? game.away : game.home;
        const score =
          showScore && game.home.score !== null && game.away.score !== null
            ? `${homeMine ? game.home.score : game.away.score}–${
                homeMine ? game.away.score : game.home.score
              }`
            : null;
        return (
          <li
            key={game.id}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="text-zinc-500">{homeMine ? "vs" : "@"}</span>
              <TeamLogo
                logoUrl={opp.logo_url}
                name={opp.name}
                abbreviation={opp.abbreviation}
                color={opp.color}
                size="xs"
              />
              <span className="truncate text-zinc-200">
                {opp.abbreviation ?? opp.name}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2 text-xs text-zinc-500">
              {score && (
                <span className="font-semibold tabular-nums text-zinc-200">
                  {score}
                </span>
              )}
              {formatShortDate(game.start_time)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
