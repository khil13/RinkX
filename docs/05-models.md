# 05 · Modeling Architecture

## Principles

1. **Distributions, not point estimates.** Every model outputs a full probability mass function (PMF) over the stat. `P(Over 4.5)` is read off that PMF and is never derived from the mean alone.
2. **Rate × opportunity.** Hockey counting stats are, to first order, *(rate per 60 by strength state) × (minutes in that state)*. Modeling the two separately is more stable, and easier to explain, than regressing raw game totals.
3. **Shrink noisy signals.** Goals, shooting %, and save % over small samples are mostly noise. Every rate is shrunk toward a position- or role-level prior (empirical Bayes), and the strength of each prior is estimated from data.
4. **Simple first, complex only when it wins out-of-sample.** A GLM baseline ships first. Gradient boosting and Bayesian hierarchical models are challengers, promoted only on walk-forward log loss **and** calibration.
5. **Recent overs are not a reason.** Hit rates are shown for context. They enter the model only through the shrunk rates, never as a "streak" feature.

## What is built (Phase 3, model version 1.0)

The sections below are the full design. Version 1.0 implements the baseline subset in
`pipeline/rinkx/models/`, and **ships a stat only if it passes the walk-forward test** (§11). The test runs
daily on all loaded history, and the Model Tests page (`/#/models`) shows the result for every stat.

| Stat (markets) | Formula in 1.0 | Factors the tuner may keep |
|---|---|---|
| Shots on goal | shrunk shots per hour × expected ice time | opponent shots allowed, home/road |
| Goals (Goals, Anytime Goal) | either shrunk goals per hour × ice time, or projected shots × shrunk shooting % (the better on the tuning window) | opponent shots allowed, opposing goalie, home/road |
| Assists, Points | shrunk rate per hour × ice time | opponent shots allowed, opposing goalie, home/road |
| PP points / PP goal / PP assist | shrunk rate per **PP** hour × expected PP time | opponent's PP-against rate, home/road |
| Blocked shots, Hits | shrunk rate per hour × ice time | opponent, home/road, arena scorekeeping (home-game vs road-game totals) |
| Goalie saves, goals against | shots against ~ NB(league × own defence × opponent offence); saves/GA ~ binomial on a shrunk save % | own defence, opponent offence |

* **When the test runs:** daily, or sooner when the number of completed games has grown by 10% (or the last test lacked history and new games have arrived). That way a backfill shows up the same day.
* **Point-in-time state** (`state.py`). Games are replayed in date order. Every game is predicted from the
  state *before* its date, then learned from. The live site calls the same feature functions on the
  state after the last completed game, so the backtest and the live projections run the same code.
  Leakage tests check that features never change when later games, or the game's own result, change.
* **Shrinkage.** `rate = (weighted stat + m × league rate) / (weighted hours + m)`, using position-group
  (F/D) league rates. The prior strength `m`, the recency half-life (8/20/50 games), the save-% prior
  (500/1,500/4,000 shots) and the dispersion (Poisson or NB size) are all chosen by log score on the tuning window.
  Factors that don't improve that score are dropped.
* **Expected ice time** is the player's recency-weighted average, shrunk toward the position average by
  2 games. It is a point estimate in 1.0, and NB dispersion absorbs the TOI uncertainty. The TOI
  *distribution* (§1), line/PP-unit inputs, rest/B2B terms and the game simulation (§6) come later.
* **Goalies.** The projected starter is the goalie with most of the team's last 10 starts, and the start
  probability is his share. A Quick Entry confirmation sets it to 100%. Goalie props are projected only
  for the most likely starter. Opposing skaters' scoring uses the start-weighted mix of goalies.
* **Version 1.1 adds the game model** (`models/game_sim.py`). It covers win probability (moneyline,
  goalie win), overtime chance, team and game goal totals, goalie shutout, and first goal scorer. Each
  is published only if it passes its own walk-forward test (§11). See §6 for the formulas.
