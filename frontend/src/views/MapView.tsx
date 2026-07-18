import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import Supercluster from "supercluster";
import type { FeatureCollection, LineString } from "geojson";

import { useMap, useSchedule, useTeams } from "../hooks";
import { localDayOffset } from "../lib/time";
import { greatCircle, hasCoords, haversineKm } from "../lib/geo";
import {
  COMPETITION_RING,
  DEFAULT_COLOR,
  createClusterElement,
  createClusterTooltipElement,
  createMarkerElement,
  createPlaneElement,
  createTooltipElement,
  createVenueMarkerElement,
  createVenueTooltipElement,
  type MapMode,
} from "./map/markers";
import {
  buildLiveTeams,
  buildNextGames,
  buildTravelArcs,
  buildTravelInfo,
  findFollowedTeamForSide,
  groupGamesByVenue,
  isToday,
} from "./map/travel";
import {
  readBasemap,
  setSatelliteImagery,
  writeBasemap,
  type BasemapId,
} from "./map/basemap";
import { CameraOrbit } from "./map/orbit";
import { FanCrowd } from "./map/fans";
import "./map/map.css";
import type { Game, MapGame, MapTeam } from "../types";
import MapTeamPanel from "../components/MapTeamPanel";
import MapVenueGamesPanel, {
  type MapVenueGroup,
} from "../components/MapVenueGamesPanel";
import Select, { type SelectOption } from "../components/Select";
import { usePrefersReducedMotion } from "../lib/usePrefersReducedMotion";
import { useMapFocus } from "../components/MapFocusContext";
import { useCloseTeamDetail, useOpenTeam } from "../components/TeamDetailPanel";

/**
 * FREE, keyless vector style from OpenFreeMap. No token/API key required and
 * it ships building footprints plus a ready-made `building-3d`
 * `fill-extrusion` layer (source-layer "building"), so 3D stadium-area
 * buildings render once we tilt the camera and zoom in past z14.
 */
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

/** Camera tilt (degrees) so building extrusions read as 3D. */
const DEFAULT_PITCH = 55;

/** Zoom to use when flying to / framing a single stadium. */
const SINGLE_TEAM_ZOOM = 15.5;

/**
 * "Tour my teams" pacing. Each stop flies in, slowly orbits ("hovers")
 * around the stadium, then opens that team's page — a relaxed ~12s kiosk
 * cadence (within the 10–15s the dwell was tuned to). All offsets are
 * relative to the start of each stop and must stay ordered < TOUR_STOP_MS.
 */
const TOUR_STOP_MS = 12000; // total dwell on each team before the next
const TOUR_FLY_MS = 3000; // fly-in animation
const TOUR_HOVER_AT = 3200; // orbit begins (just after arrival)
const TOUR_HOVER_MS = 3800; // orbit duration
const TOUR_PAGE_AT = 7200; // the team page opens
const TOUR_PAGE_CLOSE_AT = 11400; // page closes, before the next fly-in
const TOUR_ORBIT_FROM = -28; // fly-in bearing
const TOUR_ORBIT_TO = 28; // orbit sweeps to this bearing

/**
 * Marker clustering (supercluster). DOM markers (one absolutely-positioned
 * element per pin, transformed on every camera move) get janky in the low
 * hundreds, which is exactly what "follow all" produces. Above this many pins
 * we collapse nearby ones into a count badge that expands on click/zoom; at or
 * below it we render every pin individually (so a normal follow-set looks
 * exactly as before). 80 keeps the common "your teams + the World Cup" set
 * (~59 pins) un-clustered.
 */
const CLUSTER_MIN_MARKERS = 80;
/** supercluster radius (px) — how close two pins must be to merge at a zoom. */
const CLUSTER_RADIUS = 60;
/** Above this zoom every pin is shown individually (no clusters). */
const CLUSTER_MAX_ZOOM = 14;
/** Don't expand a cluster past this zoom on click (street level is too deep). */
const CLUSTER_EXPAND_MAX_ZOOM = 16;

/** Default day-window for the "Upcoming games" mode (matches the backend). */
const DEFAULT_DAYS = 3;
// Stadiums mode looks this far ahead for each followed team's next away game
// (so the travel arcs/planes have something to draw even off-window).
const STADIUMS_TRAVEL_DAYS = 21;

/** Preset upcoming-game windows for the day-window dropdown (replaces the
 *  old slider). Ids are the day counts as strings (Select takes string ids). */
const DAY_WINDOW_OPTIONS: SelectOption[] = [
  { id: "3", label: "Next 3 days" },
  { id: "7", label: "Next 7 days" },
  { id: "14", label: "Next 14 days" },
  { id: "30", label: "Next 30 days" },
];

/** A normalized marker the reconciler can add/remove regardless of its source. */
interface MapMarker {
  id: string;
  lat: number;
  lon: number;
  makeElement: () => HTMLDivElement;
  makeTooltip: () => HTMLDivElement;
  onSelect: () => void;
  /** True when this pin involves a team the user follows — used to tint a
   *  cluster amber when it contains any of the user's own teams. */
  followed: boolean;
  /** Cluster badges drive their own camera (expand-to-zoom) on click, so the
   *  reconciler must NOT also fly to street level the way it does for a pin. */
  isCluster?: boolean;
}

/** Per-point properties indexed by supercluster (one per individual pin). */
interface ClusterPointProps {
  markerId: string;
  /** 1 if this pin is a followed team, else 0 (summed into followedCount). */
  followed: number;
}

/** Aggregated cluster properties (supercluster map/reduce accumulator). */
interface ClusterAccumProps {
  followedCount: number;
}

/** A selected map pin (drives whichever side panel is open). */
type Selection =
  | { kind: "team"; team: MapTeam }
  | { kind: "venue"; venue: MapVenueGroup };

/** Reusable error/empty/loading frame with the dark kiosk chrome. */
function MapFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[calc(100vh-6rem)] min-h-[420px] items-center justify-center overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900">
      {children}
    </div>
  );
}

