import { createContext, useContext } from "react";

import type { TabId } from "./Layout";

/** What a view can ask its destination to open on. */
export interface NavigationParams {
  /** League: show this league. */
  leagueId?: string;
  /** League: open on this conference, where the league has them. */
  group?: string;
  /** Results: show this followed team. */
  teamId?: string;
}

/** One request to go somewhere. `nonce` tells two identical requests apart. */
export interface NavigationRequest extends NavigationParams {
  tab: TabId;
  nonce: number;
}

interface Navigation {
  navigate: (tab: TabId, params?: NavigationParams) => void;
  /** The latest request, for the destination view to honour once. */
  request: NavigationRequest | null;
}

/**
 * Lets a view send the user to another tab, optionally saying what to show
 * there ("Full standings" on Today goes to League, on that league). Tab
 * state lives in App and views take no props, so this is the one way
 * across; the default is a no-op for a view rendered alone.
 */
export const NavigateContext = createContext<Navigation>({
  navigate: () => {},
  request: null,
});

export function useNavigateTab(): Navigation["navigate"] {
  return useContext(NavigateContext).navigate;
}

/**
 * The tab on screen. Views are mounted once and then hidden, so a view that
 * polls something expensive checks this to stop while it is not showing.
 * Null when rendered outside the app shell (treated as showing).
 */
export const ActiveTabContext = createContext<TabId | null>(null);

export function useIsActiveTab(tab: TabId): boolean {
  const active = useContext(ActiveTabContext);
  return active === null || active === tab;
}

/**
 * The latest navigation request addressed to `tab`, or null. A destination
 * view applies it in an effect keyed on `request.nonce`, so the same request
 * is not applied twice and a later choice by the user is not overridden.
 */
export function useNavigationRequest(tab: TabId): NavigationRequest | null {
  const { request } = useContext(NavigateContext);
  return request !== null && request.tab === tab ? request : null;
}
