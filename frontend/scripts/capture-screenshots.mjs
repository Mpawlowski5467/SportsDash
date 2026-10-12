// Regenerate the README screenshots in docs/screenshots/ from the live app.
//
// Prereqs: the frontend dev server on http://localhost:5173 (bun run dev) with
// a backend behind it that has followed teams + refreshed data. Then:
//
//   cd frontend && bun scripts/capture-screenshots.mjs
//
// Each shot navigates the real UI (clicking tabs / sub-tabs / the pickers)
// and writes a PNG. Shots are independent: one failing (e.g. an empty view)
// is logged and skipped, not fatal, so a sparse view never clobbers a good
// existing PNG.
//
// What a shot shows depends on what is followed and on the date, so the
// choices are environment variables rather than edits to this file. Pick
// leagues that have data today: a mid-season table looks far better than a
// preseason all-zeros one, and a bracket only exists during a postseason.
//
//   SD_BASE_URL          where the app is               (http://localhost:5173)
//   SD_STANDINGS_LEAGUE  League picker option           (NFL)
//   SD_STANDINGS_GROUP   conference button, if any      (unset = the default)
//   SD_LEADERS_LEAGUE    League picker option           (NHL)
//   SD_BRACKET_LEAGUE    League picker option           (MLB)
//   SD_RESULTS_TEAM      Results team picker option     (unset = the default)
//   SD_MAP_TEAM          team whose stadium the close-up flies to
//                                                       (unset = the first pin)
//   SD_MAP_DRAG          "dx,dy" pixels to drag the map before the wide shot,
//                        when the default view misses the pins   (unset)
//   SD_TEAM_COLOURS      followed team id for the Team colours tile
//                                                       (unset = the app's pick)
//   SD_TIMEZONE          browser time zone              (the machine's)
//   SD_FIXED_TIME        ISO time to pin the browser clock to, for a backend
//                        that serves a recorded moment  (unset = now)
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";

const env = process.env;
const BASE = env.SD_BASE_URL ?? "http://localhost:5173";
const OUT = fileURLToPath(new URL("../../docs/screenshots/", import.meta.url));
const LEAGUES = {
  standings: env.SD_STANDINGS_LEAGUE ?? "NFL",
  leaders: env.SD_LEADERS_LEAGUE ?? "NHL",
  bracket: env.SD_BRACKET_LEAGUE ?? "MLB",
};

// The design width, so every view is laid out as it was drawn; 1.5x keeps
// text sharp on a retina display without doubling the size of every PNG.
const WIDTH = 1440;
const SCALE = 1.5;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
await mkdir(OUT, { recursive: true });

/** A fresh page in one appearance, with the app loaded and settled. */
async function open({ theme = "light", palette = "brand", height = 960 } = {}) {
  const context = await browser.newContext({
    viewport: { width: WIDTH, height },
    deviceScaleFactor: SCALE,
    ...(env.SD_TIMEZONE ? { timezoneId: env.SD_TIMEZONE } : {}),
  });
  // Seeded before the app boots, so a theme picked in this browser profile
  // on some other day never leaks into the README.
  await context.addInitScript(
    ([t, p, favorite]) => {
      window.localStorage.setItem("sportsdash.theme", t);
      window.localStorage.setItem("sportsdash.palette", p);
      if (favorite) window.localStorage.setItem("sportsdash.favoriteTeam", favorite);
    },
    [theme, palette, env.SD_TEAM_COLOURS ?? null],
  );
  const page = await context.newPage();
  if (env.SD_FIXED_TIME) await page.clock.setFixedTime(new Date(env.SD_FIXED_TIME));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Today", exact: true }).first().waitFor();
  await settle(page, 1200);
  return page;
}

async function settle(page, ms = 900) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await sleep(ms);
}
async function tab(page, name) {
  await page.getByRole("button", { name, exact: true }).first().click();
  await settle(page, 1200);
}
async function subtab(page, name) {
  await page.getByRole("tab", { name, exact: true }).first().click();
  await settle(page, 600);
}
// Open a custom <Select> by its aria-label and choose an option by label.
async function pick(page, ariaLabel, optionLabel) {
  await page.locator(`button[aria-label="${ariaLabel}"]`).first().click();
  await sleep(250);
  await page.locator('[role="option"]', { hasText: optionLabel }).first().click();
  await settle(page, 1200);
}
async function shoot(page, name, options = {}) {
  await page.screenshot({ path: `${OUT}${name}.png`, ...options });
  console.log(`  ✓ ${name}.png`);
}
async function safely(name, fn) {
  try {
    await fn();
  } catch (err) {
    console.log(`  ✗ ${name}: ${err.message.split("\n")[0]}`);
  }
}

console.log(`Capturing from ${BASE} → ${OUT}`);
const page = await open();

// Today — the live hero (or the day's first list), the games under it and
// the standings boxes beside them.
await safely("today", async () => {
  await tab(page, "Today");
  await shoot(page, "today");
});

// Matchup — the game sheet, win probability and the three history sheets.
await safely("matchup", async () => {
  await tab(page, "Matchup");
  await page.setViewportSize({ width: WIDTH, height: 1420 });
  await settle(page, 600);
  await shoot(page, "matchup");
  await page.setViewportSize({ width: WIDTH, height: 960 });
});

