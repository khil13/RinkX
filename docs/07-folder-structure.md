# 07 · Repository Layout

A monorepo with a Python pipeline (runs in GitHub Actions) and a static web app (served by GitHub Pages).

```
RinkX/
├── README.md
├── docs/                              # design package
├── db/
│   ├── schema.sql                     # SQLite schema (system of record)
│   ├── migrations/                    # numbered .sql files, applied in order; PRAGMA user_version tracks them
│   └── tests/test_schema.py
├── config/
│   ├── alerts.yml                     # your alert definitions
│   ├── books.yml                      # sportsbooks you use (others are ignored to save credits)
│   └── budget.yml                     # monthly odds-API credit budget, cadence windows
├── pipeline/
│   ├── pyproject.toml                 # uv-managed; ruff, mypy, pytest
│   ├── rinkx/
│   │   ├── __main__.py                # CLI: run --stage ingest|model|price|alerts|grade|publish|all
│   │   ├── config.py
│   │   ├── store/                     # download/decrypt/upload of the Release asset; SQLite access; migrations
│   │   ├── ingestion/
│   │   │   ├── adapters/              # nhl_web.py, nhl_stats.py, moneypuck.py, odds_api.py
│   │   │   ├── quick_entry.py         # parse + validate issue-form submissions
│   │   │   ├── resolve.py             # vendor name -> player id
│   │   │   ├── diff.py                # change detection -> change events
│   │   │   └── quality.py             # validation -> data_quality_issues
│   │   ├── features/                  # point-in-time feature builders
│   │   ├── models/                    # toi, shots, scoring, physical, goalie, game_sim, calibrate
│   │   ├── pricing/                   # odds math, devig, edge, confidence
│   │   ├── correlation/
│   │   ├── backtest/
│   │   ├── alerts/                    # evaluator + ntfy sender
│   │   ├── publish/
│   │   │   ├── schemas.py             # Pydantic models for every published file (the data contract)
│   │   │   ├── build.py               # store -> JSON bundle
│   │   │   ├── crypto.py              # AES-256-GCM file encryption, keyfile generation
│   │   │   └── guards.py              # refuse to publish synthetic rows in prod
│   │   └── fixtures/synthetic/        # clearly labeled synthetic generators (dev/tests only)
│   └── tests/
│       ├── unit/                      # odds math, distributions, devig, confidence, crypto round-trip
│       ├── contract/                  # recorded vendor payloads -> parser expectations
│       └── leakage/                   # point-in-time guarantees
├── web/
│   ├── package.json                   # Vite, React 19, TS strict, Tailwind, Recharts, TanStack Query, React Router
│   ├── src/
│   │   ├── routes/                    # pages per 06-ui.md (HashRouter)
│   │   ├── components/
│   │   │   ├── ui/                    # Panel, DataChip, StatNumber, Table, Sheet
│   │   │   ├── props/                 # PropCard, ExplainPanel, DistributionBars, HitRateStrip
│   │   │   ├── games/                 # GameCard, LineupGrid, GoalieStatus
│   │   │   └── charts/                # LineMoveChart, ReliabilityDiagram, PnLChart, ShotMap
│   │   ├── lib/
│   │   │   ├── data/                  # manifest polling, fetch + decrypt, generated types
│   │   │   ├── crypto.ts              # WebCrypto PBKDF2 / AES-KW / AES-GCM
│   │   │   ├── parlay.ts              # copula combiner (tested against Python vectors)
│   │   │   └── format.ts
│   │   └── styles/
│   ├── public/                        # manifest.webmanifest, icons (PWA)
│   └── tests/                         # Vitest + Playwright (iPhone 15 viewport)
├── fixtures/
│   └── parlay_vectors.json            # shared Python <-> TS test vectors
├── scripts/
│   ├── make_keyfile.py                # one-time: wrap DATA_KEY with your passphrase (run locally)
│   ├── lint_copy.py                   # banned-language check
│   └── backfill/                      # historical season loaders
└── .github/
    ├── ISSUE_TEMPLATE/                # quick-entry forms: goalie, lineup, injury, news, promote-model
    └── workflows/                     # ci, pregame, hourly, nightly, weekly, quick-entry, keepalive
```

**Why the browser does no modeling:** all statistics and odds math live in Python, in one implementation. The site only formats published numbers. The one exception is parlay combination, which is checked against shared Python-generated test vectors.
