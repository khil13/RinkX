-- Game- and team-level projections (model version 1.1 game simulation): moneyline win
-- probability, team totals and the game total. Same immutability rules as player_projections:
-- a change clears is_current on the old row and inserts a row that `supersedes` it.
CREATE TABLE game_projections (
  id                INTEGER PRIMARY KEY,
  game_id           INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  market_id         INTEGER NOT NULL REFERENCES markets(id),
  side              TEXT NOT NULL CHECK (side IN ('home','away','game')),
  model_version_id  INTEGER NOT NULL REFERENCES model_versions(id),
  computed_at       TEXT NOT NULL,
  as_of             TEXT NOT NULL,
  mean              REAL NOT NULL,                  -- win probability, or expected goals
  pmf               TEXT NOT NULL CHECK (json_valid(pmf)),
  inputs            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(inputs)),
  factors           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(factors)),
  trigger_reason    TEXT NOT NULL,
  supersedes        INTEGER REFERENCES game_projections(id),
  is_current        INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1)),
  provenance        TEXT NOT NULL DEFAULT 'derived' CHECK (provenance IN ('derived','synthetic'))
) STRICT;
CREATE UNIQUE INDEX ux_game_proj_current ON game_projections (game_id, market_id, side) WHERE is_current = 1;
