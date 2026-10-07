"""Steady clock for pipeline.yml (standard library only; runs inside ticker.yml).

GitHub delays and drops scheduled runs when it is busy: on Oct 6 the hourly + every-10-minute
schedule produced 8 pipeline runs. This loop keeps its own time instead. Every TICK it starts a
pipeline run (Run workflow, via the gh CLI) when one is due:

* hourly: an hour since the last run this loop started (the old hourly baseline);
* game day: a game starts within LOOKAHEAD (the same rule as scripts/game_day_gate.py), so goalie
  confirmations, the closing odds fetch and the pre-game check land on time.

It never starts one while another pipeline run is queued or running. After RUN_FOR it exits;
ticker.yml then starts the next ticker, so the chain doesn't depend on GitHub's scheduler. The
schedules in pipeline.yml and ticker.yml stay as a backstop if the chain ever breaks.

Logs carry only times and decisions.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import game_day_gate as gate

TICK = timedelta(minutes=10)
HOURLY = timedelta(minutes=55)  # a little under an hour, so ticks don't drift past it
RUN_FOR = timedelta(hours=5, minutes=30)  # under the 6 h job limit, with room to hand over
BUSY = {"queued", "in_progress", "waiting", "pending", "requested"}


def due(payload: dict[str, Any] | None, now: datetime, last: datetime | None) -> tuple[bool, str]:
    """Whether to start a pipeline run now, and why."""
    if last is None:
        return True, "first tick"
    if now - last >= HOURLY:
        return True, "hourly"
    if payload is None:
        return False, "schedule unavailable: waiting for the hourly run"
    start = gate.next_start(payload, now)
    if start is not None and start - now <= gate.LOOKAHEAD:
        return True, f"game day: next game starts {start:%H:%M} UTC"
    return False, "no game within the look-ahead"


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def busy() -> bool:
    try:
        runs = json.loads(_gh("run", "list", "--workflow", "pipeline.yml", "--limit", "10", "--json", "status"))
    except Exception as exc:  # can't tell: don't pile up runs
        print(f"::warning::could not list pipeline runs ({type(exc).__name__})")
        return True
    return any(r.get("status") in BUSY for r in runs)


def dispatch() -> bool:
    try:
        _gh("workflow", "run", "pipeline.yml")
        return True
    except Exception as exc:
        print(f"::warning::could not start the pipeline ({type(exc).__name__})")
        return False


def main() -> int:
    began = datetime.now(UTC)
    last: datetime | None = None
    while True:
        now = datetime.now(UTC)
        if now - began >= RUN_FOR:
            print(f"{now:%H:%M} handing over to the next ticker")
            return 0
        go, reason = due(gate.fetch(), now, last)
        if go and busy():
            go, reason = False, f"{reason}, but a pipeline run is already queued or running"
        if go and dispatch():
            last = now
            print(f"{now:%H:%M} started a pipeline run ({reason})")
        else:
            print(f"{now:%H:%M} no run ({reason})")
        nxt = now + TICK
        time.sleep(max((nxt - datetime.now(UTC)).total_seconds(), 1))


if __name__ == "__main__":
    sys.exit(main())