* **Version 1.2 adds a playoff factor** for every skater stat and for goalie shots against. It is
  the league-wide playoff vs regular-season rate, learned from earlier playoff games only and shrunk
  toward no effect (200 skater-hours, or 40 team-games). Outside the playoffs it is 1.0. A tuning
  window with no playoff games can't judge it, so it is kept by default and the walk-forward test
  decides, as usual, whether each stat is published. Real-data motivation: in the 1.1 test (mid-March
  through the playoffs), hits were projected about 8% low.
* **Version 1.3 settles totals the way books do.** FanDuel and BetMGM count the shootout winner as
  one goal in game and team totals. The game-total and team-total distributions offered for pricing
  now include it, so a regulation tie always adds exactly one goal (OT or shootout). Team goals in
  the walk-forward test are still real goals. Checked against a 400,000-game simulation in the tests.
* **Not modeled yet:** saves + win, which needs a joint model of the goalie's saves and the result.

## Module layout

```
rinkx/
  features/      point-in-time feature builders (as_of-aware)
  models/
    base.py      ProjectionModel protocol + DiscreteDistribution
    toi.py       expected ice time by strength state
    shots.py     skater shots on goal
    scoring.py   goals, assists, points, PP points (+ anytime / PP goal / PP assist)
    physical.py  hits, blocks
    goalie.py    saves, goals against, shutout
    game_sim.py  Monte Carlo game simulator (win, first goal, SGP joint probabilities)
    calibrate.py isotonic / Platt calibration per market
  pricing/
    odds.py      American <-> decimal <-> implied, devig methods
    edge.py      edge, EV, push handling
    confidence.py
  correlation/   empirical correlation estimation + copula combiner
  backtest/      walk-forward runner, metrics, reports
  registry/      model_versions I/O, champion/challenger promotion
```

```python
class DiscreteDistribution:
    support_min: int
    p: np.ndarray                     # P(X = support_min + i)
    def p_ge(self, k: int) -> float: ...
    def p_over(self, line: float) -> tuple[float, float, float]:  # (over, under, push)
    def mean(self) -> float: ...
    def quantile(self, q: float) -> int: ...

class ProjectionModel(Protocol):
    family: str
    version: str
    def fit(self, train: FeatureFrame) -> None: ...
    def predict(self, x: FeatureRow) -> DiscreteDistribution: ...
    def explain(self, x: FeatureRow) -> Explanation: ...   # factors for/against with signed effects
```

Any model satisfying the protocol can be swapped in through the registry without touching the API or UI.

---

## 1. Expected ice time model (`toi`)

TOI is the biggest driver of every skater prop, so it gets its own model.

* **Targets:** EV, PP and SH seconds, modeled separately.
* **Inputs:** exponentially weighted recent TOI by state (half-life of about 8 games), current line slot and PP unit (from the latest lineup snapshot), recent role change flag, team injuries (missing minutes to redistribute), back-to-back, expected game script (from the moneyline: trailing teams lean on top lines), blowout risk (from the spread and total), and the coach's historical deployment variance.
* **PP time:** team expected PP seconds = f(own penalties drawn/60, opponent penalties taken/60, league referee base rate) × the player's share of team PP time given the unit (PP1 ≈ 55–65%, estimated per team).
* **Method:** ridge/GLM baseline. LightGBM challenger. Output is a mean and a standard deviation per state, which later propagates into count uncertainty.
* **Role change:** when a lineup snapshot changes a player's slot or unit, the slot/unit features update immediately, while the history-based features keep their weights. The before/after projection is stored.

## 2. Shots on Goal model (`skater_shots`)

**Mean structure** (per game, summed over strength states *s* ∈ {EV, PP, SH}):

```
λ = Σ_s  r_s · (TOI_s / 60) · A_opp,s · A_env · A_venue
```

