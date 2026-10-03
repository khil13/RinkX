import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const CONFIGURED = "http://127.0.0.1:4173/"; // league data replayed from recorded NHL responses
const SETUP = "http://127.0.0.1:4174/";
const EMPTY = "http://127.0.0.1:4175/";
const MODELS = "http://127.0.0.1:4176/"; // DEV build of the SYNTHETIC test league (tests/synth.py)
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

test("every page in the menu is built: none shows a placeholder", async ({ page }) => {
  await unlock(page);
  for (const route of ["/", "/games", "/props", "/props/best", "/players", "/models", "/goalies", "/lines", "/parlay",
    "/performance", "/news", "/settings", "/admin"]) {
    await page.goto(`${CONFIGURED}#${route}`);
    await expect(page.locator("main h1").first()).toBeVisible();
    await expect(page.getByText(/Not built yet/)).toHaveCount(0);
  }
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
  for (const route of ["", "#/games", "#/games/2025021012", "#/players/8477960", "#/models"]) {
    await page.goto(CONFIGURED + route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, route || "dashboard").toBeLessThanOrEqual(0);
  }
  await unlock(page, true, MODELS);
  for (const route of ["#/games/2025029999", "#/players/8000001", "#/models", "#/props", "#/props/best", "#/performance", "#/news", "#/parlay"]) {
    await page.goto(MODELS + route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `models site ${route}`).toBeLessThanOrEqual(0);
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
  await expect(page.getByRole("heading", { name: "Box score" })).toBeVisible(); // the page chunk has loaded
  await expect(page.getByText("Final/OT")).toBeVisible();
  await expect(page.getByText("1 – 2")).toBeVisible();
  await expect(page.getByRole("link", { name: "Jeremy Swayman" }).first()).toBeVisible();
  await expect(page.getByText("15 saves on 16 shots · 1 GA")).toBeVisible();
  await expect(page.getByText("Insufficient data").first()).toBeVisible(); // team metrics: too few games
  await expect(page.getByText(/Records from official NHL standings as of 2026-03-10/)).toBeVisible();

  await page.getByRole("link", { name: "Jeremy Swayman" }).first().click();
  await expect(page.getByRole("heading", { name: "Jeremy Swayman" })).toBeVisible();
  await expect(page.getByText("Game log 2025-26 (1 played)")).toBeVisible();
  await expect(page.getByText("Every game is listed, including games missed. Nothing is filtered.")).toBeVisible();
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

test("player hit rates count every threshold and say what they include", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/players/8477960`); // Adrian Kempe: 2 SOG, 6 attempts, 1 assist
  const table = page.getByRole("table", { name: "Shots on goal hit rates" });
  await expect(table).toBeVisible();
  const row = (k: string) => table.locator("tr", { has: page.locator("td", { hasText: new RegExp(`^${k}\\+$`) }) });
  await expect(row("2").locator("td").nth(1)).toContainText("1/1"); // 2+ SOG in L5
  await expect(row("3").locator("td").nth(1)).toContainText("0/1"); // not 3+
  await page.getByRole("tab", { name: "Shot attempts" }).click();
  await expect(page.getByRole("table", { name: "Shot attempts hit rates" })).toBeVisible();
  await expect(page.getByText(/Counts games played, most recent first/)).toBeVisible();
  // Enriched log columns: PP time and shot attempts from the official stats API.
  await expect(page.locator("td", { hasText: /^2:02$/ })).toBeVisible(); // 122 s on the power play
});

test("real data with too little history: no projections, and the reason is stated", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/models`);
  await expect(page.getByRole("heading", { name: "Model tests" })).toBeVisible();
  await expect(page.getByText(/Not enough completed games loaded to test the models yet/)).toBeVisible();
  await expect(page.getByText("PASSED · PUBLISHED")).toHaveCount(0);
  await page.goto(`${CONFIGURED}#/players/8477960`);
  await expect(page.getByText(/No projections yet: the models haven't been tested/)).toBeVisible();
});

test("synthetic league: projections, Explain, and before/after from a Quick Entry goalie", async ({ page }) => {
  await unlock(page, true, MODELS);
  await expect(page.getByText(/DEV BUILD · may contain SYNTHETIC data/)).toBeVisible(); // always labelled
  await page.goto(`${MODELS}#/games/2025029999`);
  const home = page.getByRole("table", { name: "T00 projections" });
  await expect(home).toBeVisible();
  await expect(page.getByRole("table", { name: "T01 projections" })).toBeVisible();
  await expect(page.getByText("CONFIRMED", { exact: true })).toBeVisible(); // away starter, entered via Quick Entry with a source
  await expect(page.getByText(/PROJECTED · \d+%/)).toBeVisible(); // home starter, from recent starts
  await expect(page.getByText(/Lineups aren't confirmed/)).toBeVisible();
  // Game model: team and game goal totals (tested and passed on this league), no odds involved.
  const outlook = page.getByLabel("Game outlook");
  await expect(outlook).toContainText("Total goals");
  await expect(outlook).toContainText("Model only: no odds are connected");
  await expect(home.getByText("1st G", { exact: true })).toBeVisible();

  // Navigate by the link's target (on a phone the fixed tab bar can cover a link near the bottom edge).
  const href = await home.getByRole("link").first().getAttribute("href");
  await page.goto(MODELS + href);
  await expect(page.getByText(/^Projection · vs T01/)).toBeVisible();
  const sog = page.getByRole("article", { name: "Shots on goal projection" });
  await expect(sog).toBeVisible();
  await expect(sog.getByRole("img", { name: "Shots on goal distribution" })).toBeVisible();
  await sog.getByText("Explain projection").click();
  await expect(sog.getByText("Ice time", { exact: true })).toBeVisible();
  await expect(sog.getByText(/The factors multiply to the projected average/)).toBeVisible();
  // The opposing goalie was changed by Quick Entry: scoring projections show before -> after.
  await expect(page.getByText(/Updated after goalie confirmed: \d+\.\d\d → \d+\.\d\d/).first()).toBeVisible();
});

test("model tests page shows what passed and what was held back", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/models`);
  await expect(page.getByRole("heading", { name: "Model tests" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Shots on goal test" })).toContainText("PASSED · PUBLISHED");
  const win = page.getByRole("article", { name: "Win probability (moneyline, goalie win) test" });
  await expect(win).toContainText("NOT PUBLISHED");
  await expect(win).toContainText("Did not clearly beat the simple baselines");
  await expect(win).toContainText("vs standings records (log5)");
  await expect(page.getByRole("article", { name: "First goal scorer test" })).toContainText("PASSED · PUBLISHED");
  const sw = page.getByRole("article", { name: "Saves + Win (goalie) test" });
  await expect(sw).toContainText("vs league rate of this outcome");
  await expect(sw).toContainText("vs saves and win treated as independent (shown, not required)");
  await expect(page.getByText(/not modeled/)).toHaveCount(0); // every market in the catalogue has a model
});

test("sportsbook lines: comparison table, best price, no-vig, movement (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/games/2025029999`);
  const sog = page.getByRole("table", { name: "Shots on Goal lines" });
  await expect(sog).toBeVisible();
  await expect(sog.locator("thead").getByText("FanDuel")).toBeVisible();
  await expect(sog.locator("thead").getByText("BetMGM")).toBeVisible();
  await expect(sog.getByTitle("Best price").first()).toBeVisible();
  await expect(sog.getByText(/^\d+%$/).first()).toBeVisible(); // no-vig consensus
  await expect(page.getByRole("table", { name: "Moneyline lines" })).toBeVisible();
  await expect(page.getByText(/prices change, so check the book before acting/)).toBeVisible();
  // Phase 5: model probability at the line and a lean (or "No lean") for every row.
  await expect(sog.locator("thead").getByText("Lean")).toBeVisible();
  await expect(sog.locator("tbody tr").first()).toContainText(/No lean|edge [+−]\d/);

  await page.goto(`${MODELS}#/players/8000001`);
  const card = page.getByRole("article", { name: "Shots on Goal line" });
  await expect(card).toBeVisible();
  await expect(card.getByRole("img", { name: "Line movement" })).toBeVisible();
  // Event markers: what happened while the line moved (Quick Entry goalie + news), with sources.
  const events = card.getByRole("list", { name: "Events during this line" });
  await expect(events).toContainText(/goalie confirmed: /);
  await expect(events).toContainText("Injury: Synthetic note: day-to-day with a minor injury");
  await expect(card).toContainText("FanDuel");
  await expect(card).toContainText(/Model over \d+% vs market \d+%/);
  await card.getByText("Show the calculation").click();
  await expect(card.getByText(/^Implied\(/).first()).toBeVisible();
  await card.getByText(/Confidence breakdown/).click();
  await expect(card.getByText("Market agreement")).toBeVisible();
});

test("real-data site without an odds key says lines aren't connected", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/games/2025021012`);
  await expect(page.getByText(/Sportsbook lines aren't connected/)).toBeVisible();
  await page.goto(`${CONFIGURED}#/admin`);
  await expect(page.getByText(/Not connected\. Add the ODDS_API_KEY repository secret/)).toBeVisible();
});

test("best props: leans only, source and time on every row, filters, sort and the prop card (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/props/best`);
  await expect(page.getByRole("heading", { name: "Best Props" })).toBeVisible();
  const list = page.getByRole("list", { name: "Props" });
  const cards = list.getByRole("listitem");
  await expect(cards).toHaveCount(2); // 2 of the 6 priced lines clear the bar (after live calibration)
  await expect(page.getByText(/2 leans of 6 priced lines/)).toBeVisible();
  for (const c of await cards.all()) await expect(c).toContainText(/via The Odds API · line seen /);
  await expect(cards.first()).toContainText("Syn P8000002"); // highest expected value first
  await expect(list).not.toContainText("No lean");

  const filters = page.getByRole("group", { name: "Filters" });
  await filters.getByLabel("Side").selectOption("over");
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText("Syn P8000002");
  await filters.getByLabel("Side").selectOption("under");
  await expect(page.getByText("Your filters hide every prop.")).toBeVisible();
  await page.getByRole("button", { name: "Reset filters" }).click();
  await filters.getByLabel("Book").selectOption("betmgm");
  await page.reload(); // filters persist on this device
  await expect(page.getByRole("group", { name: "Filters" }).getByLabel("Book")).toHaveValue("betmgm");
  await expect(cards).toHaveCount(1);
  await page.getByRole("button", { name: "Reset filters" }).click();
  await expect(cards).toHaveCount(2);

  await cards.first().getByRole("button").click();
  const dialog = page.getByRole("dialog", { name: "Prop card" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/Confidence \d+\/100/);
  await expect(dialog.getByText("Market agreement")).toBeVisible();
  await expect(dialog.getByText(/^Implied\(/).first()).toBeVisible();
  await expect(dialog.getByText(/^Calibrated from live results: P\(over\) 0\.\d{4} → 0\.\d{4}/)).toBeVisible();
  await expect(dialog).toContainText(/Prediction #\d+ is frozen/);
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toBeHidden();
});

test("props: every priced line, sortable, with changes since the last visit marked (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/props`);
  const cards = page.getByRole("list", { name: "Props" }).getByRole("listitem");
  await expect(cards).toHaveCount(6);
  await expect(cards.filter({ hasText: "No lean" })).toHaveCount(4);
  await expect(page.getByText("NEW", { exact: true })).toHaveCount(0); // first visit: nothing to compare
  await page.getByLabel("Sort by").selectOption("confidence");
  await expect(cards.first()).toContainText("Syn P8000002");
  await expect(cards.last()).toContainText("Syn P8000001");
  await page.getByLabel("Min confidence").selectOption("60");
  await expect(cards).toHaveCount(4);
  await page.getByRole("button", { name: "Reset filters" }).click();

  // Pretend the last visit saw only one line, at an older price.
  const key = await page.evaluate(() => {
    const seen = JSON.parse(localStorage.getItem("rinkx.props.seen") ?? "{}") as Record<string, number>;
    const k = Object.keys(seen)[0] ?? "";
    localStorage.setItem("rinkx.props.seen", JSON.stringify({ [k]: (seen[k] ?? 0) - 1000 }));
    return k;
  });
  expect(key).toBeTruthy();
  await page.reload();
  await expect(page.getByText("PRICE MOVED", { exact: true })).toHaveCount(1);
  await expect(page.getByText("NEW", { exact: true })).toHaveCount(5);
});

test("best props on a site without odds says so instead of showing anything", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/props/best`);
  await expect(page.getByText(/Sportsbook lines aren't connected/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Props" })).toHaveCount(0);
});

test("model performance: graded results, calibration vs market, splits and recent bets (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/performance`);
  await expect(page.getByRole("heading", { name: "Model performance" })).toBeVisible();
  await expect(page.getByText(/SYNTHETIC·? ?test data/)).toBeVisible(); // test data is always labelled
  const summary = page.getByLabel("Summary");
  await expect(summary).toContainText("Bets (W-L-P)");
  await expect(summary).toContainText(/ROI \(95% range\)/);
  await expect(summary).toContainText(/beat the close \d+% of the time/);
  await expect(page.getByRole("img", { name: "Cumulative profit" })).toBeVisible();
  await expect(page.getByText(/Worst drawdown from a high point: −\d/)).toBeVisible();
  await expect(page.getByRole("img", { name: "Reliability diagram" })).toBeVisible();
  await expect(page.getByText("No-vig market")).toBeVisible();
  await expect(page.getByRole("table", { name: "By market" })).toContainText("Shots on Goal");
  await expect(page.getByRole("table", { name: "By confidence" })).toBeVisible();
  await expect(page.getByRole("table", { name: "By model version" })).toContainText("synthetic");
  const recent = page.getByRole("list", { name: "Recent graded bets" }).getByRole("listitem");
  await expect(recent).toHaveCount(50);
  await expect(recent.first()).toContainText(/actual \d+/);
  await expect(recent.first()).toContainText(/WIN|LOSS|win|loss/);
  await expect(page.getByText(/far too few to tell skill from luck/)).toHaveCount(0); // 600+ bets here
  const cals = page.getByRole("list", { name: "Calibrators" });
  await expect(cals).toContainText("Shots on Goal");
  await expect(cals).toContainText(/applied to pricing|not applied: no improvement/);
  await expect(cals).toContainText(/held-out Brier 0\.\d{4} → 0\.\d{4}/);
});

test("model performance before anything is graded says so", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/performance`);
  await expect(page.getByText(/Nothing graded yet/)).toBeVisible();
  await expect(page.getByLabel("Summary")).toHaveCount(0);
});

test("parlay builder: legs from props, correlation-adjusted probability, variance warning (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/props`);
  const cards = page.getByRole("list", { name: "Props" }).getByRole("listitem");
  for (const name of ["Syn P8000002", "Syn P8000003"]) {
    await cards.filter({ hasText: name }).first().getByRole("button").click();
    const dialog = page.getByRole("dialog", { name: "Prop card" });
    await dialog.getByRole("button", { name: "Add to parlay" }).click();
    await expect(dialog.getByRole("button", { name: "Remove from parlay" })).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
  }
  await page.goto(`${MODELS}#/parlay`);
  await expect(page.getByRole("heading", { name: "Parlay builder" })).toBeVisible();
  await expect(page.getByText(/Parlays multiply the bookmaker's margin and the variance/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Parlay legs" }).getByRole("listitem")).toHaveCount(2);
  const result = page.getByLabel("Parlay result");
  await expect(result).toContainText("If independent");
  await expect(result).toContainText("Adjusted for correlation");
  const pairs = page.getByRole("list", { name: "Leg pairs" });
  await expect(pairs).toContainText("teammates");
  await expect(pairs).toContainText(/ρ [+−]0\.\d\d \(95% .* n=[\d,]+\)/);
  await page.getByRole("button", { name: /^Remove Syn P8000003/ }).click();
  await expect(page.getByRole("list", { name: "Parlay legs" }).getByRole("listitem")).toHaveCount(1);
  await page.reload(); // legs persist on this device
  await expect(page.getByRole("list", { name: "Parlay legs" }).getByRole("listitem")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear all" }).click();
  await expect(page.getByText(/No legs yet/)).toBeVisible();
});

test("news and alerts: Quick Entry news with its source, fired alerts with delivery status (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/news`);
  const news = page.getByRole("list", { name: "News" });
  await expect(news).toContainText("Synthetic note: day-to-day with a minor injury");
  await expect(news).toContainText("Source: example.org (beat reporter)");
  await expect(news.getByRole("link", { name: "Syn P8000001" })).toBeVisible();
  await expect(page.getByText(/Push delivery isn't set up/)).toBeVisible();
  const fired = page.getByRole("list", { name: "Fired alerts" });
  await expect(fired).toContainText("strong-leans");
  await expect(fired).toContainText(/edge \+\d+\.\d pts, confidence \d+/);
  await expect(fired).toContainText("not sent");
  await page.goto(`${MODELS}#/players/8000001`);
  await expect(page.getByRole("list", { name: "News" })).toContainText("Synthetic note");
});

test("news page without any entries says how news gets in", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/news`);
  await expect(page.getByText(/There is no automatic news feed/)).toBeVisible();
  await expect(page.getByText(/No alerts have fired in the last 14 days/)).toBeVisible();
});

test("phone: tab bar, More bottom sheet, and the prop card as a bottom sheet", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await unlock(page, true, MODELS);
  const tabs = page.getByRole("navigation", { name: "Tabs" });
  for (const t of ["Slate", "Best", "Search", "Parlay", "More"]) await expect(tabs.getByText(t, { exact: true })).toBeVisible();
  await tabs.getByRole("button", { name: "More" }).click();
  const sheet = page.getByRole("dialog", { name: "More" });
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await tabs.getByRole("button", { name: "More" }).click();
  await page.getByRole("button", { name: "Close menu" }).click({ position: { x: 10, y: 10 } });
  await expect(sheet).toBeHidden();
  await tabs.getByRole("button", { name: "More" }).click();
  await sheet.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(sheet).toBeHidden();

  await page.goto(`${MODELS}#/props`);
  await page.getByRole("list", { name: "Props" }).getByRole("listitem").first().getByRole("button").click();
  const card = page.getByRole("dialog", { name: "Prop card" });
  await expect(card).toBeVisible();
  const box = (await card.boundingBox())!;
  const vh = page.viewportSize()!.height;
  expect(Math.abs(box.y + box.height - vh)).toBeLessThanOrEqual(2); // anchored to the bottom edge
});

test("settings: decimal odds and time zone apply everywhere and persist", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/props/best`);
  const first = page.getByRole("list", { name: "Props" }).getByRole("listitem").first();
  await expect(first).toContainText("−140");
  await page.goto(`${MODELS}#/settings`);
  await page.getByLabel(/^Decimal/).check();
  await page.getByLabel("UTC").check();
  await page.goto(`${MODELS}#/props/best`);
  await expect(first).toContainText("1.71");
  await expect(first).not.toContainText("−140");
  await expect(first).toContainText("UTC");
  await page.reload();
  await expect(first).toContainText("1.71");
  await page.goto(`${MODELS}#/settings`);
  await page.getByLabel(/^American/).check();
  await page.goto(`${MODELS}#/props/best`);
  await expect(first).toContainText("−140");
});

test("cool-off hides props, prices and parlays on this device, and can't be ended early", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/settings`);
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "24 hours" }).click();
  await expect(page.getByText(/Cool-off is on until .*It can be extended but not ended early/)).toBeVisible();
  for (const route of ["#/props", "#/props/best", "#/parlay"]) {
    await page.goto(MODELS + route);
    await expect(page.getByText(/Cool-off is on until/)).toBeVisible();
    await expect(page.getByRole("list", { name: "Props" })).toHaveCount(0);
  }
  await page.goto(`${MODELS}#/games/2025029999`);
  await expect(page.getByText(/Prices are hidden during your cool-off/).first()).toBeVisible();
  await expect(page.getByRole("table", { name: "T00 projections" })).toBeVisible(); // stats and projections stay
  await page.goto(`${MODELS}#/settings`);
  await expect(page.getByRole("button", { name: "Extend: 24 hours" })).toBeVisible();
});

test("home-screen app: manifest and icons, and it opens offline after a visit", async ({ page, context }) => {
  await page.goto(MODELS);
  const manifest = await (await page.request.get(`${MODELS}manifest.webmanifest`)).json();
  expect(manifest.display).toBe("standalone");
  const pngs = manifest.icons.filter((i: { type: string }) => i.type === "image/png");
  expect(pngs.map((i: { sizes: string }) => i.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  for (const i of [...pngs, { src: "apple-touch-icon.png" }]) {
    expect((await page.request.get(MODELS + i.src)).status()).toBe(200);
  }
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload(); // now controlled by the worker, which caches what it fetches
  await expect(page.getByLabel("Passphrase")).toBeVisible();
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByLabel("Passphrase")).toBeVisible();
  await context.setOffline(false);
});

test("goalies: today's starters with confirmed vs projected status (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/goalies`);
  await expect(page.getByRole("heading", { name: "Goalies" })).toBeVisible();
  const today = page.getByRole("list", { name: /^Goalies \d{4}-\d{2}-\d{2}$/ }).first();
  await expect(today).toContainText("T01 @ T00");
  await expect(today.getByText("CONFIRMED", { exact: true })).toBeVisible(); // entered through Quick Entry
  await expect(today.getByText(/PROJECTED · \d+%/)).toBeVisible();
  await expect(page.getByText(/1 of 2 starters confirmed/)).toBeVisible();
});

test("line movement: every open line from first seen to now, biggest moves first (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/lines`);
  await expect(page.getByRole("heading", { name: "Line movement" })).toBeVisible();
  const moves = page.getByRole("list", { name: "Line moves" }).getByRole("listitem");
  await expect(moves.first()).toContainText(/over [+−]\d+ → [+−]\d+/);
  await expect(moves.first()).toContainText(/[+−]\d+\.\d pts/);
  await expect(moves.first()).toContainText("2 price changes");
  const moved = await moves.count();
  await page.getByLabel("Moved only").uncheck();
  expect(await moves.count()).toBeGreaterThanOrEqual(moved);
  await expect(page.getByText(/Source: The Odds API/)).toBeVisible();
});

