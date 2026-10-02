-- Phase 3: per-game availability from Quick Entry, and a log of processed Quick Entry issues.

-- A player ruled in/out for one game (scratch, injury, rest). Game-specific, unlike `injuries`.
-- The latest report per (game, player) wins. No source URL, no row.
CREATE TABLE game_availability (
  id           INTEGER PRIMARY KEY,
  game_id      INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id    INTEGER NOT NULL REFERENCES players(id),
  status       TEXT NOT NULL CHECK (status IN ('out','available')),
  reason       TEXT,
  reported_at  TEXT NOT NULL,
  provenance   TEXT NOT NULL CHECK (provenance IN
                 ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id    INTEGER NOT NULL REFERENCES data_sources(id),
  source_ref   TEXT NOT NULL CHECK (length(source_ref) > 0),
  fetched_at   TEXT NOT NULL
) STRICT;
CREATE INDEX ix_availability ON game_availability (game_id, player_id, reported_at DESC);

-- Every Quick Entry issue the pipeline has handled, so each is applied exactly once.
CREATE TABLE quick_entries (
  issue_number  INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  author        TEXT NOT NULL,
  payload       TEXT NOT NULL CHECK (json_valid(payload)),
  status        TEXT NOT NULL CHECK (status IN ('applied','rejected')),
  message       TEXT NOT NULL,
  processed_at  TEXT NOT NULL
) STRICT;
