import { OutcomeSquare, SectionHead, TeamBlock } from "../../components/bs";
import type { Game, OnThisDayTeam, Team } from "../../types";
import { outcomeFor, type Outcome, type SideKey } from "./model";
import { anniversaryYear, winningSide } from "./onThisDay";

/** The followed team's side in one of its own historical games. */
function teamSide(game: Game, team: OnThisDayTeam): SideKey | null {
  if (game.home.team_id === team.team_id) return "home";
  if (game.away.team_id === team.team_id) return "away";
  if (game.home.name === team.team_name) return "home";
  if (game.away.name === team.team_name) return "away";
  return null;
}

/**
 * One historical final: the year, the scoreline away side first with the
 * winner in ink, and the result as the followed team saw it. Deliberately
 * NOT interactive: history games are assembled on demand and never stored,
 * so there is no box score behind them to open.
 */
function Row({ game, team }: { game: Game; team: OnThisDayTeam }) {
  const winner = winningSide(game);
  const side = teamSide(game, team);
  const outcome: Outcome | null = side === null ? null : outcomeFor(game, side);
  const part = (key: SideKey) => (
    <span className={winner === key ? "font-bold text-ink" : undefined}>
      {game[key].name} {game[key].score ?? "—"}
    </span>
  );
  return (
    <div className="flex items-center gap-3.5 border-t border-hair px-4 py-[0.4375rem]">
      <span className="w-11 flex-none font-condensed text-[1.375rem] font-bold leading-none text-muted">
        {anniversaryYear(game)}
      </span>
      <span className="min-w-0 flex-1 text-ink-2">
        {part("away")}, {part("home")}
      </span>
      {outcome !== null && <OutcomeSquare result={outcome} size={24} />}
    </div>
  );
}

/**
 * Followed teams' games on this date in past seasons, one group per team.
 * The caller decides whether there is anything to show; this only draws it.
 */
export default function OnThisDaySection({
  teams,
  teamsById,
  dayLabel,
}: {
  teams: readonly OnThisDayTeam[];
  teamsById: Record<string, Team>;
  /** "July 30" */
  dayLabel: string;
}) {
  return (
    <section aria-label="On this day">
      <SectionHead
        title="On this day"
        aside={<span className="bs-note">{dayLabel} in past seasons</span>}
      />
      <div className="bs-sheet tabular-nums">
        {teams.map((team, index) => {
          const known = teamsById[team.team_id];
          return (
            <div
              key={team.team_id}
              className={index > 0 ? "border-t-2 border-ink" : undefined}
            >
              <div className="flex items-center gap-2.5 px-4 pb-2 pt-3 font-bold">
                <TeamBlock
                  name={team.team_name}
                  abbreviation={known?.abbreviation}
                  color={known?.color}
                />
                {team.team_name}
              </div>
              {team.games.map((game) => (
                <Row key={game.id} game={game} team={team} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}
