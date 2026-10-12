import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError } from "../../api";
import {
  FormStrip,
  OutcomeSquare,
  SectionHead,
  Segmented,
} from "../../components/bs";
import { useResults, useSeasonResults } from "../../hooks";
import { fightMethodLabel, fightResultOf } from "../../lib/fight";
import { isIndividualSport, sportWords } from "../../lib/labels";
import { supportsArchives } from "../../lib/seasons";
import { localDateKey } from "../../lib/time";
import type { Game, GameSide, League, Team } from "../../types";
import {
  RESULTS_LIMIT,
  currentSeason,
  mergeSeason,
  seasonKey,
  summarize,
  venueWord,
} from "../results/model";
import { isFollowedSide } from "../today/model";
import {
  LAST_GAMES,
  bothFollowed,
  focusSide,
  formCaption,
  formLetters,
  lastGames,
  lastTitle,
  lastTotals,
  possessive,
  resultDate,
  scoreOrderNote,
  sideNoun,
  sideWord,
  type Outcome,
  type SideKey,
} from "./model";
import { SheetNote, SideBlock, SpareRoom } from "./parts";

/**
 * How often the stored results are read again: the view stays mounted, and
 * a table that never refetched would show last week's last eight. A plain
 * read of what the server has stored.
 */
const RESULTS_REFRESH_MS = 5 * 60_000;
/**
 * How long the stored results are held back for the season's list, so a
 * record the list is about to correct is not flashed first (as on Results).
 */
const SEASON_WAIT_MS = 2_000;
/** How often a failed season list is asked for again; each try is a provider call. */
const SEASON_RETRY_MS = 10 * 60_000;

/**
 * A followed team's finished games, newest first, the way Results builds
 * them: the stored results (current, but with a hole for every stretch the
 * app was off) laid over the provider's list of the season being played
 * (complete, but hours behind). The same two queries Results uses, so the
 * answers are shared. Without a list (no archive for the league, or it did
 * not come) the stored results stand alone.
 */
