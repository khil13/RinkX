# 03 · Data Contract (static "API")

There is no server, so the API is a set of **static, encrypted JSON files** that the pipeline writes on each publish and the browser reads. The response shapes match what a REST API would return, so moving to a real server later would mean swapping the fetch layer, not rewriting the UI.

Pydantic models in `pipeline/rinkx/publish/schemas.py` define every file. CI generates TypeScript types from their JSON Schema (`json-schema-to-typescript`) and fails if the site's types drift.

## Envelope

Every decrypted file has the same top level:

```jsonc
{
  "data": { /* payload */ },
  "meta": {
    "generated_at": "2026-10-10T21:04:11Z",
    "data_status": "live",              // live | stale | unavailable | synthetic
    "oldest_input_at": "2026-10-10T20:58:02Z",
    "sources": ["nhl_web_api", "the_odds_api"],
    "model_versions": {"skater_shots": "2026.10.03"}
  }
}
```

* **Missing values never become zero.** A field without data is `null`, and a sibling `*_reason` field says why (`"data_unavailable"`, `"insufficient_sample"`, `"not_modeled"`, `"vig_not_removable"`).
* Times are UTC ISO-8601. The browser renders them in your time zone.
* Odds are American, with decimal alongside. Probabilities are 0–1 floats.

## Encryption format

```jsonc
// /data/keyfile.json (public)
{"v": 1, "kdf": "PBKDF2-SHA256", "iterations": 600000, "salt": "<b64>",
 "wrapped_key": "<b64 AES-KW of DATA_KEY>"}
// each *.json.enc
{"v": 1, "alg": "AES-256-GCM", "iv": "<b64 96-bit>", "ct": "<b64 ciphertext+tag>"}
```

Each file gets a fresh random IV. The file path is bound in as GCM additional data, so a file can't be swapped for another.

## File map

| File | Equivalent endpoint | Contents |
|---|---|---|
| `manifest.json` *(public)* | — | Build time, schema version, `slate_date` (Eastern date the site opens on, 6 am rollover), per-feed `last_success_at` and status, file list with SHA-256 hashes |
| `slate/{date}.json.enc` | `GET /games/today` | Games with teams, records, L10, venue, start time, goalies (projected/confirmed + source), environment (total, ML, implied team totals), rest/B2B/travel, injury counts, PP1 units, team metrics |
| `games/{id}.json.enc` | `GET /games/{id}` + `/games/{id}/props` | Full game detail, lineups (ES + PP/PK), injuries, every priced prop card for the game |
| `props/best.json.enc` | `GET /props/best`, `/props` | *(Phase 6)* One row per open line for upcoming games: the latest frozen prediction, priced against a current projection. Filtering and sorting happen in the browser. |
| `props/{prediction_id}.json.enc` | `GET /props/{id}` + `/explain` + `/history` | Prop card (shape below), explain trail, projection revisions |
| `lines/{game_id}.json.enc` | `GET /lines/compare`, `/lines/movement` | Per prop: book-by-book current lines, movement series, best price, consensus |
| `players/index.json.enc` | `GET /players?q=` | Compact search index (id, name, team, position) |
| `players/{id}.json.enc` | `GET /players/{id}` (+ games, props, shots) | Season stats, splits, complete game log, usage trends, line history, prop history, shot locations |
| `goalies/{date}.json.enc` | `GET /goalies/today` | Starters, start status + source, projected shots against, saves distributions |
| `news.json.enc` | `GET /news` | *(Phase 8)* News entered through Quick Entry in the last 14 days: headline, category, reliability, source URL, published time, player/team. Player files carry the same items for that player as `news`. |
| `alerts.json.enc` | `GET /alerts` | *(Phase 8)* The alerts configured in `config/alerts.yml` (key, type, active, condition) and the last 14 days of fired events (message, game, `delivery`: pending / sent / failed). |
| `performance.json.enc` | `GET /model/performance`, `/model/calibration` | *(Phase 7)* Live-tracked results of graded predictions: `calibration` (Brier, log loss, reliability, ECE, model vs no-vig market), `bets` (record, profit, ROI + 95% interval, CLV, % beat close), `series` (daily and cumulative profit), `max_drawdown`, `by_market` / `by_confidence` / `by_month` / `by_version`, `confidence_monotonic`, `voids`, and the 50 most recent graded leans. The walk-forward (`lineup_oracle`) results stay in `models.json`. |
| `correlations.json.enc` | (used by parlay) | *(Phase 8)* Residual correlations by market pair and relation (`same_player`, `same_team`, `opponent`, `opp_goalie`), each with `rho`, 95% `ci`, `n` and method; `min_n` below which the browser treats a pair as independent. |
| `models.json.enc` | `GET /model/tests` | *(Phase 3)* Walk-forward test per stat: test/tune windows, log score vs both baselines with 95% bounds, PIT histogram, chosen settings, pass/fail and reason; markets not modeled yet |
| `admin/health.json.enc` | `GET /admin/*` | Per-source health, recent runs and failures, open data-quality issues, market coverage, odds credit usage, model registry |

