# 08 · Development Roadmap

Each phase ends with a **test gate**. The next phase starts only when the gate passes, and the results are recorded in the PR for that phase.

| Phase | Scope | Deliverables | Test gate |
|---|---|---|---|
| **0 · Foundations** ✅ | Repo scaffolding + hosting pipeline | `pipeline/` (package, CLI, store download/encrypt/upload, migrations), `web/` (Vite shell, dark theme, nav, data-state chips, unlock screen, manifest polling), publish step (bundle + AES-GCM + keyfile), `ci.yml`, `pipeline.yml` (hourly, publish-only), `deploy-pages`, `keepalive.yml`, in-browser Setup page | Crypto round-trip tests (Python encrypt → browser decrypt in Playwright); a deploy of an empty, encrypted bundle to Pages unlocks with the passphrase and rejects a wrong one; store upload → download round-trip; publish aborts on a synthetic row in prod mode; CI green |
| **1 · League data** ✅ | Teams, players, schedule, game pages | NHL Web API adapter (schedule, teams, rosters, standings, boxscores), `game_team_context` (rest/B2B/travel), `slate/`, `games/`, `players/` files, Daily Slate and Game pages with "Live data unavailable" states | Contract tests on recorded payloads; a full-week ingest matches NHL.com spot checks for 20 random games; slate renders on an iPhone viewport (Playwright); a disabled adapter shows "Live data unavailable" |
| **2 · Stats & history** ✅ | Player stats, hit rates | Box-score, play-by-play and stats-API ingestion (official EV/PP/SH TOI, PP assists, shot attempts, faceoffs, primary/secondary assists), previous-season backfill, line-independent hit-rate engine, player profile, game logs with DNPs. *Deferred:* MoneyPuck history and shift-chart line reconstruction move to Phase 3, where the models first need them. | Season totals reconcile with official totals within tolerance (TOI ±1 s/game, counting stats exact); hit-rate unit tests incl. DNP handling; store stays under the size budget |
| **3 · Projection models** | TOI, shots, scoring, physical, goalie, game sim + Quick Entry | Point-in-time features, baseline GLM/NB models, PMFs, Explain factors, recalculation on lineup/goalie changes, Quick Entry issue forms + `quick-entry.yml` | Leakage tests pass; walk-forward log score beats the season-average Poisson and L10 Poisson baselines on every modeled market; PIT roughly uniform; a Quick Entry goalie change produces a before/after projection within one run |
| **4 · Line comparison** | Odds ingestion | Odds adapter with credit budgeter, entity resolution + review list, `prop_lines`, `line_movements`, comparison table, best price, consensus, movement chart | Every displayed line traces to a vendor payload; unresolved players are never displayed; simulated month stays within `budget.yml` |
| **5 · Model vs market** | Pricing | Devig (multiplicative/power/Shin), edge, EV, push handling, confidence with breakdown, calculation trail, `predictions` freezing | Odds-math property tests (no-vig sums to 1, symmetric cases); hand-checked examples; confidence components unit-tested; immutability triggers tested |
| **6 · Best Props** | Dashboard | Best Props page with in-browser filters and sorts, empty states, prop-card drawer, change-aware refresh | Sorting/filtering e2e tests; copy lint passes; no prop shown without source + timestamp |
| **7 · Backtesting** | Performance & calibration | Walk-forward runner (`weekly.yml`), grader with book settlement rules, CLV, calibration, Model Performance page, champion/challenger via issue form | Calibration ECE within target on held-out seasons; confidence buckets monotonic (or weights revised); losing months visible |
| **8 · Alerts, news, parlay** | Alerts, news, correlation, parlay | `config/alerts.yml` sync + evaluator + ntfy push, news via Quick Entry, correlation estimation, browser Parlay Builder with Python-generated test vectors | Each alert fires at most once per game (enforced by an index and tested); correlations carry `n_obs`/CI; parlays with insufficient data fall back to independence and say so; TS parlay output matches Python vectors |
| **9 · Mobile polish** | iPhone-first refinement | Card layouts, bottom sheets, PWA, performance budget | Lighthouse mobile ≥ 90; Playwright suite on the iPhone 15 profile; check on your phone |
| **10 · Hardening** | Run it for real | Store backup rotation (last 10 versions + weekly snapshot), restore drill, offline copies of `STORE_KEY` / `DATA_KEY`, keepalive verified, admin health page, staleness alerts when no successful run in 2 h on a game day | Restore from an older store version succeeds; one full live slate in prod with zero synthetic rows; a wrong passphrase fails cleanly; a simulated 60-day idle period doesn't disable schedules |

## Decisions needed from the product owner

Decided:
* **Personal use**, single owner, no redistribution.
* **Hosting on GitHub only**: Actions + Pages, with an encrypted SQLite store and an encrypted site (see `01-architecture.md` §0 and §5).

Still open:

1. **Odds vendor and budget** (needed by Phase 4). The default recommendation is The Odds API, with a paid tier sized by the quota math in `04-data-sources.md`.
2. **Lineup / goalie / injury feed** (needed by Phase 3). The default is the free Quick Entry screen plus automatic post-game reconstruction from shift charts. A paid personal-tier feed is optional.
3. **Your jurisdiction and sportsbooks** (needed by Phase 4). Lines are shown only for the books you can actually use, which also saves odds-API credits.
4. **Setup you'll do once:** see [`setup.md`](setup.md). Enable Pages, run the pipeline, generate keys on the Setup page, add `DATA_KEY` and `STORE_KEY` as secrets, and commit the keyfile. Later: `ODDS_API_KEY`, `NTFY_TOPIC`.
