# RinkX — NHL Props Intelligence

A **personal** research terminal for NHL player-prop markets. It shows transparent statistical projections, full probability distributions, model-vs-market edges with the vig removed, matchup and role context, and honest backtesting.

RinkX answers **"why does the model project this player at this number?"** It does not tell anyone what to bet. It gives statistical estimates, never guarantees.

## Status

**Phase 3 complete, plus model 1.1:** projection models for shots, goals, assists, points, PP points, blocks, hits and goalie saves/goals against. Model 1.1 adds a game model: win probability, overtime chance, team and game goal totals, goalie win and shutout, and first goal scorer. Each comes with a full probability distribution and an *Explain* breakdown. A stat is published only after it beats a season-average and a last-10 baseline on past games it never saw (see **Model Tests** in the app). Starting goalies are projected from recent starts. **Quick Entry** issue forms confirm a goalie or rule a player out, and the projections update with a before → after. Built on Phase 1–2: hourly NHL schedule, box scores, play-by-play, ice-time splits, hit rates and game logs. **Phase 4: sportsbook lines.** FanDuel and BetMGM (New Jersey) lines from The Odds API, with best price, a no-vig consensus and line movement. Spending stays inside a monthly credit budget, and lines whose player can't be matched to an NHL id are never shown. It turns on when you add the `ODDS_API_KEY` secret (see [setup](docs/setup.md#sportsbook-lines-phase-4)). Injuries are still marked *not connected yet*. Next: Phase 5 (model vs market: edge, EV and confidence).

**Speed up the backfill:** Actions → pipeline → Run workflow → set *Games to load this run* to 400. The models need loaded history before they can pass their test, so nothing is projected until then.

**Quick Entry:** on a Game page, tap *Confirm goalie* or *Mark a player out*. It opens a prefilled GitHub issue; add a source link and submit. The pipeline applies it within a few minutes and closes the issue.

**First time?** Follow [docs/setup.md](docs/setup.md).

## Design package

| Doc | Contents |
|---|---|
| [01 · Architecture](docs/01-architecture.md) | Product rules, GitHub Actions + Pages topology, workflows and cadence, encryption and security |
| [02 · Database](docs/02-database.md) | Entity map, table groups, design decisions. DDL in [`db/schema.sql`](db/schema.sql) |
| [03 · Data contract](docs/03-api.md) | Static encrypted data contract, prop-card payload, parlay math, Quick Entry write path |
| [04 · Data sources](docs/04-data-sources.md) | Free / paid / optional sources, licensing posture, ingestion contract |
| [05 · Models](docs/05-models.md) | TOI, shots, scoring, goalie, game simulation, devig & edge, confidence, correlation, walk-forward validation |
| [06 · UI](docs/06-ui.md) | Design language, navigation, routes, wireframes, iPhone requirements |
| [07 · Folder structure](docs/07-folder-structure.md) | Repo layout |
| [08 · Roadmap](docs/08-roadmap.md) | Phases 0–10 with test gates; open decisions |
| [Setup](docs/setup.md) | One-time setup from your phone |

## How it runs

Everything runs on GitHub, at $0 hosting cost. **GitHub Actions** fetches data, runs the models and builds the site on a schedule. **GitHub Pages** serves it. The data store is an encrypted SQLite file kept as a Release asset, and every data file on the site is encrypted. Only your passphrase unlocks it. See [docs/01-architecture.md](docs/01-architecture.md).

## Local development

```bash
# pipeline (Python 3.11+)
cd pipeline && python -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ..
pipeline/.venv/bin/rinkx dev-setup                      # local keys in .rinkx/ (gitignored)
pipeline/.venv/bin/rinkx run --out web/public/data      # build a local encrypted bundle

# web
cd web && npm install && npm run dev                    # unlock with: rinkx-local-dev-passphrase
```

## Tests

```bash
cd pipeline && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy   # pipeline
python db/tests/test_schema.py                                                      # schema constraints
python scripts/lint_copy.py                                                         # banned betting language
cd web && npm run typecheck && npm test                                             # web unit + crypto interop
cd web && RINKX_PYTHON=../pipeline/.venv/bin/python npm run e2e                     # browser end-to-end
```

## Responsible use

RinkX presents probabilities, not certainties. Sports betting carries financial risk and is legal only in some jurisdictions and only for those of legal age. If gambling stops being fun, help is available: in the US, call 1-800-GAMBLER.