A slate is published only for dates that a successful schedule fetch covered (`schedule_coverage`), so an empty slate always means "no games", never "no data". Phase 1 publishes slates for today ± 3 days. Older dates stay published for 30 days once later phases need them. After that, the history remains in the store and in player and performance files.

## Canonical prop card (`props/{id}`)

```jsonc
{
  "prediction_id": 812345,
  "player": {"id": 8479318, "name": "Auston Matthews", "team": "TOR", "position": "C"},
  "game": {"id": 2026020114, "opponent": "BOS", "is_home": true, "start_time_utc": "..."},
  "market": {"code": "skater_shots_on_goal", "name": "Shots on Goal"},
  "sportsbook": {"code": "book_a", "name": "Book A"},
  "line": 4.5, "over_price": -115, "under_price": -105,

  "projection": {
    "mean": 5.21, "median": 5, "std_dev": 2.31,
    "distribution": [{"k": 0, "p_ge": 1.0}, {"k": 1, "p_ge": 0.98}, {"k": 2, "p_ge": 0.91}, "..."],
    "p_over": 0.612, "p_under": 0.388, "p_push": 0.0
  },
  "market_prob": {
    "implied_over": 0.535, "implied_under": 0.512,
    "novig_over": 0.511, "novig_under": 0.489, "devig_method": "multiplicative",
    "consensus_novig_over": 0.503, "books_in_consensus": 3
  },
  "edge": {"over": 0.101, "under": -0.101, "ev_over_per_unit": 0.144, "side": "over"},
  "calculation": [
    "Implied(-115) = 115/215 = 0.5349",
    "Implied(-105) = 105/205 = 0.5122",
    "Overround = 1.0471",
    "No-vig Over = 0.5349 / 1.0471 = 0.5108",
    "Model P(Over 4.5) = P(SOG >= 5) = 0.612",
    "Edge = 0.612 - 0.511 = +0.101 (+10.1 pts)"
  ],
  "confidence": {
    "score": 74,
    "parts": {"edge_strength": 21, "role_certainty": 17, "data_quality": 16, "market_agreement": 8, "availability": 12},
    "max":   {"edge_strength": 30, "role_certainty": 20, "data_quality": 20, "market_agreement": 15, "availability": 15}
  },
  "data_quality": {"score": 0.86, "missing_inputs": ["opponent_hd_shots_allowed_l10"]},
  "hit_rates": {
    "line": 4.5,
    "last_5":  {"hits": 4,  "games": 5,  "values": [6, 5, 3, 7, 5]},
    "last_10": {"hits": 7,  "games": 10},
    "last_15": {"hits": 9,  "games": 15},
    "last_20": {"hits": 13, "games": 20},
    "season":  {"hits": 31, "games": 55}
  },
  "context": {
    "expected_toi_s": 1284, "expected_pp_toi_s": 205,
    "line_assignment": {"es": "F1", "pp": "PP1", "status": "confirmed"},
    "opponent": "BOS",
    "opposing_goalie": {"name": "...", "status": "projected"}
  },
  "reasons_for":     [{"factor": "shot_rate", "text": "Averages 5.0 SOG over last 20 (iSF/60 11.2)"}],
  "reasons_against": [{"factor": "opp_suppression", "text": "BOS allows 27.1 SA/60 at 5v5 (5th fewest)"}],
  "timestamps": {"projection_computed_at": "...", "line_observed_at": "...", "as_of": "..."},
  "provenance": "derived"
}
```

> The values above show the **shape** only. The pipeline never publishes numbers like these unless they come from real data. Synthetic rows block a production publish.

## Browser-side computation (parlay)

The parlay builder is interactive, so it runs in the browser. It does no modeling. It combines numbers the pipeline already published:

* Leg probabilities come from the prop cards.
* Pairwise correlations (with `n_obs` and CI) come from `correlations.json`. Legs in different games are independent. Game-level legs, a goalie with his own skaters, and pairs with fewer than `min_n` games are treated as independent, and the page says which.
* The combined probability uses a Gaussian copula over the correlation matrix (made positive semi-definite). With insufficient data, legs are treated as independent and labeled that way.
* Fair odds, offered implied probability, edge and a fixed variance warning are shown.

The TypeScript implementation (`web/src/lib/parlay.ts`) is tested against **shared fixture vectors** generated by the Python reference implementation (`pipeline/rinkx/correlation/parlay.py` → `fixtures/parlay_vectors.json`, regenerated with `python -m rinkx.correlation.parlay`). They agree to 9 decimal places. The orthant probability uses Genz's method on a fixed 4,096-point lattice, so it is deterministic and within about 1e-4 of an exact multivariate-normal CDF. A matrix that isn't positive definite is shrunk toward independence, and the page says by how much.

## Projection payloads (Phase 3)

