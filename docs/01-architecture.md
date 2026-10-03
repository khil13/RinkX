# 01 · System Architecture

RinkX is a research tool for NHL player-prop markets. It answers **"why does the model project this player at this number?"** It does not answer "what should I bet?" Every number on screen has to trace back to a source, a timestamp and a model version.

## 0. Scope and hosting decisions

| Decision | Choice |
|---|---|
| Audience | **Personal use, single owner.** No sign-ups, no user management, nothing commercial. |
| Hosting | **GitHub only.** GitHub Actions does all computing on a schedule. GitHub Pages serves a static site. No servers, no database server. |
| Repo visibility | **Public** (`khil13/RinkX`). Pages is free for public repos and Actions minutes are unlimited on standard runners. |
| Data privacy | Because the site and repo are public, **all data is encrypted**, both at rest and on the published site. Only someone with your passphrase can read it (§5). |
| Cost | $0 for hosting. The only paid item is the odds API subscription. |

GitHub Pages terms allow personal, non-commercial sites. They prohibit using Pages to run a business or SaaS, and prohibit "get-rich-quick schemes". A personal research tool that makes no guarantees fits within that.

## 1. Non-negotiable product rules

These are enforced in code (schema constraints, build checks, CI tests), not only in copy.

| Rule | Enforcement |
|---|---|
| No fabricated data (lines, injuries, stats, results) | Every external row requires `source_id` + `fetched_at`. Injuries and news also require a non-empty source URL (a `CHECK` constraint). |
| Mock data is always labeled | Mock rows must have `provenance = 'synthetic'`. **The publish step aborts the production deploy if any synthetic row exists.** Local dev builds show a fixed SYNTHETIC banner. |
| Missing ≠ zero | Missing inputs go into `player_projections.missing_inputs`. Projections below a data-quality floor are not published and show **"Insufficient data"**. |
| Unavailable markets say so | Absent markets publish as `null` with `reason: "data_unavailable"` and render **"Data unavailable"**. |
| Stale data is visible | Each feed's last successful fetch is in the public `manifest.json`. The UI shows **"Live data unavailable"** or a stale badge when a feed is older than its threshold, or when the whole site hasn't been rebuilt recently. |
| No "lock / guaranteed / free money / can't lose" | A copy lint in CI fails the build on banned terms. |
| Losing periods are never hidden | Published predictions are immutable. SQLite triggers block UPDATE and DELETE on them. |
| No leakage in evaluation | Projections store `as_of`. Features are point-in-time, and backtests are walk-forward only. |

## 2. Topology

```mermaid
flowchart LR
  subgraph Sources["External sources"]
    NHL[NHL Web/Stats API]
    MP[MoneyPuck CSVs]
    ODDS[Odds API]
  end

  subgraph GH["GitHub (public repo khil13/RinkX)"]
    direction TB
    subgraph Actions["GitHub Actions (scheduled + on-demand)"]
      PIPE["rinkx pipeline (Python)<br/>ingest → diff → features → models<br/>→ price → alerts → publish"]
    end
    STORE[("Release 'store'<br/>rinkx-*.db.enc<br/>(encrypted SQLite)")]
    ISSUES["Issue forms<br/>Quick Entry"]
    PAGES["GitHub Pages<br/>static app + encrypted JSON"]
    CFG["config/*.yml<br/>alerts · books · budget"]
  end

  PHONE["You: iPhone / desktop browser<br/>passphrase → decrypt locally"]
  PUSH["ntfy push"]

  Sources --> PIPE
  STORE <-->|download / upload new version| PIPE
  ISSUES -->|issues: opened| PIPE
  CFG --> PIPE
  PIPE -->|deploy-pages| PAGES
  PIPE --> PUSH --> PHONE
  PAGES --> PHONE
  PHONE -->|GitHub app| ISSUES
```

### Components

