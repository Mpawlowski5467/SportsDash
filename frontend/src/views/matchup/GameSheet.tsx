import {
  Button,
  FollowMark,
  MarkedName,
  ScoreFigure,
  SectionHead,
  SpotChip,
} from "../../components/bs";
import { boxScoreLabel, isIndividualSport, showsScore } from "../../lib/labels";
import type { CSSProperties } from "react";

import type { Game, Standings, Weather } from "../../types";
import { isFollowedSide } from "../today/model";
import {
  figureColumnRem,
  forecastFact,
  gameStatus,
  sectionNote,
  sectionTitle,
  sideCaption,
  sideFigures,
  sideTone,
  viewerDay,
  type SideFigure,
  type SideKey,
} from "./model";
import { Fact, SideBlock } from "./parts";

/**
 * One side: its block, its name, where it stands in its table, and its
 * score. At the mockup's width that is one line; in a narrower column the
 * block, the name and the score keep the first line and the record and
 * place drop under them.
 */
function TeamRow({
  game,
  sideKey,
  figures,
  widths,
}: {
  game: Game;
  sideKey: SideKey;
  /** The side's record and place; empty when it has no row in a table. */
  figures: readonly SideFigure[];
  /** The width of each figure column in rem, shared by the two rows. */
  widths: readonly number[];
}) {
  const side = game[sideKey];
  const tone = sideTone(game, sideKey);
  const strong = tone !== "trail";
  const followed = isFollowedSide(game, side);
  const caption = sideCaption(game, sideKey);
  const scored = showsScore(game);
  const wideScore = (game.home.score ?? 0) > 99 || (game.away.score ?? 0) > 99;

  return (
    <div
      className={
        "grid flex-1 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-3 px-4 sm:gap-x-4 sm:px-6 @3xl:grid-cols-[auto_minmax(0,1fr)_auto_auto] " +
        (sideKey === "away"
          ? "pb-3.5 pt-4"
          : "border-t border-hair pb-4 pt-3.5")
      }
    >
      <SideBlock
        side={side}
        leagueId={game.league_id}
        size={64}
        className="max-sm:h-11 max-sm:w-11 max-sm:text-lg"
      />
      <div className="min-w-0">
        <div
          className={
            "font-condensed uppercase leading-none [overflow-wrap:anywhere] sm:text-[2.5rem] " +
            // Beside a three-figure score the name steps down on a phone,
            // as the score does, so a long one is not left a letter over.
            (wideScore ? "text-[1.375rem] " : "text-[1.625rem] ") +
            (strong ? "font-bold text-ink" : "font-semibold text-ink-2")
          }
        >
          {/* Tied to the end of the name, so it wraps with the name. */}
          <MarkedName
            name={side.name}
            mark={
              followed && (
                <FollowMark
                  size="lg"
                  label={
                    isIndividualSport(game.sport)
                      ? "Followed player"
                      : "Followed team"
                  }
                  className="ml-2.5 align-middle"
                />
              )
            }
          />
        </div>
        {caption !== "" && (
          <div className="mt-1 text-sm text-muted">{caption}</div>
        )}
      </div>

      {figures.length > 0 && (
        <dl className="col-span-full row-start-2 m-0 flex min-w-0 flex-wrap gap-x-4 gap-y-3 tabular-nums @3xl:col-span-1 @3xl:col-start-3 @3xl:row-start-1">
          {figures.map((figure, index) => (
            <div
              key={figure.label}
              // `justify-end` in a reversed column packs to the top: the
              // figures share a line however many lines a label takes.
              className="flex min-w-0 flex-[1_1_6rem] flex-col-reverse justify-end gap-1 border-l border-hair pl-5 first:border-l-0 first:pl-0 @3xl:w-[var(--figure-w)] @3xl:flex-none @3xl:first:border-l @3xl:first:pl-5"
              style={
                { "--figure-w": `${widths[index] ?? 8}rem` } as CSSProperties
              }
            >
              <dt className="bs-label">{figure.label}</dt>
              <dd
                className={
                  "m-0 whitespace-nowrap font-condensed text-[1.75rem] uppercase leading-none sm:text-[2.5rem] " +
                  (strong ? "font-bold text-ink" : "font-semibold text-ink-2")
                }
              >
                {figure.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {scored && (
        <div
          className={
            "col-start-3 row-start-1 min-w-[2.5rem] text-right font-condensed leading-[0.8] tabular-nums sm:min-w-[5.25rem] @3xl:col-start-4 " +
            // Three figures (basketball) step down a size, or a long team
            // name beside them has nowhere to go.
            (wideScore ? "text-[3rem] sm:text-[5rem] " : "text-[4rem] sm:text-[6rem] ") +
            (strong ? "font-bold text-ink" : "font-semibold text-muted")
          }
        >
          {/* The one figure in the row without a term of its own. */}
          <span className="sr-only">Score </span>
          {/* Pulses when the score moves, and only then: choosing another
              game is not a change of score. */}
          <ScoreFigure of={game.id} value={side.score} />
        </div>
      )}
    </div>
  );
}

/**
 * The game on screen: both sides with their record, place and score, a
 * block saying where the game stands (live, still to start, over, called
 * off), and a strip of facts: where it is played, where to watch it, and
 * the forecast when the preview has one.
 */
export default function GameSheet({
  game,
  leagueName,
  standings,
  weather,
  now,
  onOpen,
}: {
  game: Game;
  leagueName: string | undefined;
  standings: Standings | undefined;
  /** The forecast, which exists only for an outdoor game still to start. */
  weather: Weather | null;
  now: Date;
  onOpen: (game: Game) => void;
}) {
  const title = sectionTitle(game, viewerDay(now));
  const status = gameStatus(game, now);
  // Before a game starts the box score has nothing this page does not
  // already show, so the button waits for the game.
  const hasBoxScore = game.phase === "in_progress" || game.phase === "final";
  const forecast = weather === null ? null : forecastFact(weather);
  const figures = {
    away: sideFigures(game.away, standings),
    home: sideFigures(game.home, standings),
  };
  // One width per column for both rows, wide enough for the longer figure.
  const widths = [0, 1].map((index) =>
    figureColumnRem(
      [figures.away[index]?.value, figures.home[index]?.value].filter(
        (value): value is string => value !== undefined,
      ),
    ),
  );

  return (
    <section aria-label={title}>
      <SectionHead title={title} note={sectionNote(game, leagueName)} />
      <div className="bs-sheet">
        <div className="flex flex-wrap items-stretch">
          <div className="@container flex min-w-0 flex-[999_1_42.5rem] flex-col">
            {(["away", "home"] as const).map((sideKey) => (
              <TeamRow
                key={sideKey}
                game={game}
                sideKey={sideKey}
                figures={figures[sideKey]}
                widths={widths}
              />
            ))}
          </div>

          <div className="flex min-w-0 flex-[1_1_22.5rem] flex-col justify-between gap-[1.125rem] bg-tint px-4 py-5 sm:px-6">
            <div>
              {status.live ? (
                <SpotChip size="lg">Live</SpotChip>
              ) : (
                status.eyebrow !== null && (
                  <div className="font-condensed text-xl font-bold uppercase leading-[1.2] tracking-[0.08em] text-ink-2">
                    {status.eyebrow}
                  </div>
                )
              )}
              <div className="mt-2.5 font-condensed text-[2.75rem] font-bold uppercase leading-[0.9] sm:text-[3.5rem]">
                {status.headline}
                {status.detail !== null && (
                  <span className="ml-3 font-semibold tabular-nums text-ink-2">
                    {status.detail}
                  </span>
                )}
              </div>
              {status.sentence !== null && (
                <div className="mt-2 font-medium text-ink-2">
                  {status.sentence}
                </div>
              )}
            </div>
            {hasBoxScore && (
              <div className="flex flex-wrap gap-2.5">
                <Button
                  variant="solid"
                  aria-haspopup="dialog"
                  aria-label={boxScoreLabel(game)}
                  onClick={() => onOpen(game)}
                  className="px-3.5"
                >
                  Box score
                </Button>
              </div>
            )}
          </div>
        </div>

        {(game.venue || game.broadcasts.length > 0 || forecast !== null) && (
          // The hairlines between the cells are the strip's own background
          // showing through 1px gaps, whichever way the cells wrap.
          <dl className="m-0 flex flex-wrap gap-px border-t-2 border-ink bg-hair">
            {game.venue && (
              <Fact label="Venue" className="flex-[2_1_20rem]">
                {game.venue}
              </Fact>
            )}
            {game.broadcasts.length > 0 && (
              <Fact label="Watch" className="flex-[1_1_12.5rem]">
                {game.broadcasts.join(" · ")}
              </Fact>
            )}
            {forecast !== null && (
              // As wide as the venue: the forecast is a line of its own.
              // "Forecast", the word the Calendar's cards use for the
              // same figures, and not "Game-day forecast": the server's
              // day is the UTC date of the start, which for an evening
              // game in the Americas is mostly the day after at the venue.
              <Fact label="Forecast" className="flex-[2_1_20rem]">
                {forecast}
              </Fact>
            )}
          </dl>
        )}
      </div>
    </section>
  );
}
