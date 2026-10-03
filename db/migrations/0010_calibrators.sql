-- Live calibration: one isotonic map per market from the model's probability (over / yes / home,
-- pushes excluded) to how often it actually happened, fit on graded predictions. It is applied
-- to pricing only when it improved Brier on the most recent graded props it never saw.
CREATE TABLE calibrators (
  id            INTEGER PRIMARY KEY,
  market_id     INTEGER NOT NULL REFERENCES markets(id),
  fitted_at     TEXT NOT NULL,
  n_fit         INTEGER NOT NULL,          -- graded props the published map was fit on
  n_holdout     INTEGER NOT NULL,          -- latest props held out to decide whether to apply it
  brier_raw     REAL,                      -- held-out Brier of the model as is
  brier_cal     REAL,                      -- held-out Brier after calibration (fit without them)
  applied       INTEGER NOT NULL CHECK (applied IN (0,1)),
  reason        TEXT NOT NULL,             -- applied | no_improvement | too_few
  knots         TEXT NOT NULL CHECK (json_valid(knots)),   -- {"x": [...], "y": [...]} increasing
  is_current    INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
CREATE UNIQUE INDEX ux_calibrators_current ON calibrators (market_id) WHERE is_current = 1;
