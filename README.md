# RinkX — NHL Props Intelligence

A research terminal for NHL player-prop markets. It shows transparent statistical projections, full probability distributions, model-vs-market edges with the vig removed, matchup and role context, and honest backtesting.

RinkX answers **"why does the model project this player at this number?"** It does not tell anyone what to bet. It gives statistical estimates, never guarantees.

## Status

**Design phase complete. Implementation starts at Phase 0/1.** No application code yet.

## Design package

| Doc | Contents |
|---|---|
| [01 · Architecture](docs/01-architecture.md) | Product rules, system topology, event-driven recalculation, caching, security, deployment |
| [02 · Database](docs/02-database.md) | Entity map, table groups, design decisions. DDL in [`db/schema.sql`](db/schema.sql) |
| [03 · API](docs/03-api.md) | REST + SSE endpoints, response envelope, canonical prop-card payload, parlay analysis |
| [04 · Data sources](docs/04-data-sources.md) | Free / paid / optional sources, licensing posture, ingestion contract |
| [05 · Models](docs/05-models.md) | TOI, shots, scoring, goalie, game simulation, devig & edge, confidence, correlation, walk-forward validation |
| [06 · UI](docs/06-ui.md) | Design language, navigation, routes, wireframes, iPhone requirements |
| [07 · Folder structure](docs/07-folder-structure.md) | Monorepo layout |
| [08 · Roadmap](docs/08-roadmap.md) | Phases 0–10 with test gates; open decisions |

## Validate the schema

```bash
createdb rinkx
psql -v ON_ERROR_STOP=1 -d rinkx -f db/schema.sql
psql -v ON_ERROR_STOP=1 -d rinkx -f db/tests/schema_smoke.sql   # -> "schema smoke test: OK"
```

## Responsible use

RinkX presents probabilities, not certainties. Sports betting carries financial risk and is legal only in some jurisdictions and only for those of legal age. If gambling stops being fun, help is available: in the US, call 1-800-GAMBLER.