| Term | Definition |
|---|---|
| `r_s` | Shrunk individual shots/60 in state *s*. It blends iSF/60 with iCF/60 × the player's shrunk on-target ratio (shot attempts stabilize faster than SOG), using multiple windows (L10/L20/season/prior season) weighted by recency and sample size. Prior: position × role (F1–F4, D1–D3) mean. |
| `TOI_s` | From the TOI model (a distribution, not a constant). |
| `A_opp,s` | Opponent shots-allowed factor in state *s*. Opponent SA/60 and CA/60 relative to league average, shrunk, and split by position faced (F vs D) where samples allow. PP opportunities enter through `TOI_PP`, driven by opponent penalty rate. |
| `A_env` | Game environment: no-vig game total and moneyline → expected game script. Trailing teams generate more attempts (score effects), and high totals indicate pace. Estimated coefficients, **not** a blanket boost. |
| `A_venue` | Home-arena scorekeeper SOG bias, estimated from home vs road shot-count differentials and shrunk heavily. |
| Rest / travel | Back-to-back, rest days, travel km and time-zone shift enter as small multiplicative terms. They are kept only if significant out-of-sample. |

**Distribution.**
1. For each draw from the TOI distribution, compute λ. Mixing Poisson(λ) over TOI uncertainty produces a naturally over-dispersed count.
2. Fit residual dispersion with a **negative binomial** (dispersion per position group).
3. Candidate distributions are Poisson, NB and Conway-Maxwell-Poisson. The choice is made by walk-forward log score and checked with PIT histograms. The data decides; we don't assume NB.

**Challenger:** LightGBM with a Poisson objective on the same features predicts λ, using the same dispersion layer. An ensemble (stacked on out-of-fold log loss) is promoted only if it beats both.

**Output:** a full PMF, `P(k+)` for k = 0…15, mean, median and SD.

## 3. Goals / Assists / Points / PP Points (`skater_scoring`)

**Goals.**
```
λ_G = E[SOG] · f_shrunk · G_opp_goalie · PP_mix
```
* `f_shrunk`: the finishing rate per shot. Built from individual xG per shot (shot quality, stable) plus a finishing-talent term (actual − expected goals per shot) **heavily shrunk** with a beta prior whose strength is fit from data (finishing talent needs hundreds of shots to show). This is how shooting-% regression is enforced.
* `G_opp_goalie`: the opposing **expected starter's** goals saved above expected per shot, shrunk. It is a mixture over goalies weighted by start probability until confirmed.
* `PP_mix`: PP shots carry higher xG. Handled by computing by strength state.
* `P(anytime goal) = 1 − P(G = 0)` from a Poisson/NB on λ_G.
* **PP goal:** the same, restricted to PP state.

**Assists.**
```
λ_A = E[team goals while player on ice] · (a1_share + a2_share)
```
* Team on-ice goals come from the team's expected goals (from the game simulation, below) × the player's expected share of team ice time by state.
* `a1_share` and `a2_share` are the player's shrunk primary and secondary assist rates per on-ice goal. Secondary assists are noisier and shrink harder.
* Linemates enter through the on-ice goal rate of the player's current line (from the lineup snapshot), shrunk toward the player's own on-ice history.

