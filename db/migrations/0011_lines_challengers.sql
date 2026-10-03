-- Line combinations from shift charts, and champion/challenger model versions.

-- When this game's shift chart was turned into line combinations and PP units
-- (lineup_snapshots with status 'actual', provenance 'derived').
ALTER TABLE game_enrichment ADD COLUMN shifts_at TEXT;

-- Who assisted each goal (play-by-play), for the same-game simulation's assist model. Games loaded
-- before this column existed are re-read once (pbp_version 1 -> 2).
ALTER TABLE pbp_shot_events ADD COLUMN assist1_id INTEGER REFERENCES players(id);
ALTER TABLE pbp_shot_events ADD COLUMN assist2_id INTEGER REFERENCES players(id);
ALTER TABLE game_enrichment ADD COLUMN pbp_version INTEGER NOT NULL DEFAULT 1;

-- A Quick Entry line change can say a player is now off the power play: in a snapshot with
-- pp_specified = 1, a player listed in line_combinations but not in powerplay_units has no PP unit.
-- With 0 (only his even-strength line was given) his PP unit is left as it was.
ALTER TABLE lineup_snapshots ADD COLUMN pp_specified INTEGER NOT NULL DEFAULT 1 CHECK (pp_specified IN (0,1));

-- The challenger's current projection for an upcoming game, kept beside the published
-- (champion) one so every line priced can also be priced by the challenger, unpublished.
CREATE TABLE challenger_projections (
  id                INTEGER PRIMARY KEY,
  game_id           INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id         INTEGER REFERENCES players(id),          -- NULL for game markets
  side              TEXT NOT NULL DEFAULT '',                 -- game markets: home / away / game
  market_id         INTEGER NOT NULL REFERENCES markets(id),
  model_version_id  INTEGER NOT NULL REFERENCES model_versions(id),
  mean              REAL NOT NULL,
  pmf               TEXT NOT NULL CHECK (json_valid(pmf)),
  computed_at       TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX ux_challenger_proj ON challenger_projections
  (game_id, coalesce(player_id, 0), side, market_id, model_version_id);

-- For each priced line: the champion's and the challenger's probability of the first side
-- (over / yes / home, pushes excluded, before live calibration), frozen when it was priced.
-- Graded results compare the two on exactly the same props.
CREATE TABLE shadow_predictions (
  prediction_id        INTEGER NOT NULL REFERENCES predictions(id),
  model_version_id     INTEGER NOT NULL REFERENCES model_versions(id),   -- the challenger
  champion_version_id  INTEGER NOT NULL REFERENCES model_versions(id),
  p_champion           REAL NOT NULL CHECK (p_champion BETWEEN 0 AND 1),
  p_challenger         REAL NOT NULL CHECK (p_challenger BETWEEN 0 AND 1),
  created_at           TEXT NOT NULL,
  PRIMARY KEY (prediction_id, model_version_id)
) STRICT;

-- Every promotion or rejection, with the live evidence behind it.
CREATE TABLE model_promotions (
  id             INTEGER PRIMARY KEY,
  model_family   TEXT NOT NULL,
  champion       TEXT NOT NULL,       -- version that was champion
  challenger     TEXT NOT NULL,
  decision       TEXT NOT NULL CHECK (decision IN ('promoted','rejected')),
  n_props        INTEGER NOT NULL,    -- graded props (game, player, market) compared
  mean_diff      REAL NOT NULL,       -- challenger log score minus champion's, per prop
  se             REAL NOT NULL,
  decided_at     TEXT NOT NULL
) STRICT;
