import type { Outcome } from "../components/TeamProfileView";

/** W/L/D chip classes shared by the results list and matchup form rows. */
export const OUTCOME_CHIP: Record<Outcome, string> = {
  W: "bg-emerald-500/15 text-emerald-400",
  L: "bg-red-500/15 text-red-400",
  D: "bg-zinc-700/40 text-zinc-300",
};
