import {
  useEffect,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { contrastRatio, fitFill } from "../../lib/color";
import {
  THEMES,
  applyAppearance,
  getAppearance,
  teamAccentVars,
  type PaletteId,
  type ThemeId,
} from "../../lib/theme";
import Button, { ChevronDown, Icon } from "./Button";
import { FollowMark, SpotChip, Tag } from "./marks";
import OutcomeSquare, { FormStrip } from "./OutcomeSquare";
import SectionHead from "./SectionHead";
import Segmented from "./Segmented";
import TeamBlock from "./TeamBlock";

/**
 * Living sheet of the shared Box Score pieces, in every theme. Reachable in
 * dev/preview at `/#sd-pieces` (App swaps it in when the hash matches), like
 * the loader gallery at `/#sd-loaders`. Not part of the app's routed surface.
 *
 * The state can be set from the hash for screenshots:
 * `/#sd-pieces?theme=dark&palette=team&color=0c2340`.
 *
 * Everything here is sample data for looking at the pieces; none of it is
 * shown in the app. The controls restyle the page for this visit only and
 * never write the stored appearance.
 */

/** Team colours chosen to break a naive contrast rule, not to look good. */
const SAMPLE_COLORS: { name: string; code: string; color: string }[] = [
  { name: "Navy", code: "NVY", color: "#0c2340" },
  { name: "Yellow", code: "YEL", color: "#ffc52f" },
  { name: "Red", code: "RED", color: "#bd3039" },
  { name: "Green", code: "GRN", color: "#003831" },
  { name: "Royal", code: "ROY", color: "#005a9c" },
  { name: "White", code: "WHT", color: "#ffffff" },
  { name: "Black", code: "BLK", color: "#000000" },
  { name: "Mid red", code: "MID", color: "#ee2222" },
];

const TABLE: {
  rank: number;
  name: string;
  code: string;
  color: string | null;
  w: number;
  l: number;
  pct: string;
  gb: string;
  followed?: boolean;
}[] = [
  { rank: 1, name: "Sample Rangers", code: "RGR", color: "#003278", w: 55, l: 53, pct: ".509", gb: "—" },
  { rank: 2, name: "Sample Astros", code: "AST", color: "#eb6e1f", w: 54, l: 54, pct: ".500", gb: "1" },
  { rank: 3, name: "No Colour United", code: "NCU", color: null, w: 53, l: 56, pct: ".486", gb: "2.5" },
  { rank: 4, name: "Sample Athletics", code: "ATH", color: "#efb21e", w: 45, l: 63, pct: ".417", gb: "10", followed: true },
  { rank: 5, name: "Followed No Colour", code: "FNC", color: null, w: 44, l: 64, pct: ".407", gb: "11", followed: true },
  { rank: 6, name: "Sample Angels", code: "ANG", color: "#ba0021", w: 42, l: 67, pct: ".385", gb: "13.5" },
];

interface GalleryState {
  theme: ThemeId;
  palette: PaletteId;
  color: string;
}

function readHash(): Partial<GalleryState> {
  const query = window.location.hash.split("?")[1] ?? "";
  const params = new URLSearchParams(query);
  const theme = params.get("theme");
  const palette = params.get("palette");
  const color = params.get("color");
  return {
    theme: THEMES.some((option) => option.id === theme)
      ? (theme as ThemeId)
      : undefined,
    palette: palette === "team" || palette === "brand" ? palette : undefined,
    color: color && /^[0-9a-f]{6}$/i.test(color) ? `#${color}` : undefined,
  };
}

/** The Team palette's variables as an inline style, for one scope. */
function accentStyle(
  palette: PaletteId,
  color: string,
  theme: ThemeId,
): CSSProperties | undefined {
  if (palette !== "team") return undefined;
  return (teamAccentVars(color, theme) ?? undefined) as CSSProperties | undefined;
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <SectionHead title={title} />
      <div className="bs-sheet flex flex-wrap items-center gap-x-6 gap-y-4 p-5">
        {children}
      </div>
    </section>
  );
}