function useSeasonSoFar(
  team: Team,
  league: League | undefined,
  seasonLabel: string | undefined,
  labelKnown: boolean,
  now: Date,
) {
  const resultsQuery = useResults(team.id, RESULTS_LIMIT, {
    refetchInterval: RESULTS_REFRESH_MS,
  });
  const stored = resultsQuery.data;
  const archives = supportsArchives(league);
  const thisYear = now.getFullYear();
  const liveKey =
    archives && labelKnown
      ? seasonKey(seasonLabel, league?.sport, thisYear)
      : undefined;
  const seasonQuery = useSeasonResults(team.id, liveKey, { live: true });

  // "No such season" is an answer; anything else is asked for again, slowly.
  const seasonFailed =
    seasonQuery.isError &&
    !(seasonQuery.error instanceof ApiError && seasonQuery.error.status === 404);
  const reaskSeason = seasonQuery.refetch;
  useEffect(() => {
    if (!seasonFailed) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void reaskSeason();
    }, SEASON_RETRY_MS);
    return () => window.clearInterval(timer);
  }, [seasonFailed, reaskSeason]);

  const answered =
    !archives || (labelKnown && (liveKey === undefined || seasonQuery.isFetched));
  const [waitedOut, setWaitedOut] = useState(false);
  useEffect(() => {
    if (answered) return;
    const timer = window.setTimeout(() => setWaitedOut(true), SEASON_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [answered]);

  const playing = currentSeason(seasonQuery.data);
  const merged = useMemo(
    () =>
      playing !== undefined && stored !== undefined
        ? mergeSeason(playing, stored, team.league_id)
        : undefined,
    [playing, stored, team.league_id],
  );
  const ready = stored !== undefined && (answered || waitedOut);
  return {
    games: ready ? (merged ?? stored) : undefined,
    failed: resultsQuery.isError && stored === undefined,
    retry: () => void resultsQuery.refetch(),
  };
}

/** One side's form letters as squares, with what they are said beside them. */
function FormRow({
  side,
  leagueId,
  letters,
  caption,
}: {
  side: GameSide;
  leagueId: string;
  letters: readonly Outcome[];
  caption: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-hair px-5 py-3 first:border-t-0">
      <div className="flex min-w-0 items-center gap-2.5">
        <SideBlock side={side} leagueId={leagueId} />
        <div className="min-w-0">
          <div className="font-semibold [overflow-wrap:anywhere]">{side.name}</div>
          <div className="text-sm text-ink-2">{caption}</div>
        </div>
      </div>
      <FormStrip
        size={24}
        results={letters}
        label={`${side.name}, ${caption.charAt(0).toLowerCase()}${caption.slice(1)}`}
      />
    </div>
  );
}

/** A figure small enough for a third of a phone-wide sheet, whatever its length. */
function totalSize(value: string): string {
  if (value.length <= 3) return "text-[2.5rem]";
  return value.length <= 5 ? "text-[2rem]" : "text-[1.625rem]";
}

/**
 * The followed side's last few finished games as the mockup's table (date,
 * home or away, the opponent, the result, the score with the team's own
 * figure first), the figures that sum them up, and the other side's form
 * as a strip at the foot.
 */
function TeamLast({
  game,
  focus,
  team,
  league,
  seasonLabel,
  labelKnown,
  now,
  otherLetters,
  switcher,
}: {
  game: Game;
  focus: SideKey;
  team: Team;
  league: League | undefined;
  seasonLabel: string | undefined;
  labelKnown: boolean;
  now: Date;
  /** The other side's form letters, newest first. */
  otherLetters: readonly Outcome[];
  /** With both sides followed: the control that picks whose table it is. */
  switcher: ReactNode;
}) {
  const sport = game.sport;
  const words = useMemo(() => sportWords(sport), [sport]);
  const season = useSeasonSoFar(team, league, seasonLabel, labelKnown, now);
  const rows = useMemo(
    () => lastGames(season.games ?? [], team, LAST_GAMES),
    [season.games, team],
  );
  const totals = useMemo(
    () =>
      lastTotals(
        summarize(
          rows.map((row) => row.game),
          team,
          sport,
          LAST_GAMES,
        ),
        sport,
        words,
      ),
    [rows, team, sport, words],
  );
  const title =
    rows.length > 0
      ? lastTitle(team.name, rows.length, words)
      : `${team.name}, recent results`;
  const tallies = words.tally !== null;
  const wide = rows.some((row) => (row.mine ?? 0) > 99 || (row.theirs ?? 0) > 99);
  // Three figures (basketball) need a wider pair; a phone-wide sheet gives
  // each figure a little less, so the opponent's name keeps a word's width.
  const figure = wide ? "w-9 @[24rem]:w-10" : "w-6 @[24rem]:w-[1.625rem]";
  const other = focus === "home" ? game.away : game.home;
  // What the opponent column drops when the sheet is too narrow for both a
  // block and a name that does not break inside a word. A team keeps its
  // block (its colour and code) and the name is only read out; a person
  // keeps the name, since the block would be bare initials, and gets the
  // block back only in a sheet wide enough for both beside a date with its
  // year and a method ("Mar 1, 2025", "Submission"): a 390px phone's sheet
  // is not. Three-figure scores take a team's room sooner.
  const individual = isIndividualSport(sport);
  // Whole class names, so the stylesheet is generated for each of them.
  const blockShown = individual ? "hidden @[28rem]:inline-flex" : "";
  // Narrower still (a 375px phone and under) a person's name needs more:
  // the "vs" goes (it is the same word on every row, people have no home),
  // and a date's year may drop under it.
  const venueShown = individual ? "hidden @[22rem]:block " : "";
  const dateWraps = individual
    ? "@[22rem]:whitespace-nowrap "
    : "whitespace-nowrap ";
  const nameShown = individual
    ? ""
    : wide
      ? "sr-only @[25.5rem]:not-sr-only "
      : "sr-only @[22rem]:not-sr-only ";

  return (
    <section
      aria-label={title}
      className="flex min-w-0 flex-[1_1_21.25rem] flex-col"
    >
      <SectionHead
        title={title}
        note={
          rows.length > 0
            ? (scoreOrderNote(team.name, words) ?? "Newest first")
            : undefined
        }
      />
      <div className="bs-sheet @container flex flex-auto flex-col">
        {switcher}
        {rows.length > 0 ? (
          <table
            aria-label={`${title}, newest first`}
            className="w-full border-collapse tabular-nums"
          >
            <thead>
              <tr className="bs-label text-left">
                <th
                  scope="col"
                  className="py-2 pl-3 pr-1.5 font-semibold @[24rem]:pl-5 @[24rem]:pr-2"
                >
                  Date
                </th>
                <th
                  scope="col"
                  className="px-1.5 py-2 font-semibold @[24rem]:px-2"
                >
                  Opponent
                </th>
                <th
                  scope="col"
                  className="w-11 px-0 py-2 text-center font-semibold @[24rem]:w-[3.75rem]"
                >
                  Result
                </th>
                <th
                  scope="col"
                  className="py-2 pl-1.5 pr-3 text-right font-semibold @[24rem]:pl-2 @[24rem]:pr-5"
                >
                  {tallies ? "Score" : "Method"}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const fight = fightResultOf(row.game);
                return (
                  <tr key={row.game.id} className="border-t border-hair">
                    <th
                      scope="row"
                      className={
                        dateWraps +
                        "py-2 pl-3 pr-1.5 text-left font-medium @[24rem]:pl-5 @[24rem]:pr-2"
                      }
                    >
                      <time dateTime={localDateKey(row.game.start_time)}>
                        {/* "Mar 1" never splits: where the date may wrap,
                            only its year drops under. */}
                        {resultDate(row.game.start_time, now).replace(" ", "\u00A0")}
                      </time>
                    </th>
                    {/* `max-w-0` with full width: the column takes what the
                        others leave, and the name wraps inside it. */}
                    <td className="w-full max-w-0 px-1.5 py-[0.4375rem] @[24rem]:px-2">
                      <span className="flex min-w-0 items-center gap-1.5 @[24rem]:gap-2">
                        <span
                          className={
                            venueShown + "w-[1.125rem] flex-none text-muted"
                          }
                        >
                          {venueWord(sport, row.home)}
                        </span>
                        <SideBlock
                          side={row.opponent}
                          leagueId={row.game.league_id}
                          className={blockShown}
                        />
                        <span
                          className={
                            nameShown +
                            "min-w-0 text-sm leading-tight [overflow-wrap:anywhere] @[24rem]:text-[0.9375rem] @[24rem]:leading-snug"
                          }
                        >
                          {row.opponent.name}
                        </span>
                      </span>
                    </td>
                    <td className="px-0 py-2 text-center">
                      <OutcomeSquare
                        result={row.outcome}
                        size={24}
                        className="align-middle"
                      />
                    </td>
                    {/* A score keeps one line; a method ("Unanimous decision")
                        may take two, or the table would be wider than a
                        narrow sheet. */}
                    <td
                      className={
                        (tallies ? "whitespace-nowrap " : "") +
                        "py-2 pl-1.5 pr-3 text-right @[24rem]:pl-2 @[24rem]:pr-5"
                      }
                    >
                      {tallies ? (
                        <span className="font-condensed text-2xl leading-none">
                          <span className={`inline-block text-right font-bold ${figure}`}>
                            {row.mine ?? ""}
                          </span>
                          <span className="inline-block w-3 text-center text-muted @[24rem]:w-4">
                            –
                          </span>
                          <span
                            className={`inline-block text-left font-medium text-muted ${figure}`}
                          >
                            {row.theirs ?? ""}
                          </span>
                        </span>
                      ) : (
                        <span className="text-sm text-ink-2">
                          {fight === null ? "" : fightMethodLabel(fight.method)}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : season.failed ? (
          <SheetNote onRetry={season.retry}>
            Couldn&rsquo;t load {possessive(team.name)} results.
          </SheetNote>
        ) : season.games === undefined ? (
          <SheetNote status>Loading results…</SheetNote>
        ) : (
          <SheetNote>
            No finished {words.games} on record yet for {team.name}.
          </SheetNote>
        )}

        {/* The totals follow the table at once: in a sheet stretched to a
            taller neighbour the room left over goes under them, as tint. */}
        <div>
          {totals.length > 0 && (
            <dl
              className={
                "m-0 grid border-t-2 border-ink tabular-nums " +
                (totals.length === 3 ? "grid-cols-3" : "grid-cols-1")
              }
            >
              {totals.map((total, index) => (
                <div
                  key={total.label}
                  className={
                    "flex min-w-0 flex-col-reverse justify-end gap-1 py-3 pr-2 " +
                    (index === 0
                      ? "pl-3 @[24rem]:pl-5"
                      : "border-l border-hair pl-3 @[24rem]:pl-4")
                  }
                >
                  <dt className="bs-label">{total.label}</dt>
                  <dd
                    className={
                      `m-0 font-condensed leading-[0.9] ${totalSize(total.value)} ` +
                      (index === 2 ? "font-semibold text-ink-2" : "font-bold")
                    }
                  >
                    {total.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {otherLetters.length > 0 && (
            <div className="border-t-2 border-ink">
              <FormRow
                side={other}
                leagueId={game.league_id}
                letters={otherLetters}
                caption={formCaption(
                  otherLetters.length,
                  isFollowedSide(game, other),
                  league,
                  sport,
                )}
              />
            </div>
          )}
        </div>
        <SpareRoom />
      </div>
    </section>
  );
}

/**
 * "{Team}, last 8". The followed side gets the table; with both sides
 * followed a switch picks whose it is (the favourite team's first, else the
 * home side's). Results are only kept for followed teams, so with neither
 * side followed there is no table to build: both sides' form letters are
 * drawn as squares instead.
 */
export default function LastGames({
  game,
  league,
  teams,
  favoriteId,
  awayForm,
  homeForm,
  seasonLabel,
  labelKnown,
  now,
}: {
  game: Game;
  league: League | undefined;
  /** The followed teams. */
  teams: readonly Team[];
  favoriteId: string | null;
  /** The preview's form letters, newest first. */
  awayForm: readonly string[];
  homeForm: readonly string[];
  /** The live table's season label, which says which season is being played. */
  seasonLabel: string | undefined;
  /** The live table has answered (with a label or without). */
  labelKnown: boolean;
  now: Date;
}) {
  const [chosen, setChosen] = useState<SideKey | undefined>(undefined);
  const focus = focusSide(game, favoriteId, chosen);
  const team =
    focus === null
      ? undefined
      : teams.find((entry) => entry.id === game[focus].team_id);
  const letters: Record<SideKey, Outcome[]> = {
    away: formLetters(awayForm),
    home: formLetters(homeForm),
  };

  if (focus === null || team === undefined) {
    const sides = (["away", "home"] as const).filter(
      (side) => letters[side].length > 0,
    );
    if (sides.length === 0) return null;
    return (
      <section
        aria-label="Recent form"
        className="flex min-w-0 flex-[1_1_21.25rem] flex-col"
      >
        <SectionHead title="Recent form" note="Newest first" />
        {/* Beside the standings the sheet is as tall as that one. The two
            rows keep their own height rather than standing over a blank
            sheet; what is left over is tint, as in the Calendar's Legend.
            -mb-px: the hairline under the last row closes it off from the
            spare room, and tucks out of sight when there is none. */}
        <div className="bs-sheet flex-auto overflow-hidden bg-tint">
          <div className="-mb-px border-b border-hair bg-sheet">
            {sides.map((side) => (
              <FormRow
                key={side}
                side={game[side]}
                leagueId={game.league_id}
                letters={letters[side]}
                caption={formCaption(
                  letters[side].length,
                  isFollowedSide(game, game[side]),
                  league,
                  game.sport,
                )}
              />
            ))}
          </div>
        </div>
      </section>
    );
  }

  return (
    <TeamLast
      // A table per team: the wait for its season list starts afresh.
      key={team.id}
      game={game}
      focus={focus}
      team={team}
      league={league}
      seasonLabel={seasonLabel}
      labelKnown={labelKnown}
      now={now}
      otherLetters={letters[focus === "home" ? "away" : "home"]}
      switcher={
        bothFollowed(game) && (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b-2 border-ink bg-tint px-4 py-3 @[24rem]:px-5">
            <span className="bs-note">
              You follow both {sideNoun(game.sport)}s
            </span>
            <Segmented
              label="Whose results"
              value={focus}
              onChange={setChosen}
              options={(["away", "home"] as const).map((side) => ({
                value: side,
                label: sideWord(game, game[side]),
                ariaLabel: game[side].name,
              }))}
              className="[&>*]:px-3.5"
            />
          </div>
        )
      }
    />
  );
}
