import { SectionHead, TeamBlock } from "../../components/bs";
import { countWord } from "../../lib/labels";
import type { League, NewsItem } from "../../types";
import {
  dateLine,
  dateParts,
  headlineOf,
  inCapitals,
  markFor,
  markLabel,
  quietRows,
  rowLabel,
  sourceLabel,
  type FilterEntry,
  type ScopeCount,
} from "./model";
import { DateCell, HeadlineText, SourceArrow } from "./parts";

/**
 * One story. Two controls share the row: the row itself opens the reader,
 * and the arrow box goes straight to where the story was published. They
 * are siblings, the arrow laid over the room the row leaves for it, so
 * each is a control of its own for a pointer and for the Tab key.
 *
 * Wide, the row is the mockup's: the date, the team's block, then a line
 * of who and where over the headline. Narrow, those would wrap into three
 * rows with the arrow alone under them, so the row is set again: the block
 * and who it is for beside the arrow, the headline the full width under
 * them, the date and the outlet last.
 */
function StoryRow({
  story,
  entries,
  leagues,
  onOpen,
}: {
  story: NewsItem;
  entries: readonly FilterEntry[];
  leagues: readonly League[];
  onOpen: (story: NewsItem) => void;
}) {
  const mark = markFor(story, entries, leagues);
  const label = markLabel(mark);
  const headline = headlineOf(story);
  const date = dateParts(story.published_at);
  return (
    <li className="sd-stagger-item relative border-t border-hair first:border-t-0">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={rowLabel(headline, mark, story.source, date)}
        onClick={() => onOpen(story)}
        className="bs-focus-inset grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2.5 gap-y-1.5 px-4 pb-3.5 pt-3 text-left hover:bg-tint @xl:grid-cols-[3.5rem_3.75rem_minmax(0,1fr)] @xl:gap-x-5 @xl:gap-y-0 @xl:py-3.5 @xl:pl-6 @xl:pr-[5.5rem]"
      >
        <span className="hidden @xl:block">
          <DateCell date={date} />
        </span>
        <TeamBlock
          name={mark.name}
          abbreviation={mark.abbreviation}
          color={mark.color}
          className="@xl:hidden"
        />
        <TeamBlock
          name={mark.name}
          abbreviation={mark.abbreviation}
          color={mark.color}
          size={60}
          className="hidden @xl:inline-flex"
        />
        {/* Narrow: who the story is for, on the arrow's line. */}
        <span className="flex min-h-11 min-w-0 items-center pr-14 text-sm font-semibold leading-tight text-ink-2 @xl:hidden">
          <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
        </span>
        <span className="col-span-full flex min-w-0 flex-col @xl:col-span-1">
          <span className="hidden min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-sm @xl:flex">
            {label !== "" && (
              <span className="min-w-0 font-semibold text-ink-2 [overflow-wrap:anywhere]">
                {label}
              </span>
            )}
            <span className="min-w-0 font-medium text-muted [overflow-wrap:anywhere]">
              {/* `bdi`: an outlet named in a right-to-left script must not
                  reorder the time set after it. */}
              <bdi>{story.source}</bdi>
              {date !== null && ` · ${date.time}`}
            </span>
          </span>
          <span
            dir="auto"
            className={
              "font-condensed text-[1.375rem] font-bold leading-[1.05] tracking-[0.01em] text-ink [overflow-wrap:anywhere] @xl:mt-1.5 @xl:text-[1.625rem]" +
              (inCapitals(headline) ? " uppercase" : "")
            }
          >
            <HeadlineText text={headline} />
          </span>
          {/* Narrow: when and where, under the headline. */}
          <span className="mt-1.5 text-sm font-medium text-muted [overflow-wrap:anywhere] @xl:hidden">
            {date !== null && (
              <>
                <time dateTime={date.key} title={date.full}>
                  {dateLine(date)}
                </time>
                {" · "}
              </>
            )}
            <bdi>{story.source}</bdi>
          </span>
        </span>
      </button>
      <a
        href={story.url}
        target="_blank"
        rel="noreferrer"
        aria-label={sourceLabel(headline, story.source)}
        className="bs-btn bs-btn--icon absolute right-4 top-3 @xl:right-6 @xl:top-1/2 @xl:-translate-y-1/2"
      >
        <SourceArrow size={20} />
      </a>
    </li>
  );
}

