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
| `manifest.json` *(public)* | — | Build time, schema version, per-feed `last_success_at` and status, file list with SHA-256 hashes |
| `slate/{date}.json.enc` | `GET /games/today` | Games with teams, records, L10, venue, start time, goalies (projected/confirmed + source), environment (total, ML, implied team totals), rest/B2B/travel, injury counts, PP1 units, team metrics |
| `games/{id}.json.enc` | `GET /games/{id}` + `/games/{id}/props` | Full game detail, lineups (ES + PP/PK), injuries, every priced prop card for the game |
| `props/best/{date}.json.enc` | `GET /props/best` | Flattened rows for the Best Props table. Filtering and sorting happen in the browser. |
| `props/{prediction_id}.json.enc` | `GET /props/{id}` + `/explain` + `/history` | Prop card (shape below), explain trail, projection revisions |
| `lines/{game_id}.json.enc` | `GET /lines/compare`, `/lines/movement` | Per prop: book-by-book current lines, movement series, best price, consensus |
| `players/index.json.enc` | `GET /players?q=` | Compact search index (id, name, team, position) |
| `players/{id}.json.enc` | `GET /players/{id}` (+ games, props, shots) | Season stats, splits, complete game log, usage trends, line history, prop history, shot locations |
| `goalies/{date}.json.enc` | `GET /goalies/today` | Starters, start status + source, projected shots against, saves distributions |
| `news/latest.json.enc` | `GET /news`, `/injuries` | Last 7 days of news and active availability items, each with source URL and timestamp |
| `performance/*.json.enc` | `GET /model/performance`, `/model/calibration` | Metrics by market, confidence bucket and month; reliability bins; cumulative P&L; both `live_tracked` and `lineup_oracle` modes |
| `correlations/{date}.json.enc` | (used by parlay) | Pairwise correlations relevant to today's props, with `n_obs`, CI and method; same-game joint probabilities from the simulation |
| `admin/health.json.enc` | `GET /admin/*` | Per-source health, recent runs and failures, open data-quality issues, market coverage, odds credit usage, model registry |

Older dates stay published for 30 days. After that, the history remains in the store and in player and performance files.

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
* Pairwise correlations (with `n_obs` and CI) come from `correlations/{date}`. Same-game joint probabilities from the simulation are used when available.
* The combined probability uses a Gaussian copula over the correlation matrix (made positive semi-definite). With insufficient data, legs are treated as independent and labeled that way.
* Fair odds, offered implied probability, edge and a fixed variance warning are shown.

The TypeScript implementation is tested against **shared fixture vectors** generated by the Python reference implementation (`fixtures/parlay_vectors.json`), so the two can't disagree.

## Write path: Quick Entry and config

| Action | How |
|---|---|
| Confirm goalie / scratch / line or PP change / injury / news | GitHub **Issue form** (opens prefilled from buttons in the app). Required fields: type, player/team, status, **source URL**, published time. `quick-entry.yml` validates it, writes it with `provenance='manual'`, recomputes, republishes, and closes the issue with a before/after summary. |
| Create or edit alerts | Edit `config/alerts.yml` in the GitHub app. It syncs into the `alerts` table on the next run. |
| Choose books / odds budget | `config/books.yml`, `config/budget.yml` |
| Promote a model | Issue form "Promote model" (owner only) → the workflow flips champion status and attaches the backtest report |
| Force refresh | **Actions → pipeline → Run workflow** in the GitHub app |
