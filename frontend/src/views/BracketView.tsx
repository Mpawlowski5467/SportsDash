import { useMemo, useState, type ReactNode } from "react";

import {
  useBracket,
  useSchedule,
  useSetupLeagues,
  useTeams,
} from "../hooks";
import { localDayOffset } from "../lib/time";
import type { BracketSeries, BracketSide, Game } from "../types";
import TeamLogo from "../components/TeamLogo";
import Select, { type SelectOption } from "../components/Select";

/**
 * Brackets across sports, drawn TWO-SIDED: the two halves of a draw flow
 * inward from the left and right edges and converge on a center final. For
 * the US leagues (NBA/MLB/NHL) the halves are the conferences (East left,
 * West right) and the center is the league championship; for soccer cups
 * (the World Cup) the halves are the top and bottom of the knockout draw.
 * A dropdown switches between every bracket you follow that's active.
 */

const US_SPORTS = new Set(["basketball", "baseball", "hockey"]);

type RoundKey = "r32" | "r16" | "qf" | "sf" | "third" | "final";

const ROUNDS: { key: RoundKey; label: string }[] = [
  { key: "r32", label: "Round of 32" },
  { key: "r16", label: "Round of 16" },
  { key: "qf", label: "Quarterfinals" },
  { key: "sf", label: "Semifinals" },
  { key: "third", label: "Third Place" },
  { key: "final", label: "Final" },
];

/** Which soccer knockout round a fixture belongs to, from its slot names. */
function roundOf(game: Game): RoundKey | null {
  const text = `${game.home.name} ${game.away.name}`;
  if (/semifinal/i.test(text)) return /loser/i.test(text) ? "third" : "final";
  if (/quarterfinal/i.test(text)) return "sf";
  if (/round of 16/i.test(text)) return "qf";
  if (/round of 32/i.test(text)) return "r16";
  if (/group|place/i.test(text)) return "r32";
  return null;
}

/** Split an ordered list into [left, right] halves for a symmetric draw. */
function halve<T>(items: T[]): { left: T[]; right: T[] } {
  const mid = Math.ceil(items.length / 2);
  return { left: items.slice(0, mid), right: items.slice(mid) };
}

/**
 * Split one playoff round's series into left (East) / right (West) / center
 * (the championship). Uses the conference label when ESPN provides it; when
 * a round carries no conference labels it falls back to a lone series ==
 * center (the final) and otherwise halves the list by order — so the bracket
 * stays two-sided even when the upstream data is sparsely labeled.
 */
function splitRound(series: BracketSeries[]): {
  left: BracketSeries[];
  right: BracketSeries[];
  center: BracketSeries[];
} {
  const east = series.filter((s) => s.conference === "East");
  const west = series.filter((s) => s.conference === "West");
  const unlabeled = series.filter(
    (s) => s.conference !== "East" && s.conference !== "West",
  );
  if (east.length > 0 || west.length > 0) {
    // Conference-labeled round: East left, West right, anything unlabeled
    // (the championship carries no conference) to the center.
    return { left: east, right: west, center: unlabeled };
  }
  if (series.length <= 1) {
    // A lone, unlabeled series is the final → center it.
    return { left: [], right: [], center: series };
  }
  const { left, right } = halve(series);
  return { left, right, center: [] };
}

interface BracketColumn {
  key: string;
  label: string;
  nodes: ReactNode[];
}

