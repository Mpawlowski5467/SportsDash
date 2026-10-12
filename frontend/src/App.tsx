import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import ThemeProvider from "./components/ThemeProvider";
import Layout, { TABS, type TabId } from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import {
  MapFocusContext,
  type MapFocusTarget,
} from "./components/MapFocusContext";
import ManageTeamsWizard from "./components/onboarding/ManageTeamsWizard";
import OnboardingWizard from "./components/onboarding/OnboardingWizard";
import { useSetupStatus } from "./hooks";
import type { SetupStatus } from "./types";
import TodayView from "./views/TodayView";
import MatchupView from "./views/MatchupView";
import LeagueView from "./views/LeagueView";
import ResultsView from "./views/ResultsView";
import NewsView from "./views/NewsView";
import SettingsView from "./views/SettingsView";
import { TeamDetailProvider } from "./components/TeamDetailPanel";
import SportsDashSplash from "./components/loaders/SportsDashSplash";
import SportsDashSpinner from "./components/loaders/SportsDashSpinner";
import { ManageTeamsContext } from "./components/ManageTeamsContext";
import {
  ActiveTabContext,
  NavigateContext,
  type NavigationParams,
  type NavigationRequest,
} from "./components/NavigateContext";

// Heavy, route-specific views (MapLibre's ~megabyte, the Calendar) are split out
// so they only download when their tab is first opened — keeping the initial
// bundle small. LoaderGallery and PiecesGallery are dev-only showcases
// (#sd-loaders, #sd-pieces), so they stay out of the production critical
// path too.
const MapView = lazy(() => import("./views/MapView"));
const CalendarView = lazy(() => import("./views/CalendarView"));
const LoaderGallery = lazy(() => import("./components/loaders/LoaderGallery"));
const PiecesGallery = lazy(() => import("./components/bs/PiecesGallery"));

const VIEWS: Record<TabId, ComponentType> = {
  today: TodayView,
  calendar: CalendarView,
  matchup: MatchupView,
  league: LeagueView,
  results: ResultsView,
  news: NewsView,
  map: MapView,
};

/** Boot splash shown while the setup status loads (or fails) — the Prompt 2
 * cold-start splash, with the API error / retry affordance layered under the
 * wordmark when the status can't be reached. */
function Splash({ error, onRetry }: { error?: string; onRetry?: () => void }) {
  return (
    <SportsDashSplash>
      {error !== undefined && (
        <div className="flex items-center gap-3">
          <p className="m-0 font-medium text-alert">{error}</p>
          {onRetry && (
            <button type="button" onClick={onRetry} className="bs-btn">
              Retry
            </button>
          )}
        </div>
      )}
    </SportsDashSplash>
  );
}

