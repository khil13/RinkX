"""pipeline.yml game-day gate (scripts/game_day_gate.py) and the watchdog's view of skipped runs."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from rinkx.config import REPO_ROOT
from rinkx.watchdog import built

_spec = importlib.util.spec_from_file_location("game_day_gate", REPO_ROOT / "scripts/game_day_gate.py")
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

NOW = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
GAME_DAY = "*/10 15-23 * * *"


def _week(*games: tuple[str, str]) -> dict:
    return {"gameWeek": [{"date": "2026-11-10", "games": [{"startTimeUTC": t, "gameState": s} for t, s in games]}]}


def test_game_day_runs_only_when_a_game_is_near():
    soon = _week(("2026-11-10T23:00:00Z", "FUT"))  # 3 h away
    later = _week(("2026-11-11T00:30:00Z", "FUT"))  # 4.5 h away: still inside
    far = _week(("2026-11-11T02:00:00Z", "FUT"))
    assert gate.decide(GAME_DAY, soon, NOW)[0]
    assert gate.decide(GAME_DAY, later, NOW)[0]
    run, reason = gate.decide(GAME_DAY, far, NOW)
    assert not run and "more than" in reason
    # games already under way or over don't count; the next one does
    mixed = _week(("2026-11-10T19:00:00Z", "LIVE"), ("2026-11-10T17:00:00Z", "OFF"), ("2026-11-11T05:00:00Z", "FUT"))
    assert not gate.decide(GAME_DAY, mixed, NOW)[0]
    assert not gate.decide(GAME_DAY, {"gameWeek": []}, NOW)[0]


def test_other_triggers_and_outages_always_run():
    far = _week(("2026-11-12T00:00:00Z", "FUT"))
    for trigger in (None, "17 * * * *"):  # push, Run workflow, Quick Entry, hourly baseline
        assert gate.decide(trigger, far, NOW) == (True, "not a game-day trigger")
    assert gate.decide(GAME_DAY, None, NOW)[0]  # schedule unreadable: fail open


def test_gate_crons_match_the_workflow():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/pipeline.yml").read_text())
    on = wf.get("on", wf.get(True))
    crons = {c["cron"] for c in on["schedule"]}
    assert crons >= gate.GAME_DAY_CRONS
    assert timedelta(hours=4) <= gate.LOOKAHEAD  # pre-game check's longest setting is 240 minutes
    # the store's concurrency group is on the build job, so a skipped game-day run never displaces work
    assert "concurrency" not in wf
    assert wf["jobs"]["build"]["concurrency"]["group"] == "rinkx-store"
    assert wf["jobs"]["build"]["needs"] == "gate"


def test_recorded_schedule_parses():
    import json

    payload = json.loads(Path(REPO_ROOT / "pipeline/tests/fixtures/nhl/schedule_now.json").read_text())
    first = gate.next_start(payload, datetime(2026, 1, 1, tzinfo=UTC))
    assert first is not None and first.tzinfo is not None


def test_watchdog_ignores_runs_the_gate_skipped():
    assert built([{"name": "gate", "conclusion": "success"}, {"name": "build", "conclusion": "success"}])
    assert not built([{"name": "gate", "conclusion": "success"}, {"name": "build", "conclusion": "skipped"}])
    assert not built([{"name": "gate", "conclusion": "success"}])