/** A segmented toggle between the two map modes. */
function ModeToggle({
  mode,
  onChange,
}: {
  mode: MapMode;
  onChange: (mode: MapMode) => void;
}) {
  const options: { value: MapMode; label: string }[] = [
    { value: "games", label: "Upcoming games" },
    { value: "stadiums", label: "Stadiums" },
  ];
  return (
    <div className="inline-flex rounded-full border border-zinc-800 bg-zinc-900 p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={mode === option.value}
          onClick={() => onChange(option.value)}
          className={
            "rounded-full px-3 py-1 text-xs font-medium transition-colors " +
            (mode === option.value
              ? "bg-amber-500/20 text-amber-400"
              : "text-zinc-400 hover:text-zinc-200")
          }
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A segmented toggle between the vector basemap and satellite imagery. */
function BasemapToggle({
  value,
  onChange,
}: {
  value: BasemapId;
  onChange: (basemap: BasemapId) => void;
}) {
  const options: { id: BasemapId; label: string }[] = [
    { id: "vector", label: "Vector" },
    { id: "satellite", label: "Satellite" },
  ];
  return (
    <div
      role="group"
      aria-label="Basemap"
      className="inline-flex rounded-full border border-zinc-800 bg-zinc-900 p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={value === option.id}
          onClick={() => onChange(option.id)}
          className={
            "rounded-full px-3 py-1 text-xs font-medium transition-colors " +
            (value === option.id
              ? "bg-amber-500/20 text-amber-400"
              : "text-zinc-400 hover:text-zinc-200")
          }
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A toggle chip for showing/hiding a pin source (your teams / a competition). */
function FilterChip({
  label,
  dotColor,
  active,
  onClick,
}: {
  label: string;
  dotColor: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors " +
        (active
          ? "border-zinc-600 bg-zinc-800 text-zinc-100"
          : "border-zinc-800 bg-zinc-900 text-zinc-500 hover:text-zinc-300")
      }
    >
      <span
        aria-hidden
        className="inline-block size-2 rounded-full"
        style={{ backgroundColor: active ? dotColor : "#3f3f46" }}
      />
      {label}
    </button>
  );
}

/** Sentinel option id for "no group filter" in the group dropdown. */
const ALL_GROUPS = "__all__";

// --- Travel arcs (Stadiums mode) ------------------------------------------
/** Source/layer ids for the great-circle travel arcs. */
const ARCS_SOURCE = "sd-travel-arcs";
const ARCS_LAYER = "sd-travel-arcs";

/**
 * Source/layer ids for the ONE-SHOT cinematic arc — the route a clicked
 * game's flight follows. Kept separate from ARCS_SOURCE so Effect 3's
 * Stadiums-mode re-sync can never wipe a flight mid-air, and so a games-mode
 * flight can draw a route at all (arcDataRef is empty in games mode).
 */
const CINE_ARC_SOURCE = "sd-cine-arc";
const CINE_ARC_LAYER = "sd-cine-arc";

/** Amber dashed line — matches the SportsDash accent. */
const ARC_COLOR = "#f59e0b"; // amber-500

/** An empty FeatureCollection (used to clear the arc layer in games mode). */
const EMPTY_FC: FeatureCollection<LineString> = {
  type: "FeatureCollection",
  features: [],
};

/**
 * Fit/zoom the camera to frame ALL markers (not just the clustered subset).
 * A single pin can't form a box, so jump straight to it at street level. Used
 * on first load and when switching map modes.
 */
function fitMapToMarkers(map: maplibregl.Map, markers: MapMarker[]): void {
  if (markers.length === 0) return;
  if (markers.length === 1) {
    map.jumpTo({
      center: [markers[0].lon, markers[0].lat],
      zoom: SINGLE_TEAM_ZOOM,
    });
    return;
  }
  const bounds = new maplibregl.LngLatBounds();
  for (const m of markers) bounds.extend([m.lon, m.lat]);
  map.fitBounds(bounds, { padding: 80, maxZoom: 12, duration: 0 });
}

/**
 * Run the current viewport through the supercluster index and return the
 * markers to actually render: original pins where they're un-clustered at this
 * zoom, plus synthesized count-badge markers where supercluster collapsed
 * several. Leaves reuse their full MapMarker spec (logo chip / venue badge,
 * hover, click) so nothing about an individual pin changes — only the SET
 * shrinks, which is what keeps hundreds of "follow all" pins smooth.
 */
function clusterForViewport(
  map: maplibregl.Map,
  index: Supercluster<ClusterPointProps, ClusterAccumProps>,
  leafById: Map<string, MapMarker>,
  mode: MapMode,
  stopOrbit: () => void,
): MapMarker[] {
  const b = map.getBounds();
  const bbox: [number, number, number, number] = [
    b.getWest(),
    b.getSouth(),
    b.getEast(),
    b.getNorth(),
  ];
  const zoom = Math.floor(map.getZoom());
  const out: MapMarker[] = [];
  for (const f of index.getClusters(bbox, zoom)) {
    const [lon, lat] = f.geometry.coordinates;
    const props = f.properties;
    if ("cluster" in props) {
      const clusterId = props.cluster_id;
      const count = props.point_count;
      const hasFollowed = props.followedCount > 0;
      out.push({
        id: `cluster:${clusterId}`,
        lat,
        lon,
        followed: hasFollowed,
        isCluster: true,
        makeElement: () => createClusterElement(count, hasFollowed),
        makeTooltip: () => createClusterTooltipElement(count, hasFollowed, mode),
        onSelect: () => {
          // The badge drives its own camera — kill any flyover orbit first,
          // or its per-frame jumpTo would fight this zoom for the camera.
          stopOrbit();
          // Zoom to where this cluster breaks apart (clamped so a tight group
          // doesn't dive all the way to street level on one click).
          const expansion = index.getClusterExpansionZoom(clusterId);
          const target = Math.min(
            Math.max(expansion, zoom + 1),
            CLUSTER_EXPAND_MAX_ZOOM,
          );
          map.easeTo({
            center: [lon, lat],
            zoom: target,
            pitch: DEFAULT_PITCH,
            duration: 600,
            essential: true,
          });
        },
      });
    } else {
      const leaf = leafById.get(props.markerId);
      if (leaf !== undefined) out.push(leaf);
    }
  }
  return out;
}

export default function MapView() {
  const [mode, setMode] = useState<MapMode>("games");
  const [touring, setTouring] = useState(false);
  const [geoState, setGeoState] = useState<"idle" | "locating" | "denied">(
    "idle",
  );
  const tourTimersRef = useRef<number[]>([]);
  // Guards async callbacks (geolocation has no cancellation) from setting state
  // after the view unmounts.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const [days, setDays] = useState<number>(DEFAULT_DAYS);

  // Stadiums mode only uses `games` to compute travel arcs to each team's NEXT
  // away game, so it looks further ahead than the user's games-mode window —
  // otherwise a team whose next trip is >window away would have no arc/plane.
  const effectiveDays =
    mode === "stadiums" ? Math.max(days, STADIUMS_TRAVEL_DAYS) : days;
  const mapQuery = useMap(effectiveDays);
  const teamsQuery = useTeams();

  const teams = useMemo(() => mapQuery.data?.teams ?? [], [mapQuery.data]);
  const games = useMemo(() => mapQuery.data?.games ?? [], [mapQuery.data]);

  // league_id -> display name from the /teams payload (a fallback label).
  const leagueNames = useMemo(() => {
    const byId: Record<string, string> = {};
    for (const league of teamsQuery.data?.leagues ?? []) {
      byId[league.id] = league.name;
    }
    return byId;
  }, [teamsQuery.data]);

  // --- Pin filters (shared across both modes) -------------------------------
  const [followedHidden, setFollowedHidden] = useState(false);
  const [hiddenComps, setHiddenComps] = useState<Set<string>>(() => new Set());
  const [activeGroup, setActiveGroup] = useState<string | null>(null);

  const passesFilters = useMemo(
    () =>
      (item: { source: string; league_id: string; group: string | null }) => {
        if (item.source === "followed") return !followedHidden;
        if (hiddenComps.has(item.league_id)) return false;
        if (activeGroup !== null && item.group !== activeGroup) return false;
        return true;
      },
    [followedHidden, hiddenComps, activeGroup],
  );

  // Filter by hasCoords as well: the payload normally only carries resolved
  // venues, but guarding here keeps a mid-geocode null/NaN from ever reaching
  // `new maplibregl.Marker().setLngLat(...)`, which throws on non-finite input.
  const visibleTeams = useMemo(
    () => teams.filter((t) => hasCoords(t) && passesFilters(t)),
    [teams, passesFilters],
  );
  const visibleGames = useMemo(
    () => games.filter((g) => hasCoords(g) && passesFilters(g)),
    [games, passesFilters],
  );

  // The active dataset (drives the legend, group chips, and count chip).
  const activeItems = mode === "games" ? games : teams;

  // Legend: "Your teams" plus one chip per distinct competition in the active
  // dataset. MapTeam and MapGame share source/league_id/league_name fields.
  const legend = useMemo(() => {
    const hasFollowed = activeItems.some((item) => item.source === "followed");
    const competitions: { id: string; name: string }[] = [];
    const seen = new Set<string>();
    for (const item of activeItems) {
      if (item.source !== "competition" || seen.has(item.league_id)) continue;
      seen.add(item.league_id);
      competitions.push({
        id: item.league_id,
        name: item.league_name ?? leagueNames[item.league_id] ?? item.league_id,
      });
    }
    return { hasFollowed, competitions };
  }, [activeItems, leagueNames]);

  const groups = useMemo(() => {
    const seen = new Set<string>();
    for (const item of activeItems) {
      if (item.source === "competition" && item.group) seen.add(item.group);
    }
    return [...seen].sort();
  }, [activeItems]);

  // Venue groups for "games" mode (one pin per venue).
  const venueGroups = useMemo(
    () => (mode === "games" ? groupGamesByVenue(visibleGames) : []),
    [mode, visibleGames],
  );

  // Every plotted team's NEXT game (home or away) — drives which travel visual
  // they get: an away next-game flies a plane along an arc, a home next-game
  // draws fans converging on the stadium.
  const nextByTeam = useMemo(() => buildNextGames(teams, games), [teams, games]);

  // A stable content signature of the travel inputs (each plotted team's home
  // coords + its next game). The 30s map refetch hands back fresh array refs
  // even when the content is identical; keying the derived arcs/home-venues/
  // facts on this signature keeps their identity stable across polls, so the
  // plane/fan animations don't tear down and restart every 30 seconds.
  const travelSig = useMemo(() => {
    const parts: string[] = [];
    for (const team of teams) {
      if (!hasCoords(team)) continue;
      const n = nextByTeam.get(team.team_id);
      const g = n
        ? `${n.isHome ? "H" : "A"}:${n.game.game_id}:${n.game.lat.toFixed(3)},${n.game.lon.toFixed(3)}`
        : "-";
      parts.push(
        `${team.team_id}@${team.lat.toFixed(3)},${team.lon.toFixed(3)}#${g}`,
      );
    }
    return parts.sort().join("|");
  }, [teams, nextByTeam]);

  // Travel arcs (Stadiums mode): home venue -> host venue, away next-games only.
  // Keyed on travelSig so the identity is stable across content-identical
  // refetches (teams/nextByTeam are read fresh — same content as the sig).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const travelArcs = useMemo(() => buildTravelArcs(teams, nextByTeam), [travelSig]);

  // Travel facts per team with an away next-game (distance / flight time / tz /
  // in-transit), shown in the team panel.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const travelByTeam = useMemo(
    () => buildTravelInfo(teams, nextByTeam, Date.now()),
    [travelSig],
  );

  // Teams with a game HAPPENING (live or today) — clicking one's pin celebrates
  // with a fan burst (see the marker click handler). Recomputed on each poll so
  // a game going live mid-session arms the celebration.
  const liveTeams = useMemo(() => buildLiveTeams(teams, games), [teams, games]);
  const liveTeamsRef = useRef(liveTeams);
  liveTeamsRef.current = liveTeams;

  // --- The selected pin drives whichever side panel is open -----------------
  const [selected, setSelected] = useState<Selection | null>(null);
  const onSelectRef = useRef<(selection: Selection) => void>(() => {});
  onSelectRef.current = setSelected;
  // Switching modes closes any open panel (its pin no longer exists).
  useEffect(() => {
    setSelected(null);
  }, [mode]);
  // Closing the side panel stops the flyover orbit — the show belongs to the
  // open pin/game.
  useEffect(() => {
    if (selected === null) orbitRef.current?.stop();
  }, [selected]);

  // --- Normalized marker list for the active mode ---------------------------
  const markers = useMemo<MapMarker[]>(() => {
    if (mode === "games") {
      return venueGroups.map((group) => {
        const pulse = group.games.some(
          (game) => game.phase === "in_progress" || isToday(game.start_time),
        );
        return {
          id: `venue:${group.key}`,
          lat: group.lat,
          lon: group.lon,
          followed: group.source === "followed",
          makeElement: () => createVenueMarkerElement(group, pulse),
          makeTooltip: () => createVenueTooltipElement(group),
          onSelect: () => onSelectRef.current({ kind: "venue", venue: group }),
        };
      });
    }
    return visibleTeams.map((team) => {
      const pulse = isToday(team.next_match_time);
      const inTransit = travelByTeam.get(team.team_id)?.inTransit ?? false;
      return {
        id: `team:${team.team_id}`,
        lat: team.lat,
        lon: team.lon,
        followed: team.source === "followed",
        makeElement: () => createMarkerElement(team, pulse, inTransit),
        makeTooltip: () => createTooltipElement(team),
        onSelect: () => onSelectRef.current({ kind: "team", team }),
      };
    });
  }, [mode, venueGroups, visibleTeams, travelByTeam]);

  // Schedule over the competition window, grouped by venue, so a clicked
  // host-stadium pin in "Stadiums" mode lists every match played there.
  const scheduleQuery = useSchedule(localDayOffset(-21), localDayOffset(60));
  const matchesByVenue = useMemo(() => {
    const compLeagues = new Set(
      teams
        .filter((team) => team.source === "competition")
        .map((team) => team.league_id),
    );
    const byVenue: Record<string, Game[]> = {};
    for (const game of scheduleQuery.data ?? []) {
      if (!game.venue || !compLeagues.has(game.league_id)) continue;
      (byVenue[game.venue] ??= []).push(game);
    }
    for (const list of Object.values(byVenue)) {
      list.sort((a, b) => a.start_time.localeCompare(b.start_time));
    }
    return byVenue;
  }, [scheduleQuery.data, teams]);

  const reducedMotion = usePrefersReducedMotion();
  // The auto-tour opens each team's page and dismisses it before the next stop.
  const openTeam = useOpenTeam();
  const closeTeamDetail = useCloseTeamDetail();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<Map<string, maplibregl.Marker>>(new window.Map());
  const planesRef = useRef<maplibregl.Marker[]>([]);
  // The one-shot "follow the plane to the away game" cinematic. Its arrival
  // fan crowd lives in `crowd` so cancelCinematic tears down the whole show.
  const cinematicRef = useRef<{
    raf: number;
    plane: maplibregl.Marker | null;
    crowd: FanCrowd | null;
    cleanupTimer: number;
  }>({ raf: 0, plane: null, crowd: null, cleanupTimer: 0 });
  const hoverPopupRef = useRef<maplibregl.Popup | null>(null);
  // The slow flyover orbit that starts after a pin-click fly-in lands (and
  // after the flight cinematic's dive). Created with the map; stopped by user
  // interaction, panel close, tab hide, mode switch, or unmount.
  const orbitRef = useRef<CameraOrbit | null>(null);
  // Mirrors the IntersectionObserver below: false while the map's view is
  // mounted-but-hidden, so fan crowds park their rAF loops instead of
  // animating for nobody (the same reason the orbit stops on tab hide).
  const mapVisibleRef = useRef(true);
  // Fresh reduced-motion for the once-attached marker click handlers — they
  // are bound inside the mount-once map effect, so their closures would keep
  // the first render's value without a ref.
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;
  // Basemap (vector/satellite) — persisted like the theme/ticker prefs. The
  // ref mirrors the state for the mount-once map effect's style handlers.
  const [basemap, setBasemap] = useState<BasemapId>(readBasemap);
  const basemapRef = useRef(basemap);
  basemapRef.current = basemap;
  // Assigned inside the map effect; applies the current basemapRef choice to
  // the live style (retrying via styledata while the style is still loading).
  const applyBasemapRef = useRef<(() => void) | null>(null);
  // Low-level reconciler: make the live DOM markers equal exactly this set
  // (add/remove by id). Framing the camera is renderViewport's job, not this.
  const syncMarkersRef = useRef<((current: MapMarker[]) => void) | null>(null);

  // The map effect mounts once, so it reads the latest markers through a ref.
  const markersStateRef = useRef(markers);
  markersStateRef.current = markers;

  // --- Marker clustering (supercluster) -------------------------------------
  // DOM markers don't scale to hundreds of pins (each is an element the map
  // transforms on every camera move), which is exactly what "follow all"
  // produces. Above CLUSTER_MIN_MARKERS we index the pins and render only the
  // current viewport's clusters/leaves; at/below it we render every pin so a
  // normal follow-set looks and behaves exactly as before.
  const modeRef = useRef(mode);
  modeRef.current = mode;

  // id -> full MapMarker spec, so a clustered leaf renders with its original
  // element/hover/click (logo chip / venue badge) — only the SET shrinks.
  const leafById = useMemo(() => {
    const byId = new window.Map<string, MapMarker>();
    for (const marker of markers) byId.set(marker.id, marker);
    return byId;
  }, [markers]);
  const leafByIdRef = useRef(leafById);
  leafByIdRef.current = leafById;

  // One GeoJSON point per pin for supercluster (carries the id back + a
  // followed flag so a cluster tints amber when it holds your teams).
  const clusterPoints = useMemo<
    Supercluster.PointFeature<ClusterPointProps>[]
  >(
    () =>
      markers.map((marker) => ({
        type: "Feature",
        properties: { markerId: marker.id, followed: marker.followed ? 1 : 0 },
        geometry: { type: "Point", coordinates: [marker.lon, marker.lat] },
      })),
    [markers],
  );

  const clusterIndexRef = useRef<Supercluster<
    ClusterPointProps,
    ClusterAccumProps
  > | null>(null);

  // Compute the markers to render for the current camera and push them through
  // the reconciler. `refit` frames ALL pins first (mode switch / first load);
  // the resulting camera move re-clusters via the moveend handler. Stored in a
  // ref so the once-mounted map effect's moveend listener calls the latest.
  const renderViewportRef = useRef<((refit: boolean) => void) | null>(null);
  const renderViewport = (refit: boolean): void => {
    const map = mapRef.current;
    if (map === null) return;
    const full = markersStateRef.current;
    if (refit) fitMapToMarkers(map, full);
    const index = clusterIndexRef.current;
    const toRender =
      index === null
        ? full
        : clusterForViewport(map, index, leafByIdRef.current, modeRef.current, () =>
            orbitRef.current?.stop(),
          );
    syncMarkersRef.current?.(toRender);
  };
  renderViewportRef.current = renderViewport;

  // Latest arc FeatureCollection + the data we should actually paint (the full
  // set in Stadiums mode, an empty set in games mode), read by the map effect.
  const arcDataRef = useRef<FeatureCollection<LineString>>(EMPTY_FC);
  arcDataRef.current = mode === "stadiums" ? travelArcs : EMPTY_FC;

  // --- Focus-on-a-venue (from the team profile's "Next match" card) ----------
  const { target: focusTarget, clear: clearFocus } = useMapFocus();
  // Latest data the focus resolver reads (it runs from timers, not a render).
  const focusDataRef = useRef({ games, venueGroups, teams });
  focusDataRef.current = { games, venueGroups, teams };
  const pendingFocusRef = useRef<typeof focusTarget>(null);
  const focusTimersRef = useRef<number[]>([]);
  // Cancel any pending focus timers when the map view unmounts.
  useEffect(
    () => () => focusTimersRef.current.forEach((id) => window.clearTimeout(id)),
    [],
  );

  // Draw (or clear) the one-shot cinematic arc: the great-circle route the
  // clicked game's flight follows. Guards every style lookup (the style may
  // be mid-load or already torn down during unmount).
  const setCineArc = (route: [number, number][] | null): void => {
    const map = mapRef.current;
    if (map === null || !map.isStyleLoaded()) return;
    try {
      if (route === null) {
        if (map.getLayer(CINE_ARC_LAYER)) map.removeLayer(CINE_ARC_LAYER);
        if (map.getSource(CINE_ARC_SOURCE)) map.removeSource(CINE_ARC_SOURCE);
        return;
      }
      const data: FeatureCollection<LineString> = {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: {},
            geometry: { type: "LineString", coordinates: route },
          },
        ],
      };
      const source = map.getSource(CINE_ARC_SOURCE) as
        | maplibregl.GeoJSONSource
        | undefined;
      if (source !== undefined) {
        source.setData(data);
      } else {
        map.addSource(CINE_ARC_SOURCE, { type: "geojson", data });
        map.addLayer({
          id: CINE_ARC_LAYER,
          type: "line",
          source: CINE_ARC_SOURCE,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ARC_COLOR,
            "line-width": 2.5,
            "line-opacity": 0.9,
          },
        });
      }
    } catch {
      // Style already gone — the map teardown removes everything anyway.
    }
  };

  // Ref mirror: cancelCinematic clears the arc through this so it only ever
  // touches refs — keeping it non-reactive for the mount-once effects that
  // call it (same convention as syncArcsRef / syncMarkersRef below).
  const setCineArcRef = useRef(setCineArc);
  setCineArcRef.current = setCineArc;

  // Tear down a running cinematic (rAF, the plane, its route arc, and the
  // arrival fan crowd) plus any flyover orbit — every caller is about to move
  // the camera somewhere else.
  const cancelCinematic = (): void => {
    const c = cinematicRef.current;
    if (c.raf) cancelAnimationFrame(c.raf);
    if (c.cleanupTimer) window.clearTimeout(c.cleanupTimer);
    c.plane?.remove();
    c.crowd?.stop();
    cinematicRef.current = {
      raf: 0,
      plane: null,
      crowd: null,
      cleanupTimer: 0,
    };
    setCineArcRef.current(null);
    orbitRef.current?.stop();
  };
  useEffect(() => cancelCinematic, []); // stop the cinematic on unmount

  // Fans pouring into the destination stadium as the plane arrives: clumps
  // walking street-like routes to the gates (the shared crowd mechanic in
  // map/fans.ts). Replaces any previous arrival crowd.
  const burstFansAt = (lon: number, lat: number, color: string): void => {
    const map = mapRef.current;
    if (map === null) return;
    cinematicRef.current.crowd?.stop();
    cinematicRef.current.crowd = new FanCrowd({
      map,
      lon,
      lat,
      color,
      isVisible: () => mapVisibleRef.current,
    }).start();
  };

  // Click-to-celebrate: a one-shot crowd walking into the stadium of a team
  // whose game is happening — the SAME shared mechanic as the flight
  // cinematic's arrival crowd (map/fans.ts), just self-contained here so
  // repeated clicks never stack and it never collides with the cinematic's
  // crowd (which lives in cinematicRef).
  const celebrateCrowdRef = useRef<FanCrowd | null>(null);
  const stopCelebrate = (): void => {
    celebrateCrowdRef.current?.stop();
    celebrateCrowdRef.current = null;
  };
  const celebrateFansAt = (lon: number, lat: number, color: string): void => {
    const map = mapRef.current;
    if (map === null) return;
    stopCelebrate(); // a fresh click replaces any in-flight crowd
    celebrateCrowdRef.current = new FanCrowd({
      map,
      lon,
      lat,
      color,
      isVisible: () => mapVisibleRef.current,
    }).start();
  };
  // Fired from the marker click handler (via a ref, so it reads fresh live data
  // and reduced-motion each render): celebrate only when the clicked team has a
  // game happening.
  const maybeCelebrateRef = useRef<
    (teamId: string, lon: number, lat: number) => void
  >(() => {});
  maybeCelebrateRef.current = (teamId, lon, lat) => {
    if (reducedMotion) return;
    const color = liveTeamsRef.current.get(teamId);
    if (color) celebrateFansAt(lon, lat, color);
  };

  // The marquee: a plane departs home, the camera follows it along the
  // great-circle to the away venue, and fans pour in as it lands.
  const runFlightCinematic = (
    home: [number, number],
    dest: [number, number],
    color: string,
  ): void => {
    const map = mapRef.current;
    if (map === null) return;
    cancelCinematic();
    const route = greatCircle(home[0], home[1], dest[0], dest[1]);
    if (route === null) {
      map.flyTo({
        center: dest,
        zoom: SINGLE_TEAM_ZOOM,
        pitch: DEFAULT_PITCH,
        essential: true,
      });
      return;
    }
    const { el, glyph } = createPlaneElement();
    el.classList.add("sd-map-plane-cine");
    const plane = new maplibregl.Marker({ element: el })
      .setLngLat(route[0])
      .addTo(map);
    cinematicRef.current.plane = plane;
    // Draw the route the plane is about to fly (cleared on landing/cancel).
    setCineArc(route);
    // Cut to the home stadium; the plane takes off from here.
    map.jumpTo({ center: home, zoom: 6, pitch: DEFAULT_PITCH, bearing: -10 });
    let start = 0;
    let lastCam = 0;
    let fansStarted = false;
    const DURATION = 9000;
    // Camera-update cadence during the flight — matches the flyover orbit's
    // throttle (the 3D-building re-render per jumpTo is the whole cost).
    const CAMERA_UPDATE_MS = 1000 / 24;
    const tick = (ts: number) => {
      const map = mapRef.current; // re-read live; bail if the map went away
      if (map === null) {
        cinematicRef.current.raf = 0;
        return;
      }
      if (start === 0) start = ts;
      const p = Math.min(1, (ts - start) / DURATION);
      const e = p * p * (3 - 2 * p); // smoothstep along the route
      const fi = e * (route.length - 1);
      const idx = Math.floor(fi);
      const frac = fi - idx;
      const a = route[idx];
      const b = route[Math.min(idx + 1, route.length - 1)];
      const pos: [number, number] = [
        a[0] + (b[0] - a[0]) * frac,
        a[1] + (b[1] - a[1]) * frac,
      ];
      plane.setLngLat(pos);
      const ang = (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI;
      glyph.style.transform = `rotate(${ang}deg)`;
      // Camera follows the plane; zoom holds wide, then dives into the
      // stadium. Throttled to ~24fps: every jumpTo is a full style render
      // (the 3D buildings make it the flight's entire cost), while the
      // plane marker itself stays buttery at full frame rate.
      const zoom =
        p < 0.75 ? 6 : 6 + (SINGLE_TEAM_ZOOM - 6) * ((p - 0.75) / 0.25) ** 1.4;
      if (ts - lastCam >= CAMERA_UPDATE_MS || p === 1) {
        lastCam = ts;
        map.jumpTo({ center: pos, zoom, pitch: DEFAULT_PITCH, bearing: -10 });
      }
      // Fans start gathering "while the plane is coming in".
      if (!fansStarted && p > 0.62) {
        fansStarted = true;
        burstFansAt(dest[0], dest[1], color);
      }
      if (p < 1) {
        cinematicRef.current.raf = requestAnimationFrame(tick);
      } else {
        cinematicRef.current.raf = 0;
        map.easeTo({
          center: dest,
          zoom: SINGLE_TEAM_ZOOM,
          pitch: DEFAULT_PITCH,
          duration: 700,
        });
        // The dive eases out, then the slow flyover orbit takes over.
        orbitRef.current?.startAfterSettled(() => !reducedMotionRef.current);
        // Let the plane land, then remove it (the fans keep arriving a bit).
        cinematicRef.current.cleanupTimer = window.setTimeout(() => {
          cinematicRef.current.plane?.remove();
          cinematicRef.current.plane = null;
          setCineArc(null); // the flight's done — lift its route line
        }, 1600);
      }
    };
    cinematicRef.current.raf = requestAnimationFrame(tick);
  };

  useEffect(() => {
    if (!focusTarget) return;
    // Capture THIS request's target; identity checks below stop a stale timer
    // from an earlier request acting on a newer one (and vice-versa).
    const target = focusTarget;
    focusTimersRef.current.forEach((id) => window.clearTimeout(id));
    pendingFocusRef.current = target;
    // Switch to Stadiums (the travel layer's home) and widen the games window
    // so the target fixture's venue coords are present to fly the plane to.
    setMode("stadiums");
    setDays(30);

    const tryResolve = (): boolean => {
      const map = mapRef.current;
      if (pendingFocusRef.current !== target || map === null) return false;
      const { games: g, teams: ts } = focusDataRef.current;
      const game = g.find((item) => item.game_id === target.gameId);
      if (game === undefined || !hasCoords(game)) return false;
      pendingFocusRef.current = null;
      const dest: [number, number] = [game.lon, game.lat];
      const team = target.teamId
        ? ts.find((t) => t.team_id === target.teamId)
        : undefined;
      const color = team?.color ?? DEFAULT_COLOR;
      // Away game with a known home: fly the plane there, fans pour in on
      // arrival. Home game / no home coords / reduced motion: just go there
      // (a home game's fans come from the ambient Effect 6).
      if (
        target.isHome ||
        team === undefined ||
        !hasCoords(team) ||
        reducedMotion
      ) {
        cancelCinematic();
        map.flyTo({
          center: dest,
          zoom: SINGLE_TEAM_ZOOM,
          pitch: DEFAULT_PITCH,
          essential: true,
        });
      } else {
        runFlightCinematic([team.lon, team.lat], dest, color);
      }
      return true;
    };

    // The fixture's venue isn't on the map (not geocoded / outside the window):
    // fall back to the followed team's home stadium.
    const fallback = (): void => {
      const map = mapRef.current;
      if (pendingFocusRef.current !== target) return;
      pendingFocusRef.current = null;
      if (map === null || target.teamId === null) return;
      const team = focusDataRef.current.teams.find(
        (t) => t.team_id === target.teamId,
      );
      if (team === undefined || !hasCoords(team)) return;
      cancelCinematic(); // replaces any running show (and the flyover orbit)
      map.flyTo({
        center: [team.lon, team.lat],
        zoom: SINGLE_TEAM_ZOOM,
        pitch: DEFAULT_PITCH,
        essential: true,
      });
    };

    // Poll a few times while the wider window loads, then fall back. (Timers
    // live in a ref so clearing `focusTarget` below doesn't cancel them.)
    focusTimersRef.current = [
      window.setTimeout(tryResolve, 400),
      window.setTimeout(tryResolve, 1100),
      window.setTimeout(tryResolve, 2200),
      window.setTimeout(fallback, 3400),
    ];
    clearFocus();
  }, [focusTarget, clearFocus, reducedMotion]);
  // --- Games mode: click ANY upcoming game in the venue panel → travel show --
  // Replays the travel visuals for THAT fixture (not just each team's next
  // game): a followed AWAY side flies the plane from its home venue along a
  // great-circle (one-shot cinematic: arc draws, plane flies, fans burst on
  // arrival, the dive settles into the flyover orbit); a followed HOME side
  // skips the flight and just streams the fans in; anything else — or reduced
  // motion — falls back to the plain fly-to. The panel still opens the box
  // score modal on the same click.
  const onVenueGameClick = (game: MapGame): void => {
    const map = mapRef.current;
    if (map === null || !hasCoords(game)) return;
    const dest: [number, number] = [game.lon, game.lat];
    const awayTeam = findFollowedTeamForSide(teams, game.away);
    const homeTeam = findFollowedTeamForSide(teams, game.home);
    if (!reducedMotion && awayTeam !== undefined && hasCoords(awayTeam)) {
      runFlightCinematic(
        [awayTeam.lon, awayTeam.lat],
        dest,
        awayTeam.color ?? game.away.color ?? DEFAULT_COLOR,
      );
      return;
    }
    cancelCinematic(); // replaces any running show (and the flyover orbit)
    map.flyTo({
      center: dest,
      zoom: SINGLE_TEAM_ZOOM,
      pitch: DEFAULT_PITCH,
      essential: true,
    });
    if (reducedMotion) return; // no fans, no orbit — just the fly-to
    if (homeTeam !== undefined) {
      celebrateFansAt(
        dest[0],
        dest[1],
        homeTeam.color ?? game.home.color ?? DEFAULT_COLOR,
      );
    }
    orbitRef.current?.startAfterSettled(() => !reducedMotionRef.current);
  };

  // Imperatively ensures the arc source+layer exist (style must be loaded) and
  // pushes the current data. Assigned inside the map effect, called from both
  // the create effect and the arc-update effect; guards every style lookup.
  const syncArcsRef = useRef<(() => void) | null>(null);

  // Map container only renders once we have something to plot.
  const hasContent = teams.length > 0 || games.length > 0;

  // --- Effect 0: (re)build the cluster index when the pin set changes --------
  // Only index above the threshold; below it clustering is off (index = null)
  // and renderViewport renders every pin, exactly as before. Defined before the
  // map effect so on mount the index exists before the map's first paint (no
  // flash of hundreds of un-clustered pins). Rebuilding is cheap — load() is
  // sub-millisecond for a few hundred points.
  useEffect(() => {
    if (clusterPoints.length > CLUSTER_MIN_MARKERS) {
      const index = new Supercluster<ClusterPointProps, ClusterAccumProps>({
        radius: CLUSTER_RADIUS,
        maxZoom: CLUSTER_MAX_ZOOM,
        map: (props) => ({ followedCount: props.followed }),
        reduce: (acc, props) => {
          acc.followedCount += props.followedCount;
        },
      });
      index.load(clusterPoints);
      clusterIndexRef.current = index;
    } else {
      clusterIndexRef.current = null;
    }
    // Re-render the viewport with the new (or absent) index. No-ops until the
    // map exists (renderViewport guards a null map ref).
    renderViewportRef.current?.(false);
  }, [clusterPoints]);

  // --- Effect 1: create the map once, when the container first mounts -------
  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

    const initial = markersStateRef.current;
    const map = new maplibregl.Map({
      container,
      style: STYLE_URL,
      center: initial.length > 0 ? [initial[0].lon, initial[0].lat] : [0, 20],
      zoom: 4,
      pitch: DEFAULT_PITCH,
      bearing: -10,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    orbitRef.current = new CameraOrbit(map);
    // App.tsx keeps visited views mounted-but-hidden, so unmount never fires
    // on tab switch: stop the flyover orbit when the map leaves the viewport
    // (its rAF would otherwise keep driving a camera nobody can see), and
    // mirror visibility into mapVisibleRef so fan crowds park their rAF
    // loops for the same reason.
    const hideObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        mapVisibleRef.current = entry.isIntersecting;
        if (!entry.isIntersecting) orbitRef.current?.stop();
      }
    });
    hideObserver.observe(container);
    hoverPopupRef.current = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 16,
      maxWidth: "240px",
      className: "sd-map-tooltip",
    });

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));

    // Apply the persisted basemap choice (vector/satellite) to the live style.
    // The Esri imagery layer is added/removed imperatively (never a setStyle
    // swap), so the arcs, the 3D-building fix-ups, and the DOM markers all
    // survive a toggle. Retries via styledata while the style is loading.
    function applyBasemap(): void {
      if (setSatelliteImagery(map, basemapRef.current === "satellite")) return;
      const onStyleData = () => {
        if (!map.isStyleLoaded()) return;
        map.off("styledata", onStyleData);
        setSatelliteImagery(map, basemapRef.current === "satellite");
      };
      map.on("styledata", onStyleData);
    }
    applyBasemapRef.current = applyBasemap;

    map.on("load", () => {
      // 3D buildings render OPAQUE and unshaded on purpose: a translucent
      // fill-extrusion layer forfeits the depth fast path, and the vertical
      // gradient adds per-fragment cost — measured during the flyover orbit:
      // 13fps @0.85+gradient vs 26fps @1.0+flat (dense Chicago, pitch 55).
      // The look is effectively identical at pitch.
      if (map.getLayer("building-3d")) {
        map.setLayoutProperty("building-3d", "visibility", "visible");
        map.setPaintProperty("building-3d", "fill-extrusion-opacity", 1);
        map.setPaintProperty("building-3d", "fill-extrusion-vertical-gradient", false);
      } else if (map.getSource("openmaptiles") && !map.getLayer("sd-buildings-3d")) {
        map.addLayer({
          id: "sd-buildings-3d",
          type: "fill-extrusion",
          source: "openmaptiles",
          "source-layer": "building",
          minzoom: 14,
          paint: {
            "fill-extrusion-color": "hsl(35,8%,80%)",
            "fill-extrusion-height": ["get", "render_height"],
            "fill-extrusion-base": ["get", "render_min_height"],
            "fill-extrusion-opacity": 1,
            "fill-extrusion-vertical-gradient": false,
          },
        });
      }
      renderViewportRef.current?.(false);
      syncArcs();
      applyBasemap(); // style is loaded — the persisted choice applies at once
    });

    // Ensure the arc source+layer exist (needs the style) and push the current
    // FeatureCollection. Safe to call before the style loads — it no-ops then,
    // and the "load" handler above calls it once the style is ready. Every
    // getSource/getLayer is guarded since they're undefined during style swaps.
    function syncArcs(): void {
      if (!map.isStyleLoaded()) return;
      const data = arcDataRef.current;
      let source = map.getSource(ARCS_SOURCE) as
        | maplibregl.GeoJSONSource
        | undefined;
      if (source === undefined) {
        map.addSource(ARCS_SOURCE, { type: "geojson", data });
        source = map.getSource(ARCS_SOURCE) as maplibregl.GeoJSONSource | undefined;
      } else {
        source.setData(data);
      }
      if (map.getLayer(ARCS_LAYER) === undefined && source !== undefined) {
        map.addLayer({
          id: ARCS_LAYER,
          type: "line",
          source: ARCS_SOURCE,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ARC_COLOR,
            "line-width": 1.5,
            "line-opacity": 0.5,
            "line-dasharray": [2, 2],
          },
        });
      }
    }
    syncArcsRef.current = syncArcs;

    function syncMarkers(current: MapMarker[]): void {
      // Markers are DOM overlays positioned by lng/lat — they don't need the
      // (sometimes slow/flaky) vector style loaded, so we never gate them on
      // the "load" event. Only the 3D-buildings layer waits for the style.
      const live = markersRef.current;
      const next = new Set(current.map((marker) => marker.id));

      for (const [id, marker] of live) {
        if (!next.has(id)) {
          marker.remove();
          live.delete(id);
        }
      }

      for (const spec of current) {
        if (live.has(spec.id)) continue;
        const marker = new maplibregl.Marker({ element: spec.makeElement() })
          .setLngLat([spec.lon, spec.lat])
          .addTo(map);
        const el = marker.getElement();
        el.addEventListener("mouseenter", () => {
          hoverPopupRef.current
            ?.setLngLat([spec.lon, spec.lat])
            .setDOMContent(spec.makeTooltip())
            .addTo(map);
        });
        el.addEventListener("mouseleave", () => {
          hoverPopupRef.current?.remove();
        });
        el.addEventListener("click", () => {
          hoverPopupRef.current?.remove();
          spec.onSelect();
          // A cluster badge drives its own camera (expand-to-zoom) in onSelect;
          // only an individual pin flies in to street level.
          if (!spec.isCluster) {
            map.flyTo({
              center: [spec.lon, spec.lat],
              zoom: SINGLE_TEAM_ZOOM,
              pitch: DEFAULT_PITCH,
              essential: true,
            });
            // Once the fly-in lands, ease into the slow flyover orbit around
            // the stadium (the gate keeps reduced-motion sessions still).
            orbitRef.current?.startAfterSettled(
              () => !reducedMotionRef.current,
            );
            // If this team's game is happening (live or today), celebrate:
            // fans stream into its stadium as the camera dives in.
            if (spec.id.startsWith("team:")) {
              maybeCelebrateRef.current(spec.id.slice(5), spec.lon, spec.lat);
            }
          }
        });
        live.set(spec.id, marker);
      }
    }

    // Re-cluster after the camera settles (pan/zoom changes which pins merge).
    // Skip while the flight cinematic runs — it jumpTo's every frame, which
    // would otherwise recluster 60×/sec for no benefit. The flyover orbit
    // jumpTo's every frame too, and its bearing-only sweep never changes
    // which pins merge.
    map.on("moveend", () => {
      if (cinematicRef.current.raf !== 0) return;
      if (orbitRef.current?.orbiting ?? false) return;
      renderViewportRef.current?.(false);
    });

    syncMarkersRef.current = syncMarkers;
    // Plot immediately — markers don't wait for the style to finish loading.
    renderViewportRef.current?.(false);
    // Arcs need the style; this no-ops until "load" fires when it isn't ready.
    syncArcs();

    return () => {
      // Stop any in-flight cinematic BEFORE removing the map, so its rAFs
      // don't fire jumpTo/getZoom on a destroyed MapLibre instance.
      hideObserver.disconnect();
      orbitRef.current?.stop();
      orbitRef.current = null;
      cancelCinematic();
      stopCelebrate(); // and any click-to-celebrate fan burst
      for (const marker of markersRef.current.values()) marker.remove();
      markersRef.current.clear();
      hoverPopupRef.current?.remove();
      hoverPopupRef.current = null;
      syncMarkersRef.current = null;
      syncArcsRef.current = null;
      applyBasemapRef.current = null;
      // Tear down the arc layer/source before the map (guarded — the style may
      // be mid-swap). map.remove() below disposes everything else.
      try {
        if (map.getLayer(ARCS_LAYER)) map.removeLayer(ARCS_LAYER);
        if (map.getSource(ARCS_SOURCE)) map.removeSource(ARCS_SOURCE);
        if (map.getLayer(CINE_ARC_LAYER)) map.removeLayer(CINE_ARC_LAYER);
        if (map.getSource(CINE_ARC_SOURCE)) map.removeSource(CINE_ARC_SOURCE);
      } catch {
        // Style already gone — nothing to clean up.
      }
      mapRef.current = null;
      map.remove();
    };
  }, [hasContent]);

  // --- Effect 2: reconcile markers on every payload / filter / mode change --
  // Refit only when the mode changes (incl. the first run, prevMode === null),
  // so within-mode data refreshes never yank the user's camera.
  const prevModeRef = useRef<MapMode | null>(null);
  useEffect(() => {
    const refit = prevModeRef.current !== mode;
    prevModeRef.current = mode;
    // Switching modes swaps the whole marker set — drop any lingering hover
    // label so a previous mode's tooltip can't hang over the new pins.
    if (refit) hoverPopupRef.current?.remove();
    // renderViewport re-clusters for the current camera (and frames all pins
    // first when refit). `markers` is read fresh via markersStateRef.
    renderViewportRef.current?.(refit);
  }, [markers, mode]);

  // --- Effect 3: repaint travel arcs on mode / payload change ----------------
  // arcDataRef already reflects the mode (full FC in Stadiums, empty in games);
  // syncArcs reads it and no-ops safely if the style isn't loaded yet.
  useEffect(() => {
    syncArcsRef.current?.();
  }, [travelArcs, mode]);

  // --- Effect 5: planes gliding along the travel arcs (Stadiums mode) --------
  // One plane marker per arc, advancing along its great-circle samples on a
  // staggered loop. Reduced-motion parks each plane mid-route (no animation).
  // Plane markers are pointer-events:none so they never steal a stadium click.
  useEffect(() => {
    const map = mapRef.current;
    // Clear any existing planes first (mode/data/motion change rebuilds them).
    for (const plane of planesRef.current) plane.remove();
    planesRef.current = [];
    if (map === null || mode !== "stadiums") return;

    const routes = travelArcs.features
      .map((f) => f.geometry.coordinates as [number, number][])
      .filter((coords) => coords.length > 1);
    if (routes.length === 0) return;

    const planes = routes.map((coords) => {
      const { el, glyph } = createPlaneElement();
      const marker = new maplibregl.Marker({ element: el })
        .setLngLat(coords[0])
        .addTo(map);
      return { coords, glyph, marker };
    });
    planesRef.current = planes.map((p) => p.marker);

    const cleanup = () => {
      for (const p of planes) p.marker.remove();
      planesRef.current = [];
    };

    if (reducedMotion) {
      for (const p of planes) {
        p.marker.setLngLat(p.coords[Math.floor(p.coords.length / 2)]);
      }
      return cleanup;
    }

    let raf = 0;
    let startTs = 0;
    const DURATION = 8000; // ms to traverse a whole arc
    const tick = (ts: number) => {
      if (startTs === 0) startTs = ts;
      const base = (ts - startTs) / DURATION;
      planes.forEach((p, i) => {
        const prog = (base + i * 0.17) % 1; // stagger the fleet
        const fi = prog * (p.coords.length - 1);
        const idx = Math.floor(fi);
        const frac = fi - idx;
        const a = p.coords[idx];
        const b = p.coords[Math.min(idx + 1, p.coords.length - 1)];
        const lng = a[0] + (b[0] - a[0]) * frac;
        const lat = a[1] + (b[1] - a[1]) * frac;
        p.marker.setLngLat([lng, lat]);
        // Rotate the glyph to the compass bearing (SVG points north at 0°).
        const ang = (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI;
        p.glyph.style.transform = `rotate(${ang}deg)`;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      cleanup();
    };
  }, [travelArcs, mode, reducedMotion]);

  // (The old always-on "fans at every home venue" crowd was removed: fans now
  // appear ONLY when you click a team whose game is happening and fly into its
  // stadium — see celebrateFansAt + the marker click handler.)

  // Stop a running tour if the user leaves Stadiums mode; clear timers on
  // unmount. (Effects must precede the early returns below.)
  useEffect(() => {
    if (mode !== "stadiums") {
      if (tourTimersRef.current.length > 0) {
        tourTimersRef.current.forEach((id) => window.clearTimeout(id));
        tourTimersRef.current = [];
        closeTeamDetail(); // drop any team page the tour had opened
        setTouring(false);
      }
    }
    // A mode switch swaps the whole pin set and closes the panel, so any
    // one-shot show (flight cinematic, celebrate burst, flyover orbit) is
    // torn down in BOTH directions — venue-panel game clicks can start them
    // in games mode now, not just the Stadiums-mode focus flow.
    cancelCinematic();
    stopCelebrate();
    // cancelCinematic/stopCelebrate are stable-enough closures; safe to omit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);
  useEffect(
    () => () => {
      tourTimersRef.current.forEach((id) => window.clearTimeout(id));
      closeTeamDetail(); // don't leave a tour's team page open after unmount
    },
    // closeTeamDetail is stable (provider useCallback); capture once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // --- "Tour my teams": fly the camera between followed stadiums in turn ----
  const stopTour = () => {
    tourTimersRef.current.forEach((id) => window.clearTimeout(id));
    tourTimersRef.current = [];
    closeTeamDetail(); // dismiss any team page the tour had opened
    setTouring(false);
    orbitRef.current?.stop(); // the tour owns the camera now (or nobody does)
  };
  const startTour = () => {
    const map = mapRef.current;
    const stops = teams.filter((t) => t.source === "followed" && hasCoords(t));
    if (map === null || stops.length === 0) return;
    orbitRef.current?.stop(); // the tour's fly-ins own the camera now
    // Defensive: never stack a second fleet of timers on top of a running tour.
    tourTimersRef.current.forEach((id) => window.clearTimeout(id));
    tourTimersRef.current = [];
    closeTeamDetail();
    setTouring(true);
    const at = (fn: () => void, delay: number) =>
      tourTimersRef.current.push(window.setTimeout(fn, delay));

    stops.forEach((team, i) => {
      const base = i * TOUR_STOP_MS;
      const last = i === stops.length - 1;
      // 1) Fly in (page closed so the stadium is visible).
      at(() => {
        closeTeamDetail();
        map.flyTo({
          center: [team.lon, team.lat],
          zoom: SINGLE_TEAM_ZOOM,
          pitch: DEFAULT_PITCH,
          bearing: TOUR_ORBIT_FROM,
          duration: reducedMotion ? 0 : TOUR_FLY_MS,
          essential: true,
        });
      }, base);
      // 2) Hover: a slow orbit around the stadium (skipped if reduced motion).
      if (!reducedMotion) {
        at(() => {
          map.easeTo({
            bearing: TOUR_ORBIT_TO,
            duration: TOUR_HOVER_MS,
            essential: true,
          });
        }, base + TOUR_HOVER_AT);
      }
      // 3) Open the team's page.
      at(() => openTeam(team.team_id), base + TOUR_PAGE_AT);
      // 4) Close it before the next fly-in — and end the tour after the last.
      at(() => {
        closeTeamDetail();
        if (last) setTouring(false);
      }, base + TOUR_PAGE_CLOSE_AT);
    });
  };

  // --- "Near me": fly to the closest venue to the browser's location --------
  const flyToNearest = () => {
    if (!navigator.geolocation) {
      setGeoState("denied");
      return;
    }
    setGeoState("locating");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (!mountedRef.current) return;
        setGeoState("idle");
        const map = mapRef.current;
        if (map === null) return;
        const { latitude, longitude } = pos.coords;
        let best: Selection | null = null;
        let bestKm = Infinity;
        let bestLngLat: [number, number] | null = null;
        const consider = (
          lat: number,
          lon: number,
          sel: Selection,
        ): void => {
          const km = haversineKm(latitude, longitude, lat, lon);
          if (km < bestKm) {
            bestKm = km;
            best = sel;
            bestLngLat = [lon, lat];
          }
        };
        if (mode === "games") {
          for (const group of venueGroups) {
            consider(group.lat, group.lon, { kind: "venue", venue: group });
          }
        } else {
          for (const team of visibleTeams) {
            consider(team.lat, team.lon, { kind: "team", team });
          }
        }
        if (best !== null && bestLngLat !== null) {
          orbitRef.current?.stop(); // this fly-to owns the camera now
          map.flyTo({ center: bestLngLat, zoom: 9, essential: true });
          onSelectRef.current(best);
        }
      },
      () => {
        if (mountedRef.current) setGeoState("denied");
      },
      { timeout: 8000 },
    );
  };

  // --- Basemap toggle (vector ↔ satellite imagery) --------------------------
  // Persist like the theme/ticker prefs; the effect pushes the choice to the
  // live map (the INITIAL persisted choice is applied by the map's load
  // handler once its style is ready — the map effect may mount after this).
  const onBasemapChange = (next: BasemapId): void => {
    setBasemap(next);
    writeBasemap(next);
  };
  useEffect(() => {
    applyBasemapRef.current?.();
  }, [basemap]);

  if (mapQuery.isError) {
    return (
      <MapFrame>
        <p className="px-6 text-center text-sm text-red-400">
          Failed to load the map: {mapQuery.error?.message ?? "unknown error"}
        </p>
      </MapFrame>
    );
  }

  if (mapQuery.isPending) {
    return (
      <MapFrame>
        <p className="text-sm text-zinc-500">Loading stadium map…</p>
      </MapFrame>
    );
  }

  if (!hasContent) {
    return (
      <MapFrame>
        <div className="max-w-md px-6 text-center">
          <p className="text-sm font-medium text-zinc-300">No locations yet</p>
          <p className="mt-1 text-sm text-zinc-500">
            Venue coordinates are resolved in the background. Once your followed
            teams' stadiums (and upcoming-game venues) are geocoded they'll
            appear here in 3D.
          </p>
        </div>
      </MapFrame>
    );
  }

  const noGamesInWindow = mode === "games" && games.length === 0;

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <div
          ref={containerRef}
          className="h-[calc(100vh-5rem)] min-h-[420px] w-full overflow-hidden rounded-lg border border-zinc-800"
        />

        {/* ALL map controls float INSIDE the map box (top-left): mode toggle,
            tour, "Near me", the upcoming-window dropdown (games mode), the live
            count, the competition filter, and the hover/click hint. The wrapper
            is pointer-events-none so the map still drags between the cards; each
            card re-enables its own pointer events. */}
        <div className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[min(26rem,calc(100%-1.5rem))] flex-col gap-1.5">
          <div className="pointer-events-auto flex flex-col gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/80 p-1.5 shadow-lg backdrop-blur-sm">
            <div className="flex flex-wrap items-center gap-1.5">
              <ModeToggle mode={mode} onChange={setMode} />
              <BasemapToggle value={basemap} onChange={onBasemapChange} />

              {mode === "stadiums" && legend.hasFollowed && (
                <button
                  type="button"
                  onClick={() => (touring ? stopTour() : startTour())}
                  aria-pressed={touring}
                  title="Fly the camera between your teams' stadiums"
                  className={
                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition " +
                    (touring
                      ? "border-amber-500/40 bg-amber-500/10 text-amber-400"
                      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
                  }
                >
                  <span aria-hidden="true">🎥</span>
                  {touring ? "Stop tour" : "Tour my teams"}
                </button>
              )}

              <button
                type="button"
                onClick={flyToNearest}
                disabled={geoState === "locating"}
                title="Fly to the closest venue to your location"
                className={
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition " +
                  (geoState === "denied"
                    ? "border-zinc-800 bg-zinc-900 text-zinc-600"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200")
                }
              >
                <span aria-hidden="true">📍</span>
                {geoState === "locating"
                  ? "Locating…"
                  : geoState === "denied"
                    ? "Location off"
                    : "Near me"}
              </button>

              {mode === "games" && (
                <Select
                  ariaLabel="Upcoming window"
                  value={String(days)}
                  onChange={(id) => setDays(Number(id))}
                  options={DAY_WINDOW_OPTIONS}
                />
              )}

              <span className="rounded-full border border-zinc-700 bg-zinc-800/70 px-2.5 py-0.5 text-xs text-zinc-200">
                {mode === "games" ? (
                  <>
                    {visibleGames.length}{" "}
                    {visibleGames.length === 1 ? "game" : "games"}
                    {venueGroups.length > 0
                      ? ` · ${venueGroups.length} ${
                          venueGroups.length === 1 ? "venue" : "venues"
                        }`
                      : ""}
                  </>
                ) : (
                  <>
                    {visibleTeams.length}
                    {visibleTeams.length !== teams.length
                      ? ` of ${teams.length}`
                      : ""}{" "}
                    {teams.length === 1 ? "stadium" : "stadiums"}
                  </>
                )}
              </span>
            </div>

            {legend.competitions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {legend.hasFollowed && (
                  <FilterChip
                    label="Your teams"
                    dotColor={DEFAULT_COLOR}
                    active={!followedHidden}
                    onClick={() => setFollowedHidden((hidden) => !hidden)}
                  />
                )}
                {legend.competitions.map((comp) => (
                  <FilterChip
                    key={comp.id}
                    label={comp.name}
                    dotColor={COMPETITION_RING}
                    active={!hiddenComps.has(comp.id)}
                    onClick={() =>
                      setHiddenComps((prev) => {
                        const nextSet = new Set(prev);
                        if (nextSet.has(comp.id)) nextSet.delete(comp.id);
                        else nextSet.add(comp.id);
                        return nextSet;
                      })
                    }
                  />
                ))}
                {groups.length > 1 && (
                  <Select
                    ariaLabel="Filter by group"
                    value={activeGroup ?? ALL_GROUPS}
                    onChange={(id) => setActiveGroup(id === ALL_GROUPS ? null : id)}
                    options={[
                      { id: ALL_GROUPS, label: "All groups" },
                      ...groups.map(
                        (group): SelectOption => ({ id: group, label: group }),
                      ),
                    ]}
                  />
                )}
              </div>
            )}
          </div>

          <span className="pointer-events-none w-fit rounded-md bg-zinc-900/75 px-2 py-0.5 text-[11px] text-zinc-300 shadow backdrop-blur-sm">
            Hover a dot for details · click to fly in
          </span>
        </div>
        {noGamesInWindow && (
          <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
            <span className="pointer-events-auto rounded-full border border-zinc-700 bg-zinc-900/90 px-3 py-1 text-xs text-zinc-300 shadow-lg">
              No games in the next {days} {days === 1 ? "day" : "days"} — try a
              wider window.
            </span>
          </div>
        )}
      </div>

      <MapTeamPanel
        team={selected?.kind === "team" ? selected.team : null}
        leagueNames={leagueNames}
        matchesByVenue={matchesByVenue}
        travel={
          selected?.kind === "team"
            ? travelByTeam.get(selected.team.team_id) ?? null
            : null
        }
        onClose={() => setSelected(null)}
      />
      <MapVenueGamesPanel
        venue={selected?.kind === "venue" ? selected.venue : null}
        leagueNames={leagueNames}
        onClose={() => setSelected(null)}
        onGameClick={onVenueGameClick}
      />
    </div>
  );
}
