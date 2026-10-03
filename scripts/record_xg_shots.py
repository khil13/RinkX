"""Record real NHL shot attempts as a compact test fixture for the xG model (run by
record-xg-shots.yml, which commits the result to the branch).

For the first GAMES completed regular-season games of 2025-26, fetch each play-by-play, parse it
with the pipeline's own parser, and keep one row per shot attempt: when, who shot (team only),
where from, shot type, strength, empty net, and whether it was a goal. No player names or ids.
Written as gzipped CSV (pipeline/tests/fixtures/nhl/xg_shots_20252026.csv.gz).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from rinkx.ingestion.nhl import urls  # noqa: E402
from rinkx.ingestion.nhl.parse import parse_play_by_play  # noqa: E402

OUT = ROOT / "pipeline/tests/fixtures/nhl/xg_shots_20252026.csv.gz"
SEASON_PREFIX = 2025020000
GAMES = 260
UA = "RinkX fixture recorder (personal project)"
COLUMNS = (
    "game",
    "game_date",
    "period",
    "period_seconds",
    "event_idx",
    "event_type",
    "team",
    "x",
    "y",
    "shot_type",
    "strength_state",
    "empty_net",
)


def fetch(url: str) -> dict:
    time.sleep(0.5)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    rows: list[tuple] = []
    games = 0
    n = 0
    misses = 0
    while games < GAMES and misses < 20:
        n += 1
        gid = SEASON_PREFIX + n
        try:
            payload = fetch(urls.play_by_play(gid))
        except Exception as exc:
            print(f"::warning::{gid}: {exc}")
            misses += 1
            continue
        if payload.get("gameState") not in ("OFF", "FINAL"):
            misses += 1
            continue
        pbp = parse_play_by_play(payload)
        date = str(payload["gameDate"])
        for e in pbp.shots:
            rows.append(
                (
                    gid,
                    date,
                    e.period,
                    e.period_seconds,
                    e.event_idx,
                    e.event_type,
                    e.shooter_team_id,
                    "" if e.x is None else e.x,
                    "" if e.y is None else e.y,
                    e.shot_type or "",
                    e.strength_state,
                    int(e.empty_net),
                )
            )
        games += 1
        misses = 0  # stop only after 20 failures in a row
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    w.writerows(rows)
    with gzip.GzipFile(OUT, "wb", mtime=0) as f:
        f.write(buf.getvalue().encode())
    print(f"recorded {len(rows)} shot attempts from {games} games -> {OUT.name}")
    return 0 if games >= 100 else 1


if __name__ == "__main__":
    sys.exit(main())
