import { useId, useMemo } from "react";

import { SpotChip, teamCode } from "../../components/bs";
import Select, { type SelectOption } from "../../components/Select";
import type { Game } from "../../types";
import {
  joiner,
  moreGamesLabel,
  optionLabel,
  pickerLabel,
  pickerWhen,
  type PickerSplit,
} from "./model";
import { SideBlock, useIdentify } from "./parts";

/** One game: its two blocks, and under them whether it is live or when it is. */
function GameButton({
  game,
  pressed,
  today,
  onPick,
}: {
  game: Game;
  pressed: boolean;
  /** The viewer's day: a bare start time means this one. */
  today: string;
  onPick: (id: string) => void;
}) {
  const when = pickerWhen(game, today);
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={pickerLabel(game, when)}
      onClick={() => onPick(game.id)}
      className={
        // As tall as its tallest content (the Live now chip), so a row of
        // buttons and the dropdown beside it are one height and do not
        // change it when a game goes live. Two to a row on a phone, where
        // three 204px buttons would stack into a column taller than the
        // game they choose; never narrower than its two blocks, so on the
        // narrowest phones the dropdown takes a line of its own instead.
        "bs-btn min-h-[4.8125rem] min-w-[12.75rem] flex-col items-start justify-center gap-[0.4375rem] pb-[0.5625rem] pl-3 pr-4 pt-2 text-left max-sm:min-w-fit max-sm:flex-[1_1_40%] max-sm:px-2.5" +
        // Inside the pressed block the muted tones mean their on-ink ones.
        (pressed ? " bs-inverse" : "")
      }
    >
      <span className="flex items-center gap-2 text-[1.1875rem] leading-none tracking-[0.02em]">
        <SideBlock side={game.away} leagueId={game.league_id} size={40} />
        {/* ink-2, not muted: muted falls under the contrast floor on the
            fill the button takes under the pointer. */}
        <span className="text-[1.0625rem] font-semibold normal-case tracking-normal text-ink-2">
          {joiner(game.sport)}
        </span>
        <SideBlock side={game.home} leagueId={game.league_id} size={40} />
      </span>
      {when.live ? (
        <SpotChip>{when.text}</SpotChip>
      ) : (
        <span className="text-[1.0625rem] font-semibold leading-none tracking-[0.06em] text-ink-2">
          {when.text}
        </span>
      )}
    </button>
  );
}

/** The games that did not get a button, in the shared dropdown. */
function MoreGames({
  games,
  value,
  today,
  onPick,
}: {
  games: readonly Game[];
  value: string | undefined;
  today: string;
  onPick: (id: string) => void;
}) {
  const identify = useIdentify();
  const options: SelectOption[] = useMemo(
    () =>
      games.map((game) => {
        const away = identify(game.away, game.league_id);
        return {
          id: game.id,
          label: optionLabel(game, pickerWhen(game, today)),
          // The away side's block, where its crest used to be. Its code is
          // worked out here from the side's own name: the dropdown would
          // otherwise read one off the option's words ("SVA" for "Sinner
          // vs Alcaraz").
          abbreviation: teamCode(game.away.name, away.abbreviation),
          color: away.color,
        };
      }),
    [games, today, identify],
  );
  return (
    // Level with the buttons beside it (the same floor as theirs). The
    // trigger is the Select's own button; its height is the one thing set
    // from out here.
    <div className="flex max-w-full [&_button[aria-haspopup=listbox]]:min-h-[4.8125rem]">
      <Select
        options={options}
        value={value}
        onChange={onPick}
        ariaLabel="Choose another game"
        placeholder={moreGamesLabel(games.length)}
      />
    </div>
  );
}

/**
 * "Choose a game": a button for each of the first few games (the one on
 * screen always among them), and a dropdown for the rest.
 */
export default function GamePicker({
  split,
  currentId,
  today,
  onPick,
}: {
  split: PickerSplit;
  currentId: string | undefined;
  /** The viewer's own day (`viewerDay`), not the server's. */
  today: string;
  onPick: (id: string) => void;
}) {
  const labelId = useId();
  return (
    <div
      role="group"
      aria-labelledby={labelId}
      className="flex min-w-0 flex-col gap-1.5 max-sm:w-full"
    >
      <span id={labelId} className="bs-label leading-none">
        Choose a game
      </span>
      <div className="flex flex-wrap gap-2">
        {split.buttons.map((game) => (
          <GameButton
            key={game.id}
            game={game}
            pressed={game.id === currentId}
            today={today}
            onPick={onPick}
          />
        ))}
        {split.rest.length > 0 && (
          <MoreGames
            games={split.rest}
            value={currentId}
            today={today}
            onPick={onPick}
          />
        )}
      </div>
    </div>
  );
}
