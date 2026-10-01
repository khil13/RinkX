import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const CONFIGURED = "http://127.0.0.1:4173/";
const SETUP = "http://127.0.0.1:4174/";
const { passphrase } = JSON.parse(readFileSync("tests/e2e/.sites/secrets.json", "utf8")) as { passphrase: string };

async function unlock(page: Page, remember = true) {
  await page.goto(CONFIGURED);
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
  await unlock(page);
  await expect(page.getByText(/Live data unavailable\. No data sources are connected yet/)).toBeVisible();
  await expect(page.locator("li").filter({ hasText: "Never fetched" })).toHaveCount(7);
  await expect(page.getByText("DEV BUILD")).toHaveCount(0); // prod bundle: no synthetic banner

  // Admin decrypts admin/health.json.enc, written by the Python pipeline.
  await page.goto(`${CONFIGURED}#/admin`);
  await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible();
  await expect(page.getByText("v1", { exact: true })).toBeVisible();
  await expect(page.getByText("new store (first run)")).toBeVisible();
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

test("layout fits the screen with no horizontal scroll", async ({ page }) => {
  await unlock(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
