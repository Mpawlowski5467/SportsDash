import {
  FollowMark,
  FormStrip,
  MarkedName,
  SectionHead,
  TeamBlock,
} from "../../components/bs";
import type { LegendRow } from "./model";

/**
 * What is followed, and what each has on in the range: a team's games and
 * its results so far, a golfer's or driver's place on a board, a whole
 * competition's games. A row with nothing on is set back on the tint.
 */
export default function Legend({
  rows,
  resultsLabel,
}: {
  rows: readonly LegendRow[];
  /** The lead-in the result strips are read out with: "Results so far this week". */
  resultsLabel: string;
}) {
  if (rows.length === 0) return null;
  const teamsOnly = rows.every((row) => row.kind === "team");
  return (
    <section
      aria-label="Legend"
      className="flex min-w-0 flex-[1_1_22.5rem] flex-col"
    >
      <SectionHead
        title="Legend"
        aside={
          <span className="bs-note">
            {teamsOnly ? "Teams you follow" : "What you follow"}
          </span>
        }
      />
      {/* Beside "Coming up" the sheet is as tall as that one. The rows keep
          the height they are drawn at rather than stretching to fill it
          (two rows beside five would be mostly air); what is left over is
          tint, as under the last card of a short day. */}
      <div className="bs-sheet flex-1 overflow-hidden bg-tint">
        {/* -mb-px: the hairline under the last row closes it off from the
            spare room, and tucks out of sight when there is none. */}
        <ul className="m-0 -mb-px list-none border-b border-hair p-0">
          {rows.map((row) => (
            <li
              key={row.key}
              className={
                "flex min-h-[4.5625rem] items-center gap-3 border-t border-hair px-4 py-3 first:border-t-0 " +
                (row.quiet ? "bg-tint" : "bg-sheet")
              }
            >
              <TeamBlock
                name={row.name}
                abbreviation={row.abbreviation}
                color={row.color}
              />
              {/* The name and the strip share a line while there is room; a
                  long strip (a full week, a month) drops under the name
                  rather than squeezing it. */}
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-2">
                <span className="flex min-w-0 flex-[1_1_8rem] flex-col gap-px">
                  <span className="font-bold text-ink [overflow-wrap:anywhere]">
                    <MarkedName
                      name={row.name}
                      mark={
                        row.marked && (
                          <FollowMark
                            label={row.kind === "athlete" ? "Followed player" : "Followed team"}
                            className="relative -top-px ml-2 align-middle"
                          />
                        )
                      }
                    />
                  </span>
                  <span className="text-sm text-ink-2">{row.line}</span>
                </span>
                {row.results.length > 0 && (
                  <FormStrip
                    results={row.results}
                    label={resultsLabel}
                    className="max-w-full flex-none"
                  />
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
