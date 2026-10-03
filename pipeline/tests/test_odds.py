"""Phase 4 odds ingestion. The NHL side is the recorded real API data (replayed); the odds
payloads below are hand-written in The Odds API v4 response format (test-only). The live
format is checked separately with the probe-odds workflow."""

from __future__ import annotations

import copy
import dataclasses
import json
import sqlite3
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from rinkx.config import REPO_ROOT
from rinkx.ingestion.http import ReplayFetcher
from rinkx.ingestion.nhl.jobs import NhlOptions, run_nhl
from rinkx.ingestion.odds import budget as bud
from rinkx.ingestion.odds.client import OddsClient, OddsError, credits_for, redact
from rinkx.ingestion.odds.jobs import OddsConfig, run_odds
from rinkx.ingestion.odds.parse import OddsParseError, parse_quotes
from rinkx.ingestion.odds.resolve import norm, team_id
from rinkx.store.db import connect, migrate

KEY = "secret-test-key-123"
NOW = datetime(2026, 3, 12, 15, 0, tzinfo=UTC)  # 8 h before TOR-ANA
TOR_ANA = {
    "id": "ev-tor-ana",
    "sport_key": "icehockey_nhl",
    "commence_time": "2026-03-12T23:00:00Z",
    "home_team": "Toronto Maple Leafs",
    "away_team": "Anaheim Ducks",
}
MTL_TOR = {
    "id": "ev-mtl-tor",
    "sport_key": "icehockey_nhl",
    "commence_time": "2026-03-10T23:00:00Z",
    "home_team": "Montreal Canadiens",
    "away_team": "Toronto Maple Leafs",
}


def ou(name: str, point: float, over: int, under: int) -> list[dict[str, Any]]:
    return [
        {"name": "Over", "description": name, "price": over, "point": point},
        {"name": "Under", "description": name, "price": under, "point": point},
    ]


def props_payload(matthews_over: int = -140) -> dict[str, Any]:
    sog = ou("Auston Matthews", 3.5, matthews_over, 110) + ou("Will Nylander", 2.5, -120, -105)
    sog += ou("Bo Groulx", 0.5, -200, 160) + ou("Nobody Realname", 1.5, -110, -110)
    pts = ou("Auston Matthews", 0.5, -250, 190) + ou("Auston Matthews", 1.5, 140, -175)  # main = most balanced
    goal = [{"name": "Yes", "description": "Auston Matthews", "price": 120}]
    book = lambda key: {  # noqa: E731
        "key": key,
        "title": key,
        "last_update": "2026-03-12T14:55:00Z",
        "markets": [
            {"key": "player_shots_on_goal", "last_update": "2026-03-12T14:55:00Z", "outcomes": sog},
            {"key": "player_points", "outcomes": pts},
            {"key": "player_goal_scorer_anytime", "outcomes": goal},
            {"key": "player_shots_on_goal_alternate", "outcomes": ou("Auston Matthews", 4.5, 150, -190)},
        ],
    }
    return TOR_ANA | {"bookmakers": [book("fanduel"), book("betmgm"), book("draftkings")]}


GAME_ODDS = [
    TOR_ANA
    | {
        "bookmakers": [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Toronto Maple Leafs", "price": -180},
                            {"name": "Anaheim Ducks", "price": 150},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 6.5},
                            {"name": "Under", "price": -110, "point": 6.5},
                        ],
                    },
                ],
            }
        ]
    }
]


class FakeTransport:
    """Serves payloads by URL path and returns quota headers like the real API."""

    def __init__(self, routes: dict[str, Any], remaining: int = 500) -> None:
        self.routes, self.remaining, self.used = routes, remaining, 0
        self.calls = 0
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[Any, dict[str, str]]:
        self.calls += 1
        self.urls.append(url)
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        assert q["apiKey"] == [KEY]
        path = parsed.path.removeprefix("/v4")
        if path not in self.routes:
            raise OddsError(url, "HTTP 404", 404)
        cost = 0 if path.endswith("/events") else credits_for(q["markets"][0].split(","), q["bookmakers"][0].split(","))
        self.remaining -= cost
        self.used += cost
        headers = {
            "x-requests-last": str(cost),
            "x-requests-remaining": str(self.remaining),
            "x-requests-used": str(self.used),
        }
        return copy.deepcopy(self.routes[path]), headers