function Standings() {
  return (
    <section aria-label="Sample division">
      <SectionHead
        title="Section tab"
        note="A short plain-language note"
        aside={
          <a href="#sd-pieces" className="bs-link">
            A link
          </a>
        }
      />
      <div className="bs-sheet overflow-x-auto">
        <table className="w-full border-collapse text-right tabular-nums">
          <thead>
            <tr className="bs-label">
              <th scope="col" className="py-2 pl-4 pr-2 text-left font-semibold">
                Team
              </th>
              <th scope="col" className="p-2 font-semibold">W</th>
              <th scope="col" className="p-2 font-semibold">L</th>
              <th scope="col" className="p-2 font-semibold">PCT</th>
              <th scope="col" className="py-2 pl-2 pr-4 font-semibold">GB</th>
              <th scope="col" className="py-2 pl-2 pr-4 font-semibold">Last</th>
            </tr>
          </thead>
          <tbody>
            {TABLE.map((row) => (
              <tr
                key={row.rank}
                className={
                  row.followed ? "bs-inverse" : "border-t border-hair"
                }
              >
                <th
                  scope="row"
                  className={
                    "py-2.5 pl-4 pr-2 text-left " +
                    (row.followed ? "font-bold" : "font-medium")
                  }
                >
                  <span className="flex items-center gap-2.5">
                    <span className="w-4 font-condensed text-lg font-bold text-muted">
                      {row.rank}
                    </span>
                    <TeamBlock
                      name={row.name}
                      abbreviation={row.code}
                      color={row.color}
                    />
                    {row.name}
                    {row.followed && <FollowMark />}
                  </span>
                </th>
                <td className="p-2">{row.w}</td>
                <td className="p-2">{row.l}</td>
                <td className="p-2 font-bold">{row.pct}</td>
                <td className="py-2 pl-2 pr-4">{row.gb}</td>
                <td className="py-2 pl-2 pr-4">
                  <span className="inline-flex gap-1 align-middle">
                    <OutcomeSquare result="W" size={24} />
                    <OutcomeSquare result="D" size={24} />
                    <OutcomeSquare result="L" size={24} />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t-2 border-ink px-4 py-3">
          <span className="bs-note">Sample Athletics, last five</span>
          <FormStrip
            label="Sample Athletics, last five"
            results={["L", "W", "L", "D", "W"]}
          />
        </div>
      </div>
    </section>
  );
}

function ScoreLine() {
  return (
    <div className="bs-band">
      <div className="flex flex-wrap items-stretch px-4 font-condensed text-lg font-semibold uppercase tracking-[0.04em]">
        <span className="flex min-h-11 items-center gap-2.5 pr-5">
          <SpotChip>Live</SpotChip>
          BOS 3 · ATH 4<span className="text-ink-2">Bot 5th</span>
        </span>
        <span className="flex min-h-11 items-center gap-2.5 border-l border-rule px-5">
          <span className="text-ink-2">10:10 PM</span>ARI at LAD
        </span>
        <span className="flex min-h-11 items-center gap-2.5 border-l border-rule px-5">
          <span className="text-ink-2">Final</span>ARI 6 · LAD 3
        </span>
      </div>
    </div>
  );
}

/** Each theme in a nested scope: the page is not restyled to show them. */
function EveryTheme({ color }: { color: string }) {
  const scopes: { label: string; theme: ThemeId; palette: PaletteId }[] = [
    { label: "Light", theme: "light", palette: "brand" },
    { label: "Dark", theme: "dark", palette: "brand" },
    { label: "Team on Light", theme: "light", palette: "team" },
    { label: "Team on Dark", theme: "dark", palette: "team" },
    { label: "High contrast", theme: "contrast", palette: "brand" },
  ];
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(15rem,1fr))] gap-4">
      {scopes.map((scope) => (
        <div
          key={scope.label}
          data-theme={scope.theme}
          data-palette={scope.palette}
          style={accentStyle(scope.palette, color, scope.theme)}
          className="flex flex-col gap-3 bg-ground p-3 text-ink"
        >
          <div>
            <SectionHead title={scope.label} note="Tab and rule" level={3} />
            <div className="bs-sheet flex flex-col">
              <div className="flex items-center gap-2.5 px-3 py-2.5 font-medium">
                <TeamBlock name="Sample Reds" abbreviation="RED" color="#bd3039" />
                Other team
                <span className="ml-auto text-muted">muted</span>
              </div>
              <div className="bs-inverse flex items-center gap-2.5 px-3 py-2.5 font-bold">
                <TeamBlock name="Sample Navy" abbreviation="NVY" color="#0c2340" />
                Followed team
                <FollowMark />
                <span className="ml-auto inline-flex gap-1">
                  <OutcomeSquare result="W" size={24} />
                  <OutcomeSquare result="L" size={24} />
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2 border-t border-hair bg-tint px-3 py-2.5">
                <SpotChip>Live</SpotChip>
                <OutcomeSquare result="W" size={24} />
                <OutcomeSquare result="D" size={24} />
                <OutcomeSquare result="L" size={24} />
                <FollowMark />
                <span className="text-ink-2">secondary</span>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="solid">Solid</Button>
            <Button>Outline</Button>
          </div>
          <ScoreLine />
        </div>
      ))}
    </div>
  );
}

