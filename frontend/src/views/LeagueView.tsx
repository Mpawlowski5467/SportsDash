import { useEffect, useId, useMemo, useRef, useState } from "react";

import { Loading, Notice, Segmented } from "../components/bs";
import { useManageTeams } from "../components/ManageTeamsContext";
import { useNavigationRequest } from "../components/NavigateContext";
import Select from "../components/Select";
import { useStandings, useTeams } from "../hooks";
import { errorText } from "../lib/errorText";
import {
  CURRENT_SEASON,
  seasonChoices,
  seasonOptions,
  seasonTitle,
  supportsArchives,
} from "../lib/seasons";
import BracketView from "./BracketView";
import LeadersView from "./LeadersView";
import { liveSeasonKey, withoutLiveSeason } from "./league/model";
import { ControlsSlotContext } from "./league/parts";
import StandingsView from "./StandingsView";

type Section = "standings" | "leaders" | "bracket";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "standings", label: "Standings" },
  { id: "leaders", label: "Leaders" },
  { id: "bracket", label: "Bracket" },
];

/**
 * League hub: one league and one season, chosen at the top of the page,
 * seen three ways (Standings, Leaders, Bracket) behind a sub-view switch.
 *
 * The page owns everything the three views share: which league, which
 * season, and the row the switch sits on. A view renders its own controls
 * (Standings' conference toggle and legend) into that row. It also owns the
 * two small choices made inside Standings, so they survive a look at
 * another sub-view and back.
 */
