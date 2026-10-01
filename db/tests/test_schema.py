"""Constraint tests for db/schema.sql.

Run:  python -m pytest db/tests   (or)   python db/tests/test_schema.py
Uses an in-memory database and synthetic rows only.
"""
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"
NOW = "2026-10-10T18:00:00Z"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA.read_text())
    return db


def seed(db: sqlite3.Connection) -> dict:
    ids = {}
    ids["src"] = db.execute(
        "INSERT INTO data_sources (code, name, category, tier, license_notes) "
        "VALUES ('synthetic_dev','Synthetic dev fixtures','schedule','free','Test data. Never shown unlabeled.')"
    ).lastrowid
    db.execute("INSERT INTO seasons VALUES (20262027, '2026-10-01', '2027-06-30')")
    ids["home"] = db.execute(
        "INSERT INTO teams (nhl_team_id, abbrev, name, location, source_id, fetched_at) "
        "VALUES (9001,'AAA','Synthetic A','Testville',?,?)", (ids["src"], NOW)).lastrowid
    ids["away"] = db.execute(
        "INSERT INTO teams (nhl_team_id, abbrev, name, location, source_id, fetched_at) "
        "VALUES (9002,'BBB','Synthetic B','Mockford',?,?)", (ids["src"], NOW)).lastrowid
    ids["player"] = db.execute(
        "INSERT INTO players (nhl_player_id, first_name, last_name, position, current_team_id, source_id, fetched_at) "
        "VALUES (1,'Test','Skater','C',?,?,?)", (ids["home"], ids["src"], NOW)).lastrowid
    ids["game"] = db.execute(
        "INSERT INTO games (nhl_game_id, season_id, game_type, game_date, start_time_utc, "
        "home_team_id, away_team_id, source_id, fetched_at) VALUES (1,20262027,'R','2026-10-10',"
        "'2026-10-10T23:00:00Z',?,?,?,?)", (ids["home"], ids["away"], ids["src"], NOW)).lastrowid
    ids["book"] = db.execute("INSERT INTO sportsbooks (code, name) VALUES ('book_a','Synthetic Book A')").lastrowid
    ids["sog"] = db.execute("SELECT id FROM markets WHERE code='skater_shots_on_goal'").fetchone()[0]
    ids["model"] = db.execute(
        "INSERT INTO model_versions (model_family, version, algorithm, feature_list) "
        "VALUES ('skater_shots','0.0.0-test','negbin_glm','[]')").lastrowid
    return ids


def raises(db, sql, params=(), exc=sqlite3.IntegrityError):
    try:
        db.execute(sql, params)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}: {sql}")


def add_line(db, ids, over=-115, under=-105):
    return db.execute(
        "INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, line, over_price, under_price, "
        "first_seen_at, last_seen_at, last_changed_at, provenance, source_id) "
        "VALUES (?,?,?,?,4.5,?,?,?,?,?,'synthetic',?)",
        (ids["game"], ids["sog"], ids["book"], ids["player"], over, under, NOW, NOW, NOW, ids["src"])).lastrowid


def add_projection(db, ids, mean, supersedes=None, reason="scheduled"):
    return db.execute(
        "INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf, "
        "inputs, data_quality, trigger_reason, supersedes, provenance) "
        "VALUES (?,?,?,?,?,?,'{\"support_min\":0,\"p\":[1]}','{}',0.9,?,?,'synthetic')",
        (ids["game"], ids["player"], ids["sog"], ids["model"], NOW, mean, reason, supersedes)).lastrowid


def test_markets_seeded():
    assert connect().execute("SELECT count(*) FROM markets").fetchone()[0] == 19


def test_generated_columns():
    db = connect(); ids = seed(db)
    db.execute(
        "INSERT INTO player_game_stats (player_id, game_id, team_id, opponent_team_id, is_home, goals, assists, "
        "pp_goals, pp_assists, shots, provenance, quality, source_id, fetched_at) "
        "VALUES (?,?,?,?,1,1,2,0,1,5,'synthetic',1.0,?,?)",
        (ids["player"], ids["game"], ids["home"], ids["away"], ids["src"], NOW))
    assert db.execute("SELECT points, pp_points FROM player_game_stats").fetchone() == (3, 1)
    assert db.execute("SELECT full_name FROM players").fetchone()[0] == "Test Skater"


def test_strict_types_and_enums():
    db = connect(); ids = seed(db)
    raises(db, "UPDATE games SET status='maybe'")
    raises(db, "UPDATE games SET period='two'", exc=sqlite3.IntegrityError)  # STRICT type check
    raises(db, "UPDATE players SET position='X'")