| Component | Tech | Responsibility |
|---|---|---|
| **Pipeline** | Python 3.12 package `rinkx` with a CLI (`python -m rinkx run --stage ...`) | Ingestion, change detection, features, models, pricing, alerts, grading, publishing. Runs only inside GitHub Actions or locally. |
| **Data store** | One **SQLite** file ([`db/schema.sql`](../db/schema.sql)), encrypted with AES-256-GCM (`STORE_KEY`), kept as a GitHub **Release asset** | System of record. Each run downloads the newest version and uploads a new one. The last 10 versions are kept as backups. Release assets live outside git history, so the repo doesn't bloat. |
| **Analytics** | pandas / polars, numpy, scipy, statsmodels, LightGBM; DuckDB for heavy backtest queries (it reads SQLite directly) | Modeling and backtesting on the Actions runner (4 vCPU / 16 GB for public repos). |
| **Site** | Vite + React + TypeScript + Tailwind + Recharts + TanStack Query, `HashRouter` | Static SPA on Pages. Fetches encrypted JSON files and decrypts them in the browser with WebCrypto. No server code. |
| **Quick Entry** | GitHub Issue forms + a workflow on `issues: opened` | The "write" path. You confirm a goalie, scratch, line change or injury from the GitHub iPhone app, with a mandatory source URL. |
| **Alerts** | `config/alerts.yml` → evaluated each run → **ntfy** push | Notifications to your phone. |

## 3. Workflows and update cadence

| Workflow | Trigger | Does |
|---|---|---|
| `pipeline.yml` | Several cron schedules + `workflow_dispatch` + pushes to `main` | **One workflow for all scheduled work**, which gives a single "Run workflow" button and a single place where runs queue. The run decides its stages from the time and the slate: |
| ↳ game-day window | every 10 min, 15:00–03:59 UTC (11 am–midnight ET) | A `gate` job (`scripts/game_day_gate.py`, standard library only, a few seconds) reads the NHL schedule and lets the run through only if a game starts within 4.5 h; otherwise the build is skipped. When it runs it is a full run: goalies, injuries, odds (budget-aware, including the closing fetch), projections, pricing, alerts (including the pre-game check), publish. If the schedule can't be read, it runs. |
| ↳ hourly | minute 17 | Schedule, rosters, news, odds on non-game days at a low cadence. *(Phase 0 runs only this, and only publishes.)* |
| ↳ nightly | 09:37 UTC (5:37 am ET) | Final boxscores + PBP, shift-chart lineups, grading and CLV, rolling features, correlations. |
| ↳ weekly | Monday 10:13 UTC | *Not built (Phase 7 decision):* the hourly run already re-tests the models every 20 h and grades every run. A new model version runs as the challenger and is promoted only after it beats the champion on graded props. |
| `quick-entry.yml` | `issues: opened` with label `quick-entry` | Validates that the author is the repo owner, writes the row (`provenance='manual'`, `source_ref` = your URL), recomputes affected projections, publishes, then closes the issue with a summary comment. |
| `watchdog.yml` | hourly, minute 47 | *(Phase 10)* Alerts (one issue + one ntfy push) when no pipeline run has succeeded for 2 h on a game day or 26 h otherwise; closes the issue on recovery. |
| `store.yml` | manual | *(Phase 10)* `list`, `drill` (read-only restore check) or `restore` a stored version (uploads it as the newest; deletes nothing). |
| `keepalive.yml` | weekly | Re-enables the scheduled workflows through the API. GitHub silently disables schedules in public repos after 60 days without repository activity, and pipeline runs don't count as activity. |
| `ci.yml` | push / PR | Lint, typecheck, unit tests, schema tests, copy lint, contract tests. |

**Serialization.** The `build` job of `pipeline.yml` and `store.yml` share `concurrency: { group: rinkx-store, cancel-in-progress: false }`, so two runs never write the store at once. The group is on the build job, not the whole workflow, so a game-day run that the gate skips never displaces a queued run that has work to do. The watchdog counts a run as successful only if its build job ran and succeeded. GitHub keeps only the newest pending run in a group. That is fine here, because the next run does a full refresh anyway.

**Timing honesty.** Scheduled runs are commonly delayed by 5–30 minutes under GitHub load, and occasionally skipped. RinkX is therefore a **near-live** tool, not real-time:
* The UI always shows "Updated N min ago" from the manifest.
* Close to puck drop, you can force a refresh with **Run workflow** in the GitHub app (`workflow_dispatch`).
* Quick Entry issues trigger immediately (event-driven, not cron).

### Change-driven recalculation

Inside each run:

```
ingest → diff against store → change events → resolve affected projections → recompute → reprice → alerts → publish
```

| Change | Affected scope |
|---|---|
| Goalie confirmed / changed | That team's goalie props, all opponent skater props, and the game sim |
| Line / PP unit change | The player, old and new linemates, and team PP props |
| Player ruled out | Remove the player, redistribute TOI, recompute teammates |
| Prop line moved | Reprice only |
| Game total / ML moved | Game environment → all props in the game |
| Game started / postponed | Lock or void pregame predictions |

