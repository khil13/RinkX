-- Phase 7: grading. model_results has never been written, so it is rebuilt with:
--   result       the side that won at this line (over/under/yes/no/home/away), 'push' or 'void'
--   void_reason  why the bet was voided (did not play, goalie did not start, game cancelled ...)
--   outcome      for the prediction's lean; 'no_bet' when it had none (still scored for calibration)
--   closing_*    the last open price at this line before the game started (line_movements)
--   clv          leans only: closing no-vig P(lean side) x decimal price taken - 1
DROP TABLE model_results;
CREATE TABLE model_results (
  prediction_id        INTEGER PRIMARY KEY REFERENCES predictions(id),
  actual_value         REAL,
  result               TEXT NOT NULL CHECK (result IN ('over','under','yes','no','home','away','push','void')),
  void_reason          TEXT,
  outcome              TEXT NOT NULL CHECK (outcome IN ('win','loss','push','void','no_bet')),
  profit_units         REAL,
  closing_line         REAL,
  closing_over_price   INTEGER,
  closing_under_price  INTEGER,
  closing_at           TEXT,
  closing_novig_p      REAL,                          -- no-vig P(over / yes / home) at the close
  clv                  REAL,
  graded_at            TEXT NOT NULL,
  CHECK ((result = 'void') = (outcome = 'void') OR outcome = 'no_bet'),
  CHECK ((outcome = 'no_bet') = (profit_units IS NULL))
) STRICT;
CREATE INDEX ix_results_outcome ON model_results (outcome);
