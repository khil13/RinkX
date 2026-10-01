# 08 · Development Roadmap

Each phase ends with a **test gate**. The next phase starts only when the gate passes, and the results are recorded in the PR for that phase.

| Phase | Scope | Deliverables | Test gate |
|---|---|---|---|
| **0 · Foundations** | Repo scaffolding | `backend/` (FastAPI skeleton, Alembic baseline from `db/schema.sql`), `frontend/` (Next.js shell, dark theme, nav, data-state chips), `infra/docker-compose.yml`, CI (lint, typecheck, tests, schema smoke, copy lint) | `docker compose up` serves an empty app; CI green |
| **1 · League data** | Teams, players, schedule, game pages | NHL Web API adapter (schedule, teams, rosters, standings, boxscores), `game_team_context` (rest/B2B/travel), `GET /games/today`, `GET /games/{id}`, `GET /players/{id}`, Daily Slate and Game pages with "Live data unavailable" states | Contract tests on recorded payloads; a full-week ingest matches NHL.com spot checks for 20 random games; slate renders on an iPhone viewport (Playwright); killing the adapter shows "Live data unavailable" |
| **2 · Stats & history** | Player stats, prop markets catalogue, hit rates | Boxscore and PBP ingestion, shift-chart deployment reconstruction, MoneyPuck backfill (≥ 5 seasons), hit-rate engine, player profile, game logs | Season totals reconcile with official totals within tolerance (TOI ±1 s/game, counting stats exact); hit-rate unit tests incl. DNP handling |
| **3 · Projection models** | TOI, shots, scoring, physical, goalie, game sim | Feature store (point-in-time), baseline GLM/NB models, PMFs, Explain factors, recalculation pipeline on lineup/goalie changes | Leakage tests pass; walk-forward log score beats the season-average Poisson and L10 Poisson baselines on every modeled market; PIT histograms roughly uniform |
| **4 · Line comparison** | Odds ingestion | Odds vendor adapter, entity resolution + review queue, `prop_lines`, `line_movements`, comparison table, best price, consensus, movement chart | 100% of displayed lines trace to a vendor payload; unresolved players are never displayed; quota usage within budget for a simulated week |
| **5 · Model vs market** | Pricing | Devig (multiplicative/power/Shin), edge, EV, push handling, confidence score with breakdown, calculation trail, `predictions` freezing | Odds-math property tests (no-vig sums to 1, symmetric cases); hand-checked examples; confidence components unit-tested |
| **6 · Best Props** | Dashboard | `/props/best` with filters and sorts, empty states, prop-card drawer, SSE live updates | Sorting/filtering e2e tests; copy lint passes; no prop shown without source + timestamp |
| **7 · Backtesting** | Performance & calibration | Walk-forward runner, grader with book settlement rules, CLV, calibration, Model Performance page, champion/challenger registry | Calibration ECE within target on held-out seasons; confidence buckets are monotonic (or weights are revised); losing months are visible in the UI |
| **8 · Alerts & news** | Alerts, news, correlation, parlay | Alert CRUD and evaluator, in-app + email + web push, news ingestion (licensed feed + admin entry), correlation estimation, Parlay Builder | Alert fires exactly once per condition in an integration test; correlations carry `n_obs`/CI; parlays with insufficient data fall back to independence and say so |
| **9 · Mobile polish** | iPhone-first refinement | Card layouts, bottom sheets, PWA, performance budget | Lighthouse mobile ≥ 90; Playwright suite on the iPhone 15 profile; manual check on a device |
| **10 · Production** | Launch | Vercel + Render/Fly, managed Postgres/Redis, Sentry, backups/PITR, admin dashboard, legal review of data licenses, responsible-gaming copy review | Load test (p95 < 300 ms on cached reads); restore-from-backup drill; staging soak of one full slate with `synthetic` forbidden |

## Decisions needed from the product owner

These affect cost and legality, so they need answers before or during the phases noted:

1. **Odds vendor and budget** (needed by Phase 4). The default recommendation is The Odds API, with a paid tier sized by the quota math in `04-data-sources.md`.
2. **Lineup / goalie / injury feed** (needed by Phase 3 for live accuracy). Options are a licensed feed (SportsDataIO / Rotowire / Daily Faceoff partnership) or admin entry from official sources at the start.
3. **Commercial intent.** Free personal use and a paid product have different licensing requirements for NHL data and odds data.
4. **Target jurisdictions** (needed by Phase 4). This decides which sportsbooks to display and the legal-age and responsible-gaming copy.
5. **Auth providers** (needed by Phase 0). The default is email magic link + Google + Apple.
