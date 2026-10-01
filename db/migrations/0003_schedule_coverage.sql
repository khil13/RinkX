-- Which calendar dates a successful schedule fetch has covered. A date with no games is only
-- published as "no games" if it is covered here; otherwise the site says data is unavailable.
CREATE TABLE schedule_coverage (
  game_date   TEXT PRIMARY KEY,
  fetched_at  TEXT NOT NULL,
  source_id   INTEGER NOT NULL REFERENCES data_sources(id)
) STRICT;
