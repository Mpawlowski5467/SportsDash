import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, Loading, Notice, RefreshIcon } from "../components/bs";
import { useManageTeams } from "../components/ManageTeamsContext";
import { useIsActiveTab } from "../components/NavigateContext";
import NewsDetailPanel, { type NewsSource } from "../components/NewsDetailPanel";
import StaleBanner from "../components/StaleBanner";
import {
  NEWS_REFETCH_MS,
  useNewsFeeds,
  useRefreshNews,
  useTeams,
} from "../hooks";
import { errorText } from "../lib/errorText";
import { formatTime, localKeyFromDate } from "../lib/time";
import { useNow } from "../lib/useNow";
import type { NewsItem } from "../types";
import ScopeFilter from "./news/ScopeFilter";
import StoryList from "./news/StoryList";
import TopStory from "./news/TopStory";
import {
  ALL_KEY,
  FEED_REQUEST,
  feedOf,
  feedOverdue,
  filterEntries,
  filterKind,
  markFor,
  moreNote,
  quietCaveat,
  quietEntries,
  quietHeadline,
  refreshFailedLine,
  refreshLineSpent,
  refreshedLine,
  scopeCount,
  topNote,
  type Feed,
  type FilterEntry,
  type ScopeCount,
} from "./news/model";

/** How often the "37m ago" beside the top story is worked out again. */
const AGE_TICK_MS = 60_000;

/**
 * What the last press of Refresh came to: which filter it was a refresh
 * of, and the viewer's day it finished on (the line gives a time of day,
 * which means nothing once the day has changed). `seq` counts results, so
 * a second failure is a new alert and is read out again.
 */
type RefreshNote = { seq: number; scope: FilterEntry; day: string } & (
  | { kind: "done"; at: string; inserted: number }
  | { kind: "failed"; detail: string }
);

/** The page's name, beside the controls that choose what is on it. */
function PageHead({ children }: { children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
        <h1 className="bs-title">News</h1>
      </div>
      {children}
    </header>
  );
}

/** The shapes of the page while a payload is on its way. */
function PageLoading({ label }: { label: string }) {
  return (
    <Loading label={label} className="flex flex-col gap-7">
      <div>
        <div className="border-t-[3px] border-rule">
          <div className="h-[2.125rem] w-28 bg-hair" />
        </div>
        <div className="h-64 border border-t-0 border-rule bg-tint sm:h-[21.25rem]" />
      </div>
      <div>
        <div className="border-t-[3px] border-rule">
          <div className="h-[2.125rem] w-36 bg-hair" />
        </div>
        <div className="border border-t-0 border-rule bg-tint">
          {Array.from({ length: 5 }, (_, index) => (
            <div
              key={index}
              className="h-[5.5rem] border-t border-hair first:border-t-0"
            />
          ))}
        </div>
      </div>
    </Loading>
  );
}

/**
 * Refresh: asks the server to collect new stories now, for the filter on
 * screen. While it runs the button says so and its icon turns (only where
 * motion is welcome); it stays focusable, so a keyboard user is not dropped
 * to the top of the page when it finishes.
 */
function RefreshButton({
  busy,
  tall,
  onClick,
}: {
  busy: boolean;
  /** Level with the row of segments (48px with its frame) beside it. */
  tall: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      aria-label={busy ? "Refreshing news" : "Refresh news"}
      aria-disabled={busy || undefined}
      onClick={busy ? undefined : onClick}
      className={
        // Wide enough for "Refreshing", so the button does not grow when
        // pressed and push the filter beside it sideways.
        "min-w-[9.25rem] flex-none gap-2 pl-3.5 pr-[1.125rem]" +
        (tall ? " min-h-12" : "") +
        (busy ? " cursor-progress" : "")
      }
    >
      <RefreshIcon spinning={busy} />
      {busy ? "Refreshing" : "Refresh"}
    </Button>
  );
}

/**
 * What the last refresh came to, in a line of its own under the page head.
 * Under the head, not inside it: the head sets its controls on the title's
 * foot, so a line added to them would lift Refresh from under the pointer
 * that has just pressed it.
 */
function RefreshLine({
  note,
  onScreen,
  listed,
}: {
  note: RefreshNote | null;
  /** The filter on screen is the one that was refreshed. */
  onScreen: boolean;
  /** There are stories under the line. */
  listed: boolean;
}) {
  if (note === null) return null;
  if (note.kind === "failed") {
    return (
      <p
        key={note.seq}
        role="alert"
        className="m-0 text-sm font-medium text-alert sm:text-right"
      >
        {refreshFailedLine(note.detail, listed)}
      </p>
    );
  }
  return (
    <p key={note.seq} role="status" className="bs-note m-0 sm:text-right">
      {refreshedLine(note.at, note.inserted, note.scope, onScreen)}
    </p>
  );
}