`games/{id}` carries `projections: {models, reason, home, away}`. Each side has `goalie` (projected or confirmed starter, with probability and source), `players[]` (expected TOI and PP TOI, and per market `{mean, median, sd, p_ge[], data_quality, missing_inputs, previous}`) and `out[]` (players ruled out via Quick Entry, with source). `games/{id}.projections.environment` *(model 1.1)* carries the model-only game outlook: `win {home, away, tied_after_regulation, expected_goals, factors, previous}`, and `totals {home, away, game}`, each with `mean`, `p_ge[]` and `factors`. Every slate game summary carries a compact `model {p_home_win, goals_home, goals_away}`. Neither involves odds. `players/{id}` carries `projection: {game, markets[], reason}`. Its markets add the full `pmf`, `factors_for` / `factors_against` (`{name, effect, detail}`, where `effect` is multiplicative: the reference mean × Π(1 + effect) = projected mean) and `inputs`. `previous` is set when a Quick Entry changed the projection (`{mean, computed_at, reason}`), which drives the before → after display. When nothing is projected, `reason` says why (`models_not_ready`, `no_model_passed`, `game_started`, `ruled_out`, …). Projections never appear for a stat whose model failed its test.

## Sportsbook lines (Phase 4)

`games/{id}.lines` = `{books, markets[], fetched_at, reason}`. Each market has `rows[]`, one per player (or the home team, for the moneyline). A row holds:

- `books[]`: `{book, line, over, under, last_seen_at, last_changed_at}`, with American prices. Moneyline over/under = home/away; yes/no markets use over = Yes.
- `line`: the most common line across books.
- `best_over` / `best_under`: the best price, among books at that line only.
- `consensus`: `{p_over, books}`, the median multiplicative no-vig probability. It is `null` with `consensus_reason: "vig_not_removable"` when only one side is offered.
- `lines_differ`

**Phase 5 pricing:**
- Each book entry carries `pricing`, the latest frozen prediction for that line: model probabilities (pushes excluded), `p_push`, no-vig or implied probability, edge and EV for each side, `side` (`none` unless the lean rules pass), `confidence`, `devig_method` and `priced_at`.
- On player pages it also carries `confidence_parts` (`score`, `parts`, `max`, `notes`) and the `calculation` trail.
- Each row carries `lean`: the best priced side across books (book, side, line, price, edge, EV, confidence), or `null`.

`players/{id}.lines` gives the same data for the player's next game with lines, plus each book's `movement[]` (line_movements). Only lines whose player resolved to an NHL id are stored, so only those are published. Slate game summaries carry `line_count`. `admin/health.odds` holds credits, the last budget plan, unmapped market keys, and unmatched player names with suggestions.

## Best Props (Phase 6)

`props/best.json` = `{generated_at, books, open_lines, rows[], reason}`. `reason` is `not_connected` (no odds key), `no_lines` (no open lines for upcoming games), `not_priced` (lines open, but none has a current projection) or `null`. Each row holds:

- `prediction_id`, `subject` (`player` or `team`), `game`, `market` / `market_label` / `kind`;
- `book`, `book_name`, `source` (always the vendor), `line` and `prices`;
- `lean` (`null` unless the lean rules pass) and `side_scored` (the side the numbers describe), with its `price`, `p_model`, `p_market`, `market_is_novig`, `edge`, `ev`;
- `confidence`, `confidence_parts` (or `null`), `calculation`, `data_quality`, `missing_inputs`;
- `line_seen_at`, `line_changed_at`, `priced_at`.

Only the latest prediction per open line is included, and only when its projection is still current. The Best Props page shows rows with a lean; the Props page shows all of them.

## Write path: Quick Entry and config

| Action | How |
|---|---|
| Confirm goalie / rule a player out *(built in Phase 3)*; news *(Phase 8, form `quick-entry-news.yml`: headline, category, player/team, reliability, source URL)*; line or PP change *(later)* | GitHub **Issue form** (`.github/ISSUE_TEMPLATE/quick-entry-*.yml`), opened prefilled from buttons on the Game page. Required: game, player, status, **source URL**. Opening the issue starts `pipeline.yml`, but only for the owner's issues titled `Quick Entry:`. The run applies every open owner entry once (logged in `quick_entries`), writing `goalie_starts` / `game_availability` with `provenance='manual'`. It then recomputes the game's projections, tagging the changed ones `goalie_confirmed` / `player_out`, and publishes. **After** the store is saved, it comments and closes the issue. The repo is public, so the comment says how many projections changed but never shows their values; before → after is shown only in the app. |
| Create or edit alerts | Edit `config/alerts.yml` in the GitHub app. It syncs into the `alerts` table on the next run. |
| Choose books / odds budget | `config/books.yml`, `config/budget.yml`; market mapping in `config/odds_markets.yml`; name fixes in `config/player_aliases.yml` |
| Promote a model | Issue form "Promote model" (owner only) → the workflow flips champion status and attaches the backtest report |
| Force refresh | **Actions → pipeline → Run workflow** in the GitHub app |