def routes(props: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "/sports/icehockey_nhl/events": [
            TOR_ANA,
            MTL_TOR,
            {
                "id": "ev-x",
                "commence_time": "2026-03-12T23:00:00Z",
                "home_team": "Nowhere Nobodies",
                "away_team": "Anaheim Ducks",
            },
        ],
        "/sports/icehockey_nhl/odds": GAME_ODDS,
        "/sports/icehockey_nhl/events/ev-tor-ana/odds": props or props_payload(),
    }


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = connect(tmp_path / "odds.db")
    migrate(c, REPO_ROOT / "db")
    run_nhl(
        c,
        ReplayFetcher(REPO_ROOT / "pipeline/tests/fixtures/nhl"),
        datetime(2026, 3, 10, 16, tzinfo=UTC),
        NhlOptions(today=date(2026, 3, 10), boxscore_limit=100),
    )
    c.execute("UPDATE games SET status = 'scheduled' WHERE nhl_game_id = 2025021029")  # TOR-ANA, upcoming
    c.commit()
    return c


def lines(conn: sqlite3.Connection, market: str) -> list[tuple]:
    rows = conn.execute(
        "SELECT p.full_name, b.code, l.line, l.over_price, l.under_price, l.status FROM prop_lines l "
        "JOIN markets m ON m.id = l.market_id JOIN sportsbooks b ON b.id = l.sportsbook_id "
        "LEFT JOIN players p ON p.id = l.player_id WHERE m.code = ? ORDER BY p.full_name, b.code",
        (market,),
    ).fetchall()
    return [tuple(r) for r in rows]


def test_key_never_appears_in_errors():
    url = f"https://api.the-odds-api.com/v4/sports/x/odds?markets=h2h&apiKey={KEY}&regions=us"
    assert KEY not in redact(url) and "apiKey=***" in redact(url)
    assert KEY not in str(OddsError(url, f"HTTP 401 for {url}", 401))


