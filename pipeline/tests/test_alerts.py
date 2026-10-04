"""Phase 8: alerts (config, sync, once-per-game evaluation, delivery) and news via Quick Entry."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
import synth

from rinkx.alerts import evaluate as al
from rinkx.alerts import ntfy
from rinkx.config import Settings
from rinkx.ingestion.odds.store import upsert_line
from rinkx.pricing.price import PricingConfig, run_pricing
from rinkx.publish.news import feed, for_player
from rinkx.quick_entry import Issue, apply, run_quick_entry
from rinkx.timeutil import iso

DAY = date(2025, 10, 7) + timedelta(days=30)
NOW = datetime(DAY.year, DAY.month, DAY.day, 15, tzinfo=UTC)
CFG = PricingConfig(min_edge=0.03, dq_floor=0.6, implausible_edge=0.15)


@pytest.fixture
def league(tmp_path):
    conn, _truth = synth.build(str(tmp_path / "a.db"), teams=4, days=30, seed=7)
    gid = synth.add_upcoming(conn, DAY)
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    for code, name in (("fanduel", "FanDuel"), ("betmgm", "BetMGM")):
        conn.execute("INSERT INTO sportsbooks (code, name) VALUES (?, ?)", (code, name))
    books = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM sportsbooks")}
    sog = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    home = conn.execute("SELECT home_team_id FROM games WHERE id = ?", (gid,)).fetchone()[0]
    pid = conn.execute(
        "SELECT id FROM players WHERE current_team_id = ? AND position <> 'G' ORDER BY id LIMIT 1", (home,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO model_versions (model_family, version, algorithm, feature_list) "
        "VALUES ('skater_shots', 'test', 'test', '[]')"
    )
    mv = conn.execute("SELECT id FROM model_versions WHERE version = 'test'").fetchone()[0]
    # The model says P(over 2.5) is about 0.68; the books say about 0.5: a clear lean.
    pmf = [0.05, 0.1, 0.17, 0.2, 0.18, 0.13, 0.09, 0.05, 0.03]
    conn.execute(
        "INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, as_of, mean, pmf, inputs, "
        "data_quality, trigger_reason, provenance) VALUES (?, ?, ?, ?, ?, 3.4, ?, '{\"games_in_history\": 30}', 0.9, "
        "'scheduled', 'synthetic')",
        (gid, pid, sog, mv, iso(NOW), json.dumps({"p": pmf})),
    )
    for code, (o, u) in (("fanduel", (-110, -110)), ("betmgm", (-105, -115))):
        upsert_line(
            conn,
            game_id=gid,
            market_id=sog,
            book_id=books[code],
            player_id=pid,
            team_id=None,
            line=2.5,
            over=o,
            under=u,
            at=iso(NOW - timedelta(hours=3)),
            source_id=src,
            source_ref="test",
        )
    assert run_pricing(conn, NOW, CFG) == 2
    conn.commit()
    return conn, gid, pid, books, sog, src


def _yml(tmp_path, text: str):
    p = tmp_path / "alerts.yml"
    p.write_text(text)
    return p


def test_config_validation(tmp_path):
    specs, errors = al.load_specs(
        _yml(
            tmp_path,
            """
alerts:
  - {key: a, type: edge, min_edge: 4}
  - {key: a, type: goalie}
  - {key: b, type: nope}
  - {key: c, type: line_move}
  - {key: d, type: edge, colour: red}
  - {key: e, type: news, categories: [injury, gossip]}
  - {key: f, type: line, market: skater_goals, player: 1}
  - {key: g, type: goalie, enabled: false}
