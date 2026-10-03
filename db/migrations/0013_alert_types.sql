-- More alert types: deployment changes (PP1 / top-line promotions and demotions, expected TOI
-- down), projection changes, and scratches. SQLite can't widen a CHECK in place, so both alert
-- tables are rebuilt and every row copied (alert history is kept).
CREATE TABLE alerts_new (
  id                 INTEGER PRIMARY KEY,
  key                TEXT NOT NULL UNIQUE,
  alert_type         TEXT NOT NULL CHECK (alert_type IN
                       ('line_threshold','pp_unit','goalie_confirmed','edge_threshold','injury_news','line_move',
                        'deployment','projection_change','scratch')),
  player_id          INTEGER REFERENCES players(id),
  team_id            INTEGER REFERENCES teams(id),
  market_id          INTEGER REFERENCES markets(id),
  sportsbook_id      INTEGER REFERENCES sportsbooks(id),
  condition          TEXT NOT NULL CHECK (json_valid(condition)),
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  fire_once_per_game INTEGER NOT NULL DEFAULT 1 CHECK (fire_once_per_game IN (0,1)),
  last_triggered_at  TEXT
) STRICT;
INSERT INTO alerts_new SELECT * FROM alerts;
CREATE TABLE alert_events_new (
  id               INTEGER PRIMARY KEY,
  alert_id         INTEGER NOT NULL REFERENCES alerts_new(id) ON DELETE CASCADE,
  game_id          INTEGER REFERENCES games(id) ON DELETE CASCADE,
  triggered_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  payload          TEXT NOT NULL CHECK (json_valid(payload)),
  delivery_status  TEXT NOT NULL DEFAULT 'pending' CHECK (delivery_status IN ('pending','sent','failed')),
  delivered_at     TEXT
) STRICT;
INSERT INTO alert_events_new SELECT * FROM alert_events;
DROP TABLE alert_events;
DROP TABLE alerts;
ALTER TABLE alerts_new RENAME TO alerts;
ALTER TABLE alert_events_new RENAME TO alert_events;
CREATE UNIQUE INDEX ux_alert_once_per_game ON alert_events (alert_id, game_id);