/** Measured contrast of the live tokens, read back from the page itself. */
function ContrastTable({ stamp }: { stamp: string }) {
  const [rows, setRows] = useState<
    { pair: string; ratio: number; floor: number }[]
  >([]);

  useEffect(() => {
    const style = getComputedStyle(document.documentElement);
    const token = (name: string) => style.getPropertyValue(name).trim();
    const textFloor =
      document.documentElement.getAttribute("data-theme") === "contrast"
        ? 7
        : 4.5;
    const pairs: [string, string, string, number][] = [];
    for (const surface of ["sheet", "ground", "tint"]) {
      for (const text of ["ink", "ink-2", "muted", "spot-text", "alert"]) {
        pairs.push([text, surface, `--bs-${surface}`, textFloor]);
      }
    }
    pairs.push(["on-spot", "spot", "--bs-spot", textFloor]);
    pairs.push(["on-block", "block", "--bs-block", textFloor]);
    pairs.push(["on-block-2", "block", "--bs-block", textFloor]);
    pairs.push(["on-tab", "tab", "--bs-tab", textFloor]);
    pairs.push(["on-band", "band", "--bs-band", textFloor]);
    pairs.push(["on-band-2", "band", "--bs-band", textFloor]);
    pairs.push(["on-draw", "draw", "--bs-draw", textFloor]);
    setRows(
      pairs.map(([text, surface, surfaceVar, floor]) => ({
        pair: `${text} on ${surface}`,
        ratio: contrastRatio(token(`--bs-${text}`), token(surfaceVar)),
        floor,
      })),
    );
  }, [stamp]);

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-x-6 tabular-nums">
      {rows.map((row) => (
        <div
          key={row.pair}
          className="flex items-baseline justify-between gap-3 border-t border-hair py-1.5"
        >
          <span className="text-ink-2">{row.pair}</span>
          <span className="font-condensed text-lg font-bold">
            {row.ratio.toFixed(2)}
            <span className="ml-1.5 text-sm font-semibold text-muted">
              {row.ratio >= row.floor ? "ok" : `under ${row.floor}`}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export default function PiecesGallery() {
  const [state, setState] = useState<GalleryState>(() => ({
    ...getAppearance(),
    color: SAMPLE_COLORS[0].color,
    ...Object.fromEntries(
      Object.entries(readHash()).filter(([, value]) => value !== undefined),
    ),
  }));
  const [view, setView] = useState<"week" | "month" | "agenda">("week");
  const [league, setLeague] = useState<"al" | "nl">("al");
  const [picked, setPicked] = useState("ATH");

  // Restyle the page for this visit. DOM only: nothing is persisted. A
  // layout effect, so the contrast table's effect below reads the new tokens.
  useLayoutEffect(() => {
    applyAppearance(state);
    const root = document.documentElement;
    const vars =
      state.palette === "team" ? teamAccentVars(state.color, state.theme) : null;
    const names = [
      "--sd-accent",
      "--sd-on-accent",
      "--sd-accent-text",
      "--sd-accent-edge",
      "--sd-accent-edge-block",
    ] as const;
    for (const name of names) {
      if (vars === null) root.style.removeProperty(name);
      else root.style.setProperty(name, vars[name]);
    }
  }, [state]);

  const teamAllowed =
    THEMES.find((option) => option.id === state.theme)?.teamPalette ?? false;

  return (
    <div className="min-h-screen bg-ground text-ink">
      <div className="mx-auto flex max-w-[90rem] flex-col gap-7 px-4 py-7 sm:px-8">
        <header className="flex flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
            <h1 className="bs-title">Pieces</h1>
            <div className="bs-deck">Box Score · shared parts</div>
          </div>
          <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
            <div className="flex flex-col gap-1.5">
              <span className="bs-label leading-none">Base</span>
              <Segmented
                label="Base"
                value={state.theme}
                onChange={(theme) => setState({ ...state, theme })}
                options={THEMES.map((option) => ({
                  value: option.id,
                  label: option.label,
                }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="bs-label leading-none">Colours</span>
              <Segmented
                label="Colours"
                value={teamAllowed ? state.palette : "brand"}
                onChange={(palette) => setState({ ...state, palette })}
                options={[
                  { value: "brand", label: "Box Score orange" },
                  { value: "team", label: "Team" },
                ]}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="bs-label leading-none">Sample team colour</span>
              <div role="group" aria-label="Sample team colour" className="flex flex-wrap gap-1.5">
                {SAMPLE_COLORS.map((sample) => (
                  <Button
                    key={sample.color}
                    aria-pressed={state.color === sample.color}
                    onClick={() => setState({ ...state, color: sample.color })}
                    className="gap-2.5 pl-2 pr-3.5"
                  >
                    <TeamBlock
                      name={sample.name}
                      abbreviation={sample.code}
                      color={sample.color}
                    />
                    {sample.name}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </header>

        <section aria-label="Every theme">
          <SectionHead
            title="Every theme"
            note="The same markup in five scopes. Team uses the sample colour picked above."
          />
          <div className="bs-sheet p-4">
            <EveryTheme color={state.color} />
          </div>
        </section>

        <div className="flex flex-wrap items-start gap-7">
          <div className="flex min-w-0 flex-[999_1_35rem] flex-col gap-7">
            <Standings />

            <Block title="Team blocks">
              {SAMPLE_COLORS.map((sample) => (
                <TeamBlock
                  key={sample.color}
                  name={sample.name}
                  abbreviation={sample.code}
                  color={sample.color}
                  labelled
                />
              ))}
              <TeamBlock name="No Colour United" abbreviation="NCU" color={null} labelled />
              <TeamBlock name="Four Letter Club" abbreviation="LAFC" color="#c39e6d" labelled />
              <TeamBlock name="Jannik Sinner" abbreviation="J. Sinne" color={null} labelled />
              <TeamBlock name="Sample Navy" abbreviation="NVY" color="#0c2340" size={40} labelled />
              <TeamBlock name="Sample Yellow" abbreviation="YEL" color="#ffc52f" size={52} labelled />
              <TeamBlock name="Sample Royal" abbreviation="ROY" color="#005a9c" size={60} labelled />
              <TeamBlock name="Real Sample" color="#005a9c" labelled />
              <TeamBlock name="Sample Navy" abbreviation="NVY" color="#0c2340" size={56} labelled />
              <TeamBlock name="Sample Yellow" abbreviation="YEL" color="#ffc52f" size={56} labelled />
              <TeamBlock name="Sample Red" abbreviation="RED" color="#bd3039" size={64} labelled />
              <TeamBlock name="No Colour United" abbreviation="NCU" size={64} labelled />
            </Block>

            <Block title="Result squares">
              {([34, 30, 26, 24] as const).map((size) => (
                <span key={size} className="inline-flex items-center gap-1">
                  <OutcomeSquare result="W" size={size} />
                  <OutcomeSquare result="L" size={size} />
                  <OutcomeSquare result="D" size={size} />
                  <span className="bs-label ml-1.5">{size}</span>
                </span>
              ))}
              <FormStrip
                label="Form, most recent first"
                size={34}
                results={["L", "W", "L", "L", "W", "D", "W", "W"]}
              />
            </Block>
          </div>

          <div className="flex min-w-0 flex-[1_1_22.5rem] flex-col gap-7">
            <Block title="Buttons">
              <Button variant="solid">Box score</Button>
              <Button>Play-by-play</Button>
              <Button>
                All teams
                <ChevronDown />
              </Button>
              <Button variant="solid">
                <Icon>
                  <path d="M4 6h16v14H4zM4 10h16M8 3v4M16 3v4M12 13v4M10 15h4" />
                </Icon>
                Subscribe
              </Button>
              <Button disabled>Disabled</Button>
              <Button disabled aria-pressed>
                Disabled, on
              </Button>
              <Button icon aria-label="Kiosk mode" aria-pressed={false}>
                <Icon size={20}>
                  <rect x="2" y="3" width="20" height="14" rx="1" />
                  <path d="M8 21h8M12 17v4" />
                </Icon>
              </Button>
              <Button icon aria-label="Kiosk mode on" aria-pressed>
                <Icon size={20}>
                  <rect x="2" y="3" width="20" height="14" rx="1" />
                  <path d="M8 21h8M12 17v4" />
                </Icon>
              </Button>
              <Button size="lg" aria-haspopup="listbox">
                MLB
                <ChevronDown size={20} />
              </Button>
              <div role="group" aria-label="Team" className="flex flex-wrap gap-1.5">
                {[
                  { code: "ATH", name: "Athletics", color: "#efb21e" },
                  { code: "ARI", name: "Diamondbacks", color: "#a71930" },
                ].map((team) => (
                  <Button
                    key={team.code}
                    aria-pressed={picked === team.code}
                    onClick={() => setPicked(team.code)}
                    className="gap-2.5 pl-2 pr-3.5"
                  >
                    <TeamBlock name={team.name} abbreviation={team.code} color={team.color} />
                    {team.name}
                  </Button>
                ))}
              </div>
            </Block>

            <Block title="Segmented controls">
              <Segmented
                label="League views"
                kind="tabs"
                value={view}
                onChange={setView}
                options={[
                  { value: "week", label: "Standings" },
                  { value: "month", label: "Leaders" },
                  { value: "agenda", label: "Bracket" },
                ]}
              />
              <Segmented
                label="Conference"
                value={league}
                onChange={setLeague}
                options={[
                  { value: "al", label: "American League" },
                  { value: "nl", label: "National League" },
                ]}
              />
              <div className="flex flex-wrap items-center gap-2.5">
                <Segmented
                  label="Calendar view"
                  joined
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "week", label: "Week" },
                    { value: "month", label: "Month" },
                    { value: "agenda", label: "Agenda" },
                  ]}
                />
                <Button>Today</Button>
              </div>
            </Block>

            <Block title="Spot, marker, tag">
              <SpotChip size="lg">Live</SpotChip>
              <SpotChip>Live</SpotChip>
              <SpotChip size="sm">Today</SpotChip>
              <span className="inline-flex items-center gap-2 font-bold">
                Followed team <FollowMark />
              </span>
              <span className="inline-flex items-center gap-2.5 font-condensed text-[2.5rem] font-bold uppercase leading-none">
                Athletics <FollowMark size="lg" />
              </span>
              <span className="bs-note inline-flex items-center gap-2">
                <FollowMark boxed label={null} />
                Team you follow
              </span>
              <span className="bs-note inline-flex items-center gap-2">
                <Tag>Leader</Tag>
                Division leader
              </span>
              <span className="bs-label">Small caps label</span>
            </Block>
          </div>
        </div>

        <section aria-label="Score line">
          <SectionHead
            title="Score line"
            note="The ink band under the masthead, with the spot on it"
          />
          <ScoreLine />
        </section>

        <section aria-label="Type">
          <SectionHead title="Type" note="Barlow Condensed for display, Barlow for text" />
          <div className="bs-sheet flex flex-wrap items-end gap-x-10 gap-y-5 p-5">
            <div className="font-condensed text-[6rem] font-bold leading-[0.8]">4</div>
            <div className="font-condensed text-[6rem] font-semibold leading-[0.8] text-muted">3</div>
            <div className="font-condensed text-[2.5rem] font-bold uppercase leading-none">
              Athletics
            </div>
            <div className="font-condensed text-3xl font-bold leading-none">10:10 PM</div>
            <div className="max-w-md">
              <p className="m-0 font-medium">Body text in Barlow at fifteen pixels.</p>
              <p className="m-0 text-ink-2">Secondary text carries the supporting line.</p>
              <p className="m-0 text-sm text-muted">Muted text is for labels and footnotes.</p>
              <p className="m-0 text-sm text-alert">Alert text is for genuine failures only.</p>
            </div>
            <div className="tabular-nums">
              <div>.509 · 55–53 · 13.5</div>
              <div className="font-bold">1,234,567</div>
            </div>
          </div>
        </section>

        <section aria-label="Measured contrast">
          <SectionHead
            title="Measured contrast"
            note={`Read from this page's tokens. Floor: ${state.theme === "contrast" ? "7:1 (AAA)" : "4.5:1 (AA)"}.`}
          />
          <div className="bs-sheet px-5 py-3">
            <ContrastTable
              stamp={`${state.theme}/${teamAllowed ? state.palette : "brand"}/${state.color}`}
            />
            <p className="bs-note mb-0 mt-3">
              Sample team colour {state.color}: ink reads on it at{" "}
              {contrastRatio("#101418", fitFill(state.color).fill).toFixed(2)}:1,
              white at {contrastRatio("#ffffff", fitFill(state.color).fill).toFixed(2)}:1.
              The block is set in {fitFill(state.color).on === "#ffffff" ? "white" : "ink"}.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
