-- =============================================================================
-- RinkX data store: SQLite (>= 3.37, STRICT tables)
--
-- RinkX runs on GitHub Actions + GitHub Pages, with no database server. The
-- system of record is one SQLite file. Each workflow run downloads it
-- (encrypted) from a GitHub Release, updates it, and uploads a new version.
-- See docs/01-architecture.md.
--
-- Every connection MUST run:  PRAGMA foreign_keys = ON;
--
-- Conventions
--   * Timestamps are TEXT in ISO-8601 UTC ("2026-10-10T23:00:00Z").
--     Game dates are TEXT "YYYY-MM-DD", the date the NHL lists the game under.
--   * Booleans are INTEGER 0/1. JSON is TEXT validated by json_valid().
--   * Every row from the outside world carries provenance:
--       source_id  -> data_sources.id   where it came from
--       source_ref -> URL / external id  how to find it again
--       fetched_at -> when WE observed it
--       provenance -> official|licensed|community|reported|manual|derived|synthetic
--       quality    -> 0..1 score assigned by the ingestion layer
--   * provenance='synthetic' is the ONLY way mock data may enter. The publish
--     step refuses to build the production site if any synthetic row is present.
--   * Prop types are rows in `markets`, so new props are an INSERT, not a migration.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- Provenance & operations
-- -----------------------------------------------------------------------------
CREATE TABLE data_sources (
  id              INTEGER PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,              -- 'nhl_web_api', 'the_odds_api', 'quick_entry', ...
  name            TEXT NOT NULL,
  category        TEXT NOT NULL,                     -- schedule|stats|pbp|odds|lineups|injuries|news
  tier            TEXT NOT NULL CHECK (tier IN ('free','paid','optional')),
  base_url        TEXT,
  license_notes   TEXT NOT NULL,
  default_quality REAL NOT NULL DEFAULT 0.80 CHECK (default_quality BETWEEN 0 AND 1),
  is_enabled      INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;

CREATE TABLE ingestion_runs (
  id              INTEGER PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES data_sources(id),
  job_name        TEXT NOT NULL,
  workflow_run_id TEXT,                              -- GitHub Actions run id, for log lookup
  status          TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','succeeded','failed','partial','skipped')),
  started_at      TEXT,
  finished_at     TEXT,
  rows_read       INTEGER,
  rows_upserted   INTEGER,
  http_calls      INTEGER,
  quota_remaining INTEGER,                           -- metered APIs (odds)
  error           TEXT,
  meta            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta))
) STRICT;
CREATE INDEX ix_ingestion_runs_job_time ON ingestion_runs (job_name, started_at DESC);
CREATE INDEX ix_ingestion_runs_failed ON ingestion_runs (started_at DESC) WHERE status = 'failed';

CREATE TABLE data_quality_issues (
  id           INTEGER PRIMARY KEY,
  entity_type  TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  issue_code   TEXT NOT NULL,                        -- 'missing_toi','stale_odds','conflicting_goalie',...
  severity     TEXT NOT NULL CHECK (severity IN ('info','warn','error')),
  detail       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  detected_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  resolved_at  TEXT
) STRICT;
CREATE INDEX ix_dq_open ON data_quality_issues (entity_type, entity_id) WHERE resolved_at IS NULL;

-- -----------------------------------------------------------------------------
-- League structure
-- -----------------------------------------------------------------------------
CREATE TABLE seasons (
  id          INTEGER PRIMARY KEY,                   -- NHL format, e.g. 20262027
  start_date  TEXT NOT NULL,
  end_date    TEXT NOT NULL
) STRICT;

CREATE TABLE teams (
  id            INTEGER PRIMARY KEY,
  nhl_team_id   INTEGER NOT NULL UNIQUE,
  abbrev        TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  location      TEXT NOT NULL,
  conference    TEXT,
  division      TEXT,
  venue_name    TEXT,
  venue_lat     REAL,
  venue_lon     REAL,
  timezone      TEXT,
  is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  source_id     INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at    TEXT NOT NULL
) STRICT;

