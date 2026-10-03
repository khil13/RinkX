# RinkX — NHL Props Intelligence

A **personal** research terminal for NHL player-prop markets. It shows transparent statistical projections, full probability distributions, model-vs-market edges with the vig removed, matchup and role context, and honest backtesting.

RinkX answers **"why does the model project this player at this number?"** It does not tell anyone what to bet. It gives statistical estimates, never guarantees.

## Status

**Phase 3 complete, plus model 1.1:** projection models for shots, goals, assists, points, PP points, blocks, hits and goalie saves/goals against. Model 1.1 adds a game model: win probability, overtime chance, team and game goal totals, goalie win and shutout, and first goal scorer. Each comes with a full probability distribution and an *Explain* breakdown. A stat is published only after it beats a season-average and a last-10 baseline on past games it never saw (see **Model Tests** in the app). Starting goalies are projected from recent starts. **Quick Entry** issue forms confirm a goalie or rule a player out, and the projections update with a before → after. Built on Phase 1–2: hourly NHL schedule, box scores, play-by-play, ice-time splits, hit rates and game logs. **Phase 4: sportsbook lines.** FanDuel and BetMGM (New Jersey) lines from The Odds API, with best price, a no-vig consensus and line movement. Spending stays inside a monthly credit budget, and lines whose player can't be matched to an NHL id are never shown. It turns on when you add the `ODDS_API_KEY` secret (see [setup](docs/setup.md#sportsbook-lines-phase-4)). **Phase 5: model vs market.** Every line with a current projection is priced:

- the model's probability at that exact line (pushes handled);
- the no-vig market probability;
- edge and expected value at the offered price;
- a lean only when the edge is at least 3 points, EV is positive and the data is good enough;
- a 0–100 confidence score with a five-part breakdown and the full calculation.

Each priced line is frozen for later grading. **Phase 6: Card of the Day and Props.** **Card of the Day** picks the best team props and player props for one date. Picks come only from that date's games, with one per player and per game, at the best price, in team colours. **Props** lists every priced line. Props can be filtered by date, game, market, book, side, minimum edge and confidence, and sorted by EV, edge, confidence or start time. Each card shows its source and when the line was seen, marks what is new or has moved since your last visit, and opens the full calculation. **Phase 7: grading.** After each game, every frozen prediction is graded by book rules: overtime counts, the shootout doesn't, and a player who doesn't play voids the bet. Each one is compared with the closing line. **Model Performance** shows:

- record, profit, ROI with its 95% range and closing-line value;
- a profit chart with drawdowns;
- calibration next to the no-vig market;
- results by market, confidence, month and model version.

Losing stretches stay visible. Model 1.3 also prices game and team totals the way books settle them (the shootout winner counts as a goal). **Phase 8: alerts, news and parlays.**

- **Alerts:** rules in `config/alerts.yml` (strong leans, line moves, goalie confirmations, news, a player's line) push to your phone through ntfy, at most once per game (see [setup](docs/setup.md#alerts-on-your-phone-phase-8)).
- **News:** a Quick Entry form records news with its source link; it shows on the News and player pages.
- **Parlay builder:** combines legs using correlations measured from past games, each with its sample size and 95% interval. Pairs without enough data are treated as independent, and the page says so.

**Phase 9: on your phone.** The site installs as a home-screen app: tap Share → Add to Home Screen, and it opens offline with the last data it loaded.

- The tab bar is Slate · Best · Search · Parlay · More.
- Prop cards and the More menu open as bottom sheets.
- **Settings** sets odds format (American or decimal), time zone, and a **cool-off** that hides every price and prop on that device for a day to a month. It can't be ended early.
- Pages load on demand, and CI enforces a download budget.

**Phase 10: hardening.**

- **Backups:** the newest 10 store versions are kept, plus one a week for 8 weeks.
- **Restore drill:** runs about once a day and shows on Admin.
- **Rollback:** a one-click `store` workflow restores any kept version.
- **Watchdog:** an hourly workflow opens an issue (and pushes to your phone) if the pipeline stops succeeding.
- **Keepalive:** it now verifies that every scheduled workflow stays on.
- **New pages:** Goalies and Line Movement. Every page in the menu is now built.

**Keep offline copies of STORE_KEY and DATA_KEY**: see [setup](docs/setup.md#keys-backups-and-the-watchdog-phase-10).

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

**After Phase 10:**

- **Injury report:** ESPN's public report is read every run, with a source link on every entry. Players listed out, on IR or suspended get no projection, injured goalies leave the starter mix, and day-to-day players are flagged.
- **Line-movement markers:** player charts mark goalie confirmations, players ruled out, news and injury changes.
- **Live calibration:** once a market has 400 graded props, the model's probabilities are recalibrated against how often they actually came true (isotonic regression). The new map is used only if it scored better on recent props it never saw.
- **Saves + Win:** a joint model of a goalie's saves and his team winning, shown on goalie pages for 20+ to 30+ saves, with its own walk-forward test.

**Model 1.5: back-to-backs, lines and champion/challenger.**

* Lines, defence pairs and PP units are reconstructed from every game's NHL shift chart. A **Quick Entry: line change** form records a promotion or demotion for an upcoming game.
* Version 1.5 can use back-to-back terms, its line slot's usual ice time, and linemate quality.
* It runs beside the published 1.4 as the **challenger**. Every line is priced by both, and 1.5 replaces 1.4 for a model family only after it beats 1.4 on 250+ graded props. The Models page shows the race.
* Same-game parlay legs are combined by a **seeded game simulation** that the browser runs from published inputs: linemates' points, shots vs the opposing goalie's saves, and the result move together. The correlation estimates are the fallback.