""",
        )
    )
    assert [(s.key, s.enabled) for s in specs] == [("a", True), ("g", False)]
    assert len(errors) == 6
    assert any("duplicate key 'a'" in e for e in errors)
    assert any("unknown type 'nope'" in e for e in errors)
    assert any("needs `points`" in e for e in errors)
    assert any("colour" in e for e in errors)
    # The shipped file is valid.
    specs, errors = al.load_specs()
    assert errors == [] and {s.key for s in specs} >= {"strong-leans"}


def test_edge_alert_fires_once_per_game(league, tmp_path):
    conn, gid, pid, *_ = league
    path = _yml(tmp_path, "alerts:\n  - {key: leans, type: edge, min_edge: 4}\n")
    sent: list[dict] = []
    assert al.run_alerts(conn, NOW, send=sent.append, site_url="https://o.github.io/RinkX/", path=path) == 1
    assert len(sent) == 1
    p = sent[0]
    name = conn.execute("SELECT full_name FROM players WHERE id = ?", (pid,)).fetchone()[0]
    assert name in p["message"] and "Over 2.5 Shots on Goal" in p["message"] and "edge +" in p["message"]
    assert p["click"].endswith(f"#/games/{synth.UPCOMING_NHL_ID}")
    assert p["matches"] == 2  # both books, in one notification
    # Later runs: the alert has already fired for this game, so nothing new is sent.
    assert al.run_alerts(conn, NOW + timedelta(hours=1), send=sent.append, site_url=None, path=path) == 0
    assert len(sent) == 1
    row = conn.execute("SELECT delivery_status, delivered_at FROM alert_events").fetchone()
    assert row["delivery_status"] == "sent" and row["delivered_at"] == iso(NOW)
    # A database-level guarantee, not just the evaluator's.
    aid = conn.execute("SELECT id FROM alerts WHERE key = 'leans'").fetchone()[0]
    with pytest.raises(Exception, match="UNIQUE"):
        conn.execute("INSERT INTO alert_events (alert_id, game_id, payload) VALUES (?, ?, '{}')", (aid, gid))


def test_thresholds_and_filters(league, tmp_path):
    conn, *_ = league
    path = _yml(
        tmp_path,
        """
alerts:
  - {key: too-high, type: edge, min_edge: 40}
  - {key: other-team, type: edge, team: T03}
  - {key: other-market, type: edge, market: skater_goals}
""",
    )
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 0


def test_line_move_and_line_alerts(league, tmp_path):
    conn, gid, pid, books, sog, src = league
    nhl_pid = conn.execute("SELECT nhl_player_id FROM players WHERE id = ?", (pid,)).fetchone()[0]
    path = _yml(
        tmp_path,
        f"""
alerts:
  - {{key: moves, type: line_move, points: 5}}
  - {{key: cheap-over, type: line, market: skater_shots_on_goal, player: {nhl_pid}, min_price: 120}}
