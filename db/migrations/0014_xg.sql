-- Expected goals (xG): RinkX's own shot-quality model, fit on NHL play-by-play shot locations.
-- Each fit is kept with its held-out test, so the site can say whether it passed and how well.
-- Shot rows get pbp_shot_events.xg / xg_model_version from the newest fit that passed.
CREATE TABLE xg_models (
  id          INTEGER PRIMARY KEY,
  version     TEXT NOT NULL,                                -- feature set (rinkx.models.xg.VERSION)
  fitted_at   TEXT NOT NULL,
  train_from  TEXT, train_to TEXT,                          -- game dates the coefficients were fit on
  test_from   TEXT, test_to  TEXT,                          -- later game dates they were scored on
  n_train     INTEGER NOT NULL,
  n_test      INTEGER NOT NULL,
  coef        TEXT NOT NULL CHECK (json_valid(coef)),       -- feature name -> coefficient (logit)
  metrics     TEXT NOT NULL CHECK (json_valid(metrics)),    -- held-out log loss vs baselines, calibration
  passed      INTEGER NOT NULL CHECK (passed IN (0,1)),
  reason      TEXT
) STRICT;
CREATE INDEX ix_pbp_xg_pending ON pbp_shot_events (game_id) WHERE xg IS NULL;