export default function BracketView() {
  const teamsQuery = useTeams();
  const setupLeaguesQuery = useSetupLeagues();
  const scheduleQuery = useSchedule(localDayOffset(-30), localDayOffset(75));
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const leagueMeta = useMemo(() => {
    const name: Record<string, string> = {};
    const sport: Record<string, string> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      name[league.id] = league.name;
      sport[league.id] = league.sport;
    }
    const logo: Record<string, string | null> = {};
    for (const cat of setupLeaguesQuery.data?.leagues ?? []) {
      logo[cat.id] = cat.logo_url;
    }
    return { name, sport, logo };
  }, [teamsQuery.data, setupLeaguesQuery.data]);

  // Soccer cups with knockout fixtures in the schedule.
  const soccerBracketIds = useMemo(() => {
    const ids = new Set<string>();
    for (const game of scheduleQuery.data ?? []) {
      if (roundOf(game) !== null) ids.add(game.league_id);
    }
    return ids;
  }, [scheduleQuery.data]);

  // Bracket options: soccer cups first, then followed US-sport leagues (whose
  // playoff bracket, if any, is fetched on demand).
  const { options, kindById } = useMemo(() => {
    const opts: SelectOption[] = [];
    const kinds: Record<string, "soccer" | "playoff"> = {};
    for (const id of soccerBracketIds) {
      kinds[id] = "soccer";
      opts.push({ id, label: leagueMeta.name[id] ?? id, logoUrl: leagueMeta.logo[id] });
    }
    for (const league of teamsQuery.data?.leagues ?? []) {
      if (kinds[league.id] || !US_SPORTS.has(league.sport)) continue;
      kinds[league.id] = "playoff";
      opts.push({
        id: league.id,
        label: league.name,
        logoUrl: leagueMeta.logo[league.id],
      });
    }
    return { options: opts, kindById: kinds };
  }, [soccerBracketIds, teamsQuery.data, leagueMeta]);

  const selectedId = options.some((o) => o.id === selected)
    ? selected
    : options[0]?.id;
  const kind = selectedId ? kindById[selectedId] : undefined;

  // Soccer knockout fixtures grouped by round (used when kind === "soccer").
  const byRound = useMemo(() => {
    const map: Record<RoundKey, Game[]> = {
      r32: [], r16: [], qf: [], sf: [], third: [], final: [],
    };
    if (kind === "soccer") {
      for (const game of scheduleQuery.data ?? []) {
        if (game.league_id !== selectedId) continue;
        const round = roundOf(game);
        if (round) map[round].push(game);
      }
      for (const key of Object.keys(map) as RoundKey[]) {
        map[key].sort((a, b) => a.start_time.localeCompare(b.start_time));
      }
    }
    return map;
  }, [scheduleQuery.data, selectedId, kind]);

  // Playoff series (used when kind === "playoff").
  const bracketQuery = useBracket(kind === "playoff" ? selectedId : undefined);

  // Two-sided columns for a soccer cup: split each knockout round's draw into
  // a top (left) and bottom (right) half; the final + third-place sit center.
  const soccer = useMemo(() => {
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
        nodes: halves.left.map((g) => <SoccerGame key={g.id} game={g} />),
      });
      right.push({
        key: round.key,
        label: round.label,
        nodes: halves.right.map((g) => <SoccerGame key={g.id} game={g} />),
      });
    }
    const centerNodes: ReactNode[] = [
      ...byRound.final.map((g) => <SoccerGame key={g.id} game={g} />),
      ...byRound.third.map((g) => <SoccerGame key={g.id} game={g} />),
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
      const card = (s: BracketSeries, i: number) => (
        <SeriesCard key={`${round.name}-${i}`} series={s} />
      );
      if (l.length > 0 || r.length > 0) {
        left.push({ key: `${round.name}-L`, label: round.name, nodes: l.map(card) });
        right.push({ key: `${round.name}-R`, label: round.name, nodes: r.map(card) });
      }
      if (c.length > 0) {
        centerLabel = round.name;
        centerNodes.push(...c.map(card));
      }
    }
    const center: BracketColumn | null = centerNodes.length
      ? { key: "center", label: centerLabel, nodes: centerNodes }
      : null;
    return { left, right, center };
  }, [bracketQuery.data]);

  if (options.length === 0) {
    return (
      <p className="max-w-md text-sm text-zinc-500">
        No brackets to show yet. Brackets appear for cup tournaments (like the
        World Cup) and for the playoffs of leagues you follow once they're
        underway.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {options.length > 1 && (
        <Select
          options={options}
          value={selectedId}
          onChange={setSelected}
          ariaLabel="Choose bracket"
        />
      )}

      {kind === "soccer" ? (
        soccer.left.length === 0 &&
        soccer.right.length === 0 &&
        soccer.center === null ? (
          <p className="max-w-md text-sm text-zinc-500">
            No knockout fixtures yet for{" "}
            {leagueMeta.name[selectedId ?? ""] ?? "this competition"}. The
            bracket fills in once the knockout stage is drawn.
          </p>
        ) : (
          <TwoSidedBracket
            left={soccer.left}
            center={soccer.center}
            right={soccer.right}
          />
        )
      ) : bracketQuery.isPending ? (
        <p className="text-sm text-zinc-500">Loading bracket…</p>
      ) : (bracketQuery.data?.rounds.length ?? 0) === 0 ? (
        <p className="max-w-md text-sm text-zinc-500">
          No active playoffs for {leagueMeta.name[selectedId ?? ""] ?? "this league"}{" "}
          right now. The bracket fills in once its postseason begins.
        </p>
      ) : (
        <TwoSidedBracket
          left={playoff.left}
          center={playoff.center}
          right={playoff.right}
        />
      )}
    </div>
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
  left,
  center,
  right,
}: {
  left: BracketColumn[];
  center: BracketColumn | null;
  right: BracketColumn[];
}) {
  const hasLeft = left.some((c) => c.nodes.length > 0);
  const hasRight = right.some((c) => c.nodes.length > 0);
  // Mirror: the right half reads from the center outward.
  const rightOutward = [...right].reverse();
  return (
    <div className="-mx-1 overflow-x-auto px-1 pb-2">
      <div className="flex min-w-max items-stretch justify-center gap-3 sm:gap-4">
        {hasLeft &&
          left.map((col) => <BracketColumnView key={`L-${col.key}`} col={col} />)}
        {center && <BracketColumnView col={center} highlight />}
        {hasRight &&
          rightOutward.map((col) => (
            <BracketColumnView key={`R-${col.key}`} col={col} align="right" />
          ))}
      </div>
    </div>
  );
}

