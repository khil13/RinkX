"""Injury report (ESPN): strict parsing of the recorded real response, storage semantics, and its
effect on projections, goalie starters, confidence and the published pages."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import synth
import test_nhl_ingest as nhl

from rinkx.config import REPO_ROOT
from rinkx.ingestion.http import FixtureFetcher, ReplayFetcher
from rinkx.ingestion.injuries import espn
from rinkx.ingestion.nhl.jobs import NhlOptions, run_nhl
from rinkx.models import project
from rinkx.pricing import confidence as conf
from rinkx.store.db import connect, migrate
from rinkx.timeutil import iso

FIX = REPO_ROOT / "pipeline/tests/fixtures/injuries"
REAL = json.loads((FIX / "espn_injuries.json").read_text())


class Fake:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get_json(self, url):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_parses_the_recorded_report():
    entries = espn.parse(REAL)
    assert len(entries) == 109
    by_status: dict[str, int] = {}
    for e in entries:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    assert by_status == {"injured_reserve": 85, "long_term_ir": 8, "out": 8, "day_to_day": 7, "suspended": 1}
    terry = next(e for e in entries if e.name == "Troy Terry")
    assert (terry.team, terry.status, terry.body_part, terry.expected_return) == (
        "ANA",
        "long_term_ir",
        "Hip",
        "2026-11-21",
    )
    assert terry.reported_at == "2026-09-30T14:53:00Z" and terry.source_ref.startswith(
        "https://www.espn.com/nhl/player/"
    )
    assert terry.description.startswith("Terry (hip) was moved")
    # ESPN's short codes become NHL abbreviations; every team is one the NHL uses.
    assert {"LAK", "NJD", "SJS", "TBL"} <= {e.team for e in entries}
    assert not {"LA", "NJ", "SJ", "TB"} & {e.team for e in entries}
    # The bare "ir" comment is replaced by the body part.
    helleson = next(e for e in entries if e.name == "Drew Helleson")
    assert helleson.description == "Lower Body"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("injuries"),
        lambda d: d["injuries"][0]["injuries"][0].pop("athlete"),
        lambda d: d["injuries"][0]["injuries"][0].update(date="yesterday"),
        lambda d: d["injuries"][0]["injuries"][0]["type"].pop("name"),
    ],
)
def test_strict_parser_rejects_a_changed_shape(mutate):
    bad = copy.deepcopy(REAL)
    mutate(bad)
    with pytest.raises(espn.InjuryFeedError):
        espn.parse(bad)


@pytest.fixture
def league(tmp_path: Path):
    c = connect(tmp_path / "l.db")
    migrate(c, REPO_ROOT / "db")
    run_nhl(c, ReplayFetcher(nhl.FIX), nhl.NOW, NhlOptions(today=nhl.TODAY, boxscore_limit=100))
    return c


def _rows(c):
    return sorted(
        tuple(r)
        for r in c.execute(
            "SELECT p.full_name, i.status, i.is_active FROM injuries i JOIN players p ON p.id = i.player_id"
        )
    )


def test_replayed_report_is_stored_and_kept_current(league):
    c = league
    espn.run_injuries(c, FixtureFetcher(FIX), nhl.NOW)
    assert _rows(c) == [
        ("Charlie McAvoy", "suspended", 1),
        ("Dakota Joshua", "long_term_ir", 1),
        ("Kevin Fiala", "injured_reserve", 1),
        ("Max Domi", "long_term_ir", 1),
    ]  # the players on the recorded rosters; the rest wait on Admin, never guessed
    run = c.execute("SELECT status, meta FROM ingestion_runs WHERE job_name = 'injuries'").fetchone()
    assert run["status"] == "partial" and json.loads(run["meta"])["unmatched"] == 105
    assert (
        c.execute(
            "SELECT count(*) FROM data_quality_issues WHERE entity_type = 'injury_player_name' AND resolved_at IS NULL"
        ).fetchone()[0]
        == 105
    )
    assert all(r[0].startswith("https://www.espn.com/") for r in c.execute("SELECT source_ref FROM injuries"))

    # The same report again: nothing new.
    later = nhl.NOW + timedelta(hours=1)
    espn.run_injuries(c, FixtureFetcher(FIX), later)
    assert len(_rows(c)) == 4
    # McAvoy drops off the report; Fiala goes day-to-day: one resolved, one changed (history kept).
    nxt = copy.deepcopy(REAL)
    for t in nxt["injuries"]:
        t["injuries"] = [e for e in t["injuries"] if e["athlete"]["displayName"] != "Charlie McAvoy"]
        for e in t["injuries"]:
            if e["athlete"]["displayName"] == "Kevin Fiala":
                e["type"]["name"] = "INJURY_STATUS_DAYTODAY"
                e["details"]["fantasyStatus"]["abbreviation"] = "GTD"
    espn.run_injuries(c, Fake(nxt), later + timedelta(hours=1))
    assert _rows(c) == [
        ("Charlie McAvoy", "suspended", 0),
        ("Dakota Joshua", "long_term_ir", 1),
        ("Kevin Fiala", "day_to_day", 1),
        ("Kevin Fiala", "injured_reserve", 0),
        ("Max Domi", "long_term_ir", 1),
    ]


def test_a_failed_fetch_keeps_the_previous_report(league):
    c = league
    espn.run_injuries(c, FixtureFetcher(FIX), nhl.NOW)
    before = _rows(c)
    espn.run_injuries(c, Fake({"unexpected": True}), nhl.NOW + timedelta(hours=1))
    assert _rows(c) == before
    statuses = [r[0] for r in c.execute("SELECT status FROM ingestion_runs WHERE job_name = 'injuries' ORDER BY id")]
    assert statuses == ["partial", "failed"]
    # Freshness follows the last good check, on the data's own clock.
    assert espn.feed_fresh(c, nhl.NOW + timedelta(hours=20))
    assert not espn.feed_fresh(c, nhl.NOW + timedelta(hours=30))


# ---- effect on projections (synthetic league) ---------------------------------------------------

DAYS = 200
TODAY = date(2025, 10, 7) + timedelta(days=DAYS)
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 15, tzinfo=UTC)


def _report(conn, entries: list[tuple[int, str]]) -> dict:
    """An ESPN-shaped report for synthetic players: [(players.id, ESPN type)]."""
    items = []
    for pid, kind in entries:
        name, abbrev = conn.execute(
            "SELECT p.full_name, t.abbrev FROM players p JOIN teams t ON t.id = p.current_team_id WHERE p.id = ?",
            (pid,),
        ).fetchone()
        items.append(
            {
                "id": str(pid),
                "date": "2026-02-03T12:00Z",
                "shortComment": "Synthetic test entry",
                "status": kind,
                "athlete": {
                    "displayName": name,
                    "team": {"abbreviation": abbrev},
                    "links": [{"rel": ["playercard"], "href": f"https://www.espn.com/nhl/player/_/id/{pid}"}],
                },
                "type": {"name": kind},
                "details": {"type": "Lower Body", "returnDate": "2026-02-20"},
            }
        )
    return {"injuries": [{"injuries": items}]}


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    conn, truth = synth.build(str(tmp_path_factory.mktemp("s") / "s.db"), days=DAYS, seed=9)
    gid = synth.add_upcoming(conn, TODAY)
    g = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE id = ?", (gid,)).fetchone()
    return conn, truth, gid, g


def _current(conn, gid, pid):
    return conn.execute(
        "SELECT count(*) FROM player_projections WHERE game_id = ? AND player_id = ? AND is_current = 1", (gid, pid)
    ).fetchone()[0]


def test_injury_report_drives_projections_starters_and_quality(synthetic):
    conn, truth, gid, (home, away) = synthetic
    skaters = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM players WHERE current_team_id = ? AND position <> 'G' ORDER BY id", (home,)
        )
    ]
    out_p, dtd_p, back_p = skaters[0], skaters[1], skaters[2]
    starter = truth.goalies[away][0]

    # Without a report: "injuries not connected" on every projection.
    project.run_models(conn, NOW, TODAY)
    miss = json.loads(
        conn.execute(
            "SELECT missing_inputs FROM player_projections WHERE player_id = ? AND is_current = 1 LIMIT 1", (dtd_p,)
        ).fetchone()[0]
    )
    assert "injuries_not_connected" in miss
    assert project.goalie_mix(conn, gid, away).mix[0][0] == starter

    report = _report(
        conn,
        [(out_p, "INJURY_STATUS_OUT"), (dtd_p, "INJURY_STATUS_DAYTODAY"), (back_p, "INJURY_STATUS_IR"),
         (starter, "INJURY_STATUS_IR")],
    )  # fmt: skip
    espn.run_injuries(conn, Fake(report), NOW)
    # A Quick Entry "back in" for this game overrides the report.
    src = conn.execute("SELECT id FROM data_sources LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO game_availability (game_id, player_id, status, reported_at, provenance, source_id, source_ref, "
        "fetched_at) VALUES (?, ?, 'available', ?, 'manual', ?, 'https://example.org/back', ?)",
        (gid, back_p, iso(NOW), src, iso(NOW)),
    )
    project.run_models(conn, NOW + timedelta(minutes=5), TODAY, {gid: "injury"})

    assert _current(conn, gid, out_p) == 0  # out: no projection
    assert _current(conn, gid, back_p) > 0  # IR on the report, but back in by Quick Entry
    row = conn.execute(
        "SELECT missing_inputs, data_quality FROM player_projections WHERE player_id = ? AND is_current = 1 LIMIT 1",
        (dtd_p,),
    ).fetchone()
    miss = json.loads(row[0])
    assert "injury_day_to_day" in miss and "injuries_not_connected" not in miss
    healthy = conn.execute(
        "SELECT data_quality FROM player_projections WHERE player_id = ? AND is_current = 1 LIMIT 1", (skaters[3],)
    ).fetchone()[0]
    assert row[1] == pytest.approx(healthy - 0.1)
    # The injured starter is no longer the projected starter.
    assert starter not in [p for p, _ in project.goalie_mix(conn, gid, away).mix]
    out = project.players_out(conn, gid)
    assert out[out_p]["reason"] == "injury report: out" and out[out_p]["source"].startswith("https://www.espn.com/")

    # A report checked long before the game is ignored.
    conn.execute(
        "UPDATE ingestion_runs SET meta = json_set(meta, '$.checked_at', ?) WHERE job_name = 'injuries'",
        (iso(NOW - timedelta(days=5)),),
    )
    assert project.players_out(conn, gid).get(out_p) is None
    assert not project.injury_report_fresh(conn, gid)


def test_confidence_marks_day_to_day():
    base = dict(
        edge=0.06, p_model=0.6, games_in_history=60, data_quality=0.9, book_novigs=[0.54, 0.54], one_sided=False,
        moved_against_pts=None, is_goalie_prop=False, is_game_market=False, start_probability=None,
        start_confirmed=False, opp_goalie_confirmed=True, injury_feed=True,
    )  # fmt: skip
    ok = conf.score(conf.Inputs(**base))
    dtd = conf.score(conf.Inputs(**base, day_to_day=True))
    assert ok.parts["availability"] - dtd.parts["availability"] == 6
    assert any("day-to-day" in n for n in dtd.notes["availability"])
    nofeed = conf.score(conf.Inputs(**{**base, "injury_feed": False}))
    assert any("No injury feed" in n for n in nofeed.notes["availability"])
    assert not any("No injury feed" in n for n in ok.notes["availability"])
