import type { CSSProperties, ReactNode } from "react";

import { FormStrip, SectionHead } from "../../components/bs";
import { sportWords } from "../../lib/labels";
import { ordinal } from "../../lib/periodLabel";
import type { Sport, StandingRow, Standings, Team } from "../../types";
import { recordText } from "../today/model";
import {
  figureEms,
  figureSize,
  formRecord,
  hasSeasonRecord,
  lastGames,
  recordWords,
  standingFigure,
  streakFigure,
  streakWords,
  type FormSummary,
} from "./model";

/** Space between two figures sharing a tile's line, in ems of the figure. */
const PAIR_GAP_EMS = 0.3;

/** Proportional figures, as the mockup sets them (`figureEms` measures these). */
const FIGURE = "font-condensed font-bold leading-[0.85]";
/**
 * The line under a figure sits on the tile's bottom edge, not a fixed
 * distance under the figure: the tiles of a line are equally tall, so
 * their feet share a baseline even when one figure has been shrunk to fit
 * or a caption has wrapped. The mockup's 34px line, under its 14px gap.
 */
const FOOT = "m-0 mt-auto min-h-[3rem] pt-3.5 text-sm leading-tight text-ink-2";

/**
 * One key figure: a small-caps label, the figure, and a line under it. The
 * tile is a size container, so a figure can be sized to the tile it is in.
 */
function Tile({
  label,
  flex,
  children,
}: {
  label: string;
  /**
   * The mockup's grow, shrink and basis for this tile, with the grow factors
   * times ten: the proportions are the same, but a tile alone on its line
   * now fills it (a factor under 1 takes only that fraction of the line,
   * which left the ink behind the strip showing beside the Streak tile).
   */
  flex: string;
  children: ReactNode;
}) {
  return (
    <div
      className="@container flex min-w-0 flex-col bg-sheet px-[1.125rem] py-4"
      style={{ flex }}
    >
      <dt className="font-condensed text-base font-semibold uppercase leading-none tracking-[0.08em] text-ink-2">
        {label}
      </dt>
      {children}
    </div>
  );
}

const sized = (ems: number): CSSProperties => ({ fontSize: figureSize(ems) });

/**
 * Two totals as one bar: what the team scored, solid, against what it gave
 * up, outlined. Told by fill, like a result; never by colour. Decoration:
 * the figures over it are what a screen reader is given.
 */
function TallyBar({ scored, allowed }: { scored: number; allowed: number }) {
  const total = scored + allowed;
  if (total === 0) return null;
  return (
    <span className="flex h-3 gap-0.5">
      {scored > 0 && (
        <span className="bg-ink" style={{ width: `${(scored / total) * 100}%` }} />
      )}
      {allowed > 0 && (
        <span
          className="border-2 border-ink bg-sheet"
          style={{ width: `${(allowed / total) * 100}%` }}
        />
      )}
    </span>
  );
}

/**
 * The strip of key figures over the results: the season record and the
 * place in the table (from standings), and the form, the streak and the
 * scoring over the last few games (from the results on screen). A tile is
 * only drawn when there is a real figure to put in it.
 */
