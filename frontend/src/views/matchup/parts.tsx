import { createContext, useContext, type ReactNode } from "react";

import { TeamBlock, type TeamBlockSize } from "../../components/bs";
import type { GameSide } from "../../types";

// ── Team identity ───────────────────────────────────────────────────────

/** What a side is drawn with: its colour and its code, either may be missing. */
export interface SideIdentity {
  color: string | null;
  abbreviation: string | null;
}

type SideLike = Pick<GameSide, "team_id" | "name" | "abbreviation" | "color">;

/**
 * Finds a side's colour and code. A game that came through a followed
 * team's own schedule (and every past meeting and result) carries no
 * colour on either side, so MatchupView joins what the other payloads
 * know: the followed teams, the league's setup catalog, its standings
 * rows, and the colours seen on any loaded game. The league is asked for
 * because two leagues can each have a team of the same name.
 */
export type Identify = (side: SideLike, leagueId: string) => SideIdentity;

const asSent: Identify = (side) => ({
  color: side.color,
  abbreviation: side.abbreviation,
});

/** Provided once by MatchupView; outside it a side is drawn as it was sent. */
export const IdentityContext = createContext<Identify>(asSent);

/** The finder itself, for a caller with a list of sides to resolve. */
export function useIdentify(): Identify {
  return useContext(IdentityContext);
}

export function useIdentity(side: SideLike, leagueId: string): SideIdentity {
  return useContext(IdentityContext)(side, leagueId);
}

/** A side's colour block, in the colour and code the view could find for it. */
export function SideBlock({
  side,
  leagueId,
  size,
  labelled,
  className,
}: {
  side: SideLike;
  leagueId: string;
  size?: TeamBlockSize;
  labelled?: boolean;
  className?: string;
}) {
  const identity = useIdentity(side, leagueId);
  return (
    <TeamBlock
      name={side.name}
      abbreviation={identity.abbreviation}
      color={identity.color}
      size={size}
      labelled={labelled}
      className={className}
    />
  );
}

// ── Small pieces ────────────────────────────────────────────────────────

/** A line in place of a list: loading, empty, or a failed request. */
export function SheetNote({
  children,
  onRetry,
  status = false,
  className,
}: {
  children: ReactNode;
  /** Makes the line an alert with a Retry beside it. */
  onRetry?: () => void;
  /** The line reports something still on its way. */
  status?: boolean;
  className?: string;
}) {
  return (
    <p
      role={onRetry !== undefined ? "alert" : status ? "status" : undefined}
      className={
        "m-0 flex flex-wrap items-center justify-between gap-x-3 px-5 py-4 text-sm text-ink-2" +
        (className ? ` ${className}` : "")
      }
    >
      {children}
      {onRetry !== undefined && (
        <button
          type="button"
          onClick={onRetry}
          // The word alone is 34px wide; the target is 44px either way.
          className="bs-link min-w-11 cursor-pointer justify-center"
        >
          Retry
        </button>
      )}
    </p>
  );
}

/**
 * The last thing in a sheet that shares a row with taller ones. The sheets
 * of a row are stretched to one height so their edges line up; the content
 * of a shorter one keeps the height it is drawn at, and what is left over
 * is this: tint under a hairline, as in the Calendar's Legend. Content is
 * never spread out or centred to fill a stretched sheet. With no room to
 * spare it has no height and draws nothing (the hairline is an inset
 * shadow, not a border, which would be a pixel of its own). The sheet must
 * be a flex column.
 */
export function SpareRoom() {
  return (
    <div
      aria-hidden="true"
      className="flex-auto bg-tint shadow-[inset_0_1px_0_var(--bs-hair)]"
    />
  );
}

/** The tinted band over a list inside a sheet: its name left, a summary right. */
export function SubHead({
  title,
  summary,
  rule = "top",
}: {
  title: ReactNode;
  summary?: string;
  /** Which edge carries the heavy rule: under it when it heads a sheet. */
  rule?: "top" | "bottom";
}) {
  return (
    <div
      className={
        "flex flex-wrap items-center justify-between gap-x-3 gap-y-1 bg-tint px-5 py-2 " +
        (rule === "top" ? "border-t-2 border-ink" : "border-b-2 border-ink")
      }
    >
      <h3 className="m-0 flex min-w-0 items-center gap-2.5 font-condensed text-base font-bold uppercase leading-5 tracking-[0.08em]">
        {title}
      </h3>
      {summary !== undefined && summary !== "" && (
        <span className="text-sm leading-5 text-ink-2">{summary}</span>
      )}
    </div>
  );
}

/** "Box score" as the underlined link the mockup draws, opening the game. */
export function BoxScoreLink({
  label,
  onOpen,
  className,
}: {
  /** The whole story: "Box score, Monday, July 27". */
  label: string;
  onOpen: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      aria-label={label}
      onClick={onOpen}
      className={
        "bs-link cursor-pointer whitespace-nowrap" +
        (className ? ` ${className}` : "")
      }
    >
      Box score
    </button>
  );
}

/** One cell of a key/value strip (the facts under a game, the betting lines). */
export function Fact({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={
        "flex min-w-0 flex-wrap items-baseline gap-x-3.5 gap-y-0.5 bg-sheet px-4 py-3 sm:px-6" +
        (className ? ` ${className}` : "")
      }
    >
      <dt className="bs-label">{label}</dt>
      <dd className="m-0 min-w-0 font-semibold [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  );
}