function BracketColumnView({
  col,
  highlight = false,
  align = "left",
}: {
  col: BracketColumn;
  highlight?: boolean;
  align?: "left" | "right";
}) {
  return (
    <section
      className={
        "flex w-52 shrink-0 flex-col justify-center gap-2 sm:w-56 " +
        (highlight ? "rounded-xl bg-amber-500/[0.04] px-1.5 py-2" : "")
      }
    >
      <h2
        className={
          "py-1 text-xs font-semibold uppercase tracking-wider " +
          (highlight ? "text-center text-amber-300" : "text-zinc-400") +
          (align === "right" && !highlight ? " text-right" : "") +
          (align === "left" && !highlight ? " text-left" : "")
        }
      >
        {col.label}
        {col.nodes.length > 1 && (
          <span className="ml-1 text-zinc-600">{col.nodes.length}</span>
        )}
      </h2>
      {col.nodes.length > 0 ? (
        <div className="flex flex-col justify-center gap-2">{col.nodes}</div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-xs text-zinc-700">
          —
        </div>
      )}
    </section>
  );
}

// --- Playoff series card ----------------------------------------------------

function SeriesCard({ series }: { series: BracketSeries }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <SeriesSide side={series.team1} />
      <SeriesSide side={series.team2} />
      {series.summary && (
        <p className="mt-1 text-[11px] text-zinc-500">{series.summary}</p>
      )}
    </div>
  );
}

function SeriesSide({ side }: { side: BracketSide }) {
  return (
    <div className="flex items-center gap-2 py-0.5 text-sm">
      <TeamLogo
        logoUrl={side.logo_url}
        name={side.name}
        abbreviation={side.abbreviation}
        size="xs"
      />
      <span className="truncate text-zinc-200">
        {side.abbreviation ?? side.name}
      </span>
    </div>
  );
}

// --- Soccer knockout game card ----------------------------------------------

/** Trim ESPN's verbose slot text ("Round of 32 1 Winner" → "R32 #1"). */
function shortSlot(name: string): string {
  const r32 = name.match(/^Round of 32 (\d+) Winner$/i);
  if (r32) return `R32 #${r32[1]}`;
  const r16 = name.match(/^Round of 16 (\d+) Winner$/i);
  if (r16) return `R16 #${r16[1]}`;
  const qf = name.match(/^Quarterfinal (\d+) Winner$/i);
  if (qf) return `QF #${qf[1]}`;
  const sf = name.match(/^Semifinal (\d+) (Winner|Loser)$/i);
  if (sf) return `SF${sf[1]} ${sf[2] === "Winner" ? "W" : "L"}`;
  return name;
}

function SoccerGame({ game }: { game: Game }) {
  const decided = game.phase === "final";
  const live = game.phase === "in_progress";
  const date = new Date(game.start_time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <SoccerSide
        name={shortSlot(game.away.name)}
        score={game.away.score}
        decided={decided}
        winner={
          decided &&
          game.away.score !== null &&
          game.home.score !== null &&
          game.away.score > game.home.score
        }
      />
      <SoccerSide
        name={shortSlot(game.home.name)}
        score={game.home.score}
        decided={decided}
        winner={
          decided &&
          game.away.score !== null &&
          game.home.score !== null &&
          game.home.score > game.away.score
        }
      />
      <div className="mt-1 flex items-center justify-between text-[11px] text-zinc-500">
        <span>{date}</span>
        {live && <span className="font-semibold text-amber-400">LIVE</span>}
        {decided && <span>Final</span>}
      </div>
    </div>
  );
}

function SoccerSide({
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
    <div className="flex items-center justify-between gap-2 text-sm">
      <span
        className={
          "truncate " +
          (winner
            ? "font-semibold text-zinc-100"
            : decided
              ? "text-zinc-500"
              : "text-zinc-300")
        }
      >
        {name}
      </span>
      <span className="shrink-0 tabular-nums text-zinc-400">{score ?? ""}</span>
    </div>
  );
}
