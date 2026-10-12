import type { ReactNode } from "react";

import { splitLastGlyph } from "../../lib/labels";

/**
 * A name followed by a marker (the followed square), with the marker tied
 * to the name's last character so the two wrap together: however the name
 * breaks, even inside a word too long for its column, the marker stays on
 * the end of the name it marks and never drops onto a line of its own.
 *
 * Without a marker it is just the name, so a caller can pass
 * `followed && <FollowMark />`.
 *
 * For a name that wraps. A name held to one line with an ellipsis keeps
 * its marker outside the truncated text instead (see Today's `SideName`),
 * or the ellipsis would swallow it.
 */
export default function MarkedName({
  name,
  mark,
}: {
  name: string;
  /** The marker, with its own spacing: `<FollowMark className="ml-2" />`. */
  mark?: ReactNode;
}) {
  if (mark === undefined || mark === null || mark === false) return <>{name}</>;
  const { head, last } = splitLastGlyph(name);
  return (
    <>
      {head}
      <span className="whitespace-nowrap">
        {last}
        {mark}
      </span>
    </>
  );
}
