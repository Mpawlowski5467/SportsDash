import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
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

// Heavy, route-specific deps (MapLibre ~megabyte, FullCalendar) are split out
// so they only download when their tab is first opened — keeping the initial
// bundle small. LoaderGallery is a dev-only showcase (#sd-loaders), so it
// stays out of the production critical path too.
const MapView = lazy(() => import("./views/MapView"));
const CalendarView = lazy(() => import("./views/CalendarView"));
const LoaderGallery = lazy(() => import("./components/loaders/LoaderGallery"));

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
          <p className="text-sm text-red-400">{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
            >
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

  // ThemeProvider lives inside the QueryClientProvider (mounted in main.tsx)
  // so its stadium-accent effect can read the cached `/teams` payload, and
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
  const [notificationsOpen, setNotificationsOpen] = useState(false);
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
    <ManageTeamsContext.Provider value={openManageTeams}>
    <TeamDetailProvider>
      <Layout
        active={active}
        onChange={setActive}
        onManageTeams={() => setManageOpen(true)}
        onOpenNotifications={() => setNotificationsOpen(true)}
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
        <OnboardingWizard
          mode="manage"
          onComplete={() => {
            setManageOpen(false);
            void queryClient.invalidateQueries();
          }}
          onClose={() => setManageOpen(false)}
        />
      )}
      {notificationsOpen && (
        <SettingsView onClose={() => setNotificationsOpen(false)} />
      )}
    </TeamDetailProvider>
    </ManageTeamsContext.Provider>
    </MapFocusContext.Provider>
  );
}