CREATE TABLE players (
  id              INTEGER PRIMARY KEY,
  nhl_player_id   INTEGER NOT NULL UNIQUE,
  first_name      TEXT NOT NULL,
  last_name       TEXT NOT NULL,
  full_name       TEXT GENERATED ALWAYS AS (first_name || ' ' || last_name) STORED,
  position        TEXT NOT NULL CHECK (position IN ('C','L','R','D','G')),
  shoots_catches  TEXT CHECK (shoots_catches IN ('L','R')),
  birth_date      TEXT,
  height_cm       INTEGER,
  weight_kg       INTEGER,
  current_team_id INTEGER REFERENCES teams(id),
  sweater_number  INTEGER,
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  source_id       INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at      TEXT NOT NULL
) STRICT;
CREATE INDEX ix_players_team ON players (current_team_id) WHERE is_active = 1;
CREATE INDEX ix_players_name ON players (last_name COLLATE NOCASE, first_name COLLATE NOCASE);

-- Vendor name -> player mapping (odds feeds key on names, not NHL ids).
CREATE TABLE player_aliases (
  source_id   INTEGER NOT NULL REFERENCES data_sources(id),
  alias       TEXT NOT NULL COLLATE NOCASE,
  player_id   INTEGER NOT NULL REFERENCES players(id),
  method      TEXT NOT NULL CHECK (method IN ('exact','curated','fuzzy_reviewed')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (source_id, alias)
) STRICT;

CREATE TABLE player_team_stints (
  player_id   INTEGER NOT NULL REFERENCES players(id),
  team_id     INTEGER NOT NULL REFERENCES teams(id),
  start_date  TEXT NOT NULL,
  end_date    TEXT,
  source_id   INTEGER NOT NULL REFERENCES data_sources(id),
  PRIMARY KEY (player_id, start_date)
) STRICT;

CREATE TABLE games (
  id               INTEGER PRIMARY KEY,
  nhl_game_id      INTEGER NOT NULL UNIQUE,
  season_id        INTEGER NOT NULL REFERENCES seasons(id),
  game_type        TEXT NOT NULL CHECK (game_type IN ('P','R','O')),  -- preseason, regular, playoffs
  game_date        TEXT NOT NULL,
  start_time_utc   TEXT NOT NULL,
  home_team_id     INTEGER NOT NULL REFERENCES teams(id),
  away_team_id     INTEGER NOT NULL REFERENCES teams(id),
  venue_name       TEXT,
  is_neutral_site  INTEGER NOT NULL DEFAULT 0 CHECK (is_neutral_site IN (0,1)),
  status           TEXT NOT NULL DEFAULT 'scheduled'
                   CHECK (status IN ('scheduled','pregame','live','final','postponed','cancelled')),
  period           INTEGER,
  home_score       INTEGER,
  away_score       INTEGER,
  ended_in         TEXT CHECK (ended_in IN ('REG','OT','SO')),
  source_id        INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at       TEXT NOT NULL,
  CHECK (home_team_id <> away_team_id)
) STRICT;
CREATE INDEX ix_games_date ON games (game_date);
CREATE INDEX ix_games_home_date ON games (home_team_id, game_date);
CREATE INDEX ix_games_away_date ON games (away_team_id, game_date);

-- Per-team, per-game schedule context (derived): rest, B2B, travel.
CREATE TABLE game_team_context (
  game_id          INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id          INTEGER NOT NULL REFERENCES teams(id),
  is_home          INTEGER NOT NULL CHECK (is_home IN (0,1)),
  rest_days        INTEGER,                          -- NULL = first game of season
  is_back_to_back  INTEGER NOT NULL CHECK (is_back_to_back IN (0,1)),
  games_last_7d    INTEGER NOT NULL,
  travel_km        REAL,
  tz_shift_hours   INTEGER,
  computed_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (game_id, team_id)
) STRICT;

-- -----------------------------------------------------------------------------
-- Box-score statistics
-- -----------------------------------------------------------------------------
CREATE TABLE player_game_stats (
  player_id            INTEGER NOT NULL REFERENCES players(id),
  game_id              INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id              INTEGER NOT NULL REFERENCES teams(id),
  opponent_team_id     INTEGER NOT NULL REFERENCES teams(id),
  is_home              INTEGER NOT NULL CHECK (is_home IN (0,1)),
  toi_s                INTEGER,
  ev_toi_s             INTEGER,
  pp_toi_s             INTEGER,
  sh_toi_s             INTEGER,
  shifts               INTEGER,
  goals                INTEGER,
  assists              INTEGER,
  primary_assists      INTEGER,
  secondary_assists    INTEGER,
  points               INTEGER GENERATED ALWAYS AS (goals + assists) STORED,
  pp_goals             INTEGER,
  pp_assists           INTEGER,
  pp_points            INTEGER GENERATED ALWAYS AS (pp_goals + pp_assists) STORED,
  sh_goals             INTEGER,
  gw_goals             INTEGER,
  shots                INTEGER,                      -- shots on goal
  shot_attempts        INTEGER,                      -- iCF
  missed_shots         INTEGER,
  shots_blocked_by_opp INTEGER,
  ixg                  REAL,
  hits                 INTEGER,
  blocked_shots        INTEGER,                      -- blocks MADE
  takeaways            INTEGER,
  giveaways            INTEGER,
  faceoff_wins         INTEGER,
  faceoff_losses       INTEGER,
  pim                  INTEGER,
  plus_minus           INTEGER,
  extra                TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extra)),
  provenance           TEXT NOT NULL CHECK (provenance IN
                         ('official','licensed','community','reported','manual','derived','synthetic')),
  quality              REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
  source_id            INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at           TEXT NOT NULL,
  PRIMARY KEY (player_id, game_id)
) STRICT;
CREATE INDEX ix_pgs_game ON player_game_stats (game_id);
CREATE INDEX ix_pgs_opp ON player_game_stats (opponent_team_id);