export default function SeasonTiles({
  team,
  sport,
  title,
  note,
  summary,
  standings,
  row,
}: {
  team: Team;
  sport: Sport | undefined;
  /** "Season so far", or "2024 season" for a past one. */
  title: string;
  note: string | undefined;
  summary: FormSummary;
  /** The table the season on screen is ranked in, and the team's row in it. */
  standings: Standings | undefined;
  row: StandingRow | undefined;
}) {
  const words = sportWords(sport);
  const played = summary.form.length;
  const record =
    row !== undefined && standings !== undefined && hasSeasonRecord(standings.sport)
      ? recordText(row, standings.sport)
      : null;
  const place =
    row !== undefined && standings !== undefined
      ? standingFigure(row, standings)
      : null;
  const tally =
    words.tally !== null && summary.scored !== null && summary.allowed !== null
      ? { ...words.tally, scored: summary.scored, allowed: summary.allowed }
      : null;

  if (record === null && place === null && played === 0) return null;

  const form = formRecord(summary, sport);
  const stretch = lastGames(played, words);
  const rankText = place === null ? "" : ordinal(place.rank);
  const rankDigits = place === null ? "" : String(place.rank);

  return (
    <section aria-label={title}>
      <SectionHead title={title} note={note} />
      {/* The ink behind the tiles shows through the 2px gaps: that is what
          draws the heavy rules between them, whichever way they wrap. */}
      <dl className="m-0 flex flex-wrap gap-0.5 border border-t-0 border-rule bg-ink">
        {record !== null && row !== undefined && standings !== undefined && (
          <Tile label="Season record" flex="10 1 15rem">
            <dd className={`m-0 mt-1.5 ${FIGURE}`} style={sized(figureEms(record))}>
              {record}
            </dd>
            <dd className={`${FOOT} flex items-end`}>
              {recordWords(row, standings.sport)}
            </dd>
          </Tile>
        )}

        {played > 0 && (
          <Tile
            label={stretch.charAt(0).toUpperCase() + stretch.slice(1)}
            flex="13 1 21.875rem"
          >
            <dd className="m-0 mt-1.5 flex flex-wrap items-baseline gap-x-3.5">
              <span className={FIGURE} style={sized(figureEms(form))}>
                {form}
              </span>
              <span className="text-sm leading-tight text-ink-2">
                Form, most recent first
              </span>
            </dd>
            <dd className="m-0 mt-auto flex min-h-[3rem] items-end pt-3.5">
              <FormStrip
                size={34}
                results={summary.form}
                label={`${team.name}, ${stretch}, most recent first`}
              />
            </dd>
          </Tile>
        )}

        {summary.streak !== null && (
          <Tile label="Streak" flex="7 1 10rem">
            <dd
              className={`m-0 mt-1.5 ${FIGURE}`}
              style={sized(figureEms(streakFigure(summary.streak)))}
            >
              {streakFigure(summary.streak)}
            </dd>
            <dd className={`${FOOT} flex items-end`}>
              {streakWords(summary.streak, words)}
            </dd>
          </Tile>
        )}

        {tally !== null && (
          <Tile label={`${tally.noun}, ${stretch}`} flex="10 1 16.25rem">
            <dd
              className={`m-0 mt-1.5 flex items-baseline justify-between gap-3 ${FIGURE}`}
              style={sized(
                figureEms(`${tally.scored}${tally.allowed}`) + PAIR_GAP_EMS,
              )}
            >
              {/* The two figures and their labels sit apart on screen; said
                  together they are one sentence. */}
              <span className="sr-only">
                {`${tally.scored} ${tally.forLabel.toLowerCase()}, ${tally.allowed} ${tally.againstLabel.toLowerCase()}`}
              </span>
              <span aria-hidden="true">{tally.scored}</span>
              <span aria-hidden="true" className="font-medium text-muted">
                {tally.allowed}
              </span>
            </dd>
            <dd aria-hidden="true" className={`${FOOT} flex flex-col justify-end gap-1`}>
              <TallyBar scored={tally.scored} allowed={tally.allowed} />
              <span className="flex justify-between gap-3">
                <span>{tally.forLabel}</span>
                <span>{tally.againstLabel}</span>
              </span>
            </dd>
          </Tile>
        )}

        {place !== null && (
          <Tile label={place.label} flex="10 1 16.25rem">
            <dd
              className={`m-0 mt-1.5 flex items-baseline justify-between gap-3 uppercase ${FIGURE}`}
              style={sized(
                // The ordinal's letters are set at 45% of the figure.
                figureEms(rankDigits) +
                  0.45 +
                  (place.second === null
                    ? 0
                    : figureEms(place.second.value) + PAIR_GAP_EMS),
              )}
            >
              <span className="sr-only">
                {`${rankText} in ${place.table}` +
                  (place.second === null
                    ? ""
                    : `, ${place.second.value} ${place.second.label}`)}
              </span>
              <span aria-hidden="true">
                {rankDigits}
                <span className="text-[0.45em]">{rankText.slice(rankDigits.length)}</span>
              </span>
              {place.second !== null && (
                <span aria-hidden="true">{place.second.value}</span>
              )}
            </dd>
            <dd
              aria-hidden="true"
              className={`${FOOT} flex items-end justify-between gap-3`}
            >
              <span>in {place.table}</span>
              {place.second !== null && (
                <span className="text-right">{place.second.label}</span>
              )}
            </dd>
          </Tile>
        )}
      </dl>
    </section>
  );
}