**Points / PP Points.** Points are *not* goals + assists as independent variables. Each simulated on-ice team goal is assigned to the player as goal, A1, A2 or nothing (a multinomial with the player's shares), so the joint distribution of G, A and P is coherent. PP points follow the same process restricted to PP goals.

**Inputs explicitly considered:** expected TOI, line position, linemates, team scoring environment (implied team total), individual rates, ixG, shot volume, shooting-% regression, A1/A2 rates, PP role, opponent defense (xGA/60), opponent PK (PP xGF allowed/60, shrunk), opposing starter, recent usage.

## 4. Hits and Blocks (`skater_hits`, `skater_blocks`)

Rate × TOI with NB dispersion, like shots. Two adjustments matter most:
* **Venue scorekeeper bias** is large for hits, and needs a strong, explicitly shown factor.
* **Blocks** scale with opponent shot attempts against while the player is on ice. They use opponent CF/60 and expected game script: a team protecting a lead blocks more.

## 5. Goaltender model (`goalie`)

```
SA      ~ NegBin( μ_SA , φ )                     # shots faced
saves|SA ~ BetaBinomial( SA, α, β )              # save talent with uncertainty
GA       = SA − saves
```
* `μ_SA` = opponent shots/60 (shrunk) × own team shots-against suppression × expected-script adjustment × expected goalie TOI.
* The save-probability prior combines the goalie's shrunk save % (career → season → recent, weighted by shots, never L5 alone), adjusted for the expected shot quality of this opponent (opponent xG per shot, high-danger share), and the goalie's high-danger save % (shrunk hard).
* **Pull risk:** P(pulled) depends on goals allowed, estimated from history. Implemented inside the game simulation so saves and pull are jointly consistent.
* **Starter uncertainty:** until confirmed, goalie props are **priced only for the projected starter**, with the start probability shown. Confidence takes the "goalie unconfirmed" penalty. Most books void goalie props if the goalie doesn't start, and the grader follows the book's settlement rule.
* **Outputs:** saves PMF (P(over 27.5), P(over 28.5), …), GA PMF, shutout probability and win probability. Win and shutout come from the game simulation.

## 6. Game environment and simulation (`game_sim`)

**Built in 1.1, without odds.** Everything below is computed in closed form, so the results carry no
simulation noise. A test checks the formulas against 400,000 simulated games.

1. **Team expected goals.**
   `λ = league shots/game × team shots-for factor × opponent shots-against factor × league goals/shot
   × team finishing (shrunk) × opposing goalie (start-weighted mix, shrunk save %) × home/road`.
   Settings are tuned on the tuning window by team-goal log score: the finishing and save-% prior
   strengths, which factors to keep, and Poisson vs negative binomial. Explain shows these factors,
   and they multiply exactly to λ.
2. **Regulation:** home and away goals are independent NB/Poisson counts.
3. **Ties after 60 minutes.**
   * Regular season: an overtime goal happens with probability `q`, the share of past tied games
     that ended in OT. The goal is split by λ_home : λ_away. Otherwise there is a shootout, and the
     home team wins it at the past home shootout rate. Both rates are shrunk toward 50/50.
   * Playoffs: play continues until a goal.
4. **Derived outputs.**
   * Win probability, and the chance the game goes past regulation.
   * Team and game totals, including OT goals. The shootout "goal" is never a goal.
   * Shutout: the opponent scores in neither regulation nor OT.
   * First goal: `P(team scores first) × (player's projected goals / team λ)`.
   * Goalie win and shutout are priced for the projected starter only.

**Tests and baselines.**

| Output | Baselines |
|---|---|
| Team goals | Each team's season-average and last-10 Poisson |
| Win | League home-win rate, and log5 of the two teams' win % with home advantage |
| Shutout | League shutout rate, and the goalie's season rate |
| First goal | Equal chance per dressed skater, and season goal share |

Binary outputs also need a Hosmer-Lemeshow calibration p-value above 0.01. In the synthetic tests,
win probability usually doesn't beat the log5 record baseline by more than chance, so it is held
back there. That is the gate working as intended, and the same will happen on real data if the
model can't earn it.

**Later (with odds, Phase 4–5):** blend the model's team totals with market-implied totals,
weighting the two by walk-forward results, and add same-game joint probabilities for parlays.

**First goal (closed-form check):** with competing Poisson scoring processes, `P(player i scores first) ≈ (λ_i / Λ) · (1 − e^(−Λ))`, where Λ is the total game goal rate.

## 7. Pricing: model vs. market

```
implied(American a) = 100/(a+100)        if a > 0
                    = |a|/(|a|+100)      if a < 0
overround           = implied_over + implied_under
```

| Devig method | When |
|---|---|
| **Multiplicative** `p/overround` | Default for two-way markets near even money |
| **Power** (solve `p_o^k + p_u^k = 1`) | Lopsided two-way markets (e.g. Over 0.5 goals at −250/+190), where favorite-longshot bias matters |
| **Shin** | Optional alternative. Reported if it differs materially from power. |
| **Consensus** | Median no-vig probability across books, with market-making books weighted higher. Shown separately. |
| **One-sided markets** (e.g. anytime goal with only "Yes" offered) | Vig **cannot** be removed exactly. We show raw implied probability, set `p_novig = null` with `reason: "vig_not_removable"`, and optionally show an *estimate* based on the book's typical margin, labeled as an estimate. |

```
edge  = p_model − p_novig                  (percentage points)
EV/1u = p_model · (decimal − 1) − (1 − p_model)    at the offered price
```
* **Pushes:** on whole-number lines, `p_over` and `p_under` are conditioned on no push, and `p_push` is reported.
* A side is labeled *Over/Under* only when edge ≥ the market's minimum (initially 3 pts, tuned by backtest), EV > 0 at the offered price, and data quality ≥ the floor. Otherwise `side = none`.
* The full calculation trail is returned with every prop (see the API doc).

**Built in Phase 5** (`pipeline/rinkx/pricing/`, thresholds in `config/pricing.yml`):

- **Removing the margin:** multiplicative by default; power when either side's implied probability is above 0.65. Shin is reported in the calculation trail. The consensus is the median across books at the most common line.
- **Model probability at the line:** read off the projection's PMF. On whole-number lines, P(push) is reported and over/under are conditioned on no push. Expected value uses the unconditional probabilities, because a push returns the stake.
- **One-sided markets:** compared with the raw implied probability. That includes the margin, so the edge is understated rather than overstated.
- **Frozen predictions:** every priced line is written to `predictions` as published (immutable by trigger), but only when the projection or the price changed. Since migration 0008, game markets (moneyline, total) are priced too, via `game_projection_id`.

## 8. Confidence score (0–100)

Confidence is **not** "how much the model likes it". Edge is one of five components and is measured relative to uncertainty.

| Component | Max | How it's computed |
|---|---|---|
| **Edge strength** | 30 | `z = edge / σ_edge`, where σ_edge combines model probability uncertainty (from bootstrap/posterior draws of λ) and cross-book no-vig dispersion. Score = 30·clip(z/2.5, 0, 1) × **track-record multiplier**: the realized-vs-predicted edge ratio for this market and probability bucket in live history, shrunk toward 1 with few observations. This is where historical model performance enters. |
| **Role certainty** | 20 | Lineup status (confirmed > likely > projected), stability of line slot and PP unit over the last 5 games, TOI forecast SD, and a recent role-change penalty. |
| **Data quality** | 20 | `data_quality × 20` minus sample penalties (< 10 games with the current team, new to the league, missing PBP for recent games). |
| **Market agreement** | 15 | Line stability (penalty if the line moved against our side recently), cross-book agreement, and an **implausible-edge penalty**: edges above ~15 pts are more often missing information (injury, lineup) than real value, so confidence *drops*. |
| **Availability** | 15 | Player status (available vs GTD), own goalie confirmation (goalie props), opposing goalie confirmation (skater props), and no postponement risk. |

**As built (Phase 5):**
- **Model uncertainty** is approximated as `sqrt(p(1−p)/n)`, with n = games of history (5–82). Cross-book dispersion is half the spread of no-vig probabilities, plus a 1.5-point floor.
- **Track-record multiplier** (Phase 7): once a market has 200 graded leans, realized ROI ÷ expected
  ROI of those leans, clamped to 0.5–1.2. Below 200 it is 1.0, and the note says so.
- **Not available yet, so scored conservatively and stated in the notes:** confirmed lineups and an injury feed.

Every part carries notes explaining its score.

The breakdown is always displayed. Confidence buckets (50–59, 60–69, …) are evaluated on graded results (Model Performance). Once at least two buckets have 30 bets, the page says whether ROI rises with confidence. If it doesn't, the weights get revised.

## 9. Hit-rate engine

* Windows: L5, L10, L15, L20 and season, plus home/away, vs. this opponent, and the last 2 seasons vs. this opponent.
* Computed against **today's line** (counterfactual), and separately against **the line offered that day** where historical odds exist. Both are labeled.
* All games are listed with values. DNPs are listed and excluded from denominators, and games with a different team are flagged. **Nothing is filtered.**
* Wilson 95% intervals are shown for small samples ("4/5 = 80%, CI 38–96%"). This display is descriptive and isn't fed to the model as a streak signal.

## 10. Correlation engine

* **As built (Phase 8, `correlation/estimate.py`):**
  - Each appearance's stat becomes a standardized residual: (actual − the player's average in earlier games) ÷ √average, using players with 5+ earlier games.
  - Pearson correlations of those residuals are pooled for the same player, teammates, opponents, and a skater vs the opposing starting goalie. Teammate and opponent pairs use closed-form sums per team-game, and a test checks them against brute force.
  - `n_obs` counts independent units: player-games, or team-games. The 95% CI is Fisher's z with that n.
  - Correlations are re-estimated at most every 20 h.
  - Not built: tetrachoric correlations at specific lines, linemate (line-combination) pairs, and simulation-based same-game joints.
