import { useMemo, type ReactNode } from "react";

import {
  Loading,
  Notice,
  ScrollRegion,
  SpotChip,
  TeamBlock,
} from "../components/bs";
import { useBracket, useSchedule } from "../hooks";
import { errorText } from "../lib/errorText";
import { formatShortDate, localDayOffset } from "../lib/time";
import type { BracketSeries, BracketSide, Game, League } from "../types";
import {
  ROUNDS,
  bracketKind,
  halve,
  roundOf,
  shortSlot,
  splitRound,
  type RoundKey,
  type TeamLookup,
} from "./league/model";
import { useTeamLookup } from "./league/parts";

/**
 * The selected league's bracket, drawn TWO-SIDED: the two halves of a draw
 * flow inward from the left and right edges and converge on a center
 * final. For the US leagues (NBA/MLB/NHL) the halves are the conferences
 * (East left, West right) and the center is the league championship; for
 * soccer cups (the World Cup) the halves are the top and bottom of the
 * knockout draw.
 *
 * The league comes from the page's picker, shared with Standings and
 * Leaders, so a league with no bracket says so here rather than being
 * missing from a list of its own.
 */

interface BracketColumn {
  key: string;
  label: string;
  nodes: ReactNode[];
}

export default function BracketView({
  league,
  seasonYear,
  onCurrentSeason,
}: {
  league: League;
  /** The archive season picked, or undefined for the season in progress. */
  seasonYear: number | undefined;
  onCurrentSeason: () => void;
}) {
  const scheduleQuery = useSchedule(localDayOffset(-30), localDayOffset(75));
  const lookup = useTeamLookup(league);

  // A cup is known by its knockout fixtures: this league's games whose side
  // names are still draw slots, grouped by round in start order.
  const { byRound, hasKnockout } = useMemo(() => {
    const map: Record<RoundKey, Game[]> = {
      r32: [], r16: [], qf: [], sf: [], third: [], final: [],
    };
    let found = false;
    for (const game of scheduleQuery.data ?? []) {
      if (game.league_id !== league.id) continue;
      const round = roundOf(game);
      if (round === null) continue;
      map[round].push(game);
      found = true;
    }
    for (const key of Object.keys(map) as RoundKey[]) {
      map[key].sort((a, b) => a.start_time.localeCompare(b.start_time));
    }
    return { byRound: map, hasKnockout: found };
  }, [scheduleQuery.data, league.id]);

  const kind = bracketKind(league.sport, hasKnockout);

  // Playoff series are fetched only for a league that can have them.
  const bracketQuery = useBracket(kind === "playoff" ? league.id : undefined);

  // Two-sided columns for a soccer cup: split each knockout round's draw into
  // a top (left) and bottom (right) half; the final + third-place sit center.
  const cup = useMemo(() => {
    const left: BracketColumn[] = [];
    const right: BracketColumn[] = [];
    for (const round of ROUNDS) {
      if (round.key === "final" || round.key === "third") continue;
      const games = byRound[round.key];
      if (games.length === 0) continue;
      const halves = halve(games);
      left.push({
        key: round.key,
        label: round.label,
        nodes: halves.left.map((g) => <CupGame key={g.id} game={g} />),
      });
      right.push({
        key: round.key,
        label: round.label,
        nodes: halves.right.map((g) => <CupGame key={g.id} game={g} />),
      });
    }
    const centerNodes: ReactNode[] = [
      ...byRound.final.map((g) => <CupGame key={g.id} game={g} center />),
      ...byRound.third.map((g) => <CupGame key={g.id} game={g} center />),
    ];
    const center: BracketColumn | null = centerNodes.length
      ? {
          key: "final",
          label: byRound.third.length > 0 ? "Final · 3rd Place" : "Final",
          nodes: centerNodes,
        }
      : null;
    return { left, right, center };
  }, [byRound]);

  // Two-sided columns for a US playoff bracket: East left, West right, the
  // league championship in the center.
  const playoff = useMemo(() => {
    const left: BracketColumn[] = [];
    const right: BracketColumn[] = [];
    const centerNodes: ReactNode[] = [];
    let centerLabel = "Final";
    for (const round of bracketQuery.data?.rounds ?? []) {
      const { left: l, right: r, center: c } = splitRound(round.series);
      const card = (center: boolean) => (s: BracketSeries, i: number) => (
        <SeriesCard
          key={`${round.name}-${center ? "c" : "s"}-${i}`}
          series={s}
          lookup={lookup}
          center={center}
        />
      );
      if (l.length > 0 || r.length > 0) {
        left.push({
          key: `${round.name}-L`,
          label: round.name,
          nodes: l.map(card(false)),
        });
        right.push({
          key: `${round.name}-R`,
          label: round.name,
          nodes: r.map(card(false)),
        });
      }
      if (c.length > 0) {
        centerLabel = round.name;
        centerNodes.push(...c.map(card(true)));
      }
    }
    const center: BracketColumn | null = centerNodes.length
      ? { key: "center", label: centerLabel, nodes: centerNodes }
      : null;
    return { left, right, center };
  }, [bracketQuery.data, lookup]);

  // No request here takes a season: a bracket is the postseason in progress.
  if (seasonYear !== undefined) {
    return (
      <Notice
        title="Bracket"
        headline="Current season only"
        action={{ label: "Current season", onClick: onCurrentSeason }}
      >
        The bracket is the postseason in progress. The {seasonYear}{" "}
        season&rsquo;s bracket is not kept.
      </Notice>
    );
  }

  if (kind === "cup") {
    return (
      <TwoSidedBracket
        label={`${league.name} bracket`}
        left={cup.left}
        center={cup.center}
        right={cup.right}
      />
    );
  }

  if (kind === null) {
    // Whether this league is a cup is not known until the schedule answers.
    if (scheduleQuery.isPending) return <Loading label="Loading bracket" rows={4} />;
    if (scheduleQuery.isError && scheduleQuery.data === undefined) {
      return (
        <Notice
          title="Bracket"
          alert
          action={{ label: "Retry", onClick: () => void scheduleQuery.refetch() }}
        >
          Couldn&rsquo;t load the fixtures a bracket is drawn from
          {errorText(scheduleQuery.error)}
        </Notice>
      );
    }
    return league.sport === "soccer" ? (
      <Notice title="Bracket" headline="No bracket yet">
        No knockout fixtures yet for {league.name}. A cup&rsquo;s bracket fills
        in once its knockout stage is drawn; a league decided by its table
        has none.
      </Notice>
    ) : (
      <Notice title="Bracket" headline="No bracket">
        {league.name} has no bracket to show. Brackets appear for cup
        tournaments (like the World Cup) and for the playoffs of basketball,
        baseball and hockey leagues once they&rsquo;re underway.
      </Notice>
    );
  }

  if (bracketQuery.isPending) return <Loading label="Loading bracket" rows={4} />;
  if (bracketQuery.isError && bracketQuery.data === undefined) {
    return (
      <Notice
        title="Bracket"
        alert
        action={{ label: "Retry", onClick: () => void bracketQuery.refetch() }}
      >
        Couldn&rsquo;t load the bracket
        {errorText(bracketQuery.error)}
      </Notice>
    );
  }
  if ((bracketQuery.data?.rounds.length ?? 0) === 0) {
    return (
      <Notice title="Bracket" headline="No active playoffs">
        No active playoffs for {league.name} right now. The bracket fills in
        once its postseason begins.
      </Notice>
    );
  }
  return (
    <TwoSidedBracket
      label={`${league.name} bracket`}
      left={playoff.left}
      center={playoff.center}
      right={playoff.right}
    />
  );
}

