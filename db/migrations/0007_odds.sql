-- Phase 4: sportsbook lines (The Odds API).

-- Vendor event id -> our game, so each event is matched once.
CREATE TABLE odds_events (
  source_id         INTEGER NOT NULL REFERENCES data_sources(id),
  event_id          TEXT NOT NULL,
  game_id           INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  commence_time     TEXT NOT NULL,
  first_seen_at     TEXT NOT NULL,
  props_fetched_at  TEXT,
  PRIMARY KEY (source_id, event_id)
) STRICT;
CREATE INDEX ix_odds_events_game ON odds_events (game_id);

-- Every metered call: what it cost and what the account had left (from the API's headers).
CREATE TABLE odds_usage (
  id                 INTEGER PRIMARY KEY,
  at                 TEXT NOT NULL,
  endpoint           TEXT NOT NULL CHECK (endpoint IN ('events','game_odds','event_odds')),
  game_id            INTEGER REFERENCES games(id) ON DELETE SET NULL,
  markets            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(markets)),
  credits_charged    INTEGER NOT NULL,
  credits_remaining  INTEGER,
  credits_used       INTEGER
) STRICT;
CREATE INDEX ix_odds_usage_at ON odds_usage (at);

-- prop_lines conventions (the table and its unique index are in schema.sql):
-- * one is_main_line = 1 row per (game, market, book, player or team), updated in place;
--   every change is appended to line_movements.
-- * Moneyline rows: team_id = home team, over_price = home price, under_price = away price.
-- * Yes/no rows (anytime / first goal): over_price = Yes, under_price = No (often not offered).