Each recompute inserts a new `player_projections` row that `supersedes` the old one, so the UI shows *"Projection updated: 3.6 → 4.1 (moved to PP1)"*.

### Odds credit budget

`config/budget.yml` sets a monthly credit budget for the odds API. Each run spends its share in this order: game lines; a closing fetch about 90 minutes before puck drop for games where the model has leans (markets with leans first, so closing-line value is measured on the bets that matter); a cheap first look (shots on goal, points, anytime goal; the odds API charges only for markets a book actually offers) at every game not fetched yet; then refreshes and deeper looks, the games with the biggest model edges first. Credits for the closing fetches still to come that day are held back, and spending is paced through the day (`pace` in `config/budget.yml`: at most 30% of the day before noon ET, 60% before 4 pm), so the overnight run that sees lines first can't spend the whole day. It covers only the markets and books listed in `config/books.yml`. Each run logs `quota_remaining`, and if the budget runs low, the refresh cadence stretches out. The app still works without odds: projections and hit rates show, and market fields read "Data unavailable".

## 4. Published site and data bundle

Each publish builds the SPA and a **data bundle** of JSON files, then deploys both with `actions/deploy-pages`. Nothing is committed to git.

```
/                       app shell (public, no data)
/data/manifest.json     public: build time, schema version, per-feed last-success times, file hashes
/data/keyfile.json      public: PBKDF2 salt + iterations + the data key wrapped by your passphrase
/data/*.json.enc        everything else, AES-256-GCM encrypted
```

The data contract is in [`03-api.md`](03-api.md). The site polls `manifest.json` every 2 minutes while open and refetches only the files whose hashes changed.

## 5. Security model

The repo, the Release asset and the Pages site are all **publicly downloadable**, so confidentiality comes entirely from encryption.

| Asset | Protection |
|---|---|
| Data store (`rinkx-*.db.enc`) | AES-256-GCM with a random 256-bit key in the Actions secret `STORE_KEY`. **Keep an offline copy of that key; losing it loses the store.** A store that fails to decrypt stops the run. The pipeline never silently starts a fresh one. |
| Published data files | AES-256-GCM with a random 256-bit data key (Actions secret `DATA_KEY`). `keyfile.json` holds that key wrapped with a key derived from your passphrase (PBKDF2-SHA256, 600k iterations). Your passphrase never leaves your device and is never stored in the repo. |
| Unlock on your devices | Keys are generated in the browser on the Setup page ([`setup.md`](setup.md)), so no command-line tools are needed. You enter the passphrase once per device. The browser stores the derived key in IndexedDB as a **non-extractable** CryptoKey. "Lock" clears it. |
| Passphrase strength | All of the protection rests on it. Use a long passphrase (5+ random words). The keyfile is public, so a weak passphrase can be brute-forced offline. |
| API keys | Actions secrets only (`ODDS_API_KEY`, `NTFY_TOPIC`, ...). The ntfy topic is a long random name, which acts as its password. Never in the bundle or the repo. |
| Quick Entry | The workflow acts only on issues opened by the repo owner, and anyone else's issues are ignored. Issue contents are public, but that information (a goalie confirmation plus a public source URL) is already public. |
| Odds vendor terms | Raw odds are never published in readable form. They exist only inside the encrypted store and bundle, for your personal use. |

What this does **not** hide: the app's code (the repo is public anyway), the fact that the site exists, and the timing and size of updates from `manifest.json`.

## 6. Responsible-use layer

* A persistent footer says: *"Statistical estimates, not guarantees."* It links to responsible-gambling resources (e.g. 1-800-GAMBLER in the US, ConnexOntario in Ontario), and also carries the data attribution (NHL, MoneyPuck).
* An optional cool-off setting (stored on the device) hides pricing and edge views for a chosen period.
* Parlay Builder always shows a variance warning.
* No "bet now" links. The sportsbook columns are informational.

## 7. Environments and local development

| Environment | Where | Data |
|---|---|---|
| `dev` | Your machine: `python -m rinkx run --local` + `npm run dev` | Local SQLite. Synthetic fixtures allowed and bannered. |
| `prod` | GitHub Actions + Pages | Encrypted store. Synthetic rows block the deploy. |

Everything also runs locally with no GitHub dependency, which makes debugging easy and keeps an exit path open if you ever want a server.
