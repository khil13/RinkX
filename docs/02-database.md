# 02 · Database Design

The authoritative DDL is in [`db/schema.sql`](../db/schema.sql). It is checked by [`db/tests/test_schema.py`](../db/tests/test_schema.py).

**Engine: SQLite (STRICT tables), one file.** RinkX runs on GitHub Actions + Pages with no database server (see [`01-architecture.md`](01-architecture.md)). Each run downloads the encrypted SQLite file from a GitHub Release, applies changes, and uploads a new version. SQLite was chosen over DuckDB because it enforces the constraints this design relies on: partial unique indexes, cascading foreign keys, CHECKs and triggers. DuckDB still attaches the same file read-only for heavy backtest queries.

Expected size is a few hundred MB for 5 seasons of stats and PBP plus one season of line movements for your books. That is well within the 2 GB Release-asset limit, and line movements from past seasons can be archived to Parquet assets. Schema changes ship as numbered files in `db/migrations/`, tracked with `PRAGMA user_version`.

## Entity map

```mermaid
erDiagram
  data_sources ||--o{ ingestion_runs : logs
  seasons ||--o{ games : contains
  teams ||--o{ games : "home/away"
  teams ||--o{ players : rosters
  players ||--o{ player_team_stints : history
  games ||--o{ game_team_context : "rest/travel"
  games ||--o{ player_game_stats : box
  games ||--o{ goalie_game_stats : box
  games ||--o{ team_game_stats : box
  games ||--o{ pbp_shot_events : pbp
  players ||--o{ injuries : availability
  teams ||--o{ lineup_snapshots : deploys
  lineup_snapshots ||--o{ line_combinations : ES
  lineup_snapshots ||--o{ powerplay_units : "PP/PK"
  games ||--o{ goalie_starts : starters
  markets ||--o{ prop_lines : defines
  sportsbooks ||--o{ prop_lines : offers
  prop_lines ||--o{ line_movements : history
  games ||--o{ historical_odds : archive
  model_versions ||--o{ player_projections : produces
  player_projections ||--o{ predictions : priced
  predictions ||--o| model_results : graded
  model_versions ||--o{ backtest_runs : evaluated
  markets ||--o{ prop_correlations : pairs
  news ||--o{ news_entities : tags
  alerts ||--o{ alert_events : fires
  players ||--o{ player_aliases : names
```

## Table groups

| Group | Tables | Notes |
|---|---|---|
| Provenance / ops | `data_sources`, `ingestion_runs`, `data_quality_issues` | Every source is registered with its tier (free/paid/optional) and license notes. Each job run records HTTP calls and the quota left. |
| League | `seasons`, `teams`, `players`, `player_team_stints`, `games`, `game_team_context` | `game_team_context` is derived: rest days, back-to-back flag, games in the last 7 days, travel km (haversine between venues) and time-zone shift. |
| Stats | `player_game_stats`, `goalie_game_stats`, `team_game_stats`, `pbp_shot_events` | TOI is split by strength (EV/PP/SH). Each table has an `extra` JSON column for new stats without a migration. Shot-level PBP drives shot charts and in-house xG. |
| Availability & roles | `injuries`, `lineup_snapshots`, `line_combinations`, `powerplay_units`, `goalie_starts` | Lineups are stored as **snapshots**, so role changes come from diffing consecutive snapshots. Goalie starts move through `projected → likely → confirmed → actual`. |
| Markets | `markets`, `sportsbooks`, `prop_lines`, `line_movements`, `historical_odds` | `markets` is a catalogue, so a new prop is an `INSERT`. `prop_lines` holds current state. `line_movements` is append-only (written only on change). Closing prices for CLV come from the last `line_movements` row before the start. |
| Modeling | `model_versions`, `player_projections`, `predictions`, `model_results`, `backtest_runs`, `prop_correlations` | See below. `model_results` (Phase 7, migration 0009): one row per graded prediction with `actual_value`, the winning `result`, `void_reason`, the lean's `outcome` (`no_bet` when it had none) and `profit_units`, the closing price, and `clv`. |
| Calibration | `calibrators` | Migration 0010: one current isotonic map per market (`knots`), with the fit and held-out sizes, held-out Brier before and after, and whether it is `applied`. |
| Alerts | `alerts`, `alert_events` | Single owner, so there is no users table. Alert definitions live in `config/alerts.yml` and are synced in. A unique (alert, game) index makes each alert fire at most once per game. Saved parlays stay in the browser. |
| Entity resolution | `player_aliases` | Maps odds-vendor player names to NHL ids |
| News | `news`, `news_entities` | `url` is `NOT NULL`, so a news item without a source cannot be stored. Phase 8 fills them from the "Quick Entry: news" form (`provenance='manual'`, `external_id='issue-N'`). |