def test_parser_is_strict():
    bad = props_payload()
    bad["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.85  # decimal odds: format changed
    with pytest.raises(OddsParseError, match="not an American price"):
        parse_quotes(bad)
    with pytest.raises(OddsParseError, match="neither team"):
        parse_quotes(
            TOR_ANA
            | {
                "bookmakers": [
                    {
                        "key": "fanduel",
                        "markets": [{"key": "h2h", "outcomes": [{"name": "Somebody Else", "price": 120}]}],
                    }
                ]
            }
        )


def test_team_names_resolve_with_accents_and_punctuation(conn):
    tid = lambda abbrev: conn.execute("SELECT id FROM teams WHERE abbrev = ?", (abbrev,)).fetchone()[0]  # noqa: E731
    assert team_id(conn, "Montreal Canadiens") == tid("MTL")
    assert team_id(conn, "St Louis Blues") == tid("STL")
    assert team_id(conn, "Utah Hockey Club") == tid("UTA")  # old club name: falls back to location
    assert team_id(conn, "Vegas Golden Knights") == tid("VGK")
    assert norm("Jean-Gabriel Pageau Jr.") == "jean gabriel pageau"


def test_odds_run_stores_resolved_lines_only(conn):
    t = FakeTransport(routes())
    run_odds(conn, OddsClient(KEY, t), NOW, OddsConfig.load())
    sog = lines(conn, "skater_shots_on_goal")
    assert ("Auston Matthews", "betmgm", 3.5, -140, 110, "open") in sog
    assert ("Auston Matthews", "fanduel", 3.5, -140, 110, "open") in sog
    assert not any(r[1] == "draftkings" for r in sog)  # not one of your books
    assert ("William Nylander", "fanduel", 2.5, -120, -105, "open") in sog  # "Will" -> William (curated rule)
    assert not any(r[0] is None for r in sog)  # the unknown name was not stored
    issue = conn.execute(
        "SELECT detail FROM data_quality_issues WHERE entity_type = 'odds_player_name' AND resolved_at IS NULL"
    ).fetchone()
    assert json.loads(issue[0])["name"] == "Nobody Realname"
    pts = lines(conn, "skater_points")
    assert {(r[1], r[2]) for r in pts} == {("betmgm", 1.5), ("fanduel", 1.5)}  # +140/-175 beats -250/+190
    assert ("Auston Matthews", "fanduel", None, 120, None, "open") in lines(conn, "skater_anytime_goal")
    ml = conn.execute(
        "SELECT t.abbrev, l.over_price, l.under_price FROM prop_lines l JOIN markets m ON m.id = l.market_id "
        "JOIN teams t ON t.id = l.team_id WHERE m.code = 'game_moneyline'"
    ).fetchall()
    assert [tuple(r) for r in ml] == [("TOR", -180, 150)]  # home price, away price
    plan = json.loads(conn.execute("SELECT meta FROM ingestion_runs WHERE job_name = 'odds.plan'").fetchone()[0])
    assert plan["unmapped_markets"] == ["player_shots_on_goal_alternate"]
    used = conn.execute("SELECT sum(credits_charged), min(credits_remaining) FROM odds_usage").fetchone()
    assert used[0] == 500 - t.remaining and used[1] == t.remaining
    assert all(KEY not in (r[0] or "") for r in conn.execute("SELECT error FROM ingestion_runs"))


def test_line_movements_only_on_change_and_removal(conn):
    t = FakeTransport(routes())
    cfg = OddsConfig.load()
    run_odds(conn, OddsClient(KEY, t), NOW, cfg)
    # Next refresh (props due again 6 h later, <12 h to puck drop): Matthews over moves, Groulx is pulled.
    moved = props_payload(matthews_over=-160)
    for bm in moved["bookmakers"]:
        bm["markets"][0]["outcomes"] = [o for o in bm["markets"][0]["outcomes"] if o["description"] != "Bo Groulx"]
    t.routes = routes(moved)
    run_odds(conn, OddsClient(KEY, t), NOW + timedelta(hours=6), cfg)
    hist = conn.execute(
        "SELECT m.observed_at, m.over_price, m.status FROM line_movements m JOIN prop_lines l ON l.id = m.prop_line_id "
        "JOIN players p ON p.id = l.player_id JOIN sportsbooks b ON b.id = l.sportsbook_id "
        "JOIN markets k ON k.id = l.market_id WHERE p.full_name = ? AND b.code = 'fanduel' "
        "AND k.code = 'skater_shots_on_goal' ORDER BY m.observed_at",
        ("Auston Matthews",),
    ).fetchall()
    assert [(r[1], r[2]) for r in hist] == [(-140, "open"), (-160, "open")]
    nyl = conn.execute(
        "SELECT count(*) FROM line_movements m JOIN prop_lines l ON l.id = m.prop_line_id JOIN players p "
        "ON p.id = l.player_id WHERE p.full_name = 'William Nylander'"
    ).fetchone()[0]
    assert nyl == 2  # one per book, unchanged on the second fetch: no new rows
    assert ("Bo Groulx", "fanduel", 0.5, -200, 160, "removed") in lines(conn, "skater_shots_on_goal")


def test_budget_month_simulation_stays_within_credits():
    cfg = bud.BudgetConfig.load()
    books = ["fanduel", "betmgm"]
    for monthly in (cfg.monthly_credits, 20_000):
        c = bud.BudgetConfig(
            monthly,
            cfg.reserve,
            cfg.game_markets,
            cfg.game_times_per_day,
            cfg.window_hours,
            cfg.refresh_hours,
            cfg.prop_markets,
        )
        start = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
        games = [
            bud.Candidate(d * 10 + i, f"e{d}-{i}", start + timedelta(days=d, hours=18 + i % 3), None)
            for d in range(30)
            for i in range(8)
        ]
        last: dict[int, datetime] = {}
        remaining, spent_by_day, gl_by_day, last_gl = monthly, {}, {}, None
        fetched_any: set[int] = set()
        for h in range(30 * 24):
            now = start + timedelta(hours=h)
            day = (now - timedelta(hours=5)).date()
            cands = [bud.Candidate(g.game_id, g.event_id, g.start, last.get(g.game_id)) for g in games]
            p = bud.plan(
                c,
                now,
                remaining=remaining,
                spent_today=spent_by_day.get(day, 0),
                game_lines_today=gl_by_day.get(day, 0),
                last_game_lines=last_gl,
                candidates=cands,
                books=books,
            )
            cost = p.cost(c, books)
            assert cost <= p.allowance
            remaining -= cost
            spent_by_day[day] = spent_by_day.get(day, 0) + cost
            if p.game_lines:
                gl_by_day[day] = gl_by_day.get(day, 0) + 1
                last_gl = now
            for cand, _ in p.props:
                last[cand.game_id] = now
                fetched_any.add(cand.game_id)
        assert remaining >= c.reserve  # never planned past the reserve
        if monthly == 20_000:
            assert len(fetched_any) == len(games)  # a paid plan covers every game
        else:
            assert len(fetched_any) >= 30  # the free plan still covers about one game a day


def test_pipeline_publishes_lines_without_leaking_the_key(tmp_path, keys):
    from dataclasses import replace

    from conftest import PASSPHRASE, make_settings

    from rinkx import crypto
    from rinkx.pipeline import run

    settings = replace(
        make_settings(tmp_path, keys, env="prod"),
        sources=frozenset({"nhl"}),
        fixtures_dir=REPO_ROOT / "pipeline/tests/fixtures/nhl",
        today=date(2026, 3, 10),
        boxscore_limit=100,
        odds_api_key=KEY,
    )
    out = tmp_path / "data"
    run(settings, out, odds_client=OddsClient(KEY, FakeTransport(routes())))
    manifest = json.loads((out / "manifest.json").read_text())
    assert {f["code"]: f["state"] for f in manifest["feeds"]}["odds"] == "ok"
    key = crypto.unwrap_keyfile(json.loads((out / "keyfile.json").read_text()), PASSPHRASE)

    def read(rel: str) -> dict:
        return crypto.decrypt_json(json.loads((out / f"{rel}.enc").read_text()), rel, key)["data"]

    game = read("games/2025021029.json")  # TOR-ANA
    ml = next(m for m in game["lines"]["markets"] if m["market"] == "game_moneyline")
    row = ml["rows"][0]
    assert row["team"] == "TOR" and row["books"][0]["over"] == -180 and row["books"][0]["under"] == 150
    assert 0.55 < row["consensus"]["p_over"] < 0.62  # no-vig home win probability from -180/+150
    assert game["line_count"] == 2
    admin = read("admin/health.json")
    assert admin["odds"]["credits"]["remaining"] is not None
    blob = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert KEY not in blob and KEY not in json.dumps(admin)


def test_budget_first_looks_then_biggest_edges_then_closing_fetch():
    cfg = dataclasses.replace(bud.BudgetConfig.load(), pace=())  # order of spending, not pacing
    books = ["fanduel", "betmgm"]
    now = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    s = now + timedelta(hours=8)
    looked = now - timedelta(hours=1)
    cands = [
        bud.Candidate(1, "new", s, None),  # never fetched: a cheap first look
        bud.Candidate(2, "edge", s, looked, 0.09, ("player_points",), cfg.first_look_markets),
        bud.Candidate(3, "small", s, looked, 0.03, ("player_shots_on_goal",), cfg.first_look_markets),
        bud.Candidate(4, "none", s, looked, 0.0, (), cfg.first_look_markets),
    ]
    p = bud.plan(
        cfg, now, remaining=400, spent_today=0, game_lines_today=1, last_game_lines=now, candidates=cands, books=books
    )
    got = {c.event_id: m for c, m in p.props}
    assert got["new"] == cfg.prop_markets[: cfg.first_look_markets]
    assert "none" not in got  # no lean on its first look and not due: no credits
    order = [c.event_id for c, _ in p.props]
    assert "edge" in order and ("small" not in order or order.index("edge") < order.index("small"))
    assert got["edge"][0] == "player_points"  # markets with leans first
    assert p.cost(cfg, books) <= p.allowance

    # 1 h before puck drop: a closing fetch for the games with leans, lean markets first
    close = s - timedelta(hours=1)
    cands = [
        bud.Candidate(2, "edge", s, s - timedelta(hours=5), 0.09, ("player_points",), 9),
        bud.Candidate(4, "none", s, s - timedelta(hours=5), 0.0, (), 9),
    ]
    p = bud.plan(
        cfg,
        close,
        remaining=400,
        spent_today=0,
        game_lines_today=1,
        last_game_lines=close,
        candidates=cands,
        books=books,
    )
    got = {c.event_id: m for c, m in p.props}
    assert got["edge"][: cfg.closing_markets][0] == "player_points" and len(got["edge"]) == cfg.closing_markets
    assert "none" not in got
    # ...and only once
    cands[0] = bud.Candidate(2, "edge", s, close, 0.09, ("player_points",), cfg.closing_markets)
    p = bud.plan(
        cfg,
        close + timedelta(minutes=30),
        remaining=400,
        spent_today=10,
        game_lines_today=1,
        last_game_lines=close,
        candidates=cands,
        books=books,
    )
    assert not p.props


def test_budget_holds_credits_for_todays_closing_fetches():
    cfg = bud.BudgetConfig.load()
    books = ["fanduel", "betmgm"]
    now = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    later = [
        bud.Candidate(i, f"g{i}", now + timedelta(hours=7), now - timedelta(hours=1), 0.05, (), 9) for i in range(3)
    ]
    fresh = [bud.Candidate(10 + i, f"n{i}", now + timedelta(hours=20), None) for i in range(20)]
    p = bud.plan(
        cfg,
        now,
        remaining=200,
        spent_today=0,
        game_lines_today=1,
        last_game_lines=now,
        candidates=later + fresh,
        books=books,
    )
    unit = bud.credits_for(["x"], books)
    held = min(3 * cfg.closing_markets * unit, p.allowance // 2)
    assert p.cost(cfg, books) <= p.allowance - held


def _ladder_event(with_main: bool = False, label: str = "Over") -> dict:
    rungs = [
        {"name": label, "description": "Auston Matthews", "point": p, "price": price}
        for p, price in ((0.5, -250), (1.5, 160), (2.5, 600))
    ]
    markets = [{"key": "player_points_alternate", "outcomes": rungs}]
    if with_main:
        markets.append(
            {
                "key": "player_points",
                "outcomes": [
                    {"name": "Over", "description": "Auston Matthews", "point": 1.5, "price": 140},
                    {"name": "Under", "description": "Auston Matthews", "point": 1.5, "price": -170},
                ],
            }
        )
    return {
        "id": "e1",
        "commence_time": "2026-10-03T23:00:00Z",
        "home_team": "Toronto Maple Leafs",
        "away_team": "Boston Bruins",
        "bookmakers": [{"key": "fanduel", "markets": markets}],
    }


def test_points_ladder_keeps_the_one_plus_rung():
    """FanDuel posts NHL points only as a ladder (player_points_alternate): 1+, 2+, 3+ points."""
    from rinkx.ingestion.odds.store import assemble

    for label in ("Over", "Yes"):  # both labels seen for ladders across books
        _, quotes = parse_quotes(_ladder_event(label=label))
        (line,) = assemble(quotes)
        assert (line.market, line.line, line.over, line.under) == ("player_points_alternate", 0.5, -250, None)
    cfg = OddsConfig.load()
    assert cfg.market_map["player_points_alternate"] == "skater_points"
    assert cfg.market_map["player_assists_alternate"] == "skater_assists"
    assert "player_points_alternate" in cfg.budget.prop_markets[: cfg.budget.first_look_markets]


def test_main_points_line_wins_over_the_ladder(conn, monkeypatch):
    """When a book has both, only its main line is stored (both map to skater_points)."""
    from rinkx.ingestion.odds import jobs

    stored: list[tuple] = []
    monkeypatch.setattr(
        jobs, "upsert_line", lambda conn, **kw: (stored.append((kw["line"], kw["over"], kw["under"])) or 1, "new")
    )
    monkeypatch.setattr(jobs, "mark_removed", lambda *a, **k: 0)

    class _R:
        def resolve(self, *_a):
            return 1

    gid = conn.execute("SELECT id FROM games LIMIT 1").fetchone()
    if gid is None:
        pytest.skip("fixture league has no games")
    cfg = OddsConfig.load()
    books = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM sportsbooks")} or {"fanduel": 1}
    jobs._store_payload(
        conn, cfg, _ladder_event(with_main=True), gid[0], books, _R(), 1, datetime(2026, 10, 3, tzinfo=UTC), set(),
        ["player_points", "player_points_alternate"],
    )  # fmt: skip
    assert stored == [(1.5, 140, -170)]


def test_budget_paces_the_day_so_the_overnight_run_cannot_spend_it_all():
    cfg = bud.BudgetConfig.load()
    assert cfg.pace and cfg.pace[-1][1] == 1.0
    books = ["fanduel", "betmgm"]
    games = [bud.Candidate(i, f"g{i}", datetime(2026, 11, 10, 23, 0, tzinfo=UTC), None) for i in range(12)]
    # 3 am ET (07:00 UTC): only the first share of the day is spendable
    night = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    p = bud.plan(cfg, night, remaining=440, spent_today=0, game_lines_today=0, last_game_lines=None,
                 candidates=games, books=books)  # fmt: skip
    daily = (440 - cfg.reserve) / bud.days_left_in_month(night)
    assert p.allowance <= int(daily * cfg.pace[0][1]) < int(daily)
    # 5 pm ET: the rest of the day is available
    evening = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    spent = p.cost(cfg, books)
    q = bud.plan(cfg, evening, remaining=440 - spent, spent_today=spent, game_lines_today=1,
                 last_game_lines=night, candidates=games, books=books)  # fmt: skip
    assert q.allowance > 0 and spent + q.allowance <= int(daily) + 1