/** A follow with no stories: the mockup's tinted row under the list. */
function QuietRow({ entry, line }: { entry: FilterEntry; line: string }) {
  return (
    <p className="m-0 flex items-center gap-x-2.5 border-t border-hair bg-tint px-4 py-3.5 font-medium text-ink-2 first:border-t-0 @xl:gap-x-5 @xl:px-6">
      <span
        aria-hidden="true"
        className="hidden w-14 flex-none font-condensed text-[1.375rem] font-bold leading-none text-muted @xl:block"
      >
        —
      </span>
      <TeamBlock
        name={entry.name}
        abbreviation={entry.abbreviation}
        color={entry.color}
        className="@xl:hidden"
      />
      <TeamBlock
        name={entry.name}
        abbreviation={entry.abbreviation}
        color={entry.color}
        size={60}
        className="hidden h-8 text-lg @xl:inline-flex"
      />
      <span className="min-w-0 [overflow-wrap:anywhere]">{line}</span>
    </p>
  );
}

/**
 * Many follows with no stories: one row naming them all. Only the plain
 * ones; a follow whose line needs a second sentence has a row of its own.
 */
function QuietTogether({ quiet }: { quiet: readonly FilterEntry[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5 border-t border-hair bg-tint px-4 py-3.5 font-medium text-ink-2 first:border-t-0 @xl:px-6">
      <span
        aria-hidden="true"
        className="hidden w-14 flex-none font-condensed text-[1.375rem] font-bold leading-none text-muted @xl:block"
      >
        —
      </span>
      <p className="m-0">No stories for {countWord(quiet.length)} follows:</p>
      <ul className="m-0 flex min-w-0 list-none flex-wrap gap-x-4 gap-y-2 p-0">
        {quiet.map((entry) => (
          <li key={entry.key} className="flex min-w-0 items-center gap-2">
            <TeamBlock
              name={entry.name}
              abbreviation={entry.abbreviation}
              color={entry.color}
            />
            <span className="min-w-0 [overflow-wrap:anywhere]">{entry.name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Every story under the top one, newest first, and under them the follows
 * that have none. The quiet follows are listed only on the unfiltered page
 * (the caller passes none otherwise), and only those whose own feed came
 * back empty. `counts` is what each filter lists: what a quiet person's
 * row may say depends on whether a league-mate holds any stories.
 */
export default function StoryList({
  stories,
  entries,
  leagues,
  quiet,
  counts,
  note,
  busy,
  onOpen,
}: {
  stories: readonly NewsItem[];
  entries: readonly FilterEntry[];
  leagues: readonly League[];
  quiet: readonly FilterEntry[];
  counts: ReadonlyMap<string, ScopeCount | null>;
  note: string;
  /** The list on screen belongs to the previous filter; the next is loading. */
  busy: boolean;
  onOpen: (story: NewsItem) => void;
}) {
  if (stories.length === 0 && quiet.length === 0) return null;
  const rows = quietRows(quiet, entries, leagues, counts);
  return (
    <section aria-label="More stories" aria-busy={busy || undefined}>
      <SectionHead
        title="More stories"
        note={note}
        aside={
          stories.length > 0 ? (
            // Left out on a phone, where it would be a third line of head
            // over every list: the row and the arrow each say what they do.
            <span className="bs-note text-muted max-sm:hidden">
              A story opens in the reader · the arrow goes to its source
            </span>
          ) : undefined
        }
      />
      <div className="bs-sheet @container">
        {stories.length > 0 && (
          <ul className="m-0 list-none p-0">
            {stories.map((story) => (
              <StoryRow
                key={story.id}
                story={story}
                entries={entries}
                leagues={leagues}
                onOpen={onOpen}
              />
            ))}
          </ul>
        )}
        {rows.together.length > 0 && <QuietTogether quiet={rows.together} />}
        {rows.alone.map(({ entry, line }) => (
          <QuietRow key={entry.key} entry={entry} line={line} />
        ))}
      </div>
    </section>
  );
}
