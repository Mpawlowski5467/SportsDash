/**
 * The News screen's logic, kept pure (no React) so it can be unit-tested:
 * which filters there are, what each one's count may say, which follows
 * are provably without stories, how a story is labelled and dated, and how
 * a headline and a code are sized to fit.
 *
 * Everything here is derived from `/teams` and `/news`. A story is nine
 * fields (id, team_id, league_id, title, url, source, published_at,
 * summary, image_url), so there is no function for what the mockup shows
 * beyond them: a category ("Injury", "Recap"), a team's short name, a
 * second team colour, a byline, or how "recent" a story is.
 */

import {
  formatShortDate,
  formatTime,
  localDateKey,
  monthShort,
  relativeTime,
} from "../../lib/time";
import type { League, NewsItem, NewsScope, Sport, Team } from "../../types";
import { teamSwitcherKind } from "../results/model";

// ── The window ──────────────────────────────────────────────────────────

/** How many stories a filter lists. */
export const FEED_SHOWN = 50;
/**
 * How many are asked for: one more than are listed. The extra one is never
 * shown; its arrival is the proof that the filter holds more than the page
 * lists, which is the difference between "50" and "50+".
 */
export const FEED_REQUEST = FEED_SHOWN + 1;

/** One filter's stories as the page lists them. */
export interface Feed {
  /** Newest first, at most `FEED_SHOWN`. */
  stories: NewsItem[];
  /** The server holds more for this filter than are listed. */
  more: boolean;
}

/** A feed from what `/news` answered; null while it has not answered. */
export function feedOf(items: readonly NewsItem[] | undefined): Feed | null {
  if (items === undefined) return null;
  return {
    stories: items.slice(0, FEED_SHOWN),
    more: items.length > FEED_SHOWN,
  };
}

/**
 * Whether a feed last read (or last failed) at `readAt` has missed a beat.
 * The view reads its feeds every `every` ms only while it is the tab on
 * screen; coming back, the ones this is true of are read at once.
 */
export function feedOverdue(readAt: number, now: number, every: number): boolean {
  return now - readAt >= every;
}

// ── Filters ─────────────────────────────────────────────────────────────

export const ALL_KEY = "all";

/**
 * What a filter stands for. An athlete is a followed "team" of a sport
 * played by people (tennis, MMA, golf, racing): same row in `/teams`, but a
 * person's name, usually no colour, and a clipped short name for a code.
 */
export type ScopeKind = "all" | "team" | "athlete" | "competition";

const PEOPLE_SPORTS: ReadonlySet<Sport> = new Set<Sport>([
  "tennis",
  "mma",
  "golf",
  "racing",
]);

export interface FilterEntry {
  /** "all", "team:<id>" or "league:<id>". */
  key: string;
  /** What `/news` is asked for; undefined is everything followed. */
  scope: NewsScope | undefined;
  kind: ScopeKind;
  name: string;
  /** The code for the block: a team's own, a competition's from its name. */
  abbreviation: string | null;
  color: string | null;
  /** The league a team or athlete plays in; a competition's own id. */
  leagueId: string | null;
}

/**
 * A short code for a competition, which has none of its own: initials of
 * its words ("FIFA World Cup" is FWC), or the start of a single word.
 */
