# 07 · Repository Layout

A monorepo with two deployables (the `frontend` web app and the `backend` API + workers) and a shared database schema.

```
RinkX/
├── README.md
├── docs/                          # this design package
├── db/
│   ├── schema.sql                 # reference DDL (becomes Alembic baseline in Phase 1)
│   └── tests/schema_smoke.sql
├── backend/
│   ├── pyproject.toml             # uv-managed; ruff, mypy, pytest config
│   ├── alembic.ini
│   ├── migrations/                # Alembic versions
│   ├── rinkx/
│   │   ├── config.py              # pydantic-settings; ALLOW_SYNTHETIC per environment
│   │   ├── db/                    # SQLAlchemy models, session, repositories
│   │   ├── ingestion/
│   │   │   ├── adapters/          # nhl_web.py, nhl_stats.py, moneypuck.py, odds_api.py, lineups_*.py, news_*.py
│   │   │   ├── resolve.py         # player/team entity resolution
│   │   │   ├── diff.py            # change detection -> events
│   │   │   └── quality.py         # validation rules -> data_quality_issues
│   │   ├── features/              # point-in-time feature builders
│   │   ├── models/                # toi, shots, scoring, physical, goalie, game_sim, calibrate
│   │   ├── pricing/               # odds math, devig, edge, confidence
│   │   ├── correlation/
│   │   ├── backtest/
│   │   ├── registry/
│   │   ├── alerts/
│   │   ├── events/                # Redis Streams producer/consumer, event schemas
│   │   ├── api/
│   │   │   ├── main.py            # FastAPI app factory
│   │   │   ├── deps.py            # auth (JWT/JWKS), db session, rate limit
│   │   │   ├── schemas/           # Pydantic response models (envelope + meta)
│   │   │   └── routers/           # games, players, props, goalies, lines, news, model, parlay, alerts, stream, admin
│   │   ├── workers/
│   │   │   ├── celery_app.py      # queues: critical (goalies/lineups), odds, stats, models, maintenance
│   │   │   ├── schedules.py       # adaptive beat schedule
│   │   │   └── tasks/
│   │   └── fixtures/synthetic/    # clearly-labeled synthetic generators (dev/test only)
│   └── tests/
│       ├── unit/                  # odds math, distributions, devig, confidence
│       ├── contract/              # recorded vendor payloads -> parser expectations
│       ├── leakage/               # point-in-time guarantees
│       └── api/                   # endpoint tests against Postgres (testcontainers)
├── frontend/
│   ├── package.json               # Next.js 15, TS strict, Tailwind, Recharts, TanStack Query, Auth.js
│   ├── src/
│   │   ├── app/                   # routes per 06-ui.md
│   │   ├── components/
│   │   │   ├── ui/                # primitives: Panel, DataChip, StatNumber, Table, Sheet
│   │   │   ├── props/             # PropCard, ExplainPanel, DistributionBars, HitRateStrip
│   │   │   ├── games/             # GameCard, LineupGrid, GoalieStatus
│   │   │   └── charts/            # LineMoveChart, ReliabilityDiagram, PnLChart, ShotMap
│   │   ├── lib/
│   │   │   ├── api/               # generated OpenAPI client + query hooks
│   │   │   ├── sse.ts             # stream subscription -> query invalidation
│   │   │   └── format.ts          # odds/probability/time formatting
│   │   └── styles/
│   └── tests/                     # Vitest + Playwright (iPhone 15 viewport project)
├── infra/
│   ├── docker-compose.yml         # postgres, redis, api, worker, beat, web (local dev)
│   ├── docker/                    # Dockerfiles
│   └── deploy/                    # Render/Fly config; later Terraform
├── scripts/
│   ├── lint_copy.py               # banned-language check
│   └── backfill/                  # historical season loaders
└── .github/workflows/             # ci.yml, nightly-backtest.yml
```

**Why Python owns all data and modeling:** the statistical stack (scipy, statsmodels, LightGBM, PyMC) lives there, and it means a single implementation of the odds math. The frontend never computes probabilities. It only formats what the API returns, so the web app and backtests can't disagree.