CREATE TABLE goalie_game_stats (
  player_id            INTEGER NOT NULL REFERENCES players(id),
  game_id              INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id              INTEGER NOT NULL REFERENCES teams(id),
  opponent_team_id     INTEGER NOT NULL REFERENCES teams(id),
  is_home              INTEGER NOT NULL CHECK (is_home IN (0,1)),
  started              INTEGER NOT NULL CHECK (started IN (0,1)),
  pulled               INTEGER NOT NULL DEFAULT 0 CHECK (pulled IN (0,1)),
  toi_s                INTEGER,
  shots_against        INTEGER,
  saves                INTEGER,
  goals_against        INTEGER,
  ev_shots_against     INTEGER,
  ev_saves             INTEGER,
  pp_shots_against     INTEGER,
  pp_saves             INTEGER,
  sh_shots_against     INTEGER,
  sh_saves             INTEGER,
  hd_shots_against     INTEGER,
  hd_saves             INTEGER,
  xga                  REAL,
  decision             TEXT CHECK (decision IN ('W','L','O')),   -- O = OT/SO loss
  shutout              INTEGER CHECK (shutout IN (0,1)),
  extra                TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extra)),
  provenance           TEXT NOT NULL CHECK (provenance IN
                         ('official','licensed','community','reported','manual','derived','synthetic')),
  quality              REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
  source_id            INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at           TEXT NOT NULL,
  PRIMARY KEY (player_id, game_id)
) STRICT;
CREATE INDEX ix_ggs_game ON goalie_game_stats (game_id);

CREATE TABLE team_game_stats (
  team_id              INTEGER NOT NULL REFERENCES teams(id),
  game_id              INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  opponent_team_id     INTEGER NOT NULL REFERENCES teams(id),
  is_home              INTEGER NOT NULL CHECK (is_home IN (0,1)),
  goals_for            INTEGER,
  shots_for            INTEGER,
  shot_attempts_for    INTEGER,
  ev_shot_attempts_for INTEGER,
  xgf                  REAL,
  pp_opportunities     INTEGER,
  pp_goals             INTEGER,
  times_shorthanded    INTEGER,
  pp_goals_against     INTEGER,
  pim                  INTEGER,
  hits                 INTEGER,
  blocked_shots        INTEGER,
  pp_toi_s             INTEGER,
  sh_toi_s             INTEGER,
  extra                TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extra)),
  provenance           TEXT NOT NULL CHECK (provenance IN
                         ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id            INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at           TEXT NOT NULL,
  PRIMARY KEY (team_id, game_id)
) STRICT;

