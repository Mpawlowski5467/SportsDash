import { createContext, useContext } from "react";

/**
 * Lets any view open the Manage Teams wizard — the same one the header's
 * gear menu opens. Used by empty states ("no teams followed yet") so they
 * can offer the fix directly instead of describing it.
 */
export const ManageTeamsContext = createContext<() => void>(() => {});

export function useManageTeams(): () => void {
  return useContext(ManageTeamsContext);
}