* **Empirical (design).** For pairs of prop outcomes (same player across markets, linemates, same team, opponent skater vs. goalie), estimate correlations from historical games:
  * Binary outcomes at specific lines: phi / tetrachoric correlation.
  * Continuous: Pearson correlation of residuals (actual − projected), which removes the shared-mean confound.
  * Each estimate stores `n_obs`, a bootstrap CI, the method and the window.
* **Simulation.** For same-game legs, the game simulation produces the joint probability directly. It is compared with the empirical estimate, and a large disagreement is flagged.
* **Combining legs:** a Gaussian copula with the estimated correlation matrix, made positive semi-definite. With insufficient data, legs are treated as independent and labeled that way. A correlation is never assumed.

---

## 11. Validation & backtesting

**Publication gate (Phase 3, implemented in `models/fit.py`).**
1. All completed games are replayed. Every skater appearance and goalie start gets a prediction made
   only from earlier dates.
2. The first 21 days of history only build state. The earliest 60% of the remaining dates **tune**
   the settings, and the latest 40% are the **test**. Nothing tuned ever sees the test window.
3. On the test window, each stat's mean log score is compared with two baselines: a Poisson at the
   player's **season average** (falling back to last season, then the position average), and a Poisson
   at his **last-10 average**. The comparison is paired per game, with a standard error.