CREATE TABLE pbp_shot_events (
  id               INTEGER PRIMARY KEY,
  game_id          INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  event_idx        INTEGER NOT NULL,
  period           INTEGER NOT NULL,
  period_seconds   INTEGER NOT NULL,
  event_type       TEXT NOT NULL CHECK (event_type IN ('shot','miss','block','goal')),
  shooter_id       INTEGER REFERENCES players(id),
  goalie_id        INTEGER REFERENCES players(id),
  blocker_id       INTEGER REFERENCES players(id),
  team_id          INTEGER NOT NULL REFERENCES teams(id),
  strength_state   TEXT,
  x_coord          INTEGER,
  y_coord          INTEGER,
  shot_type        TEXT,
  is_rebound       INTEGER CHECK (is_rebound IN (0,1)),
  is_rush          INTEGER CHECK (is_rush IN (0,1)),
  xg               REAL,
  xg_model_version TEXT,
  source_id        INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at       TEXT NOT NULL,
  UNIQUE (game_id, event_idx)
) STRICT;
CREATE INDEX ix_pbp_shooter ON pbp_shot_events (shooter_id);
CREATE INDEX ix_pbp_goalie ON pbp_shot_events (goalie_id);

-- -----------------------------------------------------------------------------
-- Availability, lineups, roles
-- -----------------------------------------------------------------------------
CREATE TABLE injuries (
  id               INTEGER PRIMARY KEY,
  player_id        INTEGER NOT NULL REFERENCES players(id),
  team_id          INTEGER REFERENCES teams(id),
  status           TEXT NOT NULL CHECK (status IN (
                     'available','day_to_day','questionable','out','injured_reserve','long_term_ir',
                     'suspended','healthy_scratch','rest','personal','unknown')),
  body_part        TEXT,
  description      TEXT,
  expected_return  TEXT,
  reported_at      TEXT NOT NULL,
  resolved_at      TEXT,
  is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  provenance       TEXT NOT NULL CHECK (provenance IN
                     ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id        INTEGER NOT NULL REFERENCES data_sources(id),
  source_ref       TEXT NOT NULL CHECK (length(source_ref) > 0),   -- no source, no row
  fetched_at       TEXT NOT NULL
) STRICT;
CREATE INDEX ix_injuries_active ON injuries (player_id) WHERE is_active = 1;

-- One observation of a team's deployment at a point in time. Diffing
-- consecutive snapshots is how role changes (PP2 -> PP1) are detected.
CREATE TABLE lineup_snapshots (
  id            INTEGER PRIMARY KEY,
  team_id       INTEGER NOT NULL REFERENCES teams(id),
  game_id       INTEGER REFERENCES games(id) ON DELETE CASCADE,
  status        TEXT NOT NULL CHECK (status IN ('projected','likely','confirmed','actual')),
  observed_at   TEXT NOT NULL,
  provenance    TEXT NOT NULL CHECK (provenance IN
                  ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id     INTEGER NOT NULL REFERENCES data_sources(id),
  source_ref    TEXT,
  fetched_at    TEXT NOT NULL
) STRICT;
CREATE INDEX ix_lineup_snap ON lineup_snapshots (team_id, game_id, observed_at DESC);

CREATE TABLE line_combinations (
  snapshot_id   INTEGER NOT NULL REFERENCES lineup_snapshots(id) ON DELETE CASCADE,
  unit          TEXT NOT NULL CHECK (unit IN ('F1','F2','F3','F4','D1','D2','D3','G','EXTRA','SCRATCH')),
  slot          TEXT NOT NULL CHECK (slot IN ('LW','C','RW','LD','RD','G1','G2','X')),
  player_id     INTEGER NOT NULL REFERENCES players(id),
  PRIMARY KEY (snapshot_id, player_id)
) STRICT;

CREATE TABLE powerplay_units (
  snapshot_id   INTEGER NOT NULL REFERENCES lineup_snapshots(id) ON DELETE CASCADE,
  unit          TEXT NOT NULL CHECK (unit IN ('PP1','PP2','PK1','PK2')),
  slot          INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 5),
  player_id     INTEGER NOT NULL REFERENCES players(id),
  PRIMARY KEY (snapshot_id, unit, player_id)
) STRICT;

CREATE TABLE goalie_starts (
  id            INTEGER PRIMARY KEY,
  game_id       INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id       INTEGER NOT NULL REFERENCES teams(id),
  player_id     INTEGER NOT NULL REFERENCES players(id),
  status        TEXT NOT NULL CHECK (status IN ('projected','likely','confirmed','actual')),
  reported_at   TEXT NOT NULL,
  provenance    TEXT NOT NULL CHECK (provenance IN
                  ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id     INTEGER NOT NULL REFERENCES data_sources(id),
  source_ref    TEXT,
  fetched_at    TEXT NOT NULL
) STRICT;
CREATE INDEX ix_goalie_starts_game ON goalie_starts (game_id, team_id, reported_at DESC);

-- -----------------------------------------------------------------------------
-- Markets & odds
-- -----------------------------------------------------------------------------
CREATE TABLE markets (
  id               INTEGER PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL,
  subject          TEXT NOT NULL CHECK (subject IN ('skater','goalie','team','game')),
  kind             TEXT NOT NULL CHECK (kind IN ('over_under','yes_no','moneyline','spread')),
  stat_expr        TEXT NOT NULL,
  settlement_rule  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settlement_rule)),
  model_family     TEXT,                             -- NULL = lines shown, projection "Not modeled"
  is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
) STRICT;

