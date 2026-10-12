import type {
  Game,
  GameLineup,
  GameSide,
  LineupSlot,
  Sport,
  TeamLineup,
} from "../types";
import { leagueFallbackColor } from "./GameRow";
import TeamLogo from "./TeamLogo";
import PlayerAvatar from "./PlayerAvatar";

/**
 * Roster-derived, sport-specific projected lineups for a matchup. The backend
 * arranges each team's real roster into the sport's starting shape (positional,
 * NOT a confirmed gameday XI — no free feed supplies those); this renders it on
 * a sport-appropriate surface: a pitch for soccer, a court for basketball /
 * volleyball, lines for hockey, a batting card for baseball, units for football.
 *
 * Away team on top, home below (matching the box score). A side with no stored
 * roster (an unfollowed opponent / a whole-competition team) is shown as a small
 * "lineup unavailable" note rather than omitted, so the matchup stays balanced.
 */

/** Per-sport surface styling + the unit rows to lay out, back row first. */
const SURFACE: Partial<
  Record<Sport, { className: string; units: { key: string; label: string }[] }>
> = {
  soccer: {
    className:
      "bg-[repeating-linear-gradient(0deg,#16331f,#16331f_28px,#173a22_28px,#173a22_56px)] border-[#1e4a2c]",
    units: [
      { key: "GK", label: "Goalkeeper" },
      { key: "DEF", label: "Defence" },
      { key: "MID", label: "Midfield" },
      { key: "FWD", label: "Attack" },
    ],
  },
  basketball: {
    className: "bg-[#3b2a1a] border-[#5a4126]",
    units: [
      { key: "G", label: "Guards" },
      { key: "F", label: "Forwards" },
      { key: "C", label: "Center" },
    ],
  },
  hockey: {
    className: "bg-[#10283a] border-[#1d4563]",
    units: [
      { key: "G", label: "Goalie" },
      { key: "D", label: "Defense" },
      { key: "F", label: "Forwards" },
    ],
  },
  volleyball: {
    className: "bg-[#3a3210] border-[#5c5019]",
    units: [
      { key: "S", label: "Setter" },
      { key: "L", label: "Libero" },
      { key: "MB", label: "Middle" },
      { key: "OH", label: "Outside" },
      { key: "OPP", label: "Opposite" },
    ],
  },
  football: {
    className: "bg-[#12301c] border-[#1e4a2c]",
    units: [
      { key: "OFF", label: "Offense" },
      { key: "DEF", label: "Defense" },
      { key: "ST", label: "Special Teams" },
    ],
  },
};

function lastName(name: string): string {
  const parts = name.trim().split(/\s+/);
  return parts.length > 1 ? parts.slice(1).join(" ") : name;
}

function sideColor(side: GameSide, sport: Sport): string {
  return side.color ?? leagueFallbackColor(sport);
}

export default function LineupView({
  lineup,
  game,
}: {
  lineup: GameLineup;
  game: Game;
}) {
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Projected lineups
      </h3>
      <p className="mb-3 text-[11px] leading-snug text-zinc-500">
        Arranged from each squad by position — a depth-chart view, not a
        confirmed starting lineup.
      </p>
      <div className="space-y-4">
        <SideLineup teamLineup={lineup.away} side={game.away} sport={game.sport} />
        <SideLineup teamLineup={lineup.home} side={game.home} sport={game.sport} />
      </div>
    </div>
  );
}

function SideLineup({
  teamLineup,
  side,
  sport,
}: {
  teamLineup: TeamLineup | null;
  side: GameSide;
  sport: Sport;
}) {
  const color = sideColor(side, sport);
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <TeamLogo
          logoUrl={side.logo_url}
          name={side.name}
          abbreviation={side.abbreviation}
          color={color}
          size="xs"
        />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-100">
          {side.name}
        </span>
        {teamLineup?.formation != null && (
          <span className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-zinc-300">
            {teamLineup.formation}
          </span>
        )}
      </div>
      {teamLineup === null ? (
        <p className="rounded-lg border border-dashed border-zinc-800 px-3 py-4 text-center text-xs text-zinc-500">
          Lineup unavailable — follow this team to see its squad.
        </p>
      ) : sport === "baseball" ? (
        <BattingCard teamLineup={teamLineup} color={color} />
      ) : (
        <FieldLineup teamLineup={teamLineup} sport={sport} color={color} />
      )}
      {teamLineup !== null && teamLineup.bench.length > 0 && (
        <Bench names={teamLineup.bench.map((p) => p.name)} />
      )}
    </div>
  );
}