test("admin: stored versions and the daily restore drill", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/admin`);
  const panel = page.locator("section", { has: page.getByRole("heading", { name: "Store & backups" }) });
  await expect(panel).toContainText(/Versions kept/);
  await expect(panel).toContainText(/policy: newest 10 plus one per week for 8 weeks/);
  await expect(panel).toContainText(/Restore drill\s*passed/);
  await expect(panel).toContainText(/Keep offline copies of STORE_KEY and DATA_KEY/);
});

test("injury report (recorded ESPN response): player status with source, game note, admin review list", async ({ page }) => {
  await unlock(page);
  await page.goto(`${CONFIGURED}#/players/8479325`);
  await expect(page.getByRole("heading", { name: "Charlie McAvoy" })).toBeVisible();
  const status = page.getByLabel("Injury status");
  await expect(status.getByText("SUSPENDED", { exact: true })).toBeVisible();
  await expect(status).toContainText(/expected back/);
  await expect(status.getByRole("link", { name: "source" })).toHaveAttribute("href", /^https:\/\/www\.espn\.com\//);
  await page.goto(`${CONFIGURED}#/players/8477942`);
  await expect(page.getByLabel("Injury status").getByText("IR", { exact: true })).toBeVisible();

  await page.goto(`${CONFIGURED}#/games/2025021012`);
  await expect(page.getByRole("heading", { name: "Box score" })).toBeVisible();
  await expect(page.getByText("Shown for upcoming games only")).toBeVisible(); // the report describes now, not the past

  await page.goto(`${CONFIGURED}#/admin`);
  const panel = page.locator("section", { has: page.getByRole("heading", { name: "Injury report (ESPN)" }) });
  await expect(panel).toContainText("109 listed, 4 matched and active, 105 unmatched");
  await expect(panel).toContainText("partial");
});

test("saves + win: the confirmed starter's joint projection by save line (synthetic)", async ({ page }) => {
  await unlock(page, true, MODELS);
  await page.goto(`${MODELS}#/players/8000040`); // T01's backup, confirmed through Quick Entry
  const card = page.getByRole("article", { name: "Saves + Win projection" });
  await expect(card).toContainText(/Probability he gets the win with 25\+ saves · win \d+%/);
  const lines = card.getByLabel("Win with saves");
  await expect(lines).toContainText("20+ saves");
  await expect(lines).toContainText("30+ saves");
});
