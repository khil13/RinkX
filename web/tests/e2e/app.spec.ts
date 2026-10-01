import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const CONFIGURED = "http://127.0.0.1:4173/"; // league data replayed from recorded NHL responses
const SETUP = "http://127.0.0.1:4174/";
const EMPTY = "http://127.0.0.1:4175/";
const { passphrase } = JSON.parse(readFileSync("tests/e2e/.sites/secrets.json", "utf8")) as { passphrase: string };

async function unlock(page: Page, remember = true, site = CONFIGURED) {
  await page.goto(site);
  await page.getByLabel("Passphrase").fill(passphrase);
  if (!remember) await page.getByLabel("Remember on this device").uncheck();
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test("wrong passphrase is rejected and nothing is shown", async ({ page }) => {
  await page.goto(CONFIGURED);
  await page.getByLabel("Passphrase").fill("not the passphrase at all");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByText("That passphrase doesn't unlock this site.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toHaveCount(0);
});

test("unlocks pipeline-encrypted data and reports honest empty state", async ({ page }) => {
  await unlock(page, true, EMPTY);
  await expect(page.getByText(/Live data unavailable\. No data sources are connected yet/)).toBeVisible();
  await expect(page.locator("li").filter({ hasText: "Never fetched" })).toHaveCount(7);
  await expect(page.getByText("DEV BUILD")).toHaveCount(0); // prod bundle: no synthetic banner

  // Admin decrypts admin/health.json.enc, written by the Python pipeline.
  await page.goto(`${EMPTY}#/admin`);
  await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible();
  await expect(page.getByText("new store (first run)")).toBeVisible();

  // With no schedule data, the Games page says so instead of showing an empty slate.
  await page.goto(`${EMPTY}#/games`);
  await expect(page.getByText(/Live data unavailable for/)).toBeVisible();
  await expect(page.getByText("No NHL games scheduled.")).toHaveCount(0);
});

test("remembered key survives reload; Lock forgets it", async ({ page }) => {
  await unlock(page, true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await page.getByRole("button", { name: "Lock" }).click();
  await expect(page.getByLabel("Passphrase")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Passphrase")).toBeVisible();
});

test("not-remembered key is gone after reload", async ({ page }) => {
  await unlock(page, false);
  await page.reload();
  await expect(page.getByLabel("Passphrase")).toBeVisible();
});

test("unbuilt pages show no sample data", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/props/best`);
  await expect(page.getByText(/Not built yet\. This page arrives in Phase 6/)).toBeVisible();
});

test("setup page generates keys the Python pipeline accepts", async ({ page }) => {
  await page.goto(SETUP);
  await expect(page.getByText("RINKX · Setup")).toBeVisible();
  const p = "setup flow passphrase with words";
  await page.getByPlaceholder("Passphrase", { exact: true }).fill("too short");
  await expect(page.getByText(/Use at least 20 characters/)).toBeVisible();
  await page.getByPlaceholder("Passphrase", { exact: true }).fill(p);
  await page.getByPlaceholder("Repeat passphrase").fill(p);
  await page.getByRole("button", { name: "Generate keys" }).click();

  const codes = page.locator("code.num.break-all");
  await expect(codes).toHaveCount(3, { timeout: 15_000 });
  const [dataKey, storeKey, keyfile] = await codes.allTextContents();
  expect(dataKey).toMatch(/^[A-Za-z0-9+/]{43}=$/);
  expect(storeKey).toMatch(/^[A-Za-z0-9+/]{43}=$/);
  expect(storeKey).not.toBe(dataKey);

  // The browser-made keyfile must unwrap (in Python) to exactly the DATA_KEY it displayed,
  // and pass the pipeline's key_check, or the pipeline would refuse to publish.
  const py = process.env.RINKX_PYTHON ?? "python3";
  const out = execFileSync(
    py,
    [
      "-c",
      [
        "import json,sys; sys.path.insert(0,'../pipeline')",
        "from rinkx import crypto",
        "kf=json.loads(sys.argv[1]); key=crypto.unwrap_keyfile(kf, sys.argv[2])",
        "assert crypto.keyfile_matches(kf, key)",
        "print(crypto.b64e(key))",
      ].join("\n"),
      keyfile!,
      p,
    ],
    { encoding: "utf8" },
  ).trim();
  expect(out).toBe(dataKey);
});

test("pages fit the screen with no horizontal page scroll", async ({ page }) => {
  await unlock(page);
  for (const route of ["", "#/games", "#/games/2025021012", "#/players/8477960"]) {
    await page.goto(CONFIGURED + route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, route || "dashboard").toBeLessThanOrEqual(0);
  }
});

test("daily slate shows real games with records, rest and honest gaps", async ({ page }) => {
  await unlock(page);
  await expect(page.getByText(/Today · /)).toBeVisible();
  await page.goto(`${CONFIGURED}#/games`);
  await expect(page.getByText(/13 games/)).toBeVisible();
  const card = page.locator("a", { hasText: "Bruins" }).filter({ hasText: "Kings" });
  await expect(card).toContainText("Final/OT");
  await expect(card).toContainText("TD Garden");
  await expect(card).toContainText(/\d+-\d+-\d+ · L10/); // official record + last 10
  await expect(card.getByText("Not connected yet").first()).toBeVisible(); // goalies, odds: not guessed

  // Only one schedule week is recorded, so rest before 03-10 is unknown, not "first game".
  await expect(card.getByText("Rest unknown").first()).toBeVisible();
  await expect(page.getByText("First game")).toHaveCount(0);

  // Dates outside the fetched schedule are absent, not shown as "no games".
  await expect(page.getByRole("button", { name: /Mar 9/ })).toHaveCount(0);
});

test("game page: box score, context, and missing data labeled", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/games/2025021012`);
  await expect(page.getByText("Final/OT")).toBeVisible();
  await expect(page.getByText("1 – 2")).toBeVisible();
  await expect(page.getByRole("link", { name: "Jeremy Swayman" }).first()).toBeVisible();
  await expect(page.getByText("15 saves on 16 shots · 1 GA")).toBeVisible();
  await expect(page.getByText("Insufficient data").first()).toBeVisible(); // team metrics: too few games
  await expect(page.getByText(/Records from official NHL standings as of 2026-03-10/)).toBeVisible();

  await page.getByRole("link", { name: "Jeremy Swayman" }).first().click();
  await expect(page.getByRole("heading", { name: "Jeremy Swayman" })).toBeVisible();
  await expect(page.getByText("Game log (1)")).toBeVisible();
  await expect(page.getByText("Every game is listed. Nothing is filtered.")).toBeVisible();
});

test("player search finds a player from the recorded rosters", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/players`);
  await page.getByLabel("Search players").fill("kempe");
  await page.getByRole("link", { name: /Adrian Kempe/ }).click();
  await expect(page.getByRole("heading", { name: "Adrian Kempe" })).toBeVisible();
  await expect(page.getByText("LAK · R")).toBeVisible();
});

test("game outside the published window says so", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/games/1`);
  await expect(page.getByText(/Live data unavailable for this game/)).toBeVisible();
});
