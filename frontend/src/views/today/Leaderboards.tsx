import { useId } from "react";

import { FollowMark, SectionHead, SpotChip } from "../../components/bs";
import type { LeaderRow, SportEvent } from "../../types";

/**
 * A golf tournament or a race in progress today: its name and round, the
 * top three, and any followed athlete outside the top three. The whole
 * block is the button for the full leaderboard, as the old card was.
 *
 * Followedness comes from the backend contract: a non-null `player_id` IS a
 * followed athlete (the scheduler rewrites the row's id to the followed
 * team's id, else null).
 */
function EventBlock({ event, onOpen }: { event: SportEvent; onOpen: () => void }) {
  const top = event.leaderboard.slice(0, 3);
  const shown = new Set(top.map((row) => row.player_id));
  const followedBelow = event.leaderboard.filter(
    (row) => row.player_id !== null && !shown.has(row.player_id),
  );
  const live = event.phase === "in_progress";
  const round = event.round_label || (event.phase === "final" ? "Final" : "Upcoming");
  const describedBy = useId();
  // Golf's detail is a hole ("F", "16"); racing's is a line of text
  // ("Winner · Corvane Works · #7 · Grid 2"). One layout per board: a long
  // detail anywhere puts every row's detail on its own line under the name.
  const stacked = event.leaderboard.some((row) => (row.detail ?? "").length > 8);

  const line = (row: LeaderRow, key: string) => {
    const followed = row.player_id !== null;
    return (
      <span
        key={key}
        className={
          "grid items-center gap-x-2.5 border-t border-hair py-2 " +
          (stacked
            ? "grid-cols-[2.25rem_minmax(0,1fr)_auto]"
            : "grid-cols-[2.25rem_minmax(0,1fr)_auto_3.5rem]")
        }
      >
        <span className="font-condensed text-lg font-bold leading-none text-muted">
          {row.position_label || row.position}
        </span>
        <span
          className={
            "flex min-w-0 items-center gap-2 " +
            (followed ? "font-bold text-ink" : "text-ink-2")
          }
        >
          <span className="truncate">{row.name}</span>
          {followed && <FollowMark label="Followed" />}
        </span>
        {/* The sign carries the meaning; under par is not coloured. */}
        <span className="font-condensed text-[1.375rem] font-bold leading-none">
          {row.score.trim() || "—"}
        </span>
        {stacked ? (
          row.detail !== null &&
          row.detail !== "" && (
            <span className="col-span-2 col-start-2 text-sm text-muted">
              {row.detail}
            </span>
          )
        ) : (
          <span className="text-right text-sm text-muted">{row.detail ?? ""}</span>
        )}
      </span>
    );
  };

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      // The name says where the button leads; the description is what the
      // block shows (round, the top three, followed athletes), which a name
      // alone would hide from a screen reader.
      aria-label={`Open ${event.name} leaderboard`}
      aria-describedby={describedBy}
      className="bs-focus-inset block w-full cursor-pointer border-t-2 border-ink px-4 pb-2 pt-3 text-left tabular-nums first:border-t-0 hover:bg-tint md:px-6"
    >
      <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pb-2">
        <span className="min-w-0 font-condensed text-[1.375rem] font-bold uppercase leading-none">
          {event.name}
        </span>
        {live ? (
          <span className="flex items-center gap-2">
            <SpotChip>Live</SpotChip>
            <span className="bs-label text-ink">{round}</span>
          </span>
        ) : (
          <span className="bs-label">{round}</span>
        )}
      </span>
      <span id={describedBy} className="block">
        <span className="sr-only">
          {live ? "Live, " : ""}
          {round}.
        </span>
        {top.length === 0 ? (
          <span className="block border-t border-hair py-2 text-muted">
            Leaderboard not available yet.
          </span>
        ) : (
          top.map((row, index) => line(row, `top-${index}`))
        )}
        {followedBelow.map((row, index) => line(row, `followed-${index}`))}
      </span>
    </button>
  );
}

/** Today's leaderboard events (golf, racing), below the games. */
export default function Leaderboards({
  events,
  onOpen,
}: {
  events: readonly SportEvent[];
  onOpen: (eventId: string) => void;
}) {
  return (
    <section aria-label="Leaderboards">
      <SectionHead
        title="Leaderboards"
        note="The top three, and anyone you follow below them"
      />
      <div className="bs-sheet">
        {events.map((event) => (
          <EventBlock key={event.id} event={event} onOpen={() => onOpen(event.id)} />
        ))}
      </div>
    </section>
  );
}
