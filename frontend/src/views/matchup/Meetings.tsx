import {
  FollowMark,
  Icon,
  MarkedName,
  OutcomeSquare,
  SectionHead,
} from "../../components/bs";
import { sportWords } from "../../lib/labels";
import { formatShortDate, localDateKey, longDay } from "../../lib/time";
import type { Game, GameSide, HeadToHeadRecord } from "../../types";
import { isFollowedSide } from "../today/model";
import {
  meetingDate,
  meetingDetail,
  meetingWinner,
  recordLine,
  seriesHeadline,
  seriesNote,
  seriesTally,
  sideNoun,
  splitSeries,
  venueSentence,
  type SideKey,
} from "./model";
import { BoxScoreLink, SideBlock, SpareRoom, SubHead } from "./parts";

const key = (name: string): string => name.trim().toLowerCase();

/**
 * A score in its square: the winner's on the ink block, the loser's plain
 * and muted, and both on the mid-tone draw fill when nobody won.
 */
function ScoreSquare({
  value,
  state,
}: {
  value: number | null;
  state: "win" | "loss" | "draw";
}) {
  const box =
    "mx-auto h-[1.875rem] w-fit min-w-[1.875rem] px-1 text-2xl leading-none tabular-nums";
  if (state === "loss") {
    return (
      <span
        className={`flex items-center justify-center font-condensed font-medium text-muted ${box}`}
      >
        {value ?? ""}
      </span>
    );
  }
  return (
    <span className={`bs-wl bs-wl--${state === "win" ? "w" : "d"} flex ${box}`}>
      {value ?? ""}
    </span>
  );
}

/**
 * The two sides' meetings, as the mockup's season series: who has won more
 * of them, then each meeting oldest first with its winner set in bold and
 * its score on the ink block.
 *
 * It is not called a season series, because the API cannot scope meetings
 * to a season: these are the newest few on record, whenever they were
 * played. Older ones follow in a compact list, and the record over the
 * last few seasons closes the sheet. "Box score" is offered only where the
 * game is stored (the others would open on a 404).
 */
