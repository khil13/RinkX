-- Smoke test for db/schema.sql. Runs inside a transaction and rolls back.
-- Usage: psql -v ON_ERROR_STOP=1 -d rinkx -f db/tests/schema_smoke.sql
-- All rows are provenance 'synthetic' — this file never touches real data.
\set ON_ERROR_STOP 1
BEGIN;

INSERT INTO data_sources (code, name, category, tier, license_notes)
VALUES ('synthetic_dev', 'Synthetic dev fixtures', 'schedule', 'free', 'Generated test data. Never shown unlabeled.');

INSERT INTO seasons VALUES (20262027, '2026-10-01', '2027-06-30');

INSERT INTO teams (nhl_team_id, abbrev, name, location, source_id, fetched_at)
SELECT 9001, 'AAA', 'Synthetic A', 'Testville', id, now() FROM data_sources WHERE code = 'synthetic_dev';
INSERT INTO teams (nhl_team_id, abbrev, name, location, source_id, fetched_at)
SELECT 9002, 'BBB', 'Synthetic B', 'Mockford', id, now() FROM data_sources WHERE code = 'synthetic_dev';

INSERT INTO players (nhl_player_id, first_name, last_name, position, current_team_id, source_id, fetched_at)
SELECT 1, 'Test', 'Skater', 'C', t.id, s.id, now()
FROM teams t, data_sources s WHERE t.abbrev = 'AAA' AND s.code = 'synthetic_dev';

INSERT INTO games (nhl_game_id, season_id, game_type, game_date, start_time_utc,
                   home_team_id, away_team_id, source_id, fetched_at)
SELECT 1, 20262027, 'R', '2026-10-10', '2026-10-10T23:00Z',
       (SELECT id FROM teams WHERE abbrev='AAA'), (SELECT id FROM teams WHERE abbrev='BBB'), id, now()
FROM data_sources WHERE code = 'synthetic_dev';

-- generated columns
INSERT INTO player_game_stats (player_id, game_id, team_id, opponent_team_id, is_home,
                               goals, assists, pp_goals, pp_assists, shots, provenance, quality, source_id, fetched_at)
SELECT p.id, g.id, g.home_team_id, g.away_team_id, true, 1, 2, 0, 1, 5, 'synthetic', 1.0, s.id, now()
FROM players p, games g, data_sources s WHERE s.code = 'synthetic_dev';

DO $$ BEGIN
  ASSERT (SELECT points FROM player_game_stats) = 3, 'points generated column';
  ASSERT (SELECT pp_points FROM player_game_stats) = 1, 'pp_points generated column';
  ASSERT (SELECT full_name FROM players) = 'Test Skater', 'full_name generated column';
END $$;

-- prop line + movement partition routing
INSERT INTO sportsbooks (code, name) VALUES ('book_a', 'Synthetic Book A');
INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, line, over_price, under_price,
                        first_seen_at, last_seen_at, last_changed_at, provenance, source_id)
SELECT g.id, m.id, b.id, p.id, 4.5, -115, -105, now(), now(), now(), 'synthetic', s.id
FROM games g, markets m, sportsbooks b, players p, data_sources s
WHERE m.code = 'skater_shots_on_goal' AND s.code = 'synthetic_dev';

INSERT INTO line_movements (prop_line_id, observed_at, line, over_price, under_price, status, source_id)
SELECT id, now(), 4.5, -120, +100, 'open', source_id FROM prop_lines;

-- invalid American odds must be rejected
DO $$ BEGIN
  BEGIN
    UPDATE prop_lines SET over_price = 50;
    RAISE EXCEPTION 'expected odds CHECK to fail';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- duplicate line for same key must be rejected
DO $$ BEGIN
  BEGIN
    INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, line, over_price, under_price,
                            first_seen_at, last_seen_at, last_changed_at, provenance, source_id)
    SELECT game_id, market_id, sportsbook_id, player_id, line, -110, -110, now(), now(), now(), 'synthetic', source_id
    FROM prop_lines;
    RAISE EXCEPTION 'expected unique violation';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END $$;

-- projections: only one current row per (game, player, market); supersede chain
INSERT INTO model_versions (model_family, version, algorithm, feature_list)
VALUES ('skater_shots', '0.0.0-test', 'negbin_glm', '[]');

INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf,
                                inputs, data_quality, trigger_reason)
SELECT g.id, p.id, m.id, mv.id, now(), 4.1, '{"support_min":0,"p":[1]}', '{}', 0.9, 'scheduled'
FROM games g, players p, markets m, model_versions mv WHERE m.code = 'skater_shots_on_goal';

DO $$ BEGIN
  BEGIN
    INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf,
                                    inputs, data_quality, trigger_reason)
    SELECT game_id, player_id, market_id, model_version_id, now(), 4.6, pmf, inputs, 0.9, 'lineup_change'
    FROM player_projections;
    RAISE EXCEPTION 'expected unique violation on current projection';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END $$;

-- recalculation: retire old, insert new that supersedes it
UPDATE player_projections SET is_current = false WHERE is_current;
INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf,
                                inputs, data_quality, trigger_reason, supersedes)
SELECT game_id, player_id, market_id, model_version_id, now(), 4.6, pmf, inputs, 0.9, 'lineup_change', id
FROM player_projections;
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM player_projections WHERE is_current) = 1, 'one current projection';
  ASSERT (SELECT o.mean FROM player_projections n JOIN player_projections o ON o.id = n.supersedes) = 4.1,
         'before/after chain';
END $$;

-- only one champion per model family
UPDATE model_versions SET status = 'champion';
DO $$ BEGIN
  BEGIN
    INSERT INTO model_versions (model_family, version, algorithm, feature_list, status)
    VALUES ('skater_shots', '0.0.1-test', 'lgbm_poisson', '[]', 'champion');
    RAISE EXCEPTION 'expected unique violation on champion';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END $$;

-- prediction probabilities must sum to 1
DO $$ BEGIN
  BEGIN
    INSERT INTO predictions (projection_id, game_id, market_id, line, p_model_over, p_model_under, side)
    SELECT id, game_id, market_id, 4.5, 0.6, 0.6, 'over' FROM player_projections LIMIT 1;
    RAISE EXCEPTION 'expected probability CHECK to fail';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- injuries require a source reference
DO $$ BEGIN
  BEGIN
    INSERT INTO injuries (player_id, status, reported_at, provenance, source_id, source_ref, fetched_at)
    SELECT p.id, 'out', now(), 'synthetic', s.id, NULL, now() FROM players p, data_sources s;
    RAISE EXCEPTION 'expected NOT NULL on source_ref';
  EXCEPTION WHEN not_null_violation THEN NULL;
  END;
END $$;

\echo 'schema smoke test: OK'
ROLLBACK;