CREATE TABLE sportsbooks (
  id              INTEGER PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,
  name            TEXT NOT NULL,
  regions         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(regions)),
  is_market_maker INTEGER NOT NULL DEFAULT 0 CHECK (is_market_maker IN (0,1)),
  is_enabled      INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1))  -- only books you use
) STRICT;

CREATE TABLE prop_lines (
  id               INTEGER PRIMARY KEY,
  game_id          INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  market_id        INTEGER NOT NULL REFERENCES markets(id),
  sportsbook_id    INTEGER NOT NULL REFERENCES sportsbooks(id),
  player_id        INTEGER REFERENCES players(id),
  team_id          INTEGER REFERENCES teams(id),
  line             REAL,                             -- NULL for yes/no & moneyline
  over_price       INTEGER CHECK (over_price IS NULL OR over_price <= -100 OR over_price >= 100),
  under_price      INTEGER CHECK (under_price IS NULL OR under_price <= -100 OR under_price >= 100),
  is_main_line     INTEGER NOT NULL DEFAULT 1 CHECK (is_main_line IN (0,1)),
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','suspended','closed','removed')),
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  last_changed_at  TEXT NOT NULL,
  provenance       TEXT NOT NULL CHECK (provenance IN
                     ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id        INTEGER NOT NULL REFERENCES data_sources(id),
  source_ref       TEXT
) STRICT;
CREATE UNIQUE INDEX ux_prop_lines_key ON prop_lines
  (game_id, market_id, sportsbook_id, coalesce(player_id, 0), coalesce(team_id, 0), coalesce(line, -1));
CREATE INDEX ix_prop_lines_player ON prop_lines (player_id, game_id);

-- Append-only; written only when line/price/status changed.
CREATE TABLE line_movements (
  prop_line_id   INTEGER NOT NULL REFERENCES prop_lines(id) ON DELETE CASCADE,
  observed_at    TEXT NOT NULL,
  line           REAL,
  over_price     INTEGER,
  under_price    INTEGER,
  status         TEXT NOT NULL CHECK (status IN ('open','suspended','closed','removed')),
  source_id      INTEGER NOT NULL REFERENCES data_sources(id),
  PRIMARY KEY (prop_line_id, observed_at)
) STRICT, WITHOUT ROWID;

CREATE TABLE historical_odds (
  id             INTEGER PRIMARY KEY,
  game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  market_id      INTEGER NOT NULL REFERENCES markets(id),
  sportsbook_id  INTEGER NOT NULL REFERENCES sportsbooks(id),
  player_id      INTEGER REFERENCES players(id),
  team_id        INTEGER REFERENCES teams(id),
  line           REAL,
  over_price     INTEGER,
  under_price    INTEGER,
  snapshot_kind  TEXT NOT NULL CHECK (snapshot_kind IN ('open','t_minus_6h','t_minus_1h','close','other')),
  captured_at    TEXT NOT NULL,
  provenance     TEXT NOT NULL CHECK (provenance IN
                   ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id      INTEGER NOT NULL REFERENCES data_sources(id)
) STRICT;
CREATE INDEX ix_hist_odds_lookup ON historical_odds (game_id, market_id, player_id, snapshot_kind);

-- -----------------------------------------------------------------------------
-- Models, projections, predictions, grading
-- -----------------------------------------------------------------------------
CREATE TABLE model_versions (
  id              INTEGER PRIMARY KEY,
  model_family    TEXT NOT NULL,
  version         TEXT NOT NULL,
  algorithm       TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'candidate'
                  CHECK (status IN ('candidate','champion','challenger','retired')),
  train_start     TEXT,
  train_end       TEXT,
  feature_list    TEXT NOT NULL CHECK (json_valid(feature_list)),
  hyperparams     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(hyperparams)),
  calibrator      TEXT CHECK (calibrator IS NULL OR json_valid(calibrator)),
  oos_metrics     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(oos_metrics)),
  artifact_uri    TEXT,                              -- release asset name
  git_sha         TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  promoted_at     TEXT,
  UNIQUE (model_family, version)
) STRICT;
CREATE UNIQUE INDEX ux_one_champion ON model_versions (model_family) WHERE status = 'champion';

