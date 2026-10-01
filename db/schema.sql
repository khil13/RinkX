-- =============================================================================
-- RinkX — PostgreSQL 16 reference schema (design baseline, pre-Alembic)
--
-- Conventions
--   * Every row that came from the outside world carries provenance:
--       source_id   -> data_sources.id   (where it came from)
--       source_ref  -> external id / URL (how to find it again)
--       fetched_at  -> when WE observed it
--       provenance  -> what kind of data it is (official / licensed / derived / synthetic ...)
--       quality     -> 0.00–1.00 quality score assigned by the ingestion layer
--   * 'synthetic' provenance is the ONLY way mock/dev data may enter the DB.
--     The API refuses to serve synthetic rows unless the environment allows it,
--     and the UI always labels them.
--   * All timestamps are timestamptz (UTC). Game dates are stored as the local
--     calendar date the NHL schedules them under.
--   * Prop types are rows in `markets`, not enum values, so new props can be
--     added with an INSERT rather than a migration.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS citext;

-- -----------------------------------------------------------------------------
-- Enums (only for small, genuinely closed sets)
-- -----------------------------------------------------------------------------
CREATE TYPE provenance_kind AS ENUM (
  'official',    -- league / team primary source
  'licensed',    -- paid feed under contract
  'community',   -- free third-party dataset used under its license (e.g. MoneyPuck)
  'reported',    -- news/beat-reporter claim, not yet official
  'manual',      -- entered by an admin with a mandatory source URL
  'derived',     -- computed by RinkX from other rows
  'synthetic'    -- development/mock data; must never be shown unlabeled
);

CREATE TYPE game_status AS ENUM ('scheduled','pregame','live','final','postponed','cancelled');
CREATE TYPE lineup_status AS ENUM ('projected','likely','confirmed','actual');
CREATE TYPE availability_status AS ENUM (
  'available','day_to_day','questionable','out','injured_reserve','long_term_ir',
  'suspended','healthy_scratch','rest','personal','unknown'
);
CREATE TYPE subject_type AS ENUM ('skater','goalie','team','game');
CREATE TYPE market_kind  AS ENUM ('over_under','yes_no','moneyline','spread');
CREATE TYPE line_status  AS ENUM ('open','suspended','closed','removed');
CREATE TYPE bet_side     AS ENUM ('over','under','yes','no','home','away','none');
CREATE TYPE grade_outcome AS ENUM ('win','loss','push','void','pending');
CREATE TYPE model_status AS ENUM ('candidate','champion','challenger','retired');
CREATE TYPE job_status   AS ENUM ('queued','running','succeeded','failed','partial','skipped');
CREATE TYPE user_role    AS ENUM ('user','admin');