The requested core table list maps onto this schema, except `users`, which is unnecessary with a single owner. `player_projections` stores full distributions. `predictions` stores those projections priced against a specific line.

## Key design decisions

1. **Props are data, not code.** `markets(code, subject, kind, stat_expr, settlement_rule, model_family)` describes each prop. Supporting a new prop (say "Faceoffs Won") means inserting a row, mapping the vendor's market key to it in the adapter config, and pointing `model_family` at a model. Until a model exists, `model_family` stays `NULL`, and the UI shows lines and hit rates but labels the projection "Not modeled".
2. **Projection ≠ prediction.**
   * `player_projections` holds the model's full PMF for a (player, game, stat), plus inputs, reasons for and against, data quality and `as_of`. It does not depend on any sportsbook.
   * `predictions` freezes that projection against a specific line and price at a moment in time: model probability, raw implied probability, no-vig probability, edge, EV and confidence. Once published, these rows are **immutable**: SQLite triggers abort any UPDATE or DELETE. They are what backtesting and ROI are computed from.
3. **Immutable projection history.** A recalculation sets `is_current = false` on the old row and inserts a new row with `supersedes` pointing at it. A partial unique index guarantees one current projection per (game, player, market). The chain powers the before/after display.
4. **Point-in-time correctness.** `as_of` on projections, `observed_at`/`reported_at` on lineups, goalies and injuries, and `captured_at` on odds let the feature builder reconstruct "what we knew at time T". Walk-forward backtests depend on this.
5. **Data quality is first-class.** Stat rows carry `provenance` and `quality`. Projections carry `data_quality` and `missing_inputs[]`. A `data_quality_issues` queue feeds the admin dashboard.
6. **Enrichment.** `game_enrichment` records when each game's play-by-play and stats-API reports were applied, so each runs once. Teammate-blocked shots follow the official convention, verified against the box score and stats API: they count as the shooter's blocked attempt but not as anyone's blocked shot (`is_teammate_block = 1`, `blocker_id` NULL).
7. **Quick Entry** *(migration 0005)*. `game_availability` records a player ruled out of, or back into, one game, with a mandatory source URL. `quick_entries` logs each processed issue, so it is applied exactly once. Projections for games more than 14 days old are pruned, unless a prediction references them.
8. **Game projections** *(migration 0006)*. `game_projections` holds game- and team-level outputs: moneyline win probability, team totals and the game total. It follows the same immutable supersede chain as `player_projections`.
9. **Size management.** `line_movements` stores only changes, and only for books enabled in `config/books.yml`. At season end, its rows are exported to a Parquet Release asset and pruned from SQLite. Stats and PBP tables stay small, at under 200k rows per season per table.
10. **One champion per model family.** A partial unique index on `model_versions(model_family) WHERE status='champion'` makes promotion an atomic swap.

## Prop catalogue (seeded)

| Code | Prop | Kind | Model family |
|---|---|---|---|
| `skater_shots_on_goal` | Shots on Goal | O/U | `skater_shots` |
| `skater_goals` | Goals | O/U | `skater_scoring` |
| `skater_assists` | Assists | O/U | `skater_scoring` |
| `skater_points` | Points ("Total Points") | O/U | `skater_scoring` |
| `skater_pp_points` | Power-play Points | O/U | `skater_scoring` |
| `skater_pp_goal` / `skater_pp_assist` | PP Goal / PP Assist | Yes/No | `skater_scoring` |
| `skater_blocked_shots` | Blocked Shots | O/U | `skater_blocks` |
| `skater_hits` | Hits | O/U | `skater_hits` |
| `skater_anytime_goal` | Anytime Goal | Yes/No | `skater_scoring` |
| `skater_first_goal` | First Goal | Yes/No | `game_sim` |
| `goalie_saves` | Saves | O/U | `goalie` |
| `goalie_goals_against` | Goals Against | O/U | `goalie` |
| `goalie_shutout` | Shutout | Yes/No | `goalie` |
| `goalie_win` | Win | Yes/No | `game_sim` |
| `goalie_saves_and_win` | Saves + Win | Yes/No | `game_sim` |
| `game_moneyline`, `game_total`, `team_total` | Game environment inputs | — | `game_sim` |

Settlement rules (whether OT counts, shootout exclusions, void-if-DNP, etc.) live in `markets.settlement_rule`. Books differ here: shootout goals never count as goals, and goalie props generally void if the goalie doesn't start. The adapter records each book's rule, and the grader applies it.

## Running the schema locally

```bash
python db/tests/test_schema.py            # in-memory DB, synthetic rows -> "schema tests: 10 passed"
sqlite3 rinkx.db < db/schema.sql          # create a local dev store
```

Every connection must run `PRAGMA foreign_keys = ON`. The `rinkx.store` module does this for you.