-- Full predictive distribution for (player, game, stat). Immutable: a
-- recalculation clears is_current on the old row and inserts a new row that
-- `supersedes` it, in one transaction. That chain powers "Projection updated".
CREATE TABLE player_projections (
  id                  INTEGER PRIMARY KEY,
  game_id             INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id           INTEGER NOT NULL REFERENCES players(id),
  market_id           INTEGER NOT NULL REFERENCES markets(id),
  model_version_id    INTEGER NOT NULL REFERENCES model_versions(id),
  computed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  as_of               TEXT NOT NULL,                 -- latest input timestamp used (leakage guard)
  mean                REAL NOT NULL,
  median              REAL,
  std_dev             REAL,
  pmf                 TEXT NOT NULL CHECK (json_valid(pmf)),
  expected_toi_s      INTEGER,
  expected_pp_toi_s   INTEGER,
  inputs              TEXT NOT NULL CHECK (json_valid(inputs)),
  factors_for         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(factors_for)),
  factors_against     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(factors_against)),
  data_quality        REAL NOT NULL CHECK (data_quality BETWEEN 0 AND 1),
  missing_inputs      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(missing_inputs)),
  trigger_reason      TEXT NOT NULL,                 -- scheduled|lineup_change|goalie_confirmed|injury|line_move
  supersedes          INTEGER REFERENCES player_projections(id),
  is_current          INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1)),
  provenance          TEXT NOT NULL DEFAULT 'derived' CHECK (provenance IN ('derived','synthetic'))
) STRICT;
CREATE UNIQUE INDEX ux_proj_current ON player_projections (game_id, player_id, market_id) WHERE is_current = 1;
CREATE INDEX ix_proj_player ON player_projections (player_id, computed_at DESC);

-- A projection priced against one line at one moment. Immutable; this is
-- what backtesting and ROI are computed from. There is no delete path.
CREATE TABLE predictions (
  id                 INTEGER PRIMARY KEY,
  projection_id      INTEGER NOT NULL REFERENCES player_projections(id),
  prop_line_id       INTEGER REFERENCES prop_lines(id) ON DELETE SET NULL,
  game_id            INTEGER NOT NULL REFERENCES games(id),
  market_id          INTEGER NOT NULL REFERENCES markets(id),
  sportsbook_id      INTEGER REFERENCES sportsbooks(id),   -- NULL = consensus
  player_id          INTEGER REFERENCES players(id),
  line               REAL,
  over_price         INTEGER,
  under_price        INTEGER,
  p_model_over       REAL NOT NULL CHECK (p_model_over BETWEEN 0 AND 1),
  p_model_under      REAL NOT NULL CHECK (p_model_under BETWEEN 0 AND 1),
  p_push             REAL NOT NULL DEFAULT 0 CHECK (p_push BETWEEN 0 AND 1),
  p_implied_over     REAL,
  p_implied_under    REAL,
  p_novig_over       REAL,                           -- NULL if vig not removable (one-sided)
  p_novig_under      REAL,
  devig_method       TEXT,
  edge_over          REAL,
  edge_under         REAL,
  ev_over            REAL,
  ev_under           REAL,
  side               TEXT NOT NULL CHECK (side IN ('over','under','yes','no','home','away','none')),
  confidence         INTEGER CHECK (confidence BETWEEN 0 AND 100),
  confidence_parts   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(confidence_parts)),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  is_published       INTEGER NOT NULL DEFAULT 0 CHECK (is_published IN (0,1)),
  CHECK (abs(p_model_over + p_model_under - 1) <= 0.001)
) STRICT;
CREATE INDEX ix_pred_game ON predictions (game_id, market_id);
CREATE INDEX ix_pred_published ON predictions (created_at) WHERE is_published = 1;

