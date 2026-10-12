import { SectionHead } from "../../components/bs";
import type { Game, GameLineup, Player, TeamLineup } from "../../types";
import { lineupGroups, slotOrder, statusLabel } from "./model";
import { SheetNote, SideBlock, SubHead } from "./parts";

const NUMERAL = "font-condensed text-lg font-semibold leading-6 tabular-nums";

/**
 * One side's projected lineup as a table: the batting order for baseball,
 * and for the other sports the players grouped by unit (goalkeeper,
 * defence, midfield, attack). A side with no stored squad says so, as the
 * old view did, so the two halves stay balanced.
 */
function SideLineup({
  game,
  side,
  lineup,
}: {
  game: Game;
  side: "away" | "home";
  lineup: TeamLineup | null;
}) {
  const team = game[side];
  const groups = lineup === null ? [] : lineupGroups(game.sport, lineup);
  const ordered = game.sport === "baseball";
  // One group is the whole lineup: its name goes in the band, not in a row.
  const banded = groups.length > 1;
  const columns = ordered ? 4 : 3;
  return (
    <div className="flex min-w-0 flex-[1_1_21.25rem] flex-col bg-sheet">
      <SubHead
        title={
          <>
            <SideBlock side={team} leagueId={game.league_id} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{team.name}</span>
          </>
        }
        summary={
          lineup?.formation != null && lineup.formation !== ""
            ? `Formation ${lineup.formation}`
            : groups.length === 1
              ? groups[0].label
              : undefined
        }
        rule="bottom"
      />
      {lineup === null || groups.length === 0 ? (
        <SheetNote>
          Lineup unavailable — follow this team to see its squad.
        </SheetNote>
      ) : (
        <table
          aria-label={`${team.name} projected lineup`}
          className="w-full border-collapse"
        >
          <thead>
            <tr className="bs-label text-left">
              {ordered && (
                <th scope="col" className="w-12 py-2 pl-5 pr-2 font-semibold">
                  <span aria-hidden="true">#</span>
                  <span className="sr-only">Order</span>
                </th>
              )}
              <th
                scope="col"
                className={
                  "w-16 py-2 pr-2 font-semibold " + (ordered ? "pl-2" : "pl-5")
                }
              >
                Pos
              </th>
              <th scope="col" className="p-2 font-semibold">
                Player
              </th>
              <th
                scope="col"
                className="w-16 py-2 pl-2 pr-5 text-right font-semibold"
              >
                No.
              </th>
            </tr>
          </thead>
          {groups.map((group) => (
            <tbody key={group.label}>
              {banded && (
                <tr className="border-t border-hair">
                  <th
                    scope="rowgroup"
                    colSpan={columns}
                    className="bs-label bg-tint px-5 py-1.5 text-left font-semibold"
                  >
                    {group.label}
                  </th>
                </tr>
              )}
              {group.slots.map((slot, index) => (
                <tr key={slot.player.id} className="border-t border-hair">
                  {ordered && (
                    <td className={`py-2 pl-5 pr-2 text-muted ${NUMERAL}`}>
                      {slotOrder(slot, index, game.sport)}
                    </td>
                  )}
                  <td
                    className={
                      "py-2 pr-2 font-condensed text-base font-semibold uppercase leading-6 tracking-[0.06em] text-ink-2 " +
                      (ordered ? "pl-2" : "pl-5")
                    }
                  >
                    {slot.role}
                  </td>
                  <th
                    scope="row"
                    className="p-2 text-left font-semibold [overflow-wrap:anywhere]"
                  >
                    {slot.player.name}
                  </th>
                  <td className={`py-2 pl-2 pr-5 text-right text-ink-2 ${NUMERAL}`}>
                    {slot.player.jersey_number ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      )}
      {lineup !== null && lineup.bench.length > 0 && (
        <dl className="m-0 mt-auto flex flex-wrap items-baseline gap-x-3.5 gap-y-0.5 border-t-2 border-ink px-5 py-3">
          <dt className="bs-label">Bench</dt>
          <dd className="m-0 min-w-0 text-sm text-ink-2 [overflow-wrap:anywhere]">
            {lineup.bench.map((player) => player.name).join(", ")}
          </dd>
        </dl>
      )}
    </div>
  );
}

/**
 * The projected lineups, kept from the old view and redrawn as Box Score
 * tables: every player, role, shirt number, batting order, formation and
 * bench the old view showed, with its disclaimer beside the tab and its
 * "lineup unavailable" line for a side with no stored squad.
 *
 * The shared LineupView (still used by the game detail modal) is not
 * embedded here. On this page its jersey badges and team chips measured
 * 2.2:1 to 3.5:1, its type is 9px and 11px set in px, and its pitch is an
 * illustration in colours of its own; none of that holds in four themes
 * or at kiosk scale. Left out with it: the pitch drawing and the round
 * headshots.
 */
export function Lineups({ game, lineup }: { game: Game; lineup: GameLineup }) {
  return (
    <section aria-label="Projected lineups">
      <SectionHead
        title="Projected lineups"
        note="Arranged from each stored squad by position: a depth chart, not a confirmed starting lineup"
      />
      {/* The hairline between the two halves is the sheet's own background
          showing through a 1px gap, side by side or stacked. */}
      <div className="bs-sheet flex flex-wrap gap-px bg-hair">
        <SideLineup game={game} side="away" lineup={lineup.away} />
        <SideLineup game={game} side="home" lineup={lineup.home} />
      </div>
    </section>
  );
}

/** One side's unavailable players as a table: who, where they play, how they are. */
function InjuryTable({
  game,
  side,
  players,
}: {
  game: Game;
  side: "away" | "home";
  players: readonly Player[];
}) {
  const team = game[side];
  return (
    <div className="flex min-w-0 flex-[1_1_21.25rem] flex-col bg-sheet">
      <SubHead
        title={
          <>
            <SideBlock side={team} leagueId={game.league_id} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{team.name}</span>
          </>
        }
        summary={`${players.length} listed`}
        rule="bottom"
      />
      {/* Fixed columns: a long status note wraps in its own column and
          does not squeeze the name beside it into broken words. */}
      <table
        aria-label={`${team.name} injuries`}
        className="w-full table-fixed border-collapse"
      >
        <thead>
          <tr className="bs-label text-left">
            <th scope="col" className="w-[42%] py-2 pl-5 pr-2 font-semibold">
              Player
            </th>
            <th scope="col" className="w-12 p-2 font-semibold">
              Pos
            </th>
            <th scope="col" className="py-2 pl-2 pr-5 font-semibold">
              Status
            </th>
          </tr>
        </thead>
        <tbody>
          {players.map((player) => (
            <tr key={player.id} className="border-t border-hair align-top">
              <th
                scope="row"
                className="py-2.5 pl-5 pr-2 text-left font-semibold [overflow-wrap:anywhere]"
              >
                {player.name}
              </th>
              <td className="px-2 py-2.5 font-condensed text-base font-semibold uppercase tracking-[0.06em] text-ink-2">
                {player.position ?? ""}
              </td>
              <td className="py-2.5 pl-2 pr-5">
                <span className="font-semibold">{statusLabel(player.status)}</span>
                {player.status_detail !== null && player.status_detail !== "" && (
                  <span className="block text-sm text-ink-2 [overflow-wrap:anywhere]">
                    {player.status_detail}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Injuries, kept from the old view: the players a followed side lists as
 * not active. A side with none listed gets no table; a side that is not
 * followed never has any (its squad is not stored), so nothing is claimed
 * about it.
 */
export function Injuries({
  game,
  away,
  home,
}: {
  game: Game;
  away: readonly Player[];
  home: readonly Player[];
}) {
  if (away.length === 0 && home.length === 0) return null;
  return (
    <section aria-label="Injuries">
      <SectionHead
        title="Injuries"
        note="Players not listed as active on a followed team’s stored squad"
      />
      {/* The hairline between the two tables is the sheet's own background
          showing through a 1px gap, side by side or stacked. */}
      <div className="bs-sheet flex flex-wrap gap-px bg-hair">
        {away.length > 0 && <InjuryTable game={game} side="away" players={away} />}
        {home.length > 0 && <InjuryTable game={game} side="home" players={home} />}
      </div>
    </section>
  );
}
