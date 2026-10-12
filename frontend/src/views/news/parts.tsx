import { Icon } from "../../components/bs";
import { headlineParts, type DateParts } from "./model";

/** A headline's words, with each score held on one line. */
export function HeadlineText({ text }: { text: string }) {
  return (
    <>
      {headlineParts(text).map((part, index) =>
        part.keep ? (
          <span key={index} className="whitespace-nowrap">
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

/** The arrow that means "this leaves the app". */
export function SourceArrow({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M7 17L17 7" />
      <path d="M8 7h9v9" />
    </Icon>
  );
}

/**
 * A story's date as the list sets it: the month over the day, and under
 * them the year when it is not this one (last July must not read as this
 * July). A story with no date gets a dash; the full date and time are the
 * tooltip.
 */
export function DateCell({ date }: { date: DateParts | null }) {
  if (date === null) {
    return (
      <span
        aria-hidden="true"
        className="font-condensed text-[1.375rem] font-bold leading-none text-muted"
      >
        —
      </span>
    );
  }
  return (
    <time
      dateTime={date.key}
      title={date.full}
      className="flex flex-col gap-[0.1875rem] font-condensed uppercase"
    >
      <span className="text-base font-semibold leading-none tracking-[0.08em] text-muted">
        {date.month}
      </span>
      <span className="text-[2.5rem] font-bold leading-[0.9] tabular-nums text-ink-2">
        {date.day}
      </span>
      {date.year !== null && (
        <span className="text-base font-semibold leading-none tracking-[0.08em] text-muted">
          {date.year}
        </span>
      )}
    </time>
  );
}
