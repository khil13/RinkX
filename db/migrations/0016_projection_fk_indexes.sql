-- Indexes on the columns that point at projections. With foreign keys on, deleting a
-- projection makes SQLite look for rows that still reference it (a newer projection's
-- `supersedes`, a prediction's `projection_id` / `game_projection_id`). Without these indexes
-- every single delete scanned both tables, so the daily prune of old projections grew with the
-- square of their size. From Oct 18 it ran past the 60-minute job limit, and every run after
-- that was killed in the same prune before it could save.
CREATE INDEX IF NOT EXISTS ix_proj_supersedes ON player_projections (supersedes);
CREATE INDEX IF NOT EXISTS ix_gproj_supersedes ON game_projections (supersedes);
CREATE INDEX IF NOT EXISTS ix_pred_projection ON predictions (projection_id);
CREATE INDEX IF NOT EXISTS ix_pred_game_projection ON predictions (game_projection_id);
-- The prune selects projections by game.
CREATE INDEX IF NOT EXISTS ix_proj_game ON player_projections (game_id);
CREATE INDEX IF NOT EXISTS ix_gproj_game ON game_projections (game_id);