def test_foreign_keys_enforced():
    db = connect(); seed(db)
    raises(db, "INSERT INTO player_team_stints (player_id, team_id, start_date, source_id) VALUES (999,1,'2026-10-01',1)")


def test_prop_lines_odds_and_uniqueness():
    db = connect(); ids = seed(db)
    line_id = add_line(db, ids)
    raises(db, "UPDATE prop_lines SET over_price = 50")
    raises(db, "INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, line, over_price, under_price, "
               "first_seen_at, last_seen_at, last_changed_at, provenance, source_id) "
               "VALUES (?,?,?,?,4.5,-110,-110,?,?,?,'synthetic',?)",
           (ids["game"], ids["sog"], ids["book"], ids["player"], NOW, NOW, NOW, ids["src"]))
    db.execute("INSERT INTO line_movements (prop_line_id, observed_at, line, over_price, under_price, status, source_id) "
               "VALUES (?,?,4.5,-120,100,'open',?)", (line_id, NOW, ids["src"]))


def test_projection_supersede_chain():
    db = connect(); ids = seed(db)
    old = add_projection(db, ids, 4.1)
    raises(db, *("INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf, "
                 "inputs, data_quality, trigger_reason, provenance) VALUES (?,?,?,?,?,4.6,'{}','{}',0.9,'x','synthetic')",
                 (ids["game"], ids["player"], ids["sog"], ids["model"], NOW)))
    db.execute("UPDATE player_projections SET is_current = 0 WHERE id = ?", (old,))
    add_projection(db, ids, 4.6, supersedes=old, reason="lineup_change")
    assert db.execute("SELECT count(*) FROM player_projections WHERE is_current = 1").fetchone()[0] == 1
    assert db.execute("SELECT o.mean, n.mean FROM player_projections n JOIN player_projections o "
                      "ON o.id = n.supersedes").fetchone() == (4.1, 4.6)


def test_one_champion_per_family():
    db = connect(); seed(db)
    db.execute("UPDATE model_versions SET status='champion'")
    raises(db, "INSERT INTO model_versions (model_family, version, algorithm, feature_list, status) "
               "VALUES ('skater_shots','0.0.1','lgbm_poisson','[]','champion')")


def test_predictions_probability_and_immutability():
    db = connect(); ids = seed(db)
    proj = add_projection(db, ids, 4.1)
    raises(db, "INSERT INTO predictions (projection_id, game_id, market_id, line, p_model_over, p_model_under, side) "
               "VALUES (?,?,?,4.5,0.6,0.6,'over')", (proj, ids["game"], ids["sog"]))
    pred = db.execute("INSERT INTO predictions (projection_id, game_id, market_id, line, p_model_over, p_model_under, "
                      "side, is_published) VALUES (?,?,?,4.5,0.612,0.388,'over',1)",
                      (proj, ids["game"], ids["sog"])).lastrowid
    raises(db, "UPDATE predictions SET p_model_over = 0.7, p_model_under = 0.3 WHERE id = ?", (pred,))
    raises(db, "DELETE FROM predictions WHERE id = ?", (pred,))


def test_sources_required():
    db = connect(); ids = seed(db)
    raises(db, "INSERT INTO injuries (player_id, status, reported_at, provenance, source_id, source_ref, fetched_at) "
               "VALUES (?,'out',?,'synthetic',?,NULL,?)", (ids["player"], NOW, ids["src"], NOW))
    raises(db, "INSERT INTO injuries (player_id, status, reported_at, provenance, source_id, source_ref, fetched_at) "
               "VALUES (?,'out',?,'synthetic',?,'',?)", (ids["player"], NOW, ids["src"], NOW))
    raises(db, "INSERT INTO news (source_id, url, headline, category, reliability, published_at, fetched_at, provenance) "
               "VALUES (?, '', 'x', 'injury', 'official', ?, ?, 'synthetic')", (ids["src"], NOW, NOW))


def test_alert_fires_once_per_game():
    db = connect(); ids = seed(db)
    alert = db.execute("INSERT INTO alerts (key, alert_type, condition) VALUES ('g1','goalie_confirmed','{}')").lastrowid
    db.execute("INSERT INTO alert_events (alert_id, game_id, payload) VALUES (?,?,'{}')", (alert, ids["game"]))
    raises(db, "INSERT INTO alert_events (alert_id, game_id, payload) VALUES (?,?,'{}')", (alert, ids["game"]))


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
    print(f"schema tests: {len(tests)} passed")
