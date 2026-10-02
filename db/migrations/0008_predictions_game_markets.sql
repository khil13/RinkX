-- Phase 5: predictions can price game markets (moneyline, totals) as well as player props.
-- SQLite can't relax NOT NULL in place, so the (still empty) table is rebuilt with:
--   projection_id NULL-able, plus game_projection_id; exactly one of the two is set.
DROP TRIGGER IF EXISTS trg_predictions_immutable;
DROP TRIGGER IF EXISTS trg_predictions_no_delete;
CREATE TABLE predictions_new (
  id                 INTEGER PRIMARY KEY,
  projection_id      INTEGER REFERENCES player_projections(id),
  game_projection_id INTEGER REFERENCES game_projections(id),
  prop_line_id       INTEGER REFERENCES prop_lines(id) ON DELETE SET NULL,
  game_id            INTEGER NOT NULL REFERENCES games(id),
  market_id          INTEGER NOT NULL REFERENCES markets(id),
  sportsbook_id      INTEGER REFERENCES sportsbooks(id),
  player_id          INTEGER REFERENCES players(id),
  line               REAL,
  over_price         INTEGER,
  under_price        INTEGER,
  p_model_over       REAL NOT NULL CHECK (p_model_over BETWEEN 0 AND 1),
  p_model_under      REAL NOT NULL CHECK (p_model_under BETWEEN 0 AND 1),
  p_push             REAL NOT NULL DEFAULT 0 CHECK (p_push BETWEEN 0 AND 1),
  p_implied_over     REAL,
  p_implied_under    REAL,
  p_novig_over       REAL,
  p_novig_under      REAL,
  devig_method       TEXT,
  edge_over          REAL,
  edge_under         REAL,
  ev_over            REAL,
  ev_under           REAL,
  side               TEXT NOT NULL CHECK (side IN ('over','under','yes','no','home','away','none')),
  confidence         INTEGER CHECK (confidence BETWEEN 0 AND 100),
  confidence_parts   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(confidence_parts)),
  calculation        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(calculation)),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  is_published       INTEGER NOT NULL DEFAULT 0 CHECK (is_published IN (0,1)),
  CHECK (abs(p_model_over + p_model_under - 1) <= 0.001),
  CHECK ((projection_id IS NULL) <> (game_projection_id IS NULL))
) STRICT;
INSERT INTO predictions_new (id, projection_id, prop_line_id, game_id, market_id, sportsbook_id, player_id, line,
  over_price, under_price, p_model_over, p_model_under, p_push, p_implied_over, p_implied_under, p_novig_over,
  p_novig_under, devig_method, edge_over, edge_under, ev_over, ev_under, side, confidence, confidence_parts,
  created_at, is_published)
SELECT id, projection_id, prop_line_id, game_id, market_id, sportsbook_id, player_id, line, over_price, under_price,
  p_model_over, p_model_under, p_push, p_implied_over, p_implied_under, p_novig_over, p_novig_under, devig_method,
  edge_over, edge_under, ev_over, ev_under, side, confidence, confidence_parts, created_at, is_published
FROM predictions;
DROP TABLE predictions;
ALTER TABLE predictions_new RENAME TO predictions;
CREATE INDEX ix_pred_game ON predictions (game_id, market_id);
CREATE INDEX ix_pred_published ON predictions (created_at) WHERE is_published = 1;
CREATE INDEX ix_pred_line ON predictions (prop_line_id, created_at);
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
