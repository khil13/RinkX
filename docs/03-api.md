# 03 · API Architecture

FastAPI service, versioned under `/api/v1`, with an OpenAPI 3.1 spec. The Next.js app consumes a TypeScript client generated from that spec (`openapi-typescript`). CI fails if the generated client drifts from the spec.

## Conventions

**Response envelope** — every read endpoint returns:

```jsonc
{
  "data": { /* payload */ },
  "meta": {
    "generated_at": "2026-10-10T21:04:11Z",
    "data_status": "live",            // live | stale | unavailable | synthetic
    "oldest_input_at": "2026-10-10T20:58:02Z",
    "sources": ["nhl_web_api", "the_odds_api"],
    "model_versions": {"skater_shots": "2026.10.03"}
  }
}
```

* **Missing values never become zero.** A field the backend can't fill is `null`, and a sibling `*_reason` field explains why (`"data_unavailable"`, `"insufficient_sample"`, `"not_modeled"`, `"vig_not_removable"`).
* Errors follow RFC 9457 `application/problem+json`.
* Cursor pagination (`?cursor=&limit=`) on lists.
* Times are UTC ISO-8601. The client renders them in the user's time zone.
* Odds come back in American format with decimal alongside. Probabilities are 0–1 floats.

## Public endpoints (authenticated users)

| Method & path | Purpose | Key query params |
|---|---|---|
| `GET /games/today` | Daily slate | `date` (default: today, ET) |
| `GET /games/{id}` | Game detail: teams, records, venue, goalies (projected/confirmed), lines, PP units, injuries, environment (total, ML, implied team totals), team metrics | |
| `GET /games/{id}/props` | Every prop for a game, grouped by player | `market`, `side` |
| `GET /players` | Search | `q`, `team`, `position` |
| `GET /players/{id}` | Profile: season stats, splits, usage trends, line history | `season` |
| `GET /players/{id}/games` | Game log (complete — never filtered to favorable games) | `season`, `last` |
| `GET /players/{id}/props` | Current props plus prop history for the player | `market` |
| `GET /players/{id}/shots` | Shot locations (if PBP available) | `season` |
| `GET /goalies/today` | Today's goalies: start status, projections, save distributions | |
| `GET /props/today` | All priced props today | `market`, `team`, `book`, `min_conf` |
| `GET /props/best` | Strongest edges across the slate | `market[]`, `side`, `book[]`, `sort=edge\|confidence\|probability\|hit_rate\|market`, `min_edge`, `min_conf` |
| `GET /props/{prediction_id}` | Full prop card (structure below) | |
| `GET /props/{prediction_id}/explain` | Explain Projection: inputs, factors for and against, calculation trail | |
| `GET /props/{prediction_id}/history` | Projection revisions (before/after) | |
| `GET /projections` | Raw projection distributions | `game_id`, `player_id`, `market` |
| `GET /lines/compare` | Book-by-book comparison for a prop | `game_id`, `player_id`, `market` |
| `GET /lines/movement` | Time series of line/price changes | `prop_line_id` or (`game_id`,`player_id`,`market`), `book[]` |
| `GET /injuries` | Active availability items with source and timestamp | `team` |
| `GET /news` | News & alerts feed | `team`, `player`, `category`, `since` |
| `GET /matchups` | Player → opponent breakdown | `player_id`, `game_id` |
| `GET /model/performance` | Backtest and live performance | `market`, `bucket=confidence\|probability`, `from`, `to`, `mode=live\|backtest` |
| `GET /model/calibration` | Reliability-diagram bins | `market`, `model_version` |
| `POST /parlay/analyze` | Combined probability with correlation adjustment | body: legs |
| `GET /alerts` · `POST /alerts` · `PATCH /alerts/{id}` · `DELETE /alerts/{id}` | User alerts | |
| `GET /stream` | SSE: `projection.updated`, `line.moved`, `goalie.confirmed`, `lineup.changed`, `news.created`, `alert.fired` | `game_id[]` |

## Admin endpoints (`role=admin`)

| Method & path | Purpose |
|---|---|
| `GET /admin/sources` | Per-source health: last success, lag, error rate, quota remaining |
| `GET /admin/jobs` · `POST /admin/jobs/{name}/run` | Job runs, failures, manual trigger |
| `GET /admin/data-quality` | Open data-quality issues |
| `GET /admin/markets` | Prop market coverage by book and game ("which markets are missing today") |
| `GET /admin/models` · `POST /admin/models/{id}/promote` · `POST /admin/models/retrain` | Model registry and retraining |
| `GET /admin/users` · `PATCH /admin/users/{id}` | User management |
| `POST /admin/news` | Manual news entry (`url` and `published_at` required) |
| `GET /admin/logs` | Structured log search (proxied from the log store) |

## Canonical prop-card payload

This matches the product's required model-output structure. Every field is either populated or `null` with a reason.

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
    "implied_over": 0.535, "implied_under": 0.512,          // raw, includes vig (sum 1.047)
    "novig_over": 0.511, "novig_under": 0.489,
    "devig_method": "multiplicative",
    "consensus_novig_over": 0.503, "books_in_consensus": 5
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
    "parts": {"edge_strength": 21, "role_certainty": 17, "data_quality": 16,
              "market_agreement": 8, "availability": 12},
    "max":   {"edge_strength": 30, "role_certainty": 20, "data_quality": 20,
              "market_agreement": 15, "availability": 15}
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

> Every number in the example above is illustrative of the **shape** only. The API never serves values like these unless they come from real data or rows labeled `synthetic`.

## `POST /parlay/analyze`

```jsonc
// request
{"legs": [{"prediction_id": 812345, "side": "over"}, {"prediction_id": 812399, "side": "yes"}],
 "offered_price": +260}   // optional: the book's parlay price
// response
{
  "legs": [{"p": 0.612, "fair_american": -158}, {"p": 0.41, "fair_american": +144}],
  "independent_p": 0.251,
  "correlation": {"pairs": [{"a": 0, "b": 1, "rho": 0.31, "n_obs": 4120, "method": "tetrachoric",
                             "ci": [0.24, 0.38]}]},
  "adjusted_p": 0.294, "adjustment_method": "gaussian_copula",
  "fair_american": +240, "offered_implied_p": 0.278, "edge": 0.016,
  "warnings": ["Parlays compound variance: a 29% event fails about 7 times in 10.",
               "Same-game parlays are priced with correlation by most books; the offered price may already include it."]
}
```

If a correlation estimate lacks enough data (`n_obs` below the threshold, or a CI that straddles 0 while being wider than ±0.2), the response sets `rho: null` with `reason: "insufficient_sample"` and computes the independent product. It says so in the response and does not invent a correlation.