// Calendar — the week of day columns (the default view). CalendarView marks
// whichever of its three grids is up with data-calendar-grid="week" |
// "month" | "agenda"; it is there once the range's games have loaded.
await safely("calendar", async () => {
  await tab(page, "Calendar");
  await page.locator('[data-calendar-grid="week"]').first().waitFor({ timeout: 15000 });
  await settle(page, 900);
  await shoot(page, "calendar");
});

// Standings — point at an in-season league for a populated table.
await safely("standings", async () => {
  await tab(page, "League");
  await subtab(page, "Standings");
  await pick(page, "Choose league", LEAGUES.standings);
  if (env.SD_STANDINGS_GROUP) {
    await page.getByRole("button", { name: env.SD_STANDINGS_GROUP, exact: true }).first().click();
    await settle(page, 900);
  }
  await shoot(page, "standings");
});

// Leaders — stat leaders / Golden Boot (needs a league with leaders loaded).
await safely("leaders", async () => {
  await subtab(page, "Leaders");
  await pick(page, "Choose league", LEAGUES.leaders);
  await shoot(page, "leaders");
});

// Bracket — playoff series / knockout rounds. The rounds are a short strip,
// so the shot is cropped to the sub-tabs and the rounds under them.
await safely("bracket", async () => {
  await subtab(page, "Bracket");
  await pick(page, "Choose league", LEAGUES.bracket);
  const tabs = await page.getByRole("tablist", { name: "League views" }).boundingBox();
  const rounds = await page
    .locator("main section[aria-label]:visible")
    .evaluateAll((sections) => Math.max(...sections.map((s) => s.getBoundingClientRect().bottom)));
  if (tabs === null || !Number.isFinite(rounds)) throw new Error("no bracket on screen");
  await shoot(page, "bracket", {
    clip: { x: 0, y: tabs.y - 20, width: WIDTH, height: rounds - tabs.y + 44 },
  });
});

// Results — one team's season: the tiles, then a table per month.
await safely("results", async () => {
  await tab(page, "Results");
  if (env.SD_RESULTS_TEAM) await pick(page, "Choose team", env.SD_RESULTS_TEAM);
  await shoot(page, "results");
});

// News — the top story and the first of the list under it.
await safely("news", async () => {
  await tab(page, "News");
  await settle(page, 1500);
  await shoot(page, "news");
});

// Map — every followed team's ground, then one of them up close. MapLibre
// needs a moment to fetch tiles and settle after each move. Map imagery is
// the one thing here PNG compresses badly, so these two are shot at 1x.
await safely("map", async () => {
  await page.setViewportSize({ width: WIDTH, height: 810 });
  await tab(page, "Map");
  await page.locator(".maplibregl-canvas").first().waitFor({ timeout: 20000 });
  await settle(page, 5000);
  await page.getByRole("button", { name: "Stadiums", exact: true }).first().click();
  await settle(page, 5000);
  if (env.SD_MAP_DRAG) {
    const [dx, dy] = env.SD_MAP_DRAG.split(",").map(Number);
    // In strokes that stay inside the map: one long drag would run off it.
    const strokes = Math.max(1, Math.ceil(Math.abs(dx) / 800));
    for (let i = 0; i < strokes; i++) {
      const from = dx > 0 ? 500 : 1300;
      await page.mouse.move(from, 520);
      await page.mouse.down();
      await page.mouse.move(from + dx / strokes, 520 + dy / strokes, { steps: 16 });
      await page.mouse.up();
      await sleep(900);
    }
    await settle(page, 6000);
  }
  await shoot(page, "map", { scale: "css" });
});
await safely("map-stadium", async () => {
  const pin = env.SD_MAP_TEAM
    ? page.locator(`button.sd-map-pin[aria-label^="${env.SD_MAP_TEAM}"]`).first()
    : page.locator("button.sd-map-pin").first();
  await pin.click({ timeout: 8000 });
  await sleep(9000); // the fly-in, then the 3D buildings
  await settle(page, 5000);
  await shoot(page, "map-stadium", { scale: "css" });
});
await page.context().close();

// Themes — Today in the three bases and with a team's colours, as one image.
await safely("themes", async () => {
  const tiles = [];
  for (const appearance of [
    { theme: "light", palette: "brand" },
    { theme: "dark", palette: "brand" },
    { theme: "contrast", palette: "brand" },
    { theme: "light", palette: "team" },
  ]) {
    const themed = await open({ ...appearance, height: 810 });
    tiles.push((await themed.screenshot()).toString("base64"));
    await themed.context().close();
  }
  const sheet = await browser.newPage({
    viewport: { width: WIDTH, height: 810 },
    deviceScaleFactor: SCALE,
  });
  await sheet.setContent(
    `<body style="margin:0;display:grid;grid-template-columns:1fr 1fr;gap:6px;background:#101418">` +
      tiles.map((png) => `<img src="data:image/png;base64,${png}" style="width:100%;display:block">`).join("") +
      `</body>`,
  );
  await sheet.waitForLoadState("load");
  await sheet.screenshot({ path: `${OUT}themes.png`, fullPage: true });
  console.log("  ✓ themes.png");
  await sheet.close();
});

await browser.close();
console.log("Done.");