export default function LeagueView() {
  const openManageTeams = useManageTeams();
  const teamsQuery = useTeams();
  const request = useNavigationRequest("league");

  const [section, setSection] = useState<Section>("standings");
  // A request that is already waiting when the view first mounts ("Full
  // standings" on Today) is where the view starts, with no first frame on
  // another league. An id that is not a followed league falls back below.
  const [selected, setSelected] = useState<string | undefined>(
    () => request?.leagueId,
  );
  // Past-season archive picker (espn team sports only).
  const [seasonSel, setSeasonSel] = useState<string>(CURRENT_SEASON);
  const [group, setGroup] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [controlsSlot, setControlsSlot] = useState<HTMLElement | null>(null);
  const panelId = useId();

  const leagues = useMemo(
    () => teamsQuery.data?.leagues ?? [],
    [teamsQuery.data?.leagues],
  );

  // A later request (the view is already mounted, hidden) is applied once,
  // keyed on its nonce, so the user's own next choice is never overridden.
  const applied = useRef<number | null>(
    request?.leagueId !== undefined ? request.nonce : null,
  );
  useEffect(() => {
    if (request === null || request.leagueId === undefined) return;
    if (applied.current === request.nonce) return;
    if (teamsQuery.data === undefined) return; // decide once the leagues are known
    applied.current = request.nonce;
    if (!leagues.some((league) => league.id === request.leagueId)) return;
    setSelected(request.leagueId);
    setSeasonSel(CURRENT_SEASON);
    // A conference picked earlier would win over the one holding the
    // followed team, which is what "Full standings" is asking to see.
    setGroup(request.group ?? null);
    setSection("standings");
  }, [request, teamsQuery.data, leagues]);

  // Ignore a selection that no longer exists — re-running setup replaces
  // the followed leagues, which would leave `selected` pointing at a 404.
  const leagueId = leagues.some((league) => league.id === selected)
    ? selected
    : leagues[0]?.id;
  const league = leagues.find((entry) => entry.id === leagueId);
  const archives = supportsArchives(league);
  const seasonYear =
    archives && seasonSel !== CURRENT_SEASON ? Number(seasonSel) : undefined;

  // The season's name comes from the table itself: the live one names the
  // live option, the one on screen names the page.
  const liveSeason = useStandings(leagueId).data?.season;
  const liveTitle = seasonTitle(liveSeason);
  const shownTitle = seasonTitle(useStandings(leagueId, seasonYear).data?.season);
  const pageSeason =
    shownTitle ?? (seasonYear !== undefined ? `${seasonYear} season` : null);

  const showCurrentSeason = () => setSeasonSel(CURRENT_SEASON);

  const head = (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
      <h1 className="bs-title">League</h1>
      {league !== undefined && (
        <p className="bs-deck m-0">
          {[league.name, pageSeason].filter(Boolean).join(" · ")}
        </p>
      )}
    </div>
  );

  if (teamsQuery.isError && !teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        {head}
        <Notice
          title="League"
          alert
          action={{ label: "Retry", onClick: () => void teamsQuery.refetch() }}
        >
          Couldn&rsquo;t load your leagues
          {errorText(teamsQuery.error)}
        </Notice>
      </div>
    );
  }
  if (!teamsQuery.data) {
    return (
      <div className="flex flex-col gap-7">
        {head}
        <Loading label="Loading leagues" rows={6} className="max-w-[35rem]" />
      </div>
    );
  }
  if (league === undefined) {
    return (
      <div className="flex flex-col gap-7">
        {head}
        <Notice
          title="League"
          headline="No leagues followed yet"
          action={{ label: "Manage teams", onClick: openManageTeams }}
        >
          Follow a league to see its standings, leaders and bracket here.
        </Notice>
      </div>
    );
  }

  const current = SECTIONS.find((entry) => entry.id === section) ?? SECTIONS[0];
  // The archive key the live season is filed under is that season again
  // (for a soccer league under another name: "2026" is 2026-27), so it is
  // not offered beside the live option.
  const seasons = withoutLiveSeason(
    seasonChoices(
      seasonOptions(),
      CURRENT_SEASON,
      seasonSel,
      liveTitle,
      seasonYear !== undefined ? shownTitle : null,
    ),
    liveSeasonKey(liveSeason, league.sport, new Date().getFullYear()),
    seasonSel,
  );

  return (
    <div className="flex flex-col gap-7">
      <div className="flex flex-col gap-5">
        <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
          {head}
          <div className="flex max-w-full flex-wrap gap-2.5">
            <Select
              size="lg"
              options={leagues.map((entry) => ({ id: entry.id, label: entry.name }))}
              value={league.id}
              onChange={(id) => {
                setSelected(id);
                setSeasonSel(CURRENT_SEASON); // a new league starts on its live table
                // ...and on its own conference: two leagues can share the
                // names ("Western Conference" in the NBA and the NHL).
                setGroup(null);
              }}
              ariaLabel="Choose league"
            />
            {archives && (
              <Select
                size="lg"
                options={seasons}
                value={seasonSel}
                onChange={setSeasonSel}
                ariaLabel="Choose season"
              />
            )}
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-x-7 gap-y-3">
          <Segmented
            kind="tabs"
            label="League views"
            value={section}
            onChange={setSection}
            options={SECTIONS.map((entry) => ({
              value: entry.id,
              label: entry.label,
              controls: panelId,
            }))}
            className="max-sm:w-full max-sm:[&>*]:px-2"
          />
          {/* Where the sub-view puts its own controls; `contents`, so they
              are laid out as items of this row. */}
          <div ref={setControlsSlot} className="contents" />
        </div>
      </div>

      <ControlsSlotContext.Provider value={controlsSlot}>
        <div
          role="tabpanel"
          id={panelId}
          aria-label={current.label}
          className="flex flex-col gap-7"
        >
          {section === "standings" && (
            <StandingsView
              league={league}
              seasonYear={seasonYear}
              onCurrentSeason={showCurrentSeason}
              group={group}
              onGroup={setGroup}
              teamId={teamId}
              onTeam={setTeamId}
            />
          )}
          {section === "leaders" && (
            <LeadersView
              league={league}
              seasonYear={seasonYear}
              onCurrentSeason={showCurrentSeason}
            />
          )}
          {section === "bracket" && (
            <BracketView
              league={league}
              seasonYear={seasonYear}
              onCurrentSeason={showCurrentSeason}
            />
          )}
        </div>
      </ControlsSlotContext.Provider>
    </div>
  );
}