// --- Two-sided layout -------------------------------------------------------

/**
 * Mirrored bracket: left columns flow inward (outer round → inner), the
 * center column holds the final, and the right columns mirror the left
 * (rendered inner → outer so they flow back out to the right edge). Columns
 * share height and vertically center their matchups so the draw visibly
 * converges on the center. Scrolls horizontally when the rounds are wide.
 */
function TwoSidedBracket({
  label,
  left,
  center,
  right,
}: {
  label: string;
  left: BracketColumn[];
  center: BracketColumn | null;
  right: BracketColumn[];
}) {
  const hasLeft = left.some((c) => c.nodes.length > 0);
  const hasRight = right.some((c) => c.nodes.length > 0);
  // Mirror: the right half reads from the center outward.
  const rightOutward = [...right].reverse();
  return (
    <ScrollRegion label={label} className="pb-2">
      <div className="flex min-w-max items-stretch justify-center gap-3 sm:gap-4">
        {hasLeft &&
          left.map((col) => <BracketColumnView key={`L-${col.key}`} col={col} />)}
        {center && <BracketColumnView col={center} align="center" />}
        {hasRight &&
          rightOutward.map((col) => (
            <BracketColumnView key={`R-${col.key}`} col={col} align="right" />
          ))}
      </div>
    </ScrollRegion>
  );
}

