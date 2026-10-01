-- Official standings snapshots (one row per team per date fetched). Records shown on the
-- slate come from here rather than being recomputed, so they always match NHL.com.
CREATE TABLE team_standings (
  team_id          INTEGER NOT NULL REFERENCES teams(id),
  season_id        INTEGER NOT NULL REFERENCES seasons(id),
  as_of_date       TEXT NOT NULL,                    -- the standings date reported by the NHL
  games_played     INTEGER NOT NULL,
  wins             INTEGER NOT NULL,
  losses           INTEGER NOT NULL,
  ot_losses        INTEGER NOT NULL,
  points           INTEGER NOT NULL,
  goals_for        INTEGER NOT NULL,
  goals_against    INTEGER NOT NULL,
  home_wins        INTEGER,
  home_losses      INTEGER,
  home_ot_losses   INTEGER,
  road_wins        INTEGER,
  road_losses      INTEGER,
  road_ot_losses   INTEGER,
  l10_wins         INTEGER,
  l10_losses       INTEGER,
  l10_ot_losses    INTEGER,
  streak_code      TEXT,
  streak_count     INTEGER,
  conference       TEXT,
  division         TEXT,
  provenance       TEXT NOT NULL CHECK (provenance IN
                     ('official','licensed','community','reported','manual','derived','synthetic')),
  source_id        INTEGER NOT NULL REFERENCES data_sources(id),
  fetched_at       TEXT NOT NULL,
  PRIMARY KEY (team_id, season_id, as_of_date)
) STRICT;
CREATE INDEX ix_team_standings_latest ON team_standings (season_id, as_of_date DESC);