export function leagueCode(name: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .filter((word) => /^[\p{L}\p{N}]/u.test(word));
  if (words.length === 0) return "";
  if (words.length === 1) {
    const word = words[0];
    return (word.length <= 4 ? word : word.slice(0, 3)).toUpperCase();
  }
  return words
    .slice(0, 4)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

/**
 * The filters, in the order the page shows them: everything, then each
 * followed team as `/teams` lists it, then each competition followed whole
 * (those have no team rows; their stories are keyed by league).
 */
export function filterEntries(
  teams: readonly Team[],
  leagues: readonly League[],
): FilterEntry[] {
  const sports = new Map(leagues.map((league) => [league.id, league.sport]));
  const entries: FilterEntry[] = [
    {
      key: ALL_KEY,
      scope: undefined,
      kind: "all",
      name: "All",
      abbreviation: null,
      color: null,
      leagueId: null,
    },
  ];
  for (const team of teams) {
    const sport = sports.get(team.league_id);
    entries.push({
      key: `team:${team.id}`,
      scope: { teamId: team.id },
      kind: sport !== undefined && PEOPLE_SPORTS.has(sport) ? "athlete" : "team",
      name: team.name,
      abbreviation: team.abbreviation,
      color: team.color,
      leagueId: team.league_id,
    });
  }
  for (const league of leagues) {
    if (!league.follow_all) continue;
    entries.push({
      key: `league:${league.id}`,
      scope: { leagueId: league.id },
      kind: "competition",
      name: league.name,
      abbreviation: leagueCode(league.name),
      color: null,
      leagueId: league.id,
    });
  }
  return entries;
}

/**
 * A row of segments for a handful of follows, the dropdown beyond that:
 * the same number Results uses for its team switcher.
 */
export function filterKind(
  entries: readonly FilterEntry[],
): "segments" | "select" {
  const follows = entries.filter((entry) => entry.kind !== "all").length;
  return teamSwitcherKind(follows) === "select" ? "select" : "segments";
}

// ── Counts ──────────────────────────────────────────────────────────────

/** The filter a story belongs to: its team, else its competition. */
export function storyKey(
  item: Pick<NewsItem, "team_id" | "league_id">,
): string | null {
  if (item.team_id) return `team:${item.team_id}`;
  if (item.league_id) return `league:${item.league_id}`;
  return null;
}

export interface ScopeCount {
  /** Stories this filter lists. */
  count: number;
  /** There are more than that: the number is a floor, written "50+". */
  more: boolean;
}

/**
 * What a filter's count may say. It is the number of stories that filter
 * lists when pressed, from the filter's OWN answer: the unscoped feed is
 * the newest fifty of everything, in which one busy team crowds the others
 * out, so a team's share of it says nothing about how many it has. Null
 * until the filter has answered: no number is better than a guessed one.
 *
 * The feeds are read on the same beat but not at the same instant, so the
 * unscoped feed can hold a story the filter's own (older) answer lacks.
 * The larger of the two is still true, and stops a "0" beside a story.
 */
export function scopeCount(
  entry: FilterEntry,
  own: Feed | null,
  all: Feed | null,
): ScopeCount | null {
  if (own === null) return null;
  if (entry.kind === "all") {
    return { count: own.stories.length, more: own.more };
  }
  const seen =
    all === null
      ? 0
      : all.stories.filter((story) => storyKey(story) === entry.key).length;
  return { count: Math.max(own.stories.length, seen), more: own.more };
}

/** "4", "0", "50+"; null when there is nothing to say yet. */
export function countText(count: ScopeCount | null): string | null {
  if (count === null) return null;
  return count.more ? `${count.count}+` : String(count.count);
}

/** "Athletics, 4 stories", "All, more than 50 stories", "Bournemouth, no stories". */
export function countSpoken(name: string, count: ScopeCount | null): string {
  if (count === null) return name;
  if (count.more) return `${name}, more than ${count.count} stories`;
  if (count.count === 0) return `${name}, no stories`;
  return `${name}, ${count.count} ${count.count === 1 ? "story" : "stories"}`;
}

/**
 * The follows that provably have no stories: their own feed has answered
 * and is empty, and the unscoped feed shows none of theirs either. A team
 * merely absent from the newest fifty of everything is not one of them.
 */
export function quietEntries(
  entries: readonly FilterEntry[],
  feeds: ReadonlyMap<string, Feed | null>,
): FilterEntry[] {
  const all = feeds.get(ALL_KEY) ?? null;
  return entries.filter((entry) => {
    if (entry.kind === "all") return false;
    const count = scopeCount(entry, feeds.get(entry.key) ?? null, all);
    return count !== null && count.count === 0;
  });
}

/** Past this many, the quiet follows share one row instead of one each. */
export const QUIET_ROWS_MAX = 3;

/**
 * What an empty filter says. The feed has no age filter, so nothing is
 * called "recent": the filter holds no stories, and that is all.
 *
 * A league of people is the exception worth a second sentence. ESPN has no
 * per-athlete feed, so the league's whole feed is stored under ONE of the
 * followed athletes; the others hold only what a search for their own name
 * found. "No stories for Sinner" would then be wrong while the tour's
 * stories, his among them, sit under another name.
 *
 * That is only said when it can be seen: another followed person of the
 * same league whose own filter lists a story. With none (two players just
 * followed, a collection that failed) nobody holds the tour's coverage,
 * and the plain sentence is the true one.
 */
export function quietCaveat(
  entry: FilterEntry,
  entries: readonly FilterEntry[],
  leagues: readonly Pick<League, "id" | "name">[],
  counts: ReadonlyMap<string, ScopeCount | null>,
): string | null {
  if (entry.kind !== "athlete") return null;
  const held = entries.some(
    (other) =>
      other.kind === "athlete" &&
      other.key !== entry.key &&
      other.leagueId === entry.leagueId &&
      (counts.get(other.key)?.count ?? 0) > 0,
  );
  if (!held) return null;
  const league = leagues.find((item) => item.id === entry.leagueId);
  const whose = league === undefined ? "The league’s" : `${league.name}’s`;
  return `${whose} own coverage is kept under one of the people you follow there.`;
}

/** "No stories for Bournemouth", or "filed under" where the caveat applies. */
export function quietHeadline(
  entry: FilterEntry,
  entries: readonly FilterEntry[],
  leagues: readonly Pick<League, "id" | "name">[],
  counts: ReadonlyMap<string, ScopeCount | null>,
): string {
  return quietCaveat(entry, entries, leagues, counts) === null
    ? `No stories for ${entry.name}`
    : `No stories filed under ${entry.name}`;
}

/** The whole of it, as the row under the list says it. */
export function quietLine(
  entry: FilterEntry,
  entries: readonly FilterEntry[],
  leagues: readonly Pick<League, "id" | "name">[],
  counts: ReadonlyMap<string, ScopeCount | null>,
): string {
  const caveat = quietCaveat(entry, entries, leagues, counts);
  const headline = `${quietHeadline(entry, entries, leagues, counts)}.`;
  return caveat === null ? headline : `${headline} ${caveat}`;
}

/** How the quiet follows are set under the list. */
export interface QuietRows {
  /** A row each, with what it says. */
  alone: { entry: FilterEntry; line: string }[];
  /** One shared row naming them all: "No stories for five follows". */
  together: FilterEntry[];
}

/**
 * A row each for up to `QUIET_ROWS_MAX` quiet follows; past that the plain
 * ones share a row. A follow whose line carries the caveat keeps a row of
 * its own either way: "No stories for Sinner" in a shared row would be the
 * very sentence the caveat is there to avoid. A shared row of one is not a
 * list, so a lone plain follow keeps its own row too.
 */
export function quietRows(
  quiet: readonly FilterEntry[],
  entries: readonly FilterEntry[],
  leagues: readonly Pick<League, "id" | "name">[],
  counts: ReadonlyMap<string, ScopeCount | null>,
): QuietRows {
  const plain = quiet.filter(
    (entry) => quietCaveat(entry, entries, leagues, counts) === null,
  );
  const share = quiet.length > QUIET_ROWS_MAX && plain.length > 1;
  const together = share ? plain : [];
  return {
    alone: quiet
      .filter((entry) => !together.includes(entry))
      .map((entry) => ({
        entry,
        line: quietLine(entry, entries, leagues, counts),
      })),
    together,
  };
}

// ── A story's mark and label ────────────────────────────────────────────

export interface StoryMark {
  /** The filter this story belongs to, when it is one of the page's. */
  key: string | null;
  /** The team, athlete or competition; "" when the story names neither. */
  name: string;
  abbreviation: string | null;
  /** A team's colour. Competitions and most athletes have none. */
  color: string | null;
  /** For an athlete: the league, since the story may be the tour's. */
  context: string | null;
}

const NO_MARK: StoryMark = {
  key: null,
  name: "",
  abbreviation: null,
  color: null,
  context: null,
};

/**
 * Who a story is for: its followed team, else its competition. A story
 * whose team has since been unfollowed (the list is a poll behind) gets no
 * label and an empty neutral block rather than a raw id.
 */
export function markFor(
  item: Pick<NewsItem, "team_id" | "league_id">,
  entries: readonly FilterEntry[],
  leagues: readonly Pick<League, "id" | "name">[],
): StoryMark {
  const key = storyKey(item);
  const entry =
    key === null ? undefined : entries.find((candidate) => candidate.key === key);
  if (entry !== undefined) {
    return {
      key: entry.key,
      name: entry.name,
      abbreviation: entry.abbreviation,
      color: entry.color,
      context:
        entry.kind === "athlete"
          ? (leagues.find((league) => league.id === entry.leagueId)?.name ?? null)
          : null,
    };
  }
  // A competition that is known but not followed whole.
  if (!item.team_id && item.league_id) {
    const league = leagues.find((candidate) => candidate.id === item.league_id);
    if (league !== undefined) {
      return {
        key: null,
        name: league.name,
        abbreviation: leagueCode(league.name),
        color: null,
        context: null,
      };
    }
  }
  return NO_MARK;
}

/**
 * Who a story is for, in words: the team or competition, and for an
 * athlete the league too, since the story may be the tour's rather than
 * the player's own. "" for a story that names neither.
 */
export function markLabel(mark: StoryMark): string {
  if (mark.name === "") return "";
  return mark.context === null ? mark.name : `${mark.name} · ${mark.context}`;
}

// ── Headline and summary ────────────────────────────────────────────────

/**
 * The headline without the outlet's name on the end. Search-feed titles
 * arrive as "Headline - Outlet", and the outlet is `source`, printed
 * beside the headline already. Only an exact match of the story's own
 * source is taken off; any other dash is the headline's own.
 */
export function headlineOf(item: Pick<NewsItem, "title" | "source">): string {
  const title = item.title.trim();
  const source = item.source.trim();
  if (source === "") return title;
  for (const dash of [" - ", " – ", " — ", " | "]) {
    const tail = dash + source;
    if (
      title.length > tail.length &&
      title.slice(-tail.length).toLowerCase() === tail.toLowerCase()
    ) {
      return title.slice(0, -tail.length).trimEnd();
    }
  }
  return title;
}

/**
 * Whether a headline is set in capitals, as Box Score headings are. The
 * capitals are the browser's (`text-transform`), so the text itself stays
 * as published for a screen reader and for copying; and the browser is not
 * told the language, because a story does not carry one. For nearly every
 * headline that is harmless: German "ß" becomes "SS", which is how German
 * writes it, and a script with no capitals (Arabic, Chinese) is untouched.
 * Turkish is the exception: its dotted and dotless i are two letters, and
 * capitals without the language turn both into "I", which respells words
 * ("kritik" should be KRİTİK, not KRITIK). A headline carrying a letter
 * only that family of languages uses is left the way it was written.
 */
export function inCapitals(headline: string): boolean {
  return !/[ıİğĞ]/u.test(headline);
}

/** A piece of a headline; a `keep` piece is never split across lines. */
export interface HeadlinePart {
  text: string;
  keep: boolean;
}

const SCORE = /\d+(?:[-–:]\d+)+/g;

/**
 * A headline cut where it may wrap. A browser breaks a line after a
 * hyphen, which in a narrow column leaves "11-" at the end of one line and
 * "8" at the start of the next. Figures joined by a hyphen, a dash or a
 * colon (a score, "3:1", a season "2026-27") are kept in one piece.
 */
export function headlineParts(headline: string): HeadlinePart[] {
  const parts: HeadlinePart[] = [];
  let from = 0;
  for (const match of headline.matchAll(SCORE)) {
    if (match.index > from) {
      parts.push({ text: headline.slice(from, match.index), keep: false });
    }
    parts.push({ text: match[0], keep: true });
    from = match.index + match[0].length;
  }
  if (from < headline.length) {
    parts.push({ text: headline.slice(from), keep: false });
  }
  return parts;
}

const lettersOnly = (text: string): string =>
  text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/**
 * The summary, when it says more than the headline. Search-feed stories
 * carry the headline again with the outlet's name after it, and video
 * clips carry the headline alone: neither is a summary, and a deck that
 * repeats the line above it is worse than none.
 *
 * Some search-feed summaries go on after that: the headline, the outlet,
 * then other outlets' headlines on the same subject, run together without
 * a stop. That is a list of related coverage, not a summary of this story,
 * and it is known by how it starts (the headline, then the story's own
 * outlet). A real summary that merely opens with the headline's words is
 * kept.
 */
export function deckOf(
  item: Pick<NewsItem, "title" | "source" | "summary">,
): string | null {
  const summary = item.summary?.trim() ?? "";
  if (summary === "") return null;
  const said = lettersOnly(summary);
  if (said === "") return null;
  const withOutlet = lettersOnly(`${headlineOf(item)} ${item.source}`);
  const echoes = [
    lettersOnly(item.title),
    lettersOnly(headlineOf(item)),
    withOutlet,
  ];
  if (echoes.includes(said)) return null;
  if (lettersOnly(item.source) !== "" && said.startsWith(withOutlet)) return null;
  return summary;
}

// ── Dates ───────────────────────────────────────────────────────────────

const FULL_FORMAT = new Intl.DateTimeFormat("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export interface DateParts {
  /** "Jul" */
  month: string;
  /** "29" */
  day: string;
  /**
   * "2025" for a story from another year than the viewer's current one,
   * else null. A row prints the month, the day and the time of day, which
   * read the same for last July as for this one; the year is printed
   * wherever it is not the obvious one.
   */
  year: string | null;
  /** "2026-07-29", the viewer's calendar day: the `datetime` attribute. */
  key: string;
  /** "9:46 PM" */
  time: string;
  /** "Wednesday, July 29, 2026 at 9:46 PM": the tooltip, and what is read out. */
  full: string;
}

/**
 * A story's date in the viewer's zone, cut for the date cell. Null for a
 * story with no date (the cell shows a dash; no "recently" is made up).
 */
export function dateParts(
  iso: string | null,
  now: Date = new Date(),
): DateParts | null {
  if (iso === null) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  return {
    month: monthShort(when),
    day: String(when.getDate()),
    year:
      when.getFullYear() === now.getFullYear()
        ? null
        : String(when.getFullYear()),
    key: localDateKey(iso),
    time: formatTime(iso),
    // Some engines set a narrow or no-break space before "PM".
    full: FULL_FORMAT.format(when).replace(/[\u202f\u00a0]/g, " "),
  };
}

/** "Jul 29, 9:46 PM"; "Jul 29, 2025, 9:46 PM" for another year's story. */
export function dateLine(date: DateParts): string {
  const day = `${date.month} ${date.day}`;
  return date.year === null
    ? `${day}, ${date.time}`
    : `${day}, ${date.year}, ${date.time}`;
}

/**
 * How old the top story is: "37m ago", and past five weeks its date, which
 * carries the year when that is not this one ("Dec 1, 2025").
 */
export function ageText(iso: string, date: DateParts): string {
  const age = relativeTime(iso);
  return date.year !== null && age === formatShortDate(iso)
    ? `${age}, ${date.year}`
    : age;
}

// ── Fit ─────────────────────────────────────────────────────────────────

/**
 * The top headline's size in rem at full width: the mockup's 52px for a
 * headline of the mockup's length, stepping down for the longer ones real
 * feeds send (135 characters is not unusual), so the story still fits
 * beside its photo.
 */
export function heroHeadlineRem(headline: string): number {
  const length = headline.trim().length;
  if (length <= 90) return 3.25;
  if (length <= 130) return 2.75;
  return 2.25;
}

/**
 * The size of the code in the photo slot's colour block: the mockup's
 * 168px where it fits, and never wider than the slot. A capital in Barlow
 * Condensed 700 is at most about 0.6em wide, so `n` of them at `140 / n`
 * percent of the slot's width span no more than 84% of it.
 */
export function heroCodeSize(code: string): string {
  const letters = Math.max(1, Array.from(code).length);
  return `min(10.5rem, ${Math.round(140 / letters)}cqi)`;
}

// ── Words ───────────────────────────────────────────────────────────────

/** The note beside "Top story": what the newest headline is the newest of. */
export function topNote(
  active: FilterEntry,
  entries: readonly FilterEntry[],
): string {
  if (active.kind !== "all") return `Newest headline for ${active.name}`;
  const onlyTeams = entries.every(
    (entry) => entry.kind === "all" || entry.kind === "team",
  );
  return onlyTeams
    ? "Newest headline across your teams"
    : "Newest headline across everything you follow";
}

/** The note beside "More stories": how many are under it, and why no more. */
export function moreNote(feed: Feed): string {
  const rest = Math.max(0, feed.stories.length - 1);
  if (rest === 0) return "No other stories";
  const count = `${rest} ${rest === 1 ? "story" : "stories"} · newest first`;
  return feed.more
    ? `${count} · only the newest ${FEED_SHOWN} are listed`
    : count;
}

/**
 * What a finished refresh found. A refresh of one follow names it. A
 * refresh of everything needs no name on the unfiltered page, but its
 * result can land after the filter was changed, and "3 new stories" under
 * the Athletics filter would read as three for the Athletics: off its own
 * page it says what it was a refresh of.
 */
export function refreshedLine(
  at: string,
  inserted: number,
  scope: Pick<FilterEntry, "kind" | "name">,
  onScreen: boolean,
): string {
  const found =
    inserted === 0
      ? "nothing new"
      : `${inserted} new ${inserted === 1 ? "story" : "stories"}`;
  const whose =
    scope.kind !== "all"
      ? ` for ${scope.name}`
      : onScreen
        ? ""
        : " across everything you follow";
  return `Refreshed at ${at} · ${found}${whose}`;
}

/**
 * What a failed refresh says. `detail` is the error as `errorText` words
 * it (" — provider timed out", or ""). The second sentence is only for a
 * page that has stories under it.
 */
export function refreshFailedLine(detail: string, listed: boolean): string {
  return listed
    ? `Refresh failed${detail}. The stories below are the last ones loaded.`
    : `Refresh failed${detail}.`;
}

/**
 * Whether a refresh's result line has outlived its day. The line gives a
 * time of day ("Refreshed at 9:58 PM"), which the next morning would read
 * as this evening's. Both are the viewer's calendar days, "YYYY-MM-DD".
 * Only a day gone by counts: the page's clock is up to a minute behind, so
 * a result from just after midnight can carry a later day than `today`.
 */
export function refreshLineSpent(day: string, today: string): boolean {
  return day < today;
}

/** What a row's button is called: the headline first, then who, where, when. */
export function rowLabel(
  headline: string,
  mark: StoryMark,
  source: string,
  date: DateParts | null,
): string {
  const facts = [markLabel(mark), source, date?.full ?? ""].filter(
    (part) => part !== "",
  );
  return facts.length === 0 ? headline : `${headline}. ${facts.join(", ")}`;
}

/** What the link out is called. */
export function sourceLabel(headline: string, source: string): string {
  return source === ""
    ? `Read “${headline}” at its source, in a new tab`
    : `Read “${headline}” at ${source}, in a new tab`;
}
