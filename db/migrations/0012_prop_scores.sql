-- Prop scores, frozen with each prediction when it is priced (pre-game data only), so the
-- backtest can filter on them without looking ahead. Weights come from config/scoring.yml and
-- are stored with each row, so a weight change never rewrites history.
CREATE TABLE prediction_scores (
  prediction_id     INTEGER PRIMARY KEY REFERENCES predictions(id),
  intelligence      INTEGER CHECK (intelligence BETWEEN 0 AND 100),   -- NULL: too little data
  intelligence_parts TEXT NOT NULL CHECK (json_valid(intelligence_parts)),
  shot_environment  INTEGER CHECK (shot_environment BETWEEN 0 AND 100), -- shots-on-goal props only
  shot_env_parts    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(shot_env_parts)),
  value             INTEGER CHECK (value BETWEEN 0 AND 100),
  facts             TEXT NOT NULL CHECK (json_valid(facts)),            -- the inputs "Why this prop?" quotes
  config_version    TEXT NOT NULL,
  created_at        TEXT NOT NULL
) STRICT;