export default function App() {
  // Dev/preview-only living demo of the logo + loader system at /#sd-loaders.
  // Returned from App (which has no hooks) so the early exit doesn't violate
  // the rules of hooks; it needs neither the theme nor the query cache.
  if (
    typeof window !== "undefined" &&
    window.location.hash === "#sd-loaders"
  ) {
    return (
      <Suspense fallback={null}>
        <LoaderGallery />
      </Suspense>
    );
  }

  // The shared Box Score pieces in every theme, at /#sd-pieces. It may carry
  // a query (#sd-pieces?theme=dark&palette=team), hence the prefix match.
  if (
    typeof window !== "undefined" &&
    window.location.hash.startsWith("#sd-pieces")
  ) {
    return (
      <Suspense fallback={null}>
        <PiecesGallery />
      </Suspense>
    );
  }

  // ThemeProvider lives inside the QueryClientProvider (mounted in main.tsx)
  // so its custom-accent effect can read the cached `/teams` payload, and
  // above all app content so the theme switcher updates everything live.
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

function AppContent() {
  const queryClient = useQueryClient();
  const setup = useSetupStatus();
  const [active, setActive] = useState<TabId>("today");
  // Tabs whose view has been activated at least once — drives the
  // mount-once-visited rendering at the bottom of this component.
  const [visited, setVisited] = useState<ReadonlySet<TabId>>(
    () => new Set([active]),
  );
  const [manageOpen, setManageOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [firstRunActive, setFirstRunActive] = useState(false);
  const [mapFocus, setMapFocus] = useState<MapFocusTarget | null>(null);
  // Stable so views consuming ManageTeamsContext don't re-render on App state.
  const openManageTeams = useCallback(() => setManageOpen(true), []);

  // Lets the team profile's "Next match" card jump to the Map and fly to that
  // game's venue. requestFocus stores the target and switches to the Map tab;
  // MapView consumes it and clears it. requestFocus/clear are stable (only call
  // stable setState) so the map's focus effect doesn't churn on every change.
  const requestFocus = useCallback((target: MapFocusTarget) => {
    setMapFocus(target);
    setActive("map");
  }, []);
  const clearFocus = useCallback(() => setMapFocus(null), []);

  // A view sending the user to another tab ("Full standings" on Today). A
  // click in the nav leaves the page where it is; this is a jump from deep
  // in one page to the top of another, so it scrolls there and hands the
  // keyboard to the page instead of dropping focus on <body>.
  const [navRequest, setNavRequest] = useState<NavigationRequest | null>(null);
  const navigate = useCallback((tab: TabId, params?: NavigationParams) => {
    setNavRequest({ tab, ...params, nonce: Date.now() });
    setActive(tab);
  }, []);
  useEffect(() => {
    if (navRequest === null) return;
    window.scrollTo(0, 0);
    document.getElementById("sd-main")?.focus({ preventScroll: true });
  }, [navRequest]);
  const navigation = useMemo(
    () => ({ navigate, request: navRequest }),
    [navigate, navRequest],
  );
  const mapFocusValue = useMemo(
    () => ({ target: mapFocus, requestFocus, clear: clearFocus }),
    [mapFocus, requestFocus, clearFocus],
  );

  // Latch the first-run wizard open. Its syncing step invalidates EVERY
  // query — including setup-status — so `onboarded` flips to true while
  // the wizard is still mid-sync. Without the latch that refetch would
  // unmount the wizard before its onComplete fires.
  useEffect(() => {
    if (!firstRunActive && setup.data !== undefined && !setup.data.onboarded) {
      setFirstRunActive(true);
    }
  }, [firstRunActive, setup.data]);

  // A save answers as soon as the followed set is written; the schedules,
  // standings and rosters of anything newly followed arrive with the refresh
  // it kicks, anywhere from seconds to minutes later. Most queries never
  // refetch by themselves, and the views stay mounted, so without this they
  // would show the moment of the save until a reload. The status poll (see
  // `statusRefetchInterval`) watches the refresh; when it ends, refetch
  // everything, once.
  const refreshing = setup.data?.refreshing ?? false;
  const wasRefreshingRef = useRef(false);
  useEffect(() => {
    if (wasRefreshingRef.current && !refreshing) {
      void queryClient.invalidateQueries();
    }
    wasRefreshingRef.current = refreshing;
  }, [refreshing, queryClient]);

  if (firstRunActive || (setup.data !== undefined && !setup.data.onboarded)) {
    return (
      <OnboardingWizard
        mode="first-run"
        onComplete={() => {
          // The wizard only completes after a successful follow POST,
          // so the backend is authoritatively onboarded. Write that through
          // the cache BEFORE releasing the latch: if the post-sync refetch
          // of setup-status hasn't landed yet (slow or failed), stale
          // `onboarded: false` data would re-latch the wizard — whose
          // one-shot syncing step has already fired — stranding the app on
          // the syncing screen forever. The invalidate below still refetches
          // the real status, so server truth wins moments later.
          queryClient.setQueryData<SetupStatus>(
            ["setup-status"],
            (prev) => prev && { ...prev, onboarded: true },
          );
          setFirstRunActive(false);
          void queryClient.invalidateQueries();
        }}
      />
    );
  }

  if (setup.isPending) {
    return <Splash />;
  }

  if (setup.data === undefined) {
    return (
      <Splash
        error="Couldn't reach the SportsDash API."
        onRetry={() => void setup.refetch()}
      />
    );
  }

  if (!visited.has(active)) {
    // Adjusting state during render: React re-renders synchronously before
    // committing, so a newly-activated tab mounts in the same pass — no
    // blank frame between the switch and the view's first paint.
    setVisited(new Set(visited).add(active));
  }

  return (
    <MapFocusContext.Provider value={mapFocusValue}>
    <NavigateContext.Provider value={navigation}>
    <ActiveTabContext.Provider value={active}>
    <ManageTeamsContext.Provider value={openManageTeams}>
    <TeamDetailProvider>
      <Layout
        active={active}
        onChange={setActive}
        onManageTeams={() => setManageOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
      >
        {/* Mount-once-visited: a view mounts the first time its tab is
            activated, then stays mounted and is hidden via the `hidden`
            attribute. Tab switches (incl. the kiosk auto-rotation, which
            just calls onChange) no longer remount the tree — the old
            `key={active}` remount tore down and rebuilt the MapLibre WebGL
            context on every rotation, re-downloading the style/tiles and
            resetting the camera. Accepted tradeoff for this single-user
            dashboard: hidden views keep their TanStack Query observers
            alive, so background polling continues for visited tabs.
            Toggling `hidden` (display:none → visible) restarts the
            `sd-view-enter` CSS animation, so the per-switch enter
            transition survives the change; reduced-motion still no-ops it. */}
        {TABS.map((tab) => {
          if (!visited.has(tab.id)) return null;
          const View = VIEWS[tab.id];
          return (
            <div
              key={tab.id}
              className="sd-view-enter"
              hidden={tab.id !== active}
            >
              {/* Per-view boundary: a render error degrades only this view
                  (an error in a hidden view must not blank the visible
                  one). resetKey re-tries the view on the next tab switch
                  instead of sticking on the fallback; `key={active}` on the
                  boundary would remount the subtree and defeat the whole
                  mount-once-visited point above. */}
              <ErrorBoundary resetKey={active}>
                <Suspense
                  fallback={
                    <div className="flex justify-center py-16">
                      <SportsDashSpinner size={88} label="Loading" />
                    </div>
                  }
                >
                  <View />
                </Suspense>
              </ErrorBoundary>
            </div>
          );
        })}
      </Layout>
      {manageOpen && (
        <ManageTeamsWizard
          onComplete={() => {
            setManageOpen(false);
            void queryClient.invalidateQueries();
          }}
          onClose={() => setManageOpen(false)}
        />
      )}
      {settingsOpen && (
        <SettingsView onClose={() => setSettingsOpen(false)} />
      )}
    </TeamDetailProvider>
    </ManageTeamsContext.Provider>
    </ActiveTabContext.Provider>
    </NavigateContext.Provider>
    </MapFocusContext.Provider>
  );
}
