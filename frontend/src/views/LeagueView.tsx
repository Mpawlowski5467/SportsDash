import { useState } from "react";

import StandingsView from "./StandingsView";
import LeadersView from "./LeadersView";
import BracketView from "./BracketView";

/**
 * League hub: the three competition-wide views (Standings, Leaders, Bracket)
 * behind one tab, switched by a sub-tab bar. Each child keeps its own league
 * selector and data, so this is purely a presentational wrapper — collapsing
 * three top-level tabs into one keeps the main nav from sprawling.
 */
type Section = "standings" | "leaders" | "bracket";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "standings", label: "Standings" },
  { id: "leaders", label: "Leaders" },
  { id: "bracket", label: "Bracket" },
];

export default function LeagueView() {
  const [section, setSection] = useState<Section>("standings");

  return (
    <div className="space-y-4">
      <div
        role="tablist"
        aria-label="League views"
        className="inline-flex gap-1 rounded-lg border border-zinc-800 bg-zinc-900 p-1"
      >
        {SECTIONS.map((s) => {
          const activeSection = s.id === section;
          return (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={activeSection}
              onClick={() => setSection(s.id)}
              className={
                "rounded-md px-3 py-1.5 text-sm font-medium transition " +
                (activeSection
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200")
              }
            >
              {s.label}
            </button>
          );
        })}
      </div>
      {section === "standings" && <StandingsView />}
      {section === "leaders" && <LeadersView />}
      {section === "bracket" && <BracketView />}
    </div>
  );
}