/** Soccer / basketball / hockey / volleyball / football: chips on unit rows. */
function FieldLineup({
  teamLineup,
  sport,
  color,
}: {
  teamLineup: TeamLineup;
  sport: Sport;
  color: string;
}) {
  const config = SURFACE[sport];
  const units = config?.units ?? inferredUnits(teamLineup.slots);
  return (
    // A playing surface is an illustration with its own fixed, opaque
    // colours, the same in every theme; names on it are white. The dark
    // scope makes any token-coloured text inside follow suit.
    <div
      data-theme="dark"
      className={
        "flex flex-col gap-2 rounded-lg border px-2 py-3 " +
        (config?.className ?? "border-[#3a424c] bg-[#1c222a]")
      }
    >
      {units.map((unit) => {
        const slots = teamLineup.slots.filter((s) => s.unit === unit.key);
        if (slots.length === 0) return null;
        return (
          <div
            key={unit.key}
            className="flex flex-wrap items-start justify-center gap-x-3 gap-y-2"
          >
            {slots.map((slot) => (
              <PlayerChip key={slot.player.id} slot={slot} color={color} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

/** Baseball: a numbered lineup card with field positions. */
function BattingCard({
  teamLineup,
  color,
}: {
  teamLineup: TeamLineup;
  color: string;
}) {
  return (
    <ol className="overflow-hidden rounded-lg border border-zinc-800">
      {teamLineup.slots.map((slot, index) => (
        <li
          key={slot.player.id}
          className={
            "flex items-center gap-2.5 px-3 py-1.5 text-sm " +
            (index % 2 === 0 ? "bg-zinc-900/60" : "bg-zinc-900/30")
          }
        >
          <span className="w-4 shrink-0 text-right text-xs tabular-nums text-zinc-500">
            {slot.order ?? index + 1}
          </span>
          <span
            className="w-9 shrink-0 rounded px-1 py-0.5 text-center text-[11px] font-semibold uppercase tabular-nums"
            style={{ backgroundColor: color + "33", color: "var(--bs-ink)" }}
          >
            {slot.role}
          </span>
          <span className="min-w-0 flex-1 truncate text-zinc-200">
            {slot.player.name}
          </span>
          {slot.player.jersey_number != null && (
            <span className="shrink-0 text-xs tabular-nums text-zinc-500">
              #{slot.player.jersey_number}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

function PlayerChip({ slot, color }: { slot: LineupSlot; color: string }) {
  // Names sit on a colored field (pitch/court/ice); a dark text-shadow keeps
  // them legible regardless of how dark or light the surface is.
  const shadow = { textShadow: "0 1px 3px rgba(0,0,0,0.9)" };
  return (
    <div className="flex w-16 flex-col items-center gap-0.5 text-center">
      <div className="relative mb-1 rounded-full ring-2" style={{ color }}>
        <PlayerAvatar photoUrl={slot.player.photo_url} name={slot.player.name} />
        {slot.player.jersey_number && (
          <span className="absolute -bottom-1 -right-1 rounded-full px-1 text-[9px] font-bold text-white ring-1 ring-white/40" style={{ backgroundColor: color }}>
            {slot.player.jersey_number}
          </span>
        )}
      </div>
      <span
        className="max-w-full truncate text-[11px] font-medium leading-tight text-white"
        style={shadow}
      >
        {lastName(slot.player.name)}
      </span>
      <span
        className="text-[9px] font-semibold uppercase tracking-wide text-zinc-200/90"
        style={shadow}
      >
        {slot.role}
      </span>
    </div>
  );
}

function Bench({ names }: { names: string[] }) {
  return (
    <p className="mt-1.5 text-[11px] leading-snug text-zinc-500">
      <span className="font-semibold uppercase tracking-wide text-zinc-500">
        Bench:{" "}
      </span>
      {names.join(", ")}
    </p>
  );
}

/** Fallback unit list when a sport has no preset surface (keeps order stable). */
function inferredUnits(slots: LineupSlot[]): { key: string; label: string }[] {
  const seen: string[] = [];
  for (const slot of slots) {
    if (!seen.includes(slot.unit)) seen.push(slot.unit);
  }
  return seen.map((key) => ({ key, label: key }));
}