""",
    )
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 0
    upsert_line(
        conn,
        game_id=gid,
        market_id=sog,
        book_id=books["fanduel"],
        player_id=pid,
        team_id=None,
        line=2.5,
        over=125,
        under=-150,
        at=iso(NOW + timedelta(minutes=30)),
        source_id=src,
        source_ref="test",
    )
    assert al.run_alerts(conn, NOW + timedelta(hours=1), send=None, site_url=None, path=path) == 2
    msgs = {
        r["key"]: json.loads(r["payload"])["message"]
        for r in conn.execute("SELECT a.key, e.payload FROM alert_events e JOIN alerts a ON a.id = e.alert_id")
    }
    assert "over -110 → +125 (-7.9 pts)" in msgs["moves"]
    assert "over +125" in msgs["cheap-over"]


def test_goalie_alert(league, tmp_path):
    conn, gid, *_, src = league
    g = conn.execute("SELECT home_team_id FROM games WHERE id = ?", (gid,)).fetchone()
    goalie = conn.execute(
        "SELECT id, full_name FROM players WHERE current_team_id = ? AND position = 'G' LIMIT 1", (g[0],)
    ).fetchone()
    abbrev = conn.execute("SELECT abbrev FROM teams WHERE id = ?", (g[0],)).fetchone()[0]
    path = _yml(tmp_path, f"alerts:\n  - {{key: g, type: goalie, team: {abbrev}}}\n")
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 0
    conn.execute(
        "INSERT INTO goalie_starts (game_id, team_id, player_id, status, reported_at, provenance, source_id, "
        "fetched_at) VALUES (?, ?, ?, 'confirmed', ?, 'manual', ?, ?)",
        (gid, g[0], goalie["id"], iso(NOW), src, iso(NOW)),
    )
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 1
    msg = json.loads(conn.execute("SELECT payload FROM alert_events").fetchone()[0])["message"]
    assert msg == f"{abbrev}: {goalie['full_name']} confirmed to start"


class _Tracker:
    def __init__(self, issues):
        self.issues = issues

    def open_issues(self):
        return self.issues

    def comment(self, number, body): ...

    def close(self, number, completed): ...


def _news_body(headline="Out week-to-week (lower body)", category="Injury", player="", team="", url="https://x.org/a"):
    return (
        f"### Headline\n\n{headline}\n\n### Category\n\n{category}\n\n### Player\n\n{player or '_No response_'}\n\n"
        f"### Team\n\n{team or '_No response_'}\n\n### Reliability\n\nBeat reporter\n\n### Details\n\n_No response_\n\n"
        f"### Source URL\n\n{url}\n"
    )


def test_news_quick_entry_feeds_news_page_and_alert(league, tmp_path):
    conn, _gid, pid, *_ = league
    nhl_pid = conn.execute("SELECT nhl_player_id FROM players WHERE id = ?", (pid,)).fetchone()[0]
    issues = [
        Issue(1, "Quick Entry: news", _news_body(player=str(nhl_pid)), "owner", iso(NOW)),
        Issue(2, "Quick Entry: news", _news_body(url="not a url"), "owner", iso(NOW)),
        Issue(3, "Quick Entry: news", _news_body(team=""), "owner", iso(NOW)),  # no player, no team
        Issue(4, "Quick Entry: news", _news_body(category="Gossip", team="T00"), "owner", iso(NOW)),
    ]
    res = run_quick_entry(conn, _Tracker(issues), "owner", NOW)
    assert [o.applied for o in res.outcomes] == [True, False, False, False]
    assert "source URL" in res.outcomes[1].message
    assert "player, a team" in res.outcomes[2].message
    assert res.reasons == {}  # news never changes projections

    item = feed(conn, NOW)["items"][0]
    assert item["url"] == "https://x.org/a" and item["category"] == "injury"
    assert item["reliability"] == "beat_reporter" and item["player"]["id"] == nhl_pid
    assert for_player(conn, pid, NOW)[0]["headline"] == "Out week-to-week (lower body)"
    # The same item is an event marker on that player's line-movement chart.
    from rinkx.publish.lines import line_events

    ev = line_events(conn, _gid, pid)
    assert ev == [
        {"at": iso(NOW), "kind": "news", "label": "Injury: Out week-to-week (lower body)", "source": "https://x.org/a"}
    ]

    path = _yml(tmp_path, "alerts:\n  - {key: injuries, type: news, categories: [injury]}\n")
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 1
    msg = json.loads(conn.execute("SELECT payload FROM alert_events").fetchone()[0])["message"]
    assert msg == "Injury: Out week-to-week (lower body) (https://x.org/a)"
    # A name works too (resolved across all teams when no team is given).
    name = conn.execute("SELECT full_name FROM players WHERE id = ?", (pid,)).fetchone()[0]
    out = apply(conn, Issue(5, "Quick Entry: news", _news_body(player=name), "owner", iso(NOW)), 1, NOW)
    assert out.applied, out.message


def test_delivery_failure_retry_and_quiet_logs(league, tmp_path, caplog):
    conn, *_ = league
    path = _yml(tmp_path, "alerts:\n  - {key: leans, type: edge}\n")

    def broken(payload):
        raise OSError("connection refused")

    with caplog.at_level(logging.WARNING):
        al.run_alerts(conn, NOW, send=broken, site_url=None, path=path)
    assert conn.execute("SELECT delivery_status FROM alert_events").fetchone()[0] == "failed"
    msg = json.loads(conn.execute("SELECT payload FROM alert_events").fetchone()[0])["message"]
    assert msg.split(":")[0] not in caplog.text  # public logs never carry the notification text
    sent: list[dict] = []
    al.run_alerts(conn, NOW + timedelta(hours=1), send=sent.append, site_url=None, path=path)
    assert len(sent) == 1 and conn.execute("SELECT delivery_status FROM alert_events").fetchone()[0] == "sent"
    # Without a topic, events are recorded and left pending; after the retry window they are never sent.
    conn.execute("UPDATE alert_events SET delivery_status = 'pending'")
    assert al.deliver(conn, NOW + timedelta(hours=1), None) == (0, 0)
    assert al.deliver(conn, NOW + timedelta(hours=7), sent.append) == (0, 0)


def test_history_and_removed_alerts(league, tmp_path):
    conn, *_ = league
    al.run_alerts(conn, NOW, send=None, site_url=None, path=_yml(tmp_path, "alerts:\n  - {key: leans, type: edge}\n"))
    al.run_alerts(conn, NOW, send=None, site_url=None, path=_yml(tmp_path, "alerts: []\n"))
    h = al.history(conn, NOW, delivery=False)
    assert h["delivery"] is None
    assert h["alerts"] == [
        {"key": "leans", "type": "edge", "active": False, "condition": {}, "last_triggered_at": iso(NOW)}
    ]
    assert h["events"][0]["delivery"] == "pending" and h["events"][0]["matchup"]


def test_ntfy_json_publish(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return Resp()

    monkeypatch.setattr(ntfy.urllib.request, "urlopen", fake_urlopen)
    ntfy.sender("secret-topic")({"title": "RinkX · a · X @ Y", "message": "Over -110 → +125", "click": "https://s/"})
    assert seen["url"] == "https://ntfy.sh"
    assert seen["body"] == {
        "topic": "secret-topic",
        "title": "RinkX · a · X @ Y",
        "message": "Over -110 → +125",
        "tags": ["ice_hockey"],
        "click": "https://s/",
    }


def test_site_url_and_topic_settings():
    s = Settings.from_env({"GITHUB_REPOSITORY": "Khil13/RinkX", "RINKX_NTFY_TOPIC": "t"})
    assert s.site_url == "https://khil13.github.io/RinkX/" and s.ntfy_topic == "t"
    assert Settings.from_env({}).site_url is None


def test_pregame_check_lists_what_changed_on_the_card(league, tmp_path):
    conn, gid, pid, books, sog, src = league
    path = _yml(tmp_path, "alerts:\n  - {key: pre, type: pregame, minutes_before: 60}\n")
    sent: list[dict] = []
    # 15:00: the pick makes the card; the 23:00 game is far off, so nothing is checked yet
    assert al.run_alerts(conn, NOW, send=sent.append, site_url="https://o.github.io/RinkX/", path=path) == 0
    was = json.loads(conn.execute("SELECT state FROM card_picks").fetchone()[0])
    assert was["price"] in (-105, -110) and was["goalie"]["status"] == "projected"

    # by 22:00 the price has moved against the pick and the other goalie is confirmed
    later = NOW + timedelta(hours=7)
    for code in ("fanduel", "betmgm"):
        upsert_line(
            conn,
            game_id=gid,
            market_id=sog,
            book_id=books[code],
            player_id=pid,
            team_id=None,
            line=2.5,
            over=-150,
            under=120,
            at=iso(later - timedelta(minutes=5)),
            source_id=src,
            source_ref="test",
        )
    run_pricing(conn, later, CFG)
    away = conn.execute("SELECT away_team_id FROM games WHERE id = ?", (gid,)).fetchone()[0]
    other = conn.execute(
        "SELECT id, full_name FROM players WHERE current_team_id = ? AND position = 'G' AND id != ? LIMIT 1",
        (away, was["goalie"]["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO goalie_starts (game_id, team_id, player_id, status, reported_at, provenance, source_id, "
        "fetched_at) VALUES (?, ?, ?, 'confirmed', ?, 'manual', ?, ?)",
        (gid, away, other["id"], iso(later), src, iso(later)),
    )
    assert al.run_alerts(conn, later, send=sent.append, site_url="https://o.github.io/RinkX/", path=path) == 1
    p = sent[-1]
    assert p["title"].startswith("RinkX · pre · Card of the Day")
    assert p["click"].endswith("#/props/best")
    assert "Over 2.5 Shots on Goal" in p["message"]
    assert "→ -150" in p["message"] or "no longer a lean" in p["message"]
    assert f"goalie now {other['full_name']} (confirmed)" in p["message"]
    # once per day
    assert al.run_alerts(conn, later + timedelta(minutes=20), send=sent.append, site_url=None, path=path) == 0


def test_pregame_is_silent_when_nothing_changed(league, tmp_path):
    conn, *_ = league
    path = _yml(tmp_path, "alerts:\n  - {key: pre, type: pregame}\n")
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 0
    assert al.run_alerts(conn, NOW + timedelta(hours=7), send=None, site_url=None, path=path) == 0
    assert conn.execute("SELECT count(*) FROM card_picks").fetchone()[0] == 1


def test_pregame_options_are_checked(tmp_path):
    _, errors = al.load_specs(_yml(tmp_path, "alerts:\n  - {key: p, type: pregame, minutes_before: 5}\n"))
    assert errors and "minutes_before" in errors[0]


def test_pregame_card_handles_game_props_and_a_broken_alert_does_not_stop_the_rest(league, tmp_path, monkeypatch):
    """Regression: game-line props (moneyline, totals) have no player id; the card must still build.
    And one alert that raises is recorded and skipped while the others still fire."""
    from rinkx.alerts import pregame

    conn, *_ = league
    player = {"subject": {"type": "player", "id": 1, "name": "P"}, "game": {"id": 9, "date": "2025-11-06"},
              "market": "skater_shots_on_goal", "lean": "over", "line": 2.5, "ev": 0.1}  # fmt: skip
    game = {"subject": {"type": "game", "name": "A @ B", "team": None}, "game": {"id": 9, "date": "2025-11-06"},
            "market": "game_moneyline", "lean": "home", "line": None, "ev": 0.2}  # fmt: skip
    picks = pregame.card([player, game], "2025-11-06")
    assert [p["market"] for p in picks] == ["game_moneyline", "skater_shots_on_goal"]

    def boom(*_a, **_k):
        raise RuntimeError("broken")

    monkeypatch.setitem(al.MATCHERS, "pregame", boom)
    path = _yml(tmp_path, "alerts:\n  - {key: pre, type: pregame}\n  - {key: leans, type: edge, min_edge: 4}\n")
    assert al.run_alerts(conn, NOW, send=None, site_url=None, path=path) == 1  # the edge alert still fires
    run = conn.execute("SELECT status, meta FROM ingestion_runs WHERE job_name = 'alerts' ORDER BY id DESC").fetchone()
    assert run["status"] == "partial" and "alert 'pre' failed: RuntimeError" in run["meta"]


def test_run_log_diagnostics_count_lines_and_leans_per_market(league):
    from rinkx.pipeline import diagnostics

    conn, *_ = league
    d = diagnostics(conn, NOW)
    assert d["markets"] == {"skater_shots_on_goal": {"open_lines": 2, "priced": 2, "leans": 2}}
    # counts and names only: no odds or probabilities go to the public log
    assert set(d) == {"markets", "models_passed", "models_not_passed"}


def test_pregame_card_groups_like_the_site():
    """Same rule as web/src/lib/card.ts: longshot goal props don't crowd out shots on goal."""
    from rinkx.alerts import pregame

    def row(pid, market, ev):
        return {"subject": {"type": "player", "id": pid, "name": f"P{pid}"}, "game": {"id": 9, "date": "D"},
                "market": market, "lean": "over", "line": 0.5, "ev": ev}  # fmt: skip

    rows = [row(100 + i, "skater_anytime_goal", 0.5 + i / 100) for i in range(10)]
    rows += [row(1, "skater_shots_on_goal", 0.04), row(2, "skater_shots_on_goal", 0.06), row(3, "skater_points", 0.05)]
    rows += [row(1, "skater_anytime_goal", 0.9)]  # P1 already has an SOG pick: one pick per player
    names = [(r["market"], r["subject"]["id"]) for r in pregame.card(rows, "D")]
    assert names[:3] == [("skater_shots_on_goal", 2), ("skater_shots_on_goal", 1), ("skater_points", 3)]
    goals = [n for n in names if n[0] == "skater_anytime_goal"]
    assert len(goals) == pregame.PER_GROUP and (("skater_anytime_goal", 1) not in goals)
    avoid = row(4, "skater_shots_on_goal", 0.2) | {"decision": {"code": "avoid"}}
    assert 4 not in [r["subject"]["id"] for r in pregame.card([*rows, avoid], "D")]