/**
 * The newest stories for everything followed: the newest one large, the
 * rest in a list under it, and a filter to one team, athlete or competition
 * at a time. A row opens the in-app reader; the arrows go to the source.
 *
 * Every filter has a feed of its own, asked for side by side. That is what
 * lets each filter carry a true count (the unfiltered feed is only the
 * newest fifty of everything, in which a busy team crowds the rest out),
 * lets a follow be called quiet only when its own feed is empty, and makes
 * a change of filter instant.
 */
export default function NewsView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();
  const [chosen, setChosen] = useState<string>(ALL_KEY);
  const [selected, setSelected] = useState<NewsItem | null>(null);
  const [refreshNote, setRefreshNote] = useState<RefreshNote | null>(null);
  const refreshSeq = useRef(0);
  const showing = useIsActiveTab("news");
  // "37m ago" is a function of the clock, not of a payload.
  const now = useNow(AGE_TICK_MS);

  const teams = teamsQuery.data?.teams;
  const leagues = teamsQuery.data?.leagues;
  const entries = useMemo(
    () => filterEntries(teams ?? [], leagues ?? []),
    [teams, leagues],
  );
  const scopes = useMemo(() => entries.map((entry) => entry.scope), [entries]);
  // The five-minute reads run only while News is the tab on screen: the
  // view stays mounted behind the others, and a round is a request per
  // follow.
  const queries = useNewsFeeds(scopes, FEED_REQUEST, showing);

  // Back on screen (the kiosk rotation comes round every minute or so), a
  // feed that missed its beat meanwhile is read at once. No dependency
  // list: it has to see the feeds as they are now, and does nothing except
  // on the render where the tab comes back.
  const wasShowing = useRef(showing);
  useEffect(() => {
    const returned = showing && !wasShowing.current;
    wasShowing.current = showing;
    if (!returned) return;
    const at = Date.now();
    for (const query of queries) {
      const readAt = Math.max(query.dataUpdatedAt, query.errorUpdatedAt);
      if (!query.isFetching && feedOverdue(readAt, at, NEWS_REFETCH_MS)) {
        void query.refetch();
      }
    }
  });

  // A filter whose follow has gone (unfollowed since) falls back to All.
  const active = entries.find((entry) => entry.key === chosen) ?? entries[0];
  const refresh = useRefreshNews(active.scope);

  const feeds = new Map<string, Feed | null>();
  entries.forEach((entry, index) => {
    feeds.set(entry.key, feedOf(queries[index]?.data));
  });
  const allFeed = feeds.get(ALL_KEY) ?? null;
  const counts = new Map<string, ScopeCount | null>();
  for (const entry of entries) {
    counts.set(entry.key, scopeCount(entry, feeds.get(entry.key) ?? null, allFeed));
  }

  const activeQuery = queries[entries.indexOf(active)];
  const activeFeed = feeds.get(active.key) ?? null;
  const answered = activeFeed !== null;

  // While a newly chosen filter is still on its way, the list already on
  // screen stays where it is, so the page does not drop to a skeleton and
  // jump back. `heldKey` is the last filter whose stories were shown.
  const [heldKey, setHeldKey] = useState<string>(ALL_KEY);
  useEffect(() => {
    if (answered) setHeldKey(active.key);
  }, [answered, active.key]);
  const held = entries.find((entry) => entry.key === heldKey);
  const heldFeed = held === undefined ? null : (feeds.get(held.key) ?? null);
  // An empty list is not held: "No stories for Bournemouth" under a
  // pressed Chelsea, with nothing on the page loading, would read as
  // Chelsea's answer.
  const holding = !answered && heldFeed !== null && heldFeed.stories.length > 0;
  const shown = answered ? active : holding ? held : undefined;
  const shownFeed = answered ? activeFeed : holding ? heldFeed : null;
  const waiting = !answered && shown !== undefined;

  const selectedSource = useMemo<NewsSource | undefined>(() => {
    if (selected === null) return undefined;
    const mark = markFor(selected, entries, leagues ?? []);
    return {
      name: mark.name,
      color: mark.color,
      abbreviation: mark.abbreviation,
      context: mark.context,
    };
  }, [selected, entries, leagues]);

  if (teamsQuery.isError && !teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <Notice
          title="News"
          alert
          headline="Couldn’t load teams"
          action={{ label: "Retry", onClick: () => void teamsQuery.refetch() }}
        >
          {`The request failed${errorText(teamsQuery.error)}.`}
        </Notice>
      </div>
    );
  }
  if (!teamsQuery.data || leagues === undefined) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <PageLoading label="Loading teams" />
      </div>
    );
  }
  if (entries.length === 1) {
    return (
      <div className="flex flex-col gap-7">
        <PageHead />
        <Notice
          title="News"
          headline="Nothing followed yet"
          action={{ label: "Manage teams", onClick: openManageTeams }}
        >
          Follow a team, a player or a competition to see its stories here.
        </Notice>
      </div>
    );
  }

  const pickFilter = (key: string) => {
    setChosen(key);
    // The line under the controls is about the filter that was refreshed.
    setRefreshNote(null);
  };
  const runRefresh = () => {
    const scope = active;
    // Which result this is, what it was a refresh of, and the day it came.
    const made = (at: Date) => {
      refreshSeq.current += 1;
      return { seq: refreshSeq.current, scope, day: localKeyFromDate(at) };
    };
    refresh.mutate(undefined, {
      onSuccess: (result) => {
        const at = new Date();
        setRefreshNote({
          ...made(at),
          kind: "done",
          at: formatTime(at.toISOString()),
          inserted: result.inserted,
        });
      },
      onError: (error) =>
        setRefreshNote({
          ...made(new Date()),
          kind: "failed",
          detail: errorText(error),
        }),
    });
  };

  const scopeName = active.kind === "all" ? null : active.name;
  const stale = Boolean(activeQuery?.isError) && answered;
  // The last result stays in its line while the next refresh runs (the
  // button says that one is running): taking it out for those seconds
  // pulled the page up under the reader and pushed it down again. It goes
  // when the day changes, since it gives only a time of day. And "3 new
  // stories" is not said over a list that could not be read again
  // afterwards: the banner says the stories on screen are the last ones
  // loaded.
  const refreshLine =
    refreshNote === null ||
    refreshLineSpent(refreshNote.day, localKeyFromDate(now)) ||
    (stale && refreshNote.kind === "done")
      ? null
      : refreshNote;
  let listed = false;
  let body: ReactNode;
  if (activeQuery?.isError && !answered) {
    body = (
      <Notice
        title="News"
        alert
        headline="Couldn’t load news"
        action={{ label: "Retry", onClick: () => void activeQuery.refetch() }}
      >
        {`The request failed${errorText(activeQuery.error)}.`}
      </Notice>
    );
  } else if (shown === undefined || shownFeed === null) {
    body = (
      <PageLoading
        label={
          active.kind === "all"
            ? "Loading news"
            : `Loading stories for ${active.name}`
        }
      />
    );
  } else if (shownFeed.stories.length === 0) {
    const caveat = quietCaveat(shown, entries, leagues, counts);
    body = (
      <Notice
        title="News"
        headline={
          shown.kind === "all"
            ? "No stories yet"
            : quietHeadline(shown, entries, leagues, counts)
        }
      >
        {(shown.kind === "all"
          ? "Nothing has been collected yet for what you follow. "
          : caveat === null
            ? ""
            : `${caveat} `) +
          "Refresh collects the latest now; otherwise new stories arrive with the next scheduled collection."}
      </Notice>
    );
  } else {
    const [top, ...rest] = shownFeed.stories;
    const minutes = Math.round(NEWS_REFETCH_MS / 60_000);
    listed = true;
    body = (
      <>
        <TopStory
          story={top}
          mark={markFor(top, entries, leagues)}
          note={
            waiting ? `Loading stories for ${active.name}` : topNote(shown, entries)
          }
          aside={`Checks for new stories every ${minutes} minutes`}
          busy={waiting}
        />
        <StoryList
          stories={rest}
          entries={entries}
          leagues={leagues}
          quiet={shown.kind === "all" && !waiting ? quietEntries(entries, feeds) : []}
          counts={counts}
          note={moreNote(shownFeed)}
          busy={waiting}
          onOpen={setSelected}
        />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-7">
      {stale && (
        <StaleBanner
          message={
            scopeName === null
              ? "Connection lost — showing the last loaded stories."
              : `Connection lost — showing the last loaded stories for ${scopeName}.`
          }
          onRetry={() => void activeQuery?.refetch()}
        />
      )}

      <div className="flex flex-col gap-2.5">
        <PageHead>
          {/* From a tablet up Refresh stays beside the filter, which keeps
              to its own share of the row (scrolling inside it, or from a
              wide window wrapping); on a phone the filter is a strip of
              its own and Refresh goes under it. */}
          <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3 sm:flex-nowrap">
            <ScopeFilter
              entries={entries}
              counts={counts}
              value={active.key}
              onChange={pickFilter}
            />
            <RefreshButton
              busy={refresh.isPending}
              tall={filterKind(entries) === "segments"}
              onClick={runRefresh}
            />
          </div>
        </PageHead>
        <RefreshLine
          note={refreshLine}
          onScreen={refreshLine?.scope.key === active.key}
          listed={listed}
        />
      </div>

      {waiting && (
        <p role="status" className="sr-only">
          {`Loading stories for ${active.name}`}
        </p>
      )}

      {body}

      <NewsDetailPanel
        item={selected}
        source={selectedSource}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
