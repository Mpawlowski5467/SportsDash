// Regenerate the README screenshots in docs/screenshots/ from the live app.
//
// Prereqs: the frontend dev server on http://localhost:5173 (bun run dev) with
// a backend behind it that has followed teams + refreshed data. Then:
//
//   cd frontend && bun scripts/capture-screenshots.mjs
//
// Each shot navigates the real UI (clicking tabs / sub-tabs / the league
// picker) and writes a PNG at 2x for crisp output. Pick leagues per shot
// (LEAGUES below) that actually have data for the current date — a mid-season
// standings table looks far better than a preseason all-zeros one, and a
// finished playoff bracket beats an undrawn one full of "TBD" placeholders.
// Shots are independent: one failing (e.g. an empty view) is logged and
// skipped, not fatal, so a sparse view never clobbers a good existing PNG.
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";

const BASE = process.env.SD_BASE_URL ?? "http://localhost:5173";
const OUT = fileURLToPath(new URL("../../docs/screenshots/", import.meta.url));

// Which league to point the league-scoped shots at. Change these to whatever
// is in-season / interesting when you run it.
const LEAGUES = { standings: "MLB", leaders: "MLB", bracket: "NBA" };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 2 });
await mkdir(OUT, { recursive: true });

async function view(w, h) {
  await page.setViewportSize({ width: w, height: h });
}
async function tab(name) {
  await page.getByRole("button", { name, exact: true }).first().click();
}
async function subtab(name) {
  await page.getByRole("tab", { name, exact: true }).first().click();
}
// Open a custom <Select> by its aria-label and choose an option by label.
async function pick(ariaLabel, optionLabel) {
  await page.locator(`button[aria-label="${ariaLabel}"]`).first().click();
  await sleep(250);
  await page.locator('[role="option"]', { hasText: optionLabel }).first().click();
  await sleep(900);
}
async function shoot(name) {
  await page.screenshot({ path: `${OUT}${name}.png` });
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
await view(1200, 1320);
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Today", exact: true }).waitFor();
await sleep(1500);

// Today — 2-column game cards; short viewport crops the empty space below.
await safely("today", async () => {
  await tab("Today");
  await view(1200, 560);
  await sleep(1200);
  await shoot("today");
});

// Calendar — month grid (FullCalendar).
await safely("calendar", async () => {
  await view(1200, 1320);
  await tab("Calendar");
  await page.locator(".fc").first().waitFor({ timeout: 15000 });
  await sleep(1800);
  await shoot("calendar");
});

// Standings — point at an in-season league for a populated table.
await safely("standings", async () => {
  await tab("League");
  await sleep(500);
  await subtab("Standings");
  await pick("Choose league", LEAGUES.standings);
  await shoot("standings");
});

// Leaders — stat leaders / Golden Boot (needs a league with leaders loaded).
await safely("leaders", async () => {
  await subtab("Leaders");
  await pick("Choose league", LEAGUES.leaders);
  await shoot("leaders");
});

// Bracket — playoff series / knockout (BracketView's picker = "Choose bracket").
await safely("bracket", async () => {
  await subtab("Bracket");
  await sleep(400);
  await pick("Choose bracket", LEAGUES.bracket);
  await view(1200, 660);
  await sleep(800);
  await shoot("bracket");
});

// Map — MapLibre needs a moment to fetch tiles and settle.
await safely("map", async () => {
  await view(1200, 1320);
  await tab("Map");
  await page.locator(".maplibregl-canvas").first().waitFor({ timeout: 20000 });
  await sleep(6000);
  await shoot("map");
});

// Onboarding — the "choose leagues" catalog (Settings → Manage teams).
await safely("onboarding-leagues", async () => {
  await page.locator('button[aria-label="Settings"]').first().click();
  await sleep(400);
  await page.getByText("Manage teams", { exact: true }).click();
  await sleep(2000);
  await shoot("onboarding-leagues");
});

await browser.close();
console.log("Done.");