4. A stat passes only if the 95% lower bound of the improvement is above zero against **both**
   baselines, and its randomized-PIT histogram is close to flat (largest bin gap within
   0.02 + 3·√(0.09/n)). It also needs at least 3,000 test rows (150 for goalies).
5. The test knows the actual starting goalies and who dressed (`lineup_mode = lineup_oracle`). Live
   projections don't, and they carry "goalie/lineup unconfirmed" in `missing_inputs`. The
   `live_tracked` numbers come from grading (below).

**Grading and live-tracked performance (Phase 7, `grading/`).**
1. Every run, each prediction for a finished game gets a `model_results` row: the actual stat, the
   side that won at that line, and the outcome and profit (1 unit, at the price taken) of its lean.
   Games from the last 3 days are graded again on each run, so NHL stat corrections flow through.
2. **Settlement rules** (`grading/settle.py`), applied the same way to every book:
   - Stats include overtime, never the shootout.
   - Totals and the moneyline count the shootout winner's goal.
   - A skater prop is void if the player doesn't play; a goalie prop is void unless he starts.
   - First goal: if no goal comes before a shootout, every "Yes" loses.
   - A cancelled game, or one postponed more than 48 h, voids every bet.
3. **Closing line** is the last open price at the same line before the start (`line_movements`).
   **CLV** = closing no-vig probability of our side × decimal price taken − 1: positive means the
   price beat the close.