export default function Meetings({
  game,
  meetings,
  record,
  openable,
  onOpen,
  now,
}: {
  /** The game on screen: the tally is counted for its two sides. */
  game: Game;
  /** Every meeting known, newest first. */
  meetings: readonly Game[];
  record: HeadToHeadRecord | null;
  /** Ids of the meetings a box score can be opened for. */
  openable: ReadonlySet<string>;
  onOpen: (game: Game) => void;
  now: Date;
}) {
  if (meetings.length === 0) return null;
  const words = sportWords(game.sport);
  const { shown, earlier } = splitSeries(meetings);
  const tally = seriesTally(game, shown);
  const head = seriesHeadline(game, tally, shown.length);
  const venue = venueSentence(shown, words);
  const scored = words.tally !== null;
  // "Team", "Player", "Fighter": what the sport's two sides are.
  const noun = sideNoun(game.sport);
  const sideHead = noun.charAt(0).toUpperCase() + noun.slice(1);
  const anyLink = shown.some((meeting) => openable.has(meeting.id));
  const leagueId = game.league_id;
  // The score is the last column wherever the link has none of its own: in
  // a narrow sheet, and at any width when no meeting can be opened. There
  // it carries the sheet's own inset (the widths include the padding).
  const scoreColumn =
    "w-16 pl-0 pr-3 " +
    (anyLink ? "@[24rem]:w-[3.25rem] @[24rem]:pr-0" : "@[24rem]:w-[4.5rem] @[24rem]:pr-5");

  // A past meeting tags only the side that was asked for, so "followed" is
  // read off today's two sides by name.
  const followedNames = new Set(
    (["home", "away"] as const)
      .filter((side) => isFollowedSide(game, game[side]))
      .map((side) => key(game[side].name)),
  );
  const followed = (side: GameSide) =>
    followedNames.has(key(side.name)) ||
    (side.team_id !== null && game.followed_team_ids.includes(side.team_id));

  const team = (meeting: Game, side: SideKey, last: boolean) => {
    const winner = meetingWinner(meeting);
    const tone =
      winner === side
        ? "font-bold text-ink"
        : winner === "draw" || winner === null
          ? "font-medium text-ink"
          : "text-ink-2";
    return (
      <td className={"px-2 " + (last ? "pb-3 pt-0.5" : "pb-0.5 pt-3")}>
        <span className={`flex min-h-[1.875rem] items-center gap-2.5 ${tone}`}>
          <SideBlock side={meeting[side]} leagueId={leagueId} />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            <MarkedName
              name={meeting[side].name}
              mark={
                followed(meeting[side]) && (
                  <FollowMark className="ml-2.5 align-middle" />
                )
              }
            />
          </span>
        </span>
      </td>
    );
  };

  const result = (meeting: Game, side: SideKey, last: boolean) => {
    const winner = meetingWinner(meeting);
    return (
      <td className={`${scoreColumn} ` + (last ? "pb-3 pt-0.5" : "pb-0.5 pt-3")}>
        {scored ? (
          <ScoreSquare
            value={meeting[side].score}
            state={
              winner === side ? "win" : winner === "draw" ? "draw" : "loss"
            }
          />
        ) : winner === side ? (
          <OutcomeSquare result="W" size={30} className="mx-auto" />
        ) : winner === "draw" ? (
          <OutcomeSquare result="D" size={30} className="mx-auto" />
        ) : null}
      </td>
    );
  };

  const venueLine =
    venue === null ? null : (
      <p className="m-0 flex items-center gap-2 px-5 py-3 text-sm font-medium text-ink-2">
        <Icon size={16}>
          <path d="M12 21s-7-6.1-7-11a7 7 0 0 1 14 0c0 4.9-7 11-7 11z" />
          <circle cx="12" cy="10" r="2.5" />
        </Icon>
        {venue}
      </p>
    );

  const link = (meeting: Game) =>
    openable.has(meeting.id) ? (
      <BoxScoreLink
        label={`Box score, ${longDay(meeting.start_time)}`}
        onOpen={() => onOpen(meeting)}
      />
    ) : null;

  return (
    <section
      aria-label="Recent meetings"
      className="flex min-w-0 flex-[1_1_21.25rem] flex-col"
    >
      <SectionHead
        title="Recent meetings"
        note={seriesNote(game, tally) ?? undefined}
      />
      <div className="bs-sheet @container flex flex-auto flex-col">
        {tally.played > 0 && (
          <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2.5 border-b-2 border-ink px-5 py-[1.125rem]">
            {tally.leader !== null && (
              <SideBlock
                side={game[tally.leader]}
                leagueId={leagueId}
                size={52}
              />
            )}
            <div
              aria-hidden="true"
              className="font-condensed text-[4.5rem] font-bold leading-[0.8] tracking-[0.01em] tabular-nums"
            >
              {tally.ahead}
              <span className="font-semibold text-muted">–{tally.behind}</span>
            </div>
            <div className="min-w-0">
              <div className="font-condensed text-2xl font-bold uppercase leading-none [overflow-wrap:anywhere]">
                {head.headline}
                <span className="sr-only">
                  {" "}
                  {tally.ahead} to {tally.behind}
                </span>
              </div>
              <div className="mt-1 text-sm text-ink-2">{head.sub}</div>
            </div>
          </div>
        )}

        <table
          aria-label="Recent meetings, oldest first"
          className="w-full border-collapse tabular-nums"
        >
          <thead>
            <tr className="bs-label text-left">
              <th scope="col" className="py-2 pl-5 pr-2 font-semibold">
                Date
              </th>
              <th scope="col" className="p-2 font-semibold">
                {sideHead}
              </th>
              <th
                scope="col"
                className={`${scoreColumn} py-2 text-center font-semibold`}
              >
                {scored ? "Score" : "Result"}
              </th>
              {anyLink && (
                <td className="hidden py-2 pl-2 pr-5 @[24rem]:table-cell" />
              )}
            </tr>
          </thead>
          {shown.map((meeting) => {
            const when = meetingDate(meeting.start_time, now);
            // For two people: the tournament or card, and how a fight ended.
            const detail = meetingDetail(meeting);
            return (
              <tbody key={meeting.id}>
                <tr className="border-t border-hair">
                  <th
                    scope="rowgroup"
                    rowSpan={2}
                    className="whitespace-nowrap py-3 pl-5 pr-2 text-left align-middle font-semibold"
                  >
                    <span className="bs-label block">{when.top}</span>
                    <time dateTime={localDateKey(meeting.start_time)}>
                      {when.date}
                    </time>
                  </th>
                  {team(meeting, "away", false)}
                  {result(meeting, "away", false)}
                  {anyLink && (
                    <td
                      rowSpan={2}
                      className="hidden py-0 pl-2 pr-5 text-right @[24rem]:table-cell"
                    >
                      {link(meeting)}
                    </td>
                  )}
                </tr>
                <tr>
                  {team(meeting, "home", true)}
                  {result(meeting, "home", true)}
                </tr>
                {/* In a narrow sheet the link has no column of its own: it
                    takes a line under the two sides instead. */}
                {(openable.has(meeting.id) || detail !== null) && (
                  <tr
                    className={
                      detail !== null ? undefined : "@[24rem]:hidden"
                    }
                  >
                    <td />
                    <td
                      colSpan={anyLink ? 3 : 2}
                      className="pb-2 pl-2 pr-5 text-sm text-ink-2"
                    >
                      <span className="flex flex-wrap items-center justify-between gap-x-3">
                        <span>{detail}</span>
                        <span className="@[24rem]:hidden">{link(meeting)}</span>
                      </span>
                    </td>
                  </tr>
                )}
              </tbody>
            );
          })}
        </table>

        {/* With older meetings listed after it, the venue line goes straight
            under the meetings it speaks of, so it is never read as a line
            about the older ones. */}
        {earlier.length > 0 && venueLine !== null && (
          <div className="border-t-2 border-ink">{venueLine}</div>
        )}

        {earlier.length > 0 && (
          <>
            <SubHead title="Earlier meetings" summary="Newest first" />
            <ul className="m-0 list-none p-0">
              {earlier.map((meeting) => {
                const winner = meetingWinner(meeting);
                const figure = (side: SideKey) =>
                  winner === side || winner === "draw"
                    ? "font-bold text-ink"
                    : "font-medium text-muted";
                return (
                  <li
                    key={meeting.id}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-hair px-5 py-2 first:border-t-0"
                  >
                    <time
                      dateTime={localDateKey(meeting.start_time)}
                      className="w-[6.25rem] flex-none text-sm text-ink-2"
                    >
                      {formatShortDate(meeting.start_time)},{" "}
                      {new Date(meeting.start_time).getFullYear()}
                    </time>
                    <span className="sr-only">
                      {meeting.away.name}
                      {scored ? ` ${meeting.away.score ?? ""}` : ""},{" "}
                      {meeting.home.name}
                      {scored ? ` ${meeting.home.score ?? ""}` : ""}
                    </span>
                    <span
                      aria-hidden="true"
                      className="flex items-center gap-2 font-condensed text-[1.375rem] leading-none tabular-nums"
                    >
                      <SideBlock side={meeting.away} leagueId={leagueId} />
                      {scored && (
                        <>
                          <span className={figure("away")}>
                            {meeting.away.score}
                          </span>
                          <span className="text-muted">–</span>
                          <span className={figure("home")}>
                            {meeting.home.score}
                          </span>
                        </>
                      )}
                      <SideBlock side={meeting.home} leagueId={leagueId} />
                    </span>
                    {openable.has(meeting.id) && (
                      <span className="ml-auto">{link(meeting)}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}

        {((earlier.length === 0 && venueLine !== null) || record !== null) && (
          <div className="border-t-2 border-ink">
            {earlier.length === 0 && venueLine}
            {record !== null && (
              <dl
                className={
                  "m-0 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-5 py-3" +
                  (earlier.length === 0 && venueLine !== null
                    ? " border-t border-hair"
                    : "")
                }
              >
                <dt className="bs-label">{recordLine(record, game.sport).label}</dt>
                <dd className="m-0 text-sm font-semibold">
                  {recordLine(record, game.sport).value}
                </dd>
              </dl>
            )}
          </div>
        )}
        <SpareRoom />
      </div>
    </section>
  );
}