-- -----------------------------------------------------------------------------
-- Provenance & operations
-- -----------------------------------------------------------------------------
CREATE TABLE data_sources (
  id              smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code            text NOT NULL UNIQUE,              -- 'nhl_web_api', 'the_odds_api', ...
  name            text NOT NULL,
  category        text NOT NULL,                     -- schedule|stats|pbp|odds|lineups|injuries|news
  tier            text NOT NULL CHECK (tier IN ('free','paid','optional')),
  base_url        text,
  license_notes   text NOT NULL,                     -- what we may and may not do with it
  default_quality numeric(3,2) NOT NULL DEFAULT 0.80 CHECK (default_quality BETWEEN 0 AND 1),
  is_enabled      boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_runs (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id      smallint NOT NULL REFERENCES data_sources(id),
  job_name       text NOT NULL,
  status         job_status NOT NULL DEFAULT 'queued',
  started_at     timestamptz,
  finished_at    timestamptz,
  rows_read      integer,
  rows_upserted  integer,
  http_calls     integer,
  quota_remaining integer,                           -- for metered APIs (odds)
  error          text,
  meta           jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_ingestion_runs_job_time ON ingestion_runs (job_name, started_at DESC);
CREATE INDEX ix_ingestion_runs_failed ON ingestion_runs (started_at DESC) WHERE status = 'failed';

CREATE TABLE data_quality_issues (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_type  text NOT NULL,                        -- 'game','player_game_stats','prop_line',...
  entity_id    text NOT NULL,
  issue_code   text NOT NULL,                        -- 'missing_toi','stale_odds','conflicting_goalie'...
  severity     text NOT NULL CHECK (severity IN ('info','warn','error')),
  detail       jsonb NOT NULL DEFAULT '{}',
  detected_at  timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz
);
CREATE INDEX ix_dq_open ON data_quality_issues (entity_type, entity_id) WHERE resolved_at IS NULL;

-- -----------------------------------------------------------------------------
-- Users, alerts
-- -----------------------------------------------------------------------------
CREATE TABLE users (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email           citext NOT NULL UNIQUE,
  display_name    text,
  role            user_role NOT NULL DEFAULT 'user',
  auth_provider   text NOT NULL,                     -- 'email','google','apple'
  settings        jsonb NOT NULL DEFAULT '{}',       -- default books, odds format, region
  rg_settings     jsonb NOT NULL DEFAULT '{}',       -- responsible-gaming: reminders, cool-off until
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_login_at   timestamptz,
  disabled_at     timestamptz
);

-- -----------------------------------------------------------------------------
-- League structure
-- -----------------------------------------------------------------------------
CREATE TABLE seasons (
  id          integer PRIMARY KEY,                   -- NHL format, e.g. 20262027
  start_date  date NOT NULL,
  end_date    date NOT NULL
);

CREATE TABLE teams (
  id            smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nhl_team_id   integer NOT NULL UNIQUE,
  abbrev        text NOT NULL UNIQUE,
  name          text NOT NULL,
  location      text NOT NULL,
  conference    text,
  division      text,
  venue_name    text,
  venue_lat     numeric(9,6),                        -- for travel distance
  venue_lon     numeric(9,6),
  timezone      text,                                -- IANA tz
  is_active     boolean NOT NULL DEFAULT true,
  source_id     smallint NOT NULL REFERENCES data_sources(id),
  fetched_at    timestamptz NOT NULL
);

CREATE TABLE players (
  id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nhl_player_id   integer NOT NULL UNIQUE,
  first_name      text NOT NULL,
  last_name       text NOT NULL,
  full_name       text GENERATED ALWAYS AS (first_name || ' ' || last_name) STORED,
  position        char(1) NOT NULL CHECK (position IN ('C','L','R','D','G')),
  shoots_catches  char(1) CHECK (shoots_catches IN ('L','R')),
  birth_date      date,
  height_cm       smallint,
  weight_kg       smallint,
  current_team_id smallint REFERENCES teams(id),
  sweater_number  smallint,
  is_active       boolean NOT NULL DEFAULT true,
  source_id       smallint NOT NULL REFERENCES data_sources(id),
  fetched_at      timestamptz NOT NULL
);
CREATE INDEX ix_players_team ON players (current_team_id) WHERE is_active;
CREATE INDEX ix_players_name ON players (lower(last_name), lower(first_name));

-- Roster history (trades, call-ups) — needed for "current roster changes".
CREATE TABLE player_team_stints (
  player_id   integer NOT NULL REFERENCES players(id),
  team_id     smallint NOT NULL REFERENCES teams(id),
  start_date  date NOT NULL,
  end_date    date,
  source_id   smallint NOT NULL REFERENCES data_sources(id),
  PRIMARY KEY (player_id, start_date)
);

CREATE TABLE games (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nhl_game_id      bigint NOT NULL UNIQUE,
  season_id        integer NOT NULL REFERENCES seasons(id),
  game_type        char(1) NOT NULL CHECK (game_type IN ('P','R','O')),  -- preseason, regular, playoffs ('O')
  game_date        date NOT NULL,
  start_time_utc   timestamptz NOT NULL,
  home_team_id     smallint NOT NULL REFERENCES teams(id),
  away_team_id     smallint NOT NULL REFERENCES teams(id),
  venue_name       text,
  is_neutral_site  boolean NOT NULL DEFAULT false,
  status           game_status NOT NULL DEFAULT 'scheduled',
  period           smallint,
  home_score       smallint,
  away_score       smallint,
  ended_in         text CHECK (ended_in IN ('REG','OT','SO')),
  source_id        smallint NOT NULL REFERENCES data_sources(id),
  fetched_at       timestamptz NOT NULL,
  CHECK (home_team_id <> away_team_id)
);
CREATE INDEX ix_games_date ON games (game_date);
CREATE INDEX ix_games_team_date ON games (home_team_id, game_date);
CREATE INDEX ix_games_away_team_date ON games (away_team_id, game_date);

-- Per-team, per-game schedule context (derived): rest, B2B, travel.
CREATE TABLE game_team_context (
  game_id          bigint NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id          smallint NOT NULL REFERENCES teams(id),
  is_home          boolean NOT NULL,
  rest_days        smallint,                         -- NULL = first game of season
  is_back_to_back  boolean NOT NULL,
  games_last_7d    smallint NOT NULL,
  travel_km        numeric(7,1),                     -- since previous game
  tz_shift_hours   smallint,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (game_id, team_id)
);

-- -----------------------------------------------------------------------------
-- Box-score level statistics
-- -----------------------------------------------------------------------------
CREATE TABLE player_game_stats (
  player_id            integer  NOT NULL REFERENCES players(id),
  game_id              bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id              smallint NOT NULL REFERENCES teams(id),
  opponent_team_id     smallint NOT NULL REFERENCES teams(id),
  is_home              boolean  NOT NULL,
  -- ice time (seconds)
  toi_s                integer,
  ev_toi_s             integer,
  pp_toi_s             integer,
  sh_toi_s             integer,
  shifts               smallint,
  -- scoring
  goals                smallint,
  assists              smallint,
  primary_assists      smallint,
  secondary_assists    smallint,
  points               smallint GENERATED ALWAYS AS (goals + assists) STORED,
  pp_goals             smallint,
  pp_assists           smallint,
  pp_points            smallint GENERATED ALWAYS AS (pp_goals + pp_assists) STORED,
  sh_goals             smallint,
  gw_goals             smallint,
  -- shooting / volume
  shots                smallint,                     -- shots on goal
  shot_attempts        smallint,                     -- iCF: SOG + missed + blocked
  missed_shots         smallint,
  shots_blocked_by_opp smallint,
  ixg                  numeric(6,3),                 -- individual expected goals
  -- physical / defensive
  hits                 smallint,
  blocked_shots        smallint,                     -- blocks MADE by this player
  takeaways            smallint,
  giveaways            smallint,
  faceoff_wins         smallint,
  faceoff_losses       smallint,
  pim                  smallint,
  plus_minus           smallint,
  -- extensibility for stats we add later without a migration
  extra                jsonb NOT NULL DEFAULT '{}',
  provenance           provenance_kind NOT NULL,
  quality              numeric(3,2) NOT NULL CHECK (quality BETWEEN 0 AND 1),
  source_id            smallint NOT NULL REFERENCES data_sources(id),
  fetched_at           timestamptz NOT NULL,
  PRIMARY KEY (player_id, game_id)
);
CREATE INDEX ix_pgs_game ON player_game_stats (game_id);
CREATE INDEX ix_pgs_opp ON player_game_stats (opponent_team_id);

CREATE TABLE goalie_game_stats (
  player_id            integer  NOT NULL REFERENCES players(id),
  game_id              bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id              smallint NOT NULL REFERENCES teams(id),
  opponent_team_id     smallint NOT NULL REFERENCES teams(id),
  is_home              boolean  NOT NULL,
  started              boolean  NOT NULL,
  pulled               boolean  NOT NULL DEFAULT false,
  toi_s                integer,
  shots_against        smallint,
  saves                smallint,
  goals_against        smallint,
  ev_shots_against     smallint,
  ev_saves             smallint,
  pp_shots_against     smallint,                     -- opponent on PP
  pp_saves             smallint,
  sh_shots_against     smallint,
  sh_saves             smallint,
  hd_shots_against     smallint,                     -- high-danger (definition versioned in extra)
  hd_saves             smallint,
  xga                  numeric(6,3),
  decision             char(1) CHECK (decision IN ('W','L','O')),  -- O = OT/SO loss
  shutout              boolean,
  extra                jsonb NOT NULL DEFAULT '{}',
  provenance           provenance_kind NOT NULL,
  quality              numeric(3,2) NOT NULL CHECK (quality BETWEEN 0 AND 1),
  source_id            smallint NOT NULL REFERENCES data_sources(id),
  fetched_at           timestamptz NOT NULL,
  PRIMARY KEY (player_id, game_id)
);
CREATE INDEX ix_ggs_game ON goalie_game_stats (game_id);

CREATE TABLE team_game_stats (
  team_id              smallint NOT NULL REFERENCES teams(id),
  game_id              bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  opponent_team_id     smallint NOT NULL REFERENCES teams(id),
  is_home              boolean  NOT NULL,
  goals_for            smallint,
  shots_for            smallint,
  shot_attempts_for    smallint,                     -- CF (all situations)
  ev_shot_attempts_for smallint,
  xgf                  numeric(6,3),
  pp_opportunities     smallint,
  pp_goals             smallint,
  times_shorthanded    smallint,
  pp_goals_against     smallint,
  pim                  smallint,
  hits                 smallint,
  blocked_shots        smallint,
  pp_toi_s             integer,
  sh_toi_s             integer,
  extra                jsonb NOT NULL DEFAULT '{}',
  provenance           provenance_kind NOT NULL,
  source_id            smallint NOT NULL REFERENCES data_sources(id),
  fetched_at           timestamptz NOT NULL,
  PRIMARY KEY (team_id, game_id)
);

-- Shot-level play-by-play (shots, misses, blocks, goals). Powers shot charts,
-- in-house xG, and danger-zone splits.
CREATE TABLE pbp_shot_events (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id          bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  event_idx        integer  NOT NULL,                -- order within game feed
  period           smallint NOT NULL,
  period_seconds   smallint NOT NULL,
  event_type       text     NOT NULL CHECK (event_type IN ('shot','miss','block','goal')),
  shooter_id       integer  REFERENCES players(id),
  goalie_id        integer  REFERENCES players(id),
  blocker_id       integer  REFERENCES players(id),
  team_id          smallint NOT NULL REFERENCES teams(id),
  strength_state   text,                             -- '5v5','5v4','4v5','6v5',...
  x_coord          smallint,
  y_coord          smallint,
  shot_type        text,
  is_rebound       boolean,
  is_rush          boolean,
  xg               numeric(5,4),
  xg_model_version text,
  source_id        smallint NOT NULL REFERENCES data_sources(id),
  fetched_at       timestamptz NOT NULL,
  UNIQUE (game_id, event_idx)
);
CREATE INDEX ix_pbp_shooter ON pbp_shot_events (shooter_id);
CREATE INDEX ix_pbp_goalie ON pbp_shot_events (goalie_id);

-- -----------------------------------------------------------------------------
-- Availability, lineups, roles
-- -----------------------------------------------------------------------------
CREATE TABLE injuries (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id        integer  NOT NULL REFERENCES players(id),
  team_id          smallint REFERENCES teams(id),
  status           availability_status NOT NULL,     -- covers injuries, suspensions, scratches, rest
  body_part        text,
  description      text,
  expected_return  date,
  reported_at      timestamptz NOT NULL,             -- when the source published it
  resolved_at      timestamptz,
  is_active        boolean NOT NULL DEFAULT true,
  provenance       provenance_kind NOT NULL,
  source_id        smallint NOT NULL REFERENCES data_sources(id),
  source_ref       text NOT NULL,                    -- URL / feed id. Required: no source, no row.
  fetched_at       timestamptz NOT NULL
);
CREATE INDEX ix_injuries_active ON injuries (player_id) WHERE is_active;

-- A lineup snapshot is one observation of a team's deployment at a point in
-- time. Diffing consecutive snapshots is how role changes (PP2 -> PP1) are detected.
CREATE TABLE lineup_snapshots (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  team_id       smallint NOT NULL REFERENCES teams(id),
  game_id       bigint REFERENCES games(id) ON DELETE CASCADE,  -- NULL = "current lines" outside a game context
  status        lineup_status NOT NULL,
  observed_at   timestamptz NOT NULL,                -- when source says these were the lines
  provenance    provenance_kind NOT NULL,
  source_id     smallint NOT NULL REFERENCES data_sources(id),
  source_ref    text,
  fetched_at    timestamptz NOT NULL
);
CREATE INDEX ix_lineup_snap_team_game ON lineup_snapshots (team_id, game_id, observed_at DESC);

CREATE TABLE line_combinations (
  snapshot_id   bigint   NOT NULL REFERENCES lineup_snapshots(id) ON DELETE CASCADE,
  unit          text     NOT NULL CHECK (unit IN ('F1','F2','F3','F4','D1','D2','D3','G','EXTRA','SCRATCH')),
  slot          text     NOT NULL CHECK (slot IN ('LW','C','RW','LD','RD','G1','G2','X')),
  player_id     integer  NOT NULL REFERENCES players(id),
  PRIMARY KEY (snapshot_id, player_id)
);

CREATE TABLE powerplay_units (
  snapshot_id   bigint   NOT NULL REFERENCES lineup_snapshots(id) ON DELETE CASCADE,
  unit          text     NOT NULL CHECK (unit IN ('PP1','PP2','PK1','PK2')),
  slot          smallint NOT NULL CHECK (slot BETWEEN 1 AND 5),
  player_id     integer  NOT NULL REFERENCES players(id),
  PRIMARY KEY (snapshot_id, unit, player_id)
);

CREATE TABLE goalie_starts (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id       bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id       smallint NOT NULL REFERENCES teams(id),
  player_id     integer  NOT NULL REFERENCES players(id),
  status        lineup_status NOT NULL,              -- projected -> likely -> confirmed -> actual
  reported_at   timestamptz NOT NULL,
  provenance    provenance_kind NOT NULL,
  source_id     smallint NOT NULL REFERENCES data_sources(id),
  source_ref    text,
  fetched_at    timestamptz NOT NULL
);
CREATE INDEX ix_goalie_starts_game ON goalie_starts (game_id, team_id, reported_at DESC);

-- -----------------------------------------------------------------------------
-- Markets & odds
-- -----------------------------------------------------------------------------
-- One row per bettable market type. Adding a prop = INSERT here + a settlement rule.
CREATE TABLE markets (
  id               smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code             text NOT NULL UNIQUE,             -- 'skater_shots_on_goal', 'goalie_saves', ...
  name             text NOT NULL,
  subject          subject_type NOT NULL,
  kind             market_kind  NOT NULL,
  stat_expr        text NOT NULL,                    -- settlement expression, e.g. 'shots', 'goals+assists', 'saves & decision=W'
  settlement_rule  jsonb NOT NULL DEFAULT '{}',      -- OT/SO inclusion, void rules, etc.
  model_family     text,                             -- which projection model serves it; NULL = unsupported
  is_active        boolean NOT NULL DEFAULT true
);

CREATE TABLE sportsbooks (
  id            smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code          text NOT NULL UNIQUE,
  name          text NOT NULL,
  regions       text[] NOT NULL DEFAULT '{}',        -- jurisdictions where it operates
  is_market_maker boolean NOT NULL DEFAULT false,    -- weight in consensus
  is_enabled    boolean NOT NULL DEFAULT true
);

-- Current state of every offered line. One row per (game, market, book, subject, line).
CREATE TABLE prop_lines (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id          bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  market_id        smallint NOT NULL REFERENCES markets(id),
  sportsbook_id    smallint NOT NULL REFERENCES sportsbooks(id),
  player_id        integer  REFERENCES players(id),
  team_id          smallint REFERENCES teams(id),
  line             numeric(6,2),                     -- NULL for yes/no & moneyline
  over_price       integer,                          -- American odds; for yes/no = YES; ML = home
  under_price      integer,                          -- NULL when book offers only one side
  is_main_line     boolean NOT NULL DEFAULT true,    -- false = alternate line
  status           line_status NOT NULL DEFAULT 'open',
  first_seen_at    timestamptz NOT NULL,
  last_seen_at     timestamptz NOT NULL,
  last_changed_at  timestamptz NOT NULL,
  provenance       provenance_kind NOT NULL,
  source_id        smallint NOT NULL REFERENCES data_sources(id),
  source_ref       text,
  CHECK (over_price IS NULL OR over_price <= -100 OR over_price >= 100),
  CHECK (under_price IS NULL OR under_price <= -100 OR under_price >= 100)
);
CREATE UNIQUE INDEX ux_prop_lines_key ON prop_lines
  (game_id, market_id, sportsbook_id, COALESCE(player_id, 0), COALESCE(team_id, 0), COALESCE(line, -1));
CREATE INDEX ix_prop_lines_player ON prop_lines (player_id, game_id);

-- Append-only price/line history. Written only when something changed.
CREATE TABLE line_movements (
  prop_line_id   bigint      NOT NULL REFERENCES prop_lines(id) ON DELETE CASCADE,
  observed_at    timestamptz NOT NULL,
  line           numeric(6,2),
  over_price     integer,
  under_price    integer,
  status         line_status NOT NULL,
  source_id      smallint    NOT NULL REFERENCES data_sources(id),
  PRIMARY KEY (prop_line_id, observed_at)
) PARTITION BY RANGE (observed_at);
CREATE TABLE line_movements_default PARTITION OF line_movements DEFAULT;
-- Monthly partitions are created by a scheduled job, e.g.:
-- CREATE TABLE line_movements_2026_10 PARTITION OF line_movements
--   FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- Archived odds for completed games (from a licensed historical-odds feed or our
-- own snapshots). snapshot_kind 'close' is what CLV is measured against.
CREATE TABLE historical_odds (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id        bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  market_id      smallint NOT NULL REFERENCES markets(id),
  sportsbook_id  smallint NOT NULL REFERENCES sportsbooks(id),
  player_id      integer  REFERENCES players(id),
  team_id        smallint REFERENCES teams(id),
  line           numeric(6,2),
  over_price     integer,
  under_price    integer,
  snapshot_kind  text NOT NULL CHECK (snapshot_kind IN ('open','t_minus_6h','t_minus_1h','close','other')),
  captured_at    timestamptz NOT NULL,
  provenance     provenance_kind NOT NULL,
  source_id      smallint NOT NULL REFERENCES data_sources(id)
);
CREATE INDEX ix_hist_odds_lookup ON historical_odds (game_id, market_id, player_id, snapshot_kind);

-- -----------------------------------------------------------------------------
-- Models, projections, predictions, grading
-- -----------------------------------------------------------------------------
CREATE TABLE model_versions (
  id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  model_family    text NOT NULL,                     -- 'skater_shots', 'skater_points', 'goalie_saves', 'toi', 'game_sim'
  version         text NOT NULL,                     -- semver or date tag
  algorithm       text NOT NULL,                     -- 'negbin_glm', 'lgbm_poisson', 'ensemble', ...
  status          model_status NOT NULL DEFAULT 'candidate',
  train_start     date,
  train_end       date,
  feature_list    jsonb NOT NULL,
  hyperparams     jsonb NOT NULL DEFAULT '{}',
  calibrator      jsonb,                             -- serialized isotonic/Platt map, if any
  oos_metrics     jsonb NOT NULL DEFAULT '{}',       -- walk-forward log loss, Brier, ECE, MAE, RMSE
  artifact_uri    text,
  git_sha         text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  promoted_at     timestamptz,
  UNIQUE (model_family, version)
);
CREATE UNIQUE INDEX ux_one_champion ON model_versions (model_family) WHERE status = 'champion';

-- Full predictive distribution for (player, game, stat) at a point in time.
-- Projections are immutable; a recalculation flips the old row's is_current off
-- and inserts a new row that `supersedes` it (one transaction). That chain is
-- what powers the before/after "Projection updated" UI.
CREATE TABLE player_projections (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id             bigint   NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id           integer  NOT NULL REFERENCES players(id),
  market_id           smallint NOT NULL REFERENCES markets(id),
  model_version_id    integer  NOT NULL REFERENCES model_versions(id),
  computed_at         timestamptz NOT NULL DEFAULT now(),
  as_of               timestamptz NOT NULL,          -- latest input timestamp used (leakage guard)
  mean                numeric(7,3) NOT NULL,
  median              numeric(7,3),
  std_dev             numeric(7,3),
  pmf                 jsonb NOT NULL,                -- {"support_min":0,"p":[...]} probabilities summing to 1
  expected_toi_s      integer,
  expected_pp_toi_s   integer,
  inputs              jsonb NOT NULL,                -- feature snapshot used (for Explain & audit)
  factors_for         jsonb NOT NULL DEFAULT '[]',   -- [{factor, value, effect, text}]
  factors_against     jsonb NOT NULL DEFAULT '[]',
  data_quality        numeric(3,2) NOT NULL CHECK (data_quality BETWEEN 0 AND 1),
  missing_inputs      text[] NOT NULL DEFAULT '{}',
  trigger_reason      text NOT NULL,                 -- 'scheduled','lineup_change','goalie_confirmed','injury','line_move'
  supersedes          bigint REFERENCES player_projections(id),
  is_current          boolean NOT NULL DEFAULT true,
  provenance          provenance_kind NOT NULL DEFAULT 'derived'
);
CREATE UNIQUE INDEX ux_proj_current ON player_projections (game_id, player_id, market_id)
  WHERE is_current;
CREATE INDEX ix_proj_player ON player_projections (player_id, computed_at DESC);

-- A prediction = a projection priced against a specific sportsbook line at a
-- specific moment. This is the immutable record used for backtesting & ROI.
CREATE TABLE predictions (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  projection_id      bigint   NOT NULL REFERENCES player_projections(id),
  prop_line_id       bigint   REFERENCES prop_lines(id) ON DELETE SET NULL,
  game_id            bigint   NOT NULL REFERENCES games(id),
  market_id          smallint NOT NULL REFERENCES markets(id),
  sportsbook_id      smallint REFERENCES sportsbooks(id),   -- NULL = consensus
  player_id          integer  REFERENCES players(id),
  line               numeric(6,2),
  over_price         integer,
  under_price        integer,
  p_model_over       numeric(5,4) NOT NULL,          -- P(over | not push)
  p_model_under      numeric(5,4) NOT NULL,
  p_push             numeric(5,4) NOT NULL DEFAULT 0,
  p_implied_over     numeric(5,4),                   -- raw, with vig
  p_implied_under    numeric(5,4),
  p_novig_over       numeric(5,4),                   -- NULL if vig cannot be removed (one-sided)
  p_novig_under      numeric(5,4),
  devig_method       text,                           -- 'multiplicative','power','shin','consensus'
  edge_over          numeric(6,4),                   -- p_model - p_novig
  edge_under         numeric(6,4),
  ev_over            numeric(6,4),                   -- expected return per 1u at offered price
  ev_under           numeric(6,4),
  side               bet_side NOT NULL,              -- 'none' when no qualifying edge
  confidence         smallint CHECK (confidence BETWEEN 0 AND 100),
  confidence_parts   jsonb NOT NULL DEFAULT '{}',
  created_at         timestamptz NOT NULL DEFAULT now(),
  is_published       boolean NOT NULL DEFAULT false, -- shown to users (what we're graded on)
  CHECK (p_model_over + p_model_under BETWEEN 0.999 AND 1.001)
);
CREATE INDEX ix_pred_game ON predictions (game_id, market_id);
CREATE INDEX ix_pred_published ON predictions (created_at) WHERE is_published;

CREATE TABLE model_results (
  prediction_id        bigint PRIMARY KEY REFERENCES predictions(id) ON DELETE CASCADE,
  actual_value         numeric(7,2),
  outcome              grade_outcome NOT NULL,
  closing_line         numeric(6,2),
  closing_over_price   integer,
  closing_under_price  integer,
  closing_novig_p      numeric(5,4),                 -- for the side we took
  clv                  numeric(6,4),                 -- closing no-vig p - our no-vig p at pick time
  profit_units         numeric(7,4),                 -- 1u flat stake at the price we recorded
  graded_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE backtest_runs (
  id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  model_version_id integer NOT NULL REFERENCES model_versions(id),
  scheme           text NOT NULL,                    -- 'walk_forward_expanding', 'walk_forward_rolling'
  lineup_mode      text NOT NULL CHECK (lineup_mode IN ('live_tracked','lineup_oracle')),
  period_start     date NOT NULL,
  period_end       date NOT NULL,
  config           jsonb NOT NULL,
  metrics          jsonb NOT NULL,                   -- overall + by market + by confidence bucket + calibration bins
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- Empirical correlations between prop outcomes, estimated from history.
CREATE TABLE prop_correlations (
  id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  market_a_id     smallint NOT NULL REFERENCES markets(id),
  market_b_id     smallint NOT NULL REFERENCES markets(id),
  relation        text NOT NULL,                     -- 'same_player','linemate','same_team','opponent','opp_goalie'
  position_group  text,                              -- 'F','D','any'
  line_a          numeric(6,2),                      -- NULL = continuous (residual) correlation
  line_b          numeric(6,2),
  coefficient     numeric(5,4) NOT NULL,
  ci_low          numeric(5,4),
  ci_high         numeric(5,4),
  n_obs           integer NOT NULL,
  method          text NOT NULL,                     -- 'phi','tetrachoric','residual_pearson','sim'
  window_start    date NOT NULL,
  window_end      date NOT NULL,
  computed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_corr_lookup ON prop_correlations (market_a_id, market_b_id, relation);

-- -----------------------------------------------------------------------------
-- News & alerts
-- -----------------------------------------------------------------------------
CREATE TABLE news (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id      smallint NOT NULL REFERENCES data_sources(id),
  external_id    text,
  url            text NOT NULL,                      -- no URL, no item
  headline       text NOT NULL,
  summary        text,
  category       text NOT NULL CHECK (category IN
                   ('injury','lineup','goalie','scratch','suspension','coach','rest','transaction','general')),
  reliability    text NOT NULL CHECK (reliability IN ('official','beat_reporter','aggregator','unverified')),
  published_at   timestamptz NOT NULL,
  fetched_at     timestamptz NOT NULL,
  provenance     provenance_kind NOT NULL,
  UNIQUE (source_id, external_id)
);
CREATE INDEX ix_news_published ON news (published_at DESC);

CREATE TABLE news_entities (
  news_id    bigint NOT NULL REFERENCES news(id) ON DELETE CASCADE,
  player_id  integer REFERENCES players(id),
  team_id    smallint REFERENCES teams(id),
  CHECK (player_id IS NOT NULL OR team_id IS NOT NULL)
);
CREATE INDEX ix_news_entities_player ON news_entities (player_id);
CREATE INDEX ix_news_entities_team ON news_entities (team_id);

CREATE TABLE alerts (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  alert_type         text NOT NULL CHECK (alert_type IN
                       ('line_threshold','pp_unit','goalie_confirmed','edge_threshold','injury_news','line_move')),
  player_id          integer  REFERENCES players(id),
  team_id            smallint REFERENCES teams(id),
  game_id            bigint   REFERENCES games(id) ON DELETE CASCADE,
  market_id          smallint REFERENCES markets(id),
  sportsbook_id      smallint REFERENCES sportsbooks(id),
  condition          jsonb NOT NULL,                 -- e.g. {"op":"<=","line":4.5} or {"unit":"PP1"}
  channels           text[] NOT NULL DEFAULT '{in_app}',
  is_active          boolean NOT NULL DEFAULT true,
  fire_once          boolean NOT NULL DEFAULT true,
  last_triggered_at  timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_alerts_active ON alerts (alert_type) WHERE is_active;

CREATE TABLE alert_events (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  alert_id         bigint NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  triggered_at     timestamptz NOT NULL DEFAULT now(),
  payload          jsonb NOT NULL,
  delivery_status  text NOT NULL DEFAULT 'pending' CHECK (delivery_status IN ('pending','sent','failed','read')),
  delivered_at     timestamptz
);

CREATE TABLE saved_parlays (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  legs        jsonb NOT NULL,                        -- [{prediction_id | prop_line_id, side}]
  analysis    jsonb NOT NULL,                        -- snapshot of combined prob, corr adj, fair odds
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Seed: market catalogue (prop definitions are data, not code)
-- -----------------------------------------------------------------------------
INSERT INTO markets (code, name, subject, kind, stat_expr, model_family) VALUES
  ('skater_shots_on_goal',   'Shots on Goal',        'skater', 'over_under', 'shots',                         'skater_shots'),
  ('skater_goals',           'Goals',                'skater', 'over_under', 'goals',                         'skater_scoring'),
  ('skater_assists',         'Assists',              'skater', 'over_under', 'assists',                       'skater_scoring'),
  ('skater_points',          'Points',               'skater', 'over_under', 'goals+assists',                 'skater_scoring'),
  ('skater_pp_points',       'Power-play Points',    'skater', 'over_under', 'pp_goals+pp_assists',           'skater_scoring'),
  ('skater_pp_goal',         'Power-play Goal',      'skater', 'yes_no',     'pp_goals>=1',                   'skater_scoring'),
  ('skater_pp_assist',       'Power-play Assist',    'skater', 'yes_no',     'pp_assists>=1',                 'skater_scoring'),
  ('skater_blocked_shots',   'Blocked Shots',        'skater', 'over_under', 'blocked_shots',                 'skater_blocks'),
  ('skater_hits',            'Hits',                 'skater', 'over_under', 'hits',                          'skater_hits'),
  ('skater_anytime_goal',    'Anytime Goal',         'skater', 'yes_no',     'goals>=1',                      'skater_scoring'),
  ('skater_first_goal',      'First Goal',           'skater', 'yes_no',     'first_goal_of_game',            'game_sim'),
  ('goalie_saves',           'Saves',                'goalie', 'over_under', 'saves',                         'goalie'),
  ('goalie_goals_against',   'Goals Against',        'goalie', 'over_under', 'goals_against',                 'goalie'),
  ('goalie_shutout',         'Shutout',              'goalie', 'yes_no',     'shutout',                       'goalie'),
  ('goalie_win',             'Goalie Win',           'goalie', 'yes_no',     'decision=W',                    'game_sim'),
  ('goalie_saves_and_win',   'Saves + Win',          'goalie', 'yes_no',     'saves>=line & decision=W',      'game_sim'),
  ('game_moneyline',         'Moneyline',            'game',   'moneyline',  'winner',                        'game_sim'),
  ('game_total',             'Game Total Goals',     'game',   'over_under', 'home_score+away_score',         'game_sim'),
  ('team_total',             'Team Total Goals',     'team',   'over_under', 'team_goals',                    'game_sim');

COMMIT;
