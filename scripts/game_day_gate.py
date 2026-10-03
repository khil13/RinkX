"""Game-day gate for pipeline.yml (standard library only, so it runs in seconds).

The game-day schedule fires every 10 minutes through the afternoon and evening, but a full run
is only worth it when an NHL game starts within LOOKAHEAD: that is when goalies get confirmed,
the closing odds fetch happens and the pre-game check fires. Any other trigger (the hourly
baseline, a push, Run workflow, a Quick Entry issue) always runs. If the schedule can't be
read, the run goes ahead: a wasted run is cheaper than a missed one.

Writes `run=true|false` and `reason=...` to $GITHUB_OUTPUT (or prints them).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/now"
GAME_DAY_CRONS = {"*/10 15-23 * * *", "*/10 0-3 * * *"}  # must match pipeline.yml
LOOKAHEAD = timedelta(hours=4, minutes=30)  # covers the pre-game check's longest setting (4 h + margin)
UPCOMING = {"FUT", "PRE"}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def next_start(payload: dict[str, Any], now: datetime) -> datetime | None:
    """The soonest start of a game that hasn't started, at or after `now`."""
    starts = [
        _parse(g["startTimeUTC"])
        for day in payload.get("gameWeek") or []
        for g in day.get("games") or []
        if g.get("gameState") in UPCOMING and g.get("startTimeUTC")
    ]
    later = [s for s in starts if s >= now]
    return min(later) if later else None


def decide(schedule: str | None, payload: dict[str, Any] | None, now: datetime) -> tuple[bool, str]:
    if schedule not in GAME_DAY_CRONS:
        return True, "not a game-day trigger"
    if payload is None:
        return True, "schedule unavailable: running anyway"
    start = next_start(payload, now)
    if start is None:
        return False, "no upcoming game this week"
    if start - now <= LOOKAHEAD:
        return True, f"next game starts {start:%H:%M} UTC"
    return False, f"next game starts {start:%Y-%m-%d %H:%M} UTC: more than {LOOKAHEAD} away"


def fetch() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(SCHEDULE_URL, headers={"User-Agent": "RinkX game-day gate"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data: dict[str, Any] = json.loads(r.read())
            return data
    except Exception as exc:  # fail open
        print(f"::warning::schedule fetch failed ({type(exc).__name__})")
        return None


def main() -> int:
    schedule = os.environ.get("EVENT_SCHEDULE") or None
    payload = fetch() if schedule in GAME_DAY_CRONS else None
    run, reason = decide(schedule, payload, datetime.now(UTC))
    lines = f"run={'true' if run else 'false'}\nreason={reason}\n"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(lines)
    print(lines, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
