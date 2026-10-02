-- Phase 2: play-by-play and stats-API enrichment.

-- Which enrichment steps have completed for each game, so each runs exactly once.
CREATE TABLE game_enrichment (
  game_id   INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
  pbp_at    TEXT,   -- play-by-play shot events + primary/secondary assists written
  stats_at  TEXT    -- stats-API TOI splits, PP assists, shot attempts, faceoffs written
) STRICT;

-- Official convention: a shot blocked by the shooter's own teammate is a blocked attempt
-- for the shooter but not a blocked shot for anyone (blocker_id stays NULL).
ALTER TABLE pbp_shot_events ADD COLUMN is_teammate_block INTEGER NOT NULL DEFAULT 0
  CHECK (is_teammate_block IN (0,1));
ALTER TABLE pbp_shot_events ADD COLUMN is_empty_net INTEGER NOT NULL DEFAULT 0
  CHECK (is_empty_net IN (0,1));
