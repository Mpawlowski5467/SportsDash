import { useEffect, useMemo } from "react";

import { useGameOdds } from "../hooks";
import type { Game } from "../types";
import {
  ODDS_BATCH,
  joiner,
  liveUpsetSentence,
  marketSentence,
  matchupNames,
  pickerWhen,
  radarFootnote,
  radarNote,
  radarSignals,
  type RadarSignal,
} from "../views/matchup/model";
import { SheetNote, SideBlock } from "../views/matchup/parts";
import { SectionHead, SpotChip } from "./bs";

/**
 * How old the odds on screen may get before the batch is read again. The
 * query is keyed by the list of games, so on its own it is asked once per
 * list and a projection or a line published later never reached the radar.
 * The same beat as the Matchup preview's, and like it only while the view
 * is the tab on screen: each read costs the server provider calls.
 */
const ODDS_REFRESH_MS = 10 * 60_000;

/** One flagged game. Pressing it puts that game on screen. */
function SignalCard({
  signal,
  pressed,
  today,
  onSelect,
}: {
  signal: RadarSignal;
  pressed: boolean;
  today: string;
  onSelect: (gameId: string) => void;
}) {
  const { game, live, market } = signal;
  const when = pickerWhen(game, today);
  const sentence = "text-[1.3125rem] font-semibold leading-[1.3]";
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onSelect(game.id)}
      className={
        "bs-focus-inset flex min-h-11 flex-1 cursor-pointer flex-col items-start justify-center gap-3 border-t border-hair px-5 py-[1.125rem] text-left first:border-t-0 " +
        (pressed ? "bs-inverse" : "bg-sheet text-ink hover:bg-tint")
      }
    >
      <span className="flex flex-wrap items-center gap-2 font-condensed font-bold leading-none">
        {/* The blocks are codes; the names are said once, in words. */}
        <span className="sr-only">{matchupNames(game)}, </span>
        <SideBlock side={game.away} leagueId={game.league_id} />
        <span aria-hidden="true" className="text-base font-semibold text-muted">
          {joiner(game.sport)}
        </span>
        <SideBlock side={game.home} leagueId={game.league_id} />
        {when.live ? (
          <SpotChip className="ml-1">{when.text}</SpotChip>
        ) : (
          <span className="ml-1 text-base font-semibold uppercase tracking-[0.06em] text-ink-2">
            {when.text}
          </span>
        )}
      </span>
      {live !== null && (
        <span className={sentence}>{liveUpsetSentence(game, live)}</span>
      )}
      {market !== null && (
        <span className={live === null ? sentence : "text-sm text-ink-2"}>
          {marketSentence(market)}
        </span>
      )}
    </button>
  );
}

/**
 * The games on offer that are running against expectation, as cards that
 * select the game. Two kinds are flagged: the one the radar always had
 * (the betting underdog is the model's favourite, live or not), and a live
 * game whose leader was the model's underdog before the start. Odds come
 * in one batch for every listed game; a game with no projection (soccer,
 * hockey) simply never flags, so the sheet is often a single quiet line.
 */
export default function UpsetRadar({
  games,
  currentId,
  today,
  showing,
  onSelect,
}: {
  /** The games on offer, with their live scores. */
  games: readonly Game[];
  /** The game on screen: its card is the pressed one. */
  currentId?: string;
  /** The viewer's own day (`viewerDay`): a card's bare time means this one. */
  today: string;
  /** The view is the tab on screen: the odds are kept fresh only then. */
  showing: boolean;
  onSelect: (gameId: string) => void;
}) {
  // The endpoint prices the first sixty ids and drops the rest in silence.
  const ids = useMemo(
    () => games.slice(0, ODDS_BATCH).map((game) => game.id),
    [games],
  );
  // The list of games changes through the day (one ends, the day turns),
  // and each list is a query of its own. The last list's odds stand in
  // until the new ones land, or the cards would give way to "Scanning the
  // slate" and come back on every change.
  const oddsQuery = useGameOdds(ids, { keepPrevious: true });
  const oddsById = oddsQuery.data;
  const oddsAt = Math.max(oddsQuery.dataUpdatedAt, oddsQuery.errorUpdatedAt);
  const refetchOdds = oddsQuery.refetch;
  const priced = ids.length > 0;
  useEffect(() => {
    if (!showing || !priced || oddsAt === 0) return;
    const timer = window.setTimeout(
      () => void refetchOdds(),
      Math.max(0, ODDS_REFRESH_MS - (Date.now() - oddsAt)),
    );
    return () => window.clearTimeout(timer);
  }, [showing, priced, oddsAt, refetchOdds]);
  const signals = useMemo(
    () => radarSignals(games, oddsById),
    [games, oddsById],
  );
  const viewing = signals.some((signal) => signal.game.id === currentId);

  return (
    <section
      aria-label="Upset radar"
      className="flex min-w-0 flex-[1_1_23.75rem] flex-col"
    >
      <SectionHead
        title="Upset radar"
        note={signals.length > 0 ? radarNote(signals.length) : undefined}
      />
      <div className="bs-sheet flex flex-auto flex-col">
        {signals.length > 0 ? (
          <>
            {signals.map((signal) => (
              <SignalCard
                key={signal.game.id}
                signal={signal}
                pressed={signal.game.id === currentId}
                today={today}
                onSelect={onSelect}
              />
            ))}
            <p className="m-0 border-t border-hair px-5 pb-[0.8125rem] pt-3 text-sm text-ink-2">
              {radarFootnote(viewing, signals.length)}
            </p>
          </>
        ) : oddsQuery.isLoading ? (
          <SheetNote status>Scanning the slate for upset signals…</SheetNote>
        ) : oddsQuery.isError && oddsById === undefined ? (
          <SheetNote onRetry={() => void oddsQuery.refetch()}>
            Couldn&rsquo;t scan the slate for upset signals.
          </SheetNote>
        ) : (
          <SheetNote>No upset signals right now.</SheetNote>
        )}
      </div>
    </section>
  );
}