CREATE TRIGGER trg_predictions_immutable BEFORE UPDATE ON predictions
WHEN OLD.is_published = 1
BEGIN
  SELECT RAISE(ABORT, 'published predictions are immutable');
END;
CREATE TRIGGER trg_predictions_no_delete BEFORE DELETE ON predictions
WHEN OLD.is_published = 1
BEGIN
  SELECT RAISE(ABORT, 'published predictions cannot be deleted');
END;

CREATE TABLE model_results (
  prediction_id        INTEGER PRIMARY KEY REFERENCES predictions(id),
  actual_value         REAL,
  outcome              TEXT NOT NULL CHECK (outcome IN ('win','loss','push','void','pending')),
  closing_line         REAL,
  closing_over_price   INTEGER,
  closing_under_price  INTEGER,
  closing_novig_p      REAL,
  clv                  REAL,
  profit_units         REAL,
  graded_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;

CREATE TABLE backtest_runs (
  id               INTEGER PRIMARY KEY,
  model_version_id INTEGER NOT NULL REFERENCES model_versions(id),
  scheme           TEXT NOT NULL,
  lineup_mode      TEXT NOT NULL CHECK (lineup_mode IN ('live_tracked','lineup_oracle')),
  period_start     TEXT NOT NULL,
  period_end       TEXT NOT NULL,
  config           TEXT NOT NULL CHECK (json_valid(config)),
  metrics          TEXT NOT NULL CHECK (json_valid(metrics)),
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;

CREATE TABLE prop_correlations (
  id              INTEGER PRIMARY KEY,
  market_a_id     INTEGER NOT NULL REFERENCES markets(id),
  market_b_id     INTEGER NOT NULL REFERENCES markets(id),
  relation        TEXT NOT NULL,                     -- same_player|linemate|same_team|opponent|opp_goalie
  position_group  TEXT,
  line_a          REAL,
  line_b          REAL,
  coefficient     REAL NOT NULL CHECK (coefficient BETWEEN -1 AND 1),
  ci_low          REAL,
  ci_high         REAL,
  n_obs           INTEGER NOT NULL,
  method          TEXT NOT NULL,                     -- phi|tetrachoric|residual_pearson|sim
  window_start    TEXT NOT NULL,
  window_end      TEXT NOT NULL,
  computed_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;
CREATE INDEX ix_corr_lookup ON prop_correlations (market_a_id, market_b_id, relation);

-- -----------------------------------------------------------------------------
-- News & alerts (single owner: no users table)
-- -----------------------------------------------------------------------------
CREATE TABLE news (
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER NOT NULL REFERENCES data_sources(id),
  external_id    TEXT,
  url            TEXT NOT NULL CHECK (length(url) > 0),   -- no URL, no item
  headline       TEXT NOT NULL,
  summary        TEXT,
  category       TEXT NOT NULL CHECK (category IN
                   ('injury','lineup','goalie','scratch','suspension','coach','rest','transaction','general')),
  reliability    TEXT NOT NULL CHECK (reliability IN ('official','beat_reporter','aggregator','unverified')),
  published_at   TEXT NOT NULL,
  fetched_at     TEXT NOT NULL,
  provenance     TEXT NOT NULL CHECK (provenance IN
                   ('official','licensed','community','reported','manual','derived','synthetic')),
  UNIQUE (source_id, external_id)
) STRICT;
CREATE INDEX ix_news_published ON news (published_at DESC);

CREATE TABLE news_entities (
  news_id    INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
  player_id  INTEGER REFERENCES players(id),
  team_id    INTEGER REFERENCES teams(id),
  CHECK (player_id IS NOT NULL OR team_id IS NOT NULL)
) STRICT;
CREATE INDEX ix_news_entities_player ON news_entities (player_id);
CREATE INDEX ix_news_entities_team ON news_entities (team_id);

-- Alert definitions are authored in config/alerts.yml and synced here each run.
CREATE TABLE alerts (
  id                 INTEGER PRIMARY KEY,
  key                TEXT NOT NULL UNIQUE,           -- stable id from alerts.yml
  alert_type         TEXT NOT NULL CHECK (alert_type IN
                       ('line_threshold','pp_unit','goalie_confirmed','edge_threshold','injury_news','line_move')),
  player_id          INTEGER REFERENCES players(id),
  team_id            INTEGER REFERENCES teams(id),
  market_id          INTEGER REFERENCES markets(id),
  sportsbook_id      INTEGER REFERENCES sportsbooks(id),
  condition          TEXT NOT NULL CHECK (json_valid(condition)),
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  fire_once_per_game INTEGER NOT NULL DEFAULT 1 CHECK (fire_once_per_game IN (0,1)),
  last_triggered_at  TEXT
) STRICT;

CREATE TABLE alert_events (
  id               INTEGER PRIMARY KEY,
  alert_id         INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  game_id          INTEGER REFERENCES games(id) ON DELETE CASCADE,
  triggered_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  payload          TEXT NOT NULL CHECK (json_valid(payload)),
  delivery_status  TEXT NOT NULL DEFAULT 'pending' CHECK (delivery_status IN ('pending','sent','failed')),
  delivered_at     TEXT
) STRICT;
CREATE UNIQUE INDEX ux_alert_once_per_game ON alert_events (alert_id, game_id);

-- -----------------------------------------------------------------------------
-- Seed: market catalogue
-- -----------------------------------------------------------------------------
INSERT INTO markets (code, name, subject, kind, stat_expr, model_family) VALUES
  ('skater_shots_on_goal',   'Shots on Goal',        'skater', 'over_under', 'shots',                    'skater_shots'),
  ('skater_goals',           'Goals',                'skater', 'over_under', 'goals',                    'skater_scoring'),
  ('skater_assists',         'Assists',              'skater', 'over_under', 'assists',                  'skater_scoring'),
  ('skater_points',          'Points',               'skater', 'over_under', 'goals+assists',            'skater_scoring'),
  ('skater_pp_points',       'Power-play Points',    'skater', 'over_under', 'pp_goals+pp_assists',      'skater_scoring'),
  ('skater_pp_goal',         'Power-play Goal',      'skater', 'yes_no',     'pp_goals>=1',              'skater_scoring'),
  ('skater_pp_assist',       'Power-play Assist',    'skater', 'yes_no',     'pp_assists>=1',            'skater_scoring'),
  ('skater_blocked_shots',   'Blocked Shots',        'skater', 'over_under', 'blocked_shots',            'skater_blocks'),
  ('skater_hits',            'Hits',                 'skater', 'over_under', 'hits',                     'skater_hits'),
  ('skater_anytime_goal',    'Anytime Goal',         'skater', 'yes_no',     'goals>=1',                 'skater_scoring'),
  ('skater_first_goal',      'First Goal',           'skater', 'yes_no',     'first_goal_of_game',       'game_sim'),
  ('goalie_saves',           'Saves',                'goalie', 'over_under', 'saves',                    'goalie'),
  ('goalie_goals_against',   'Goals Against',        'goalie', 'over_under', 'goals_against',            'goalie'),
  ('goalie_shutout',         'Shutout',              'goalie', 'yes_no',     'shutout',                  'goalie'),
  ('goalie_win',             'Goalie Win',           'goalie', 'yes_no',     'decision=W',               'game_sim'),
  ('goalie_saves_and_win',   'Saves + Win',          'goalie', 'yes_no',     'saves>=line & decision=W', 'game_sim'),
  ('game_moneyline',         'Moneyline',            'game',   'moneyline',  'winner',                   'game_sim'),
  ('game_total',             'Game Total Goals',     'game',   'over_under', 'home_score+away_score',    'game_sim'),
  ('team_total',             'Team Total Goals',     'team',   'over_under', 'team_goals',               'game_sim');
