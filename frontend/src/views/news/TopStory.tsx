import { useState, type CSSProperties, type ReactNode } from "react";

import { SectionHead, TeamBlock, teamCode } from "../../components/bs";
import type { NewsItem } from "../../types";
import {
  ageText,
  dateParts,
  deckOf,
  headlineOf,
  heroCodeSize,
  heroHeadlineRem,
  inCapitals,
  markLabel,
  type StoryMark,
} from "./model";
import { HeadlineText, SourceArrow } from "./parts";

/**
 * The photo slot: the story's own picture, cut to fill it. Most stories
 * come without one (only the provider's own articles carry an image), and
 * a hot-linked one can fail, so the slot's other state is the mockup's: a
 * block of the team's colour carrying its code, as large as the slot
 * allows. A competition or an athlete has no colour and gets the neutral
 * block. Decorative either way: the label and headline beside it say
 * everything it does.
 *
 * Keyed by story at the call site, so a failed picture is not remembered
 * against the next story.
 */
function Photo({ story, mark }: { story: NewsItem; mark: StoryMark }) {
  const [failed, setFailed] = useState(false);
  const pictured = Boolean(story.image_url) && !failed;
  const code = teamCode(mark.name, mark.abbreviation);
  return (
    <div
      // Stacked over the story (a phone, a narrow window) the slot is a
      // 16:9 strip no taller than the mockup's; beside the story it is the
      // mockup's 340px, or the story's height where that is more.
      className="relative aspect-video max-h-[21.25rem] w-full min-w-0 overflow-hidden @container @[58.75rem]:aspect-auto @[58.75rem]:max-h-none @[58.75rem]:min-h-[21.25rem]"
      style={{ "--news-code": heroCodeSize(code) } as CSSProperties}
    >
      {pictured ? (
        <img
          src={story.image_url ?? undefined}
          alt=""
          className="absolute inset-0 h-full w-full bg-tint object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <TeamBlock
          name={mark.name}
          abbreviation={mark.abbreviation}
          color={mark.color}
          className="absolute inset-0 h-full w-full items-end justify-start px-[1.375rem] pb-[1.375rem] text-[length:var(--news-code)] leading-[0.78] tracking-[-0.01em]"
        />
      )}
    </div>
  );
}

/**
 * The newest story, large: its picture or its team's block, who it is for,
 * the headline, the summary where there is a real one, and the way out to
 * where it was published.
 */
export default function TopStory({
  story,
  mark,
  note,
  aside,
  busy,
}: {
  story: NewsItem;
  mark: StoryMark;
  /** What this is the newest of. */
  note: string;
  /** The note at the far end of the head. */
  aside: ReactNode;
  /** The list on screen belongs to the previous filter; the next is loading. */
  busy: boolean;
}) {
  const headline = headlineOf(story);
  const deck = deckOf(story);
  const date = dateParts(story.published_at);
  const label = markLabel(mark);
  return (
    <section aria-label="Top story" aria-busy={busy || undefined}>
      <SectionHead
        title="Top story"
        note={note}
        aside={<span className="bs-note block text-muted max-sm:pb-1.5">{aside}</span>}
        // The far note drops under the tab on a phone; there it lines up
        // with the tab instead of hanging off the right edge alone.
        className="max-sm:[&>:last-child]:ml-0"
      />
      <article className="bs-sheet @container">
        <div className="grid @[58.75rem]:grid-cols-[28.75rem_minmax(0,1fr)]">
          <Photo key={story.id} story={story} mark={mark} />
          <div className="flex min-w-0 flex-col">
            <div className="flex flex-1 flex-col gap-3.5 px-4 pb-5 pt-5 @xl:gap-[1.125rem] @xl:px-8 @xl:pb-6 @xl:pt-7">
              {label !== "" && (
                <p className="m-0 font-condensed text-lg font-semibold uppercase leading-none tracking-[0.04em] text-ink-2 [overflow-wrap:anywhere] @xl:text-[1.375rem]">
                  {label}
                </p>
              )}
              <h3
                // `dir`: a headline in a right-to-left script reads from
                // its own side.
                dir="auto"
                className={
                  "m-0 font-condensed font-bold text-balance [overflow-wrap:anywhere] " +
                  (inCapitals(headline) ? "uppercase leading-[0.96]" : "leading-none")
                }
                style={{
                  fontSize: `clamp(1.75rem, 8cqi, ${heroHeadlineRem(headline)}rem)`,
                }}
              >
                <HeadlineText text={headline} />
              </h3>
              {deck !== null && (
                <p
                  dir="auto"
                  className="m-0 max-w-[46rem] text-[1.0625rem] leading-snug text-ink-2 [overflow-wrap:anywhere]"
                >
                  {deck}
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t-2 border-ink px-4 py-4 @xl:px-8">
              <p className="m-0 min-w-0 text-[1.0625rem] text-ink-2 [overflow-wrap:anywhere]">
                {/* `bdi`: an outlet named in a right-to-left script must
                    not reorder the age set after it. */}
                <bdi className="font-semibold text-ink">{story.source}</bdi>
                {date !== null && story.published_at !== null && (
                  <>
                    {" · "}
                    <time dateTime={date.key} title={date.full}>
                      {ageText(story.published_at, date)}
                    </time>
                  </>
                )}
              </p>
              <a
                href={story.url}
                target="_blank"
                rel="noreferrer"
                aria-label="Read at source, in a new tab"
                className="bs-btn bs-btn--solid gap-2 pl-5 pr-4"
              >
                Read at source
                <SourceArrow />
              </a>
            </div>
          </div>
        </div>
      </article>
    </section>
  );
}