4. **Metrics** (`grading/performance.py`, published as `performance.json`). They use one row per
   prop: the last prediction before the game at each book, then the best-EV lean across books, so a
   prop at two books counts once.
   - Probability quality on every graded prop, lean or not: Brier, log loss, reliability and ECE,
     next to the same scores for the no-vig market on the same props.
   - Betting results on leans: record, profit, ROI with a normal 95% interval, CLV, % beating the
     close, and cumulative profit with the worst drawdown.
   - Splits by market, confidence bucket, month and model version.
   - Below 100 bets the page says the sample is too small to judge.

*Not built in Phase 7.*
- A separate weekly workflow: the hourly pipeline already re-tests every 20 h and grades every run.
- Isotonic calibrators.
- Automated champion/challenger promotion. A model version changes only by merging code, and its
  walk-forward test still decides what is published. Results are split by model version, so
  versions can be compared on live bets.

Results are stored in `backtest_runs`, per model family, and in `model_versions.oos_metrics`, and
published as `models.json`.


**Walk-forward, never random splits.**
```
train: [season_start_2015 ... day d−1]  →  predict day d   (expanding window, recency-weighted)
retrain models weekly (weekly.yml on an Actions runner) · refit calibrators monthly · features computed at as_of = prediction time
```
* **Leakage checklist** (tested in CI on a fixture season):
  * No same-game stats in features.
  * Rolling stats end at d−1, and opponent stats likewise.
  * Scalers and encoders are fit on train only.
  * Roster and team at as-of time.
  * Injuries and lineups as known at as_of.
  * Market inputs as of prediction time; closing lines are used **only** for grading and CLV.
* **Two backtest modes, always labeled:**
  * `lineup_oracle`: uses the actual dressed lineup and deployment, because historical *projected* lineups don't exist before we start recording them. This is optimistic and shown with a warning.
  * `live_tracked`: uses only what RinkX recorded in real time. This is the honest number, and it accumulates from launch day.
* **Odds availability:** ROI and CLV are computed only where historical odds exist (vendor history or our own snapshots). Otherwise the backtest reports probability metrics only.

**Metrics** (by market, by confidence bucket, by probability bucket, and over time):

| Kind | Metrics |
|---|---|
| Distribution | Log score, ranked probability score (RPS), PIT histogram |
| Binary at line | Brier score, log loss, ECE, reliability diagram |
| Point | MAE, RMSE of the mean |
| Betting (published picks, 1u flat) | Count, hit rate, ROI, average CLV, % beating the close, cumulative P&L with max drawdown, bootstrap CI on ROI |
| Baselines | Market no-vig probability (the bar to beat), season-average Poisson, L10-average Poisson |

**Calibration.** Isotonic regression per market (Platt scaling for low-sample markets), fit on out-of-fold walk-forward predictions. It is applied only if it improves held-out Brier, and the calibration map is stored in `model_versions.calibrator`.

**Promotion gate** (champion/challenger): a challenger needs ≥ 2,000 out-of-sample predictions for the market, a lower log loss with a paired bootstrap p < 0.05, an ECE no worse than the champion's, and no confidence-bucket inversion. Promotion is a manual action (a "Promote model" issue form) with the report attached. Model artifacts are stored as Release assets and referenced by `model_versions.artifact_uri`.

**Retraining schedule.** Weekly feature and model refresh, monthly calibrator refresh, and a full re-tune each off-season. Drift monitors (feature PSI, rolling Brier vs. backtest) raise admin alerts.

**Losing periods are visible.** The performance dashboard always shows the full cumulative P&L chart and monthly table, including negative months. There is no date filter that hides them by default.