/**
 * One round: its tab hanging from the rule, then its matchups centered in
 * the column's height. The tab sits on the side the round flows from, and
 * in the middle over the final.
 */
function BracketColumnView({
  col,
  align = "left",
}: {
  col: BracketColumn;
  align?: "left" | "center" | "right";
}) {
  return (
    <section
      aria-label={col.label}
      className="flex w-52 shrink-0 flex-col gap-2 sm:w-56"
    >
      <div
        className={
          "bs-section-head items-center " +
          (align === "right"
            ? "flex-row-reverse"
            : align === "center"
              ? "justify-center"
              : "")
        }
      >
        <h2 className="bs-tab">{col.label}</h2>
        {col.nodes.length > 1 && (
          <span className="bs-note">
            {col.nodes.length}
            <span className="sr-only"> matchups</span>
          </span>
        )}
      </div>
      {col.nodes.length > 0 ? (
        <div className="flex flex-1 flex-col justify-center gap-2">
          {col.nodes}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted">
          —
        </div>
      )}
    </section>
  );
}

/** A matchup's sheet; the final's is framed in ink. */
const CARD = "bs-sheet px-3 py-2";
const CARD_CENTER = "border-2 border-ink bg-sheet px-3 py-2";

// --- Playoff series card ----------------------------------------------------

function SeriesCard({
  series,
  lookup,
  center,
}: {
  series: BracketSeries;
  lookup: TeamLookup;
  center: boolean;
}) {
  return (
    <div className={center ? CARD_CENTER : CARD}>
      <SeriesSide side={series.team1} lookup={lookup} />
      <SeriesSide side={series.team2} lookup={lookup} />
      {series.summary && (
        <p className="m-0 mt-1 text-sm text-ink-2">{series.summary}</p>
      )}
    </div>
  );
}

function SeriesSide({ side, lookup }: { side: BracketSide; lookup: TeamLookup }) {
  const identity = lookup(side.name, side.abbreviation);
  return (
    <div className="flex items-center gap-2 py-1 font-medium">
      <TeamBlock
        name={side.name}
        abbreviation={side.abbreviation ?? identity?.abbreviation}
        color={identity?.color}
      />
      <span className="min-w-0">{side.name}</span>
    </div>
  );
}

// --- Soccer knockout game card ----------------------------------------------

function CupGame({ game, center = false }: { game: Game; center?: boolean }) {
  const decided = game.phase === "final";
  const live = game.phase === "in_progress";
  const scored = game.away.score !== null && game.home.score !== null;
  return (
    <div className={center ? CARD_CENTER : CARD}>
      <CupSide
        name={shortSlot(game.away.name)}
        score={game.away.score}
        decided={decided}
        winner={
          decided && scored && (game.away.score ?? 0) > (game.home.score ?? 0)
        }
      />
      <CupSide
        name={shortSlot(game.home.name)}
        score={game.home.score}
        decided={decided}
        winner={
          decided && scored && (game.home.score ?? 0) > (game.away.score ?? 0)
        }
      />
      <div className="mt-1 flex items-center justify-between gap-2 text-sm text-muted">
        <span>{formatShortDate(game.start_time)}</span>
        {live && <SpotChip>Live</SpotChip>}
        {decided && <span>Final</span>}
      </div>
    </div>
  );
}

function CupSide({
  name,
  score,
  decided,
  winner,
}: {
  name: string;
  score: number | null;
  decided: boolean;
  winner: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span
        className={
          "min-w-0 " +
          (winner
            ? "font-bold text-ink"
            : decided
              ? "text-muted"
              : "font-medium text-ink")
        }
      >
        {name}
      </span>
      <span className="flex-none font-condensed text-lg font-bold leading-none tabular-nums">
        {score ?? ""}
      </span>
    </div>
  );
}
